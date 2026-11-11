"""Native MEGA public link downloader (files and folders), decrypting on the fly.

Subject to MEGA's free transfer quota - route through a debrid provider to avoid it.
"""
from __future__ import annotations

import base64
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .http_dl import CHUNK, Progress, safe_relpath

API = "https://g.api.mega.co.nz/cs"
MEGA_RE = re.compile(r"mega\.(nz|co\.nz|io)/", re.I)


class MegaError(Exception):
    pass


def is_mega(url: str) -> bool:
    return bool(MEGA_RE.search(url))


def _b64(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/").replace(",", "")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _a32(b: bytes) -> tuple[int, ...]:
    return struct.unpack(f">{len(b) // 4}I", b)


def _bytes(a: tuple[int, ...]) -> bytes:
    return struct.pack(f">{len(a)}I", *a)


def _ecb_decrypt(key: bytes, data: bytes) -> bytes:
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return d.update(data) + d.finalize()


def _attrs(at: str, key: bytes) -> dict:
    d = Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).decryptor()
    raw = (d.update(_b64(at)) + d.finalize()).rstrip(b"\0")
    if not raw.startswith(b"MEGA"):
        raise MegaError("Bad decryption key")
    return json.loads(raw[4:].decode("utf-8", "replace"))


def _file_key(k: tuple[int, ...]) -> tuple[bytes, bytes]:
    """32-byte node key -> (aes key, ctr nonce)."""
    aes = _bytes((k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7]))
    return aes, _bytes((k[4], k[5]))


@dataclass
class MegaFile:
    path: str
    size: int
    aes: bytes
    nonce: bytes
    req: dict           # api request for the download url
    folder: str | None  # folder handle for the n= query


def parse(url: str) -> tuple[str, str, str, str | None]:
    """-> (kind 'file'|'folder', handle, key, sub-file handle)"""
    m = re.search(r"/(file|folder)/([\w-]+)#([\w,-]+)(?:/file/([\w-]+))?", url)
    if m:
        return m.group(1), m.group(2), m.group(3), m.group(4)
    m = re.search(r"#(F?)!([\w-]+)!([\w,-]+)", url)
    if m:
        return ("folder" if m.group(1) else "file"), m.group(2), m.group(3), None
    raise MegaError("Unrecognised MEGA link (is the #key part missing?)")


async def _api(client: httpx.AsyncClient, payload: dict, folder: str | None = None):
    params = {"id": "0"} | ({"n": folder} if folder else {})
    r = await client.post(API, params=params, json=[payload])
    r.raise_for_status()
    d = r.json()
    if isinstance(d, int) or (isinstance(d, list) and isinstance(d[0], int)):
        code = d if isinstance(d, int) else d[0]
        raise MegaError({-9: "File not found / removed", -11: "Access denied", -16: "Blocked by MEGA",
                         -17: "Transfer quota exceeded", -18: "MEGA temporarily unavailable"}.get(code, f"MEGA error {code}"))
    return d[0]


async def listing(client: httpx.AsyncClient, url: str) -> tuple[str, list[MegaFile]]:
    kind, handle, key, sub = parse(url)
    try:
        raw_key = _b64(key)
    except ValueError:
        raise MegaError("Bad MEGA key in link") from None
    if len(raw_key) not in (16, 32):
        raise MegaError("Bad MEGA key in link (truncated?)")
    if kind == "file":
        k = _a32(raw_key)
        if len(k) != 8:
            raise MegaError("Bad MEGA file key")
        aes, nonce = _file_key(k)
        info = await _api(client, {"a": "g", "p": handle})
        name = _attrs(info["at"], aes).get("n", handle)
        return name, [MegaFile(name, int(info["s"]), aes, nonce, {"a": "g", "g": 1, "p": handle}, None)]

    master = raw_key[:16]
    d = await _api(client, {"a": "f", "c": 1, "r": 1, "ca": 1}, folder=handle)
    nodes, names, parents = d["f"], {}, {}
    keys: dict[str, tuple] = {}
    for n in nodes:
        enc = n.get("k", "").split(":")[-1]
        if not enc:
            continue
        raw = _ecb_decrypt(master, _b64(enc))
        keys[n["h"]] = _a32(raw)
        aes = _file_key(_a32(raw))[0] if n["t"] == 0 else raw[:16]
        try:
            names[n["h"]] = _attrs(n["a"], aes).get("n", n["h"])
        except MegaError:
            names[n["h"]] = n["h"]
        parents[n["h"]] = n.get("p")

    root = next((n["h"] for n in nodes if n["t"] == 1 and n.get("p") not in names), None)

    def path_of(h: str) -> str:
        parts = []
        while h in names and h != root:
            parts.append(names[h])
            h = parents.get(h)
        return "/".join(reversed(parts))

    files = []
    for n in nodes:
        if n["t"] != 0 or n["h"] not in keys or (sub and n["h"] != sub):
            continue
        aes, nonce = _file_key(keys[n["h"]])
        files.append(MegaFile(path_of(n["h"]), int(n.get("s", 0)), aes, nonce,
                              {"a": "g", "g": 1, "n": n["h"]}, handle))
    return names.get(root, handle), files


async def download_file(client: httpx.AsyncClient, f: MegaFile, dest_dir: Path, progress: Progress) -> Path:
    target = dest_dir / safe_relpath(f.path)
    if target.exists() and target.stat().st_size == f.size:
        progress(f.size, f.size)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    offset = (part.stat().st_size // 16) * 16 if part.exists() else 0
    info = await _api(client, f.req, f.folder)
    counter = int.from_bytes(f.nonce + b"\0" * 8, "big") + offset // 16
    dec = Cipher(algorithms.AES(f.aes), modes.CTR(counter.to_bytes(16, "big"))).decryptor()
    url = info["g"] + (f"/{offset}-" if offset else "")
    async with client.stream("GET", url) as r:
        if r.status_code == 509:
            raise MegaError("MEGA transfer quota exceeded - try again later or use a debrid provider")
        r.raise_for_status()
        with open(part, "r+b" if offset else "wb") as fh:
            fh.seek(offset)
            fh.truncate()
            done = offset
            async for chunk in r.aiter_bytes(CHUNK):
                fh.write(dec.update(chunk))
                done += len(chunk)
                progress(done, f.size)
            fh.write(dec.finalize())
    part.replace(target)
    return target
