"""Shared fixtures: an in-process fake of the Wayback CDX/replay/TimeMap and
archive.today endpoints, served through ``httpx.MockTransport``.

Response formats mirror the real services (CDX JSON with ``showResumeKey``
trailer, ``id_`` raw replay with Memento-Datetime, link-format TimeMaps).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import unquote

import httpx
import pytest

from archive_recover.core.config import Config
from archive_recover.core.utils import sha1_base32

HOME_V1 = b"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/style.css"><title>Home</title>
<script>alert('x')</script></head>
<body onload="track()"><a href="/about">About</a>
<a href="https://web.archive.org/web/20200101000000/http://example.com/about#team">About (replay)</a>
<a href="/web/20200101000000/http://example.com/about">About (relative replay)</a>
<a href="/missing.html">Gone</a><a href="https://other.org/">Elsewhere</a>
<img src="img/logo.png" srcset="img/logo.png 1x, img/logo@2x.png 2x">
</body></html>"""
HOME_V2 = HOME_V1.replace(b"Home", b"Home v2")
ABOUT = "<html><head><meta charset='shift_jis'></head><body><p>会社概要</p><a href='/'>top</a></body></html>".encode("shift_jis")
CSS = b"body{background:url('img/bg.png')} @import \"/print.css\";"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
BG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 32


@dataclass
class FakeArchive:
    """Mutable fake archive; tests can tweak behavior and inspect requests."""

    # (timestamp, original, status, mime, body)
    captures: list = field(default_factory=lambda: [
        ("20200101000000", "http://example.com/", "200", "text/html", HOME_V1),
        ("20200601000000", "http://www.example.com:80/", "200", "text/html", HOME_V1),  # same digest
        ("20210101000000", "https://example.com/", "200", "text/html", HOME_V2),
        ("20200101000001", "http://example.com/about", "200", "text/html", ABOUT),
        ("20200101000002", "http://example.com/style.css", "200", "text/css", CSS),
        ("20200101000003", "http://example.com/img/logo.png", "200", "image/png", PNG),
        ("20200101000004", "http://example.com/img/bg.png", "200", "image/png", BG),
        ("20200101000005", "http://example.com/old", "404", "text/html", b"<h1>404 archived</h1>"),
        ("20200101000006", "http://sub.example.com/x", "200", "text/html", b"<p>sub</p>"),
    ])
    page_size_override: int | None = 4
    requests: list = field(default_factory=list)
    fail_once: set = field(default_factory=set)  # originals that 503 on first try
    gone: set = field(default_factory=set)       # originals IA no longer serves
    redirect_evil: set = field(default_factory=set)
    cdx_status: int = 200

    def rows(self):
        return [[ts, orig, st, mime, sha1_base32(body), str(len(body))]
                for ts, orig, st, mime, body in self.captures]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        url = request.url
        if url.host == "web.archive.org" and url.path == "/cdx/search/cdx":
            if self.cdx_status != 200:
                return httpx.Response(self.cdx_status, text="busy")
            p = dict(url.params)
            assert p["output"] == "json" and p["showResumeKey"] == "true"
            rows = self.rows()
            start = int(p.get("resumeKey", 0) or 0)
            size = self.page_size_override or int(p["limit"])
            page = rows[start:start + size]
            body = [["timestamp", "original", "statuscode", "mimetype", "digest", "length"], *page]
            if start + size < len(rows):
                body += [[], [str(start + size)]]
            return httpx.Response(200, json=body)
        m = re.match(r"^/web/timemap/link/(.+)$", url.raw_path.decode())
        if url.host == "web.archive.org" and m:
            target = unquote(m.group(1))
            lines = [f'<{target}>; rel="original",',
                     f'<https://web.archive.org/web/timemap/link/{target}>; rel="self"; type="application/link-format",']
            found = [c for c in self.captures if c[1].rstrip("/") == target.rstrip("/")]
            if not found:
                return httpx.Response(404, text="")
            for i, (ts, orig, *_rest) in enumerate(found):
                rel = "first memento" if i == 0 else "memento"
                lines.append(f'<https://web.archive.org/web/{ts}/{orig}>; rel="{rel}"; '
                             f'datetime="Wed, 01 Jan 2020 00:00:0{i} GMT",')
            return httpx.Response(200, text="\n".join(lines),
                                  headers={"content-type": "application/link-format"})
        m = re.match(r"^/web/(\d{14})id_/(.+)$", url.raw_path.decode())
        if url.host == "web.archive.org" and m:
            ts, orig = m.groups()
            if orig in self.redirect_evil:
                return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
            if orig in self.gone:
                return httpx.Response(404, text="Wayback Machine has not archived that URL.")
            if orig in self.fail_once:
                self.fail_once.discard(orig)
                return httpx.Response(503, headers={"retry-after": "0"})
            for cts, corig, st, mime, body in self.captures:
                if cts == ts and corig == orig:
                    return httpx.Response(int(st), content=body, headers={
                        "content-type": mime, "memento-datetime": "Wed, 01 Jan 2020 00:00:00 GMT"})
            return httpx.Response(404, text="not archived")
        if url.host == "archive.ph" and url.path.startswith("/timemap/"):
            return httpx.Response(429, text="Too many requests")
        if url.host == "archive.ph" and url.path == "/AbCd1":
            return httpx.Response(200, text="<html><div class='g-recaptcha'></div></html>")
        if url.host == "megalodon.jp":
            return httpx.Response(200, content="<p>魚拓</p>".encode(), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(599, text=f"unexpected request {url}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def fake() -> FakeArchive:
    return FakeArchive()


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        domain="example.com", output_dir=tmp_path / "out", db_path=str(tmp_path / "t.db"),
        sources=["wayback"], rate_limit_delay=0, max_retries=2, page_size=4,
    ).validate()


@pytest.fixture
def make_engine(cfg, fake):
    from archive_recover.core.engine import RecoveryEngine

    def _make(**overrides):
        for k, v in overrides.items():
            setattr(cfg, k, v)
        eng = RecoveryEngine(cfg, transport=fake.transport, log=lambda m: None)
        # no real sleeping in tests
        for name in ("wayback", "memento", "archiveis", "megalodon"):
            a = eng.adapter(name)
            async def _nosleep(_s):
                return None
            a.settings.sleep = _nosleep
            a.fetcher.limiter._sleep = _nosleep
        return eng
    return _make


def run(coro):
    import asyncio
    return asyncio.run(coro)
