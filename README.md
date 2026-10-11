# Universal Web Archive Recovery & Reconstruction Engine

Discover, download, verify and rebuild archived web content from:

- **Internet Archive (Wayback Machine)** — full CDX enumeration with real `resumeKey` pagination
- **archive.today / archive.is** — seed-based (snapshot links, or originals via Memento TimeMap)
- **Megalodon.jp** — seed-based (known snapshot URLs)
- **Memento TimeMaps** — standards-based per-URL discovery (RFC 7089)

## Key Principles

- **Lawful & conservative**: no CAPTCHA bypass, no rate-limit evasion. Challenge pages are detected and reported; a source that keeps pushing back is paused for the run.
- **Never fetches the live site**: retrieval only ever hits archive hosts.
- **Resumable & durable**: SQLite state, per-page discovery checkpoints, per-capture commits, atomic file writes.
- **Honest reporting**: discovered vs. recovered vs. metadata-only vs. failed are reported separately; "Discovery complete within the configured scope" only appears when the source's enumeration actually terminated.
- **Security-conscious**: per-adapter host allowlists enforced on every redirect hop, private/loopback/metadata IPs blocked, path-traversal-safe writes, per-object and total byte limits, recovered scripts neutralized in reconstructions.

## Installation

```bash
cd archive-recover
python -m venv .venv
source .venv/bin/activate            # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

Python 3.11+. Termux works (`pkg install python libxml2 libxslt` first for lxml).

## Quick Start

```bash
# 1. Index every Wayback capture of the host (metadata only, no payloads)
archive-recover discover --domain example.com --sources wayback --collapse digest

# 2. Download payloads (raw id_ bytes), HTML+CSS+images first
archive-recover recover --domain example.com --types html,css,images --max-concurrency 2

# 3. Interrupted? Just run it again — it continues where it stopped
archive-recover recover --domain example.com

# 4. Integrity + coverage
archive-recover verify  --domain example.com
archive-recover report  --domain example.com          # writes report.json + report.md

# 5. Browsable static copy (latest captures, or --at 2015-06-01)
archive-recover reconstruct --domain example.com
```

Seed-based sources take a URL list (one per line, `#` comments, `-` for stdin):

```bash
archive-recover discover --domain example.com --sources memento,archiveis,megalodon --seeds urls.txt
```

All settings can live in a YAML file (`config/example.yaml`) and be overridden on the command line:

```bash
archive-recover --config config/example.yaml discover
```

## Commands

| Command | What it does |
|---|---|
| `discover` | Query sources, store captures in SQLite (deduplicated, scope-filtered). Resumes from the last checkpoint; `--fresh` restarts. `--max-pages N` for incremental runs. |
| `recover` | Download pending captures. Retries transient errors with backoff (honors `Retry-After`), skips already-stored identical payloads by digest, enforces storage budget, caps attempts per capture. |
| `verify` | Re-hash stored objects against recorded SHA-256. `--fix` requeues missing/corrupt ones. |
| `reconcile` | `verify --fix` + requeue retriable failures. |
| `report` | Coverage/integrity report (Markdown + JSON, `--json` to stdout). |
| `reconstruct` | Static site under `<output>/<domain>/site/` with links rewritten to local files; `_index.html` lists pages and referenced-but-missing URLs. |
| `status` | Counts by source/status, recent jobs. |
| `sources` | Adapters and their limitations. |

## Output layout

```
recovered/<domain>/
├── objects/ab/ab12…            # content-addressed payloads (SHA-256), deduplicated
├── report.json, report.md
└── site/                       # reconstruct output
    ├── _index.html, _manifest.json
    └── example.com/index.html …
```

Wayback payloads are checked against the CDX digest (base32 SHA-1); matches are marked `verified`.

## Limitations (Source-Imposed)

| Source | Domain-wide enumeration | Public API | Notes |
|--------|--------------------------|------------|-------|
| Wayback | Excellent (CDX `matchType=host`/`domain`) | Yes | Rate-limited free tier; use responsibly |
| archive.is | No | TimeMap only | Frequent challenges; seed URLs only |
| Megalodon | No | No | Snapshot-URL seeds; others kept as metadata with a gyo.tc hint |
| Memento | Per known URL | TimeMap | Aggregators may be slow or discontinued |

The system never claims exhaustive historical coverage.

## Development

```bash
pytest            # 66 tests, fully offline (archive endpoints are mocked with httpx.MockTransport)
```

See `docs/` for security notes, validation matrix and troubleshooting. Attribution in `docs/ATTRIBUTION.md`.

## License

See LICENSE.
