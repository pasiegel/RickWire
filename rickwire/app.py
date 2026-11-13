from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import logging
import mimetypes
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, associate, vault
from .config import DEFAULT_DOWNLOAD_DIR, FROZEN, IS_MAC, IS_WIN, PORTABLE, PUBLIC_SETTING_KEYS, WEB_DIR, Settings
from .discover import Discover, DiscoverError
from .manager import JobManager
from .providers import ALL as PROVIDERS, ProviderError
from .security import COOKIE, Auth, Guard, client_ip, is_loopback, is_remote, local_ips, public_url, require
from .webauth import LoginError
from .sources import MODES, gofile, ytdl
from .tools import TOOLS, ToolError, ToolManager

log = logging.getLogger("rickwire.app")
MAX_TORRENT = 10 * 1024 * 1024
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


class Hooks:
    """Filled in by the desktop shell; no-ops when running headless."""
    focus: Callable[[], None] = staticmethod(lambda: None)
    pick_folder: Callable[[str], str | None] = staticmethod(lambda start: None)
    pick_send: Callable[[bool, str], str | None] = staticmethod(lambda folder, start: None)
    notify: Callable[[str, str], None] = staticmethod(lambda title, msg: None)
    restart: Callable[[], bool] = staticmethod(lambda: False)
    quit: Callable[[], None] = staticmethod(lambda: None)


class AddReq(BaseModel):
    text: str
    mode: str = "auto"
    provider: str | None = None
    audio_only: bool = False
    sort: bool = False


class SendReq(BaseModel):
    path: str = ""
    job: int | None = None


class LoginReq(BaseModel):
    username: str
    password: str
    code: str = ""


class AccountReq(BaseModel):
    username: str
    password: str


class CodeReq(BaseModel):
    code: str


class FilePick(BaseModel):
    names: list[str]


class CacheReq(BaseModel):
    magnets: list[str]
    provider: str | None = None


class ProviderPick(BaseModel):
    provider: str
    remember: bool = False


class KeyReq(BaseModel):
    key: str


class PairReq(BaseModel):
    code: str
    name: str = "Phone"
    key: str = ""


class LocalAdd(BaseModel):
    args: list[str]


SETTING_TYPES = {
    "download_dir": str, "max_concurrent": int, "ask_provider": bool, "default_provider": (str, type(None)),
    "providers_enabled": dict, "hoster_mode": str, "cleanup_remote": bool, "ytdlp_format": str,
    "ytdlp_audio_format": str, "ytdlp_subs": bool, "ytdlp_sub_langs": str, "ytdlp_auto_subs": bool,
    "ffmpeg_path": str, "lan_enabled": bool, "lan_https": bool, "lan_port": int,
    "allowed_cidrs": list, "close_to_tray": bool, "start_minimized": bool,
    "search_url": str, "yts_api": str, "eztv_api": str,
    "onboarded": bool, "auto_update_ytdlp": bool, "check_tool_updates": bool,
    "pick_files": bool, "auto_retry": int, "spin_portal": bool,
    "remote_enabled": bool, "trusted_proxies": list, "public_hosts": list,
    "wormhole_relay": str, "wormhole_transit": str, "wormhole_max_gb": int,
}
RESTART_KEYS = {"lan_enabled", "lan_https", "lan_port"}


def _win_select(target: Path) -> bool:
    """Open the file's folder in Explorer with the file selected (SHOpenFolderAndSelectItems)."""
    import ctypes
    from ctypes import wintypes

    def worker(ok: list):
        shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
        ole32.CoInitialize(None)
        shell32.ILCreateFromPathW.restype = ctypes.c_void_p
        shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
        shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.DWORD]
        shell32.ILFree.argtypes = [ctypes.c_void_p]
        pidl = shell32.ILCreateFromPathW(str(target.resolve()))
        if pidl:
            ok.append(shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) == 0)
            shell32.ILFree(pidl)
        ole32.CoUninitialize()

    import threading
    ok: list = []
    t = threading.Thread(target=worker, args=(ok,), daemon=True)   # own COM apartment, off the event loop
    t.start()
    t.join(10)
    return bool(ok and ok[0])


