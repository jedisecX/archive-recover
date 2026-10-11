# Implementation Status — v0.2.0

## Implemented and tested (offline, mocked archive endpoints)

- **Wayback CDX**: `output=json` + `showResumeKey=true` pagination, host/domain/prefix match types, date range, `collapse`, `id_` raw retrieval, archived non-200 captures accepted only with `Memento-Datetime`.
- **Memento**: RFC 6690 link-format parser (commas inside quoted dates, single-line or multi-line TimeMaps), per-seed resume, `id_` retrieval for IA mementos, configurable archives.
- **archive.today**: snapshot-link seeds, TimeMap lookup for original URLs, challenge detection (never bypassed), early stop on 429/403.
- **Megalodon**: snapshot URL parsing (incl. `https:/` collapse and percent-encoded Japanese paths), metadata-only records with gyo.tc hint for non-snapshot seeds.
- **HTTP layer**: per-host rate limiter shared across workers, retries with exponential backoff + jitter, `Retry-After`, streamed downloads with per-object byte cap, SSRF guard + adapter host allowlist on every redirect hop.
- **Storage**: content-addressed objects, atomic writes (tmp + fsync + rename), total storage budget, digest-based dedupe without re-downloading.
- **Pipeline**: discovery checkpoints per page, per-capture commits, attempt caps, permanent vs. retriable failure classification, per-source circuit breaker.
- **verify / reconcile / report / status / reconstruct** — all functional.
- **Schema**: unique constraints that v0.1.0 declared but never created; additive in-place migration of v0.1.0 databases.

## Not verified live

The test environment could not reach web.archive.org, archive.ph or megalodon.jp. Response
handling is tested against fakes that reproduce the documented formats. Run a small live
check before large jobs:

```bash
archive-recover discover --domain example.com --max-pages 1 --page-size 50
archive-recover recover  --domain example.com --limit 5
```

## Known gaps

- No recursive asset discovery from recovered HTML (only what the archives index).
- Reconstruction does not rewrite URLs inside JavaScript.
- archive.today TimeMap availability varies by mirror and is often challenged.
