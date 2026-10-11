"""archive.today / archive.is adapter.

Capabilities:
- Seed URLs that are already snapshot links (archive.ph/AbCd1) are recorded directly
- Seed original URLs are resolved through the Memento TimeMap
  (``https://archive.ph/timemap/<url>``) — standards-based, no scraping
- Retrieval of known snapshot URLs

Limitations:
- No public CDX-like bulk API; domain-wide enumeration not feasible
- Frequent interstitial / CAPTCHA / anti-bot measures — detected and reported
  as ``challenge`` failures, never bypassed
- Never falls back to fetching the live original site
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlsplit

from archive_recover.core.http import FetchError

from .base import BaseAdapter, CaptureRecord, DiscoveryResult

MIRRORS = ("archive.today", "archive.is", "archive.ph", "archive.li",
           "archive.vn", "archive.fo", "archive.md")

_CHALLENGE_MARKERS = (
    b"g-recaptcha", b"h-captcha", b"cf-challenge", b"challenge-platform",
    b"Please complete the security check", b"captcha",
)


def is_archiveis_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == m or host.endswith("." + m) for m in MIRRORS)


def looks_like_challenge(content: bytes, status: int) -> bool:
    if status in (403, 429):
        return True
    head = content[:20000].lower()
    return any(m.lower() in head for m in _CHALLENGE_MARKERS)


class ArchiveIsAdapter(BaseAdapter):
    name = "archiveis"
    base_url = "https://archive.ph"
    allowed_hosts = MIRRORS

    def supports_domain_enumeration(self) -> bool:
        return False

    def limitations(self) -> str:
        return (
            "No stable public bulk API. Discovery is seed-URL based: snapshot links are "
            "recorded directly, original URLs are looked up via Memento TimeMap. "
            "Anti-bot challenges are detected and reported, never bypassed. "
            "Retrieval of known snapshot URLs is supported when accessible."
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
            "archive.is does not expose a reliable public enumeration API. "
            "Provide known snapshot URLs or original URLs as seeds."
        )
        if not seed_urls:
            return DiscoveryResult(captures=[], complete=True, source_notes=notes)

        start = int(resume_key) if resume_key and resume_key.isdigit() else 0
        captures: list[CaptureRecord] = []
        errors: list[str] = []
        i = start
        for i in range(start, len(seed_urls)):
            url = seed_urls[i].strip()
            if is_archiveis_url(url):
                captures.append(CaptureRecord(
                    original_url=url, snapshot_url=url, source=self.name,
                    extra={"seed": True, "original_unknown": True},
                ))
            else:
                tm = f"{self.base_url}/timemap/{quote(url, safe=':/?&=%+@,;~')}"
                try:
                    captures.extend(await self._timemap_captures(
                        tm, url, since=since, until=until, limit=limit - len(captures)))
                except FetchError as exc:
                    errors.append(f"{url}: {exc}")
                    if exc.category in ("rate_limited", "blocked") or exc.status in (403, 429):
                        # Stop early instead of hammering a service that is pushing back.
                        break
            if len(captures) >= limit:
                break
        next_index = i + 1
        more = next_index < len(seed_urls)
        return DiscoveryResult(
            captures=captures,
            next_resume_key=str(next_index) if more else None,
            complete=not more and not errors,
            error="; ".join(errors[:5]) if errors else None,
            source_notes=notes,
        )

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        target = capture.snapshot_url
        if not target or not is_archiveis_url(target):
            raise ValueError("No archive.today snapshot URL (live-site fetch is not performed)")
        res = await self.fetcher.get(target, accept_status=(200, 403, 429))
        if looks_like_challenge(res.content, res.status_code):
            raise FetchError(
                f"anti-bot challenge at {res.final_url} (HTTP {res.status_code}); not bypassed",
                category="challenge", status=res.status_code,
            )
        return res.content, self._meta(res)

    @staticmethod
    def snapshot_id(url: str) -> Optional[str]:
        m = re.match(r"^/([A-Za-z0-9]{4,10})/?$", urlsplit(url).path)
        return m.group(1) if m else None
