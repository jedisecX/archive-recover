"""Memento TimeMap adapter for standards-based discovery (RFC 7089).

Queries per-URL TimeMaps (``application/link-format``) on one or more
Memento-compliant archives. Default: Internet Archive.
Useful for known URLs; domain-wide discovery needs a seed list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from archive_recover.core.http import FetchError

from .base import BaseAdapter, CaptureRecord, DiscoveryResult
from .wayback import wayback_raw_url

DEFAULT_TIMEMAPS = {
    "ia": "https://web.archive.org/web/timemap/link/{url}",
}


class MementoAdapter(BaseAdapter):
    name = "memento"
    base_url = "https://web.archive.org"

    def __init__(self, *args: Any, timemaps: Optional[dict[str, str]] = None, **kwargs: Any) -> None:
        self.timemaps = dict(timemaps or DEFAULT_TIMEMAPS)
        # Only the archives we query (and their memento hosts) may be fetched.
        hosts = {urlsplit(t.format(url="x")).hostname for t in self.timemaps.values()}
        self.allowed_hosts = tuple(sorted(h for h in hosts if h))
        super().__init__(*args, **kwargs)

    def supports_domain_enumeration(self) -> bool:
        return False  # TimeMaps are per-URL

    def limitations(self) -> str:
        return (
            "Per-URL TimeMaps only. Efficient for known URLs across participating archives. "
            "Domain-wide requires external URL lists (--seeds). Aggregators (e.g. former "
            "TimeTravel) may be slow or unavailable."
        )

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
        if not seed_urls:
            return DiscoveryResult(
                captures=[], complete=True,
                source_notes="Memento TimeMaps require seed URLs; domain enumeration not supported.",
            )
        # resume_key = index of the next seed to query
        start = int(resume_key) if resume_key and resume_key.isdigit() else 0
        captures: list[CaptureRecord] = []
        errors: list[str] = []
        i = start
        for i in range(start, len(seed_urls)):
            url = seed_urls[i]
            for label, template in self.timemaps.items():
                tm_url = template.format(url=quote(url, safe=":/?&=%+@,;~!$*'()"))
                try:
                    recs = await self._timemap_captures(
                        tm_url, url, since=since, until=until, limit=limit - len(captures)
                    )
                except FetchError as exc:
                    errors.append(f"{label} {url}: {exc}")
                    continue
                for r in recs:
                    r.extra["archive"] = label
                captures.extend(recs)
            if len(captures) >= limit:
                break
        next_index = i + 1
        more = next_index < len(seed_urls)
        return DiscoveryResult(
            captures=captures,
            next_resume_key=str(next_index) if more else None,
            complete=not more and not errors,
            error="; ".join(errors[:5]) + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else "")
            if errors else None,
            source_notes=f"TimeMaps from {', '.join(self.timemaps)}",
        )

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        if not capture.snapshot_url:
            raise ValueError("No memento URI")
        url = capture.snapshot_url
        if urlsplit(url).hostname == "web.archive.org":
            url = wayback_raw_url(url)
        res = await self.fetcher.get(url)
        return res.content, self._meta(res, raw_url=url)
