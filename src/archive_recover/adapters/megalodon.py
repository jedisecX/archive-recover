"""Megalodon.jp (ウェブ魚拓) adapter.

Capabilities:
- Retrieval of known snapshot URLs of form https://megalodon.jp/YYYY-MMDD-HHMM-SS/original
- Search assistance via gyo.tc redirector for individual URLs
- Handling of Japanese content and non-ASCII paths

Limitations:
- No documented public bulk domain enumeration API
- On-demand service; historical coverage depends on prior user submissions
- May respect robots / noarchive
- Character encoding and URL normalization important
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import httpx

from .base import BaseAdapter, CaptureRecord, DiscoveryResult


class MegalodonAdapter(BaseAdapter):
    name = "megalodon"
    base_url = "https://megalodon.jp"

    def supports_domain_enumeration(self) -> bool:
        return False

    def limitations(self) -> str:
        return (
            "On-demand Japanese archive. No reliable public domain-wide index. "
            "Supports known snapshot URLs and gyo.tc lookup for individual pages. "
            "Handle encodings carefully. Domain enumeration requires seed URLs "
            "or external discovery."
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
        notes = (
            "Megalodon domain-wide indexing is not available via public API. "
            "Use seed URLs. Snapshot pattern: https://megalodon.jp/YYYY-MMDD-HHMM-SS/URL"
        )
        captures: list[CaptureRecord] = []
        if seed_urls:
            for url in seed_urls[:limit]:
                captures.append(
                    CaptureRecord(
                        original_url=url,
                        source=self.name,
                        extra={"seed": True, "gyo_tc": f"https://gyo.tc/{quote(url, safe='')}"},
                    )
                )
        return DiscoveryResult(captures=captures, complete=True, source_notes=notes)

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        target = capture.snapshot_url or capture.original_url
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(target)
            return resp.content, {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "final_url": str(resp.url),
                "content_type": resp.headers.get("content-type"),
            }
