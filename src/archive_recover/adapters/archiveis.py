"""archive.today / archive.is adapter.

Capabilities:
- Retrieval of known snapshot URLs (https://archive.is/XXXX or archive.ph)
- Limited search via public pages when possible (often challenged)
- Memento TimeMap support if the service exposes it

Limitations:
- No public CDX-like bulk API
- Frequent interstitial / CAPTCHA / anti-bot measures
- Domain-wide enumeration not feasible via documented interfaces
- Must support seed-URL and manual workflow
- Do not attempt to bypass protections
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from .base import BaseAdapter, CaptureRecord, DiscoveryResult


class ArchiveIsAdapter(BaseAdapter):
    name = "archiveis"
    base_url = "https://archive.is"

    def supports_domain_enumeration(self) -> bool:
        return False

    def limitations(self) -> str:
        return (
            "No stable public bulk API. Discovery is primarily seed-URL or "
            "manual snapshot submission. Automated search frequently hits "
            "anti-bot challenges. Retrieval of known snapshot URLs is supported "
            "when accessible. Do not bypass CAPTCHAs or rate limits."
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
            "archive.is does not expose a reliable public enumeration API. "
            "Provide known snapshot URLs or original URLs as seeds. "
            "Automated search pages are frequently protected."
        )
        captures: list[CaptureRecord] = []

        # If seeds provided, treat them as potential snapshots or originals
        if seed_urls:
            for url in seed_urls[:limit]:
                captures.append(
                    CaptureRecord(
                        original_url=url,
                        snapshot_url=url if "archive." in url else None,
                        source=self.name,
                        extra={"seed": True},
                    )
                )

        return DiscoveryResult(
            captures=captures,
            complete=True,
            source_notes=notes,
        )

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        target = capture.snapshot_url or capture.original_url
        if not target:
            raise ValueError("No URL to retrieve")

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(target)
            return resp.content, {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "final_url": str(resp.url),
                "content_type": resp.headers.get("content-type"),
                "warning": "May be interstitial or challenge page",
            }
