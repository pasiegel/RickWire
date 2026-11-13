"""UI test for Magic Wormhole (headless Edge/Chrome over CDP, temp data folder, public relay).
Desktop: Send… (the file picker is stubbed to return a test folder), the code dialog with QR, the 🔑 button,
the settings section. Phone: pasting the code into the add box receives it; the phone never sees the send's code.
    .venv/Scripts/python tests/dev/ui_wormhole.py <screenshot folder>
"""
import asyncio, base64, json, os, subprocess, sys, tempfile, time, pathlib
home = tempfile.mkdtemp(prefix="rwuiwh_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from rickwire.config import CONFIG_FILE, Settings  # noqa: E402
dl = pathlib.Path(home, "downloads")
CONFIG_FILE.write_text(json.dumps({"download_dir": str(dl), "local_port": 23760, "lan_port": 23761, "lan_enabled": True,
                                   "lan_https": False, "onboarded": True}))
from rickwire.app import Hooks  # noqa: E402
from rickwire.server import Backend  # noqa: E402
import httpx, websockets  # noqa: E402

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="rwshots_"))
OUT.mkdir(parents=True, exist_ok=True)
src = pathlib.Path(home, "to send", "Holiday Photos")
src.mkdir(parents=True)
(src / "beach.jpg").write_bytes(os.urandom(400_000))
(src / "notes.txt").write_text("portal fluid: 3 cans")
hooks = Hooks()
hooks.pick_send = lambda folder, start: str(src)   # stands in for the native file/folder dialog
b = Backend(Settings(), hooks); assert b.start(), b.error
url = b.auth.new_bootstrap_url(23760)
prof = tempfile.mkdtemp(prefix="edge_")
browser = os.environ.get("RW_BROWSER") or next(p for p in [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/chromium"] if os.path.exists(p))
edge = subprocess.Popen([browser, "--headless=new", "--remote-debugging-port=9342", f"--user-data-dir={prof}",
                         "--window-size=1200,1000", "about:blank"])
for _ in range(50):
    try:
        tabs = httpx.get("http://127.0.0.1:9342/json").json(); break
    except Exception:
        time.sleep(0.2)
ws_url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


async def main():
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        n = 0

        async def cmd(method, **params):
            nonlocal n; n += 1; my = n
            await ws.send(json.dumps({"id": my, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == my:
                    return m.get("result", m)

        async def js(e):
            r = await cmd("Runtime.evaluate", expression=e, awaitPromise=True, returnByValue=True)
            if "exceptionDetails" in r:
                return f"JS ERROR: {r['exceptionDetails'].get('exception', {}).get('description')}"
            return r.get("result", {}).get("value", r)

        async def shot(name):
            r = await cmd("Page.captureScreenshot", format="png")
            (OUT / name).write_bytes(base64.b64decode(r["data"]))

        async def wait(expr, t=30):
            for _ in range(t * 4):
                if await js(expr) is True:
                    return True
                await asyncio.sleep(0.25)
            return False

        await cmd("Page.enable"); await cmd("Runtime.enable")
        await cmd("Emulation.setDeviceMetricsOverride", width=1200, height=900, deviceScaleFactor=1, mobile=False)
        await cmd("Page.navigate", url=url); await asyncio.sleep(2.5)
        check("Send… button visible on the desktop", await js("!document.querySelector('#whSendBtn').classList.contains('hidden')"))
        await js("document.querySelector('#whSendBtn').click()")
        check("send dialog opens", await wait("document.querySelector('#whPickDlg').open === true", 5))
        await shot("wh1-send-dialog.png")
        await js("document.querySelector('#whFolder').click()")
        check("code dialog opens with a code", await wait("document.querySelector('#whCodeDlg').open && /^\\d+-[a-z]+-[a-z]+$/.test(document.querySelector('#whCode').textContent)", 40))
        code = await js("document.querySelector('#whCode').textContent")
        check("QR image shown", await js("document.querySelector('#whQr').naturalWidth > 0"))
        check("command shown", await js("document.querySelector('#whCmd').textContent") == f"wormhole receive {code}")
        await shot("wh2-code-dialog.png")
        await js("document.querySelector('#whCodeDlg').close()")
        check("card shows the code + 🔑", await wait(
            "[...document.querySelectorAll('.card.job')].some(c => c.textContent.includes('Waiting for the receiver') && "
            "!c.querySelector('[data-a=code]').classList.contains('hidden'))", 10))
        await shot("wh3-queue-waiting.png")

        b.auth.start_pairing()
        phone = httpx.Client(base_url="http://127.0.0.1:23761", headers={"X-RickWire": "1"})
        phone.post("/api/pair/complete", json={"code": b.auth.pair_code, "name": "Test Pixel"})
        pj = phone.get("/api/jobs").json()["jobs"]
        sendjob = next(j for j in pj if j["kind"] == "wormhole_send")
        check("phone doesn't see the code or the local path", not sendjob.get("wormhole_code") and sendjob["source"] == "Holiday Photos",
              f"{sendjob.get('wormhole_code')} / {sendjob['source']}")
        check("phone can't read the code endpoint", phone.get(f"/api/jobs/{sendjob['id']}/wormhole").status_code == 403)
        check("phone can't start a send", phone.post("/api/wormhole/send", json={"path": str(src)}).status_code == 403)
        r = phone.post("/api/jobs", json={"text": code})
        check("phone pastes the code -> receive job", r.status_code == 200 and r.json()[0]["kind"] == "wormhole", r.text[:120])
        done = await wait("[...document.querySelectorAll('.card.job')].filter(c => c.classList.contains('done')).length >= 2", 90)
        check("both cards finish on the desktop", done)
        check("files arrived", (dl / "Holiday Photos" / "beach.jpg").exists() and (dl / "Holiday Photos" / "notes.txt").exists())
        await shot("wh4-queue-done.png")
        check("📤 on the received download", await js(
            "[...document.querySelectorAll('.card.job.done')].some(c => c.textContent.includes('Holiday Photos') && "
            "!c.querySelector('[data-a=whsend]').classList.contains('hidden'))"))

        await js("document.querySelector('#settingsBtn').click()"); await asyncio.sleep(1.5)
        await js("document.querySelector('#s_whmax').value = '7'; document.querySelector('#s_whrelay').value = 'not a url'")
        await js("document.querySelector('#setSave') ? document.querySelector('#setSave').click() : saveSettings()")
        await asyncio.sleep(1.2)
        check("bad relay refused", "relay" in str(await js("document.querySelector('#setMsg').textContent")).lower(),
              await js("document.querySelector('#setMsg').textContent"))
        await js("document.querySelector('#s_whrelay').value = ''")
        await js("document.querySelector('#setSave') ? document.querySelector('#setSave').click() : saveSettings()")
        await asyncio.sleep(1.2)
        check("limit saved", b.settings["wormhole_max_gb"] == 7, b.settings["wormhole_max_gb"])
        await js("[...document.querySelectorAll('h3')].find(h => h.textContent.includes('Wormhole')).scrollIntoView()")
        await asyncio.sleep(0.4)
        await shot("wh5-settings.png")
    edge.terminate(); time.sleep(1)
    print(f"RESULT: {sum(results)}/{len(results)} passed  (screenshots: {OUT})", flush=True)
    os._exit(0 if all(results) else 1)


asyncio.run(main())
