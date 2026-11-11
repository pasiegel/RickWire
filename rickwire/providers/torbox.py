from __future__ import annotations

import asyncio

from .base import Provider, ProviderError, RemoteItem, RemoteStatus, Resolved

API = "https://api.torbox.app/v1/api"


class TorBox(Provider):
    id = "torbox"
    name = "TorBox"
    api_hosts = frozenset({"api.torbox.app"})
    key_url = "https://torbox.app/settings"
    signup_url = "https://torbox.app/"
    dashboard_url = "https://torbox.app/dashboard"
    key_steps = ("Log in to torbox.app in your browser.",
                 "Open Settings (Get API key button) and find the API Key section.",
                 "Copy the key and paste it here. You can regenerate it there if it ever leaks.")

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    async def _call(self, method: str, path: str, **kw):
        d = self._check(await self.client.request(method, f"{API}{path}", headers=self._h(), **kw))
        if d.get("success") is False:
            raise ProviderError(f"TorBox: {d.get('detail') or d.get('error')}")
        return d.get("data")

    async def account(self) -> dict:
        d = await self._call("GET", "/user/me")
        return {"user": d.get("email"), "premium": bool(d.get("plan")), "expires": d.get("premium_expires_at")}

    async def add_magnet(self, magnet: str) -> str:
        d = await self._call("POST", "/torrents/createtorrent", data={"magnet": magnet})
        return f"t:{d['torrent_id']}"

    async def add_torrent(self, data: bytes, filename: str) -> str:
        d = await self._call("POST", "/torrents/createtorrent", files={"file": (filename, data, "application/x-bittorrent")})
        return f"t:{d['torrent_id']}"

    cache_check = "lookup"

    async def cached(self, magnets: dict[str, str]) -> dict[str, bool | None]:
        try:
            d = await self._call("GET", "/torrents/checkcached",
                                 params={"hash": ",".join(magnets), "format": "object", "list_files": "false"})
        except Exception:
            return {h: None for h in magnets}
        hits = {k.lower() for k in (d or {})}
        return {h: h in hits for h in magnets}

    async def status(self, remote_id: str) -> RemoteStatus:
        kind, rid = remote_id.split(":", 1)
        path = "/torrents/mylist" if kind == "t" else "/webdl/mylist"
        d = await self._call("GET", path, params={"id": rid, "bypass_cache": "true"})
        if isinstance(d, list):
            d = d[0] if d else {}
        state = str(d.get("download_state") or "")
        name, size = d.get("name"), int(d.get("size") or 0)
        if any(w in state.lower() for w in ("error", "failed")):
            return RemoteStatus("error", message=f"TorBox: {state}")
        if not (d.get("download_finished") and d.get("download_present")):
            return RemoteStatus("downloading", float(d.get("progress") or 0) * 100, name, size,
                                int(d.get("download_speed") or 0), state)
        items = []
        for f in d.get("files") or []:
            fname = f.get("name") or f.get("short_name")
            if name and fname.startswith(name + "/"):
                fname = fname[len(name) + 1:]
            items.append(RemoteItem(fname, int(f.get("size") or 0), f"{kind}:{rid}:{f['id']}"))
        return RemoteStatus("ready", 100, name, size, items=items)

    async def resolve(self, handle: str) -> Resolved:
        kind, rid, fid = handle.split(":", 2)
        if kind == "t":
            url = await self._call("GET", "/torrents/requestdl", params={"token": self.key, "torrent_id": rid, "file_id": fid})
        else:
            url = await self._call("GET", "/webdl/requestdl", params={"token": self.key, "web_id": rid, "file_id": fid})
        return Resolved(url, "download")

    async def delete(self, remote_id: str) -> None:
        kind, rid = remote_id.split(":", 1)
        try:
            if kind == "t":
                await self._call("POST", "/torrents/controltorrent", json={"torrent_id": int(rid), "operation": "delete"})
            else:
                await self._call("POST", "/webdl/controlwebdownload", json={"webdl_id": int(rid), "operation": "delete"})
        except ProviderError:
            pass

    async def unrestrict(self, link: str) -> list[Resolved]:
        d = await self._call("POST", "/webdl/createwebdownload", data={"link": link})
        rid = f"w:{d['webdownload_id']}"
        for _ in range(360):
            st = await self.status(rid)
            if st.state == "error":
                raise ProviderError(st.message)
            if st.state == "ready":
                out = []
                for it in st.items:
                    r = await self.resolve(it.handle)
                    out.append(Resolved(r.url, it.name or "download", it.size))
                return out
            await asyncio.sleep(5)
        raise ProviderError("TorBox web download timed out")
