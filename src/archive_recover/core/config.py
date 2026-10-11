"""Configuration: YAML file + CLI overrides, with validated defaults."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from archive_recover.core.http import DEFAULT_USER_AGENT
from archive_recover.core.utils import parse_timestamp

# MIME categories for --types / retrieve_types
MIME_CATEGORIES: dict[str, tuple[str, ...]] = {
    "html": ("text/html", "application/xhtml+xml"),
    "css": ("text/css",),
    "js": ("application/javascript", "text/javascript", "application/x-javascript",
           "application/ecmascript", "text/ecmascript"),
    "images": ("image/",),
    "documents": ("application/pdf", "application/msword", "application/vnd.",
                  "text/plain", "application/rtf", "text/csv"),
    "fonts": ("font/", "application/font", "application/x-font", "application/vnd.ms-fontobject"),
    "media": ("audio/", "video/"),
    "data": ("application/json", "application/xml", "text/xml", "application/rss+xml",
             "application/atom+xml"),
}


def mime_matches(mime: Optional[str], categories: Optional[list[str]]) -> bool:
    """True if ``mime`` falls in any of ``categories`` (None/empty/'all' = everything)."""
    if not categories or "all" in categories:
        return True
    m = (mime or "").split(";")[0].strip().lower()
    if m in ("", "unk", "warc/revisit"):
        return True  # unknown type: don't silently drop it
    for cat in categories:
        for prefix in MIME_CATEGORIES.get(cat, (cat,)):
            if m == prefix or (prefix.endswith(("/", ".")) and m.startswith(prefix)):
                return True
    return False


@dataclass
class Config:
    domain: Optional[str] = None
    output_dir: Path = Path("./recovered")
    db_path: str = "archive_recover.db"
    sources: list[str] = field(default_factory=lambda: ["wayback"])
    max_concurrency: int = 2
    rate_limit_delay: float = 1.5
    timeout: float = 30.0
    max_retries: int = 4
    max_attempts: int = 3  # per capture, across runs
    max_storage_bytes: int = 10 * 1024**3
    max_object_bytes: int = 50 * 1024**2
    retrieve_types: list[str] = field(default_factory=list)  # empty = all
    include_subdomains: bool = False
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    metadata_only: bool = False
    user_agent: str = DEFAULT_USER_AGENT
    page_size: int = 5000
    max_pages: Optional[int] = None
    collapse: Optional[str] = None  # wayback CDX collapse, e.g. "digest"
    seeds: list[str] = field(default_factory=list)
    timemaps: Optional[dict[str, str]] = None
    circuit_breaker: int = 5  # consecutive rate-limit/challenge failures before pausing a source

    def validate(self) -> "Config":
        unknown = [t for t in self.retrieve_types if t not in MIME_CATEGORIES and t != "all"
                   and "/" not in t]
        if unknown:
            raise ValueError(f"unknown retrieve_types: {unknown}; valid: {sorted(MIME_CATEGORIES)}")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.since and self.until and self.since > self.until:
            raise ValueError("since is after until")
        return self


def _coerce(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in ("since", "until"):
        if isinstance(value, datetime):
            return value
        dt = parse_timestamp(str(value).replace("-", "") if len(str(value)) == 10 else str(value))
        if dt is None:
            raise ValueError(f"cannot parse {name}={value!r} (use YYYY-MM-DD or YYYYMMDDhhmmss)")
        return dt
    if name == "output_dir":
        return Path(value)
    if name in ("sources", "retrieve_types", "seeds") and isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def load_config(path: Optional[str | Path] = None, **overrides: Any) -> Config:
    data: dict[str, Any] = {}
    if path:
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path}: expected a mapping at top level")
        data.update(loaded)
        dr = data.pop("date_range", None) or {}
        data.setdefault("since", dr.get("since"))
        data.setdefault("until", dr.get("until"))
    for k, v in overrides.items():
        if v is not None and v != () and v != []:
            data[k] = v
    valid = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - valid)
    if unknown:
        raise ValueError(f"unknown config keys: {unknown}")
    cfg = Config(**{k: _coerce(k, v) for k, v in data.items()})
    return cfg.validate()
