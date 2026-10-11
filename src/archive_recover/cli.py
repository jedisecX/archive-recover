"""CLI entry point using Click."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

import click

from archive_recover import __version__
from archive_recover.adapters import ADAPTER_CLASSES
from archive_recover.core.config import MIME_CATEGORIES, Config, load_config

# Back-compat export
ADAPTERS = ADAPTER_CLASSES


def _split(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _read_seeds(path: Optional[str]) -> Optional[list[str]]:
    if not path:
        return None
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    seeds = [ln.strip() for ln in text.splitlines()]
    return list(dict.fromkeys(s for s in seeds if s and not s.startswith("#")))


def _cfg(ctx: click.Context, domain: Optional[str], **overrides: Any) -> Config:
    base: dict[str, Any] = dict(ctx.obj["overrides"])
    base.update({k: v for k, v in overrides.items() if v is not None})
    if domain:
        base["domain"] = domain
    try:
        cfg = load_config(ctx.obj["config_path"], **base)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc
    if not cfg.domain:
        raise click.UsageError("--domain is required (or set 'domain' in --config)")
    bad = [s for s in cfg.sources if s not in ADAPTER_CLASSES]
    if bad:
        raise click.UsageError(f"unknown source(s): {', '.join(bad)}; valid: {', '.join(ADAPTER_CLASSES)}")
    return cfg


def _engine(cfg: Config):
    from archive_recover.core.engine import RecoveryEngine

    return RecoveryEngine(cfg, log=lambda m: click.echo(m, err=True))


def _run(coro_factory):
    async def wrapper():
        eng, coro = coro_factory()
        try:
            return await coro
        finally:
            await eng.close()

    try:
        return asyncio.run(wrapper())
    except KeyboardInterrupt:
        click.echo("\nInterrupted. Progress is saved; re-run the same command to resume.", err=True)
        sys.exit(130)


domain_opt = click.option("--domain", help="Target domain (e.g. example.com)")
sources_opt = click.option("--sources", help=f"Comma-separated: {','.join(ADAPTER_CLASSES)}")


@click.group()
@click.version_option(__version__, prog_name="archive-recover")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              help="YAML config (see config/example.yaml)")
@click.option("--db", help="SQLite database path [archive_recover.db]")
@click.option("--output", help="Output directory for payloads [./recovered]")
@click.option("--rate-limit", type=float, help="Seconds between requests per host [1.5]")
@click.option("--user-agent", help="HTTP User-Agent (include contact info for heavy use)")
@click.pass_context
def cli(ctx: click.Context, config_path, db, output, rate_limit, user_agent) -> None:
    """Universal Web Archive Recovery & Reconstruction Engine."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["overrides"] = {k: v for k, v in {
        "db_path": db, "output_dir": output, "rate_limit_delay": rate_limit,
        "user_agent": user_agent}.items() if v is not None}


@cli.command()
@domain_opt
@sources_opt
@click.option("--seeds", "seeds_file", type=click.Path(allow_dash=True),
              help="File of URLs (one per line, '-' for stdin) for seed-based sources")
@click.option("--since", help="YYYY-MM-DD or YYYYMMDDhhmmss")
@click.option("--until", help="YYYY-MM-DD or YYYYMMDDhhmmss")
@click.option("--subdomains/--no-subdomains", default=None, help="Include subdomains")
@click.option("--collapse", help="Wayback CDX collapse field, e.g. 'digest'")
@click.option("--page-size", type=int, help="CDX rows per page [5000]")
@click.option("--max-pages", type=int, help="Stop after N pages (resumable later)")
@click.option("--fresh", is_flag=True, help="Ignore saved resume keys and start over")
@click.option("--metadata-only", is_flag=True, help="Accepted for compatibility; discover never downloads payloads")
@click.pass_context
def discover(ctx, domain, sources, seeds_file, since, until, subdomains, collapse, page_size,
             max_pages, fresh, metadata_only) -> None:
    """Discover captures from configured sources and index them in SQLite."""
    cfg = _cfg(ctx, domain, sources=_split(sources), since=since, until=until,
               include_subdomains=subdomains, collapse=collapse, page_size=page_size,
               max_pages=max_pages, seeds=_read_seeds(seeds_file))
    click.echo(f"Discovering {cfg.domain} from: {', '.join(cfg.sources)}")

    def go():
        eng = _engine(cfg)
        return eng, eng.discover(cfg.sources, resume=not fresh)

    results = _run(go)
    for r in results:
        state = "complete" if r.complete else ("ERROR" if r.error else "partial")
        click.echo(f"  {r.source:10} {state:9} new={r.new} seen={r.seen} pages={r.pages}"
                   + (f" out_of_scope={r.out_of_scope}" if r.out_of_scope else "")
                   + (f" resumed_from={r.resumed_from}" if r.resumed_from else ""))
        if r.notes:
            click.echo(f"             {r.notes}")
        if r.error:
            click.echo(f"             error: {r.error}")
    if any(r.error for r in results):
        sys.exit(2)


