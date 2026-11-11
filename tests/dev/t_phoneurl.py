"""Phone address follows remote access: public URL when the proxy is on, LAN otherwise. 127.0.0.2 = NPM."""
import json, os, sys, tempfile
home = tempfile.mkdtemp(prefix="rwphone_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from rickwire.config import CONFIG_FILE, Settings
CONFIG_FILE.write_text(json.dumps({"download_dir": home, "local_port": 23760, "lan_port": 23761, "lan_enabled": True,
                                   "lan_https": False, "onboarded": True}))
from rickwire.app import Hooks
from rickwire.server import Backend
from rickwire.security import local_ips
import httpx

notes = []
hooks = Hooks(); hooks.notify = lambda t, m: notes.append(f"{t}: {m}")
b = Backend(Settings(), hooks); assert b.start(), b.error
H = {"X-RickWire": "1"}
res = []
def check(name, ok, detail=""):
    res.append(ok); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))

desk = httpx.Client(base_url="http://127.0.0.1:23760", headers=H, follow_redirects=True)
desk.get(b.auth.new_bootstrap_url(23760).replace("http://127.0.0.1:23760", ""))
ip = local_ips()[0]
HTML = {"Accept": "text/html,application/xhtml+xml"}
def lan():   # a phone on the LAN (real LAN address, not loopback)
    return httpx.Client(base_url=f"http://{ip}:23761", headers=H, transport=httpx.HTTPTransport(local_address=ip))
def proxy(cookies=None, xff="203.0.113.50"):
    return httpx.Client(base_url="http://127.0.0.2:23761", cookies=cookies,
                        headers=H | {"X-Forwarded-For": xff, "X-Forwarded-Proto": "https", "Host": "rw.example.com"},
                        transport=httpx.HTTPTransport(local_address="127.0.0.2"))

print("-- remote access off: LAN address")
p = desk.post("/api/pair/start").json()
check("pair QR uses LAN address, no key", p["url"].startswith(f"http://{ip}:23761/pair.html#") and "." not in p["url"].split("#")[1] and not p["remote"], p["url"])
check("no redirect on LAN", lan().get("/", headers=HTML).status_code == 200)
r = lan().post("/api/pair/complete", json={"code": p["code"], "name": "lan phone"})
check("LAN pairing with typed code", r.status_code == 200)
lan_cookie = r.cookies.get("rw_session")

print("-- remote access on: public address")
desk.put("/api/auth/account", json={"username": "rick", "password": "pickle-rick-137"})
r = desk.put("/api/settings", json={"remote_enabled": True, "trusted_proxies": ["127.0.0.2"], "public_hosts": ["rw.example.com"]})
check("remote on", r.status_code == 200, r.text)
check("/api/system public_url", desk.get("/api/system").json()["public_url"] == "https://rw.example.com")
r = lan().get("/share?text=magnet%3A%3Fxt", headers=HTML)
check("LAN page load -> 307 to public address (query kept)", r.status_code == 307 and r.headers["location"] == "https://rw.example.com/share?text=magnet%3A%3Fxt", (r.status_code, r.headers.get("location")))
check("LAN API calls not redirected", lan().get("/api/auth/status").status_code == 200)
check("desktop window not redirected", desk.get("/", headers=HTML, follow_redirects=False).status_code == 200)
check("proxied page not redirected", proxy().get("/pair.html", headers=HTML).status_code == 200)
p = desk.post("/api/pair/start").json()
code, key = p["url"].split("#")[1].split(".")
check("pair QR uses public address + long key", p["url"].startswith("https://rw.example.com/pair.html#") and p["remote"] and len(key) >= 30, p["url"])
r = proxy().post("/api/pair/complete", json={"code": code, "name": "x"})
check("typed code alone refused through proxy", r.status_code == 403 and "QR" in r.json()["detail"], r.text)
r = proxy().post("/api/pair/complete", json={"code": code, "name": "x", "key": "A" * 32})
check("wrong key refused", r.status_code == 403)
r = proxy().post("/api/pair/complete", json={"code": code, "name": "Test Pixel", "key": key})
check("QR pairing through proxy works", r.status_code == 200, r.text)
check("cookie Secure", "secure" in r.headers["set-cookie"].lower())
pc = r.cookies.get("rw_session")
rp = proxy(cookies={"rw_session": pc})
check("remote-paired phone: queue works via proxy", rp.get("/api/jobs").status_code == 200)
check("remote-paired phone: not admin", rp.get("/api/settings").status_code == 200 and "web_login" not in rp.get("/api/settings").json() and rp.get("/api/auth").status_code == 403)
check("remote-paired phone works from another outside IP", proxy(cookies={"rw_session": pc}, xff="198.51.100.9").get("/api/jobs").status_code == 200)
check("key is single-use", proxy().post("/api/pair/complete", json={"code": code, "name": "y", "key": key}).status_code == 403)
check("notification", any("Phone paired" in n for n in notes), notes)
check("LAN-paired phone still refused via proxy", proxy(cookies={"rw_session": lan_cookie}).get("/api/jobs").status_code == 401)
devs = desk.get("/api/devices").json()
check("device list marks remote phone", [d["remote"] for d in devs] == [False, True], devs)
p = desk.post("/api/pair/start").json(); code, key = p["url"].split("#")[1].split(".")
for _ in range(5): proxy().post("/api/pair/complete", json={"code": code, "name": "x", "key": "B" * 32})
check("5 wrong tries cancel the pairing", proxy().post("/api/pair/complete", json={"code": code, "name": "x", "key": key}).status_code == 403)
desk.delete(f"/api/devices/{devs[1]['id']}")
check("revoke works", rp.get("/api/jobs").status_code == 401)

print("-- remote off again")
desk.put("/api/settings", json={"remote_enabled": False})
check("no redirect", lan().get("/", headers=HTML).status_code == 200)
check("pair QR back to LAN", desk.post("/api/pair/start").json()["url"].startswith(f"http://{ip}:23761/"))
b.stop()
print(f"RESULT: {sum(res)}/{len(res)}")
sys.exit(0 if all(res) else 1)
