r"""Security audit: intercepts every request each debrid provider would send and checks where the API key,
magnet and .torrent go. No network traffic leaves the PC and no real keys are used.

    .venv\Scripts\python tests\audit_provider_security.py
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rickwire import vault  # noqa: E402
from rickwire.providers import ALL, ProviderError  # noqa: E402

MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Audit+Test"
TORRENT = b"d8:announce30:udp://tracker.example:1337/ann4:infod4:name5:audit6:lengthi1e12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee"
KEYS = {pid: f"SECRETKEY-{pid}-9f8e7d6c5b4a" for pid in ALL}

# Known, intentional exceptions (documented provider API requirements)
ALLOWED_KEY_IN_URL = {("torbox", "/v1/api/torrents/requestdl"), ("torbox", "/v1/api/webdl/requestdl")}

vault.get = lambda name: KEYS.get(name)          # fake vault - never touches Credential Manager
vault._known.update(KEYS.values())


def fake_api(pid: str, req: httpx.Request) -> httpx.Response:
    p = req.url.path
    ok = lambda data, **kw: httpx.Response(200, json=data, **kw)
    if pid == "realdebrid":
        if p.endswith("/user"): return ok({"username": "rick", "type": "premium", "expiration": "2027-01-01"})
        if "addMagnet" in p or "addTorrent" in p: return ok({"id": "RD1"})
        if "/torrents/info/" in p:
            return ok({"status": "downloaded", "filename": "Audit", "bytes": 1,
                       "files": [{"id": 1, "path": "/a.mkv", "bytes": 1, "selected": 1}],
                       "links": ["https://real-debrid.com/d/ABC"]})
        if "unrestrict/link" in p: return ok({"download": "https://cdn.example/a.mkv", "filename": "a.mkv", "filesize": 1})
        return ok({})
    if pid == "alldebrid":
        d = {}
        if p.endswith("/user"): d = {"user": {"username": "rick", "isPremium": True, "premiumUntil": 1800000000}}
        elif p.endswith("/magnet/upload"): d = {"magnets": [{"id": 7}]}
        elif p.endswith("/upload/file"): d = {"files": [{"id": 7}]}
        elif p.endswith("/magnet/status"): d = {"magnets": {"id": 7, "statusCode": 4, "filename": "Audit", "size": 1}}
        elif p.endswith("/magnet/files"): d = {"magnets": [{"files": [{"n": "a.mkv", "s": 1, "l": "https://alldebrid.com/f/X"}]}]}
        elif p.endswith("/link/unlock"): d = {"link": "https://cdn.example/a.mkv", "filename": "a.mkv", "filesize": 1}
        return ok({"status": "success", "data": d})
    if pid == "torbox":
        d = {}
        if p.endswith("/user/me"): d = {"email": "rick@citadel", "plan": 2}
        elif "createtorrent" in p: d = {"torrent_id": 5}
        elif "mylist" in p: d = {"download_finished": True, "download_present": True, "name": "Audit",
                                 "files": [{"id": 0, "name": "Audit/a.mkv", "size": 1}]}
        elif "requestdl" in p: d = "https://cdn.example/a.mkv"
        elif "createwebdownload" in p: d = {"webdownload_id": 9}
        return ok({"success": True, "data": d})
    if pid == "premiumize":
        if p.endswith("/account/info"): return ok({"status": "success", "customer_id": 1, "premium_until": 1800000000})
        if p.endswith("/transfer/create"): return ok({"status": "success", "id": "PM1"})
        if p.endswith("/transfer/list"): return ok({"status": "success", "transfers": [{"id": "PM1", "status": "finished", "file_id": "F1", "name": "Audit"}]})
        if p.endswith("/item/details"): return ok({"status": "success", "name": "a.mkv", "size": 1, "link": "https://cdn.example/a.mkv"})
        if p.endswith("/transfer/directdl"): return ok({"status": "success", "content": [{"path": "a.mkv", "size": 1, "link": "https://cdn.example/a.mkv"}]})
        return ok({"status": "success"})
    return httpx.Response(404)


def where(key: str, req: httpx.Request) -> list[str]:
    spots = []
    if key in str(req.url): spots.append("URL")
    for h, v in req.headers.items():
        if key in v: spots.append(f"header:{h}")
    if key.encode() in (req.content or b""): spots.append("body")
    return spots


async def audit_provider(pid: str, prov) -> list[str]:
    sent: list[httpx.Request] = []
    key = KEYS[pid]

    def handler(req: httpx.Request):
        req.read()
        sent.append(req)
        return fake_api(pid, req)

    prov.client._transport = httpx.MockTransport(handler)
    problems: list[str] = []

    await prov.account()
    rid = await prov.add_magnet(MAGNET)
    await prov.add_torrent(TORRENT, "audit.torrent")
    st = await prov.status(rid)
    for it in st.items:
        await prov.resolve(it.handle)
    if pid != "torbox":  # torbox unrestrict polls; covered by resolve()
        await prov.unrestrict("https://rapidgator.net/file/abc")
    await prov.delete(rid)

    print(f"\n== {prov.name}: {len(sent)} requests")
    for req in sent:
        spots = where(key, req)
        tag = []
        if req.url.scheme != "https": problems.append(f"non-HTTPS request {req.url}")
        if req.url.host not in prov.api_hosts: problems.append(f"request to foreign host {req.url.host}")
        if spots != ["header:authorization"]:
            if "URL" in spots and (pid, req.url.path) in ALLOWED_KEY_IN_URL:
                tag.append("key in query (TorBox API requirement, HTTPS-encrypted)")
            else:
                problems.append(f"key found in {spots} for {req.method} {req.url.path}")
        if MAGNET in str(req.url) or "0123456789abcdef0123456789abcdef01234567" in str(req.url):
            problems.append(f"magnet in URL (would land in server logs) {req.url.path}")
        if b"pieces20:" in (req.content or b"") :
            tag.append("torrent uploaded in body")
        if b"btih" in (req.content or b""):
            tag.append("magnet in POST body")
        print(f"  {req.method:6} https://{req.url.host}{req.url.path}  key->{','.join(spots) or 'none'}"
              + (f"  [{'; '.join(tag)}]" if tag else ""))
    return problems


async def audit_guards(prov) -> list[str]:
    problems = []
    prov.client._transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    # 1) plain http must be refused before it is sent
    try:
        await prov.client.get(f"http://{next(iter(prov.api_hosts))}/x", headers={"Authorization": "Bearer k"})
        problems.append("plain-HTTP request was allowed")
    except ProviderError:
        pass
    # 2) other hosts must be refused
    try:
        await prov.client.get("https://evil.example/steal", headers={"Authorization": "Bearer k"})
        problems.append("request to evil.example was allowed")
    except ProviderError:
        pass
    # 3) a redirect must not be followed (key would travel with it)
    hits = []

    def redir(req):
        hits.append(str(req.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    prov.client._transport = httpx.MockTransport(redir)
    try:
        await prov.account()
    except ProviderError:
        pass
    if any("evil.example" in h for h in hits):
        problems.append("followed a redirect off the API host")
    return problems


def audit_logs() -> list[str]:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(vault.ScrubFilter())
    lg = logging.getLogger("audit")
    lg.addHandler(h)
    lg.propagate = False
    lg.warning("provider said: bad key %s", KEYS["realdebrid"])
    lg.warning(f"url https://api.torbox.app/x?token={KEYS['torbox']}")
    out = buf.getvalue()
    return [f"key leaked into log: {out!r}"] if any(k in out for k in KEYS.values()) else []


async def main():
    problems = []
    for pid, prov in ALL.items():
        problems += [f"{prov.name}: {p}" for p in await audit_provider(pid, prov)]
        problems += [f"{prov.name}: {p}" for p in await audit_guards(prov)]
    problems += audit_logs()
    print("\n== Guards: plain-HTTP refused, foreign hosts refused, redirects not followed")
    print("== Logs: keys scrubbed")
    print("\nRESULT:", "PASS - no problems found" if not problems else "FAIL")
    for p in problems:
        print("  !!", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
