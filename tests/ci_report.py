"""CI helper: run a command, show its output, and on failure also post the last lines as a GitHub annotation
(annotations are visible on the run page without opening the full log).

    python tests/ci_report.py "title" cmd arg...
"""
import subprocess
import sys

title, cmd = sys.argv[1], sys.argv[2:]
p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
print(p.stdout, flush=True)
if p.returncode:
    tail = p.stdout.strip().splitlines()[-40:]
    msg = "%0A".join(line.replace("%", "%25").replace("\r", "") for line in tail)
    print(f"::error title={title} (exit {p.returncode})::{msg}", flush=True)
sys.exit(p.returncode)
