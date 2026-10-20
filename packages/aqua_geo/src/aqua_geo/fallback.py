"""STAC provider fallback: try catalogues in order and move on only for transient or access errors.

Which errors justify the next provider (RESEARCH_NOTES Q2, IMPLEMENTATION_PLAN risk "Planetary
Computer policy/availability change"):
- timeouts and connection failures (DNS, reset, refused);
- HTTP 5xx, 408 and 429 (the service is down or throttling us);
- HTTP 401/403 (SAS token or subscription key refused, or a collection newly gated).

Anything else (HTTP 400/404, a malformed query, a bug) is raised immediately: another provider would
hide a configuration error rather than fix it.

pystac-client wraps every `requests` failure in `APIError(str(err))` without a status code, and
GDAL/rasterio report HTTP errors only in the message, so the classification walks the exception
chain and also reads the status code out of the message.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from typing import Protocol

RETRYABLE_STATUS: frozenset[int] = frozenset({401, 403, 408, 429, 500, 502, 503, 504})

_STATUS_PATTERNS = (
    re.compile(r"HTTP response code:\s*(\d{3})"),  # GDAL / rasterio
    re.compile(r"\b(\d{3}) (?:Client|Server) Error\b"),  # requests.HTTPError
    re.compile(r"\bstatus(?:[ _]code)?\s*[:=]?\s*(\d{3})\b", re.IGNORECASE),
)
_TRANSIENT_WORDS = (
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "connection aborted",
    "remote end closed connection",
    "max retries exceeded",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname provided",
)


class Named(Protocol):
    @property
    def name(self) -> str: ...


class AllProvidersFailedError(RuntimeError):
    """Every configured provider failed with a retryable error."""

    def __init__(self, errors: Sequence[tuple[str, str]]) -> None:
        self.errors = list(errors)
        detail = "; ".join(f"{name}: {err}" for name, err in self.errors)
        super().__init__(f"all STAC providers failed ({detail})")


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < 10:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def http_status(exc: BaseException) -> int | None:
    """HTTP status carried by `exc` itself (attribute, `response`, or message), if any."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    message = str(exc)
    for pattern in _STATUS_PATTERNS:
        match = pattern.search(message)
        if match:
            return int(match.group(1))
    return None


def is_retryable(exc: BaseException) -> bool:
    """True for timeouts, connection failures, HTTP 5xx/408/429 and HTTP 401/403."""
    import requests

    for err in _chain(exc):
        if isinstance(err, (TimeoutError, ConnectionError, requests.Timeout)):
            return True
        if isinstance(err, requests.ConnectionError):
            return True
        status = http_status(err)
        if status is not None:
            return status in RETRYABLE_STATUS
        message = str(err).lower()
        if any(word in message for word in _TRANSIENT_WORDS):
            return True
    return False


def describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def first_successful[P: Named, T](
    providers: Sequence[P],
    attempt: Callable[[P], T],
    *,
    on_fallback: Callable[[P, BaseException], None] | None = None,
) -> tuple[P, T]:
    """Return (provider, result) of the first provider whose `attempt` succeeds.

    A retryable failure moves on to the next provider (after calling `on_fallback`); any other
    failure is raised as is. If every provider fails, raises `AllProvidersFailedError`."""
    if not providers:
        raise ValueError("no STAC providers configured")
    errors: list[tuple[str, str]] = []
    for provider in providers:
        try:
            return provider, attempt(provider)
        except Exception as exc:
            if not is_retryable(exc):
                raise
            errors.append((provider.name, describe(exc)))
            if on_fallback is not None:
                on_fallback(provider, exc)
    raise AllProvidersFailedError(errors)
