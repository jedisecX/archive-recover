"""Internet Archive Wayback CDX adapter.

Capabilities:
- Full domain / host / prefix enumeration via CDX API
- Date filtering, field selection, pagination (resumeKey or page)
- Status, MIME, digest, length available
- Replay and raw (id_) URLs

Limitations:
- Rate limited; respect delays
- Large domains require careful pagination
- Digest is SHA1, not SHA256
- Successful CDX row != successfully recoverable payload
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urljoin

import httpx

from .base import BaseAdapter, CaptureRecord, DiscoveryResult


class WaybackAdapter(BaseAdapter):
    name = "wayback"
    base_url = "https://web.archive.org"
    cdx_url = "https://web.archive.org/cdx/search/cdx"

    def supports_domain_enumeration(self) -> bool:
        return True

    def limitations(self) -> str:
        return (
            "Excellent CDX enumeration. Rate-limited free infrastructure. "
            "Use low concurrency and delays. Digest is SHA1. "
            "Replay may rewrite content; use id_ for closer-to-original."
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
        url_param = prefix or domain
        if not url_param.startswith(("http://", "https://")):
            url_param = f"http://{url_param}"

        params: dict[str, Any] = {
            "url": url_param,
            "matchType": "domain" if not prefix else "prefix",
            "output": "json",
            "fl": "timestamp,original,statuscode,mimetype,digest,length",
            "limit": min(limit, 10000),
            "gzip": "false",
        }
        if since:
            params["from"] = since.strftime("%Y%m%d%H%M%S")
        if until:
            params["to"] = until.strftime("%Y%m%d%H%M%S")
        if resume_key:
            params["resumeKey"] = resume_key

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            try:
                resp = await client.get(self.cdx_url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                return DiscoveryResult(captures=[], error=str(exc), complete=False)

        captures: list[CaptureRecord] = []
        # First row is headers when output=json
        rows = data[1:] if data and isinstance(data[0], list) else data
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            ts, original, status, mime, digest, length = row[:6]
            try:
                ts_dt = datetime.strptime(ts, "%Y%m%d%H%M%S") if ts else None
            except ValueError:
                ts_dt = None
            snapshot = f"{self.base_url}/web/{ts}/{original}"
            captures.append(
                CaptureRecord(
                    original_url=original,
                    timestamp=ts_dt,
                    timestamp_str=ts,
                    snapshot_url=snapshot,
                    mime_type=mime,
                    status_code=int(status) if status and status.isdigit() else None,
                    digest=digest,
                    length=int(length) if length and str(length).isdigit() else None,
                    source=self.name,
                    extra={"cdx_raw": row},
                )
            )

        # Resume key is usually last line or header; simplified handling
        next_key = None
        if len(rows) >= limit:
            next_key = rows[-1][0] if rows else None  # rough; real impl uses proper resumeKey

        return DiscoveryResult(
            captures=captures,
            next_resume_key=next_key,
            complete=len(rows) < limit,
            source_notes="CDX JSON response",
        )

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        if not capture.snapshot_url:
            raise ValueError("No snapshot URL")
        # Prefer id_ raw form when possible
        raw_url = capture.snapshot_url.replace("/web/", "/web/", 1)
        if "id_" not in raw_url and capture.timestamp_str:
            parts = capture.snapshot_url.split("/web/", 1)
            if len(parts) == 2:
                raw_url = f"{parts[0]}/web/{capture.timestamp_str}id_/{parts[1].split('/', 1)[-1]}"

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(raw_url or capture.snapshot_url)
            headers = dict(resp.headers)
            return resp.content, {
                "status_code": resp.status_code,
                "headers": headers,
                "final_url": str(resp.url),
                "content_type": resp.headers.get("content-type"),
            }
