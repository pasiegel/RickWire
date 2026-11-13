"""Check a BUILT RickWire can do Magic Wormhole: its own `--wormhole-worker` sends to the source-tree worker and
receives from it, through the public relay (needs internet). Catches missing modules/data files in the frozen app.

    python tests/check_wormhole_worker.py dist/RickWire/RickWire.exe                     (Windows)
    python tests/check_wormhole_worker.py dist/RickWire.app/Contents/MacOS/RickWire      (Mac)
"""
import asyncio
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rickwire.sources import wormhole as W  # noqa: E402

APP = [str(Path(sys.argv[1]).resolve()), "--wormhole-worker"]
SRC = [sys.executable, "-m", "rickwire.wormhole_worker"]
tmp = Path(tempfile.mkdtemp(prefix="rwwhchk_"))
payload = tmp / "payload.bin"
payload.write_bytes(os.urandom(1_000_000))


async def transfer(sender, receiver, label):
    stage = tmp / f"stage-{len(list(tmp.iterdir()))}"
    stage.mkdir()
    code = {}
    W.worker_cmd = lambda: sender
    send = asyncio.create_task(W.run({"op": "send", "path": str(payload)},
                                     lambda e: code.setdefault("c", e.get("code")) if e["ev"] == "code" else None))
    for _ in range(120):
        if "c" in code or send.done():
            break
        await asyncio.sleep(0.5)
    if "c" not in code:
        ok, tail, _ = await send
        print(f"FAIL {label}: the sender never got a code\n{tail[-1500:]}")
        return False
    W.worker_cmd = lambda: receiver
    ok_r, tail_r, _ = await asyncio.wait_for(W.run({"op": "receive", "code": code["c"], "dest": str(stage)}, lambda e: None), 180)
    ok_s, tail_s, _ = await asyncio.wait_for(send, 60)
    got = stage / payload.name
    same = got.exists() and hashlib.sha256(got.read_bytes()).digest() == hashlib.sha256(payload.read_bytes()).digest()
    print(f"{'OK  ' if ok_s and ok_r and same else 'FAIL'} {label}: sent={ok_s} received={ok_r} intact={same}", flush=True)
    if not (ok_s and ok_r and same):
        print("  sender:", tail_s[-1500:], "\n  receiver:", tail_r[-1500:])
    return ok_s and ok_r and same


async def main():
    a = await transfer(APP, SRC, "app sends")
    b = await transfer(SRC, APP, "app receives")
    shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT:", "PASS" if a and b else "FAIL")
    return 0 if a and b else 1


os.chdir(ROOT)
sys.exit(asyncio.run(main()))
