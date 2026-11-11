"""Remote access / web login security test. 127.0.0.2 plays Nginx Proxy Manager, 127.0.0.1 a phone on the LAN."""
import json, os, sys, tempfile, time
home = tempfile.mkdtemp(prefix="rwremote_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from rickwire.config import CONFIG_FILE, Settings
CONFIG_FILE.write_text(json.dumps({"download_dir": home, "local_port": 23740, "lan_port": 23741, "lan_enabled": True,
                                   "lan_https": False, "onboarded": True}))
from rickwire.app import Hooks
from rickwire.server import Backend
from rickwire import webauth
import httpx

notes = []
hooks = Hooks()
hooks.notify = lambda t, m: notes.append(f"{t}: {m}")
b = Backend(Settings(), hooks); assert b.start(), b.error
H = {"X-RickWire": "1"}
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


desk = httpx.Client(base_url="http://127.0.0.1:23740", headers=H, follow_redirects=True)
desk.get(b.auth.new_bootstrap_url(23740).replace("http://127.0.0.1:23740", ""))
assert desk.get("/api/me").json()["admin"]

lan = httpx.Client(base_url="http://127.0.0.1:23741", headers=H)


def proxy(xff="8.8.8.8", proto="https", host="rw.example.com", cookies=None):
    hdr = dict(H)
    if xff is not None: hdr["X-Forwarded-For"] = xff
    if proto is not None: hdr["X-Forwarded-Proto"] = proto
    hdr["Host"] = host
    return httpx.Client(base_url="http://127.0.0.2:23741", headers=hdr, cookies=cookies,
                        transport=httpx.HTTPTransport(local_address="127.0.0.2"))


print("\n-- remote access off")
r = proxy().get("/api/auth/status")
check("proxy headers from an untrusted address are refused", r.status_code == 403, r.status_code)
r = desk.put("/api/settings", json={"remote_enabled": True, "trusted_proxies": ["127.0.0.2"]})
check("can't enable remote access without a web login", r.status_code == 400, r.text)

print("\n-- web login")
r = desk.put("/api/auth/account", json={"username": "rick", "password": "short"})
check("short password rejected", r.status_code == 400)
r = desk.put("/api/auth/account", json={"username": "rick", "password": "pickle-rick-137"})
check("login saved", r.status_code == 200 and r.json()["enabled"], r.text)
cfg = json.loads(CONFIG_FILE.read_text())
check("password stored only as scrypt hash", cfg["web_login"]["hash"].startswith("scrypt$") and "pickle" not in CONFIG_FILE.read_text())
check("hash not exposed by /api/settings", "web_login" not in desk.get("/api/settings").json())
r = desk.put("/api/settings", json={"remote_enabled": True, "trusted_proxies": ["not-an-ip"]})
check("bad proxy address rejected", r.status_code == 400)
r = desk.put("/api/settings", json={"remote_enabled": True, "trusted_proxies": ["127.0.0.2"], "public_hosts": ["https://RW.example.com/"]})
check("remote access enabled", r.status_code == 200, r.text)
check("public host normalised", desk.get("/api/settings").json()["public_hosts"] == ["rw.example.com"])

print("\n-- through the proxy")
check("plain-HTTP proxying refused", proxy(proto="http").get("/api/auth/status").status_code == 403)
check("unknown public host refused", proxy(host="evil.example.net").get("/api/auth/status").status_code == 421)
st = proxy().get("/api/auth/status").json()
check("status: remote, login on, no pairing", st == {"login": True, "totp": False, "remote": True, "pairing": False}, st)
check("queue needs sign-in", proxy().get("/api/jobs").status_code == 401)
check("pairing refused remotely", proxy().post("/api/pair/complete", json={"code": "123456", "name": "x"}).status_code == 403)
st = proxy(xff="192.168.1.50").get("/api/auth/status").json()
check("a home-looking address through the proxy is still remote", st["remote"] and not st["pairing"])
check("no X-Forwarded-For through the proxy is still remote", proxy(xff=None).get("/api/auth/status").json()["remote"])
# desktop & paired-phone tokens are useless through the proxy
desk_cookie = desk.cookies.get("rw_session")
check("desktop session not accepted remotely", proxy(cookies={"rw_session": desk_cookie}).get("/api/jobs").status_code == 401)
b.auth.start_pairing(); code = b.auth.pair_code
lr = lan.post("/api/pair/complete", json={"code": code, "name": "test phone"})
check("pairing still works on the LAN", lr.status_code == 200)
phone_cookie = lan.cookies.get("rw_session")
check("paired phone works on the LAN", lan.get("/api/jobs").status_code == 200)
check("paired-phone token not accepted remotely", proxy(cookies={"rw_session": phone_cookie}).get("/api/jobs").status_code == 401)

print("\n-- brute force")
c = proxy(xff="203.0.113.5")
codes = [c.post("/api/auth/login", json={"username": "rick", "password": "wrong"}).status_code for _ in range(5)]
check("wrong passwords refused", codes == [403] * 5, codes)
r = c.post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137"})
check("locked after 5 failures, even with the right password", r.status_code == 403 and "Too many" in r.json()["detail"], r.text)
r = proxy(xff="X-spoof, 203.0.113.9").post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137"})
check("other address unaffected; XFF uses the proxy's entry", r.status_code == 200, r.text)
check("cookie is Secure + HttpOnly", "secure" in r.headers["set-cookie"].lower() and "httponly" in r.headers["set-cookie"].lower())
check("remote sign-in notification", any("Remote sign-in" in n and "203.0.113.9" in n for n in notes), notes)
rc = proxy(xff="203.0.113.9", cookies={"rw_session": r.cookies.get("rw_session")})
check("signed in: queue works", rc.get("/api/jobs").status_code == 200)
check("signed in: still not admin (settings)", set(rc.get("/api/settings").json()) <= {"ask_provider", "default_provider", "hoster_mode", "ytdlp_format", "lan_https", "spin_portal"})
check("signed in: can't read login/2FA admin", rc.get("/api/auth").status_code == 403)
check("signed in: can't change keys", rc.put("/api/providers/realdebrid/key", json={"key": "x" * 20}).status_code == 403)
check("signed in: no masked keys", all("masked" not in p for p in rc.get("/api/providers").json()["providers"]))
check("signed in: can't open folders", rc.post("/api/jobs/1/open").status_code in (403, 404))
me = rc.get("/api/me").json()
check("/api/me says login + remote", me["login"] and me["remote"] and not me["admin"])
check("unknown user costs like a wrong password", proxy(xff="198.51.100.1").post("/api/auth/login", json={"username": "morty", "password": "pickle-rick-137"}).status_code == 403)

print("\n-- 2FA")
t = desk.post("/api/auth/totp/start").json()
secret = t["secret"].replace(" ", "")
check("QR + key returned", t["qr"].startswith("data:image/svg") and len(secret) == 32)
check("wrong confirm code rejected", desk.post("/api/auth/totp/confirm", json={"code": "000000"}).status_code == 400)
now_code = webauth.totp_at(secret, int(time.time() // 30))
r = desk.post("/api/auth/totp/confirm", json={"code": now_code})
check("2FA confirmed", r.status_code == 200 and r.json()["totp"], r.text)
check("turning 2FA on signed everyone out", rc.get("/api/jobs").status_code == 401)
check("secret not in config.json", secret not in CONFIG_FILE.read_text())
c2 = proxy(xff="203.0.113.20")
r = c2.post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137"})
check("password alone -> asks for code", r.status_code == 401 and r.json()["need_code"], r.text)
r = c2.post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137", "code": "123456" if now_code != "123456" else "654321"})
check("wrong code refused", r.status_code == 401)
code_next = webauth.totp_at(secret, int(time.time() // 30) + 1)   # confirm used the current step
r = c2.post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137", "code": code_next})
check("right code -> signed in", r.status_code == 200, r.text)
r = proxy(xff="203.0.113.21").post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137", "code": code_next})
check("same code can't be reused", r.status_code == 401, r.text)
check("wrong password with right code still refused",
      proxy(xff="203.0.113.22").post("/api/auth/login", json={"username": "rick", "password": "nope", "code": code_next}).status_code == 403)

print("\n-- sessions")
lst = desk.get("/api/auth").json()["list"]
check("session listed with IP", any(s["ip"] == "203.0.113.20" for s in lst), lst)
c2c = proxy(xff="203.0.113.20", cookies={"rw_session": c2.cookies.get("rw_session")})
check("works before sign-out-everywhere", c2c.get("/api/jobs").status_code == 200)
desk.delete("/api/auth/sessions")
check("sign out everywhere", c2c.get("/api/jobs").status_code == 401)

print("\n-- LAN login + desktop with NPM on the same PC")
while int(time.time() // 30) <= b.auth.web.last_counter:   # wait for a fresh code, like a real user
    time.sleep(1)
r = lan.post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137",
                                       "code": webauth.totp_at(secret, int(time.time() // 30))})
check("web login also works on the LAN", r.status_code == 200, r.text)
desk.put("/api/settings", json={"trusted_proxies": ["127.0.0.1", "127.0.0.2"]})
check("desktop window still admin when 127.0.0.1 is a trusted proxy", desk.get("/api/me").json()["admin"])

print("\n-- global lock (30 failures from 6 addresses)")
for i in range(6):
    cc = proxy(xff=f"198.51.100.{100 + i}")
    for _ in range(5):
        cc.post("/api/auth/login", json={"username": "rick", "password": "bad"})
r = proxy(xff="198.51.100.200").post("/api/auth/login", json={"username": "rick", "password": "pickle-rick-137", "code": "000000"})
check("all logins paused", r.status_code == 403 and "paused" in r.json()["detail"], r.text)
check("lock notification", any("Sign-ins paused" in n for n in notes), notes)

print("\n-- remove login")
desk.delete("/api/auth/account")
s = desk.get("/api/settings").json()
check("remove login turns remote access off", s["remote_enabled"] is False)
check("proxy refused again", proxy().get("/api/auth/status").status_code == 403)
b.stop()
print(f"\nRESULT: {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
