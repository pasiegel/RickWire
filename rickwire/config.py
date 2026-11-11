"""Paths and non-secret settings. Secrets never live here - see vault.py."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path

from . import APP_NAME

FROZEN = getattr(sys, "frozen", False)
IS_WIN = sys.platform == "win32"
EXE = ".exe" if IS_WIN else ""
# Folder holding RickWire.exe (portable build) or the project root (source checkout / Linux portable).
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
# The portable zip / tarball ships a PORTABLE.txt beside the exe or run.sh; the installed copy doesn't.
PORTABLE = (APP_DIR / "PORTABLE.txt").exists()
BUNDLED_TOOLS_DIR = APP_DIR / "tools"   # ffmpeg / deno shipped with (or downloaded into) the app folder


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _data_dir() -> Path:
    if os.environ.get("RICKWIRE_HOME"):
        return Path(os.environ["RICKWIRE_HOME"])
    if PORTABLE:
        # Portable: keep everything beside the exe, one subfolder per PC, so the same folder can be
        # copied around or even run from a network share on two machines at once without clashing.
        return APP_DIR / "data" / socket.gethostname().upper()
    return _user_dir("APPDATA", "XDG_DATA_HOME", ".local/share")


def _user_dir(win_var: str, xdg_var: str, xdg_default: str) -> Path:
    if IS_WIN:
        return Path(os.environ.get(win_var, Path.home())) / APP_NAME
    return Path(os.environ.get(xdg_var) or Path.home() / xdg_default) / APP_NAME


# Where downloaded/updated tools go: the app folder when we can write to it (portable, per-user install,
# source checkout), otherwise %LOCALAPPDATA%/RickWire/tools or ~/.local/share/RickWire/tools.
TOOLS_DIR = BUNDLED_TOOLS_DIR if _writable(BUNDLED_TOOLS_DIR) else (
    _user_dir("LOCALAPPDATA", "XDG_DATA_HOME", ".local/share") / "tools")
TOOLS_DIR.mkdir(parents=True, exist_ok=True)


DATA_DIR = _data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / APP_NAME)
CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "jobs.sqlite3"
TORRENT_DIR = DATA_DIR / "torrents"
TLS_DIR = DATA_DIR / "tls"
LOCAL_TOKEN_FILE = DATA_DIR / "local.token"
LOG_FILE = DATA_DIR / "rickwire.log"
TORRENT_DIR.mkdir(exist_ok=True)

WEB_DIR = Path(__file__).parent / "web"

DEFAULTS: dict = {
    "download_dir": "",            # empty = <this PC's>\Downloads\RickWire
    "search_url": "https://bt4gprx.com",
    "yts_api": "https://yts.gg/api/v2",
    "eztv_api": "https://eztvx.to/api",
    "max_concurrent": 3,
    "ask_provider": True,          # ask which debrid to use when more than one is configured
    "default_provider": None,
    "providers_enabled": {},       # provider id -> bool
    "hoster_mode": "debrid_first", # debrid_first | native_first
    "cleanup_remote": True,        # delete torrent from debrid after local download
    "pick_files": True,            # ask which files to get when a torrent has several
    "spin_portal": True,           # animate the portal logo (off = still image, everywhere incl. phones)
    "auto_retry": 3,               # automatic retries for network errors / expired links (0 = off)
    "ytdlp_format": "bestvideo*+bestaudio/best",
    "ytdlp_audio_format": "mp3",
    "ytdlp_subs": True,            # fetch subtitles for yt-dlp videos and embed them (like YT-Playlist-Grabber)
    "ytdlp_sub_langs": "en.*",     # comma-separated yt-dlp languages / regexes ("en.*" = en, en-US, en-GB...)
    "ytdlp_auto_subs": False,      # also accept YouTube's auto-generated captions
    "ffmpeg_path": "",
    "local_port": 13700,           # 127.0.0.1 only, desktop window
    "lan_enabled": True,
    "lan_port": 13701,             # LAN / phone PWA
    "lan_https": True,
    "allowed_cidrs": [],           # extra allowlist; private ranges are always allowed
    "close_to_tray": IS_WIN,       # Linux: GNOME hides tray icons without an extension, so closing quits by default
    "start_minimized": False,
    "onboarded": False,            # first-run setup finished or skipped
    "auto_update_ytdlp": True,     # yt-dlp breaks often when sites change - keep it current
    "check_tool_updates": True,    # daily check for ffmpeg / Deno / yt-dlp updates
    "tools_checked": 0,
    "devices": [],                 # paired phones: {id, name, hash, created, last_seen}
    "remote_enabled": False,       # allow access from outside, through a trusted reverse proxy + web login
    "trusted_proxies": [],         # e.g. ["192.168.1.20"] - the PC running Nginx Proxy Manager
    "public_hosts": [],            # e.g. ["rickwire.example.com"]
    "web_login": {},               # {user, hash (scrypt), totp (bool), sessions[]} - never sent to the UI
}

# Keys a paired (non-desktop) device may read. Everything else is desktop-only.
PUBLIC_SETTING_KEYS = {"ask_provider", "default_provider", "hoster_mode", "ytdlp_format", "lan_https", "spin_portal"}


class Settings:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError):
                pass
        Path(self["download_dir"]).mkdir(parents=True, exist_ok=True)

    def __getitem__(self, key):
        value = self.data.get(key, DEFAULTS.get(key))
        if key == "download_dir" and not value:
            return DEFAULT_DOWNLOAD_DIR
        return value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, values: dict):
        with self._lock:
            self.data.update(values)
            self.save_locked()

    def save(self):
        with self._lock:
            self.save_locked()

    def save_locked(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        os.replace(tmp, self.path)
