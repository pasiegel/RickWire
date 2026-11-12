"""Desktop shell: single instance, pywebview window (Edge WebView2 on Windows, WKWebView on macOS, WebKitGTK or Qt
on Linux), tray / menu-bar icon, magnet handoff."""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler

import httpx

from . import APP_NAME, vault
from .app import Hooks
from .config import LOCAL_TOKEN_FILE, LOG_FILE, Settings

log = logging.getLogger("rickwire")
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def setup_logging():
    if sys.stdout is None:  # pythonw
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(vault.ScrubFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs at INFO
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # native crashes (pythonnet / WebView2) never reach Python logging - dump them next to the log instead
    try:
        import faulthandler
        global _crash_fh
        _crash_fh = open(LOG_FILE.with_name("crash.log"), "a", encoding="utf-8")
        faulthandler.enable(_crash_fh)
    except Exception:
        pass
    threading.excepthook = lambda a: log.error("thread %s crashed", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def handoff(settings: Settings, items: list[str]) -> bool:
    """If RickWire is already running, pass the magnet/torrent to it and return True."""
    try:
        token = LOCAL_TOKEN_FILE.read_text("utf-8").strip()
    except OSError:
        return False
    base = f"http://127.0.0.1:{settings['local_port']}"
    try:
        path = "/api/local/add" if items else "/api/local/focus"
        r = httpx.post(base + path, json={"args": items}, headers={"X-RickWire-Local": token}, timeout=10)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def linux_gui() -> str:
    """pywebview toolkit on Linux: the system's GTK + WebKit2 when present, otherwise Qt (pip-installable)."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        for v in ("4.1", "4.0"):
            try:
                gi.require_version("WebKit2", v)
                return "gtk"
            except ValueError:
                continue
    except (ImportError, ValueError):
        pass
    return "qt"


def bring_to_front(window) -> None:
    """Show, un-minimise and raise the window. Safe from any thread.

    Not via pywebview's `window.on_top`: on Windows that sets the form's TopMost straight from the calling thread
    while holding the GIL. The window thread then needs the GIL to handle the window messages that causes (its
    500 ms timer and event handlers are Python), so both threads wait for each other forever - RickWire froze,
    server included. Invoke() runs it on the window thread and lets go of the GIL while it waits."""
    window.show()
    window.restore()
    if IS_MAC:
        return   # pywebview's Cocoa show() already runs on the main thread and activates the app
    if not IS_WIN:
        window.on_top = True
        window.on_top = False
        return
    from System import Func, Type
    from webview.platforms.winforms import BrowserView
    form = BrowserView.instances.get(window.uid)
    if form is None:
        return

    def _raise():
        form.TopMost = True
        form.TopMost = False
        form.Activate()

    if form.InvokeRequired:
        form.Invoke(Func[Type](_raise))
    else:
        _raise()


def make_tray(on_open, on_quit, gui: str | None = None):
    """-> (icon or None, alive()). The tray is optional on Linux (GNOME hides it without an extension)."""
    if not IS_WIN and not IS_MAC and gui != "gtk":
        os.environ.setdefault("PYSTRAY_BACKEND", "xorg")   # no GTK: plain X11 tray (also works under XWayland)
    state = {"ok": True}
    try:
        import pystray
        from .icons import portal
        menu = pystray.Menu(pystray.MenuItem("Open RickWire", lambda: on_open(), default=True),
                            pystray.MenuItem("Quit", lambda: on_quit()))
        icon = pystray.Icon(APP_NAME, portal(64), "RickWire", menu)
        if IS_MAC:
            # AppKit: the menu-bar item lives on the main thread and pywebview's NSApp loop runs it. pystray's default
            # setup would show the icon from a worker thread, so it's shown here instead (we are on the main thread).
            icon.run_detached(setup=lambda _icon: None)
            icon.visible = True
            return icon, lambda: True
        if not IS_WIN and gui == "gtk":
            icon.run_detached()   # share pywebview's GTK main loop instead of starting a second one
            return icon, lambda: state["ok"]
    except Exception:
        log.exception("tray icon unavailable")
        return None, lambda: False

    def run():
        try:
            icon.run()
        except Exception:
            state["ok"] = False
            log.exception("tray icon failed")
    threading.Thread(target=run, name="tray", daemon=True).start()
    return icon, lambda: state["ok"]


def main(argv: list[str] | None = None):
    try:
        _main(argv)
    except SystemExit:
        raise
    except BaseException as e:
        log.exception("startup failed")
        _fatal(f"RickWire hit an error and has to close:\n\n{e.__class__.__name__}: {str(e)[:400]}\n\n"
               f"Details: {LOG_FILE}")


def _main(argv: list[str] | None = None):
    p = argparse.ArgumentParser(prog="rickwire")
    p.add_argument("--add", action="append", default=[], help="magnet link, URL or .torrent path")
    p.add_argument("--headless", action="store_true", help="server + tray only, no window")
    p.add_argument("--minimized", action="store_true")
    p.add_argument("--register", action="store_true", help="register as the magnet/.torrent handler and exit")
    p.add_argument("--unregister", action="store_true", help="remove the magnet/.torrent registration and exit")
    p.add_argument("--quit", action="store_true", help="ask a running RickWire to quit (used by the installer)")
    p.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)   # used by restart
    p.add_argument("items", nargs="*")
    args = p.parse_args(argv)
    items = [i for i in args.add + args.items if i]

    if args.register or args.unregister:   # called by the installer / uninstaller
        from . import associate
        associate.register() if args.register else associate.unregister()
        return
    if args.quit:
        _quit_running()
        return
    if args.wait_pid:
        _wait_for_exit(args.wait_pid)

    setup_logging()
    from . import __version__
    from .config import DATA_DIR
    log.info("RickWire %s starting (exe=%s, data=%s)", __version__, sys.executable, DATA_DIR)
    settings = Settings()
    if handoff(settings, items):
        log.info("another instance is running - handed off and exiting")
        return
    _app_mutex()

    from .icons import ensure_icons
    from .server import Backend
    ensure_icons()

    hooks = Hooks()
    backend = Backend(settings, hooks)
    if not backend.start():
        _fatal(backend.error or "Backend failed to start")
        return
    from .sources import ytdl
    log.info("backend listening; %s", ytdl.LOAD_NOTE)
    def add_items(new: list[str]):
        for it in new:
            if it.lower().endswith(".torrent") and os.path.isfile(it):
                with open(it, "rb") as fh:
                    backend.submit(backend.manager.add("", torrent_bytes=fh.read(), torrent_filename=os.path.basename(it)))
            else:
                backend.submit(backend.manager.add_text(it))
    add_items(items)

    import webview

    gui = None if IS_WIN or IS_MAC else linux_gui()
    if gui:
        log.info("window toolkit: %s", gui)
    state = {"quitting": False}
    url = backend.auth.new_bootstrap_url(settings["local_port"])
    window = None if args.headless else webview.create_window(
        "RickWire", url, width=1200, height=820, min_size=(420, 560), background_color="#0b0f1a",
        hidden=args.minimized or settings["start_minimized"], text_select=True)

    focus_lock = threading.Lock()

    def _focus_now():
        if not focus_lock.acquire(blocking=False):
            return   # one already on its way
        try:
            bring_to_front(window)
        except Exception:
            log.exception("couldn't bring the window to the front")
        finally:
            focus_lock.release()

    def focus():
        # Called from the server's event loop and the tray thread: never block those on the window.
        if window:
            threading.Thread(target=_focus_now, name="focus", daemon=True).start()

    def quit_app():
        state["quitting"] = True
        log.info("quitting")
        backend.stop()
        if tray and not IS_MAC:   # macOS: stopping pystray stops the shared NSApp loop; the process exits anyway
            tray.stop()
        # never leave a ghost process behind (it would block restarts and the installer)
        watchdog = threading.Timer(6, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()
        if window:
            window.destroy()
        else:
            os._exit(0)

    def restart() -> bool:
        import subprocess
        cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(sys.argv[0])]
        subprocess.Popen(cmd + ["--wait-pid", str(os.getpid())], close_fds=True)
        threading.Timer(0.3, quit_app).start()   # let the HTTP response go out first
        return True

    def notify(title: str, msg: str):
        if IS_MAC:
            from .macos import notify as mac_notify
            mac_notify(title, msg)
            return
        if tray is not None and getattr(tray, "HAS_NOTIFICATION", False):
            try:
                tray.notify(msg, title)
                return
            except Exception:
                pass
        if not IS_WIN and shutil.which("notify-send"):
            from .associate import icon_path
            subprocess.Popen(["notify-send", "-a", "RickWire", "-i", icon_path() or "dialog-information", title, msg],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def pick_folder(start: str):
        if not window:
            return None
        res = window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return res[0] if res else None

    def on_job(job: dict, event: str):
        if event == "done":
            notify("Wubba lubba dub dub!", job["name"][:120])
        elif event == "error":
            notify("Aw jeez, Rick...", f"{job['name'][:80]}\n{job['message'][:120]}")
        elif event == "added" and job["state"] == "awaiting_provider":
            focus()
        elif event == "files":
            notify("Pick your files, Morty", f"{job['name'][:100]} has several files - choose which to download")

    hooks.focus, hooks.notify, hooks.pick_folder, hooks.restart = focus, notify, pick_folder, restart
    hooks.quit = lambda: threading.Timer(0.3, quit_app).start()
    backend.manager.listeners.append(on_job)
    tray, tray_alive = make_tray(focus, quit_app, gui)
    log.info("tray %s", "started" if tray else "unavailable")

    if window is None:
        threading.Event().wait()
        return

    def on_closing():
        # without a working tray icon there'd be no way back to a hidden window, so closing quits
        if settings["close_to_tray"] and tray is not None and tray_alive() and not state["quitting"]:
            window.hide()
            notify("Still running", "RickWire is in the menu bar. Click the portal there to quit." if IS_MAC else
                   "RickWire is in the tray. Right-click the portal to quit.")
            return False
        return True

    def on_shown():
        log.info("window shown")
        focus()   # the first-run firewall prompt steals focus; bring the window to the front
        if settings["close_to_tray"] and tray is not None:
            notify("RickWire is running", "Closing the window keeps it in the menu bar (the portal icon)." if IS_MAC
                   else "Closing the window keeps it in the tray" + (" (click ^ by the clock)." if IS_WIN else "."))

    window.events.closing += on_closing
    window.events.shown += on_shown
    if IS_MAC:
        from .macos import install_app_hooks

        def opened(new: list[str]):
            add_items(new)
            focus()
        try:
            install_app_hooks(opened, focus, lambda: threading.Timer(0.1, quit_app).start())
        except Exception:
            log.exception("couldn't hook up macOS open/reopen/quit events")
    log.info("opening window")
    try:
        webview.start(private_mode=True, gui=gui)
    except Exception as e:
        log.exception("window failed to start")
        if tray:
            tray.stop()
        backend.stop()
        if IS_MAC:
            hint = "Make sure you're on macOS 12 or newer, then open RickWire again."
        elif IS_WIN:
            hint = ("Make sure RickWire.exe.config sits next to RickWire.exe. If you unzipped a downloaded copy, "
                    "right-click the zip > Properties > Unblock and extract it again. The Microsoft Edge WebView2 "
                    "Runtime must also be installed (it comes with Windows 11).")
        else:
            hint = ("RickWire needs a web view. Install WebKitGTK (Debian/Ubuntu: sudo apt install python3-gi "
                    "gir1.2-webkit2-4.1 / Fedora: sudo dnf install python3-gobject webkit2gtk4.1 / Arch: sudo pacman -S "
                    "python-gobject webkit2gtk-4.1), then start it again with ./run.sh.")
        _fatal(f"RickWire's window couldn't start:\n\n{str(e).splitlines()[0][:300]}\n\n{hint}")
        return
    log.info("window closed - shutting down")
    try:
        backend.stop()
        if tray:
            tray.stop()
    except Exception:   # never show an error box on the way out
        log.exception("error while shutting down")
    import time
    time.sleep(0.8)   # let the job database flush
    logging.shutdown()
    os._exit(0)


def _quit_running():
    try:
        settings = Settings()
        token = LOCAL_TOKEN_FILE.read_text("utf-8").strip()
        httpx.post(f"http://127.0.0.1:{settings['local_port']}/api/local/quit",
                   headers={"X-RickWire-Local": token}, timeout=5)
    except (OSError, httpx.HTTPError):
        return   # not running
    pid_wait_deadline = 10
    import time
    for _ in range(pid_wait_deadline * 4):   # wait until it has let go of its port
        try:
            httpx.get(f"http://127.0.0.1:{settings['local_port']}/api/me", timeout=0.5)
        except httpx.HTTPError:
            time.sleep(1)   # give the process a moment to exit after closing its listener
            return
        time.sleep(0.25)


def _app_mutex():
    """Lets the installer/uninstaller see that RickWire is running (Inno Setup AppMutex)."""
    global _mutex
    if not IS_WIN:
        return
    try:
        import ctypes
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "RickWireAppMutex")
    except Exception:
        pass


def _wait_for_exit(pid: int, timeout: float = 20):
    import time
    if not IS_WIN:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.2)
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
        if h:
            k32.WaitForSingleObject(h, int(timeout * 1000))
            k32.CloseHandle(h)
    except Exception:
        import time
        time.sleep(3)


def _fatal(msg: str):
    log.error(msg)
    try:
        print(msg, file=sys.stderr)
    except Exception:
        pass
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "RickWire", 0x10 | 0x10000 | 0x40000)  # error, foreground, topmost
        except Exception:
            pass
        return
    if IS_MAC:
        from .macos import alert
        alert(msg)
        return
    for cmd in (["zenity", "--error", "--title=RickWire", "--no-wrap", f"--text={msg}"],
                ["kdialog", "--title", "RickWire", "--error", msg],
                ["notify-send", "-u", "critical", "RickWire", msg]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, timeout=600)
                return
            except (OSError, subprocess.SubprocessError):
                continue
