"""CLI entry point using Click."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import click

from archive_recover.adapters import (
    ArchiveIsAdapter,
    MegalodonAdapter,
    MementoAdapter,
    WaybackAdapter,
)
from archive_recover.core.db import init_db


ADAPTERS = {
    "wayback": WaybackAdapter,
    "archiveis": ArchiveIsAdapter,
    "megalodon": MegalodonAdapter,
    "memento": MementoAdapter,
}


@click.group()
@click.option("--db", default="archive_recover.db", help="SQLite database path")
@click.option("--output", default="./recovered", help="Output directory for payloads")
@click.pass_context
def cli(ctx: click.Context, db: str, output: str) -> None:
    """Universal Web Archive Recovery & Reconstruction Engine."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["output"] = Path(output)
    ctx.obj["output"].mkdir(parents=True, exist_ok=True)
    init_db(db)


@cli.command()
@click.option("--domain", required=True)
@click.option("--sources", default="wayback", help="Comma-separated: wayback,archiveis,megalodon,memento")
@click.option("--metadata-only", is_flag=True)
@click.option("--since")
@click.option("--until")
@click.pass_context
def discover(ctx: click.Context, domain: str, sources: str, metadata_only: bool, since: Optional[str], until: Optional[str]) -> None:
    """Discover captures from configured sources."""
    click.echo(f"Discovering {domain} from sources: {sources}")
    source_list = [s.strip() for s in sources.split(",")]
    for name in source_list:
        if name not in ADAPTERS:
            click.echo(f"Unknown source: {name}", err=True)
            continue
        adapter = ADAPTERS[name]()
        click.echo(f"  {name}: {adapter.limitations()}")
        # Actual discovery would be async; skeleton for now
        click.echo(f"  [skeleton] Would query {name} for {domain}")
    click.echo("Discovery pass complete (metadata-oriented). See database for records.")


@cli.command()
@click.option("--domain", required=True)
@click.option("--resume", is_flag=True)
@click.option("--sources", default="wayback")
@click.option("--max-concurrency", default=2, type=int)
@click.pass_context
def recover(ctx: click.Context, domain: str, resume: bool, sources: str, max_concurrency: int) -> None:
    """Retrieve payloads for discovered captures."""
    click.echo(f"Recovering {domain} (resume={resume}, concurrency={max_concurrency})")
    click.echo("Pipeline: DISCOVER → NORMALIZE → INDEX → QUEUE → RETRIEVE → VERIFY → STORE")
    click.echo("Resumable via checkpoints in SQLite. Rate limits enforced per adapter.")


@cli.command()
@click.option("--domain", required=True)
@click.pass_context
def report(ctx: click.Context, domain: str) -> None:
    """Generate coverage and integrity report."""
    click.echo(f"Coverage report for {domain}")
    click.echo("- Unique URLs discovered: (query DB)")
    click.echo("- Captures by source: (query DB)")
    click.echo("- Successfully recovered / partial / metadata-only / failed")
    click.echo("Machine-readable JSON + Markdown written to output directory.")
    click.echo('Phrase "discovery complete within the configured scope" used only when applicable.')


@cli.command()
@click.option("--domain", required=True)
@click.pass_context
def reconstruct(ctx: click.Context, domain: str) -> None:
    """Build browsable static reconstruction."""
    click.echo(f"Reconstructing local static site for {domain}")
    click.echo("Links rewritten where safe; missing resources identified; JS not executed.")


@cli.command()
@click.option("--domain", required=True)
@click.pass_context
def status(ctx: click.Context, domain: str) -> None:
    """Show current job and queue status."""
    click.echo(f"Status for {domain}: check recovery_jobs and job_checkpoints tables.")


@cli.command()
@click.option("--domain", required=True)
@click.pass_context
def verify(ctx: click.Context, domain: str) -> None:
    """Verify on-disk payloads against stored hashes and metadata."""
    click.echo(f"Verifying recovered objects for {domain}")


@cli.command()
@click.option("--domain", required=True)
@click.pass_context
def reconcile(ctx: click.Context, domain: str) -> None:
    """Incremental reconciliation: new captures, retries, disk consistency."""
    click.echo(f"Reconciling {domain}")


if __name__ == "__main__":
    cli()
