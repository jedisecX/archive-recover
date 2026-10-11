"""Shared HTTP layer: guarded client, per-host rate limiting, retries, size caps.

Every adapter request goes through :class:`Fetcher` so that the SSRF guard,
politeness delay, retry/backoff and byte limits apply uniformly — including to
each redirect hop.
"""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import httpx

from archive_recover import __version__
from archive_recover.core.utils import is_safe_url

DEFAULT_USER_AGENT = (
    f"archive-recover/{__version__} (+https://github.com/jedisecX/archive-recover)"
)
RETRY_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}


class FetchError(Exception):
    """Raised when a request ultimately fails. ``category`` feeds the errors table."""

    def __init__(self, message: str, category: str = "http", status: Optional[int] = None):
        super().__init__(message)
        self.category = category
        self.status = status


class BlockedURLError(FetchError):
    def __init__(self, url: str):
        super().__init__(f"blocked by SSRF/host policy: {url}", category="blocked")


class TooLargeError(FetchError):
    def __init__(self, limit: int):
        super().__init__(f"response exceeded max_object_bytes={limit}", category="too_large")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    elapsed: float = 0.0

    @property
    def content_type(self) -> Optional[str]:
        return self.headers.get("content-type")

    @property
    def text(self) -> str:
        enc = "utf-8"
        ct = self.content_type or ""
        if "charset=" in ct:
            enc = ct.split("charset=", 1)[1].split(";")[0].strip() or enc
        try:
            return self.content.decode(enc, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


@dataclass
class HttpSettings:
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 30.0
    rate_limit_delay: float = 1.0  # minimum seconds between requests to one host
    max_retries: int = 4
    backoff_base: float = 2.0
    backoff_max: float = 60.0
    max_object_bytes: int = 50 * 1024 * 1024
    max_redirects: int = 10
    transport: Optional[httpx.AsyncBaseTransport] = None  # injected in tests
    sleep: Any = field(default=asyncio.sleep, repr=False)  # injectable for tests


class HostRateLimiter:
    """Minimum interval between request *starts* per host, shared across tasks."""

    def __init__(self, delay: float, sleep=asyncio.sleep):
        self.delay = delay
        self._sleep = sleep
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> None:
        if self.delay <= 0:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            ready = self._next.get(host, 0.0)
            if ready > now:
                await self._sleep(ready - now)
            self._next[host] = max(now, ready) + self.delay

    def penalize(self, host: str, seconds: float) -> None:
        """Push the host's next slot out (used for Retry-After / 429)."""
        self._next[host] = max(self._next.get(host, 0.0), time.monotonic() + seconds)


def _retry_after(headers: httpx.Headers) -> Optional[float]:
    value = headers.get("retry-after")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value.strip())
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Fetcher:
    """Guarded async HTTP client bound to a set of allowed archive hosts."""

    def __init__(self, settings: HttpSettings, allowed_hosts: Optional[Iterable[str]] = None):
        self.settings = settings
        self.allowed_hosts = list(allowed_hosts) if allowed_hosts is not None else None
        self.limiter = HostRateLimiter(settings.rate_limit_delay, settings.sleep)
        self._client: Optional[httpx.AsyncClient] = None

    # -- lifecycle -----------------------------------------------------------
    async def __aenter__(self) -> "Fetcher":
        await self.open()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.timeout,
                follow_redirects=True,
                max_redirects=self.settings.max_redirects,
                headers={"User-Agent": self.settings.user_agent},
                transport=self.settings.transport,
                event_hooks={"request": [self._guard_hook]},
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _guard_hook(self, request: httpx.Request) -> None:
        # Runs for the initial request AND every redirect hop.
        if not is_safe_url(str(request.url), self.allowed_hosts):
            raise BlockedURLError(str(request.url))

    # -- requests ------------------------------------------------------------
    async def get(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        accept_status: Iterable[int] = (200,),
        max_bytes: Optional[int] = None,
    ) -> FetchResult:
        """GET with politeness delay, retries on transient failures and a byte cap.

        Statuses in ``accept_status`` are returned; other 4xx raise immediately;
        429/5xx and transport errors are retried with exponential backoff + jitter.
        """
        if not is_safe_url(url, self.allowed_hosts):
            raise BlockedURLError(url)
        await self.open()
        assert self._client is not None
        accept = set(accept_status)
        limit = max_bytes or self.settings.max_object_bytes
        host = httpx.URL(url).host
        last_exc: Optional[Exception] = None

        for attempt in range(self.settings.max_retries + 1):
            await self.limiter.wait(host)
            started = time.monotonic()
            try:
                async with self._client.stream("GET", url, params=params) as resp:
                    if resp.status_code in accept:
                        declared = resp.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > limit:
                            raise TooLargeError(limit)
                        buf = bytearray()
                        async for chunk in resp.aiter_bytes():
                            buf.extend(chunk)
                            if len(buf) > limit:
                                raise TooLargeError(limit)
                        return FetchResult(
                            url=url,
                            final_url=str(resp.url),
                            status_code=resp.status_code,
                            headers={k.lower(): v for k, v in resp.headers.items()},
                            content=bytes(buf),
                            elapsed=time.monotonic() - started,
                        )
                    if resp.status_code in RETRY_STATUS:
                        wait = _retry_after(resp.headers)
                        last_exc = FetchError(
                            f"HTTP {resp.status_code} from {resp.url}",
                            category="rate_limited" if resp.status_code == 429 else "server",
                            status=resp.status_code,
                        )
                        if wait is not None:
                            self.limiter.penalize(host, min(wait, self.settings.backoff_max))
                    else:
                        raise FetchError(
                            f"HTTP {resp.status_code} from {resp.url}",
                            category="not_found" if resp.status_code in (404, 410) else "http",
                            status=resp.status_code,
                        )
            except FetchError:
                raise
            except httpx.TooManyRedirects as exc:
                raise FetchError(str(exc), category="redirects") from exc
            except httpx.TransportError as exc:
                last_exc = FetchError(f"{type(exc).__name__}: {exc}", category="network")

            if attempt < self.settings.max_retries:
                delay = min(self.settings.backoff_max, self.settings.backoff_base ** attempt)
                await self.settings.sleep(delay * (0.5 + random.random() / 2))

        assert last_exc is not None
        raise last_exc
