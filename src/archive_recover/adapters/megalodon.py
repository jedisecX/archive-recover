"""Megalodon.jp (ウェブ魚拓) adapter.

Capabilities:
- Seed URLs of the snapshot form ``https://megalodon.jp/YYYY-MMDD-HHMM-SS/<original>``
  are parsed (timestamp + original URL) and retrieved
- Non-snapshot seeds are recorded as metadata-only with a gyo.tc lookup hint
- Non-ASCII / Japanese paths are preserved

Limitations:
- No documented public bulk domain enumeration API
- On-demand service; coverage depends on prior user submissions
- Never falls back to fetching the live original site
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, unquote, urlsplit

from .base import BaseAdapter, CaptureRecord, DiscoveryResult

_SNAP_RE = re.compile(
    r"^https?://(?:www\.)?megalodon\.jp/(\d{4})-(\d{4})-(\d{4})-(\d{2})/(.+)$", re.I
)


def parse_megalodon_snapshot(url: str) -> Optional[tuple[str, datetime, str]]:
    """Return (timestamp_str, datetime, original_url) for a megalodon snapshot URL."""
    m = _SNAP_RE.match(url.strip())
    if not m:
        return None
    y, mmdd, hhmm, ss, original = m.groups()
    ts = f"{y}{mmdd}{hhmm}{ss}"
    try:
        dt = datetime.strptime(ts, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    original = unquote(original)
    if "://" not in original:
        # megalodon collapses "https://" to "https:/" in some links
        original = re.sub(r"^(https?):/+", r"\1://", original)
        if "://" not in original:
            original = "http://" + original
    return ts, dt, original


class MegalodonAdapter(BaseAdapter):
    name = "megalodon"
    base_url = "https://megalodon.jp"
    allowed_hosts = ("megalodon.jp",)

    def supports_domain_enumeration(self) -> bool:
        return False

    def limitations(self) -> str:
        return (
            "On-demand Japanese archive. No reliable public domain-wide index. "
            "Supports known snapshot URLs (seeds); other seeds are kept as metadata with a "
            "gyo.tc lookup hint. Domain enumeration requires seed URLs or external discovery."
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
        notes = (
            "Megalodon domain-wide indexing is not available via public API. "
            "Use snapshot seed URLs: https://megalodon.jp/YYYY-MMDD-HHMM-SS/URL"
        )
        captures: list[CaptureRecord] = []
        for url in (seed_urls or [])[:limit]:
            parsed = parse_megalodon_snapshot(url)
            if parsed:
                ts, dt, original = parsed
                if (since and dt < since) or (until and dt > until):
                    continue
                captures.append(CaptureRecord(
                    original_url=original, snapshot_url=url.strip(), timestamp=dt,
                    timestamp_str=ts, source=self.name, extra={"seed": True},
                ))
            else:
                captures.append(CaptureRecord(
                    original_url=url.strip(), source=self.name,
                    extra={"seed": True, "gyo_tc": f"https://gyo.tc/{quote(url.strip(), safe='')}",
                           "note": "no snapshot URL; look up manually via gyo.tc"},
                ))
        return DiscoveryResult(captures=captures, complete=True, source_notes=notes)

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        target = capture.snapshot_url
        if not target or (urlsplit(target).hostname or "").lower().removeprefix("www.") != "megalodon.jp":
            raise ValueError("No megalodon snapshot URL (live-site fetch is not performed)")
        res = await self.fetcher.get(target)
        return res.content, self._meta(res)
