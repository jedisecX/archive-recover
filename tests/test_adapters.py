"""Basic tests for adapters and utilities (mocked)."""

import pytest
from archive_recover.adapters.wayback import WaybackAdapter
from archive_recover.adapters.archiveis import ArchiveIsAdapter


def test_wayback_limitations():
    a = WaybackAdapter()
    assert "CDX" in a.limitations()
    assert a.supports_domain_enumeration()


def test_archiveis_no_domain_enum():
    a = ArchiveIsAdapter()
    assert not a.supports_domain_enumeration()
    assert "No stable public bulk API" in a.limitations()


@pytest.mark.asyncio
async def test_wayback_discover_mock():
    # Placeholder: in full suite use responses or httpx mock
    a = WaybackAdapter()
    # Would mock CDX JSON response here
    assert a.name == "wayback"
