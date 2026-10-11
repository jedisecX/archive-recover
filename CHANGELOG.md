# Changelog

## 0.2.0 — 2026-10-11

Turns the v0.1.0 skeleton into a working pipeline.

### Fixed
- `urls` unique constraint was a no-op expression in the class body; captures had no dedupe.
- Wayback pagination used the last row's timestamp as a "resume key"; now uses real CDX `showResumeKey`.
- archive.is and Megalodon `retrieve()` fell back to fetching the **live original site**.
- archive.is snapshot detection matched any `archive.` host (including archive.org).
- Memento: `limit` only broke the inner loop; errors were swallowed while reporting `complete=True`; TimeMap lines with commas in dates mis-parsed.
- `is_safe_url` was never called; missed 0.0.0.0, IPv4-mapped IPv6, CGNAT, integer hosts; no redirect checks.
- No size limits, rate limiting or retries existed despite SECURITY.md.
- `datetime.utcnow` deprecations; async test required an uninstalled plugin.

### Added
- Working `discover`, `recover`, `verify`, `reconcile`, `report`, `status`, `reconstruct`, `sources` commands.
- YAML config loading (`--config`), seed files (`--seeds`), MIME-category filters, storage budget.
- Content-addressed storage, IA digest verification, dedupe, circuit breaker, in-place DB migration.
- 66 offline tests.

### Removed
- Unused dependencies: pydantic, pydantic-settings, alembic, python-dateutil, tenacity, structlog, filelock, pytest-asyncio, responses.
