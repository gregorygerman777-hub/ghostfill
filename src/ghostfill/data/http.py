"""HTTP client with exponential backoff, HTTP 429 handling and per-host pacing."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from ..logutil import redact_url

log = logging.getLogger(__name__)

# Minimum seconds between requests to a host. GeckoTerminal's free tier is strict in practice.
DEFAULT_MIN_INTERVAL = {
    "api.geckoterminal.com": 6.0,
    "api.dexscreener.com": 0.25,
    "api.rugcheck.xyz": 1.0,
}
# Public data APIs whose URL paths carry no secrets and are safe to log in full.
PUBLIC_HOSTS = frozenset({"api.geckoterminal.com", "api.dexscreener.com", "api.rugcheck.xyz"})


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname in PUBLIC_HOSTS:
        return f"{parts.scheme}://{parts.hostname}{parts.path}"
    return redact_url(url)


class HttpError(Exception):
    def __init__(self, status: int | None, url: str, detail: str = ""):
        self.status = status
        super().__init__(f"HTTP {status} from {safe_url(url)} {detail}".strip())


class RateLimited(Exception):
    """Raised by a body check to request a retry (for example a JSON-RPC 429 error)."""


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random) -> float:
    """Exponential backoff with jitter in [0.5, 1.0] of the nominal delay."""
    return min(cap, base * (2**attempt)) * (0.5 + 0.5 * rng.random())


def retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


class HttpClient:
    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        min_interval: dict[str, float] | None = None,
        seed: int | None = None,
        rate_limit_min_wait: float = 10.0,
    ):
        self._client = httpx.AsyncClient(
            timeout=timeout, transport=transport, headers={"user-agent": "ghostfill/0.1 (paper trading research)"}
        )
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.rate_limit_min_wait = rate_limit_min_wait
        self._sleep = sleep
        self._rng = random.Random(seed)
        self._min_interval = DEFAULT_MIN_INTERVAL if min_interval is None else min_interval
        self._last_call: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pace(self, host: str) -> None:
        gap = self._min_interval.get(host, 0.0)
        if gap <= 0:
            return
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_call.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last_call[host] = time.monotonic()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        check_body: Callable[[Any], None] | None = None,
    ) -> Any:
        host = urlsplit(url).hostname or ""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self._pace(host)
            delay = backoff_delay(attempt, self.backoff_base, self.backoff_max, self._rng)
            try:
                resp = await self._client.request(method, url, params=params, json=json)
            except httpx.TransportError as e:
                last_exc = HttpError(None, url, type(e).__name__)
            else:
                if resp.status_code == 429:
                    ra = retry_after_seconds(resp)
                    # Honour a positive Retry-After; without one (or "0") wait at least rate_limit_min_wait.
                    wait = ra if ra else self.rate_limit_min_wait
                    delay = min(self.backoff_max, max(delay, wait))
                    last_exc = HttpError(429, url, "rate limited")
                elif resp.status_code >= 500:
                    last_exc = HttpError(resp.status_code, url)
                elif resp.status_code >= 400:
                    raise HttpError(resp.status_code, url, resp.text[:200])
                else:
                    try:
                        body = resp.json()
                    except ValueError:
                        raise HttpError(resp.status_code, url, "response was not JSON") from None
                    if check_body is None:
                        return body
                    try:
                        check_body(body)
                        return body
                    except RateLimited as e:
                        last_exc = HttpError(429, url, str(e))
                        delay = min(self.backoff_max, max(delay, self.rate_limit_min_wait))
            if attempt == self.max_retries:
                break
            log.warning("retrying %s in %.1fs (attempt %d/%d): %s", safe_url(url), delay, attempt + 1,
                        self.max_retries, last_exc)
            await self._sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request_json("GET", url, params=params)
