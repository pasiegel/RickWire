"""yt-dlp wrapper: YouTube, Tubi, Internet Archive, plain .mpd/.m3u8 manifests, 1000+ sites."""
from __future__ import annotations

import logging
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable

from ..config import TOOLS_DIR
from ..tools import PYZ, bundled_ytdlp_version, find_exe, pyz_version, vtuple

log = logging.getLogger("rickwire.ytdlp")


_notes: list[str] = []


def _load_ytdlp():
    """Prefer a newer yt-dlp downloaded by the tool updater over the copy built into RickWire."""
    pyz = TOOLS_DIR / PYZ
    if pyz.exists():
        got, built_in = pyz_version(pyz), bundled_ytdlp_version()
        if got and vtuple(got) > vtuple(built_in):
            sys.path.insert(0, str(pyz))
            try:
                import yt_dlp
                if str(pyz) in (yt_dlp.__file__ or ""):
                    return yt_dlp, True
                _notes.append(f"downloaded yt-dlp {got} was shadowed by the built-in copy")
            except Exception:
                _notes.append(f"downloaded yt-dlp {got} failed to load - using the built-in {built_in}")
                for mod in [m for m in sys.modules if m == "yt_dlp" or m.startswith(("yt_dlp.", "yt_dlp_ejs"))]:
                    del sys.modules[mod]
            sys.path.remove(str(pyz))
    import yt_dlp
    return yt_dlp, False


yt_dlp, USING_PYZ = _load_ytdlp()
# imported before logging is set up - desktop.py logs this once it is
LOAD_NOTE = "; ".join(_notes + [f"yt-dlp {yt_dlp.version.__version__} ({'downloaded update' if USING_PYZ else 'built in'})"])
from yt_dlp.utils import DownloadCancelled  # noqa: E402

MANIFEST_EXT = (".mpd", ".m3u8", ".ism/manifest")


@lru_cache(maxsize=1)
def _extractors():
    return [ie for ie in yt_dlp.extractor.gen_extractor_classes() if ie.IE_NAME not in ("generic",)]


def has_extractor(url: str) -> bool:
    low = url.lower().split("?")[0]
    if low.endswith(MANIFEST_EXT):
        return True
    return any(ie.suitable(url) for ie in _extractors())


class _Log:
    """Send yt-dlp chatter to the RickWire log instead of the console."""
    _log = logging.getLogger("rickwire.ytdlp")

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self._log.warning(msg)

    def error(self, msg):
        self._log.error(msg)


def ffmpeg_location(configured: str = "") -> str | None:
    if configured and Path(configured).exists():
        return configured
    path, _ = find_exe("ffmpeg")
    return str(path) if path else None


def js_runtimes() -> dict:
    """YouTube needs a JS runtime (yt-dlp-ejs). Prefer RickWire's own Deno, then whatever is installed."""
    for name in ("deno", "node", "bun"):
        path, _ = find_exe(name)
        if path:
            return {name: {"path": str(path)}}
    return {}


def js_runtime_label() -> str | None:
    rt = js_runtimes()
    return next(iter(rt.values()))["path"] if rt else None


def version() -> str:
    return yt_dlp.version.__version__


def sub_langs(text: str) -> list[str]:
    """'en, es' -> ['en', 'es']. yt-dlp takes regexes: 'en.*' = en, en-US, en-GB..."""
    return [s.strip() for s in text.replace(";", ",").split(",") if s.strip()][:10]


def subtitle_opts(langs: list[str], auto: bool, ffmpeg: str | None) -> dict:
    """Subtitles like YT-Playlist-Grabber: get them as .srt and embed them in the video (the separate files are
    removed after embedding). Without ffmpeg they can't be converted or embedded, so they stay as files beside it."""
    opts = {"writesubtitles": True, "writeautomaticsub": auto, "subtitleslangs": langs,
            "subtitlesformat": "srt/best"}
    if ffmpeg:
        opts["postprocessors"] = [
            {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"},
            {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False},
        ]
    return opts


def run(url: str, dest_dir: Path, fmt: str, audio_only: bool, audio_format: str, ffmpeg: str | None,
        on_progress: Callable[[dict], None], cancel: threading.Event, on_title: Callable[[str], None],
        subs: dict | None = None, notes: list[str] | None = None) -> list[Path]:
    """Blocking - call from a worker thread. subs = {"langs": [...], "auto": bool} to fetch + embed subtitles.
    A subtitle that won't download never fails the job: it is retried once without subtitles (added to notes)."""
    want_subs = bool(subs and subs.get("langs") and not audio_only)
    try:
        return _run(url, dest_dir, fmt, audio_only, audio_format, ffmpeg, on_progress, cancel, on_title,
                    subs if want_subs else None)
    except Exception as e:
        if not want_subs or cancel.is_set() or "subtitle" not in str(e).lower():
            raise
        log.warning("subtitles failed (%s) - downloading without them", str(e)[:200])
        out = _run(url, dest_dir, fmt, audio_only, audio_format, ffmpeg, on_progress, cancel, on_title, None)
        if notes is not None:
            notes.append("no subtitles (they wouldn't download)")
        return out


def _run(url, dest_dir, fmt, audio_only, audio_format, ffmpeg, on_progress, cancel, on_title, subs) -> list[Path]:
    produced: list[Path] = []

    def hook(d: dict):
        if cancel.is_set():
            raise DownloadCancelled("Canceled")
        on_progress(d)

    def post_hook(path):
        produced.append(Path(path))

    opts = {
        "logger": _Log(),
        "outtmpl": str(dest_dir / "%(title).150B [%(id)s].%(ext)s"),
        "progress_hooks": [hook],
        "post_hooks": [post_hook],
        "noplaylist": False,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    runtimes = js_runtimes()
    if runtimes:  # YouTube needs a JS runtime for signature solving (yt-dlp-ejs)
        opts["js_runtimes"] = runtimes
    if audio_only:
        opts["format"] = "bestaudio/best"
        if ffmpeg:
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": audio_format}]
    else:
        opts["format"] = fmt if ffmpeg else "best"   # merging needs ffmpeg
        opts["merge_output_format"] = "mp4/mkv"
        if subs:
            opts.update(subtitle_opts(subs["langs"], subs.get("auto", False), ffmpeg))

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        on_title(info.get("title") or url)
        ydl.process_ie_result(info, download=True)
    return produced
