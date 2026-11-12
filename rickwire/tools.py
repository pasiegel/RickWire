"""Downloads, verifies and updates the helper programs media downloads need.

* yt-dlp - the official pure-Python release zip. It's loaded in place of the bundled copy, so yt-dlp can be
  updated without rebuilding RickWire (sites change, yt-dlp breaks, a new release fixes it).
* ffmpeg - BtbN's stable release builds (merges video + audio, converts audio). BtbN has no macOS builds, so the
  Mac uses eugeneware/ffmpeg-static (static ffmpeg + ffprobe, one gzipped file each) - or Homebrew's, if installed.
* Deno - the JavaScript runtime yt-dlp needs for YouTube.

Everything comes from each project's official GitHub releases over HTTPS. Only GitHub hosts are allowed, and
every file is checked against the SHA-256 that GitHub publishes for it before it's unpacked or used.

`python -m rickwire.tools install [--dest DIR]` does the same from the command line (build.ps1 uses it).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import platform
import shutil
import stat
import subprocess
import sys
import gzip
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

from .config import BUNDLED_TOOLS_DIR, EXE, IS_MAC, IS_WIN, TOOLS_DIR

log = logging.getLogger("rickwire.tools")

ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com", "github-releases.githubusercontent.com"}
NO_WINDOW = 0x08000000 if IS_WIN else 0
CHECK_TTL = 6 * 3600
PYZ = "yt-dlp.pyz"
ARM = platform.machine().lower() in ("aarch64", "arm64")
# Release-asset names per platform. ffmpeg = BtbN's stable release builds (nX.Y), not nightly master. BtbN used to
# name them ffmpeg-n9.0-latest-win64-gpl-9.0.zip; since 2026-11 it's ffmpeg-n9.0.2-57-gd838f9f9ae-win64-gpl-9.0.zip.
_BTBN = r"ffmpeg-n(\d+)\.(\d+)((?:\.\d+)?)-(?:latest|\d+-g[0-9a-f]+)-%s-gpl-\d+\.\d+\.%s"
if IS_WIN:
    FFMPEG_ASSET = _BTBN % ("win64", "zip")
    DENO_ASSET = "deno-x86_64-pc-windows-msvc.zip"
elif IS_MAC:
    FFMPEG_ASSET = f"ffmpeg-darwin-{'arm64' if ARM else 'x64'}.gz"   # + the matching ffprobe-darwin-*.gz
    DENO_ASSET = f"deno-{'aarch64' if ARM else 'x86_64'}-apple-darwin.zip"
else:
    FFMPEG_ASSET = _BTBN % ("linuxarm64" if ARM else "linux64", r"tar\.xz")
    DENO_ASSET = f"deno-{'aarch64' if ARM else 'x86_64'}-unknown-linux-gnu.zip"
# Windows and Linux binaries can share one tools folder (portable copy on a network share)
MANIFEST = "tools.json" if IS_WIN else "tools-mac.json" if IS_MAC else "tools-linux.json"
# Apps started from Finder/Dock don't get the shell's PATH, so Homebrew's folders are added by hand
EXTRA_PATH = "/opt/homebrew/bin:/usr/local/bin" if IS_MAC else ""

TOOLS: dict[str, dict] = {
    "yt-dlp": {"label": "yt-dlp", "repo": "yt-dlp/yt-dlp", "size": "3 MB",
               "what": "Downloads from YouTube, Tubi, archive.org, .mpd/.m3u8 and 1000+ sites"},
    "ffmpeg": {"label": "ffmpeg", "repo": "eugeneware/ffmpeg-static" if IS_MAC else "FFmpeg-Builds/FFmpeg-Builds",   # BtbN's (moved 2026-11)
               "size": "40 MB" if IS_MAC else "190 MB",
               "what": "Merges separate video + audio, converts to MP3/M4A"},
    "deno": {"label": "Deno", "repo": "denoland/deno", "size": "45 MB",
             "what": "JavaScript runtime that YouTube downloads need"},
}


class ToolError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- helpers
def vtuple(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


def pyz_version(path: Path) -> str | None:
    """Read __version__ from a yt-dlp release zip without importing it."""
    try:
        with zipfile.ZipFile(path) as z:
            src = z.read("yt_dlp/version.py").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", src)
    return m.group(1) if m else None


def bundled_ytdlp_version() -> str:
    try:
        from importlib.metadata import version
        return version("yt-dlp")
    except Exception:
        return "0"


def probe(exe: str | Path, flag: str) -> str | None:
    try:
        out = subprocess.run([str(exe), flag], capture_output=True, text=True, timeout=15,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", first)
    if m:
        return m.group(1)
    return first[:40] or None


def _manifest_path(d: Path) -> Path:
    return d / MANIFEST


def read_manifest(d: Path = TOOLS_DIR) -> dict:
    try:
        return json.loads(_manifest_path(d).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_manifest(d: Path, data: dict):
    tmp = _manifest_path(d).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    os.replace(tmp, _manifest_path(d))


def find_exe(name: str) -> tuple[Path | None, str]:
    """-> (path, source): source is "rickwire" (managed copy in the tools folder) or "system" (on PATH)."""
    for d in dict.fromkeys((TOOLS_DIR, BUNDLED_TOOLS_DIR)):
        p = d / f"{name}{EXE}"
        if p.exists():
            return p, "rickwire"
    w = shutil.which(name) or (shutil.which(name, path=EXTRA_PATH) if EXTRA_PATH else None)
    return (Path(w), "system") if w else (None, "")


def _swap(src: Path, dst: Path):
    """Replace dst with src even if dst is a running exe (Windows lets you rename those, not overwrite)."""
    try:
        os.replace(src, dst)
    except PermissionError:
        old = dst.with_name(f"{dst.name}.old-{int(time.time())}")
        os.replace(dst, old)
        os.replace(src, dst)


def cleanup_old(d: Path = TOOLS_DIR):
    for p in d.glob("*.old-*"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in d.glob(".*.download"):
        try:
            p.unlink()
        except OSError:
            pass


def _members(archive: Path, wanted: tuple[str, ...]):
    """Yield (basename, file object) for the wanted files in a .zip or .tar.xz, ignoring their folders."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = info.filename.rsplit("/", 1)[-1]
                if base in wanted and not info.is_dir():
                    yield base, z.open(info)
        return
    with tarfile.open(archive, "r:*") as t:
        for m in t:
            base = m.name.rsplit("/", 1)[-1]
            if base in wanted and m.isfile():   # regular files only - no links/devices from the archive
                yield base, t.extractfile(m)


