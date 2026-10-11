# Troubleshooting Guide

## Common Issues

### CDX queries return empty or timeout
- Large domains need pagination (`resumeKey` or multiple pages).
- Internet Archive rate-limits aggressive clients. Increase delay, lower concurrency.
- Confirm URL encoding and `matchType`.

### archive.is returns interstitial or challenge
- Expected. The adapter does not bypass. Use known snapshot URLs only.
- Provide seeds manually.

### Megalodon returns Japanese error pages or encoding issues
- Check Content-Type and charset. Adapter stores raw bytes; reconstruction may need manual charset handling.

### Resume does not continue
- Use the same `--db` path. `archive-recover status` shows recent jobs.
- Discovery resumes from the newest `discover:<source>` checkpoint that is not complete;
  `--fresh` ignores it.
- Captures that failed permanently (404, blocked, invalid) are not retried; `reconcile`
  requeues only retriable failures below `max_attempts`.

### Storage limit hit
- Raise `max_storage_bytes` / `--max-storage`, or prioritize with `--types html,css`.

### Source paused
- After `circuit_breaker` consecutive 429/challenge responses the source is paused for
  that run. Wait, then re-run `recover`; lower `--max-concurrency` or raise `--rate-limit`.

### Reconstruction missing assets
- Report lists unresolved dependencies. Many third-party or unarchived resources will be missing by design.

## Logs

Progress goes to stderr. Every failure is stored in the `errors` table (category + message)
and on the capture (`captures.last_error`); `report` summarizes them by category.

## Support

This is a research-oriented recovery engine. Report issues with the exact command, domain, and source used. Do not include recovered content that may be sensitive.
