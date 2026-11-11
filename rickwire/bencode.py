"""Just enough bencode to read a .torrent's name and info-hash."""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, unquote, urlparse


def _decode(b: bytes, i: int):
    c = b[i:i + 1]
    if c == b"i":
        e = b.index(b"e", i)
        return int(b[i + 1:e]), e + 1
    if c == b"l":
        i, out = i + 1, []
        while b[i:i + 1] != b"e":
            v, i = _decode(b, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i, out, start = i + 1, {}, i
        while b[i:i + 1] != b"e":
            k, i = _decode(b, i)
            vstart = i
            v, i = _decode(b, i)
            out[k] = v
            if k == b"info":
                out[b"__info_span"] = (vstart, i)
        return out, i + 1
    colon = b.index(b":", i)
    n = int(b[i:colon])
    return b[colon + 1:colon + 1 + n], colon + 1 + n


def torrent_info(data: bytes) -> tuple[str | None, str | None]:
    """-> (name, btih hex)"""
    try:
        d, _ = _decode(data, 0)
        s, e = d[b"__info_span"]
        name = d[b"info"].get(b"name", b"").decode("utf-8", "replace") or None
        return name, hashlib.sha1(data[s:e]).hexdigest()
    except Exception:
        return None, None


def magnet_name(magnet: str) -> str | None:
    q = parse_qs(urlparse(magnet).query)
    if q.get("dn"):
        return unquote(q["dn"][0])
    xt = next((x for x in q.get("xt", []) if x.lower().startswith("urn:btih:")), None)
    return xt[9:] if xt else None
