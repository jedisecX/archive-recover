"""RFC 6690 / RFC 7089 link-format parser for Memento TimeMaps.

Handles entries spread across lines *or* packed on one line, and commas inside
quoted values (``datetime="Sun, 20 Jan 2002 14:25:10 GMT"``), which a naive
split-on-comma or line-by-line regex gets wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class Link:
    uri: str
    params: dict[str, str] = field(default_factory=dict)

    @property
    def rels(self) -> set[str]:
        return set(self.params.get("rel", "").lower().split())

    @property
    def is_memento(self) -> bool:
        return "memento" in self.rels

    @property
    def datetime(self) -> Optional[str]:
        return self.params.get("datetime")


def parse_link_format(body: str) -> Iterator[Link]:
    i, n = 0, len(body)
    while i < n:
        start = body.find("<", i)
        if start == -1:
            return
        end = body.find(">", start + 1)
        if end == -1:
            return
        uri = body[start + 1 : end].strip()
        i = end + 1
        params: dict[str, str] = {}
        # Parse ;key=value pairs until a top-level comma or next '<'
        while i < n:
            while i < n and body[i] in " \t\r\n":
                i += 1
            if i >= n or body[i] == ",":
                i += 1
                break
            if body[i] == "<":
                break
            if body[i] != ";":
                i += 1
                continue
            i += 1
            while i < n and body[i] in " \t\r\n":
                i += 1
            k0 = i
            while i < n and body[i] not in "=;,\r\n":
                i += 1
            key = body[k0:i].strip().lower()
            value = ""
            if i < n and body[i] == "=":
                i += 1
                while i < n and body[i] in " \t":
                    i += 1
                if i < n and body[i] == '"':
                    close = body.find('"', i + 1)
                    close = n if close == -1 else close
                    value = body[i + 1 : close]
                    i = close + 1
                else:
                    v0 = i
                    while i < n and body[i] not in ";,\r\n":
                        i += 1
                    value = body[v0:i].strip()
            if key:
                params[key] = value
        if uri:
            yield Link(uri=uri, params=params)
