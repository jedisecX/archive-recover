"""End-to-end pipeline tests: discover → recover → verify → report → reconstruct."""

import asyncio
import json
import sqlite3

from click.testing import CliRunner

from archive_recover.core.db import Capture, Resource
from archive_recover.core.reconstruct import Reconstructor


def run(eng, coro):
    async def go():
        try:
            return await coro
        finally:
            await eng.close()
    return asyncio.run(go())


def test_discover_persists_dedupes_and_scopes(make_engine, fake):
    eng = make_engine()
    [r] = run(eng, eng.discover(["wayback"]))
    assert r.complete and r.error is None and r.pages == 3
    assert r.out_of_scope == 1  # sub.example.com with include_subdomains=False
    assert r.new == len(fake.captures) - 1
    st = eng.stats()
    # http://example.com/, http://www.example.com:80/, https://example.com/ -> one URL
    assert st["unique_urls"] == 6
    # Re-running discovery is idempotent
    eng2 = make_engine()
    [r2] = run(eng2, eng2.discover(["wayback"], resume=False))
    assert r2.new == 0 and r2.seen == len(fake.captures)


def test_discover_resumes_from_checkpoint(make_engine, fake):
    eng = make_engine(max_pages=1)
    [r] = run(eng, eng.discover(["wayback"]))
    assert r.pages == 1 and not r.complete
    fake.requests.clear()
    eng2 = make_engine(max_pages=None)
    [r2] = run(eng2, eng2.discover(["wayback"]))
    assert r2.resumed_from == "4" and r2.complete
    assert "resumeKey=4" in fake.requests[0]
    # After completion, the next run starts fresh (no stale key)
    eng3 = make_engine()
    [r3] = run(eng3, eng3.discover(["wayback"]))
    assert r3.resumed_from is None


def test_recover_verify_dedupe_and_digest(make_engine, fake, cfg):
    eng = make_engine()
    run(eng, eng.discover(["wayback"]))
    fake.fail_once.add("http://example.com/about")
    eng = make_engine()
    s = run(eng, eng.recover(["wayback"]))
    assert s.failed == 0 and s.recovered == 8 and s.deduplicated == 1
    assert s.digest_mismatch == 0
    with eng.Session() as ses:
        res = ses.query(Resource).all()
        assert all(r.verified for r in res)  # IA SHA-1 matched every payload
        # identical captures share one object file
        assert len({r.local_path for r in res}) == len(res) - 1
    assert eng.verify() == {"checked": 8, "ok": 8}
    # Second run has nothing to do
    s2 = run(eng, eng.recover(["wayback"]))
    assert s2.attempted == 0


def test_recover_failures_are_recorded_and_capped(make_engine, fake):
    eng = make_engine()
    run(eng, eng.discover(["wayback"]))
    fake.gone.add("http://example.com/img/bg.png")
    eng = make_engine()
    s = run(eng, eng.recover(["wayback"]))
    assert s.failed == 1 and s.errors["not_found"] == 1
    with eng.Session() as ses:
        cap = ses.query(Capture).filter(Capture.recovery_status == "failed").one()
        assert "not_found" in cap.last_error and cap.attempts >= eng.cfg.max_attempts
    # Permanent failure isn't retried
    s2 = run(eng, eng.recover(["wayback"]))
    assert s2.attempted == 0


def test_type_filter_and_limit(make_engine):
    eng = make_engine()
    run(eng, eng.discover(["wayback"]))
    s = run(eng, eng.recover(["wayback"], types=["images"]))
    assert s.recovered == 2
    s = run(eng, eng.recover(["wayback"], limit=1))
    assert s.attempted == 1


def test_storage_limit(make_engine):
    eng = make_engine(max_storage_bytes=100)
    run(eng, eng.discover(["wayback"]))
    s = run(eng, eng.recover(["wayback"]))
    assert s.skipped_storage > 0 and s.bytes_written <= 100


def test_verify_detects_tampering_and_reconcile_requeues(make_engine):
    eng = make_engine()
    run(eng, eng.discover(["wayback"]))
    run(eng, eng.recover(["wayback"]))
    with eng.Session() as ses:
        r = ses.query(Resource).filter(Resource.content_type.like("text/css%")).one()
        path = eng.root / r.local_path
    path.write_bytes(b"tampered")
    v = eng.verify()
    assert v["corrupt"] == 1
    rc = eng.reconcile()
    assert rc["requeued"] == 1
    s = run(eng, eng.recover(["wayback"]))
    assert s.recovered == 1
    assert eng.verify()["ok"] == 8


