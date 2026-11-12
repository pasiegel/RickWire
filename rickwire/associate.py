"""Register RickWire as the per-user handler for magnet: links and .torrent files (no admin / root needed).

* Windows: HKCU\\Software\\Classes + RegisteredApplications (so it shows in "Default apps").
* Linux: ~/.local/share/applications/rickwire.desktop (also the app-menu entry) + `xdg-mime default`.
* macOS: the app's Info.plist declares both; LaunchServices makes RickWire the default (see macos.py).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROG_ID = "RickWire.Torrent"
ROOT = Path(__file__).resolve().parent.parent
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
DESKTOP_ID = "rickwire.desktop"
MIME_TYPES = ("x-scheme-handler/magnet", "application/x-bittorrent")


def _launch_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    if IS_WIN:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        return [str(pyw if pyw.exists() else Path(sys.executable)), str(ROOT / "run.py")]
    if (ROOT / "run.sh").exists():   # Linux portable: run.sh picks this PC's environment
        return [str(ROOT / "run.sh")]
    return [sys.executable, str(ROOT / "run.py")]


def _desktop_quote(arg: str) -> str:
    """Quote one argument for a .desktop Exec= line (double quotes only, per the Desktop Entry spec)."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def launch_command() -> str:
    argv = _launch_argv()
    if IS_WIN:
        return " ".join(f'"{a}"' for a in argv)
    return " ".join(_desktop_quote(a) for a in argv)


def icon_path() -> str:
    name = "rickwire.ico" if IS_WIN else "icon-512.png"
    ico = ROOT / "rickwire" / "web" / "icons" / name
    if getattr(sys, "frozen", False):   # PyInstaller puts data files under _internal
        ico = Path(getattr(sys, "_MEIPASS", ROOT)) / "rickwire" / "web" / "icons" / name
    return str(ico) if ico.exists() else launch_command().split('"')[1] if IS_WIN else ""


def register() -> None:
    if IS_MAC:
        from . import macos
        return macos.register()
    _win_register() if IS_WIN else _linux_register()


def unregister() -> None:
    if IS_MAC:
        from . import macos
        return macos.unregister()
    _win_unregister() if IS_WIN else _linux_unregister()


def status() -> dict:
    if IS_MAC:
        from . import macos
        return macos.status()
    return _win_status() if IS_WIN else _linux_status()


# ------------------------------------------------------------------------------------------ Linux
def _apps_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "applications"


def _desktop_file() -> Path:
    return _apps_dir() / DESKTOP_ID


def _previous_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "RickWire" / "previous-handlers.json"


def _linux_register() -> None:
    # remember who handled magnets / torrents before, so Unregister can hand them back
    prev = {m: cur for m in MIME_TYPES if (cur := _linux_default(m)) and cur != DESKTOP_ID}
    if prev:
        import json
        _previous_file().parent.mkdir(parents=True, exist_ok=True)
        old = {}
        try:
            old = json.loads(_previous_file().read_text("utf-8"))
        except (OSError, ValueError):
            pass
        _previous_file().write_text(json.dumps(old | prev), "utf-8")
    d = _desktop_file()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=RickWire\n"
        "GenericName=Download client\n"
        "Comment=Portal-powered debrid / yt-dlp download client\n"
        f"Exec={launch_command()} --add %u\n"
        f"Icon={icon_path()}\n"
        "Terminal=false\n"
        "Categories=Network;FileTransfer;P2P;\n"
        f"MimeType={';'.join(MIME_TYPES)};\n"
        "StartupWMClass=RickWire\n", "utf-8")
    d.chmod(0o755)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(d.parent)], capture_output=True)
    for mime in MIME_TYPES:
        if shutil.which("xdg-mime"):
            subprocess.run(["xdg-mime", "default", DESKTOP_ID, mime], capture_output=True)
        else:
            _mimeapps_set(mime, DESKTOP_ID)


def _linux_unregister() -> None:
    try:
        _desktop_file().unlink()
    except FileNotFoundError:
        pass
    was_ours = {m for m in MIME_TYPES if _linux_default(m) == DESKTOP_ID}
    _mimeapps_remove(DESKTOP_ID)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(_apps_dir())], capture_output=True)
    import json
    try:
        prev = json.loads(_previous_file().read_text("utf-8"))
        _previous_file().unlink()
    except (OSError, ValueError):
        prev = {}
    for mime, desktop_id in prev.items():
        if mime in was_ours and _desktop_installed(desktop_id):
            if shutil.which("xdg-mime"):
                subprocess.run(["xdg-mime", "default", desktop_id, mime], capture_output=True)
            else:
                _mimeapps_set(mime, desktop_id)


