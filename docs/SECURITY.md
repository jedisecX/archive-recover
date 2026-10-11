# Security and Operational Constraints

This tool is designed for lawful archival recovery of publicly available historical content.

## Protections Implemented

- Request timeouts and configurable rate limits with exponential backoff + jitter
- Descriptive user-agent
- Safe URL parsing; rejection of localhost, private IPs, and non-HTTP(S) schemes (SSRF protection)
- Path traversal protection on local filesystem writes and reconstruction
- File size and total storage limits
- MIME / signature checks where feasible
- No execution of recovered JavaScript, binaries, or macros
- Sensitive values redacted from structured logs
- Dry-run and metadata-only modes

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
