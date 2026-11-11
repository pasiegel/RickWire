"""Windows. Regression test for the v0.2.7 freeze fix (v0.2.6 froze on the first call).
Start the real desktop app (source) in a temp home, then hit /api/local/focus hundreds of times while also
polling the API, and check the window keeps answering Windows (IsHungAppWindow) and the server keeps answering."""
import ctypes, json, os, subprocess, sys, tempfile, time, pathlib
import httpx
home = tempfile.mkdtemp(prefix="rwstress_")
pathlib.Path(home, "config.json").write_text(json.dumps({"download_dir": home, "local_port": 23790, "lan_port": 23791,
    "lan_enabled": False, "onboarded": True, "close_to_tray": True}))
installed = {int(x) for x in subprocess.run(['powershell','-NoProfile','-Command','(Get-Process RickWire -EA 0).Id'], capture_output=True, text=True).stdout.split()}
env = dict(os.environ, RICKWIRE_HOME=home)
ROOT = pathlib.Path(__file__).resolve().parents[2]
cmd = sys.argv[1:] or [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "run.py")]   # or pass: C:\RickWire\RickWire.exe
p = subprocess.Popen(cmd, env=env)
tok_file = pathlib.Path(home, "local.token")
for _ in range(100):
    if tok_file.exists(): break
    time.sleep(0.3)
time.sleep(4)
tok = tok_file.read_text().strip()
u32 = ctypes.windll.user32
def find_hwnd():
    found = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        pid = ctypes.c_ulong(); u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(200); u32.GetWindowTextW(h, buf, 200)
        if pid.value not in installed and buf.value == "RickWire": found.append((h, pid.value))
        return True
    u32.EnumWindows(cb, 0); print('windows:', [w[1] for w in found]); return found[0][0] if found else None
hwnd = find_hwnd(); print("window:", bool(hwnd))
c = httpx.Client(base_url="http://127.0.0.1:23790", headers={"X-RickWire-Local": tok}, timeout=5)
hung = slow = 0; t0 = time.time()
for i in range(300):
    try:
        c.post("/api/local/focus")
        r = c.get("/api/me")   # server must stay responsive
    except Exception as e:
        slow += 1; print("server timeout at call", i, e.__class__.__name__, '| window hung:', bool(hwnd and u32.IsHungAppWindow(hwnd))); break
    if hwnd and u32.IsHungAppWindow(hwnd): hung += 1
    time.sleep(0.02)
print(f"300 focus calls in {time.time()-t0:.1f}s: server timeouts={slow}, hung-window samples={hung}")
time.sleep(3)
print("after: hung =", bool(hwnd and u32.IsHungAppWindow(hwnd)), "| server ok =", c.get("/api/me").status_code)
try: c.post("/api/local/quit")
except Exception: pass
try: p.wait(15); print("quit cleanly, exit", p.returncode)
except subprocess.TimeoutExpired: p.kill(); print("had to kill")
