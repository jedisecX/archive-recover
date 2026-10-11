"""URL and domain utilities: normalization, boundary checks, SSRF protection."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse, urlunparse, quote

PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def normalize_url(url: str) -> str:
    """Basic normalization: lowercase host, remove fragment, normalize scheme."""
    if not url:
        return url
    parsed = urlparse(url if "://" in url else f"http://{url}")
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or "/"
    # Drop fragment
    return urlunparse((parsed.scheme or "http", netloc, path, parsed.params, parsed.query, ""))


def is_safe_url(url: str) -> bool:
    """Reject non-HTTP(S) and private/localhost targets."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        if host in ("localhost", "127.0.0.1", "::1"):
            return False
        try:
            ip = ipaddress.ip_address(host)
            for net in PRIVATE_NETS:
                if ip in net:
                    return False
        except ValueError:
            pass  # hostname, not IP
        return True
    except Exception:
        return False


def domain_from_url(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"http://{url}")
    return (parsed.hostname or "").lower().removeprefix("www.")
