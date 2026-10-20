"""Typed wrapper around AsyncWeb3: connection + chain-id check, contract handles, fee quotes."""

from dataclasses import dataclass
from typing import Any

from eth_utils.address import to_checksum_address
from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.contract import AsyncContract

from aqua_chain import artifacts
from aqua_chain.errors import ChainIdMismatchError, GasPriceTooHighError
from aqua_chain.wallets import ChainNotConfiguredError
from aqua_core.settings import Settings

GWEI = 10**9


@dataclass(frozen=True)
class FeeQuote:
    max_fee_per_gas: int
    max_priority_fee_per_gas: int


class ChainClient:
    def __init__(
        self,
        w3: AsyncWeb3[Any],
        *,
        chain_id: int,
        addresses: dict[str, str | None],
    ) -> None:
        self.w3 = w3
        self.chain_id = chain_id
        self._addresses = {
            name: to_checksum_address(addr) for name, addr in addresses.items() if addr
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> "ChainClient":
        provider = AsyncHTTPProvider(
            settings.chain_rpc_url, request_kwargs={"timeout": settings.chain_rpc_timeout_s}
        )
        return cls(
            AsyncWeb3(provider),
            chain_id=settings.chain_id,
            addresses={
                "AquaRegistry": settings.chain_registry_address,
                "AquaCredit": settings.chain_credit_address,
                "AquaMarketplace": settings.chain_marketplace_address,
                "MockUSDC": settings.chain_usdc_address,
                "AquaForwardFund": settings.chain_forward_fund_address,
            },
        )

    async def close(self) -> None:
        provider = self.w3.provider
        if isinstance(provider, AsyncHTTPProvider):
            await provider.disconnect()

    async def check(self) -> int:
        """Verify the node is reachable and on the configured chain; returns the chain id."""
        actual = int(await self.w3.eth.chain_id)
        if actual != self.chain_id:
            raise ChainIdMismatchError(f"RPC is on chain {actual}, expected {self.chain_id}")
        return actual

    @property
    def addresses(self) -> dict[str, str]:
        return dict(self._addresses)

    def address_of(self, name: str) -> str:
        try:
            return self._addresses[name]
        except KeyError:
            raise ChainNotConfiguredError(f"no address configured for {name}") from None

    def contract(self, name: str) -> AsyncContract:
        return self.w3.eth.contract(
            address=to_checksum_address(self.address_of(name)), abi=artifacts.abi(name)
        )

    async def latest_block(self) -> int:
        return int(await self.w3.eth.block_number)

    async def balance(self, address: str) -> int:
        return int(await self.w3.eth.get_balance(to_checksum_address(address)))

    async def pending_nonce(self, address: str) -> int:
        return int(await self.w3.eth.get_transaction_count(to_checksum_address(address), "pending"))

    async def fee_quote(self, *, cap_wei: int, priority_wei: int) -> FeeQuote:
        """EIP-1559 fees: maxFee = 2 x baseFee + tip, capped. Raises (retryable) when even the
        current base fee + tip exceeds the cap, rather than overpaying."""
        block = await self.w3.eth.get_block("latest")
        base = int(block.get("baseFeePerGas") or 0)
        if base + priority_wei > cap_wei:
            raise GasPriceTooHighError(
                f"base fee {base / GWEI:.2f} gwei + tip {priority_wei / GWEI:.2f} gwei exceeds "
                f"the cap of {cap_wei / GWEI:.2f} gwei"
            )
        return FeeQuote(
            max_fee_per_gas=min(2 * base + priority_wei, cap_wei),
            max_priority_fee_per_gas=priority_wei,
        )
