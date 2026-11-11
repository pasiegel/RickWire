from __future__ import annotations

from .base import Provider, ProviderError, RemoteItem, RemoteStatus, Resolved

API = "https://www.premiumize.me/api"
ERROR_STATES = {"error", "deleted", "banned", "timeout"}


class Premiumize(Provider):
    id = "premiumize"
    name = "Premiumize"
    api_hosts = frozenset({"www.premiumize.me"})
    key_url = "https://www.premiumize.me/account"
    signup_url = "https://www.premiumize.me/"
    dashboard_url = "https://www.premiumize.me/transfers"
    key_steps = ("Log in to premiumize.me in your browser.",
                 "Open your Account page (Get API key button).",
                 "Reveal the API key, copy it and paste it here. You can reset it on the same page.")

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    async def _call(self, method: str, path: str, **kw) -> dict:
        d = self._check(await self.client.request(method, f"{API}{path}", headers=self._h(), **kw))
        if d.get("status") != "success":
            raise ProviderError(f"Premiumize: {d.get('message') or 'request failed'}")
        return d

    async def account(self) -> dict:
        d = await self._call("GET", "/account/info")
        return {"user": d.get("customer_id"), "premium": bool(d.get("premium_until")), "expires": d.get("premium_until")}

    async def add_magnet(self, magnet: str) -> str:
        return (await self._call("POST", "/transfer/create", data={"src": magnet}))["id"]

    async def add_torrent(self, data: bytes, filename: str) -> str:
        return (await self._call("POST", "/transfer/create", files={"file": (filename, data, "application/x-bittorrent")}))["id"]

    cache_check = "lookup"

    async def cached(self, magnets: dict[str, str]) -> dict[str, bool | None]:
        try:
            d = await self._call("GET", "/cache/check", params={"items[]": list(magnets.values())})
        except Exception:
            return {h: None for h in magnets}
        res = d.get("response") or []
        return {h: bool(res[i]) if i < len(res) else None for i, h in enumerate(magnets)}

    async def status(self, remote_id: str) -> RemoteStatus:
        transfers = (await self._call("GET", "/transfer/list")).get("transfers", [])
        t = next((x for x in transfers if x.get("id") == remote_id), None)
        if not t:
            return RemoteStatus("error", message="Premiumize: transfer vanished")
        st = t.get("status")
        if st in ERROR_STATES:
            return RemoteStatus("error", message=f"Premiumize: {t.get('message') or st}")
        if st not in ("finished", "seeding"):
            return RemoteStatus("downloading" if st == "running" else "queued", float(t.get("progress") or 0) * 100,
                                t.get("name"), message=t.get("message") or st or "")
        items: list[RemoteItem] = []
        if t.get("file_id"):
            f = await self._call("GET", "/item/details", params={"id": t["file_id"]})
            items.append(RemoteItem(f.get("name"), int(f.get("size") or 0), f["link"]))
        elif t.get("folder_id"):
            await self._walk(t["folder_id"], "", items)
        return RemoteStatus("ready", 100, t.get("name"), sum(i.size for i in items), items=items)

    async def _walk(self, folder_id: str, prefix: str, items: list[RemoteItem]):
        d = await self._call("GET", "/folder/list", params={"id": folder_id})
        for c in d.get("content", []):
            if c.get("type") == "folder":
                await self._walk(c["id"], f"{prefix}{c['name']}/", items)
            elif c.get("link"):
                items.append(RemoteItem(prefix + c["name"], int(c.get("size") or 0), c["link"]))

    async def resolve(self, handle: str) -> Resolved:
        return Resolved(handle, handle.rsplit("/", 1)[-1] or "download")

    async def delete(self, remote_id: str) -> None:
        try:
            await self._call("POST", "/transfer/delete", data={"id": remote_id})
        except ProviderError:
            pass

    async def unrestrict(self, link: str) -> list[Resolved]:
        d = await self._call("POST", "/transfer/directdl", data={"src": link})
        content = d.get("content") or []
        if not content:
            raise ProviderError("Premiumize could not unrestrict that link")
        return [Resolved(c["link"], c.get("path", "download").split("/")[-1], int(c.get("size") or 0)) for c in content]
