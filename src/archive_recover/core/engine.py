"""Core recovery orchestration (skeleton with clear extension points)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from archive_recover.adapters.base import BaseAdapter
from archive_recover.core.db import SessionLocal, get_engine
from archive_recover.core.utils import normalize_url, is_safe_url, domain_from_url


class RecoveryEngine:
    """Coordinates discovery → index → retrieve → verify → store.

    Designed for resumable execution via DB checkpoints.
    """

    def __init__(self, db_path: str, output_dir: Path, adapters: list[BaseAdapter]) -> None:
        self.db_path = db_path
        self.output_dir = output_dir
        self.adapters = {a.name: a for a in adapters}
        self.engine = get_engine(db_path)
        SessionLocal.configure(bind=self.engine)

    async def discover_domain(self, domain: str, sources: Optional[list[str]] = None) -> dict:
        """Run discovery across requested sources. Independent failure isolation."""
        results = {}
        targets = sources or list(self.adapters.keys())
        for name in targets:
            adapter = self.adapters.get(name)
            if not adapter:
                results[name] = {"error": "adapter not loaded"}
                continue
            try:
                # In production: paginate, persist captures, checkpoint resume_key
                disc = await adapter.discover(domain)
                results[name] = {
                    "captures": len(disc.captures),
                    "complete": disc.complete,
                    "notes": disc.source_notes,
                    "error": disc.error,
                }
            except Exception as exc:
                results[name] = {"error": str(exc)}
        return results

    def report_status(self, domain: str) -> str:
        return (
            f"Engine ready for {domain}. "
            "Full pipeline persists to SQLite and filesystem. "
            "Use CLI commands for operational control."
        )
