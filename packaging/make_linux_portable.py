"""Build dist/RickWire-linux-portable.tar.gz: the Python source + run.sh, ready to unpack and run on Linux.

No compiling is needed (or possible from Windows): run.sh creates a per-PC Python environment on first launch.
    python packaging/make_linux_portable.py
"""
from __future__ import annotations

import io
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "RickWire-linux-portable.tar.gz"
PREFIX = "RickWire"
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIX = {".pyc", ".pyo"}

PORTABLE_TXT = """RickWire portable (Linux)
=========================
Run ./run.sh. See LINUX.md.

This file marks the folder as portable: settings live in data/<hostname>/ instead of ~/.local/share/RickWire.
"""


def add_bytes(tar: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644):
    info = tarfile.TarInfo(f"{PREFIX}/{name}")
    info.size, info.mode, info.mtime = len(data), mode, int(time.time())
    info.uname = info.gname = "rickwire"
    tar.addfile(info, io.BytesIO(data))


def lf(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def main() -> int:
    OUT.parent.mkdir(exist_ok=True)
    count = 0
    with tarfile.open(OUT, "w:gz", compresslevel=9) as tar:
        for path in sorted((ROOT / "rickwire").rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or SKIP_DIRS & set(rel.parts) or path.suffix in SKIP_SUFFIX:
                continue
            text = path.suffix in {".py", ".js", ".html", ".css", ".json", ".webmanifest", ".svg", ".txt"}
            add_bytes(tar, rel.as_posix(), lf(path) if text else path.read_bytes())
            count += 1
        add_bytes(tar, "run.py", lf(ROOT / "run.py"))
        add_bytes(tar, "requirements.txt", lf(ROOT / "requirements.txt"))
        add_bytes(tar, "README.md", lf(ROOT / "README.md"))
        add_bytes(tar, "LINUX.md", lf(ROOT / "packaging" / "linux" / "LINUX.md"))
        for doc in sorted((ROOT / "docs").rglob("*")):   # beginner guide + screenshots
            if doc.is_file():
                add_bytes(tar, doc.relative_to(ROOT).as_posix(), lf(doc) if doc.suffix == ".md" else doc.read_bytes())
        add_bytes(tar, "run.sh", lf(ROOT / "packaging" / "linux" / "run.sh"), mode=0o755)
        add_bytes(tar, "PORTABLE.txt", PORTABLE_TXT.encode())
    print(f"{OUT}  ({OUT.stat().st_size / 1e6:.1f} MB, {count + 6} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
