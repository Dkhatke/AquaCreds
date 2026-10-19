"""Operational commands: `aqua-api migrate | revision -m "msg" [--autogenerate] | bootstrap`."""

import argparse
import time

from alembic import command
from alembic.config import Config

from aqua_core.logging import configure_logging, get_logger
from aqua_core.settings import get_settings
from aqua_core.storage import ensure_bucket


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "aqua_api:migrations")
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def bootstrap(retries: int = 20, delay_s: float = 1.5) -> None:
    """Create the object-storage bucket, retrying while the storage service starts."""
    log = get_logger("aqua_api.bootstrap")
    settings = get_settings()
    for attempt in range(1, retries + 1):
        try:
            created = ensure_bucket(settings)
            log.info("storage.bucket_ready", bucket=settings.s3_bucket, created=created)
            return
        except Exception as exc:
            log.warning("storage.not_ready", attempt=attempt, error=str(exc)[:200])
            time.sleep(delay_s)
    raise SystemExit(f"Object storage not reachable after {retries} attempts")


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    parser = argparse.ArgumentParser(prog="aqua-api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply all database migrations")
    rev = sub.add_parser("revision", help="create a new migration")
    rev.add_argument("-m", "--message", required=True)
    rev.add_argument("--autogenerate", action="store_true")
    sub.add_parser("bootstrap", help="create storage bucket(s)")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        command.upgrade(alembic_config(), "head")
    elif args.cmd == "revision":
        command.revision(alembic_config(), message=args.message, autogenerate=args.autogenerate)
    elif args.cmd == "bootstrap":
        bootstrap()


if __name__ == "__main__":
    main()
