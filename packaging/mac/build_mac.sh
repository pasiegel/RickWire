#!/usr/bin/env bash
# Build RickWire.app and dist/RickWire-<version>-mac-<arch>.dmg on a Mac (Apple Silicon or Intel; builds for the
# machine it runs on). Needs Python 3.12+ (python.org or Homebrew); Xcode is not needed.
#     bash packaging/mac/build_mac.sh
# The app is ad-hoc signed, not notarized: the first time, right-click RickWire.app > Open (see READ ME FIRST.txt).
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$(pwd)
PY=${PYTHON:-python3}
ARCH=$(uname -m); [ "$ARCH" = "x86_64" ] && ARCH=intel

echo "== Python environment (.venv-mac)"
[ -x .venv-mac/bin/python ] || "$PY" -m venv .venv-mac
VPY="$ROOT/.venv-mac/bin/python"
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r requirements.txt pyinstaller
"$VPY" -m pip install -q --upgrade "yt-dlp[default]"
VERSION=$("$VPY" -c "import rickwire; print(rickwire.__version__)")
echo "== RickWire $VERSION ($ARCH)"
"$VPY" -m rickwire.icons

echo "== Icon"
ICONSET=build/mac/RickWire.iconset
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
SRC=rickwire/web/icons/icon-512.png
for s in 16 32 128 256 512; do
  sips -z $s $s "$SRC" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2)); [ $d -le 512 ] && sips -z $d $d "$SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
cp "$SRC" "$ICONSET/icon_512x512@2x.png"   # 512 source; macOS upscales the 1024 slot
iconutil -c icns "$ICONSET" -o build/mac/RickWire.icns

echo "== PyInstaller"
rm -rf dist/RickWire.app dist/RickWire
"$VPY" -m PyInstaller --noconfirm --clean --windowed --log-level WARN --name RickWire \
    --icon build/mac/RickWire.icns \
    --osx-bundle-identifier app.rickwire.RickWire \
    --add-data "rickwire/web:rickwire/web" \
    --collect-submodules uvicorn \
    --collect-all yt_dlp_ejs \
    --collect-all webview \
    --copy-metadata yt-dlp \
    --hidden-import keyring.backends.macOS \
    --hidden-import pystray._darwin \
    --hidden-import qrcode.image.svg \
    --exclude-module tkinter \
    run.py
APP=dist/RickWire.app
[ -x "$APP/Contents/MacOS/RickWire" ] || { echo "PyInstaller failed"; exit 1; }
"$VPY" packaging/mac/info_plist.py "$APP" "$VERSION"
mkdir -p "$APP/Contents/Resources/docs"
cp README.md "$APP/Contents/Resources/README.md"
cp -R docs/. "$APP/Contents/Resources/docs/"

echo "== Sign (ad-hoc)"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "== Disk image"
STAGE=build/mac/dmg
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/READ ME FIRST.txt" <<'TXT'
RickWire for macOS
==================
1. Drag RickWire into Applications.
2. The first time, RIGHT-click (or Control-click) RickWire in Applications and choose Open, then Open again.
   (RickWire isn't notarized by Apple, so a normal double-click says it "can't be opened". After the first
   time it opens normally.)
   If macOS still refuses: System Settings > Privacy & Security > scroll down > "Open Anyway".
3. RickWire lives in the menu bar (the portal icon). Closing the window keeps it running; Quit is in that menu.

Your API keys are stored in your Mac's Keychain. Settings live in ~/Library/Application Support/RickWire.
TXT
DMG="dist/RickWire-$VERSION-mac-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -volname "RickWire $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
echo "== Done: $DMG ($(du -h "$DMG" | cut -f1))"
