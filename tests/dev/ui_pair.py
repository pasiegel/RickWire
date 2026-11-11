import asyncio, base64, json, os, subprocess, sys, tempfile, time, pathlib
home = tempfile.mkdtemp(prefix="rwuir_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from rickwire.config import CONFIG_FILE, Settings
CONFIG_FILE.write_text(json.dumps({"download_dir": home, "local_port": 23752, "lan_port": 23753, "lan_enabled": True,
                                   "lan_https": False, "onboarded": True}))
from rickwire.app import Hooks
from rickwire.server import Backend
from rickwire import webauth
import httpx, websockets

OUT = pathlib.Path(sys.argv[1])
b = Backend(Settings(), Hooks()); assert b.start(), b.error
url = b.auth.new_bootstrap_url(23752)
prof = tempfile.mkdtemp(prefix="edge_")
edge = subprocess.Popen([__import__("os").environ.get("RW_BROWSER") or next(p for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/usr/bin/google-chrome", "/usr/bin/chromium"] if __import__("os").path.exists(p)), "--headless=new",
                         "--remote-debugging-port=9339", f"--user-data-dir={prof}", "--window-size=1200,1000", "about:blank"])
for _ in range(50):
    try: tabs = httpx.get("http://127.0.0.1:9339/json").json(); break
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

        r = await js("fetch('/api/auth/account',{method:'PUT',headers:{'X-RickWire':'1','Content-Type':'application/json'},body:JSON.stringify({username:'rick',password:'pickle-rick-137'})}).then(r=>r.status)")
        r2 = await js("fetch('/api/settings',{method:'PUT',headers:{'X-RickWire':'1','Content-Type':'application/json'},body:JSON.stringify({remote_enabled:true,trusted_proxies:['127.0.0.1'],public_hosts:['rw.example.com']})}).then(r=>r.status)")
        print("setup", r, r2)
        await js("document.querySelector('#pairBtn').click()")
        await wait("document.querySelector('#pairDlg').open === true")
        await asyncio.sleep(0.5)
        print("dialog:", await js("document.querySelector('#pairDlg .pair-box').innerText"))
        await shot("p_dialog.png")
        p = await js("fetch('/api/pair/start',{method:'POST',headers:{'X-RickWire':'1'}}).then(r=>r.json())")
        link = p["url"].replace("https://rw.example.com", "http://127.0.0.1:23753")
        await cmd("Network.clearBrowserCookies")
        await cmd("Network.setExtraHTTPHeaders", headers={"X-Forwarded-For": "203.0.113.77", "X-Forwarded-Proto": "https"})
        await cmd("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
        await cmd("Page.navigate", url=link)
        ok = await wait("location.pathname === '/' && !!document.querySelector('#jobs')", 15)
        await asyncio.sleep(1.5)
        print("phone after QR:", await js("location.href"), "| conn:", await js("document.querySelector('#conn').textContent"))
        await shot("p_phone.png")
    edge.terminate(); time.sleep(1)

asyncio.run(main())
b.stop()
