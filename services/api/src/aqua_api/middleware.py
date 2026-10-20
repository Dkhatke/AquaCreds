"""Security response headers (defence in depth; Caddy sits in front in production).

The API only serves JSON, so the CSP forbids everything: no scripts, frames, forms or base URIs.
The interactive docs (`/docs`, `/redoc`, development only) load Swagger UI / ReDoc assets from a
CDN, so those HTML pages are exempt from the CSP. Auth responses carry tokens and are `no-store`.
"""

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

JSON_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
BASE_HEADERS: dict[str, str] = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


class SecurityHeadersMiddleware:
    def __init__(
        self, app: ASGIApp, *, csp_exempt_paths: Iterable[str] = (), no_store_prefix: str = ""
    ) -> None:
        self.app = app
        self.csp_exempt = frozenset(p for p in csp_exempt_paths if p)
        self.no_store_prefix = no_store_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                extra = dict(BASE_HEADERS)
                if path not in self.csp_exempt:
                    extra["content-security-policy"] = JSON_CSP
                if self.no_store_prefix and path.startswith(self.no_store_prefix):
                    extra["cache-control"] = "no-store"
                for name, value in extra.items():
                    if name.encode() not in present:
                        headers.append((name.encode(), value.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
