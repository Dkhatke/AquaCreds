"""A local STAC API serving recorded item JSON, for provider and fallback tests (no network).

Fixtures in `fixtures/stac/` are real items recorded on 2026-10-20 over Bhitarkanika (MGRS 45QVC),
trimmed to the properties and assets the pipeline reads:
- `pc_sentinel-2-l2a.json`: Planetary Computer, 2024-01-14 (baseline 05.10).
- `es_sentinel-2-l2a.json`: Earth Search v1 `sentinel-2-l2a`: the same 2024-01-14 scene
  (`earthsearch:boa_offset_applied: true`), 2022-02-13 (04.00, flag false), and the 2021-12-05
  scene listed twice (`_0_` baseline 03.01 and its `_1_` 05.00 reprocessing).
- `es_sentinel-2-c1-l2a.json`: Earth Search Collection 1, the same 2024-01-14 scene.
- `pc_sentinel-1-rtc.json`: three Sentinel-1A RTC passes in July 2024.

Each catalogue is mounted under `/<name>`; its behaviour ("ok", an HTTP status such as 503, or
"slow") can be changed between requests via `StacServer.behaviour`.
"""

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "stac"

CATALOGUES = {
    "pc": ["pc_sentinel-2-l2a.json", "pc_sentinel-1-rtc.json"],
    "es": ["es_sentinel-2-l2a.json", "es_sentinel-2-c1-l2a.json"],
}


def fixture_items(name: str) -> list[dict[str, Any]]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    features: list[dict[str, Any]] = data["features"]
    return features


def _collection_of(filename: str) -> str:
    return filename.split("_", 1)[1].removesuffix(".json")


def _parse(value: str, end: bool) -> datetime:
    if value in ("..", ""):
        return datetime.max.replace(tzinfo=UTC) if end else datetime.min.replace(tzinfo=UTC)
    if len(value) == 10:  # a bare date covers the whole day
        value += "T23:59:59Z" if end else "T00:00:00Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # a client that timed out closes the socket mid-reply: expected in "slow" mode


class StacServer:
    def __init__(self) -> None:
        self.behaviour: dict[str, str | int] = dict.fromkeys(CATALOGUES, "ok")
        self.requests: list[tuple[str, str]] = []  # (catalogue, path)
        self.items = {
            cat: {_collection_of(f): fixture_items(f) for f in files}
            for cat, files in CATALOGUES.items()
        }
        self._server = _QuietServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"

    def url(self, catalogue: str) -> str:
        return f"{self.base}/{catalogue}"

    def _search(self, catalogue: str, body: dict[str, Any]) -> dict[str, Any]:
        collections = body.get("collections") or list(self.items[catalogue])
        start, end = _parse("..", False), _parse("..", True)
        if body.get("datetime"):
            a, _, b = str(body["datetime"]).partition("/")
            start, end = _parse(a, False), _parse(b or a, True)
        max_cloud = body.get("query", {}).get("eo:cloud_cover", {}).get("lt")
        found = []
        for collection in collections:
            for item in self.items[catalogue].get(collection, []):
                when = datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00"))
                cloud = item["properties"].get("eo:cloud_cover")
                if not start <= when <= end:
                    continue
                if max_cloud is not None and cloud is not None and cloud >= max_cloud:
                    continue
                found.append({**item, "collection": collection})
        return {"type": "FeatureCollection", "features": found, "links": []}

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep pytest output clean
                pass

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self) -> tuple[str, str] | None:
                parts = self.path.split("?")[0].strip("/").split("/", 1)
                if parts[0] not in CATALOGUES:
                    self._reply(404, {"error": "unknown catalogue"})
                    return None
                catalogue, rest = parts[0], (parts[1] if len(parts) > 1 else "")
                server.requests.append((catalogue, rest))
                mode = server.behaviour[catalogue]
                if mode == "slow":
                    time.sleep(2.0)
                elif isinstance(mode, int):
                    self._reply(mode, {"code": mode, "description": "simulated failure"})
                    return None
                return catalogue, rest

            def do_GET(self) -> None:
                route = self._route()
                if route is None:
                    return
                catalogue, rest = route
                if rest:
                    self._reply(404, {"error": "not found"})
                    return
                root = server.url(catalogue)
                self._reply(
                    200,
                    {
                        "type": "Catalog",
                        "id": catalogue,
                        "description": "recorded fixtures",
                        "stac_version": "1.0.0",
                        "conformsTo": [
                            "https://api.stacspec.org/v1.0.0/core",
                            "https://api.stacspec.org/v1.0.0/item-search",
                            "https://api.stacspec.org/v1.0.0/item-search#query",
                        ],
                        "links": [
                            {"rel": "self", "href": root, "type": "application/json"},
                            {"rel": "root", "href": root, "type": "application/json"},
                            {
                                "rel": "search",
                                "href": f"{root}/search",
                                "type": "application/geo+json",
                                "method": "POST",
                            },
                        ],
                    },
                )

            def do_POST(self) -> None:
                route = self._route()
                if route is None:
                    return
                catalogue, rest = route
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if rest != "search":
                    self._reply(404, {"error": "not found"})
                    return
                self._reply(200, server._search(catalogue, body))

        return Handler

    @contextmanager
    def running(self) -> Iterator["StacServer"]:
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self._server.shutdown()
            self._server.server_close()
