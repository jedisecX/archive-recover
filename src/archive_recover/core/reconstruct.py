"""Static, browsable reconstruction of a recovered site.

- Picks one capture per URL (latest, or nearest at/before ``--at``)
- Writes files under ``<output>/<domain>/site/`` (path-traversal safe)
- Rewrites links in HTML and CSS to relative local paths when the target was
  recovered; leaves everything else untouched and lists it as missing
- Unwraps archive replay URLs (``web.archive.org/web/<ts>/...``)
- Neutralizes ``<script>`` by default (recovered JS is untrusted and is never run)
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import select

from archive_recover.core.db import URL, Capture, ReconstructionManifest, Resource
from archive_recover.core.utils import (
    normalize_url,
    safe_join,
    url_in_scope,
    url_to_local_path,
    utcnow,
)

# Absolute or root-relative replay URLs whose inner part is itself an http(s) URL:
#   https://web.archive.org/web/20200101000000im_/http://ex.com/a.png
#   /web/20200101000000/https://ex.com/        (Wayback's relative rewrite)
#   https://archive.ph/20200101000000/http://ex.com/
_REPLAY_RE = re.compile(
    r"^(?:(?:https?:)?//(?:web\.archive\.org|archive\.(?:today|is|ph|li|vn|fo|md)))?"
    r"/(?:web/)?\d{8,14}[a-z]{0,2}_?/((?:https?:)?/{1,2}.+)$", re.I)
_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.I)
_CSS_IMPORT_RE = re.compile(r"""@import\s+(['"])([^'"]+)\1""", re.I)
URL_ATTRS = {
    "a": ["href"], "link": ["href"], "img": ["src", "srcset"], "script": ["src"],
    "iframe": ["src"], "frame": ["src"], "source": ["src", "srcset"], "video": ["src", "poster"],
    "audio": ["src"], "embed": ["src"], "object": ["data"], "form": ["action"],
    "input": ["src"], "track": ["src"], "area": ["href"], "body": ["background"],
    "td": ["background"], "table": ["background"],
}


def unwrap_replay(url: str) -> str:
    m = _REPLAY_RE.match(url.strip())
    if not m:
        return url
    inner = m.group(1)
    inner = re.sub(r"^(https?):/+", r"\1://", inner)  # "http:/x" -> "http://x"
    return inner if re.match(r"^https?://", inner) else "http:" + inner if inner.startswith("//") else inner


@dataclass
class ReconstructResult:
    pages: int = 0
    assets: int = 0
    rewritten_links: int = 0
    missing: dict[str, int] = field(default_factory=dict)  # in-scope URL -> references
    site_dir: Optional[Path] = None
    index: Optional[str] = None


class Reconstructor:
    def __init__(self, engine, *, at: Optional[datetime] = None, keep_scripts: bool = False):
        self.eng = engine
        self.at = at
        self.keep_scripts = keep_scripts
        self.site = engine.root / "site"
        self.map: dict[str, str] = {}  # normalized url -> relative local path

    def _select(self) -> list[tuple[Capture, Resource, str]]:
        with self.eng.Session() as s:
            rows = s.execute(
                select(Capture, Resource, URL.normalized)
                .join(Resource, Resource.capture_id == Capture.id)
                .join(URL, Capture.url_id == URL.id)
                .where(URL.domain_id == self.eng.domain_id, Capture.recovery_status == "recovered")
            ).all()
        best: dict[str, tuple[Capture, Resource, str]] = {}
        for cap, res, norm in rows:
            ts = cap.timestamp or datetime.min
            if self.at and ts > self.at:
                continue
            # Prefer successful (2xx) captures, then newest
            rank = (200 <= (cap.status_code or res.http_status or 200) < 300, ts)
            cur = best.get(norm)
            if cur is None:
                best[norm] = (cap, res, norm)
                continue
            c2, r2, _ = cur
            rank2 = (200 <= (c2.status_code or r2.http_status or 200) < 300,
                     c2.timestamp or datetime.min)
            if rank > rank2:
                best[norm] = (cap, res, norm)
        return list(best.values())

    def _ref(self, raw: str, base_url: str, page_rel: str, result: ReconstructResult) -> str:
        raw = raw.strip()
        if not raw or raw.startswith(("#", "data:", "mailto:", "tel:", "javascript:", "about:")):
            return raw
        unwrapped = unwrap_replay(raw)
        absolute = urljoin(base_url, unwrapped)
        if urlsplit(absolute).scheme not in ("http", "https"):
            return raw
        frag = urlsplit(absolute).fragment
        target = self.map.get(normalize_url(absolute))
        if target is None:
            if url_in_scope(absolute, self.eng.domain_name, self.eng.cfg.include_subdomains):
                key = normalize_url(absolute)
                result.missing[key] = result.missing.get(key, 0) + 1
            # Never leave a link pointing back into the archive's replay UI.
            return absolute if unwrapped != raw else raw
        rel = posixpath.relpath(target, posixpath.dirname(page_rel) or ".")
        result.rewritten_links += 1
        return rel + (f"#{frag}" if frag else "")

    def _rewrite_css(self, css: str, base_url: str, page_rel: str, result) -> str:
        css = _CSS_URL_RE.sub(
            lambda m: f"url({m.group(1)}{self._ref(m.group(2), base_url, page_rel, result)}{m.group(1)})",
            css)
        return _CSS_IMPORT_RE.sub(
            lambda m: f"@import {m.group(1)}{self._ref(m.group(2), base_url, page_rel, result)}{m.group(1)}",
            css)

    def _rewrite_html(self, data: bytes, base_url: str, page_rel: str, result) -> bytes:
        soup = BeautifulSoup(data, "lxml")
        base_tag = soup.find("base", href=True)
        if base_tag:
            base_url = urljoin(base_url, unwrap_replay(base_tag["href"]))
            base_tag.decompose()
        for tag in soup.find_all(list(URL_ATTRS)):
            for attr in URL_ATTRS[tag.name]:
                val = tag.get(attr)
                if not val or not isinstance(val, str):
                    continue
                if attr == "srcset":
                    parts = []
                    for item in val.split(","):
                        bits = item.strip().split(None, 1)
                        if bits:
                            bits[0] = self._ref(bits[0], base_url, page_rel, result)
                            parts.append(" ".join(bits))
                    tag[attr] = ", ".join(parts)
                else:
                    tag[attr] = self._ref(val, base_url, page_rel, result)
        for tag in soup.find_all(style=True):
            tag["style"] = self._rewrite_css(tag["style"], base_url, page_rel, result)
        for tag in soup.find_all("style"):
            if tag.string:
                tag.string.replace_with(self._rewrite_css(tag.string, base_url, page_rel, result))
        if not self.keep_scripts:
            for tag in soup.find_all("script"):
                tag["data-archived-type"] = tag.get("type", "text/javascript")
                tag["type"] = "text/plain"  # inert: never executed by the browser
            for tag in soup.find_all(True):
                for attr in [a for a in tag.attrs if a.lower().startswith("on")]:
                    del tag[attr]
        # Drop Wayback toolbar/injected markup if a replay (non-id_) payload slipped in
        for tag in soup.find_all(id=re.compile(r"^wm-ipp")):
            tag.decompose()
        return soup.encode("utf-8")  # bs4 rewrites <meta charset> to match

    def run(self) -> ReconstructResult:
        result = ReconstructResult(site_dir=self.site)
        chosen = self._select()
        # Pass 1: assign local paths
        for cap, res, norm in chosen:
            mime = (cap.mime_type or res.content_type or "").split(";")[0].strip() or None
            self.map[norm] = url_to_local_path(norm, mime)
        self.site.mkdir(parents=True, exist_ok=True)
        # Pass 2: write files
        pages: list[str] = []
        for cap, res, norm in chosen:
            rel = self.map[norm]
            src = safe_join(self.eng.root, res.local_path)
            if not src.is_file():
                continue
            data = src.read_bytes()
            dest = safe_join(self.site, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            mime = (cap.mime_type or res.content_type or "").split(";")[0].strip().lower()
            original = norm
            if mime in ("text/html", "application/xhtml+xml") or rel.endswith((".html", ".htm")):
                data = self._rewrite_html(data, original, rel, result)
                result.pages += 1
                pages.append(rel)
            elif mime == "text/css" or rel.endswith(".css"):
                text = data.decode("utf-8", errors="replace")
                data = self._rewrite_css(text, original, rel, result).encode("utf-8")
                result.assets += 1
            else:
                result.assets += 1
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        # Entry point
        root_rel = self.map.get(normalize_url(f"http://{self.eng.domain_name}/"))
        result.index = root_rel or (sorted(pages)[0] if pages else None)
        self._write_index(pages, result)
        manifest = {
            "domain": self.eng.domain_name, "generated_at": utcnow().isoformat() + "Z",
            "at": self.at.isoformat() if self.at else None, "entry": result.index,
            "files": self.map, "missing": dict(sorted(result.missing.items(),
                                                      key=lambda kv: -kv[1])),
            "pages": result.pages, "assets": result.assets,
            "rewritten_links": result.rewritten_links, "scripts_neutralized": not self.keep_scripts,
        }
        (self.site / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with self.eng.Session() as s:
            s.add(ReconstructionManifest(domain_id=self.eng.domain_id,
                                         snapshot_key=self.at.strftime("%Y%m%d%H%M%S") if self.at else "latest",
                                         manifest={k: v for k, v in manifest.items() if k != "files"}))
            s.commit()
        return result

    def _write_index(self, pages: list[str], result: ReconstructResult) -> None:
        from html import escape

        items = "\n".join(f'<li><a href="{escape(p)}">{escape(p)}</a></li>' for p in sorted(pages))
        missing = "\n".join(f"<li>{escape(u)} <small>({n} refs)</small></li>"
                            for u, n in sorted(result.missing.items(), key=lambda kv: -kv[1])[:500])
        html = f"""<!doctype html><meta charset="utf-8">
<title>Reconstruction — {escape(self.eng.domain_name)}</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}}
small{{color:#777}}</style>
<h1>{escape(self.eng.domain_name)}</h1>
<p>Static reconstruction from archived captures. Scripts are {"kept" if self.keep_scripts else "neutralized"}.
Entry page: {f'<a href="{escape(result.index)}">{escape(result.index)}</a>' if result.index else "none"}</p>
<h2>Pages ({len(pages)})</h2><ul>{items}</ul>
<h2>Referenced but not recovered ({len(result.missing)})</h2><ul>{missing}</ul>
"""
        (self.site / "_index.html").write_text(html, encoding="utf-8")
