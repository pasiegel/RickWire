"""Upgrade an installed RickWire with a new installer and prove nothing the user set up was lost.

    .venv\\Scripts\\python tests\\check_upgrade.py --installer dist\\RickWire-Setup-0.2.1.exe [--dir C:\\RickWire]

It upgrades *while RickWire is running* (the real-world case), then compares before/after:
settings, paired phones, queue, TLS certificates, saved API keys (as short hashes - keys are never printed),
downloaded tools, magnet/.torrent registration and the Start-menu shortcut. Finally it starts the new version
and checks that it comes up on the same data. Exit code 0 = everything intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import winreg
from pathlib import Path

VOLATILE = {"tools_checked"}   # timestamps the app itself updates
KEY_NAMES = ("realdebrid", "alldebrid", "torbox", "premiumize", "gofile")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def reg(path: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, "")[0]
    except OSError:
        return ""


def installed_dir() -> Path | None:
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F4C2B1E-9D3A-4E7B-8A51-52C1D0A7E3B9}_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return Path(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        return None


def snapshot(app: Path, data: Path) -> dict:
    import keyring
    from keyring.backends.Windows import WinVaultKeyring
    keyring.set_keyring(WinVaultKeyring())
    cfg = json.loads((data / "config.json").read_text("utf-8")) if (data / "config.json").exists() else {}
    jobs = []
    if (data / "jobs.sqlite3").exists():
        db = sqlite3.connect(f"file:{data / 'jobs.sqlite3'}?mode=ro", uri=True)
        jobs = sorted((jid, json.loads(d).get("name", "")) for jid, d in db.execute("SELECT id, data FROM jobs"))
        db.close()
    keys = {}
    for n in KEY_NAMES:
        v = keyring.get_password("RickWire", n)
        keys[n] = hashlib.sha256(v.encode()).hexdigest()[:12] if v else None
    tools = {p.name: p.stat().st_size for p in sorted((app / "tools").glob("*"))} if (app / "tools").exists() else {}
    lnk = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/RickWire.lnk"
    return {
        "settings": {k: v for k, v in cfg.items() if k not in VOLATILE},
        "paired phones": [d.get("name") for d in cfg.get("devices", [])],
        "queue": jobs,
        # only the CA matters to phones: the server certificate is re-issued (by that same CA) whenever this PC's
        # network addresses change - VPN / virtual adapters coming and going - so it's allowed to differ
        "tls certificates (CA)": {p.name: sha(p) for p in sorted((data / "tls").glob("rickwire-ca.*"))}
                                 if (data / "tls").exists() else {},
        "api keys (hashed)": keys,
        "tools": tools,
        "magnet handler": reg(r"Software\Classes\magnet\shell\open\command"),
        ".torrent handler": reg(r"Software\Classes\.torrent"),
        "start menu shortcut": lnk.exists(),
        "download folder exists": Path(cfg.get("download_dir") or Path.home() / "Downloads" / "RickWire").exists(),
    }


def app_version_from_log(data: Path, since: float) -> str | None:
    log = data / "rickwire.log"
    for _ in range(60):
        if log.exists() and log.stat().st_mtime >= since:
            lines = log.read_text("utf-8", "replace").splitlines()
            start = [l for l in lines if " starting (exe=" in l]
            if start and any("window shown" in l for l in lines[lines.index(start[-1]):]):
                return start[-1].split("RickWire ", 1)[1].split(" ", 1)[0]
        time.sleep(0.5)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--installer", required=True, type=Path)
    ap.add_argument("--dir", type=Path, default=None, help="install folder (default: from the registry)")
    a = ap.parse_args()
    app = a.dir or installed_dir()
    if not app or not (app / "RickWire.exe").exists():
        print("No installed RickWire found - pass --dir")
        return 2
    data = Path(os.environ["APPDATA"]) / "RickWire"
    exe = app / "RickWire.exe"
    print(f"install: {app}\ndata:    {data}\nupgrade: {a.installer.name}\n")

    subprocess.run([str(exe), "--quit"], timeout=60)          # quiet moment for a clean snapshot
    time.sleep(1)
    before = snapshot(app, data)

    subprocess.Popen([str(exe)])                              # upgrade over a *running* copy
    if not app_version_from_log(data, time.time() - 1):
        print("warning: old version didn't report its window before the upgrade")
    t0 = time.time()
    r = subprocess.run([str(a.installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f'/DIR={app}'], timeout=600)
    print(f"installer exit code {r.returncode} ({time.time() - t0:.0f}s)")
    time.sleep(2)
    still = subprocess.run(["tasklist", "/FI", "IMAGENAME eq RickWire.exe", "/NH"], capture_output=True, text=True).stdout
    print("old copy closed by installer:", "RickWire.exe" not in still)
    after = snapshot(app, data)

    ok = r.returncode == 0
    print()
    for k in before:
        same = before[k] == after[k]
        ok &= same
        detail = "" if same else f"\n      before: {before[k]}\n      after:  {after[k]}"
        shown = len(before[k]) if isinstance(before[k], (list, dict)) else before[k]
        print(f"  {'OK ' if same else 'CHANGED'}  {k:24} {shown if not isinstance(before[k], str) else (before[k][:60] or '(none)')}{detail}")

    launched = time.time()
    subprocess.Popen([str(exe)])
    ver = app_version_from_log(data, launched)
    print(f"\nnew version running on the same data: {ver or 'did not start'}")
    ok &= bool(ver)
    print("\nRESULT:", "PASS - upgrade kept everything" if ok else "FAIL - see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
