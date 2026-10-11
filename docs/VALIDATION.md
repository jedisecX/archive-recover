# Validation Matrix

| Component | Unit Tests | Integration (live) | Notes |
|-----------|------------|--------------------|-------|
| URL normalization | Planned / fixture-based | N/A | IDN, www, http/https, redirects |
| Wayback CDX adapter | Mocked responses | Optional public query | Pagination, field parsing |
| archive.is adapter | Mocked | Limited (known snapshots only) | No bulk enumeration claimed |
| Megalodon adapter | Mocked | Limited | Seed-based |
| Memento TimeMap | Mocked link-format | Optional IA TimeMap | Per-URL |
| SQLite schema & migrations | Planned | N/A | Versioned |
| Checkpoint / resume | Planned | N/A | Interruption simulation |
| Hash verification | Planned | N/A | SHA-256 of recovered bytes |
| Path traversal / SSRF | Planned | N/A | Defensive tests |
| Reconstruction link rewrite | Planned | N/A | Safe rewriting only |
| Reporting accuracy | Planned | N/A | Distinguishes discovery vs retrieval |

**Status**: Core adapters and schema implemented. Full test suite and production hardening are incremental next steps. Live integration tests are not required for unit correctness and are run only when permitted.

No fabricated successful downloads or capture counts.
