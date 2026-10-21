"""Operational commands: `aqua-api migrate | revision -m "msg" [--autogenerate] | bootstrap |
create-user | seed-demo [--seed N] [--with-chain]`."""

import argparse
import asyncio
import getpass
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


async def create_user(email: str, full_name: str, password: str, role: str) -> None:
    """Create (or promote) a user. Used to bootstrap the first registry admin / verifiers."""
    from sqlalchemy import select

    from aqua_api.security import hash_password
    from aqua_api.services import audit
    from aqua_core.db import create_engine, session_factory
    from aqua_core.models import PlatformRole, User

    engine = create_engine(get_settings().database_url)
    try:
        async with session_factory(engine)() as session:
            user = await session.scalar(select(User).where(User.email == email.lower()))
            if user is None:
                user = User(
                    email=email.lower(),
                    full_name=full_name,
                    password_hash=hash_password(password),
                )
                session.add(user)
            user.platform_role = PlatformRole(role)
            await session.flush()
            await audit.record(
                session,
                actor_id=None,
                action="user.platform_role_set_by_cli",
                entity_type="user",
                entity_id=user.id,
                data={"platform_role": role},
            )
            await session.commit()
            get_logger("aqua_api.cli").info("user.ready", email=user.email, role=role)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    parser = argparse.ArgumentParser(prog="aqua-api")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply all database migrations")
    rev = sub.add_parser("revision", help="create a new migration")
    rev.add_argument("-m", "--message", required=True)
    rev.add_argument("--autogenerate", action="store_true")
    rev.add_argument("--rev-id", default=None, help="e.g. 0002_identity")
    sub.add_parser("bootstrap", help="create storage bucket(s)")
    cu = sub.add_parser("create-user", help="create or promote a user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name", default="Registry Admin")
    cu.add_argument(
        "--role", default="registry_admin", choices=["user", "verifier", "registry_admin"]
    )
    sd = sub.add_parser(
        "seed-demo",
        help="seed a clearly labelled SYNTHETIC demo project (refused in production)",
    )
    sd.add_argument("--seed", type=int, default=42, help="deterministic seed (default 42)")
    sd.add_argument(
        "--with-chain",
        action="store_true",
        help="also anchor the reports and issue credits on a local Anvil (chain id 31337 only)",
    )
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        command.upgrade(alembic_config(), "head")
    elif args.cmd == "revision":
        command.revision(
            alembic_config(),
            message=args.message,
            autogenerate=args.autogenerate,
            rev_id=args.rev_id,
        )
    elif args.cmd == "bootstrap":
        bootstrap()
    elif args.cmd == "create-user":
        from pydantic import EmailStr, TypeAdapter, ValidationError

        try:  # same rules as the API, otherwise the account could never log in
            TypeAdapter(EmailStr).validate_python(args.email)
        except ValidationError as exc:
            raise SystemExit(f"Invalid email: {exc.errors()[0]['msg']}") from exc
        password = getpass.getpass("Password (min 10 chars): ")
        if len(password) < 10:
            raise SystemExit("Password must be at least 10 characters")
        asyncio.run(create_user(args.email, args.name, password, args.role))
    elif args.cmd == "seed-demo":
        seed_demo_command(args.seed, args.with_chain)


def seed_demo_command(seed: int, with_chain: bool) -> None:
    import logging

    from aqua_api.demo import DemoSeedError, print_result, refuse_unsafe, seed_demo

    logging.getLogger("httpx").setLevel(logging.WARNING)  # in-process API calls, not news
    settings = get_settings()
    try:
        refuse_unsafe(settings, with_chain=with_chain)  # before touching any service
        print_result(asyncio.run(seed_demo(settings, seed=seed, with_chain=with_chain)))
    except DemoSeedError as exc:
        raise SystemExit(f"seed-demo: {exc}") from exc


if __name__ == "__main__":
    main()
