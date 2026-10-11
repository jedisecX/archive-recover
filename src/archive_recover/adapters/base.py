"""Base classes and shared types for archive adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from archive_recover.core.http import Fetcher, FetchError, FetchResult, HttpSettings
from archive_recover.core.linkformat import parse_link_format
from archive_recover.core.utils import parse_timestamp


@dataclass
class CaptureRecord:
    """Normalized capture metadata from any source."""

    original_url: str
    timestamp: Optional[datetime] = None
    timestamp_str: Optional[str] = None  # raw YYYYMMDDHHMMSS or similar
    snapshot_url: Optional[str] = None
    mime_type: Optional[str] = None
    status_code: Optional[int] = None
    digest: Optional[str] = None  # source-provided (e.g. base32 SHA-1 for IA)
    length: Optional[int] = None
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def replay_url(self) -> Optional[str]:
        return self.snapshot_url


@dataclass
class DiscoveryResult:
    """Result of one discovery page/pass."""

    captures: list[CaptureRecord]
    next_resume_key: Optional[str] = None
    complete: bool = False
    error: Optional[str] = None
    source_notes: Optional[str] = None


class BaseAdapter(ABC):
    """Independent adapter interface. Failures must not affect other adapters.

    Every network call goes through ``self.fetcher``, which enforces the
    adapter's ``allowed_hosts`` (on every redirect hop), rate limiting,
    retries and byte limits.
    """

    name: str = "base"
    base_url: str = ""
    allowed_hosts: tuple[str, ...] = ()

    def __init__(
        self,
        rate_limit_delay: float = 1.0,
        timeout: float = 30.0,
        settings: Optional[HttpSettings] = None,
    ) -> None:
        self.settings = settings or HttpSettings(rate_limit_delay=rate_limit_delay, timeout=timeout)
        self.rate_limit_delay = self.settings.rate_limit_delay
        self.timeout = self.settings.timeout
        self.fetcher = Fetcher(self.settings, self.allowed_hosts or None)

    async def close(self) -> None:
        await self.fetcher.close()

    @abstractmethod
    async def discover(
        self,
        domain: str,
        *,
        prefix: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        seed_urls: Optional[list[str]] = None,
        resume_key: Optional[str] = None,
        limit: int = 1000,
        include_subdomains: bool = True,
    ) -> DiscoveryResult:
        """Enumerate captures (one page). Paginated via ``resume_key`` where supported."""

    @abstractmethod
    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        """Fetch archived payload. Returns (content, metadata). Never fetches the live site."""

    def supports_domain_enumeration(self) -> bool:
        return False

    def needs_seeds(self) -> bool:
        return not self.supports_domain_enumeration()

    def limitations(self) -> str:
        return "Generic adapter."

    # -- shared helpers ------------------------------------------------------
    @staticmethod
    def _meta(res: FetchResult, **extra: Any) -> dict[str, Any]:
        meta = {
            "status_code": res.status_code,
            "headers": res.headers,
            "final_url": res.final_url,
            "content_type": res.content_type,
        }
        meta.update(extra)
        return meta

    async def _timemap_captures(
        self,
        timemap_url: str,
        original: str,
        *,
        since: Optional[datetime],
        until: Optional[datetime],
        limit: int,
    ) -> list[CaptureRecord]:
        """Fetch one TimeMap and convert memento links to CaptureRecords.

        Raises :class:`FetchError` on failure so the caller can report it
        instead of silently claiming completeness.
        """
        try:
            res = await self.fetcher.get(timemap_url)
        except FetchError as exc:
            if exc.status in (404, 410):
                return []  # no captures for this URL is a valid answer
            raise
        out: list[CaptureRecord] = []
        for link in parse_link_format(res.text):
            if not link.is_memento:
                continue
            ts = parse_timestamp(link.datetime)
            if since and ts and ts < since:
                continue
            if until and ts and ts > until:
                continue
            out.append(
                CaptureRecord(
                    original_url=original,
                    snapshot_url=link.uri,
                    timestamp=ts,
                    timestamp_str=ts.strftime("%Y%m%d%H%M%S") if ts else link.datetime,
                    source=self.name,
                    extra={"timemap": timemap_url, "rel": sorted(link.rels)},
                )
            )
            if len(out) >= limit:
                break
        return out