def _mac_prepare(exe: Path):
    """Apple Silicon only runs signed code: give a downloaded binary an ad-hoc signature if it has none, and drop the
    quarantine flag (httpx downloads normally don't get one)."""
    subprocess.run(["xattr", "-d", "com.apple.quarantine", str(exe)], capture_output=True)
    if subprocess.run(["codesign", "-v", str(exe)], capture_output=True).returncode != 0:
        subprocess.run(["codesign", "--force", "-s", "-", str(exe)], capture_output=True)


# ---------------------------------------------------------------------------------------------- manager
class ToolManager:
    def __init__(self, dest: Path = TOOLS_DIR):
        self.dest = dest
        self.dest.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, read=60), follow_redirects=True, trust_env=False,
            headers={"User-Agent": "RickWire tool updater", "Accept": "application/vnd.github+json"},
            event_hooks={"request": [self._guard]})
        self.releases: dict[str, dict] = {}
        self.checked_at = 0.0
        self.check_errors: dict[str, str] = {}
        self.progress: dict[str, dict] = {}
        self._locks = {n: asyncio.Lock() for n in TOOLS}

    @staticmethod
    async def _guard(request: httpx.Request):
        # Every request - including each redirect hop - must be HTTPS to a GitHub host.
        if request.url.scheme != "https" or request.url.host not in ALLOWED_HOSTS:
            raise ToolError(f"Blocked request to {request.url.scheme}://{request.url.host} (only HTTPS to GitHub is allowed)")

    # ------------------------------------------------------------------ what's installed
    def installed(self, name: str) -> dict:
        if name == "yt-dlp":
            from .sources import ytdl
            running = ytdl.version()
            pyz = self.dest / PYZ
            pending = pyz_version(pyz) if pyz.exists() else None
            info = {"version": running, "source": "rickwire" if ytdl.USING_PYZ else "built in", "path": None}
            if pending and vtuple(pending) > vtuple(running):
                info["pending"] = pending   # downloaded, active after a restart
            return info
        if name == "ffmpeg":
            path, src = find_exe("ffmpeg")
            flag = "-version"
        else:
            path, src = find_exe("deno")
            flag = "--version"
            if not path:   # Node or Bun also work for YouTube
                for alt in ("node", "bun"):
                    p, s = find_exe(alt)
                    if p:
                        return {"version": None, "source": s, "path": str(p),
                                "alt": f"{alt.title()} {probe(p, '--version') or ''}".strip()}
        if not path:
            return {"version": None, "source": "", "path": None}
        man = read_manifest(path.parent) if src == "rickwire" else {}
        ver = (man.get(name) or {}).get("version") or probe(path, flag)
        return {"version": ver, "source": src, "path": str(path)}

    def status(self) -> dict:
        out = []
        for name, meta in TOOLS.items():
            inst = self.installed(name)
            rel = self.releases.get(name)
            latest = rel["version"] if rel else None
            have = inst.get("pending") or inst.get("version")
            update = bool(latest and (not have or vtuple(latest) > vtuple(have)))
            if name == "deno" and inst.get("alt"):
                update = False   # Node/Bun already does the job; Deno is optional
            out.append({"name": name, "label": meta["label"], "what": meta["what"], "size": meta["size"],
                        **inst, "latest": latest, "update": update,
                        "missing": not inst.get("version") and not inst.get("alt"),
                        "error": self.check_errors.get(name), "job": self.progress.get(name)})
        return {"tools": out, "checked_at": self.checked_at, "dir": str(self.dest),
                "restart_needed": any(t.get("pending") for t in out),
                "busy": any(j.get("state") in ("downloading", "verifying", "installing")
                            for j in self.progress.values())}

    # ------------------------------------------------------------------ what's available
    async def _latest(self, name: str) -> dict:
        # CI builds set GITHUB_TOKEN (shared runner IPs hit the anonymous rate limit); only ever sent to the API
        token = os.environ.get("GITHUB_TOKEN")
        r = await self.client.get(f"https://api.github.com/repos/{TOOLS[name]['repo']}/releases/latest",
                                  headers={"Authorization": f"Bearer {token}"} if token else None)
        if r.status_code != 200:
            raise ToolError(f"GitHub returned HTTP {r.status_code}"
                            + (" (rate limited - try again in an hour)" if r.status_code in (403, 429) else ""))
        rel = r.json()
        assets = {a["name"]: a for a in rel.get("assets", [])}
        if name == "yt-dlp":
            asset, version = assets.get("yt-dlp"), rel["tag_name"]
        elif name == "deno":
            asset, version = assets.get(DENO_ASSET), rel["tag_name"].lstrip("v")
        elif IS_MAC:   # eugeneware/ffmpeg-static: tag b6.1.1, separate ffmpeg-darwin-arm64.gz + ffprobe-darwin-arm64.gz
            asset, version = assets.get(FFMPEG_ASSET), rel["tag_name"].lstrip("bv")
            probe_asset = assets.get(FFMPEG_ASSET.replace("ffmpeg-", "ffprobe-", 1))
            if asset and probe_asset:
                return {"version": version, "asset": asset, "assets": assets, "extra": [probe_asset]}
            asset = None
        else:   # newest stable release build, e.g. ffmpeg-n9.0-latest-win64-gpl-9.0.zip
            best = None
            for n, a in assets.items():
                m = re.fullmatch(FFMPEG_ASSET, n)
                if m:
                    ver = f"{m.group(1)}.{m.group(2)}{m.group(3)}"   # 9.0 or 9.0.2
                    if best is None or vtuple(ver) > vtuple(best[0]):
                        best = (ver, a)
            asset, version = (best[1], best[0]) if best else (None, None)
        if not asset:
            raise ToolError(f"No download for this system ({sys.platform}/{platform.machine()}) in the latest release")
        return {"version": version, "asset": asset, "assets": assets}

    async def check(self, force: bool = False) -> dict:
        if force or time.time() - self.checked_at > CHECK_TTL:
            results = await asyncio.gather(*(self._latest(n) for n in TOOLS), return_exceptions=True)
            self.check_errors = {}
            for name, res in zip(TOOLS, results):
                if isinstance(res, Exception):
                    self.check_errors[name] = str(res) or res.__class__.__name__
                    log.warning("update check for %s failed: %s", name, res)
                else:
                    self.releases[name] = res
            self.checked_at = time.time()
        return self.status()

    # ------------------------------------------------------------------ install / update
    async def _expected_sha(self, rel: dict, asset: dict | None = None) -> str:
        asset = asset or rel["asset"]
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            return digest.split(":", 1)[1].lower()
        # Older API responses: fall back to the checksum file published with the release.
        for cand in (asset["name"] + ".sha256sum", "SHA2-256SUMS", "checksums.sha256"):
            if cand in rel["assets"]:
                r = await self.client.get(rel["assets"][cand]["browser_download_url"])
                for line in r.text.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[-1].lstrip("*").endswith(asset["name"]):
                        return parts[0].lower()
                    if len(parts) == 1 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                        return parts[0].lower()
        raise ToolError("GitHub didn't publish a checksum for this file - refusing to install it")

    async def install(self, name: str) -> dict:
        if name not in TOOLS:
            raise ToolError("Unknown tool")
        lock = self._locks[name]
        if lock.locked():
            raise ToolError("Already downloading")
        async with lock:
            job = self.progress[name] = {"state": "downloading", "done": 0, "total": 0, "error": None}
            tmp = self.dest / f".{name}.download"
            tmps = [tmp]
            try:
                rel = self.releases.get(name) or await self._latest(name)
                self.releases[name] = rel
                assets = [rel["asset"]] + rel.get("extra", [])   # Mac ffmpeg: ffmpeg + ffprobe are two files
                job["total"] = sum(a["size"] for a in assets)
                archives, sha = [], ""
                for i, asset in enumerate(assets):
                    path = tmp if i == 0 else tmp.with_name(f"{tmp.name}{i}")
                    tmps.append(path)
                    job["state"] = "downloading"
                    expected = await self._expected_sha(rel, asset)
                    h = hashlib.sha256()
                    async with self.client.stream("GET", asset["browser_download_url"]) as r:
                        if r.status_code != 200:
                            raise ToolError(f"Download failed (HTTP {r.status_code})")
                        with open(path, "wb") as fh:
                            async for chunk in r.aiter_bytes(1 << 16):
                                fh.write(chunk)
                                h.update(chunk)
                                job["done"] += len(chunk)
                    job["state"] = "verifying"
                    if h.hexdigest() != expected:
                        raise ToolError("Checksum mismatch - the download was damaged or tampered with, so it was thrown away")
                    archives.append((asset["name"], path))
                    sha = sha or expected
                job["state"] = "installing"
                version = await asyncio.to_thread(self._unpack, name, tmp, rel["version"], archives)
                man = read_manifest(self.dest)
                man[name] = {"version": version, "sha256": sha, "installed": int(time.time()),
                             "from": rel["asset"]["browser_download_url"]}
                _write_manifest(self.dest, man)
                job.update(state="done", version=version)
                log.info("installed %s %s (sha256 %s)", name, version, sha)
            except Exception as e:
                job.update(state="error", error=str(e) or e.__class__.__name__)
                log.warning("installing %s failed: %s", name, e)
                raise ToolError(job["error"]) from None
            finally:
                for path in tmps:
                    try:
                        path.unlink()
                    except OSError:
                        pass
        return self.status()

    def _unpack(self, name: str, archive: Path, version: str, archives: list | None = None) -> str:
        if name == "yt-dlp":
            got = pyz_version(archive)
            if not got:
                raise ToolError("That yt-dlp download isn't a valid release zip")
            dst = self.dest / PYZ
            staged = dst.with_suffix(".new")
            shutil.copyfile(archive, staged)
            os.replace(staged, dst)
            return got
        wanted = {"ffmpeg": (f"ffmpeg{EXE}", f"ffprobe{EXE}"), "deno": (f"deno{EXE}",)}[name]
        with tempfile.TemporaryDirectory(dir=self.dest) as td:
            staged = {}
            if archives and archives[0][0].endswith(".gz") and not archives[0][0].endswith(".tar.gz"):
                members = ((n.split("-", 1)[0], gzip.open(p)) for n, p in archives)   # ffmpeg-darwin-arm64.gz -> ffmpeg
            else:
                members = _members(archive, wanted)
            for base, src in members:
                if base not in wanted:
                    continue
                out = Path(td) / base
                with src as fh_in, open(out, "wb") as fh:
                    shutil.copyfileobj(fh_in, fh, 1 << 20)
                if not IS_WIN:
                    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                if IS_MAC:
                    _mac_prepare(out)
                staged[base] = out
            missing = [w for w in wanted if w not in staged]
            if missing:
                raise ToolError(f"{', '.join(missing)} not found in the download")
            # make sure the new binary actually runs before it replaces a working one
            main = staged[wanted[0]]
            got = probe(main, "-version" if name == "ffmpeg" else "--version")
            if not got:
                raise ToolError(f"The downloaded {wanted[0]} didn't run")
            for base, path in staged.items():
                _swap(path, self.dest / base)
        return version if name == "ffmpeg" else got

    async def aclose(self):
        await self.client.aclose()


# ---------------------------------------------------------------------------------------------- CLI
def _cli(argv: list[str]):
    import argparse
    p = argparse.ArgumentParser(prog="python -m rickwire.tools")
    p.add_argument("action", choices=["status", "install"])
    p.add_argument("names", nargs="*", default=[])
    p.add_argument("--dest", type=Path, default=TOOLS_DIR)
    a = p.parse_args(argv)

    async def main():
        tm = ToolManager(a.dest)
        try:
            if a.action == "status":
                st = await tm.check(force=True)
                for t in st["tools"]:
                    print(f"{t['label']:8} installed={t.get('version') or t.get('alt') or '-':14} "
                          f"latest={t['latest'] or '?':12} {'UPDATE' if t['update'] else ''} {t.get('error') or ''}")
                return 0
            for name in a.names or ["ffmpeg", "deno"]:
                print(f"installing {name} -> {a.dest}", flush=True)
                await tm.install(name)
                print(f"  ok: {tm.progress[name].get('version')}", flush=True)
            return 0
        except ToolError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        finally:
            await tm.aclose()

    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    _cli(sys.argv[1:])
