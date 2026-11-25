"""Shared HTTP client: timeout, one retry, TTL cache, typed failures. Never invents a value.

Every tool goes through `HttpClient.get_json`. It returns a `FetchOutcome` and does not raise
for network or HTTP problems, so a failing source can only become `unavailable`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from elnino_planner.logging_setup import log_event
from elnino_planner.settings import Settings
from elnino_planner.tools.result import FailureReason, utcnow

log = logging.getLogger("planner.http")


@dataclass
class FetchOutcome:
    ok: bool
    json: Any = None
    fetched_at: datetime | None = None
    from_cache: bool = False
    reason: FailureReason | None = None
    http_status: int | None = None
    attempts: int = 0
    duration_ms: float = 0.0


class TTLCache:
    """In-memory. Stores (value, fetched_at, expires_monotonic). Injectable clock for tests."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._items: dict[Any, tuple[Any, datetime, float]] = {}

    def get(self, key):
        hit = self._items.get(key)
        if not hit:
            return None
        value, fetched_at, expires = hit
        if self._clock() >= expires:
            del self._items[key]
            return None
        return value, fetched_at

    def put(self, key, value, fetched_at: datetime, ttl_s: float) -> None:
        self._items[key] = (value, fetched_at, self._clock() + ttl_s)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


class HttpClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 cache: TTLCache | None = None, sleep: Callable[[float], Any] | None = None):
        self.settings = settings
        self.cache = cache if cache is not None else TTLCache()
        self._client = httpx.AsyncClient(
            timeout=settings.http_timeout_s,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(self, url: str, *, params: dict[str, Any] | None = None, ttl_s: float = 0,
                       tool: str = "http") -> FetchOutcome:
        """GET and parse JSON. `tool` is only a log label. Params are never logged (may hold a point)."""
        start = time.perf_counter()
        key = (url, tuple(sorted((params or {}).items())))
        host = urlsplit(url).netloc

        if ttl_s > 0 and (hit := self.cache.get(key)):
            value, fetched_at = hit  # keeps the ORIGINAL fetched_at, per contract
            out = FetchOutcome(True, value, fetched_at, from_cache=True)
            self._log(tool, out, host, start)
            return out

        out = await self._fetch_with_retry(url, params)
        if out.ok and ttl_s > 0:
            self.cache.put(key, out.json, out.fetched_at, ttl_s)  # only successes are cached
        self._log(tool, out, host, start)
        return out

    async def _fetch_with_retry(self, url: str, params) -> FetchOutcome:
        out = FetchOutcome(False)
        for attempt in range(1, self.settings.http_retries + 2):
            out = await self._fetch_once(url, params)
            out.attempts = attempt
            retryable = out.reason in (FailureReason.TIMEOUT, FailureReason.UPSTREAM_ERROR)
            if out.ok or not retryable:
                break
        return out

    async def _fetch_once(self, url: str, params) -> FetchOutcome:
        try:
            resp = await self._client.get(url, params=params)
        except httpx.TimeoutException:
            return FetchOutcome(False, reason=FailureReason.TIMEOUT)
        except httpx.HTTPError:
            return FetchOutcome(False, reason=FailureReason.NETWORK_ERROR)

        code = resp.status_code
        if code >= 500:
            return FetchOutcome(False, reason=FailureReason.UPSTREAM_ERROR, http_status=code)
        if code >= 400:
            return FetchOutcome(False, reason=FailureReason.CLIENT_ERROR, http_status=code)
        try:
            body = resp.json()
        except ValueError:
            return FetchOutcome(False, reason=FailureReason.BAD_RESPONSE, http_status=code)
        return FetchOutcome(True, body, utcnow(), http_status=code)

    def _log(self, tool: str, out: FetchOutcome, host: str, start: float) -> None:
        out.duration_ms = (time.perf_counter() - start) * 1000
        log_event(log, "http_get", tool=tool, status="ok" if out.ok else "failed",
                  duration_ms=out.duration_ms, reason=out.reason.value if out.reason else None,
                  attempts=out.attempts or None, cached=out.from_cache, host=host,
                  http_status=out.http_status)
