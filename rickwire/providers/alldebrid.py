from __future__ import annotations

import asyncio

from .base import Provider, ProviderError, RemoteItem, RemoteStatus, Resolved

API = "https://api.alldebrid.com"
AGENT = "RickWire"


class AllDebrid(Provider):
    id = "alldebrid"
    name = "AllDebrid"
    api_hosts = frozenset({"api.alldebrid.com"})
    key_url = "https://alldebrid.com/apikeys/"
    signup_url = "https://alldebrid.com/register/"
    dashboard_url = "https://alldebrid.com/magnets/"
    key_steps = ("Log in to alldebrid.com in your browser.",
                 "Open the API keys page (Get API key button).",
                 "Create a new key, name it \"RickWire\" so you can revoke just this app later, and copy it here.",
                 "AllDebrid may ask you to approve a new device/IP the first time - check your email.")

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    async def _call(self, method: str, path: str, **kw) -> dict:
        params = kw.pop("params", {}) | {"agent": AGENT}
        r = await self.client.request(method, f"{API}{path}", headers=self._h(), params=params, **kw)
        d = self._check(r)
        if d.get("status") != "success":
            err = d.get("error") or {}
            raise ProviderError(f"AllDebrid: {err.get('message') or err.get('code') or 'request failed'}")
        return d.get("data") or {}

    async def account(self) -> dict:
        u = (await self._call("GET", "/v4/user")).get("user", {})
        return {"user": u.get("username"), "premium": u.get("isPremium"), "expires": u.get("premiumUntil")}

    async def add_magnet(self, magnet: str) -> str:
        d = await self._call("POST", "/v4/magnet/upload", data={"magnets[]": magnet})
        m = d["magnets"][0]
        if m.get("error"):
            raise ProviderError(f"AllDebrid: {m['error'].get('message')}")
        return str(m["id"])

    async def add_torrent(self, data: bytes, filename: str) -> str:
        d = await self._call("POST", "/v4/magnet/upload/file", files={"files[]": (filename, data, "application/x-bittorrent")})
        f = d["files"][0]
        if f.get("error"):
            raise ProviderError(f"AllDebrid: {f['error'].get('message')}")
        return str(f["id"])

    async def cached(self, magnets: dict[str, str]) -> dict[str, bool | None]:
        # magnet/instant was removed, but upload answers "ready" for cached torrents - upload, read, delete.
        out: dict[str, bool | None] = {}
        for h, magnet in magnets.items():
            try:
                d = await self._call("POST", "/v4/magnet/upload", data={"magnets[]": magnet})
                m = (d.get("magnets") or [{}])[0]
                out[h] = None if m.get("error") else bool(m.get("ready"))
                if m.get("id"):
                    await self.delete(str(m["id"]))
            except Exception:
                out[h] = None
        return out

    async def status(self, remote_id: str) -> RemoteStatus:
        d = await self._call("GET", "/v4.1/magnet/status", params={"id": remote_id})
        m = d.get("magnets")
        if isinstance(m, list):
            m = m[0] if m else {}
        code = int(m.get("statusCode", 0))
        size = int(m.get("size") or 0)
        if code > 4:
            return RemoteStatus("error", message=f"AllDebrid: {m.get('status')}")
        if code < 4:
            done = int(m.get("downloaded") or 0)
            return RemoteStatus("downloading" if code else "queued", done * 100 / size if size else 0,
                                m.get("filename"), size, int(m.get("downloadSpeed") or 0), m.get("status", ""))
        items = await self._files(remote_id)
        return RemoteStatus("ready", 100, m.get("filename"), size, items=items)

    async def _files(self, remote_id: str) -> list[RemoteItem]:
        d = await self._call("POST", "/v4/magnet/files", data={"id[]": remote_id})
        items: list[RemoteItem] = []

        def walk(nodes, prefix=""):
            for n in nodes:
                path = f"{prefix}{n.get('n', 'file')}"
                if "e" in n:
                    walk(n["e"], path + "/")
                elif n.get("l"):
                    items.append(RemoteItem(path, int(n.get("s") or 0), n["l"]))

        for mag in d.get("magnets", []):
            walk(mag.get("files", []))
        return items

    async def resolve(self, handle: str) -> Resolved:
        d = await self._call("GET", "/v4/link/unlock", params={"link": handle})
        if d.get("delayed"):
            for _ in range(120):
                await asyncio.sleep(5)
                dd = await self._call("GET", "/v4/link/delayed", params={"id": d["delayed"]})
                if dd.get("status") == 2:
                    d["link"] = dd["link"]
                    break
                if dd.get("status") == 3:
                    raise ProviderError("AllDebrid: delayed link failed")
        if not d.get("link"):
            raise ProviderError("AllDebrid returned no link")
        return Resolved(d["link"], d.get("filename") or "download", int(d.get("filesize") or 0))

    async def delete(self, remote_id: str) -> None:
        try:
            await self._call("POST", "/v4/magnet/delete", data={"id": remote_id})
        except ProviderError:
            pass

    async def unrestrict(self, link: str) -> list[Resolved]:
        return [await self.resolve(link)]
