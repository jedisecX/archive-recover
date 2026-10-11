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
- Check `job_checkpoints` table for last `resume_key` and stage.
- Ensure the same database path is used.

### Storage limit hit
- Configure `max_storage_bytes`. Prioritize HTML over media if needed.

### Reconstruction missing assets
- Report lists unresolved dependencies. Many third-party or unarchived resources will be missing by design.

## Logs

Structured JSON logs (when full logging is wired) redact sensitive query parameters. Check console output and DB `errors` table.

## Support

This is a research-oriented recovery engine. Report issues with the exact command, domain, and source used. Do not include recovered content that may be sensitive.
