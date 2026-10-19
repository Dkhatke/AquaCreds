"""Alembic environment (async engine). Run via `aqua-api migrate`."""

import asyncio

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy.engine import Connection

import aqua_core.models  # noqa: F401  (registers all tables on Base.metadata)
from aqua_core.db import Base, create_engine

config = context.config
target_metadata = Base.metadata

# Tables created by the PostGIS extension itself are not ours to manage.
_POSTGIS_TABLES = {"spatial_ref_sys", "geometry_columns", "geography_columns"}


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    if type_ == "table" and (name in _POSTGIS_TABLES or (reflected and compare_to is None)):
        # Never drop tables we don't model (PostGIS / tiger geocoder / topology internals).
        return False
    table = getattr(obj, "table", None)
    if (
        type_ == "index"
        and reflected
        and compare_to is None
        and table is not None
        and table.name not in target_metadata.tables
    ):
        return False
    return bool(alembic_helpers.include_object(obj, name, type_, reflected, compare_to))


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        process_revision_directives=alembic_helpers.writer,
        render_item=alembic_helpers.render_item,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not configured")
    engine = create_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
