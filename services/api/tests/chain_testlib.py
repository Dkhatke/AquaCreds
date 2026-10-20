"""Helpers for chain integration tests: deploy the contract suite on Anvil from the committed
artifacts, wire roles to the relayer's derived signer accounts, and mine blocks.

Uses only Anvil's public development mnemonic (prefunded accounts 0-9). Account 0 deploys; the
registrar / verifier / issuer are accounts 1 / 2 / 3 (the Settings defaults)."""

from dataclasses import dataclass
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import AsyncHTTPProvider, AsyncWeb3

from aqua_chain import artifacts
from aqua_chain.wallets import Signers
from aqua_core.models import ChainSigner
from aqua_core.settings import ANVIL_DEV_MNEMONIC, Settings

ANVIL_CHAIN_ID = 31337


@dataclass
class Deployment:
    registry: str
    credit: str
    marketplace: str
    usdc: str
    block: int
    forward_fund: str | None = None

    def settings(self, rpc_url: str, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "chain_rpc_url": rpc_url,
            "chain_id": ANVIL_CHAIN_ID,
            "chain_registry_address": self.registry,
            "chain_credit_address": self.credit,
            "chain_marketplace_address": self.marketplace,
            "chain_usdc_address": self.usdc,
            "chain_forward_fund_address": self.forward_fund,
            "chain_deploy_block": self.block,
            "relayer_mnemonic": ANVIL_DEV_MNEMONIC,
            "chain_confirmations": 2,
            "chain_reorg_depth": 3,
            "chain_index_batch_blocks": 5,
        }
        values.update(overrides)
        return values


def w3_for(rpc_url: str) -> AsyncWeb3[Any]:
    return AsyncWeb3(AsyncHTTPProvider(rpc_url))


def signers() -> Signers:
    return Signers.from_settings(Settings(_env_file=None, relayer_mnemonic=ANVIL_DEV_MNEMONIC))


async def _send(w3: AsyncWeb3[Any], account: LocalAccount, tx: Any) -> Any:
    tx["nonce"] = await w3.eth.get_transaction_count(account.address, "pending")
    tx["from"] = account.address
    signed = account.sign_transaction(tx)
    receipt = await w3.eth.wait_for_transaction_receipt(
        await w3.eth.send_raw_transaction(signed.raw_transaction)
    )
    assert receipt["status"] == 1
    return receipt


async def _deploy(w3: AsyncWeb3[Any], account: LocalAccount, name: str, *args: Any) -> str:
    factory = w3.eth.contract(abi=artifacts.abi(name), bytecode=artifacts.bytecode(name))
    tx = await factory.constructor(*args).build_transaction({"from": account.address})
    receipt = await _send(w3, account, tx)
    return str(receipt["contractAddress"])


async def call_as(w3: AsyncWeb3[Any], account: LocalAccount, fn: Any) -> Any:
    tx = await fn.build_transaction({"from": account.address})
    return await _send(w3, account, tx)


async def deploy_suite(rpc_url: str) -> Deployment:
    w3 = w3_for(rpc_url)
    deployer: LocalAccount = Account.from_key(signers().derive(0).key)
    s = signers()
    start = int(await w3.eth.block_number)
    registry = await _deploy(w3, deployer, "AquaRegistry", deployer.address)
    credit = await _deploy(
        w3, deployer, "AquaCredit", deployer.address, registry, "https://example.org/{id}.json"
    )
    usdc = await _deploy(w3, deployer, "MockUSDC", deployer.address)
    market = await _deploy(
        w3, deployer, "AquaMarketplace", deployer.address, credit, usdc, deployer.address, 200
    )
    reg = w3.eth.contract(address=registry, abi=artifacts.abi("AquaRegistry"))  # type: ignore[arg-type]
    cred = w3.eth.contract(address=credit, abi=artifacts.abi("AquaCredit"))  # type: ignore[arg-type]
    for role, account in (
        ("REGISTRAR_ROLE", s.address(ChainSigner.REGISTRAR)),
        ("VERIFIER_ROLE", s.address(ChainSigner.VERIFIER)),
        ("CREDIT_CONTRACT_ROLE", credit),
    ):
        role_id = await getattr(reg.functions, role)().call()
        await call_as(w3, deployer, reg.functions.grantRole(role_id, account))
    issuer_role = await cred.functions.ISSUER_ROLE().call()
    await call_as(
        w3, deployer, cred.functions.grantRole(issuer_role, s.address(ChainSigner.ISSUER))
    )
    await w3.provider.disconnect()  # type: ignore[attr-defined]
    return Deployment(registry=registry, credit=credit, marketplace=market, usdc=usdc, block=start)


async def deploy_forward_fund(rpc_url: str, deployment: Deployment) -> Deployment:
    """Add AquaForwardFund (admin = account 0) to a deployed suite."""
    w3 = w3_for(rpc_url)
    deployer: LocalAccount = Account.from_key(signers().derive(0).key)
    try:
        deployment.forward_fund = await _deploy(
            w3,
            deployer,
            "AquaForwardFund",
            deployer.address,
            deployment.registry,
            deployment.credit,
            deployment.usdc,
        )
    finally:
        await w3.provider.disconnect()  # type: ignore[attr-defined]
    return deployment


async def mine(rpc_url: str, blocks: int = 1) -> None:
    w3 = w3_for(rpc_url)
    await w3.provider.make_request("anvil_mine", [hex(blocks)])  # type: ignore[arg-type]
    await w3.provider.disconnect()  # type: ignore[attr-defined]


async def rpc(rpc_url: str, method: str, params: list[Any]) -> Any:
    w3 = w3_for(rpc_url)
    try:
        return await w3.provider.make_request(method, params)  # type: ignore[arg-type]
    finally:
        await w3.provider.disconnect()  # type: ignore[attr-defined]
