"""Magic Wormhole: receive with a code (paste `7-crossover-clockwork` or `wormhole-transfer:7-crossover-clockwork`)
and send a file or folder (RickWire shows the code to give the other person).

The transfer itself runs in a separate process (rickwire/wormhole_worker.py) - see that file for why.
Both sides have to be online at the same time, and each code works once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable

log = logging.getLogger("rickwire.wormhole")

URI = "wormhole-transfer:"
_CODE = r"\d{1,4}(?:-[a-z]{2,20}){1,8}"
CODE_RE = re.compile(rf"^{_CODE}$")
_IN_TEXT = re.compile(rf"(?:wormhole-transfer:|wormhole\s+(?:receive|rx|recv|recieve)\s+(?:--\S+\s+)*)({_CODE})\b", re.I)
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
ROOT = Path(__file__).resolve().parents[2]


def find_codes(text: str) -> list[str]:
    """Codes in pasted text: wormhole-transfer: links, `wormhole receive CODE` commands, or text that is only a code."""
    text = text or ""
    found = [m.group(1).lower() for m in _IN_TEXT.finditer(text)]
    if not found and CODE_RE.fullmatch(text.strip().lower()):
        found = [text.strip().lower()]
    return list(dict.fromkeys(found))


def is_wormhole(source: str) -> bool:
    return source.lower().startswith(URI)


def code_of(source: str) -> str:
    return source[len(URI):] if is_wormhole(source) else source


def worker_cmd() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--wormhole-worker"]
    return [sys.executable, "-m", "rickwire.wormhole_worker"]


def friendly_error(stderr_tail: str, events: dict) -> str:
    t = stderr_tail
    if "too_big" in events:
        e = events["too_big"]
        return (f"Refused: {e['name']} is {_gb(e['size'])}, over your {_gb(e['limit'])} limit "
                "(Settings > Downloads > Magic Wormhole)")
    if "WrongPasswordError" in t or "Key confirmation failed" in t or "typed the code wrong" in t:
        return "Wrong code - check it with the other person (each code works only once)"
    if "crowded" in t.lower() or "nameplate" in t.lower():
        return "That code is already in use or has expired - ask for a new one"
    if "insufficient free space" in t:
        return "Not enough free disk space for this transfer"
    if "ServerConnectionError" in t or "relay URL was" in t:
        return "Couldn't reach the wormhole relay server - check your internet connection (or the relay in Settings)"
    if "UnsendableFileError" in t:
        return "That file or folder can't be sent (unreadable or a special file)"
    if "transfer rejected" in t.lower():
        return "The other side said no"
    m = re.search(r"TransferError: (.+)", t)
    if m:
        return f"Transfer failed: {m.group(1).strip()}"
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    errs = [x for x in lines if x.startswith("ERROR")]
    return (errs or lines or ["the wormhole closed unexpectedly"])[-1][:300]


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


async def run(spec: dict, on_event: Callable[[dict], None]) -> tuple[bool, str, dict]:
    """Run one transfer. on_event gets each event dict. -> (ok, stderr tail, last event of each kind).
    Cancelling the calling task kills the transfer."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = await asyncio.create_subprocess_exec(
        *worker_cmd(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=str(ROOT) if not getattr(sys, "frozen", False) else None, env=env, creationflags=NO_WINDOW)
    proc.stdin.write((json.dumps(spec) + "\n").encode())
    await proc.stdin.drain()
    proc.stdin.close()
    events: dict[str, dict] = {}
    err: list[str] = []

    async def read_err():
        async for line in proc.stderr:
            s = line.decode("utf-8", "replace").rstrip()
            if s:
                err.append(s)
                del err[:-60]

    err_task = asyncio.create_task(read_err())
    try:
        async for line in proc.stdout:
            s = line.decode("utf-8", "replace").strip()
            if not s.startswith("@@RW "):
                continue
            try:
                ev = json.loads(s[5:])
            except ValueError:
                continue
            events[ev.get("ev", "")] = ev
            on_event(ev)
        rc = await proc.wait()
        await asyncio.wait_for(err_task, 5)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        err_task.cancel()
        raise
    tail = "\n".join(err)
    if rc and tail:
        log.info("wormhole exited %s: %s", rc, tail.splitlines()[-1][:200])
    return rc == 0 and "finished" in events, tail, events
