from __future__ import annotations

import asyncio

from .base import Provider, ProviderError, RemoteItem, RemoteStatus, Resolved

API = "https://api.real-debrid.com/rest/1.0"
ERROR_STATES = {"error", "virus", "dead", "magnet_error"}


class RealDebrid(Provider):
    id = "realdebrid"
    name = "Real-Debrid"
    api_hosts = frozenset({"api.real-debrid.com"})
    key_url = "https://real-debrid.com/apitoken"
    signup_url = "https://real-debrid.com/"
    dashboard_url = "https://real-debrid.com/torrents"
    key_steps = ("Log in to real-debrid.com in your browser.",
                 "Open the API token page (Get API key button).",
                 "Copy \"Your private API token\" and paste it here.",
                 "Treat it like a password - anyone with it can use your account. Regenerate it on that page if it ever leaks.")

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    async def account(self) -> dict:
        d = self._check(await self.client.get(f"{API}/user", headers=self._h()))
        return {"user": d.get("username"), "premium": d.get("type") == "premium", "expires": d.get("expiration")}

    async def add_magnet(self, magnet: str) -> str:
        d = self._check(await self.client.post(f"{API}/torrents/addMagnet", headers=self._h(), data={"magnet": magnet}))
        return d["id"]

    async def add_torrent(self, data: bytes, filename: str) -> str:
        d = self._check(await self.client.put(f"{API}/torrents/addTorrent", headers=self._h(), content=data))
        return d["id"]

    async def status(self, remote_id: str) -> RemoteStatus:
        d = self._check(await self.client.get(f"{API}/torrents/info/{remote_id}", headers=self._h()))
        st = d.get("status")
        if st == "waiting_files_selection":
            # Always the whole torrent: Real-Debrid only counts a torrent as cached for the exact set of files
            # selected, so picking a subset here can turn an instant download into a slow (or failing) one.
            # The user's file choice is applied when RickWire downloads to the PC instead.
            await self.select_files(remote_id, [])
            return RemoteStatus("queued", 0, d.get("filename"), d.get("bytes", 0), message="Selecting files")
        if st in ERROR_STATES:
            return RemoteStatus("error", message=f"Real-Debrid: {st}")
        if st != "downloaded":
            return RemoteStatus("downloading" if st == "downloading" else "queued", float(d.get("progress", 0)),
                                d.get("filename"), d.get("bytes", 0), int(d.get("speed") or 0), st or "")
        selected = [f for f in d.get("files", []) if f.get("selected")]
        links = d.get("links", [])
        same = len(selected) == len(links)
        items = [RemoteItem(selected[i]["path"].lstrip("/") if same else None,
                            selected[i]["bytes"] if same else 0, link) for i, link in enumerate(links)]
        return RemoteStatus("ready", 100, d.get("filename"), d.get("bytes", 0), items=items)

    async def select_files(self, remote_id: str, handles: list[str]) -> None:
        self._check(await self.client.post(f"{API}/torrents/selectFiles/{remote_id}", headers=self._h(),
                                           data={"files": ",".join(handles) or "all"}))

    async def cached(self, magnets: dict[str, str]) -> dict[str, bool | str | None]:
        # instantAvailability is disabled (error 37), so: add, see if it's "downloaded" straight away, delete.
        out: dict[str, bool | str | None] = {}
        for h, magnet in magnets.items():
            for attempt in range(2):
                out[h] = await self._probe(magnet)
                if out[h] is not None:
                    break
                await asyncio.sleep(2)   # usually Real-Debrid's rate limit - one more try
        return out

    async def _probe(self, magnet: str) -> bool | str | None:
        rid = None
        try:
            rid = await self.add_magnet(magnet)
            for _ in range(4):
                d = self._check(await self.client.get(f"{API}/torrents/info/{rid}", headers=self._h()))
                st = d.get("status")
                if st == "waiting_files_selection":
                    await self.select_files(rid, [])
                elif st == "downloaded":
                    return True
                elif st != "magnet_conversion":
                    return False
                await asyncio.sleep(1)
            return False
        except ProviderError as e:
            return "blocked" if "infringing" in str(e) else None   # Real-Debrid's filter refuses it outright
        except Exception:
            return None
        finally:
            if rid:
                try:
                    await self.delete(rid)
                except Exception:
                    pass

    async def resolve(self, handle: str) -> Resolved:
        d = self._check(await self.client.post(f"{API}/unrestrict/link", headers=self._h(), data={"link": handle}))
        return Resolved(d["download"], d.get("filename") or "download", int(d.get("filesize") or 0))

    async def delete(self, remote_id: str) -> None:
        await self.client.delete(f"{API}/torrents/delete/{remote_id}", headers=self._h())

    async def unrestrict(self, link: str) -> list[Resolved]:
        if "/folder/" in link or "#F!" in link:
            links = self._check(await self.client.post(f"{API}/unrestrict/folder", headers=self._h(), data={"link": link}))
            if not isinstance(links, list) or not links:
                raise ProviderError("Real-Debrid could not expand that folder")
            return [await self.resolve(l) for l in links]
        return [await self.resolve(link)]
