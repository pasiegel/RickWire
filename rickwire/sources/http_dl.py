"""Resumable HTTP downloader shared by every source."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import httpx

from ..providers.base import USER_AGENT

CHUNK = 1 << 20
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

Progress = Callable[[int, int], None]   # (bytes_done_for_file, file_total)


def safe_name(name: str, fallback: str = "download") -> str:
    name = _BAD.sub("_", unquote(name or "")).strip().rstrip(". ")
    if not name:
        name = fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name[:200]


def safe_relpath(rel: str) -> Path:
    parts = [safe_name(p) for p in re.split(r"[\\/]+", rel) if p not in ("", ".", "..")]
    return Path(*parts) if parts else Path("download")


def name_from_response(r: httpx.Response) -> str | None:
    cd = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd) or re.search(r'filename\s*=\s*"?([^";]+)"?', cd)
    if m:
        return unquote(m.group(1))
    tail = urlparse(str(r.url)).path.rsplit("/", 1)[-1]
    return unquote(tail) or None


def make_client(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(60, read=120), follow_redirects=True,
                             headers={"User-Agent": USER_AGENT}, **kw)


async def download(client: httpx.AsyncClient, url: str, dest_dir: Path, name: str | None,
                   progress: Progress, size_hint: int = 0, headers: dict | None = None) -> Path:
    """Download url into dest_dir. If name is None it is taken from the response.
    Resumes from an existing .part file when the server supports ranges."""
    headers = dict(headers or {})
    dest_dir.mkdir(parents=True, exist_ok=True)

    if name:
        target = dest_dir / safe_relpath(name)
        if target.exists() and (not size_hint or target.stat().st_size == size_hint):
            progress(target.stat().st_size, target.stat().st_size)
            return target

    for attempt in range(2):
        offset = 0
        if name:
            part = (dest_dir / safe_relpath(name)).with_name((dest_dir / safe_relpath(name)).name + ".part")
            offset = part.stat().st_size if part.exists() else 0
        req_headers = headers | ({"Range": f"bytes={offset}-"} if offset else {})
        async with client.stream("GET", url, headers=req_headers) as r:
            if r.status_code == 416 and offset:
                total = offset
            else:
                r.raise_for_status()
                if not name:
                    name = safe_name(name_from_response(r) or "download")
                    if attempt == 0 and (dest_dir / (name + ".part")).exists():
                        continue  # reopen with a Range header now that we know the name
                if r.status_code != 206:
                    offset = 0
                length = int(r.headers.get("content-length") or 0)
                total = offset + length if length else size_hint
            target = dest_dir / safe_relpath(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(target.name + ".part")
            if r.status_code != 416:
                with open(part, "r+b" if offset else "wb") as fh:
                    fh.seek(offset)
                    fh.truncate()
                    done = offset
                    progress(done, total)
                    async for chunk in r.aiter_bytes(CHUNK):
                        fh.write(chunk)
                        done += len(chunk)
                        progress(done, total)
            os.replace(part, target)
            return target
    raise RuntimeError("download failed")
