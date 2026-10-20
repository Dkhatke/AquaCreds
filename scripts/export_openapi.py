"""Write the FastAPI OpenAPI schema to packages/api-client/openapi.json (deterministic).

Usage: uv run python scripts/export_openapi.py [--out PATH] [--check]

The app is built with development settings and no `.env` file, so no database, object storage or
secrets are needed: engine creation is lazy and nothing connects until a request is served.
Output has sorted keys and a trailing newline, so it only changes when the API changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "packages" / "api-client" / "openapi.json"


def build_schema() -> dict[str, object]:
    # Force safe, environment-independent settings before aqua_api.main builds its module-level app.
    os.environ["APP_ENV"] = "development"
    os.environ["LOG_JSON"] = "false"
    os.environ["LOG_LEVEL"] = "WARNING"

    from aqua_api.main import create_app
    from aqua_core.settings import Settings

    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    return app.openapi()


def render(schema: dict[str, object]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the file on disk is out of date"
    )
    args = parser.parse_args(argv)

    text = render(build_schema())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date; run `pnpm gen:api`", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
