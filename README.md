# Universal Web Archive Recovery & Reconstruction Engine

Production-grade tool for discovering, indexing, downloading, verifying, and reconstructing archived web content from:

- **Internet Archive (Wayback Machine)** — full CDX API support
- **archive.today / archive.is** — conservative discovery (no stable public bulk API)
- **Megalodon.jp** — Japanese on-demand archive with limited programmatic access
- **Memento TimeMaps** — standards-based cross-archive discovery where available

## Key Principles

- **Lawful & conservative**: No CAPTCHA bypass, no rate-limit evasion, no anti-automation circumvention.
- **Resumable & durable**: SQLite-backed jobs, checkpoints, atomic writes.
- **Honest reporting**: Distinguishes discoverable vs. retrieved vs. referenced vs. historically complete.
- **Security-conscious**: SSRF protection, path traversal guards, untrusted input handling, size limits.
- **Modular adapters**: Independent source adapters so one failure does not block others.

## Installation

```bash
cd archive-recover
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

Supported Python: 3.11+

## Quick Start

```bash
# Discover captures for a domain (metadata only recommended first)
archive-recover discover --domain example.com --sources wayback --metadata-only

# Full recovery (respects rate limits)
archive-recover recover --domain example.com --sources wayback --max-concurrency 2

# Resume interrupted job
archive-recover recover --domain example.com --resume

# Generate coverage report
archive-recover report --domain example.com

# Reconstruct browsable static site (optional)
archive-recover reconstruct --domain example.com
```

See `docs/` for detailed usage, security notes, and limitations.

## Limitations (Source-Imposed)

| Source | Domain-wide enumeration | Public API | Notes |
|--------|--------------------------|------------|-------|
| Wayback | Excellent (CDX `matchType=domain`) | Yes | Rate-limited free tier; use responsibly |
| archive.is | Poor | No | Form-based search, frequent challenges; seed-URL or manual workflow only |
| Megalodon | Limited | No | On-demand; search via gyo.tc / known patterns; Japanese encoding handling |
| Memento | Good for known URLs | TimeMap | Aggregators may be slow or discontinued (e.g. TimeTravel) |

The system never claims exhaustive historical coverage. "Discovery complete within configured scope" is used only when the implemented interfaces terminate under the given rules.

## Project Structure

```
src/archive_recover/
├── adapters/       # Source-specific discovery & retrieval
├── core/           # DB, queue, verification, reconstruction
├── cli.py          # Click CLI
└── ...
```

## License

See LICENSE. Attributions for archive interfaces and Memento RFC 7089 are documented in docs/ATTRIBUTION.md.

## Validation Matrix

See `docs/VALIDATION.md` for which adapters, operations, and tests were verified vs. mocked.
