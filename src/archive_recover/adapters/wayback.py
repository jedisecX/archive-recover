"""Internet Archive Wayback CDX adapter.

Capabilities:
- Domain / host / prefix enumeration via the CDX API
- Date filtering, optional digest collapsing
- Real pagination via ``showResumeKey=true`` (the key comes back as the last
  JSON row after an empty separator row)
- Raw payload retrieval via the ``id_`` replay modifier

Limitations:
- Rate limited free infrastructure; respect delays (429 + Retry-After honored)
- Digest is base32(SHA-1(payload)), not SHA-256
- A CDX row is not a guarantee the payload is still retrievable
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from archive_recover.core.http import FetchError
from archive_recover.core.utils import parse_timestamp

from .base import BaseAdapter, CaptureRecord, DiscoveryResult

_SNAPSHOT_RE = re.compile(r"^(https?://web\.archive\.org/web/)(\d{1,14})([a-z]{2}_)?/(.+)$", re.I)


def wayback_raw_url(snapshot_url: str, timestamp_str: Optional[str] = None) -> str:
    """Rewrite a replay URL to its ``id_`` (unmodified payload) form.

    ``https://web.archive.org/web/20200101000000/http://ex.com/`` ->
    ``https://web.archive.org/web/20200101000000id_/http://ex.com/``
    Existing modifiers (``im_``, ``js_``, ``if_``...) are replaced with ``id_``.
    """
    m = _SNAPSHOT_RE.match(snapshot_url)
    if not m:
        return snapshot_url
    prefix, ts, _mod, original = m.groups()
    return f"{prefix}{timestamp_str or ts}id_/{original}"


class WaybackAdapter(BaseAdapter):
    name = "wayback"
    base_url = "https://web.archive.org"
    cdx_url = "https://web.archive.org/cdx/search/cdx"
    allowed_hosts = ("web.archive.org",)
    fields = ("timestamp", "original", "statuscode", "mimetype", "digest", "length")

    def __init__(self, *args: Any, collapse: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.collapse = collapse  # e.g. "digest" to skip consecutive identical captures

    def supports_domain_enumeration(self) -> bool:
        return True

    def limitations(self) -> str:
        return (
            "Excellent CDX enumeration. Rate-limited free infrastructure. "
            "Use low concurrency and delays. Digest is SHA1. "
            "Replay may rewrite content; id_ is used for closer-to-original bytes."
        )

    def build_params(
        self,
        domain: str,
        *,
        prefix: Optional[str],
        since: Optional[datetime],
        until: Optional[datetime],
        resume_key: Optional[str],
        limit: int,
        include_subdomains: bool,
    ) -> dict[str, Any]:
        target = prefix or domain
        target = re.sub(r"^https?://", "", target)
        if prefix:
            match_type = "prefix"
        else:
            match_type = "domain" if include_subdomains else "host"
        params: dict[str, Any] = {
            "url": target,
            "matchType": match_type,
            "output": "json",
            "fl": ",".join(self.fields),
            "limit": max(1, min(limit, 10000)),
            "showResumeKey": "true",
        }
        if self.collapse:
            params["collapse"] = self.collapse
        if since:
            params["from"] = since.strftime("%Y%m%d%H%M%S")
        if until:
            params["to"] = until.strftime("%Y%m%d%H%M%S")
        if resume_key:
            params["resumeKey"] = resume_key
        return params

    @classmethod
    def parse_cdx_json(cls, data: Any) -> tuple[list[CaptureRecord], Optional[str]]:
        """Parse CDX ``output=json`` (+ ``showResumeKey``) into records and resume key."""
        if not isinstance(data, list) or not data:
            return [], None
        rows = list(data)
        header = rows[0] if rows and isinstance(rows[0], list) and rows[0][:1] == ["timestamp"] else None
        if header is not None:
            rows = rows[1:]
        resume_key = None
        # Trailer: [] then [resumeKey]
        if len(rows) >= 2 and rows[-2] == [] and isinstance(rows[-1], list) and len(rows[-1]) == 1:
            resume_key = str(rows[-1][0]) or None
            rows = rows[:-2]
        cols = header or list(cls.fields)
        idx = {name: i for i, name in enumerate(cols)}
        records: list[CaptureRecord] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < len(cols):
                continue
            get = lambda k: row[idx[k]] if k in idx else None  # noqa: E731
            ts, original = get("timestamp"), get("original")
            if not ts or not original:
                continue
            status, length = get("statuscode"), get("length")
            records.append(
                CaptureRecord(
                    original_url=original,
                    timestamp=parse_timestamp(ts),
                    timestamp_str=ts,
                    snapshot_url=f"{cls.base_url}/web/{ts}/{original}",
                    mime_type=get("mimetype"),
                    status_code=int(status) if status and str(status).isdigit() else None,
                    digest=get("digest") if get("digest") not in (None, "-") else None,
                    length=int(length) if length and str(length).isdigit() else None,
                    source=cls.name,
                )
            )
        return records, resume_key

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
        params = self.build_params(
            domain, prefix=prefix, since=since, until=until, resume_key=resume_key,
            limit=limit, include_subdomains=include_subdomains,
        )
        try:
            res = await self.fetcher.get(self.cdx_url, params=params)
            data = json.loads(res.content or b"[]")
        except FetchError as exc:
            return DiscoveryResult(captures=[], error=str(exc), complete=False,
                                   next_resume_key=resume_key)
        except ValueError as exc:
            return DiscoveryResult(captures=[], error=f"invalid CDX JSON: {exc}", complete=False,
                                   next_resume_key=resume_key)

        captures, next_key = self.parse_cdx_json(data)
        return DiscoveryResult(
            captures=captures,
            next_resume_key=next_key,
            complete=next_key is None,
            source_notes=f"CDX matchType={params['matchType']}",
        )

    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        if not capture.snapshot_url:
            raise ValueError("No snapshot URL")
        raw_url = wayback_raw_url(capture.snapshot_url, capture.timestamp_str)
        accept = {200}
        if capture.status_code:
            accept.add(capture.status_code)  # archived 404s etc. are still valid captures
        res = await self.fetcher.get(raw_url, accept_status=accept)
        if res.status_code != 200 and "memento-datetime" not in res.headers:
            raise FetchError(
                f"HTTP {res.status_code} without memento headers (not an archived response)",
                category="not_found", status=res.status_code,
            )
        return res.content, self._meta(res, raw_url=raw_url)