def open_in_file_manager(target: Path) -> None:
    """Show a file (selected) or folder in Explorer / Finder / the Linux file manager."""
    if IS_WIN:
        # `explorer /select,<path>` re-parses its command line itself: a comma in the file name ends the path
        # and Explorer falls back to Documents. The shell API takes the path as data, so any name works.
        if target.is_file() and _win_select(target):
            return
        os.startfile(target.parent if target.is_file() else target)
        return
    if IS_MAC:
        from .macos import reveal
        reveal(target)
        return
    folder = target.parent if target.is_file() else target
    if target.is_file():
        try:   # most Linux file managers can highlight the file over D-Bus
            r = subprocess.run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                                "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                                f"array:string:{target.as_uri()}", "string:"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            if r.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def lan_url(settings: Settings) -> str | None:
    if not settings["lan_enabled"]:
        return None
    ips = local_ips()
    scheme = "https" if settings["lan_https"] else "http"
    return f"{scheme}://{ips[0] if ips else '127.0.0.1'}:{settings['lan_port']}"


def phone_url(settings: Settings) -> str | None:
    """Where phones should go: the public address when remote access is on, else the LAN address."""
    return public_url(settings) or lan_url(settings)


def create_app(settings: Settings, manager: JobManager, auth: Auth, hooks: Hooks) -> FastAPI:
    app = FastAPI(title="RickWire", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, auth=auth, settings=settings)

    def view(job: dict, admin: bool = True) -> dict:
        j = {k: v for k, v in job.items() if k not in ("torrent_file", "outputs")}
        if j["kind"] == "torrent" and j["source"].startswith("magnet:"):
            j["source"] = j["source"][:80] + "..."
        if j["kind"] == "wormhole_send" and not admin:   # a send's code and local path stay on the desktop
            j["source"], j["wormhole_code"] = j["name"], None
        j["files"] = j["files"][:200]
        return j

    # ------------------------------------------------------------- auth
    @app.get("/auth/bootstrap")
    async def bootstrap(request: Request, t: str = ""):
        if not is_loopback(request):
            raise HTTPException(403)
        session = auth.redeem_bootstrap(t)
        if not session:
            return RedirectResponse("/", 303)
        resp = RedirectResponse("/", 303)
        resp.set_cookie(COOKIE, session, httponly=True, samesite="strict", path="/")
        return resp

    @app.get("/api/me")
    async def me(request: Request):
        s = request.state.session
        return {"authenticated": bool(s), "admin": bool(s and s.admin), "name": s.name if s else None,
                "version": __version__, "lan_url": lan_url(settings) if s and s.admin else None,
                "platform": sys.platform if s and s.admin else None,
                "secure": request.url.scheme == "https" or is_remote(request),
                "login": bool(s and s.login), "remote": is_remote(request)}

    @app.post("/api/pair/start")
    async def pair_start(request: Request):
        require(request, admin=True)
        url = phone_url(settings)
        if not url:
            raise HTTPException(400, "Enable LAN access first")
        code, expires = auth.start_pairing()
        remote = bool(public_url(settings))
        link = f"{url}/pair.html#{code}" + (f".{auth.pair_key}" if remote else "")
        import qrcode
        import qrcode.image.svg
        buf = io.BytesIO()
        qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, box_size=12).save(buf)
        return {"code": code, "expires": expires, "url": link, "lan_url": url, "remote": remote,
                "qr": "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()}

    @app.post("/api/pair/complete")
    async def pair_complete(body: PairReq, request: Request):
        remote = is_remote(request)
        token = auth.complete_pairing(body.code, body.name, remote=remote, key=body.key)
        log.info("phone paired%s: %s", f" through the proxy from {client_ip(request)}" if remote else "", body.name[:40])
        if remote:
            hooks.notify("Phone paired", f"{body.name.strip()[:40] or 'A phone'} paired from {client_ip(request)}")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax",
                        secure=remote or request.url.scheme == "https", path="/")
        return resp

    # ------------------------------------------------------------- web login (+ 2FA)
    web = auth.web

    @app.get("/api/auth/status")
    async def auth_status(request: Request):
        st = web.status()
        return {"login": st["enabled"], "totp": st["totp"], "remote": is_remote(request),
                "pairing": not is_remote(request)}

    @app.post("/api/auth/login")
    async def login(body: LoginReq, request: Request):
        ip = client_ip(request)
        try:
            token, _ = await asyncio.to_thread(web.login, body.username, body.password, body.code, ip,
                                               request.headers.get("user-agent", ""))
        except LoginError as e:
            if not (e.need_code and not body.code):
                log.warning("web sign-in failed from %s%s: %s", ip, " (remote)" if is_remote(request) else "", e)
            if "paused" in str(e) and not getattr(web, "_told", False):
                web._told = True
                hooks.notify("Sign-ins paused", "Lots of failed RickWire sign-ins - someone may be guessing passwords.")
            if not (e.need_code and not body.code):   # just asking for the 2FA code isn't a failure
                await asyncio.sleep(1)
            return JSONResponse({"detail": str(e), "need_code": e.need_code}, 401 if e.need_code else 403)
        web._told = False
        log.info("web sign-in from %s%s", ip, " (remote)" if is_remote(request) else "")
        if is_remote(request):
            hooks.notify("Remote sign-in", f"{body.username.strip()[:40]} signed in from {ip}")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax",
                        secure=is_remote(request) or request.url.scheme == "https", path="/")
        return resp

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        web.logout(request.cookies.get(COOKIE, ""))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/auth")
    async def auth_info(request: Request):
        require(request, admin=True)
        return web.status() | {"list": web.sessions(), "keyring": bool(vault.BACKEND)}

    @app.put("/api/auth/account")
    async def set_account(body: AccountReq, request: Request):
        require(request, admin=True)
        try:
            await asyncio.to_thread(web.set_account, body.username, body.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return web.status()

    @app.delete("/api/auth/account")
    async def remove_account(request: Request):
        require(request, admin=True)
        web.remove_account()
        return web.status()

    @app.post("/api/auth/totp/start")
    async def totp_start(request: Request):
        require(request, admin=True)
        if not web.enabled():
            raise HTTPException(400, "Set a username and password first")
        if not vault.BACKEND:
            raise HTTPException(503, vault.NO_KEYRING)
        from .webauth import otpauth_uri
        import qrcode
        import qrcode.image.svg
        secret = web.totp_begin()
        uri = otpauth_uri(secret, web.data.get("user", "rickwire"), socket.gethostname())
        buf = io.BytesIO()
        qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10).save(buf)
        return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
                "qr": "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()}

    @app.post("/api/auth/totp/confirm")
    async def totp_confirm(body: CodeReq, request: Request):
        require(request, admin=True)
        try:
            web.totp_confirm(body.code)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except vault.VaultUnavailable as e:
            raise HTTPException(503, str(e))
        return web.status()

    @app.delete("/api/auth/totp")
    async def totp_off(request: Request):
        require(request, admin=True)
        web.totp_disable()
        return web.status()

    @app.delete("/api/auth/sessions")
    async def sessions_revoke(request: Request, sid: str = ""):
        require(request, admin=True)
        web.revoke(sid or None)
        return {"list": web.sessions()}

    @app.get("/api/devices")
    async def devices(request: Request):
        require(request, admin=True)
        return [{k: d[k] for k in ("id", "name", "created", "last_seen")} | {"remote": bool(d.get("remote"))}
                for d in settings["devices"]]

    @app.delete("/api/devices/{device_id}")
    async def revoke(device_id: str, request: Request):
        require(request, admin=True)
        auth.revoke(device_id)
        return {"ok": True}

    @app.get("/rickwire-ca.crt")
    async def ca_cert():
        from .tls import CA_CERT
        if not CA_CERT.exists():
            raise HTTPException(404)
        return FileResponse(CA_CERT, media_type="application/x-x509-ca-cert", filename="rickwire-ca.crt")

    # ------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    async def jobs(request: Request):
        sess = require(request)
        return {"jobs": [view(j, sess.admin) for j in manager.list()], "ts": time.time()}

    @app.post("/api/jobs")
    async def add(body: AddReq, request: Request):
        require(request)
        if body.mode not in MODES:
            raise HTTPException(400, "Bad mode")
        jobs = await manager.add_text(body.text, body.mode, body.provider or None, body.audio_only, body.sort)
        if not jobs:
            raise HTTPException(400, "No links found in that, Morty")
        return [view(j) for j in jobs]

    @app.post("/api/jobs/torrent")
    async def add_torrent(request: Request, files: list[UploadFile] = File(...), provider: str = Form(""),
                          sort: bool = Form(False)):
        require(request)
        out = []
        for f in files:
            data = await f.read(MAX_TORRENT + 1)
            if len(data) > MAX_TORRENT or not data.startswith(b"d"):
                raise HTTPException(400, f"{f.filename} is not a torrent file")
            out.append(view(await manager.add("", provider=provider or None, torrent_bytes=data,
                                              torrent_filename=f.filename, sort=sort)))
        return out

    def _job(jid: int) -> dict:
        if jid not in manager.jobs:
            raise HTTPException(404, "No such job")
        return manager.jobs[jid]

    @app.post("/api/jobs/{jid}/pause")
    async def pause(jid: int, request: Request):
        require(request); _job(jid)
        await manager.pause(jid)
        return {"ok": True}

    @app.post("/api/jobs/{jid}/resume")
    async def resume(jid: int, request: Request):
        require(request); _job(jid)
        manager.resume(jid)
        return {"ok": True}

    @app.post("/api/jobs/{jid}/provider")
    async def pick(jid: int, body: ProviderPick, request: Request):
        require(request); _job(jid)
        if body.provider not in manager.configured_providers():
            raise HTTPException(400, "That provider isn't configured")
        manager.choose_provider(jid, body.provider, body.remember)
        return {"ok": True}

    @app.post("/api/jobs/{jid}/files")
    async def pick_files(jid: int, body: FilePick, request: Request):
        require(request); _job(jid)
        try:
            manager.pick_files(jid, body.names)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.post("/api/jobs/retry")
    async def retry_failed(request: Request):
        require(request)
        return {"retried": manager.retry_failed()}

    @app.get("/api/jobs/{jid}/sort")
    async def sort_preview(jid: int, request: Request):
        require(request, admin=True)
        job = _job(jid)
        root = Path(settings["download_dir"]).resolve()

        def rel(p: Path) -> str:
            try:
                return str(p.resolve().relative_to(root))
            except ValueError:
                return str(p)
        moves = await asyncio.to_thread(manager.sort_plan, job)
        return {"moves": [{"from": rel(a), "to": rel(b)} for a, b in moves]}

    @app.post("/api/jobs/{jid}/sort")
    async def sort_apply(jid: int, request: Request):
        require(request, admin=True)
        job = _job(jid)
        if job["state"] != "done":
            raise HTTPException(400, "Only finished downloads can be sorted")
        try:
            note = await asyncio.to_thread(manager.sort_job, job)
        except OSError as e:
            raise HTTPException(500, f"Couldn't move the files: {e}")
        job["message"] = note
        manager._write(job)
        return {"ok": True, "message": note}

    @app.post("/api/cache")
    async def cache_check(body: CacheReq, request: Request):
        require(request)
        usable = manager.configured_providers()
        pids = [body.provider] if body.provider in usable else usable
        if not pids:
            raise HTTPException(400, "No debrid provider configured")
        return {"results": await manager.check_cache(pids, body.magnets[:25])}

    @app.delete("/api/jobs/{jid}")
    async def remove(jid: int, request: Request, delete_files: bool = False):
        sess = require(request); _job(jid)
        await manager.remove(jid, delete_files and sess.admin)
        return {"ok": True}

    @app.post("/api/jobs/clear")
    async def clear(request: Request):
        require(request)
        await manager.clear_finished()
        return {"ok": True}

    @app.post("/api/jobs/{jid}/open")
    async def open_folder(jid: int, request: Request):
        require(request, admin=True)
        job = _job(jid)
        if job["kind"] == "wormhole_send":   # show what was sent (it was never in the download folder)
            target = Path(job["source"])
        else:
            target = Path(job.get("folder") or (job["outputs"][0] if job.get("outputs") else settings["download_dir"]))
        open_in_file_manager(target if target.exists() else Path(settings["download_dir"]))
        return {"ok": True}

    # ------------------------------------------------------------- magic wormhole
    @app.post("/api/wormhole/send")
    async def wormhole_send(body: SendReq, request: Request):
        require(request, admin=True)   # sends files from this PC: desktop only
        path = body.path.strip()
        if body.job is not None:
            src = _job(body.job)
            if src["state"] != "done" or not (src.get("folder") or src.get("outputs")):
                raise HTTPException(400, "That download has no files to send")
            path = src.get("folder") or (src["outputs"][0] if len(src["outputs"]) == 1 else str(Path(src["outputs"][0]).parent))
        if not path:
            raise HTTPException(400, "Pick a file or folder to send")
        try:
            return view(await manager.add_send(path))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/jobs/{jid}/wormhole")
    async def wormhole_code(jid: int, request: Request):
        require(request, admin=True)
        job = _job(jid)
        code = job.get("wormhole_code")
        if not code:
            raise HTTPException(404, "No code yet")
        import qrcode
        import qrcode.image.svg
        buf = io.BytesIO()
        qrcode.make(f"wormhole-transfer:{code}", image_factory=qrcode.image.svg.SvgPathImage, box_size=10).save(buf)
        return {"code": code, "uri": f"wormhole-transfer:{code}", "command": f"wormhole receive {code}",
                "qr": "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()}

    @app.post("/api/system/pick-send")
    async def pick_send(request: Request, folder: bool = False):
        require(request, admin=True)
        return {"path": await asyncio.to_thread(hooks.pick_send, folder, settings["download_dir"])}

    # ------------------------------------------------------------- providers
    @app.get("/api/providers")
    async def providers(request: Request):
        sess = require(request)
        enabled = settings["providers_enabled"] or {}
        out = []
        for pid, p in PROVIDERS.items():
            item = {"id": pid, "name": p.name, "configured": p.configured(), "enabled": enabled.get(pid, True),
                    "cache_check": p.cache_check}
            if sess.admin:
                item |= {"masked": vault.mask(vault.get(pid)), "key_url": p.key_url, "signup_url": p.signup_url,
                         "dashboard_url": p.dashboard_url, "steps": list(p.key_steps)}
            out.append(item)
        extra = {"gofile": {"configured": gofile.has_token()}}
        if sess.admin:
            extra["gofile"]["masked"] = vault.mask(vault.get(gofile.VAULT_KEY))
        return {"providers": out, "usable": manager.configured_providers(), "extra": extra,
                "ask": settings["ask_provider"], "default": settings["default_provider"]}

    @app.put("/api/providers/{pid}/key")
    async def set_key(pid: str, body: KeyReq, request: Request):
        require(request, admin=True)
        if pid not in PROVIDERS and pid != gofile.VAULT_KEY or len(body.key.strip()) < 8:
            raise HTTPException(400, "Bad provider or key")
        try:
            vault.put(pid, body.key)
        except vault.VaultUnavailable as e:
            raise HTTPException(503, str(e))
        if pid == gofile.VAULT_KEY:
            return {"ok": True, "account": {"user": "Gofile token saved"}}
        try:
            return {"ok": True, "account": await PROVIDERS[pid].account()}
        except (ProviderError, Exception) as e:  # keep the key; let the user see why it failed
            return {"ok": False, "error": vault.scrub(str(e))}

    @app.delete("/api/providers/{pid}/key")
    async def del_key(pid: str, request: Request):
        require(request, admin=True)
        if pid not in PROVIDERS and pid != gofile.VAULT_KEY:
            raise HTTPException(404)
        vault.delete(pid)
        return {"ok": True}

    @app.post("/api/providers/{pid}/test")
    async def test_key(pid: str, request: Request):
        require(request, admin=True)
        if pid not in PROVIDERS:
            raise HTTPException(404)
        try:
            return {"ok": True, "account": await PROVIDERS[pid].account()}
        except Exception as e:
            return {"ok": False, "error": vault.scrub(str(e))}

    # ------------------------------------------------------------- settings / system
    @app.get("/api/settings")
    async def get_settings(request: Request):
        sess = require(request)
        data = {k: settings[k] for k in SETTING_TYPES}
        if not sess.admin:
            data = {k: v for k, v in data.items() if k in PUBLIC_SETTING_KEYS}
        return data

    @app.put("/api/settings")
    async def put_settings(values: dict, request: Request):
        require(request, admin=True)
        clean = {}
        for k, v in values.items():
            t = SETTING_TYPES.get(k)
            if t is None or not isinstance(v, t) or (t is int and isinstance(v, bool)):
                raise HTTPException(400, f"Bad setting {k}")
            clean[k] = v
        if "max_concurrent" in clean:
            clean["max_concurrent"] = max(1, min(10, clean["max_concurrent"]))
            manager.resize(clean["max_concurrent"])
        if "auto_retry" in clean:
            clean["auto_retry"] = max(0, min(10, clean["auto_retry"]))
        if "ytdlp_sub_langs" in clean:
            clean["ytdlp_sub_langs"] = clean["ytdlp_sub_langs"].strip() or "en.*"
            if len(clean["ytdlp_sub_langs"]) > 100 or not re.fullmatch(r"[\w.*,;\- ]+", clean["ytdlp_sub_langs"]):
                raise HTTPException(400, "Subtitle languages: use codes like en, es or en.* separated by commas")
        if "wormhole_max_gb" in clean:
            clean["wormhole_max_gb"] = max(0, min(100000, clean["wormhole_max_gb"]))
        if clean.get("wormhole_relay", "").strip() and not re.fullmatch(r"wss?://[\w.\-]+(:\d+)?(/\S*)?",
                                                                        clean["wormhole_relay"].strip()):
            raise HTTPException(400, "Wormhole relay: use a ws:// or wss:// address, e.g. wss://relay.example.com/v1")
        if clean.get("wormhole_transit", "").strip() and not re.fullmatch(r"tcp:[\w.\-]+:\d+",
                                                                          clean["wormhole_transit"].strip()):
            raise HTTPException(400, "Wormhole transit relay: use tcp:host:port, e.g. tcp:relay.example.com:4001")
        if "hoster_mode" in clean and clean["hoster_mode"] not in ("debrid_first", "native_first"):
            raise HTTPException(400, "Bad hoster_mode")
        if "download_dir" in clean:
            if os.path.normcase(clean["download_dir"]) in ("", os.path.normcase(DEFAULT_DOWNLOAD_DIR)):
                clean["download_dir"] = ""   # keep "this PC's Downloads" so the portable folder works on any machine
            Path(clean["download_dir"] or DEFAULT_DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
        for c in clean.get("trusted_proxies", []):
            try:
                ipaddress.ip_network(str(c).strip(), strict=False)
            except ValueError:
                raise HTTPException(400, f"Trusted proxy '{c}' isn't an IP address or range")
        if "trusted_proxies" in clean:
            clean["trusted_proxies"] = [str(c).strip() for c in clean["trusted_proxies"] if str(c).strip()]
        if "public_hosts" in clean:
            hosts = []
            for h in clean["public_hosts"]:
                h = str(h).strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
                if h and not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:\d{1,5})?", h):
                    raise HTTPException(400, f"Public address '{h}' doesn't look like a host name (e.g. rickwire.example.com)")
                if h:
                    hosts.append(h)
            clean["public_hosts"] = hosts
        if clean.get("remote_enabled"):
            proxies = clean.get("trusted_proxies", settings["trusted_proxies"])
            if not auth.web.enabled():
                raise HTTPException(400, "Set a web username and password before turning on remote access")
            if not proxies:
                raise HTTPException(400, "Add your proxy's address (the PC running Nginx Proxy Manager) first")
            if not clean.get("lan_enabled", settings["lan_enabled"]):
                raise HTTPException(400, "Remote access goes through the LAN port - turn on LAN access too")
        for k in ("search_url", "yts_api", "eztv_api"):
            if k in clean and not clean[k].startswith("https://"):
                raise HTTPException(400, f"{k} must be an https:// URL")
        for c in clean.get("allowed_cidrs", []):
            ipaddress.ip_network(c, strict=False)
        changed = {k for k in RESTART_KEYS & clean.keys() if clean[k] != settings[k]}
        settings.update(clean)
        return {"ok": True, "restart": bool(changed)}

    @app.get("/api/system")
    async def system(request: Request):
        require(request, admin=True)
        from .config import DATA_DIR
        return {"ffmpeg": ytdl.ffmpeg_location(settings["ffmpeg_path"]), "ytdlp": ytdl.version(),
                "js_runtime": ytdl.js_runtime_label(), "frozen": FROZEN, "portable": PORTABLE, "data_dir": str(DATA_DIR),
                "platform": sys.platform, "keyring": vault.BACKEND,
                "associations": associate.status(), "lan_url": lan_url(settings), "ips": local_ips(),
                "public_url": public_url(settings)}

    @app.post("/api/system/associate")
    async def do_associate(request: Request, on: bool = True):
        require(request, admin=True)
        try:
            associate.register() if on else associate.unregister()
        except Exception as e:
            if e.__class__.__name__ != "NotTheApp":   # macOS, run from source: there's no app bundle to register
                raise
            raise HTTPException(400, str(e))
        return associate.status()

    @app.post("/api/system/default-apps")
    async def default_apps(request: Request):
        require(request, admin=True)
        if not IS_WIN:
            raise HTTPException(400, "Not available on this system")
        os.startfile("ms-settings:defaultapps?registeredAppUser=RickWire")
        return {"ok": True}

    @app.post("/api/system/pick-folder")
    async def pick_folder(request: Request):
        require(request, admin=True)
        # in a thread: the dialog stays open as long as the user likes, and the server must keep running meanwhile
        return {"path": await asyncio.to_thread(hooks.pick_folder, settings["download_dir"])}

    # ------------------------------------------------------------- media tools (yt-dlp / ffmpeg / Deno)
    tools = app.state.tools = ToolManager()

    @app.get("/api/tools")
    async def tools_status(request: Request):
        require(request, admin=True)
        return tools.status()

    @app.post("/api/tools/check")
    async def tools_check(request: Request):
        require(request, admin=True)
        st = await tools.check(force=True)
        settings.update({"tools_checked": int(time.time())})
        return st

    @app.post("/api/tools/{name}/install")
    async def tools_install(name: str, request: Request):
        require(request, admin=True)
        if name not in TOOLS:
            raise HTTPException(404)
        if tools._locks[name].locked():
            raise HTTPException(409, "Already downloading")

        async def run():
            try:
                await tools.install(name)
            except ToolError:
                pass   # recorded in tools.progress for the UI

        asyncio.create_task(run())
        await asyncio.sleep(0.05)
        return tools.status()

    @app.post("/api/system/restart")
    async def restart(request: Request):
        require(request, admin=True)
        if not hooks.restart():
            raise HTTPException(400, "Restart isn't available here - close and reopen RickWire")
        return {"ok": True}

    # ------------------------------------------------------------- local handoff (magnet clicks)
    @app.post("/api/local/add")
    async def local_add(body: LocalAdd, request: Request):
        if not auth.check_local(request):
            raise HTTPException(403)
        added = 0
        for arg in body.args:
            p = Path(arg)
            if arg.lower().endswith(".torrent") and p.is_file() and p.stat().st_size <= MAX_TORRENT:
                await manager.add("", torrent_bytes=p.read_bytes(), torrent_filename=p.name)
                added += 1
            elif arg.lower().startswith(("magnet:", "http://", "https://")):
                added += len(await manager.add_text(arg))
        hooks.focus()
        return {"added": added}

    @app.post("/api/local/quit")
    async def local_quit(request: Request):
        if not auth.check_local(request):
            raise HTTPException(403)
        hooks.quit()
        return {"ok": True}

    @app.post("/api/local/focus")
    async def local_focus(request: Request):
        if not auth.check_local(request):
            raise HTTPException(403)
        hooks.focus()
        return {"ok": True}

    # ------------------------------------------------------------- search & news
    disc = Discover(settings)

    async def _disc(coro):
        try:
            return await coro
        except DiscoverError as e:
            raise HTTPException(502, str(e))

    @app.get("/api/search")
    async def search(request: Request, q: str, sort: str = "relevance", cat: str = "all"):
        require(request)
        return {"results": await _disc(disc.search(q[:200], sort, cat))}

    @app.get("/api/news/yts")
    async def news_yts(request: Request, sort: str = "latest", page: int = 1, quality: str = "all", q: str = ""):
        require(request)
        return await _disc(disc.yts(sort, min(page, 200), quality, q[:100]))

    @app.get("/api/news/eztv")
    async def news_eztv(request: Request, page: int = 1, imdb: str = ""):
        require(request)
        if imdb and not imdb.isdigit():
            raise HTTPException(400, "Bad show id")
        return await _disc(disc.eztv(min(max(page, 1), 500), imdb[:12]))

    @app.get("/api/news/eztv/shows")
    async def eztv_shows(request: Request, q: str = ""):
        require(request)
        return {"shows": await _disc(disc.tv_shows(q[:80]))}

    @app.get("/api/img")
    async def img(request: Request, u: str):
        require(request)
        try:
            ctype, data = await disc.image(u)
        except (DiscoverError, httpx.HTTPError):
            raise HTTPException(404)
        return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/sw.js")
    async def sw():
        return FileResponse(WEB_DIR / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/share")
    async def share():
        return FileResponse(WEB_DIR / "share.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
