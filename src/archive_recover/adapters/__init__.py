"""Archive source adapters package."""

from .base import BaseAdapter, CaptureRecord, DiscoveryResult
from .wayback import WaybackAdapter
from .archiveis import ArchiveIsAdapter
from .megalodon import MegalodonAdapter
from .memento import MementoAdapter

__all__ = [
    "BaseAdapter",
    "CaptureRecord",
    "DiscoveryResult",
    "WaybackAdapter",
    "ArchiveIsAdapter",
    "MegalodonAdapter",
    "MementoAdapter",
]
