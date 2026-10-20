"""Event indexer: polls contract logs from a per-contract cursor in bounded block ranges, decodes
them and upserts into `chain_events`.

- Idempotent: (chain_id, tx_hash, log_index) is unique; re-scans update rows in place.
- Small reorgs: every run re-scans the last `reorg_depth` blocks before the cursor. Within a
  scanned range the fresh logs are authoritative, so rows from orphaned blocks are deleted.
- One indexer at a time: a transaction-scoped advisory lock (a second run simply skips).
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqua_chain.client import ChainClient
from aqua_chain.events import DecodedEvent, EventDecoder
from aqua_core.models import ChainCursor, ChainEvent

INDEXER_LOCK_KEY = 0x41_51_49_58  # "AQIX"
INDEXED_CONTRACTS = ("AquaRegistry", "AquaCredit", "AquaMarketplace", "AquaForwardFund")


@dataclass(frozen=True)
class IndexerConfig:
    deploy_block: int = 0
    batch_blocks: int = 2000
    reorg_depth: int = 12
    max_batches: int = 50  # per contract per run, keeps one run bounded


@dataclass
class IndexResult:
    latest_block: int
    events: dict[str, int] = field(default_factory=dict)  # contract → events upserted
    removed: int = 0
    cursors: dict[str, int] = field(default_factory=dict)
    skipped: bool = False


async def _store_range(
    session: AsyncSession,
    *,
    chain_id: int,
    address: str,
    from_block: int,
    to_block: int,
    events: list[DecodedEvent],
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
                },
            )
        )
    return len(stale)


async def index_events(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: ChainClient,
    config: IndexerConfig,
) -> IndexResult:
    latest = await client.latest_block()
    result = IndexResult(latest_block=latest)
    names = [n for n in INDEXED_CONTRACTS if n in client.addresses]
    decoder = EventDecoder(client, tuple(names))
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
                result.removed += await _store_range(
                    session,
                    chain_id=client.chain_id,
                    address=address,
                    from_block=frm,
                    to_block=to,
                    events=events,
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
        await session.commit()
    return result


async def events_for_project(session: AsyncSession, project_key: str) -> list[ChainEvent]:
    rows = await session.scalars(
        select(ChainEvent)
        .where(ChainEvent.project_key == project_key)
        .order_by(tuple_(ChainEvent.block_number, ChainEvent.log_index))
    )
    return list(rows)
