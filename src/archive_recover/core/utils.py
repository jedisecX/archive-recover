"""URL and domain utilities: normalization, boundary checks, SSRF protection, safe paths."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


# --------------------------------------------------------------------------- URLs


def _ensure_scheme(url: str) -> str:
    return url if "://" in url else f"http://{url}"


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication.

    - scheme/host lowercased, IDN hosts punycoded, ``www.`` stripped
    - default ports (:80 / :443) dropped (IA "original" values often carry ``:80``)
    - fragment removed, empty path -> ``/``
    - query parameters sorted (stable), percent-encoding normalized
    - http/https collapsed to ``http`` (archives treat them as the same resource)
    """
    if not url:
        return url
    parts = urlsplit(_ensure_scheme(url.strip()))
    scheme = parts.scheme.lower()
    if scheme == "https":
        scheme = "http"
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii") if host else host
    except UnicodeError:
        pass
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host
    if port and port != DEFAULT_PORTS.get(parts.scheme.lower()):
        netloc = f"{host}:{port}"
    path = quote(unquote(parts.path or "/"), safe="/:@!$&'()*+,;=-._~%")
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def domain_from_url(url: str) -> str:
    host = (urlsplit(_ensure_scheme(url)).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def url_in_scope(url: str, domain: str, include_subdomains: bool = True) -> bool:
    host = domain_from_url(url)
    domain = domain_from_url(domain)
    if host == domain:
        return True
    return include_subdomains and host.endswith("." + domain)


# --------------------------------------------------------------------------- SSRF

_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in ipaddress.ip_network("100.64.0.0/10")  # CGNAT
    )


def is_safe_url(url: str, allowed_hosts: Optional[Iterable[str]] = None) -> bool:
    """Reject non-HTTP(S), credentials-in-URL, private/loopback/reserved targets.

    If ``allowed_hosts`` is given, the host must equal one of them or be a
    subdomain of one. Adapters pass their archive hosts here so a crafted seed or
    redirect can never point the fetcher at an arbitrary site.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        return False
    try:
        if _ip_is_blocked(ipaddress.ip_address(host)):
            return False
    except ValueError:
        # Not a literal IP. Reject decimal/hex integer hosts like "2130706433".
        if re.fullmatch(r"(0x[0-9a-f]+|\d+)", host):
            return False
    if allowed_hosts is not None:
        allowed = [h.lower() for h in allowed_hosts]
        if not any(host == h or host.endswith("." + h) for h in allowed):
            return False
    return True


# --------------------------------------------------------------------------- time

def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse 14-digit archive timestamps (or shorter prefixes) and RFC 1123 dates.

    Returns naive UTC datetimes (SQLite stores naive values).
    """
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4,14}", value):
        # Pad partial timestamps: YYYY -> YYYY0101000000, YYYYMM -> YYYYMM01000000, ...
        padded = value + "0101000000"[len(value) - 4 :] if len(value) < 8 else value.ljust(14, "0")
        try:
            return datetime.strptime(padded, "%Y%m%d%H%M%S")
        except ValueError:
            return None
    dt: Optional[datetime]
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- files

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha1_base32(data: bytes) -> str:
    """Internet Archive CDX digest format: base32(SHA-1(payload))."""
    import base64

    return base64.b32encode(hashlib.sha1(data).digest()).decode("ascii")


def safe_join(root: Path, *parts: str) -> Path:
    """Join untrusted path parts under ``root``; raise if the result escapes it."""
    root = root.resolve()
    rel = PurePosixPath(*[p.replace("\\", "/") for p in parts])
    clean = [seg for seg in rel.parts if seg not in ("", ".", "/")]
    if any(seg == ".." for seg in clean):
        raise ValueError(f"path traversal rejected: {rel}")
    target = (root.joinpath(*clean)).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"path escapes output root: {target}")
    return target


_UNSAFE_SEG = re.compile(r"[^A-Za-z0-9._\-~+=,@]")


def url_to_local_path(url: str, mime: Optional[str] = None) -> str:
    """Map an archived URL to a relative filesystem path for reconstruction.

    ``http://ex.com/a/b?x=1`` -> ``ex.com/a/b__x=1.html`` (when HTML),
    directories become ``.../index.html``. Segments are sanitized; ``..`` cannot appear.
    """
    parts = urlsplit(_ensure_scheme(url))
    host = domain_from_url(url) or "_"
    segs = [s for s in unquote(parts.path).split("/") if s not in ("", ".", "..")]
    segs = [_UNSAFE_SEG.sub("_", s)[:120] or "_" for s in segs]
    is_dir = parts.path.endswith("/") or not segs
    is_html = (mime or "").startswith("text/html") or (mime is None and is_dir)
    if parts.query:
        qtag = _UNSAFE_SEG.sub("_", parts.query)[:60]
        qtag += "_" + hashlib.sha1(parts.query.encode()).hexdigest()[:8]
        if is_dir:
            segs.append(f"index__{qtag}")
        else:
            stem, dot, ext = segs[-1].rpartition(".")
            segs[-1] = f"{stem}__{qtag}.{ext}" if dot and stem else f"{segs[-1]}__{qtag}"
        is_dir = False
    if is_dir:
        segs.append("index.html")
    elif is_html and not segs[-1].lower().endswith((".html", ".htm")):
        segs[-1] += ".html"
    return "/".join([_UNSAFE_SEG.sub("_", host)] + segs)
