"""Memento TimeMap adapter for standards-based discovery.

Uses RFC 7089 TimeMap (application/link-format) where archives expose it.
Internet Archive: https://web.archive.org/web/timemap/link/{url}
Other Memento-compliant archives similarly.

Useful for known URLs; less efficient for domain-wide than CDX.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import re

import httpx

from .base import BaseAdapter, CaptureRecord, DiscoveryResult


class MementoAdapter(BaseAdapter):
    name = "memento"
    base_url = "https://web.archive.org"  # default; configurable per archive

    def supports_domain_enumeration(self) -> bool:
        return False  # TimeMaps are per-URL

    def limitations(self) -> str:
        return (
            "Per-URL TimeMaps only. Efficient for known URLs across participating archives. "
            "Domain-wide requires external URL lists. Aggregators (e.g. former TimeTravel) "
            "may be slow or unavailable."
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
    ) -> DiscoveryResult:
        if not seed_urls:
            return DiscoveryResult(
                captures=[],
                complete=True,
                source_notes="Memento TimeMaps require seed URLs; domain enumeration not supported.",
            )

        captures: list[CaptureRecord] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for url in seed_urls[:50]:  # conservative
                timemap_url = f"https://web.archive.org/web/timemap/link/{url}"
                try:
                    resp = await client.get(timemap_url)
                    if resp.status_code != 200:
                        continue
                    # Very simple link-format parser
                    for line in resp.text.splitlines():
                        m = re.search(r'<([^>]+)>;\s*rel="[^"]*memento[^"]*"', line, re.I)
                        if m:
                            memento_uri = m.group(1)
                            dt_match = re.search(r'datetime="([^"]+)"', line)
                            captures.append(
                                CaptureRecord(
                                    original_url=url,
                                    snapshot_url=memento_uri,
                                    timestamp_str=dt_match.group(1) if dt_match else None,
                                    source=self.name,
                                    extra={"timemap": True},
                                )
                            )
                            if len(captures) >= limit:
                                break
                except Exception:
                    continue

        return DiscoveryResult(captures=captures, complete=True, source_notes="Parsed IA TimeMap")

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        # Delegate to snapshot URL
        if not capture.snapshot_url:
            raise ValueError("No memento URI")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(capture.snapshot_url)
            return resp.content, {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "final_url": str(resp.url),
            }