def test_challenge_circuit_breaker(make_engine, cfg):
    eng = make_engine(circuit_breaker=2, sources=["archiveis"])
    run(eng, eng.discover(["archiveis"], seeds=[f"https://archive.ph/AbCd1?{i}" for i in range(5)]))
    s = run(eng, eng.recover(["archiveis"]))
    assert "archiveis" in s.paused_sources
    assert s.failed == 2  # stopped after 2 challenges instead of hammering all 5


def test_report_and_reconstruct(make_engine):
    eng = make_engine()
    run(eng, eng.discover(["wayback"]))
    run(eng, eng.recover(["wayback"]))
    jpath, mpath, st = eng.report()
    data = json.loads(jpath.read_text())
    assert data["discovery_complete_within_scope"] == ["wayback"]
    assert "Discovery complete within the configured scope for: wayback" in mpath.read_text()

    r = Reconstructor(eng).run()
    site = eng.root / "site"
    home = (site / "example.com/index.html").read_text(encoding="utf-8")
    assert "Home v2" in home  # latest capture chosen
    assert 'href="about.html"' in home
    assert 'href="about.html#team"' in home  # absolute replay URL unwrapped + rewritten
    assert "/web/2020" not in home  # no link left pointing into the replay UI
    assert 'href="https://other.org/"' in home  # external untouched
    assert 'src="img/logo.png"' in home
    assert 'type="text/plain"' in home and "onload" not in home  # scripts neutralized
    css = (site / "example.com/style.css").read_text()
    assert "url('img/bg.png')" in css
    about = (site / "example.com/about.html").read_text(encoding="utf-8")
    assert "会社概要" in about and "shift_jis" not in about.lower()  # transcoded, meta updated
    assert 'href="index.html"' in about
    manifest = json.loads((site / "_manifest.json").read_text())
    assert "http://example.com/missing.html" in manifest["missing"]
    assert "http://example.com/print.css" in manifest["missing"]
    assert "http://example.com/img/logo@2x.png" in manifest["missing"]
    assert r.index == "example.com/index.html"


def test_reconstruct_at_timestamp(make_engine):
    eng = make_engine()
    run(eng, eng.discover(["wayback"]))
    run(eng, eng.recover(["wayback"]))
    from datetime import datetime
    Reconstructor(eng, at=datetime(2020, 12, 31)).run()
    home = (eng.root / "site/example.com/index.html").read_text(encoding="utf-8")
    assert "Home v2" not in home and "Home" in home


def test_cli_end_to_end(tmp_path, fake, monkeypatch):
    """Drive the real CLI with the fake archive injected as the HTTP transport."""
    import archive_recover.core.engine as engine_mod
    from archive_recover.cli import cli

    orig = engine_mod.build_adapter

    def patched(name, cfg, transport=None):
        a = orig(name, cfg, fake.transport)
        return a
    monkeypatch.setattr(engine_mod, "build_adapter", patched)

    base = ["--db", str(tmp_path / "c.db"), "--output", str(tmp_path / "o"), "--rate-limit", "0"]
    runner = CliRunner()
    r = runner.invoke(cli, base + ["discover", "--domain", "example.com", "--page-size", "4"])
    assert r.exit_code == 0, r.output
    assert "complete" in r.output and "new=8" in r.output
    r = runner.invoke(cli, base + ["recover", "--domain", "example.com", "--max-concurrency", "3"])
    assert r.exit_code == 0, r.output
    assert "recovered=8" in r.output
    r = runner.invoke(cli, base + ["verify", "--domain", "example.com"])
    assert r.exit_code == 0 and "ok=8" in r.output
    r = runner.invoke(cli, base + ["status", "--domain", "example.com"])
    assert "recovered=8" in r.output
    r = runner.invoke(cli, base + ["report", "--domain", "example.com", "--json"])
    assert json.loads(r.output)["captures_by_status"]["recovered"] == 8
    r = runner.invoke(cli, base + ["reconstruct", "--domain", "example.com"])
    assert r.exit_code == 0 and "pages" in r.output
    r = runner.invoke(cli, base + ["discover", "--domain", "example.com", "--sources", "bogus"])
    assert r.exit_code != 0 and "unknown source" in r.output
    r = runner.invoke(cli, base + ["sources"])
    assert "wayback" in r.output and "megalodon" in r.output
    # DB really contains the data
    con = sqlite3.connect(tmp_path / "c.db")
    assert con.execute("select count(*) from captures").fetchone()[0] == 8
