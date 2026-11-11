"""Gofile.io downloader via the official API.

Gofile restricts API content listing to premium accounts and signs website requests with a rotating,
obfuscated token (stale/forged tokens get the IP banned), so RickWire does NOT emulate the website.
Options: store a Gofile premium account token (Settings > Gofile), or let a debrid provider handle it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import httpx

from .. import vault

API = "https://api.gofile.io"
GOFILE_RE = re.compile(r"gofile\.io/d/([\w-]+)", re.I)
VAULT_KEY = "gofile"


class GofileError(Exception):
    pass


def is_gofile(url: str) -> bool:
    return bool(GOFILE_RE.search(url))


def has_token() -> bool:
    return bool(vault.get(VAULT_KEY))


@dataclass
class GoFile:
    path: str
    size: int
    url: str


def headers(token: str) -> dict:
    return {"Cookie": f"accountToken={token}"}


async def listing(client: httpx.AsyncClient, url: str, password: str | None = None) -> tuple[str, list[GoFile], str]:
    m = GOFILE_RE.search(url)
    if not m:
        raise GofileError("Not a gofile link")
    token = vault.get(VAULT_KEY)
    if not token:
        raise GofileError("Gofile needs a premium account token (Settings) or a debrid provider that supports it")
    files: list[GoFile] = []

    async def walk(cid: str, prefix: str) -> str:
        params = {"password": hashlib.sha256(password.encode()).hexdigest()} if password else {}
        r = await client.get(f"{API}/contents/{cid}", params=params, headers={"Authorization": f"Bearer {token}"})
        d = r.json()
        if d.get("status") != "ok":
            hint = " (API access needs Gofile premium)" if d.get("status") == "error-notPremium" else ""
            raise GofileError(f"Gofile: {d.get('status')}{hint}")
        data = d["data"]
        if data.get("password") and data.get("passwordStatus") != "passwordOk":
            raise GofileError("Gofile folder is password protected")
        if data.get("type") == "file":
            files.append(GoFile(prefix + data["name"], int(data.get("size") or 0), data["link"]))
            return data["name"]
        for child in (data.get("children") or {}).values():
            if child.get("type") == "folder":
                await walk(child["id"], f"{prefix}{child['name']}/")
            else:
                files.append(GoFile(prefix + child["name"], int(child.get("size") or 0), child["link"]))
        return data.get("name") or cid

    name = await walk(m.group(1), "")
    return name, files, token
