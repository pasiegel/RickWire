"""Magic Wormhole transfers, run as a separate process so Twisted (which wormhole is built on) never shares a
thread or event loop with RickWire's server or window.

    RickWire.exe --wormhole-worker             (frozen app)
    python -m rickwire.wormhole_worker         (source)

with one line of JSON on stdin (so a receive code never shows up in the process list): {"op": "receive", "code": "7-crossover-clockwork", "dest": "<empty staging folder>", "max_bytes": 0,
       "relay": "", "transit": ""}
   or {"op": "send", "path": "<file or folder>", "relay": "", "transit": ""}

It drives wormhole's own command-line code (the same code the `wormhole` command runs, so the protocol is the
reference one), with three hooks: the progress bar reports bytes, the "accept this?" question checks the size
limit instead of asking, and received text messages are reported instead of printed. Everything RickWire needs
comes out on stdout as lines `@@RW {"ev": ...}`: code, offer, progress, text, done/error. Human-readable output
goes to stderr.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

PREFIX = "@@RW "


def _streams():
    """A windowed (no-console) exe may start with sys.stdout/stderr = None even though RickWire handed it pipes."""
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name) is None:
            try:
                setattr(sys, name, open(fd, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, "w"))
        else:
            try:
                getattr(sys, name).reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except (AttributeError, ValueError):
                pass


def emit(ev: str, **kw) -> None:
    sys.stdout.write(PREFIX + json.dumps({"ev": ev, **kw}) + "\n")
    sys.stdout.flush()


class Progress:
    """Stands in for tqdm: reports bytes done every half second."""

    def __init__(self, *a, total=None, **kw):
        self.total, self.n, self._t = int(total or 0), 0, 0.0

    def __enter__(self):
        emit("progress", done=0, total=self.total)
        return self

    def __exit__(self, *exc):
        emit("progress", done=self.n, total=self.total)
        return False

    def update(self, n=1):
        self.n += n
        now = time.monotonic()
        if now - self._t >= 0.5:
            self._t = now
            emit("progress", done=self.n, total=self.total)

    def close(self):
        pass


class Tee:
    """wormhole's stderr: passed through, and the send code is picked out of it."""
    CODE = re.compile(r"Wormhole code is: (\S+)")

    def __init__(self, real):
        self.real = real

    def write(self, s):
        m = self.CODE.search(s)
        if m:
            emit("code", code=m.group(1))
        return self.real.write(s)

    def flush(self):
        self.real.flush()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.real, name)


def main(argv: list[str]) -> int:
    _streams()
    try:
        raw = argv[0] if argv else (sys.stdin.readline() if sys.stdin else os.fdopen(0).readline())
        job = json.loads(raw)
    except (OSError, ValueError):
        print("usage: one line of JSON on stdin", file=sys.stderr)
        return 2

    from wormhole.cli import cli, cmd_receive, cmd_send

    cmd_receive.tqdm = cmd_send.tqdm = Progress
    cli.stderr = Tee(sys.stderr)
    cli.stdout = sys.stderr   # nothing but our events on stdout
    max_bytes = int(job.get("max_bytes") or 0)

    def ask_permission(self):
        name = os.path.basename(self.abs_destname)
        emit("offer", name=name, size=int(self.xfersize or 0))
        if max_bytes and self.xfersize > max_bytes:
            emit("too_big", name=name, size=int(self.xfersize), limit=max_bytes)
            raise cmd_receive.TransferRejectedError()

    def handle_text(self, them_d, w):
        emit("text", text=str(them_d.get("message", ""))[:20000])
        self._send_data({"answer": {"message_ack": "ok"}}, w)

    cmd_receive.Receiver._ask_permission = ask_permission
    cmd_receive.Receiver._handle_text = handle_text

    args = []
    if job.get("relay"):
        args += ["--relay-url", job["relay"]]
    if job.get("transit"):
        args += ["--transit-helper", job["transit"]]
    if job["op"] == "receive":
        os.chdir(job["dest"])
        args += ["receive", "--hide-progress", job["code"]]
    elif job["op"] == "send":
        path = os.path.abspath(job["path"])
        os.chdir(os.path.dirname(path) or ".")
        args += ["send", "--no-qr", os.path.basename(path)]
    else:
        print(f"unknown op {job['op']!r}", file=sys.stderr)
        return 2
    # (--hide-progress only matters to the real tqdm; Progress above always reports)
    try:
        cli.wormhole.main(args=args, prog_name="wormhole", standalone_mode=False)
    except SystemExit as e:   # twisted's react() always ends with SystemExit, also on success
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        emit("failed", exit=code) if code else emit("finished")
        return code
    except Exception as e:   # noqa: BLE001
        print(f"ERROR: {e.__class__.__name__}: {e}", file=sys.stderr)
        emit("failed", exit=1)
        return 1
    emit("finished")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
