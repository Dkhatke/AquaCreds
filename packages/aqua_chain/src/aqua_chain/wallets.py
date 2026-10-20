"""Platform signer accounts and custodial organisation wallets, all derived (BIP-32/44) from the
relayer mnemonic. Private keys live only in memory; the database stores derivation indices and
addresses."""

import uuid
from functools import cached_property

from eth_account import Account
from eth_account.hdaccount import key_from_seed, seed_from_mnemonic
from eth_account.signers.local import LocalAccount
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aqua_core.models import ChainSigner, OrgWallet
from aqua_core.settings import Settings

ORG_WALLET_LOCK_KEY = 0x41_51_57_4C  # "AQWL": serialises org-wallet index allocation


def derivation_path(index: int) -> str:
    if index < 0 or index >= 2**31:
        raise ValueError("derivation index must be in [0, 2^31)")
    return f"m/44'/60'/0'/0/{index}"


class ChainNotConfiguredError(RuntimeError):
    """The relayer mnemonic or contract addresses are missing."""


class Signers:
    """Derives the registrar / verifier / issuer accounts and organisation wallets."""

    def __init__(
        self,
        mnemonic: str,
        *,
        registrar_index: int,
        verifier_index: int,
        issuer_index: int,
        org_index_offset: int,
    ) -> None:
        words = " ".join(mnemonic.split())
        if not words:
            raise ChainNotConfiguredError("RELAYER_MNEMONIC is not set")
        indices = {
            ChainSigner.REGISTRAR: registrar_index,
            ChainSigner.VERIFIER: verifier_index,
            ChainSigner.ISSUER: issuer_index,
        }
        if len(set(indices.values())) != len(indices):
            raise ValueError("registrar, verifier and issuer must use different keys")
        self._words = words
        self._indices = indices
        self.org_index_offset = org_index_offset

    @classmethod
    def from_settings(cls, settings: Settings) -> "Signers":
        return cls(
            settings.relayer_mnemonic.get_secret_value(),
            registrar_index=settings.chain_registrar_index,
            verifier_index=settings.chain_verifier_index,
            issuer_index=settings.chain_issuer_index,
            org_index_offset=settings.chain_org_index_offset,
        )

    @cached_property
    def _seed(self) -> bytes:
        # Validates the checksum; PBKDF2 runs once per Signers instance.
        return seed_from_mnemonic(self._words, "")

    def derive(self, index: int) -> LocalAccount:
        account: LocalAccount = Account.from_key(key_from_seed(self._seed, derivation_path(index)))
        return account

    def account(self, role: ChainSigner) -> LocalAccount:
        return self.derive(self._indices[role])

    def address(self, role: ChainSigner) -> str:
        return str(self.account(role).address)

    def addresses(self) -> dict[ChainSigner, str]:
        return {role: self.address(role) for role in self._indices}


async def ensure_org_wallet(
    session: AsyncSession, signers: Signers, org_id: uuid.UUID
) -> OrgWallet:
    """Return the organisation's custodial wallet, allocating the next derivation index on first
    use (offset, offset+1, ...). Index allocation is serialised with an advisory lock; the caller
    commits."""
    existing = await session.get(OrgWallet, org_id)
    if existing is not None:
        return existing
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ORG_WALLET_LOCK_KEY})
    existing = await session.get(OrgWallet, org_id, populate_existing=True)
    if existing is not None:
        return existing
    highest = await session.scalar(select(func.max(OrgWallet.derivation_index)))
    index = signers.org_index_offset if highest is None else highest + 1
    wallet = OrgWallet(org_id=org_id, derivation_index=index, address=signers.derive(index).address)
    session.add(wallet)
    await session.flush()
    return wallet
