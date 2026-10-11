# Validation Matrix

All tests run offline: archive endpoints are served by `tests/conftest.py::FakeArchive`
through `httpx.MockTransport`, reproducing CDX JSON (+ resumeKey trailer), `id_` replay
headers, link-format TimeMaps, 429/503 responses, challenge pages and hostile redirects.

| Component | Tests | Live | Notes |
|-----------|-------|------|-------|
| URL normalization (www, ports, IDN, query order, https→http) | `test_core.py` | N/A | |
| SSRF guard + host allowlist (loopback, 169.254, mapped IPv6, CGNAT, int hosts, creds) | `test_core.py`, redirect test in `test_adapters.py` | N/A | Checked on every redirect hop |
| Wayback CDX parse + pagination + params | `test_adapters.py` | Not run (blocked) | |
| Wayback retrieval (`id_`, archived 404, 503 retry, size cap) | `test_adapters.py` | Not run | |
| Memento TimeMap (parser, limit, 404 = zero captures) | `test_core.py`, `test_adapters.py` | Not run | |
| archive.is (host detection, TimeMap 429, challenge, no live fetch) | `test_adapters.py` | Not run | |
| Megalodon (snapshot parsing, Japanese paths, no live fetch) | `test_adapters.py` | Not run | |
| Schema constraints + v0.1.0 migration | `test_core.py` | N/A | |
| Discovery persistence, dedupe, scope, resume | `test_pipeline.py` | N/A | |
| Recovery: dedupe, digest verify, failures, type filter, storage cap, circuit breaker | `test_pipeline.py` | N/A | |
| verify / reconcile (tamper detection, requeue) | `test_pipeline.py` | N/A | |
| Reconstruction (link rewrite, replay unwrap, srcset, CSS, charset, script neutralizing, `--at`) | `test_pipeline.py` | N/A | |
| Full CLI run | `test_pipeline.py::test_cli_end_to_end` | N/A | |

No fabricated successful downloads or capture counts.
