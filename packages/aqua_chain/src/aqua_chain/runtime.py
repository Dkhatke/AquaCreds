"""Wiring from Settings: the objects a worker task or API request needs."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_chain.client import ChainClient
from aqua_chain.indexer import IndexerConfig
from aqua_chain.relayer import Relayer, RelayerPolicy
from aqua_chain.wallets import ChainNotConfiguredError, Signers
from aqua_core.settings import Settings


def require_configured(settings: Settings) -> None:
    if not settings.chain_configured:
        raise ChainNotConfiguredError(
            "chain integration is not configured: set RELAYER_MNEMONIC, CHAIN_REGISTRY_ADDRESS "
            "and CHAIN_CREDIT_ADDRESS"
        )


def build_relayer(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    client: ChainClient | None = None,
) -> Relayer:
    require_configured(settings)
    return Relayer(
        sessionmaker=sessionmaker,
        client=client or ChainClient.from_settings(settings),
        signers=Signers.from_settings(settings),
        policy=RelayerPolicy.from_settings(settings),
    )


def indexer_config(settings: Settings) -> IndexerConfig:
    return IndexerConfig(
        deploy_block=settings.chain_deploy_block,
        batch_blocks=settings.chain_index_batch_blocks,
        reorg_depth=settings.chain_reorg_depth,
    )
