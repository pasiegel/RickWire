"""Magic Wormhole end to end through the real job queue (temp data folder, public relay, needs internet).

Sends a folder and a file from RickWire to itself, checks they arrive intact in the download folder (no overwrite:
"name (2)"), that the size limit refuses a big offer, that a used code fails cleanly, and the code finder.
    .venv/Scripts/python tests/dev/t_wormhole.py
"""
import asyncio, hashlib, json, os, sys, tempfile, time
from pathlib import Path

home = tempfile.mkdtemp(prefix="rwwh_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rickwire.config import CONFIG_FILE, Settings  # noqa: E402
dl = Path(home, "downloads")
CONFIG_FILE.write_text(json.dumps({"download_dir": str(dl), "onboarded": True, "wormhole_max_gb": 1}))
from rickwire import manager as mgr  # noqa: E402
from rickwire.manager import JobManager  # noqa: E402
mgr.WORMHOLE_WAIT = 25   # don't wait 10 minutes for the "used code" case
from rickwire.sources.wormhole import find_codes  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


async def wait(pred, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.3)
    return False


async def transfer(m: JobManager, path: Path):
    send = await m.add_send(str(path))
    await wait(lambda: send.get("wormhole_code"), 60)
    code = send.get("wormhole_code")
    if not code:
        return send, None
    [recv] = await m.add_text(f"wormhole receive {code}")
    await wait(lambda: recv["state"] in ("done", "error") and send["state"] in ("done", "error"), 120)
    return send, recv


async def main():
    print("== code finder")
    check("bare code", find_codes(" 7-crossover-clockwork ") == ["7-crossover-clockwork"])
    check("uri", find_codes("grab wormhole-transfer:12-apple-banana-cherry please") == ["12-apple-banana-cherry"])
    check("command", find_codes("wormhole receive 3-aardvark-zulu") == ["3-aardvark-zulu"])
    check("not in normal text", find_codes("see you on 3-day-weekend at the lake") == [])
    check("not a magnet", find_codes("magnet:?xt=urn:btih:abc") == [])

    m = JobManager(Settings())
    await m.start()
    src = Path(home, "src")
    (src / "Show Folder" / "sub").mkdir(parents=True)
    (src / "Show Folder" / "ep1.bin").write_bytes(os.urandom(2_000_000))
    (src / "Show Folder" / "sub" / "notes.txt").write_text("wubba lubba")
    (src / "single.bin").write_bytes(os.urandom(300_000))

    print("== folder")
    send, recv = await transfer(m, src / "Show Folder")
    check("send got a code", bool(recv), send.get("message"))
    if recv:
        check("receive done", recv["state"] == "done", recv["message"])
        check("send done", send["state"] == "done", send["message"])
        got = dl / "Show Folder"
        check("folder arrived", (got / "ep1.bin").exists() and (got / "sub" / "notes.txt").exists())
        check("content intact", got.exists() and sha(got / "ep1.bin") == sha(src / "Show Folder" / "ep1.bin"))
        check("job outputs/folder", recv.get("folder") == str(got) and len(recv["outputs"]) == 2, recv.get("folder"))
        check("no staging left", not any(p.name.startswith(".wormhole-") for p in dl.iterdir()))
        check("code cleared after send", send.get("wormhole_code") is None)

    print("== same file twice (no overwrite)")
    for _ in range(2):
        send, recv = await transfer(m, src / "single.bin")
    check("second copy renamed", (dl / "single.bin").exists() and (dl / "single (2).bin").exists(),
          ", ".join(sorted(p.name for p in dl.iterdir())))
    check("renamed copy intact", (dl / "single (2).bin").exists() and sha(dl / "single (2).bin") == sha(src / "single.bin"))

    print("== used code")
    used = recv and recv["source"]
    if used:
        [again] = await m.add_text(used)
        await wait(lambda: again["state"] in ("done", "error"), 60)
        check("reused code gives up with a clear message", again["state"] == "error" and "Nobody sent" in again["message"],
              again["message"])
        await m.remove(again["id"])

    print("== size limit (1 GB)")
    m.settings.update({"wormhole_max_gb": 0})
    m.settings.data["wormhole_max_gb"] = 0.001   # 1 MB, set directly (the API only takes whole GB)
    send, recv = await transfer(m, src / "Show Folder")
    if recv:
        check("big offer refused", recv["state"] == "error" and "limit" in recv["message"], recv["message"])
        check("sender told", send["state"] == "error", send["message"])

    print("== cancel a waiting send")
    send = await m.add_send(str(src / "single.bin"))
    await wait(lambda: send.get("wormhole_code"), 60)
    print("   pausing", flush=True)
    await asyncio.wait_for(m.pause(send["id"]), 20)
    check("paused", send["state"] == "paused")
    print("   removing", flush=True)
    await asyncio.wait_for(m.remove(send["id"]), 20)
    check("removed", send["id"] not in m.jobs)

    ok = sum(results)
    print(f"RESULT: {ok}/{len(results)} passed", flush=True)
    os._exit(0 if ok == len(results) else 1)


asyncio.run(main())
