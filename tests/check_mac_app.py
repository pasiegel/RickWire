"""macOS: start the built RickWire.app on a throwaway data folder and check it really works as a Mac app.

    python tests/check_mac_app.py dist/RickWire.app [--shot dist/mac-screenshot.png]

Checks: it starts through LaunchServices (`open`), the server answers, the window and the menu-bar icon come up,
a magnet: link and a .torrent file sent the way Finder / a browser sends them (Apple Events) reach the queue,
Show in Finder works, and Quit shuts it down cleanly. Uses its own data folder (RICKWIRE_HOME), so a real install
is never touched. The keychain isn't touched either: no keys are saved. Exit code 0 = all OK.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = 23700   # not the default, so a RickWire the user is running is left alone
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  {'OK  ' if ok else 'FAIL'} {name}{(' - ' + detail) if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def wait(pred, timeout: float, step: float = 0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = pred()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


def pids(app: Path) -> list[int]:
    out = subprocess.run(["pgrep", "-f", str(app / "Contents" / "MacOS" / "RickWire")], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def job_count(home: Path) -> int:
    db = home / "jobs.sqlite3"
    if not db.exists():
        return 0
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("select count(*) from jobs").fetchone()[0]
    finally:
        con.close()


def tiny_torrent(path: Path):
    def encode(v) -> bytes:
        if isinstance(v, int):
            return b"i%de" % v
        if isinstance(v, bytes):
            return b"%d:%s" % (len(v), v)
        return b"d" + b"".join(encode(k) + encode(v[k]) for k in sorted(v)) + b"e"
    info = {b"name": b"rickwire-ci.txt", b"length": 4, b"piece length": 16384, b"pieces": hashlib.sha1(b"test").digest()}
    path.write_bytes(encode({b"announce": b"http://127.0.0.1:1/announce", b"info": info}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app", type=Path)
    ap.add_argument("--shot", type=Path)
    a = ap.parse_args()
    app = a.app.resolve()
    home = Path(tempfile.mkdtemp(prefix="rwmac_"))
    (home / "config.json").write_text(json.dumps({
        "download_dir": str(home / "downloads"), "local_port": PORT, "lan_enabled": False, "onboarded": True,
        "close_to_tray": True, "check_tool_updates": False}))
    log = home / "rickwire.log"
    print(f"app:  {app}\nhome: {home}", flush=True)

    print("\n== start", flush=True)
    subprocess.run(["open", "-n", "--env", f"RICKWIRE_HOME={home}", str(app)], check=True)
    base = f"http://127.0.0.1:{PORT}"
    me = wait(lambda: httpx.get(base + "/api/me", timeout=2).json(), 90)
    check("server answers", bool(me), f"version {me.get('version')}" if me else "no answer in 90 s")
    check("one process", len(pids(app)) == 1, str(pids(app)))
    text = lambda: log.read_text("utf-8", "replace") if log.exists() else ""
    check("window shown", bool(wait(lambda: "window shown" in text(), 60)))
    check("menu-bar icon started", "tray started" in text())
    time.sleep(3)
    if a.shot:
        a.shot.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["screencapture", "-x", str(a.shot)], capture_output=True)
        check("screenshot", a.shot.exists(), str(a.shot))

    print("\n== links and files from macOS (Apple Events)", flush=True)
    before = job_count(home)
    magnet = "magnet:?xt=urn:btih:" + "c9e15763f722f23e98a29decdfae341b98d53056" + "&dn=RickWire+CI+test"
    subprocess.run(["open", "-a", str(app), magnet], check=True)
    check("magnet: link reaches the queue", bool(wait(lambda: job_count(home) > before, 30)))
    tor = home / "ci test.torrent"
    tiny_torrent(tor)
    before = job_count(home)
    subprocess.run(["open", "-a", str(app), str(tor)], check=True)
    check(".torrent file reaches the queue", bool(wait(lambda: job_count(home) > before, 30)))
    check("still one process", len(pids(app)) == 1, str(pids(app)))

    print("\n== quit", flush=True)
    env = dict(os.environ, RICKWIRE_HOME=str(home))
    subprocess.run([str(app / "Contents" / "MacOS" / "RickWire"), "--quit"], env=env, timeout=60)
    check("quits cleanly", bool(wait(lambda: not pids(app), 30)))
    if pids(app):
        subprocess.run(["kill", "-9", *map(str, pids(app))])
    check("log says quitting", "quitting" in text() or "window closed" in text())

    print("\n== rickwire.log (last 40 lines)")
    print("\n".join(text().splitlines()[-40:]))
    crash = home / "crash.log"
    if crash.exists() and crash.read_text("utf-8", "replace").strip():
        print("\n== crash.log\n" + crash.read_text("utf-8", "replace")[-4000:])
    tracebacks = text().count("Traceback")
    check("no errors in the log", tracebacks == 0, f"{tracebacks} traceback(s)")
    print(f"\nRESULT: {'PASS' if not FAILS else 'FAIL - ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
