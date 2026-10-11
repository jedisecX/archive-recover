"""Adapter tests against mocked archive endpoints (no network)."""

import asyncio
from datetime import datetime

import httpx
import pytest

from archive_recover.adapters.archiveis import ArchiveIsAdapter, is_archiveis_url, looks_like_challenge
from archive_recover.adapters.base import CaptureRecord
from archive_recover.adapters.megalodon import MegalodonAdapter, parse_megalodon_snapshot
from archive_recover.adapters.memento import MementoAdapter
from archive_recover.adapters.wayback import WaybackAdapter, wayback_raw_url
from archive_recover.core.http import BlockedURLError, FetchError, HttpSettings


async def _nosleep(_s):
    return None


def settings(fake, **kw):
    return HttpSettings(rate_limit_delay=0, max_retries=2, transport=fake.transport, sleep=_nosleep, **kw)


def run(adapter, coro):
    async def go():
        try:
            return await coro
        finally:
            await adapter.close()
    return asyncio.run(go())


def test_wayback_limitations():
    a = WaybackAdapter()
    assert "CDX" in a.limitations()
    assert a.supports_domain_enumeration()


def test_archiveis_no_domain_enum():
    a = ArchiveIsAdapter()
    assert not a.supports_domain_enumeration()
    assert "No stable public bulk API" in a.limitations()


# --------------------------------------------------------------------- wayback

def test_wayback_raw_url():
    assert wayback_raw_url("https://web.archive.org/web/20200101000000/http://ex.com/a?b=1") == \
        "https://web.archive.org/web/20200101000000id_/http://ex.com/a?b=1"
    assert wayback_raw_url("https://web.archive.org/web/20200101000000im_/http://ex.com/a.png") == \
        "https://web.archive.org/web/20200101000000id_/http://ex.com/a.png"
    assert wayback_raw_url("https://example.com/x") == "https://example.com/x"


def test_cdx_json_parsing_with_resume_trailer():
    data = [["timestamp", "original", "statuscode", "mimetype", "digest", "length"],
            ["20200101000000", "http://ex.com/", "200", "text/html", "ABC", "12"],
            ["20200101000001", "http://ex.com/r", "-", "warc/revisit", "-", "-"],
            [], ["ex,com)/r 20200101000001"]]
    recs, key = WaybackAdapter.parse_cdx_json(data)
    assert key == "ex,com)/r 20200101000001"
    assert len(recs) == 2
    assert recs[0].timestamp == datetime(2020, 1, 1)
    assert recs[0].snapshot_url == "https://web.archive.org/web/20200101000000/http://ex.com/"
    assert recs[1].status_code is None and recs[1].digest is None and recs[1].length is None


def test_cdx_params(fake):
    a = WaybackAdapter(settings=settings(fake))
    p = a.build_params("https://example.com", prefix=None, since=datetime(2020, 1, 1), until=None,
                       resume_key="k", limit=50000, include_subdomains=False)
    assert p["url"] == "example.com" and p["matchType"] == "host"
    assert p["limit"] == 10000 and p["from"] == "20200101000000" and p["resumeKey"] == "k"
    p = a.build_params("example.com", prefix="example.com/blog/", since=None, until=None,
                       resume_key=None, limit=10, include_subdomains=True)
    assert p["matchType"] == "prefix"


def test_wayback_discover_paginates(fake):
    a = WaybackAdapter(settings=settings(fake))

    async def all_pages():
        key, out, pages = None, [], 0
        while True:
            r = await a.discover("example.com", resume_key=key, limit=4)
            pages += 1
            out += r.captures
            if r.complete:
                return out, pages
            key = r.next_resume_key

    caps, pages = run(a, all_pages())
    assert pages == 3
    assert len(caps) == len(fake.captures)


def test_wayback_discover_error_is_reported_not_complete(fake):
    fake.cdx_status = 503
    a = WaybackAdapter(settings=settings(fake))
    r = run(a, a.discover("example.com"))
    assert r.error and not r.complete and r.captures == []
    # retried: initial + 2 retries
    assert sum("cdx" in u for u in fake.requests) == 3


def test_wayback_retrieve_raw_and_archived_404(fake):
    a = WaybackAdapter(settings=settings(fake))
    rec = CaptureRecord(original_url="http://example.com/old", timestamp_str="20200101000005",
                        snapshot_url="https://web.archive.org/web/20200101000005/http://example.com/old",
                        status_code=404, source="wayback")
    body, meta = run(a, a.retrieve(rec))
    assert b"404 archived" in body and meta["status_code"] == 404
    assert "id_/" in meta["raw_url"]


