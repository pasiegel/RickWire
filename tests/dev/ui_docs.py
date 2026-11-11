"""Screenshots for docs/GETTING-STARTED.md from a clean, throw-away RickWire: no API keys (keyring stubbed out),
no queue history, no login. Also checks the 'spin the portal' setting."""
import asyncio, base64, json, os, subprocess, sys, tempfile, time, pathlib
home = tempfile.mkdtemp(prefix="rwdocs_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from rickwire import vault
_mem = {}
vault.get = lambda name: _mem.get(name)                        # never touch the real Credential Manager
vault.set = lambda name, value: _mem.__setitem__(name, value)
vault.delete = lambda name: _mem.pop(name, None)
from rickwire.config import CONFIG_FILE, Settings
CONFIG_FILE.write_text(json.dumps({"download_dir": home, "local_port": 23770, "lan_port": 23771, "lan_enabled": True,
                                   "lan_https": False, "onboarded": False}))
from rickwire.app import Hooks
from rickwire.server import Backend
import httpx, websockets

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
st = Settings(); b = Backend(st, Hooks()); assert b.start(), b.error
url = b.auth.new_bootstrap_url(23770)
prof = tempfile.mkdtemp(prefix="edge_")
edge = subprocess.Popen([__import__("os").environ.get("RW_BROWSER") or next(p for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "/usr/bin/google-chrome", "/usr/bin/chromium"] if __import__("os").path.exists(p)), "--headless=new",
                         "--remote-debugging-port=9341", f"--user-data-dir={prof}", "--window-size=1200,860",
                         "--hide-scrollbars", "about:blank"])
for _ in range(50):
    try: tabs = httpx.get("http://127.0.0.1:9341/json").json(); break
    except Exception: time.sleep(0.2)
ws_url = next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"]


async def main():
    async with websockets.connect(ws_url, max_size=80_000_000) as ws:
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
            await asyncio.sleep(0.4)
            r = await cmd("Page.captureScreenshot", format="png"); (OUT / name).write_bytes(base64.b64decode(r["data"]))
            print("  saved", name)
        async def wait(expr, t=15):
            for _ in range(t * 4):
                if await js(expr) is True: return True
                await asyncio.sleep(0.25)
            print("   (timed out:", expr, ")"); return False
        await cmd("Page.enable"); await cmd("Runtime.enable"); await cmd("Network.enable")
        await cmd("Emulation.setDeviceMetricsOverride", width=1200, height=860, deviceScaleFactor=1, mobile=False)
        await cmd("Page.navigate", url=url)
        await wait("document.querySelector('#setupDlg') && document.querySelector('#setupDlg').open === true")
        await asyncio.sleep(1)
        # still logo for crisp screenshots
        await js("document.body.classList.add('still')")
        await shot("01-welcome.png")
        await js("document.querySelector('#wzNext').click()"); await asyncio.sleep(0.8)
        await js("document.querySelector('#wzProvs button').click()"); await asyncio.sleep(0.8)
        await shot("02-pick-provider.png")
        await js("document.querySelector('#wzNext').click()"); await asyncio.sleep(1.5)
        print("  tools step:", (await js("document.querySelector('#wzTools').innerText"))[:200].replace("\n", " | "))
        await shot("03-media-tools.png")
        await js("document.querySelector('#wzNext').click()"); await asyncio.sleep(0.8)
        await shot("04-all-set.png")
        await js("document.querySelector('#wzAssoc').checked = false; document.querySelector('#wzNext').click()")
        await wait("document.querySelector('#setupDlg').open === false")
        await js("document.querySelector('#links').value = 'https://archive.org/details/night_of_the_living_dead'")
        await shot("05-queue.png")
        # provider question (demo only - nothing is added to the queue)
        await js("providers.usable = ['realdebrid', 'alldebrid']; providers.providers.forEach(p => { if (p.id === 'realdebrid' || p.id === 'alldebrid') p.configured = true; });"
                 "openPicker({id: 0, name: 'Night of the Living Dead (1968)'})")
        await shot("06-choose-provider.png")
        await js("document.querySelector('#pickDlg').close()")
        # pair dialog: hide the live code/QR
        await js("document.querySelector('#pairBtn').click()")
        await wait("document.querySelector('#pairDlg').open === true")
        await js("document.querySelector('#pairUrl').textContent = 'https://192.168.1.20:13701/pair.html';"
                 "document.querySelector('#pairCode').textContent = '123456';"
                 "document.querySelector('#pairQr').style.filter = 'blur(7px)'")
        await shot("07-pair-phone.png")
        await js("document.querySelector('#pairDlg').close()")
        # settings
        await js("document.querySelector('#settingsBtn').click()")
        await wait("document.querySelector('#setDlg').open === true")
        await asyncio.sleep(1.2)
        await shot("08-settings-keys.png")
        await js("document.querySelector('#s_spin').closest('section, .sect, div').scrollIntoView({block: 'center'})")
        await js("document.querySelector('#npmInfo').textContent = 'In Nginx Proxy Manager: Proxy Host for your public address → scheme https, forward to this PC port 13701. SSL tab: request a Let’s Encrypt certificate, turn on Force SSL and HSTS.'")
        await shot("09-settings-window.png")
        # spin setting test
        anim = "getComputedStyle(document.querySelector('header .portal')).animationName"
        await js("document.body.classList.remove('still')")
        print("spin default checked:", await js("document.querySelector('#s_spin').checked"), "| anim:", await js(anim))
        await js("document.querySelector('#s_spin').checked = false; document.querySelector('#saveSet').click()")
        await asyncio.sleep(1)
        print("after untick+save -> anim:", await js(anim), "| saved:", json.loads(CONFIG_FILE.read_text()).get("spin_portal"))
        await js("document.querySelector('#setDlg').close()")
        # phone view (paired phone, same setting applies)
        b.auth.start_pairing(); code = b.auth.pair_code
        await cmd("Network.clearBrowserCookies")
        await cmd("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
        await cmd("Page.navigate", url=f"http://127.0.0.1:23771/pair.html")
        await asyncio.sleep(1.5)
        print("pair page still (remembered):", await js("document.body.classList.contains('still')"))
        await js(f"document.querySelector('#code').value = '{code}'; document.querySelector('#name').value = 'My phone'; document.querySelector('#go').click()")
        await wait("location.pathname === '/' && !!document.querySelector('#jobs')", 10)
        await asyncio.sleep(1.5)
        print("phone anim:", await js(anim), "| conn:", await js("document.querySelector('#conn').textContent"))
        await shot("10-phone.png")
        # turn it back on
        st.update({"spin_portal": True})
        await cmd("Page.reload"); await asyncio.sleep(2)
        print("phone after re-enable:", await js(anim))
    edge.terminate(); time.sleep(1)

asyncio.run(main())
b.stop()
