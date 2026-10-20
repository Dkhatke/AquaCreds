"""Fixed-window rate limiting for the auth endpoints, stored in Valkey/Redis.

Each limit is a counter per (scope, subject, window) key: `INCR` + `EXPIRE` in one transaction.
Subjects are the client IP (per-IP limits) or a SHA-256 of the account email / user id (per-account
limits; no addresses in Valkey keys). A request over the limit gets 429 with `Retry-After`.

The client IP honours `X-Forwarded-For` only when the TCP peer is a trusted proxy
(`TRUSTED_PROXY_IPS`, e.g. Caddy's Docker network); the right-most untrusted hop is the client.

If Valkey is unreachable the limiter fails OPEN (logs an error): rate limiting is defence in depth
on top of Argon2id + MFA, and a broker outage must not lock every user out.
"""

import hashlib
import ipaddress
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from aqua_core.logging import get_logger
from aqua_core.settings import Settings, parse_rate

log = get_logger("aqua_api.ratelimit")

KEY_PREFIX = "aquacreds:rl"


class RateLimitBackend(Protocol):
    async def hit(self, key: str, window_s: int) -> int:
        """Increment the counter for `key` (expiring after `window_s`) and return the new count."""
        ...

    async def close(self) -> None: ...


class RedisRateLimitBackend:
    def __init__(self, url: str) -> None:
        self._client = aioredis.from_url(url, socket_timeout=1.0, socket_connect_timeout=1.0)

    async def hit(self, key: str, window_s: int) -> int:
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_s + 1, nx=True)
            count, _ = await pipe.execute()
        return int(count)

    async def close(self) -> None:
        await self._client.aclose()


@dataclass
class MemoryRateLimitBackend:
    """In-process backend for tests and single-process development."""

    clock: Callable[[], float] = time.time
    counters: dict[str, tuple[int, float]] = field(default_factory=dict)

    async def hit(self, key: str, window_s: int) -> int:
        now = self.clock()
        count, expires = self.counters.get(key, (0, 0.0))
        if expires <= now:
            count, expires = 0, now + window_s + 1
        self.counters[key] = (count + 1, expires)
        return count + 1

    async def close(self) -> None:
        self.counters.clear()


def _subject_hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:32]


def client_ip(
    request: Request, trusted: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> str:
    peer = request.client.host if request.client else "unknown"

    def is_trusted(host: str) -> bool:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in trusted)

    if not trusted or not is_trusted(peer):
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not is_trusted(hop):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer  # malformed header from a trusted proxy: fall back to the peer
    return hops[0] if hops else peer


class RateLimiter:
    def __init__(
        self,
        settings: Settings,
        backend: RateLimitBackend,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.clock = clock
        self.trusted = [ipaddress.ip_network(n, strict=False) for n in settings.trusted_proxy_ips]

    async def enforce(self, scope: str, rate: str, subject: str) -> None:
        """Count one request for `subject` under `scope`; raise 429 when over `rate`."""
        if not self.settings.rate_limit_enabled:
            return
        limit, window_s = parse_rate(rate)
        now = self.clock()
        window = math.floor(now / window_s)
        key = f"{KEY_PREFIX}:{scope}:{_subject_hash(subject)}:{window}"
        try:
            count = await self.backend.hit(key, window_s)
        except (RedisError, OSError) as exc:
            log.error("ratelimit.backend_unavailable", scope=scope, error=str(exc)[:200])
            return
        if count > limit:
            retry_after = max(1, math.ceil((window + 1) * window_s - now))
            log.warning("ratelimit.exceeded", scope=scope, count=count, limit=limit)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests; try again later",
                headers={"Retry-After": str(retry_after)},
            )

    async def enforce_ip(self, request: Request, scope: str, rate: str) -> None:
        await self.enforce(f"{scope}:ip", rate, client_ip(request, self.trusted))

    async def enforce_account(self, scope: str, rate: str, account: str) -> None:
        await self.enforce(f"{scope}:account", rate, account)


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def ip_limit(scope: str, rate_setting: str) -> Callable[[Request], object]:
    """Dependency: per-IP limit for `scope`, with the rate read from the named setting."""

    async def _dependency(request: Request) -> None:
        limiter = get_rate_limiter(request)
        await limiter.enforce_ip(request, scope, getattr(limiter.settings, rate_setting))

    return _dependency