def test_wayback_retries_transient_503(fake):
    fake.fail_once.add("http://example.com/about")
    a = WaybackAdapter(settings=settings(fake))
    rec = CaptureRecord(original_url="", timestamp_str="20200101000001", source="wayback",
                        snapshot_url="https://web.archive.org/web/20200101000001/http://example.com/about")
    body, meta = run(a, a.retrieve(rec))
    assert meta["status_code"] == 200 and body


def test_redirect_to_metadata_ip_is_blocked(fake):
    fake.redirect_evil.add("http://example.com/")
    a = WaybackAdapter(settings=settings(fake))
    rec = CaptureRecord(original_url="", timestamp_str="20200101000000", source="wayback",
                        snapshot_url="https://web.archive.org/web/20200101000000/http://example.com/")
    with pytest.raises(BlockedURLError):
        run(a, a.retrieve(rec))
    assert not any("169.254" in u for u in fake.requests)


def test_size_cap(fake):
    a = WaybackAdapter(settings=settings(fake, max_object_bytes=10))
    rec = CaptureRecord(original_url="", timestamp_str="20200101000003", source="wayback",
                        snapshot_url="https://web.archive.org/web/20200101000003/http://example.com/img/logo.png")
    with pytest.raises(FetchError, match="max_object_bytes"):
        run(a, a.retrieve(rec))


# --------------------------------------------------------------------- memento

def test_memento_timemap(fake):
    a = MementoAdapter(settings=settings(fake))
    r = run(a, a.discover("example.com", seed_urls=["http://example.com/about", "http://example.com/nope"]))
    assert r.complete and r.error is None  # 404 TimeMap = zero captures, not an error
    assert len(r.captures) == 1
    c = r.captures[0]
    assert c.snapshot_url.endswith("/http://example.com/about")
    assert c.timestamp == datetime(2020, 1, 1, 0, 0, 0)
    body, meta = run(a, a.retrieve(c))
    assert "id_/" in meta["raw_url"]


def test_memento_limit_respected(fake):
    a = MementoAdapter(settings=settings(fake))
    r = run(a, a.discover("example.com", seed_urls=["http://example.com/"] * 3, limit=2))
    assert len(r.captures) == 2
    assert r.next_resume_key is not None and not r.complete


def test_memento_requires_seeds():
    r = asyncio.run(MementoAdapter().discover("example.com"))
    assert r.captures == [] and "seed" in r.source_notes


# --------------------------------------------------------------------- archive.is

def test_archiveis_host_detection():
    assert is_archiveis_url("https://archive.ph/AbCd1")
    assert not is_archiveis_url("https://web.archive.org/web/2020/x")  # the old "archive." bug
    assert not is_archiveis_url("https://myarchive.ph.evil.com/")


def test_archiveis_seed_snapshot_and_rate_limited_timemap(fake):
    a = ArchiveIsAdapter(settings=settings(fake))
    r = run(a, a.discover("example.com", seed_urls=["https://archive.ph/AbCd1", "http://example.com/"]))
    assert len(r.captures) == 1 and r.captures[0].snapshot_url == "https://archive.ph/AbCd1"
    assert r.error and "429" in r.error and not r.complete


def test_archiveis_challenge_detected(fake):
    a = ArchiveIsAdapter(settings=settings(fake))
    with pytest.raises(FetchError) as ei:
        run(a, a.retrieve(CaptureRecord(original_url="x", snapshot_url="https://archive.ph/AbCd1")))
    assert ei.value.category == "challenge"
    assert looks_like_challenge(b"", 429)


def test_archiveis_never_fetches_live_site(fake):
    a = ArchiveIsAdapter(settings=settings(fake))
    with pytest.raises(ValueError):
        run(a, a.retrieve(CaptureRecord(original_url="http://example.com/")))
    assert fake.requests == []


# --------------------------------------------------------------------- megalodon

def test_megalodon_snapshot_parse():
    ts, dt, orig = parse_megalodon_snapshot(
        "https://megalodon.jp/2019-0412-1530-07/https:/www.example.jp/%E6%97%A5%E6%9C%AC")
    assert ts == "20190412153007" and dt == datetime(2019, 4, 12, 15, 30, 7)
    assert orig == "https://www.example.jp/日本"
    assert parse_megalodon_snapshot("https://example.com/") is None


def test_megalodon_discover_and_retrieve(fake):
    a = MegalodonAdapter(settings=settings(fake))
    seeds = ["https://megalodon.jp/2019-0412-1530-07/http://example.jp/", "http://example.jp/raw"]
    r = run(a, a.discover("example.jp", seed_urls=seeds))
    assert r.captures[0].snapshot_url and r.captures[1].snapshot_url is None
    assert "gyo.tc" in r.captures[1].extra["gyo_tc"]
    body, meta = run(a, a.retrieve(r.captures[0]))
    assert "魚拓" in body.decode()
    with pytest.raises(ValueError):
        run(a, a.retrieve(r.captures[1]))  # no live-site fallback
