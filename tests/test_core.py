"""Utilities, link-format parser, config and schema tests."""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from archive_recover.core.config import load_config, mime_matches
from archive_recover.core.db import init_db
from archive_recover.core.linkformat import parse_link_format
from archive_recover.core.utils import (
    is_safe_url,
    normalize_url,
    parse_timestamp,
    safe_join,
    sha1_base32,
    url_in_scope,
    url_to_local_path,
)


@pytest.mark.parametrize("a,b", [
    ("HTTP://WWW.Example.com:80/a#frag", "http://example.com/a"),
    ("https://example.com", "http://example.com/"),
    ("example.com/x?b=2&a=1", "http://example.com/x?a=1&b=2"),
    ("http://example.com:8080/", "http://example.com:8080/"),
    ("http://bücher.de/", "http://xn--bcher-kva.de/"),
    ("http://example.com/a%20b", "http://example.com/a%20b"),
    ("http://example.com/a b", "http://example.com/a%20b"),
])
def test_normalize_url(a, b):
    assert normalize_url(a) == b


@pytest.mark.parametrize("url,ok", [
    ("https://web.archive.org/x", True),
    ("http://127.0.0.1/", False), ("http://localhost/", False), ("http://0.0.0.0/", False),
    ("http://169.254.169.254/latest", False), ("http://10.1.2.3/", False),
    ("http://[::ffff:127.0.0.1]/", False), ("http://100.64.0.1/", False),
    ("http://2130706433/", False), ("http://user:pw@web.archive.org/", False),
    ("file:///etc/passwd", False), ("gopher://x/", False), ("http://foo.localhost/", False),
])
def test_is_safe_url(url, ok):
    assert is_safe_url(url) is ok


def test_allowlist():
    hosts = ["web.archive.org"]
    assert is_safe_url("https://web.archive.org/web/1/x", hosts)
    assert not is_safe_url("https://evil.com/web.archive.org", hosts)
    assert not is_safe_url("https://web.archive.org.evil.com/", hosts)


def test_scope():
    assert url_in_scope("http://www.example.com/a", "example.com", False)
    assert not url_in_scope("http://sub.example.com/a", "example.com", False)
    assert url_in_scope("http://sub.example.com/a", "example.com", True)
    assert not url_in_scope("http://notexample.com/", "example.com", True)


def test_parse_timestamp():
    assert parse_timestamp("20200102030405") == datetime(2020, 1, 2, 3, 4, 5)
    assert parse_timestamp("2020") == datetime(2020, 1, 1)
    assert parse_timestamp("202005") == datetime(2020, 5, 1)
    assert parse_timestamp("Sun, 20 Jan 2002 14:25:10 GMT") == datetime(2002, 1, 20, 14, 25, 10)
    assert parse_timestamp("2020-01-01T00:00:00Z") == datetime(2020, 1, 1)
    assert parse_timestamp("garbage") is None and parse_timestamp(None) is None


def test_sha1_base32_known_value():
    # Empty payload digest as used in IA CDX
    assert sha1_base32(b"") == "3I42H3S6NNFQ2MSVX7XZKYAYSCX5QBYJ"


def test_safe_join(tmp_path):
    assert safe_join(tmp_path, "a/b.txt") == (tmp_path / "a/b.txt").resolve()
    for bad in ["../x", "a/../../x", "..\\x"]:
        with pytest.raises(ValueError):
            safe_join(tmp_path, bad)
    # absolute paths are re-rooted, never escape
    assert safe_join(tmp_path, "/etc/passwd") == (tmp_path / "etc/passwd").resolve()


@pytest.mark.parametrize("url,mime,path", [
    ("http://example.com/", "text/html", "example.com/index.html"),
    ("http://example.com/blog/", None, "example.com/blog/index.html"),
    ("http://example.com/about", "text/html", "example.com/about.html"),
    ("http://example.com/a.css", "text/css", "example.com/a.css"),
    ("http://example.com/../../etc/passwd", None, "example.com/etc/passwd"),
])
def test_url_to_local_path(url, mime, path):
    assert url_to_local_path(url, mime) == path


def test_url_to_local_path_query_is_unique():
    a = url_to_local_path("http://example.com/p?id=1", "text/html")
    b = url_to_local_path("http://example.com/p?id=2", "text/html")
    assert a != b and a.endswith(".html") and ".." not in a


def test_link_format_parser_handles_commas_and_single_line():
    body = ('<http://ex.com/>; rel="original", <https://web.archive.org/web/2002/http://ex.com/>; '
            'rel="first memento"; datetime="Sun, 20 Jan 2002 14:25:10 GMT", '
            '<https://web.archive.org/web/2003/http://ex.com/>; rel="last memento"; '
            'datetime="Mon, 01 Dec 2003 00:00:00 GMT"')
    links = list(parse_link_format(body))
    assert [l.uri for l in links if l.is_memento] == [
        "https://web.archive.org/web/2002/http://ex.com/",
        "https://web.archive.org/web/2003/http://ex.com/"]
    assert links[1].datetime == "Sun, 20 Jan 2002 14:25:10 GMT"
    assert not links[0].is_memento


def test_mime_matches():
    assert mime_matches("text/html; charset=utf-8", ["html"])
    assert mime_matches("image/png", ["images"])
    assert not mime_matches("image/png", ["html", "css"])
    assert mime_matches("warc/revisit", ["html"])  # unknown kept
    assert mime_matches("anything", [])


def test_config_yaml(tmp_path):
    example = Path(__file__).resolve().parents[1] / "config" / "example.yaml"
    cfg = load_config(example, domain="override.com")
    assert cfg.domain == "override.com"
    assert cfg.since == datetime(2020, 1, 1)
    assert "wayback" in cfg.sources
    bad = tmp_path / "bad.yaml"
    bad.write_text("domain: x\nnope: 1\n")
    with pytest.raises(ValueError, match="unknown config keys"):
        load_config(bad)


def test_schema_unique_and_migration_from_v01(tmp_path):
    db = tmp_path / "old.db"
    # Simulate a v0.1.0 database: captures table without the v2 columns
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE domains (id INTEGER PRIMARY KEY, name VARCHAR(255), "
                       "normalized VARCHAR(255), created_at DATETIME, last_checked DATETIME)"))
        c.execute(text("CREATE TABLE urls (id INTEGER PRIMARY KEY, domain_id INTEGER, original TEXT, "
                       "normalized TEXT, first_seen DATETIME)"))
        c.execute(text("CREATE TABLE captures (id INTEGER PRIMARY KEY, url_id INTEGER, source VARCHAR(64), "
                       "timestamp DATETIME, timestamp_str VARCHAR(32), snapshot_url TEXT, mime_type VARCHAR(128), "
                       "status_code INTEGER, digest VARCHAR(128), length INTEGER, "
                       "recovery_status VARCHAR(32), extra JSON)"))
    eng.dispose()
    e2 = init_db(str(db))
    cols = {c["name"] for c in inspect(e2).get_columns("captures")}
    assert {"capture_key", "attempts", "last_error"} <= cols
    with e2.begin() as c:
        c.execute(text("INSERT INTO urls(domain_id, original, normalized) VALUES (1,'a','a')"))
        with pytest.raises(Exception):
            c.execute(text("INSERT INTO urls(domain_id, original, normalized) VALUES (1,'a','a')"))