def _desktop_installed(desktop_id: str) -> bool:
    dirs = [_apps_dir()] + [Path(p) / "applications" for p in
                            os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")]
    return any((d / desktop_id).exists() for d in dirs)


def _linux_default(mime: str) -> str:
    if shutil.which("xdg-mime"):
        r = subprocess.run(["xdg-mime", "query", "default", mime], capture_output=True, text=True)
        return r.stdout.strip()
    return _mimeapps_get(mime)


def _linux_status() -> dict:
    installed = _desktop_file().exists()
    magnet, torrent = (_linux_default(m) for m in MIME_TYPES)
    return {"magnet": installed and magnet == DESKTOP_ID, "torrent": installed and torrent == DESKTOP_ID,
            "magnet_cmd": magnet, "torrent_progid": torrent}


def _mimeapps() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mimeapps.list"


def _mimeapps_lines() -> list[str]:
    try:
        return _mimeapps().read_text("utf-8").splitlines()
    except OSError:
        return []


def _mimeapps_get(mime: str) -> str:
    section = None
    for line in _mimeapps_lines():
        if line.startswith("["):
            section = line.strip()
        elif section == "[Default Applications]" and line.startswith(mime + "="):
            return line.split("=", 1)[1].split(";")[0]
    return ""


def _mimeapps_set(mime: str, desktop_id: str) -> None:
    lines, out, done, section = _mimeapps_lines(), [], False, None
    for line in lines:
        if line.startswith("["):
            if section == "[Default Applications]" and not done:
                out.append(f"{mime}={desktop_id};"); done = True
            section = line.strip()
        elif section == "[Default Applications]" and line.startswith(mime + "="):
            line, done = f"{mime}={desktop_id};", True
        out.append(line)
    if not done:
        if section != "[Default Applications]":
            out.append("[Default Applications]")
        out.append(f"{mime}={desktop_id};")
    _mimeapps().parent.mkdir(parents=True, exist_ok=True)
    _mimeapps().write_text("\n".join(out) + "\n", "utf-8")


def _mimeapps_remove(desktop_id: str) -> None:
    lines = _mimeapps_lines()
    if not lines:
        return
    out = []
    for line in lines:
        if "=" in line and not line.startswith("["):
            key, val = line.split("=", 1)
            apps = [a for a in val.split(";") if a and a != desktop_id]
            if not apps:
                continue
            line = f"{key}={';'.join(apps)};"
        out.append(line)
    _mimeapps().write_text("\n".join(out) + "\n", "utf-8")


# ------------------------------------------------------------------------------------------ Windows
def _win_register() -> None:
    import winreg as w
    cmd = f'{launch_command()} --add "%1"'

    def put(path: str, values: dict):
        with w.CreateKey(w.HKEY_CURRENT_USER, path) as k:
            for name, val in values.items():
                w.SetValueEx(k, name, 0, w.REG_SZ, val)

    put(r"Software\Classes\magnet", {"": "URL:Magnet link", "URL Protocol": ""})
    put(r"Software\Classes\magnet\DefaultIcon", {"": icon_path()})
    put(r"Software\Classes\magnet\shell\open\command", {"": cmd})
    put(rf"Software\Classes\{PROG_ID}", {"": "Torrent file (RickWire)"})
    put(rf"Software\Classes\{PROG_ID}\DefaultIcon", {"": icon_path()})
    put(rf"Software\Classes\{PROG_ID}\shell\open\command", {"": cmd})
    put(r"Software\Classes\.torrent", {"": PROG_ID, "Content Type": "application/x-bittorrent"})
    put(r"Software\Classes\.torrent\OpenWithProgids", {PROG_ID: ""})
    # Advertise capabilities so Windows "Default apps" lists RickWire.
    put(r"Software\RickWire\Capabilities", {"ApplicationName": "RickWire",
                                             "ApplicationDescription": "Portal-powered debrid download client"})
    put(r"Software\RickWire\Capabilities\URLAssociations", {"magnet": PROG_ID})
    put(r"Software\RickWire\Capabilities\FileAssociations", {".torrent": PROG_ID})
    put(r"Software\RegisteredApplications", {"RickWire": r"Software\RickWire\Capabilities"})
    _win_notify()


def _win_unregister() -> None:
    import winreg as w

    def nuke(path: str):
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, path, 0, w.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        nuke(path + "\\" + w.EnumKey(k, 0))
                    except OSError:
                        break
            w.DeleteKey(w.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    if _win_status()["magnet"]:  # only remove the magnet handler if it is still ours
        nuke(r"Software\Classes\magnet")
    nuke(rf"Software\Classes\{PROG_ID}")
    nuke(r"Software\RickWire")
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, r"Software\RegisteredApplications", 0, w.KEY_SET_VALUE) as k:
            w.DeleteValue(k, "RickWire")
    except OSError:
        pass
    _win_notify()


def _win_status() -> dict:
    import winreg as w
    out = {}
    for key, label in ((r"Software\Classes\magnet\shell\open\command", "magnet_cmd"),
                       (r"Software\Classes\.torrent", "torrent_progid")):
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, key) as k:
                out[label] = w.QueryValueEx(k, "")[0]
        except OSError:
            out[label] = ""
    out["magnet"] = launch_command() in out["magnet_cmd"]
    out["torrent"] = out["torrent_progid"] == PROG_ID
    return out


def _win_notify():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass
