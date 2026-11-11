import os, sys, tempfile
from pathlib import Path
home = tempfile.mkdtemp(prefix="rwoff_")
os.environ["RICKWIRE_HOME"] = home
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
import httpx
from rickwire import organize
from rickwire.manager import transient, magnet_hash

root = Path(tempfile.mkdtemp(prefix="rwsort_"))


def mk(rel, size=10):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


def show(name, files):
    moves = organize.plan(name, files, root)
    print(f"\n== {name}")
    for a, b in moves:
        print(f"   {a.relative_to(root)}  ->  {b.relative_to(root)}")
    if not moves:
        print("   (left alone)")
    return moves


# movie in a folder with sample + subs + nfo
f = [mk("Chainsmoker.Cat.2026.1080p.WEBRip.x264/Chainsmoker.Cat.2026.1080p.WEBRip.x264.mkv", 5000),
     mk("Chainsmoker.Cat.2026.1080p.WEBRip.x264/Sample/sample.mkv", 100),
     mk("Chainsmoker.Cat.2026.1080p.WEBRip.x264/Subs/2_English.srt"),
     mk("Chainsmoker.Cat.2026.1080p.WEBRip.x264/info.nfo")]
m = show("Chainsmoker.Cat.2026.1080p.WEBRip.x264", f)
done = organize.apply(m, root, root / "Chainsmoker.Cat.2026.1080p.WEBRip.x264")
print("   left behind:", sorted(str(p.relative_to(root)) for p in (root / "Chainsmoker.Cat.2026.1080p.WEBRip.x264").rglob("*")))
# single-file movie, YTS style
show("Axel 2 Adventures Of The Spacekids (2026) [1080p] [WEBRip] [YTS.MX]",
     [mk("Axel 2 Adventures Of The Spacekids (2026) [1080p] [WEBRip] [YTS.MX]/Axel.2.Adventures.Of.The.Spacekids.2026.1080p.WEBRip.x264.AAC-[YTS.MX].mp4", 4000)])
# single episode
show("The.Last.of.Us.S02E07.1080p.WEB.H264-SuccessfulCrab", [mk("The.Last.of.Us.S02E07.1080p.WEB.H264-SuccessfulCrab.mkv", 3000)])
# season pack
show("Shogun.2024.S01.COMPLETE.1080p", [mk(f"Shogun.2024.S01.COMPLETE.1080p/Shogun.2024.S01E0{i}.1080p.mkv", 2000) for i in range(1, 4)]
     + [mk("Shogun.2024.S01.COMPLETE.1080p/Shogun.2024.S01E01.1080p.en.srt")])
# game repack: nothing
show("I Know a Guy [FitGirl Repack]", [mk("I Know a Guy [FitGirl Repack]/setup.exe"), mk("I Know a Guy [FitGirl Repack]/fg-01.bin")])
# collision: same movie again -> " (2)"
again = [mk("Chainsmoker Cat (2026) 720p/Chainsmoker.Cat.2026.720p.mkv", 3000)]
m2 = show("Chainsmoker Cat (2026) 720p", again)
d2 = organize.apply(m2, root, root / "Chainsmoker Cat (2026) 720p")
print("   collision ->", [str(v.relative_to(root)) for v in d2.values()])
print("\nfinal tree:")
for p in sorted(root.rglob("*")):
    if p.is_file():
        print("  ", p.relative_to(root))

print("\ntransient:")
req = httpx.Request("GET", "https://cdn.example/x")
for code in (503, 429, 404, 403, 400):
    e = httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))
    print(f"  HTTP {code}: torrent={transient(e, 'torrent')} direct={transient(e, 'direct')}")
print("  ConnectError:", transient(httpx.ConnectError("boom"), "direct"))
print("  ProviderError 'Real-Debrid: infringing_file':", transient(Exception("Real-Debrid: infringing_file"), "torrent"))
print("  'Real-Debrid: Service Unavailable (HTTP 503)':", transient(Exception("Real-Debrid: Service Unavailable (HTTP 503)"), "torrent"))
print("  yt-dlp 'HTTP Error 503':", transient(Exception("ERROR: unable to download: HTTP Error 503: Service Unavailable"), "ytdlp"))
print("  OSError disk full:", transient(OSError(28, "No space left on device"), "direct"))
print("\nmagnet_hash:", magnet_hash("magnet:?xt=urn:btih:08ADA5A7A6183AAE1E09D831DF6748D566095A10&dn=Sintel"),
      magnet_hash("magnet:?xt=urn:btih:BCK2LJ5GDAMAVYPATDKN67L3VZLCNM7Q"))
