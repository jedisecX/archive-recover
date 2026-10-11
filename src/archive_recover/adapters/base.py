"""Base classes and shared types for archive adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Optional


@dataclass
class CaptureRecord:
    """Normalized capture metadata from any source."""

    original_url: str
    timestamp: Optional[datetime] = None
    timestamp_str: Optional[str] = None  # raw YYYYMMDDHHMMSS or similar
    snapshot_url: Optional[str] = None
    mime_type: Optional[str] = None
    status_code: Optional[int] = None
    digest: Optional[str] = None  # source-provided (e.g. SHA1 for IA)
    length: Optional[int] = None
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def replay_url(self) -> Optional[str]:
        return self.snapshot_url


@dataclass
class DiscoveryResult:
    """Result of a discovery pass."""

    captures: list[CaptureRecord]
    next_resume_key: Optional[str] = None
    complete: bool = False
    error: Optional[str] = None
    source_notes: Optional[str] = None


class BaseAdapter(ABC):
    """Independent adapter interface. Failures must not affect other adapters."""

    name: str = "base"
    base_url: str = ""

    def __init__(self, rate_limit_delay: float = 1.0, timeout: float = 30.0) -> None:
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout

    @abstractmethod
    async def discover(
        self,
        domain: str,
        *,
        prefix: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        seed_urls: Optional[list[str]] = None,
        resume_key: Optional[str] = None,
        limit: int = 1000,
    ) -> DiscoveryResult:
        """Enumerate captures. Must be paginated / resumable where the source supports it."""
        ...

    @abstractmethod
    async def retrieve(self, capture: CaptureRecord) -> tuple[bytes, dict[str, Any]]:
        """Fetch payload. Returns (content, response_headers_or_metadata)."""
        ...

    def supports_domain_enumeration(self) -> bool:
        return False

    def limitations(self) -> str:
        return "Generic adapter."
