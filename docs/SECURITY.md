# Security and Operational Constraints

This tool is designed for lawful archival recovery of publicly available historical content.

## Protections Implemented

- Request timeouts; per-host rate limiting; exponential backoff + jitter; `Retry-After` honored
- Descriptive user-agent (override with `--user-agent`, include contact info for heavy use)
- SSRF protection: only http(s); loopback, private, link-local (169.254/metadata), CGNAT,
  reserved, IPv4-mapped IPv6 and integer-encoded hosts rejected; credentials in URLs rejected
- Per-adapter host allowlist, enforced on the initial request **and every redirect hop**
- Adapters never fall back to fetching the live original site
- Path traversal protection on object storage and reconstruction (`safe_join`)
- Per-object byte cap (streamed, aborted early) and total storage budget
- Recovered JavaScript is never executed; reconstruction neutralizes `<script>` and inline
  `on*` handlers by default (`--keep-scripts` to opt out)
- Anti-bot challenges detected and reported; a source is paused after repeated pushback
- Metadata-only mode (`metadata_only: true`); `discover` never downloads payloads

## Explicit Non-Goals

- Circumventing authentication, paywalls, CAPTCHAs, or anti-automation measures
- Exploiting archive infrastructure
- Claiming cryptographic authenticity of historical content based solely on local hashes
- Exhaustive historical completeness (impossible in general)

## Archive-Specific Notes

- **Wayback**: Public CDX is rate-limited. Respect delays. Use `id_` form carefully.
- **archive.is**: Do not attempt automated form submission or challenge solving.
- **Megalodon**: On-demand; respect service terms.

Treat all recovered content as untrusted.
