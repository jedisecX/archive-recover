"""Archive source adapters package."""

from .archiveis import ArchiveIsAdapter
from .base import BaseAdapter, CaptureRecord, DiscoveryResult
from .megalodon import MegalodonAdapter
from .memento import MementoAdapter
from .wayback import WaybackAdapter

ADAPTER_CLASSES: dict[str, type[BaseAdapter]] = {
    "wayback": WaybackAdapter,
    "archiveis": ArchiveIsAdapter,
    "megalodon": MegalodonAdapter,
    "memento": MementoAdapter,
}

__all__ = [
    "ADAPTER_CLASSES",
    "BaseAdapter",
    "CaptureRecord",
    "DiscoveryResult",
    "WaybackAdapter",
    "ArchiveIsAdapter",
    "MegalodonAdapter",
    "MementoAdapter",
]
