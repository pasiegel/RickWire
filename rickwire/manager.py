"""Job queue: debrid torrents, hoster links, MEGA, Gofile, yt-dlp and direct downloads."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import httpx

from . import bencode, organize, vault
from .config import DB_FILE, TORRENT_DIR, Settings
from .providers import ALL as PROVIDERS, ProviderError, RemoteItem
from .sources import classify, extract_links, gofile, is_magnet, mega, ytdl
from .sources.http_dl import download, make_client, safe_name, safe_relpath

log = logging.getLogger("rickwire.jobs")

ACTIVE = {"queued", "remote", "downloading"}
NEEDS_PROVIDER = {"torrent", "torrent_url", "debrid"}
RETRY_DELAYS = (10, 30, 90, 180, 300)
_TRANSIENT_MSG = re.compile(r"HTTP Error (429|5\d\d)|\(HTTP (429|5\d\d)\)|timed? ?out|Connection (reset|aborted|refused)|"
                            r"Temporary failure|getaddrinfo|Remote end closed|IncompleteRead", re.I)
CACHE_TTL = {True: 3600, False: 900, None: 120, "blocked": 3600}


class Canceled(Exception):
    pass


class AwaitingFiles(Exception):
    """The user has to pick which files of the torrent to download."""


def transient(e: Exception, kind: str) -> bool:
    """Worth retrying on its own: network hiccups, overloaded servers, and expired download links."""
    if isinstance(e, httpx.TransportError):
        return True
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        # 403/404/410 from a CDN usually means the debrid link expired - a retry asks for a fresh one
        return code in (408, 425, 429, 500, 502, 503, 504) or (kind in NEEDS_PROVIDER and code in (403, 404, 410))
    return bool(_TRANSIENT_MSG.search(str(e)))


def magnet_hash(magnet: str) -> str | None:
    m = re.search(r"btih:([0-9a-fA-F]{40}|[A-Z2-7a-z]{32})", magnet or "")
    if not m:
        return None
    h = m.group(1)
    if len(h) == 32:   # base32 info-hash -> hex
        import base64
        try:
            h = base64.b32decode(h.upper()).hex()
        except ValueError:
            return None
    return h.lower()


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")
        self.jobs: dict[int, dict] = {}
        for jid, data in self.db.execute("SELECT id, data FROM jobs"):
            job = json.loads(data)
            job["id"] = jid
            self.jobs[jid] = job
        self.tasks: dict[int, asyncio.Task] = {}
        self.flags: dict[int, threading.Event] = {}
        self.dirty: set[int] = set()
        self.listeners: list[Callable[[dict, str], None]] = []
        self.sem: asyncio.Semaphore | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.http = make_client()
        self._cache: dict[tuple[str, str], tuple[float, bool | None]] = {}
        self._probe_locks: dict[str, asyncio.Semaphore] = {}

    # ------------------------------------------------------------------ setup
    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.sem = asyncio.Semaphore(max(1, int(self.settings["max_concurrent"])))
        for job in self.jobs.values():
            if job["state"] in ACTIVE:
                job["state"] = "queued"
                self.schedule(job)
        asyncio.create_task(self._ticker())
        asyncio.get_running_loop().run_in_executor(None, ytdl._extractors)  # warm the extractor list

    def resize(self, n: int):
        self.sem = asyncio.Semaphore(max(1, n))

    async def _ticker(self):
        last: dict[int, tuple[float, int]] = {}
        n = 0
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            for jid, job in list(self.jobs.items()):
                if job["state"] == "downloading":
                    t0, d0 = last.get(jid, (now, job["done"]))
                    if now > t0:
                        inst = (job["done"] - d0) / (now - t0)
                        job["speed"] = int(job.get("speed", 0) * 0.6 + inst * 0.4)
                    last[jid] = (now, job["done"])
                    self.dirty.add(jid)
                elif jid in last:
                    last.pop(jid)
                    job["speed"] = 0
            n += 1
            if n % 3 == 0:
                self.flush()

    def flush(self):
        for jid in list(self.dirty):
            if jid in self.jobs:
                self._write(self.jobs[jid])
        self.dirty.clear()

    def _write(self, job: dict):
        self.db.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job), job["id"]))
        self.db.commit()

    def _emit(self, job: dict, event: str):
        for cb in self.listeners:
            try:
                cb(job, event)
            except Exception:
                log.exception("listener failed")

    # ------------------------------------------------------------ public API
    def configured_providers(self) -> list[str]:
        enabled = self.settings["providers_enabled"] or {}
        return [pid for pid, p in PROVIDERS.items() if enabled.get(pid, True) and p.configured()]

    def list(self) -> list[dict]:
        return sorted(self.jobs.values(), key=lambda j: j["id"], reverse=True)

    async def add_text(self, text: str, mode: str = "auto", provider: str | None = None,
                       audio_only: bool = False, sort: bool = False) -> list[dict]:
        return [await self.add(link, mode, provider, audio_only, sort=sort) for link in extract_links(text)]

    async def add(self, source: str, mode: str = "auto", provider: str | None = None, audio_only: bool = False,
                  torrent_bytes: bytes | None = None, torrent_filename: str | None = None, sort: bool = False) -> dict:
        providers = self.configured_providers()
        name = None
        if torrent_bytes is not None:
            kind = "torrent"
            name, _ = bencode.torrent_info(torrent_bytes)
            name = name or (torrent_filename or "torrent").removesuffix(".torrent")
        elif is_magnet(source):
            kind, name = "torrent", bencode.magnet_name(source)
        elif mode == "debrid":
            kind = "debrid"
        elif mode == "ytdlp":
            kind = "ytdlp"
        elif mode == "direct":
            kind = "direct"
        else:
            hoster_mode = "native_first" if mode == "native" else self.settings["hoster_mode"]
            kind = await asyncio.to_thread(classify, source, bool(providers), hoster_mode)
            if mode == "native" and kind == "debrid":
                kind = "mega" if mega.is_mega(source) else "gofile" if gofile.is_gofile(source) else "direct"
        if kind in ("direct", "debrid"):
            name = unquote(urlparse(source).path.rstrip("/").rsplit("/", 1)[-1]) or None

        job = {
            "created": time.time(), "source": source if torrent_bytes is None else (torrent_filename or "upload.torrent"),
            "kind": kind, "mode": mode, "provider": None, "remote_id": None, "state": "queued",
            "name": name or source[:120], "size": 0, "done": 0, "speed": 0, "remote_progress": 0,
            "message": "", "files": [], "outputs": [], "audio_only": audio_only, "torrent_file": None,
            "sort": sort,
        }
        if kind in NEEDS_PROVIDER:
            if provider and provider in PROVIDERS:
                job["provider"] = provider
            elif not providers:
                job.update(state="error", message="No debrid provider configured - add an API key in Settings")
            elif len(providers) == 1:
                job["provider"] = providers[0]
            elif self.settings["ask_provider"]:
                job.update(state="awaiting_provider", message="Which dimension, Morty? Pick a provider.")
            else:
                dp = self.settings["default_provider"]
                job["provider"] = dp if dp in providers else providers[0]

        cur = self.db.execute("INSERT INTO jobs (data) VALUES (?)", (json.dumps(job),))
        self.db.commit()
        job["id"] = cur.lastrowid
        if torrent_bytes is not None:
            path = TORRENT_DIR / f"{job['id']}.torrent"
            path.write_bytes(torrent_bytes)
            job["torrent_file"] = str(path)
        self.jobs[job["id"]] = job
        self._write(job)
        if job["state"] == "queued":
            self.schedule(job)
        self._emit(job, "added")
        return job

    def schedule(self, job: dict):
        jid = job["id"]
        if jid in self.tasks and not self.tasks[jid].done():
            return
        self.flags[jid] = threading.Event()
        self.tasks[jid] = asyncio.create_task(self._run(job))

    def choose_provider(self, jid: int, provider: str, remember: bool):
        job = self.jobs[jid]
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider")
        if remember:
            self.settings.update({"ask_provider": False, "default_provider": provider})
        job.update(provider=provider, state="queued", message="")
        self._write(job)
        self.schedule(job)

    async def _stop(self, jid: int):
        if jid in self.flags:
            self.flags[jid].set()
        task = self.tasks.get(jid)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

    async def pause(self, jid: int):
        job = self.jobs[jid]
        if job["state"] in ACTIVE:
            await self._stop(jid)
            job.update(state="paused", speed=0, message="Paused")
            self._write(job)

    def resume(self, jid: int):
        job = self.jobs[jid]
        if job["state"] in ("paused", "error"):
            if job["state"] == "error" and job.get("remote_failed"):
                job["remote_id"], job["remote_failed"] = None, False
            job.update(state="queued" if job.get("provider") or job["kind"] not in NEEDS_PROVIDER else "awaiting_provider",
                       message="")
            self._write(job)
            if job["state"] == "queued":
                self.schedule(job)

    def retry_failed(self) -> int:
        failed = [j["id"] for j in self.jobs.values() if j["state"] == "error"]
        for jid in failed:
            self.resume(jid)
        return len(failed)

    def pick_files(self, jid: int, names: list[str]):
        job = self.jobs[jid]
        if job["state"] != "awaiting_files":
            raise ValueError("This download isn't waiting for a file choice")
        offered = {c["name"] for c in job.get("choices") or []}
        names = [n for n in names if n in offered]
        if not names:
            raise ValueError("Pick at least one file")
        job.update(picked=names, state="queued", message="")
        self._write(job)
        self.schedule(job)

    def sort_plan(self, job: dict) -> list[tuple[Path, Path]]:
        root = Path(self.settings["download_dir"])
        files = [Path(p) for p in job.get("outputs", []) if Path(p).is_file()]
        return organize.plan(job["name"], files, root)

    def sort_job(self, job: dict) -> str:
        """Rename & sort a finished job's files. Returns a short description for the card."""
        root = Path(self.settings["download_dir"])
        moves = self.sort_plan(job)
        if not moves:
            return "Couldn't tell what this is - left the files as they are"
        cleanup = Path(job["folder"]) if job.get("folder") else None
        done = organize.apply(moves, root, cleanup)
        job["outputs"] = [str(done.get(Path(p), Path(p))) for p in job.get("outputs", [])]
        job["sorted"] = True
        videos = [v for k, v in done.items() if k.suffix.lower() in organize.VIDEO]
        folder = organize.common_folder(videos or list(done.values()))
        if folder:
            job["folder"] = str(folder)
            try:
                return f"Sorted into {folder.resolve().relative_to(root.resolve())}"
            except ValueError:
                return f"Sorted into {folder}"
        return "Sorted"

    async def remove(self, jid: int, delete_files: bool = False):
        job = self.jobs.get(jid)
        if not job:
            return
        await self._stop(jid)
        if job.get("remote_id") and job.get("provider") and job["state"] != "done":
            try:
                await PROVIDERS[job["provider"]].delete(job["remote_id"])
            except Exception:
                pass
        if delete_files:
            self._delete_outputs(job)
        if job.get("torrent_file"):
            Path(job["torrent_file"]).unlink(missing_ok=True)
        self.jobs.pop(jid, None)
        self.db.execute("DELETE FROM jobs WHERE id=?", (jid,))
        self.db.commit()

    def _delete_outputs(self, job: dict):
        root = Path(self.settings["download_dir"]).resolve()
        for p in job.get("outputs", []):
            for cand in (Path(p), Path(p + ".part")):
                try:
                    rp = cand.resolve()
                    if root in rp.parents and rp.is_file():
                        rp.unlink()
                except OSError:
                    pass
        folder = job.get("folder")
        if folder:
            fp = Path(folder).resolve()
            if root in fp.parents and fp.is_dir():
                shutil.rmtree(fp, ignore_errors=True)

    async def clear_finished(self):
        for jid in [j["id"] for j in self.jobs.values() if j["state"] in ("done", "canceled")]:
            await self.remove(jid)

    # ------------------------------------------------------------ cache check
    async def check_cache(self, pids: list[str], magnets: list[str]) -> dict[str, dict[str, bool | None]]:
        """Is each magnet already cached at each provider (= downloads instantly)? Results are remembered a while."""
        hashes: dict[str, str] = {}
        for m in magnets:
            h = magnet_hash(m)
            if h:
                hashes[h] = m
        out: dict[str, dict[str, bool | None]] = {}
        now = time.time()
        for pid in pids:
            prov = PROVIDERS[pid]
            res: dict[str, bool | None] = {}
            todo: dict[str, str] = {}
            for h, m in hashes.items():
                hit = self._cache.get((pid, h))
                if hit and now - hit[0] < CACHE_TTL[hit[1]]:
                    res[h] = hit[1]
                else:
                    todo[h] = m
            if todo:
                lock = self._probe_locks.setdefault(pid, asyncio.Semaphore(2))
                if prov.cache_check == "lookup":
                    async with lock:
                        got = await prov.cached(todo)
                else:   # each probe adds + deletes a torrent: a few at a time, politely
                    async def one(h, m, prov=prov, lock=lock):
                        async with lock:
                            return await prov.cached({h: m})
                    got = {}
                    for part in await asyncio.gather(*(one(h, m) for h, m in todo.items())):
                        got |= part
                for h, v in got.items():
                    self._cache[(pid, h)] = (time.time(), v)
                    res[h] = v
            out[pid] = res
        if len(self._cache) > 5000:
            self._cache.clear()
        return out

    # --------------------------------------------------------------- pipeline
    def _check(self, job: dict):
        if self.flags.get(job["id"]) and self.flags[job["id"]].is_set():
            raise Canceled()

    def _tracker(self, job: dict, rel: str, size: int):
        entry = {"name": rel, "size": size, "done": 0}
        job["files"].append(entry)

        def progress(done: int, total: int):
            entry["done"] = done
            if total:
                entry["size"] = total
            job["done"] = sum(f["done"] for f in job["files"])
            job["size"] = max(job.get("planned", 0), sum(f["size"] for f in job["files"]))
            self._check(job)
        return progress

    async def _run(self, job: dict):
        jid = job["id"]
        attempt = 0
        try:
            while True:
                try:
                    await self._attempt(job)
                    break
                except (asyncio.CancelledError, Canceled, AwaitingFiles):
                    raise
                except Exception as e:  # noqa: BLE001 - every failure becomes a job error
                    msg = vault.scrub(str(e) or e.__class__.__name__)
                    if isinstance(e, httpx.HTTPStatusError):
                        msg = f"HTTP {e.response.status_code} from {e.request.url.host}"
                    tries = max(0, int(self.settings["auto_retry"]))
                    if attempt < tries and transient(e, job["kind"]):
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        attempt += 1
                        log.info("job %s: %s - retry %s/%s in %ss", jid, msg, attempt, tries, delay)
                        job.update(state="queued", speed=0,
                                   message=f"Portal flickered ({msg}) - retry {attempt}/{tries} in {delay}s")
                        self._write(job)
                        await asyncio.sleep(delay)
                        self._check(job)
                        continue
                    log.warning("job %s failed: %s", jid, msg)
                    job.update(state="error", speed=0,
                               message=f"Aw jeez: {msg}" + (f" (gave up after {attempt} retries)" if attempt else ""))
                    self._emit(job, "error")
                    return
            note = job.pop("note", "")   # e.g. "no subtitles" from the downloader
            if job.get("sort"):
                try:
                    note = "; ".join(filter(None, [await asyncio.to_thread(self.sort_job, job), note]))
                except Exception as e:  # a failed rename never fails the download
                    log.warning("job %s: sorting failed: %s", jid, e)
                    note = f"Downloaded, but sorting failed: {e}"
            job.update(state="done", speed=0, message=note, finished=time.time())
            self._emit(job, "done")
        except AwaitingFiles:
            self._emit(job, "files")
        except (asyncio.CancelledError, Canceled):
            if job["state"] not in ("paused", "canceled"):
                job["state"] = "paused"
            job["speed"] = 0
        finally:
            self._write(job)

    async def _attempt(self, job: dict):
        kind = job["kind"]
        if kind == "torrent_url":
            await self._fetch_torrent(job)
            kind = job["kind"]
        if kind == "torrent":
            await self._torrent(job)
        elif kind == "debrid":
            await self._debrid_link(job)
        else:
            await self._local(job, getattr(self, f"_{kind}"))

    async def _local(self, job: dict, worker):
        """Run the local-transfer phase inside the concurrency limit."""
        job.update(state="queued", message="Waiting for a free portal")
        async with self.sem:
            self._check(job)
            job.update(state="downloading", message="", files=[], outputs=[], done=0)
            await worker(job)

    async def _fetch_torrent(self, job: dict):
        r = await self.http.get(job["source"])
        r.raise_for_status()
        path = TORRENT_DIR / f"{job['id']}.torrent"
        path.write_bytes(r.content)
        name, _ = bencode.torrent_info(r.content)
        job.update(kind="torrent", torrent_file=str(path), name=name or job["name"])

    async def _torrent(self, job: dict):
        prov = PROVIDERS[job["provider"]]
        if not job.get("remote_id"):
            job.update(state="remote", message=f"Opening a portal to {prov.name}")
            if job.get("torrent_file"):
                data = Path(job["torrent_file"]).read_bytes()
                job["remote_id"] = await prov.add_torrent(data, Path(job["torrent_file"]).name)
            else:
                job["remote_id"] = await prov.add_magnet(job["source"])
            self._write(job)
        delay = 2
        while True:
            self._check(job)
            st = await prov.status(job["remote_id"])
            if st.name:
                job["name"] = st.name
            if st.state == "error":
                job["remote_failed"] = True
                raise ProviderError(st.message)
            if st.state == "ready":
                break
            job.update(state="remote", remote_progress=round(st.progress, 1), size=st.size,
                       message=st.message or f"{prov.name} is cooking it up")
            await asyncio.sleep(delay)
            delay = min(delay + 1, 10)

        items = st.items
        if not items:
            raise ProviderError(f"{prov.name} finished but returned no files")
        items = self._choose(job, items)
        root = Path(self.settings["download_dir"])
        dest = root / safe_name(job["name"]) if len(items) > 1 else root
        tops = {i.name.split("/", 1)[0] for i in items if i.name}
        if len(items) > 1 and len(tops) == 1 and all(i.name and "/" in i.name for i in items):
            top = tops.pop()   # every file sits in one folder (AllDebrid includes it) - don't nest it twice
            items = [RemoteItem(i.name.split("/", 1)[1], i.size, i.handle) for i in items]
            dest = root / safe_name(top)
        job["planned"] = sum(i.size for i in items)
        job["folder"] = str(dest) if len(items) > 1 else None

        async def work(job):
            for it in items:
                self._check(job)
                res = await prov.resolve(it.handle)
                rel = it.name or res.filename
                if len(items) == 1:
                    rel = rel.split("/")[-1]
                job["outputs"].append(str(dest / safe_relpath(rel)))   # known up front so "delete files" finds the .part
                path = await download(self.http, res.url, dest, rel, self._tracker(job, rel, it.size or res.size), it.size)
                job["outputs"][-1] = str(path)

        await self._local(job, work)
        if self.settings["cleanup_remote"]:
            try:
                await prov.delete(job["remote_id"])
            except Exception:
                pass

    def _choose(self, job: dict, items: list) -> list:
        """Which of a torrent's files to download. Asks the user (raises AwaitingFiles) when it has several."""
        if len(items) <= 1 or not self.settings["pick_files"] or any(not i.name for i in items):
            return items
        picked = job.get("picked")
        if picked is not None:
            names = set(picked)
            chosen = [i for i in items if i.name in names]
            if chosen:
                return chosen
        job.update(state="awaiting_files", speed=0, message="Pick the files you want, Morty",
                   choices=[{"name": i.name, "size": i.size} for i in items])
        self._write(job)
        raise AwaitingFiles()

    async def _debrid_link(self, job: dict):
        prov = PROVIDERS[job["provider"]]
        job.update(state="remote", message=f"Unrestricting via {prov.name}")
        try:
            resolved = await prov.unrestrict(job["source"])
        except ProviderError:
            if mega.is_mega(job["source"]):
                job["message"] = f"{prov.name} refused it - going native MEGA"
                await self._local(job, self._mega)
                return
            raise
        root = Path(self.settings["download_dir"])
        if len(resolved) == 1:
            job["name"] = resolved[0].filename
        job["planned"] = sum(r.size for r in resolved)

        async def work(job):
            for r in resolved:
                job["outputs"].append(str(root / safe_relpath(r.filename)))
                path = await download(self.http, r.url, root, r.filename, self._tracker(job, r.filename, r.size), r.size)
                job["outputs"][-1] = str(path)

        await self._local(job, work)

    async def _mega(self, job: dict):
        name, files = await mega.listing(self.http, job["source"])
        job["name"] = name
        root = Path(self.settings["download_dir"])
        dest = root / safe_name(name) if len(files) > 1 else root
        job["planned"] = sum(f.size for f in files)
        job["folder"] = str(dest) if len(files) > 1 else None
        for f in files:
            job["outputs"].append(str(dest / f.path))
            await mega.download_file(self.http, f, dest, self._tracker(job, f.path, f.size))

    async def _gofile(self, job: dict):
        name, files, token = await gofile.listing(self.http, job["source"])
        job["name"] = name
        root = Path(self.settings["download_dir"])
        dest = root / safe_name(name) if len(files) > 1 else root
        job["planned"] = sum(f.size for f in files)
        job["folder"] = str(dest) if len(files) > 1 else None
        for f in files:
            job["outputs"].append(str(dest / safe_relpath(f.path)))
            path = await download(self.http, f.url, dest, f.path, self._tracker(job, f.path, f.size), f.size,
                                  headers=gofile.headers(token))
            job["outputs"][-1] = str(path)

    async def _direct(self, job: dict):
        root = Path(self.settings["download_dir"])

        def progress(done, total):
            job["done"], job["size"] = done, total
            self._check(job)

        path = await download(self.http, job["source"], root, None, progress)
        job.update(name=path.name, outputs=[str(path)])

    async def _ytdlp(self, job: dict):
        root = Path(self.settings["download_dir"])
        flag = self.flags[job["id"]]
        ff = ytdl.ffmpeg_location(self.settings["ffmpeg_path"])
        if not ff:
            job["message"] = "ffmpeg not found - using single-file formats only (Settings > Media tools)"
        seen: dict[str, dict] = {}

        def on_progress(d: dict):
            fn = d.get("filename") or d.get("tmpfilename") or "?"
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            seen[fn] = {"name": Path(fn).name, "size": int(total), "done": int(d.get("downloaded_bytes") or 0)}
            job["files"] = list(seen.values())
            job["done"] = sum(f["done"] for f in seen.values())
            job["size"] = max(sum(f["size"] for f in seen.values()), job["done"])
            if d.get("status") == "finished":
                job["message"] = "Post-processing (ffmpeg)..."

        def on_title(t: str):
            job["name"] = t

        subs = None
        if self.settings["ytdlp_subs"] and (langs := ytdl.sub_langs(self.settings["ytdlp_sub_langs"])):
            subs = {"langs": langs, "auto": self.settings["ytdlp_auto_subs"]}
        notes: list[str] = []
        try:
            out = await asyncio.to_thread(ytdl.run, job["source"], root, self.settings["ytdlp_format"],
                                          job.get("audio_only", False), self.settings["ytdlp_audio_format"],
                                          ff, on_progress, flag, on_title, subs, notes)
        except Exception as e:
            if flag.is_set():
                raise
            msg = str(e).replace("ERROR: ", "", 1) or e.__class__.__name__
            if not ytdl.js_runtimes() and "youtu" in job["source"].lower():
                msg += " - YouTube needs Deno: install it in Settings > Media tools"
            else:
                msg += " - if this keeps happening, update yt-dlp in Settings > Media tools"
            raise RuntimeError(msg) from None
        if flag.is_set():
            raise Canceled()
        job["outputs"] = [str(p) for p in out]
        job["done"] = job["size"] = sum(p.stat().st_size for p in out if p.exists()) or job["size"]
        if notes:
            job["note"] = "; ".join(notes)
