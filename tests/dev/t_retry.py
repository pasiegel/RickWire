import json, os, sys, tempfile, threading, time, pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
home = tempfile.mkdtemp(prefix="rwretry_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from rickwire.config import CONFIG_FILE, Settings
dl = pathlib.Path(home) / "dl"
CONFIG_FILE.write_text(json.dumps({"download_dir": str(dl), "local_port": 23733, "lan_enabled": False, "onboarded": True,
                                   "auto_retry": 3}))
import rickwire.manager as mgr
mgr.RETRY_DELAYS = (1, 1, 1)
from rickwire.app import Hooks
from rickwire.server import Backend
import httpx

hits = {"n": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        if self.path.startswith("/flaky/"):
            hits["n"] += 1
            if hits["n"] <= 2:
                self.send_response(503); self.end_headers(); return
        if self.path.startswith("/gone/"):
            self.send_response(404); self.end_headers(); return
        body = b"m" * 200_000
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 23799), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

b = Backend(Settings(), Hooks()); assert b.start(), b.error
c = httpx.Client(base_url="http://127.0.0.1:23733", headers={"X-RickWire": "1"}, follow_redirects=True, timeout=60)
c.get(b.auth.new_bootstrap_url(23733).replace("http://127.0.0.1:23733", ""))


def wait(jid, states, t=30):
    seen = []
    for _ in range(t * 10):
        j = next(x for x in c.get("/api/jobs").json()["jobs"] if x["id"] == jid)
        if j["message"] and (not seen or seen[-1] != j["message"]):
            seen.append(j["message"])
        if j["state"] in states:
            return j, seen
        time.sleep(0.1)
    raise SystemExit(f"timeout: {j}")


# 1) flaky server + sort checkbox on
j = c.post("/api/jobs", json={"text": "http://127.0.0.1:23799/flaky/Chainsmoker.Cat.2026.1080p.WEB.mkv", "mode": "direct", "sort": True}).json()[0]
j, msgs = wait(j["id"], {"done", "error"})
print("flaky:", j["state"], "| server hits:", hits["n"])
for m in msgs:
    print("   msg:", m)
print("   on disk:", [str(p.relative_to(dl)) for p in dl.rglob("*") if p.is_file()])

# 2) permanent 404 on a direct link: no retry, error immediately
j2 = c.post("/api/jobs", json={"text": "http://127.0.0.1:23799/gone/x.bin", "mode": "direct"}).json()[0]
j2, msgs = wait(j2["id"], {"done", "error"})
print("404 direct:", j2["state"], "|", j2["message"])

# 3) retry-failed button
r = c.post("/api/jobs/retry").json()
print("retry failed ->", r)
j2, _ = wait(j2["id"], {"error"})
print("   after retry:", j2["state"])

# 4) manual sort on a finished job (no checkbox)
hits["n"] = 10
j3 = c.post("/api/jobs", json={"text": "http://127.0.0.1:23799/ok/Severance.S02E03.1080p.ATVP.WEB-DL.mkv", "mode": "direct"}).json()[0]
j3, _ = wait(j3["id"], {"done", "error"})
print("no-sort job:", j3["state"], "| msg:", repr(j3["message"]), "| sorted flag:", j3.get("sorted"))
print("preview:", c.get(f"/api/jobs/{j3['id']}/sort").json())
print("apply:", c.post(f"/api/jobs/{j3['id']}/sort").json())
print("final tree:", sorted(str(p.relative_to(dl)) for p in dl.rglob("*") if p.is_file()))
# a phone (non-admin) can't sort
b.stop(); srv.shutdown()
