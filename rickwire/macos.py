"""macOS-only glue. Imported only on darwin.

* Opening links/files: macOS doesn't pass a clicked magnet: link or a double-clicked .torrent as a command-line
  argument. It sends the running app an Apple Event, and AppKit hands it to the app delegate's
  `application:openURLs:` - including the one that launches the app. pywebview owns the delegate, so the methods
  are added to its class before the window starts (`install_app_hooks`).
* The Dock icon ("reopen") brings the window back, and Cmd+Q / Quit really quits even though closing the
  window only hides it.
* Default handler: Info.plist (packaging/mac) declares magnet: and .torrent; LaunchServices makes it the default.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

log = logging.getLogger("rickwire.macos")

BUNDLE_ID = "app.rickwire.RickWire"
TORRENT_UTI = "org.bittorrent.torrent"
LS_ROLES_ALL = 0xFFFFFFFF


# ------------------------------------------------------------------------------------- app delegate hooks
def install_app_hooks(on_items, on_reopen, on_quit) -> None:
    """on_items(list[str]) gets magnet links / http links / .torrent paths; on_reopen() for a Dock click;
    on_quit() for Cmd+Q (RickWire then shuts down itself, so AppKit's terminate is cancelled)."""
    import objc
    from webview.platforms.cocoa import BrowserView

    def application_openURLs_(self, app, urls):
        items = []
        for u in urls:
            s = str(u.absoluteString())
            if s.startswith("file:"):
                items.append(unquote(urlparse(s).path))
            else:
                items.append(s)
        log.info("opened by macOS: %d item(s)", len(items))
        try:
            on_items(items)
        except Exception:
            log.exception("couldn't add the opened items")

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        on_reopen()
        return True

    def applicationShouldTerminate_(self, app):
        on_quit()
        return 0   # NSTerminateCancel: RickWire closes its window and exits on its own (flushes the queue first)

    objc.classAddMethods(BrowserView.AppDelegate, [
        objc.selector(application_openURLs_, selector=b"application:openURLs:", signature=b"v@:@@"),
        objc.selector(applicationShouldHandleReopen_hasVisibleWindows_,
                      selector=b"applicationShouldHandleReopen:hasVisibleWindows:", signature=b"Z@:@Z"),
        objc.selector(applicationShouldTerminate_, selector=b"applicationShouldTerminate:", signature=b"I@:@"),
    ])


# ------------------------------------------------------------------------------------- small helpers
def _osa_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, msg: str) -> None:
    try:
        subprocess.Popen(["osascript", "-e", f"display notification {_osa_quote(msg)} with title {_osa_quote(title)}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def alert(msg: str) -> None:
    try:
        subprocess.run(["osascript", "-e", f'display alert "RickWire" message {_osa_quote(msg)} as critical '
                                           'buttons {"OK"} default button "OK"'], timeout=600)
    except (OSError, subprocess.SubprocessError):
        pass


def reveal(target: Path) -> None:
    """Finder: select the file, or open the folder."""
    args = ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ------------------------------------------------------------------------------------- LaunchServices
def bundle_path() -> Path | None:
    """…/RickWire.app when running from the app bundle, else None (source checkout)."""
    if not getattr(sys, "frozen", False):
        return None
    p = Path(sys.executable).resolve()
    for parent in p.parents:
        if parent.suffix == ".app":
            return parent
    return None


class _LS:
    def __init__(self):
        cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        cs = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreServices"))
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cs.LSSetDefaultHandlerForURLScheme.restype = ctypes.c_int32
        cs.LSSetDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cs.LSCopyDefaultHandlerForURLScheme.restype = ctypes.c_void_p
        cs.LSCopyDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p]
        cs.LSSetDefaultRoleHandlerForContentType.restype = ctypes.c_int32
        cs.LSSetDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        cs.LSCopyDefaultRoleHandlerForContentType.restype = ctypes.c_void_p
        cs.LSCopyDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cs.LSRegisterURL.restype = ctypes.c_int32
        cs.LSRegisterURL.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
        self.cf, self.cs = cf, cs

    def s(self, text: str):
        return self.cf.CFStringCreateWithCString(None, text.encode(), 0x08000100)   # UTF-8

    def text(self, ref) -> str:
        if not ref:
            return ""
        buf = ctypes.create_string_buffer(1024)
        ok = self.cf.CFStringGetCString(ref, buf, 1024, 0x08000100)
        self.cf.CFRelease(ref)
        return buf.value.decode() if ok else ""


def _ls() -> _LS | None:
    try:
        return _LS()
    except (OSError, AttributeError, TypeError):
        log.exception("LaunchServices unavailable")
        return None


def status() -> dict:
    ls = _ls()
    if not ls:
        return {"magnet": False, "torrent": False, "magnet_cmd": "", "torrent_progid": ""}
    scheme, uti = ls.s("magnet"), ls.s(TORRENT_UTI)
    magnet = ls.text(ls.cs.LSCopyDefaultHandlerForURLScheme(scheme))
    torrent = ls.text(ls.cs.LSCopyDefaultRoleHandlerForContentType(uti, LS_ROLES_ALL))
    ls.cf.CFRelease(scheme)
    ls.cf.CFRelease(uti)
    return {"magnet": magnet.lower() == BUNDLE_ID.lower(), "torrent": torrent.lower() == BUNDLE_ID.lower(),
            "magnet_cmd": magnet, "torrent_progid": torrent}


class NotTheApp(Exception):
    pass


def register() -> None:
    app = bundle_path()
    if not app:
        raise NotTheApp("Only the RickWire app (from the .dmg) can open magnet links and .torrent files - "
                        "not a copy run from source.")
    ls = _ls()
    if not ls:
        raise NotTheApp("macOS LaunchServices isn't available")
    raw = str(app).encode()
    url = ls.cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), True)
    ls.cs.LSRegisterURL(url, True)   # make sure macOS knows this copy (e.g. still in Downloads) and its Info.plist
    ls.cf.CFRelease(url)
    bid, scheme, uti = ls.s(BUNDLE_ID), ls.s("magnet"), ls.s(TORRENT_UTI)
    r1 = ls.cs.LSSetDefaultHandlerForURLScheme(scheme, bid)
    r2 = ls.cs.LSSetDefaultRoleHandlerForContentType(uti, LS_ROLES_ALL, bid)
    for ref in (bid, scheme, uti):
        ls.cf.CFRelease(ref)
    if r1 or r2:
        log.warning("LaunchServices refused (magnet %s, torrent %s)", r1, r2)


def unregister() -> None:
    """macOS has no 'no default' - hand magnet/.torrent to the next app that claims them by resetting to the system
    choice, which is done by pointing at a bundle id nothing has (LaunchServices then falls back)."""
    ls = _ls()
    if not ls:
        return
    st = status()
    none, scheme, uti = ls.s("com.apple.none"), ls.s("magnet"), ls.s(TORRENT_UTI)
    if st["magnet"]:
        ls.cs.LSSetDefaultHandlerForURLScheme(scheme, none)
    if st["torrent"]:
        ls.cs.LSSetDefaultRoleHandlerForContentType(uti, LS_ROLES_ALL, none)
    for ref in (none, scheme, uti):
        ls.cf.CFRelease(ref)
