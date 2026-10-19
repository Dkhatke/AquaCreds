from alembic import command
from alembic.config import Config


def test_models_and_migrations_are_in_sync(database_url: str) -> None:
    """Fails if a model changed without a matching Alembic migration."""
    cfg = Config()
    cfg.set_main_option("script_location", "aqua_api:migrations")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.check(cfg)


def test_migrations_downgrade_and_upgrade_cleanly(database_url: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", "aqua_api:migrations")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(cfg, "0001_enable_postgis")
    command.upgrade(cfg, "head")
