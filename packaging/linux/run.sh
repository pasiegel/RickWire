#!/usr/bin/env bash
# RickWire for Linux (portable).
#
#   ./run.sh              start RickWire
#   ./run.sh --install    add RickWire to the app menu and open magnet links / .torrent files with it
#   ./run.sh --uninstall  remove the menu entry and handler again
#
# The first run on each PC sets up a private Python environment in .venv/<hostname>/ (needs internet once,
# about a minute). Settings live in data/<hostname>/, so this folder can be copied around or shared.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$HERE"
PY="${PYTHON:-python3}"
HOST="$(hostname 2>/dev/null || uname -n)"
VENV="$HERE/.venv/$HOST"

die() {
  echo "RickWire: $*" >&2
  if command -v zenity >/dev/null 2>&1 && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    zenity --error --title=RickWire --no-wrap --text="$*" 2>/dev/null || true
  fi
  exit 1
}

command -v "$PY" >/dev/null 2>&1 || die "Python 3.10 or newer is required.
  Debian/Ubuntu/Mint: sudo apt install python3 python3-venv
  Fedora:             sudo dnf install python3
  Arch:               sudo pacman -S python"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10+ is required (found $("$PY" -V 2>&1))."

stamp="$VENV/.rickwire-ok"
if [ ! -f "$stamp" ] || [ requirements.txt -nt "$stamp" ] || ! "$VENV/bin/python" -c '' 2>/dev/null; then
  echo "RickWire: first run on $HOST - setting things up (one time, about a minute)..."
  rm -rf "$VENV"
  # --system-site-packages lets the venv see the distro's PyGObject + WebKitGTK, which pip can't install
  "$PY" -m venv --system-site-packages "$VENV" || die "Couldn't create a Python environment.
  Debian/Ubuntu/Mint: sudo apt install python3-venv"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt || die "Installing Python packages failed (see the messages above)."
  if ! "$VENV/bin/python" - 2>/dev/null <<'PY'
import gi
gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:
    gi.require_version("WebKit2", "4.0")
PY
  then
    echo "RickWire: WebKitGTK not found - installing the Qt web engine instead (~150 MB)."
    echo "          To use your desktop's own instead: sudo apt install python3-gi gir1.2-webkit2-4.1"
    "$VENV/bin/python" -m pip install --quiet "pywebview[qt]" || die "Couldn't install a web view. Install WebKitGTK:
  Debian/Ubuntu/Mint: sudo apt install python3-gi gir1.2-webkit2-4.1
  Fedora:             sudo dnf install python3-gobject webkit2gtk4.1
  Arch:               sudo pacman -S python-gobject webkit2gtk-4.1"
  fi
  "$VENV/bin/python" -m rickwire.icons >/dev/null
  touch "$stamp"
fi

case "${1:-}" in
  --install)
    "$VENV/bin/python" run.py --register
    echo "RickWire is in your applications menu, and magnet links / .torrent files now open with it."
    exit 0 ;;
  --uninstall)
    "$VENV/bin/python" run.py --unregister
    echo "Removed RickWire's menu entry and magnet / .torrent handler."
    exit 0 ;;
esac
exec "$VENV/bin/python" run.py "$@"
