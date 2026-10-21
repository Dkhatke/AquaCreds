"""Event indexer: polls contract logs from a per-contract cursor in bounded block ranges, decodes
them and upserts into `chain_events`.

- Idempotent: (chain_id, tx_hash, log_index) is unique; re-scans update rows in place.
- Small reorgs: every run re-scans the last `reorg_depth` blocks before the cursor. Within a
  scanned range the fresh logs are authoritative, so rows from orphaned blocks are deleted.
- One indexer at a time: a transaction-scoped advisory lock (a second run simply skips).
- Block timestamps: each event stores its block's timestamp (`block_time`). Blocks are fetched by
  hash, once per block per batch (`BlockTimes` cache), so a reorged block never lends its time to
  the replacement. Rows indexed before timestamps were stored are backfilled, a bounded number of
  blocks per run.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from web3.exceptions import BlockNotFound

from aqua_chain.client import ChainClient
from aqua_chain.events import DecodedEvent, EventDecoder
from aqua_core.logging import get_logger
from aqua_core.models import ChainCursor, ChainEvent

log = get_logger("aqua_chain.indexer")

INDEXER_LOCK_KEY = 0x41_51_49_58  # "AQIX"
INDEXED_CONTRACTS = ("AquaRegistry", "AquaCredit", "AquaMarketplace", "AquaForwardFund")


@dataclass(frozen=True)
class IndexerConfig:
    deploy_block: int = 0
    batch_blocks: int = 2000
    reorg_depth: int = 12
    max_batches: int = 50  # per contract per run, keeps one run bounded
    backfill_blocks: int = 200  # per run: blocks whose events still lack a block_time


@dataclass
class IndexResult:
    latest_block: int
    events: dict[str, int] = field(default_factory=dict)  # contract → events upserted
    removed: int = 0
    cursors: dict[str, int] = field(default_factory=dict)
    skipped: bool = False
    block_times_backfilled: int = 0  # events whose block_time was filled in this run


class BlockTimes:
    """Block hash → block timestamp, fetching each block at most once."""

    def __init__(self, client: ChainClient) -> None:
        self._client = client
        self._cache: dict[str, datetime] = {}
        self.fetched = 0

    async def get(self, block_hash: str) -> datetime:
        key = block_hash.lower()
        if key not in self._cache:
            block: Any = await self._client.w3.eth.get_block(key)  # type: ignore[arg-type]
            self._cache[key] = datetime.fromtimestamp(int(block["timestamp"]), UTC)
            self.fetched += 1
        return self._cache[key]

    def clear(self) -> None:
        self._cache.clear()


async def _store_range(
    session: AsyncSession,
    *,
    chain_id: int,
    address: str,
    from_block: int,
    to_block: int,
    events: list[DecodedEvent],
    block_times: dict[str, datetime],
) -> int:
    keep = {(e.tx_hash, e.log_index) for e in events}
    stale_query = select(ChainEvent.id, ChainEvent.tx_hash, ChainEvent.log_index).where(
        ChainEvent.chain_id == chain_id,
        ChainEvent.address == address,
        ChainEvent.block_number.between(from_block, to_block),
    )
    stale = [row.id for row in await session.execute(stale_query) if (row[1], row[2]) not in keep]
    if stale:
        await session.execute(delete(ChainEvent).where(ChainEvent.id.in_(stale)))
    for e in events:
        stmt = insert(ChainEvent).values(
            chain_id=chain_id,
            contract=e.contract,
            address=address,
            event=e.event,
            block_number=e.block_number,
            block_hash=e.block_hash,
            tx_hash=e.tx_hash,
            log_index=e.log_index,
            project_key=e.project_key,
            args=e.args,
            block_time=block_times[e.block_hash.lower()],
        )
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_chain_events_log",
                set_={
                    "block_number": stmt.excluded.block_number,
                    "block_hash": stmt.excluded.block_hash,
                    "event": stmt.excluded.event,
                    "args": stmt.excluded.args,
                    "project_key": stmt.excluded.project_key,
                    "block_time": stmt.excluded.block_time,
                },
            )
        )
    return len(stale)


async def _backfill_block_times(
    session: AsyncSession, chain_id: int, times: BlockTimes, limit: int
) -> int:
    """Fill `block_time` on events indexed before it was stored, up to `limit` blocks."""
    if limit <= 0:
        return 0
    hashes = list(
        await session.scalars(
            select(ChainEvent.block_hash)
            .where(ChainEvent.chain_id == chain_id, ChainEvent.block_time.is_(None))
            .group_by(ChainEvent.block_hash)
            .order_by(func.min(ChainEvent.block_number))
            .limit(limit)
        )
    )
    filled = 0
    for block_hash in hashes:
        try:
            when = await times.get(block_hash)
        except BlockNotFound:
            # Orphaned by a reorg deeper than the re-scan window: no truthful time exists.
            log.warning("chain.block_time_unavailable", block_hash=block_hash)
            continue
        res = await session.execute(
            update(ChainEvent)
            .where(
                ChainEvent.chain_id == chain_id,
                ChainEvent.block_hash == block_hash,
                ChainEvent.block_time.is_(None),
            )
            .values(block_time=when)
        )
        filled += int(getattr(res, "rowcount", 0) or 0)
    return filled


async def index_events(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: ChainClient,
    config: IndexerConfig,
) -> IndexResult:
    latest = await client.latest_block()
    result = IndexResult(latest_block=latest)
    names = [n for n in INDEXED_CONTRACTS if n in client.addresses]
    decoder = EventDecoder(client, tuple(names))
    times = BlockTimes(client)
    async with sessionmaker() as session:
        locked = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": INDEXER_LOCK_KEY}
        )
        if not locked:
            result.skipped = True
            return result
        for name in names:
            address = client.address_of(name)
            cursor = await session.get(ChainCursor, (client.chain_id, address))
            if cursor is None:
                start = config.deploy_block
            else:
                start = max(config.deploy_block, cursor.last_block + 1 - config.reorg_depth)
            count = 0
            batches = 0
            frm = start
            while frm <= latest and batches < config.max_batches:
                to = min(latest, frm + config.batch_blocks - 1)
                logs = await client.w3.eth.get_logs(
                    {"address": address, "fromBlock": frm, "toBlock": to}  # type: ignore[typeddict-item]
                )
                events = [d for d in (decoder.decode(lg) for lg in logs) if d is not None]
                times.clear()  # cache per batch: one fetch per block, never stale across runs
                block_times = {e.block_hash.lower(): await times.get(e.block_hash) for e in events}
                result.removed += await _store_range(
                    session,
                    chain_id=client.chain_id,
                    address=address,
                    from_block=frm,
                    to_block=to,
                    events=events,
                    block_times=block_times,
                )
                count += len(events)
                if cursor is None:
                    cursor = ChainCursor(
                        chain_id=client.chain_id, address=address, contract=name, last_block=to
                    )
                    session.add(cursor)
                else:
                    cursor.last_block = max(cursor.last_block, to)
                await session.flush()
                frm = to + 1
                batches += 1
            result.events[name] = count
            if cursor is not None:
                result.cursors[name] = cursor.last_block
        times.clear()
        result.block_times_backfilled = await _backfill_block_times(
            session, client.chain_id, times, config.backfill_blocks
        )
        await session.commit()
    return result


async def events_for_project(session: AsyncSession, project_key: str) -> list[ChainEvent]:
    rows = await session.scalars(
        select(ChainEvent)
        .where(ChainEvent.project_key == project_key)
        .order_by(tuple_(ChainEvent.block_number, ChainEvent.log_index))
    )
    return list(rows)
