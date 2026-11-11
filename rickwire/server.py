"""Runs the FastAPI app on two listeners in a background thread:
  * 127.0.0.1:<local_port>  plain HTTP, desktop window only
  * 0.0.0.0:<lan_port>      HTTPS (local CA) for the phone PWA, private IPs only
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import uvicorn

from .app import Hooks, create_app
from .config import Settings
from .manager import JobManager
from .security import Auth
from .tools import ToolError, cleanup_old

log = logging.getLogger("rickwire.server")


class Backend:
    def __init__(self, settings: Settings, hooks: Hooks):
        self.settings = settings
        self.hooks = hooks
        self.auth = Auth(settings)
        self.manager = JobManager(settings)
        self.app = create_app(settings, self.manager, self.auth, hooks)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.servers: list[uvicorn.Server] = []
        self.ready = threading.Event()
        self.error: str | None = None

    def start(self, timeout: float = 20) -> bool:
        def run():
            try:
                asyncio.run(self._main())
            except BaseException as e:
                log.exception("backend crashed")
                if not self.ready.is_set():
                    self.error = f"RickWire's server couldn't start: {e.__class__.__name__}: {e}"
                    self.ready.set()
        threading.Thread(target=run, name="rickwire-backend", daemon=True).start()
        self.ready.wait(timeout)
        if not self.ready.is_set():
            self.error = "RickWire's server took too long to start (see rickwire.log)"
        return self.ready.is_set() and not self.error

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        await self.manager.start()
        s = self.settings
        configs = [uvicorn.Config(self.app, host="127.0.0.1", port=s["local_port"], log_config=None,
                                  log_level="warning", proxy_headers=False, server_header=False)]
        if s["lan_enabled"]:
            ssl = {}
            if s["lan_https"]:
                from .tls import ensure_server_cert
                cert, key = ensure_server_cert()
                ssl = {"ssl_certfile": cert, "ssl_keyfile": key}
            configs.append(uvicorn.Config(self.app, host="0.0.0.0", port=s["lan_port"], log_config=None,
                                          log_level="warning", proxy_headers=False, server_header=False, **ssl))
        self.servers = [uvicorn.Server(c) for c in configs]
        async def serve(srv: uvicorn.Server):
            try:
                await srv.serve()
            except SystemExit:   # uvicorn calls sys.exit() when it can't bind - don't let it kill the loop
                log.error("listener on port %s failed to start", srv.config.port)
        tasks = [asyncio.create_task(serve(srv)) for srv in self.servers]
        for _ in range(200):
            if all(srv.started for srv in self.servers) or any(t.done() for t in tasks):
                break
            await asyncio.sleep(0.05)
        if not self.servers[0].started:
            self.error = (f"Could not listen on 127.0.0.1:{s['local_port']} - another program (or another copy "
                          "of RickWire) is using that port. Change 'local_port' in config.json or close the other program.")
        elif len(self.servers) > 1 and not self.servers[1].started:
            log.error("LAN listener failed on port %s", s["lan_port"])
        self.ready.set()
        if not self.error:
            asyncio.create_task(self._tool_updates())
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _tool_updates(self):
        """Once a day: check yt-dlp / ffmpeg / Deno. yt-dlp updates itself (sites change, it breaks);
        the big ones only get a notification."""
        s, tm = self.settings, self.app.state.tools
        await asyncio.to_thread(cleanup_old)
        await asyncio.sleep(30)
        while True:
            if s["check_tool_updates"] and time.time() - (s["tools_checked"] or 0) > 86400:
                try:
                    st = await tm.check(force=True)
                    s.update({"tools_checked": int(time.time())})
                    by = {t["name"]: t for t in st["tools"]}
                    yt = by["yt-dlp"]
                    if yt["update"] and s["auto_update_ytdlp"]:
                        try:
                            await tm.install("yt-dlp")
                            self.hooks.notify("yt-dlp updated", f"Version {yt['latest']} is ready - restart RickWire "
                                                                 "(tray > Quit, then reopen) to start using it.")
                        except ToolError as e:
                            log.warning("yt-dlp auto-update failed: %s", e)
                    others = [t["label"] for t in st["tools"] if t["update"] and t["name"] != "yt-dlp" and not t["missing"]]
                    if others:
                        self.hooks.notify("Updates available", f"{', '.join(others)} - see Settings > Media tools.")
                except Exception:
                    log.exception("tool update check failed")
            await asyncio.sleep(3600)

    def stop(self):
        """Safe to call more than once (tray Quit, then the window closing)."""
        for srv in self.servers:
            srv.should_exit = True
        if self.loop and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.manager.flush)
            except RuntimeError:   # loop closed between the check and the call
                pass