@cli.command()
@domain_opt
@sources_opt
@click.option("--resume", is_flag=True, help="Continue a previous run (always the behavior; kept for compatibility)")
@click.option("--max-concurrency", type=int, help="Parallel downloads [2]")
@click.option("--types", help=f"MIME categories: {','.join(MIME_CATEGORIES)} (default all)")
@click.option("--limit", type=int, help="Retrieve at most N captures this run")
@click.option("--no-retry-failed", is_flag=True, help="Only pending captures, skip previous failures")
@click.option("--max-storage", type=int, help="Total byte budget for stored objects")
@click.pass_context
def recover(ctx, domain, sources, resume, max_concurrency, types, limit, no_retry_failed,
            max_storage) -> None:
    """Retrieve payloads for discovered captures (resumable)."""
    cfg = _cfg(ctx, domain, sources=_split(sources), max_concurrency=max_concurrency,
               retrieve_types=_split(types), max_storage_bytes=max_storage)
    click.echo(f"Recovering {cfg.domain} via {', '.join(cfg.sources)} "
               f"(concurrency={cfg.max_concurrency}, delay={cfg.rate_limit_delay}s)")

    def go():
        eng = _engine(cfg)
        return eng, eng.recover(cfg.sources, limit=limit, retry_failed=not no_retry_failed)

    s = _run(go)
    click.echo(f"  attempted={s.attempted} recovered={s.recovered} (dedup={s.deduplicated}) "
               f"failed={s.failed} storage_skipped={s.skipped_storage} "
               f"bytes_written={s.bytes_written:,}")
    if s.digest_mismatch:
        click.echo(f"  {s.digest_mismatch} payload(s) differ from the archive's SHA-1 digest")
    if s.errors:
        click.echo("  errors: " + ", ".join(f"{k}={v}" for k, v in s.errors.most_common()))
    if s.paused_sources:
        click.echo(f"  paused: {', '.join(s.paused_sources)} (source pushed back; retry later)")


@cli.command()
@domain_opt
@click.option("--json", "as_json", is_flag=True, help="Print the JSON report to stdout")
@click.pass_context
def report(ctx, domain, as_json) -> None:
    """Write coverage/integrity report (JSON + Markdown) to the output directory."""
    cfg = _cfg(ctx, domain)
    eng = _engine(cfg)
    jpath, mpath, st = eng.report()
    if as_json:
        click.echo(json.dumps(st, indent=2, default=str))
    else:
        click.echo(mpath.read_text(encoding="utf-8"))
        click.echo(f"Written: {jpath}\n         {mpath}", err=True)


@cli.command()
@domain_opt
@click.option("--at", help="Reconstruct as of this timestamp (YYYY-MM-DD or YYYYMMDDhhmmss)")
@click.option("--keep-scripts", is_flag=True, help="Leave <script> tags active (unsafe to browse)")
@click.pass_context
def reconstruct(ctx, domain, at, keep_scripts) -> None:
    """Build a browsable static reconstruction from recovered captures."""
    from archive_recover.core.config import _coerce
    from archive_recover.core.reconstruct import Reconstructor

    cfg = _cfg(ctx, domain)
    at_dt = _coerce("until", at) if at else None
    eng = _engine(cfg)
    r = Reconstructor(eng, at=at_dt, keep_scripts=keep_scripts).run()
    click.echo(f"Reconstructed {cfg.domain}: {r.pages} pages, {r.assets} assets, "
               f"{r.rewritten_links} links rewritten, {len(r.missing)} referenced URLs missing")
    click.echo(f"  site:  {r.site_dir}")
    click.echo(f"  open:  {r.site_dir / '_index.html'}")


@cli.command()
@domain_opt
@click.pass_context
def status(ctx, domain) -> None:
    """Show capture counts by source/status and recent jobs."""
    cfg = _cfg(ctx, domain)
    st = _engine(cfg).stats()
    click.echo(f"{st['domain']}: {st['unique_urls']} URLs, {st['captures_total']} captures, "
               f"{st['bytes_stored']:,} bytes stored")
    for src, counts in sorted(st["captures_by_source"].items()):
        disc = st["discovery_status_by_source"].get(src, "—")
        click.echo(f"  {src:10} discovery={disc:9} "
                   + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if st["errors_by_category"]:
        click.echo("  errors: " + ", ".join(f"{k}={v}" for k, v in st["errors_by_category"].items()))
    for j in st["recent_jobs"][:5]:
        click.echo(f"  job #{j['id']} {j['kind']}{'/' + j['source'] if j['source'] else ''} "
                   f"{j['status']} {j['started'] or ''}")


@cli.command()
@domain_opt
@click.option("--fix", is_flag=True, help="Requeue captures whose files are missing or corrupt")
@click.pass_context
def verify(ctx, domain, fix) -> None:
    """Re-hash stored payloads against their recorded SHA-256."""
    cfg = _cfg(ctx, domain)
    r = _engine(cfg).verify(fix=fix)
    click.echo("Verify: " + ", ".join(f"{k}={v}" for k, v in sorted(r.items())) if r
               else "Verify: nothing stored yet")
    if r.get("missing") or r.get("corrupt"):
        sys.exit(0 if fix else 1)


@cli.command()
@domain_opt
@click.pass_context
def reconcile(ctx, domain) -> None:
    """Requeue missing/corrupt files and retriable failures for the next `recover`."""
    cfg = _cfg(ctx, domain)
    r = _engine(cfg).reconcile()
    click.echo("Reconcile: " + ", ".join(f"{k}={v}" for k, v in sorted(r.items())))


@cli.command("sources")
def list_sources() -> None:
    """List available archive sources and their limitations."""
    for name, cls in ADAPTER_CLASSES.items():
        a = cls()
        click.echo(f"{name:10} enumeration={'yes' if a.supports_domain_enumeration() else 'seeds'}")
        click.echo(f"           {a.limitations()}")


if __name__ == "__main__":
    cli()
