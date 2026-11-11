import asyncio, base64, json, os, subprocess, sys, tempfile, time, pathlib
home = tempfile.mkdtemp(prefix="rwuir_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from rickwire.config import CONFIG_FILE, Settings
CONFIG_FILE.write_text(json.dumps({"download_dir": home, "local_port": 23754, "lan_port": 23755, "lan_enabled": True,
                                   "lan_https": False, "onboarded": True}))
from rickwire.app import Hooks
from rickwire.server import Backend
from rickwire import webauth
import httpx, websockets

OUT = pathlib.Path(sys.argv[1])
b = Backend(Settings(), Hooks()); assert b.start(), b.error
b.manager.configured_providers = lambda: ['realdebrid', 'alldebrid']
url = b.auth.new_bootstrap_url(23754)
prof = tempfile.mkdtemp(prefix="edge_")
edge = subprocess.Popen([__import__("os").environ.get("RW_BROWSER") or next(p for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/usr/bin/google-chrome", "/usr/bin/chromium"] if __import__("os").path.exists(p)), "--headless=new",
                         "--remote-debugging-port=9340", f"--user-data-dir={prof}", "--window-size=1200,1000", "about:blank"])
for _ in range(50):
    try: tabs = httpx.get("http://127.0.0.1:9340/json").json(); break
    except Exception: time.sleep(0.2)
ws_url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]


async def main():
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        n = 0
        async def cmd(method, **params):
            nonlocal n; n += 1; my = n
            await ws.send(json.dumps({"id": my, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == my: return m.get("result", m)
        async def js(e):
            r = await cmd("Runtime.evaluate", expression=e, awaitPromise=True, returnByValue=True)
            if "exceptionDetails" in r: return f"JS ERROR: {r['exceptionDetails'].get('exception', {}).get('description')}"
            return r.get("result", {}).get("value", r)
        async def shot(name):
            r = await cmd("Page.captureScreenshot", format="png", captureBeyondViewport=False); (OUT / name).write_bytes(base64.b64decode(r["data"]))
        async def wait(expr, t=20):
            for _ in range(t * 4):
                if await js(expr) is True: return True
                await asyncio.sleep(0.25)
            print("   (timed out:", expr, ")"); return False
        await cmd("Page.enable"); await cmd("Runtime.enable"); await cmd("Network.enable")
        await cmd("Page.navigate", url=url); await asyncio.sleep(2.5)

        b.auth.start_pairing()
        phone = httpx.Client(base_url="http://127.0.0.1:23755", headers={"X-RickWire": "1"})
        print("phone paired:", phone.post("/api/pair/complete", json={"code": b.auth.pair_code, "name": "phone"}).status_code)
        for rnd in (1, 2):
            r = phone.post("/api/jobs", json={"text": f"magnet:?xt=urn:btih:{'%040d' % rnd}&dn=Test{rnd}"})
            jid = [j for j in (lambda d: d["jobs"] if isinstance(d, dict) else d)(phone.get("/api/jobs").json()) if j["state"] == "awaiting_provider"][0]["id"]
            opened = await wait("document.querySelector('#pickDlg').open === true", 8)
            print(f"round {rnd}: desktop asks:", opened, await js("document.querySelector('#pickName').textContent"))
            print("  phone answers:", phone.post(f"/api/jobs/{jid}/provider", json={"provider": "alldebrid", "remember": False}).status_code)
            closed = await wait("document.querySelector('#pickDlg').open === false", 8)
            print("  desktop dialog closed by itself:", closed)
    edge.terminate(); time.sleep(1)

asyncio.run(main())
b.stop()
