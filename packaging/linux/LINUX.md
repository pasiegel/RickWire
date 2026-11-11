# RickWire on Linux

This is the portable Linux version. It runs straight from this folder, and nothing is installed system-wide.

## Start

```bash
tar xzf RickWire-linux-portable.tar.gz
cd RickWire
./run.sh                # first run sets things up (about a minute, needs internet once)
./run.sh --install      # optional: app-menu entry + open magnet links / .torrent files with RickWire
```

The first-run setup in the app walks you through adding a debrid service (or skipping it) and downloading
ffmpeg and Deno for YouTube and other media sites.

## What it needs

- **Python 3.10 or newer** with `venv`. Debian/Ubuntu/Mint: `sudo apt install python3 python3-venv`.
- **A web view.** RickWire uses your desktop's WebKitGTK when it's installed. If it isn't, it installs Qt's web
  engine (~150 MB) into its own environment instead. To use WebKitGTK:
  - Debian/Ubuntu/Mint: `sudo apt install python3-gi gir1.2-webkit2-4.1`
  - Fedora: `sudo dnf install python3-gobject webkit2gtk4.1`
  - Arch: `sudo pacman -S python-gobject webkit2gtk-4.1`
- **A keyring** for your API keys: GNOME Keyring (most desktops) or KWallet (KDE). RickWire never stores keys in
  a file. If no keyring is running, it refuses to save them and tells you why.

## Good to know

- Settings and the queue live in `data/<hostname>/`. The Python environment lives in `.venv/<hostname>/`. So you
  can copy the folder to another PC, or share it over the network, and each machine keeps its own.
- **Tray icon:** KDE, XFCE, Cinnamon and MATE show it. Stock GNOME needs the *AppIndicator* extension. Because of
  that, closing the window quits RickWire by default on Linux. Turn on *Settings › Window › Closing the window
  keeps RickWire running in the tray* if your desktop shows the icon.
- **Phone access** uses port 13701. If you run a firewall: `sudo ufw allow from 192.168.0.0/16 to any port 13701 proto tcp`.
- ffmpeg, Deno and yt-dlp updates work the same as on Windows (*Settings › Media tools*): official GitHub
  releases, SHA-256 checked.
- To remove it: `./run.sh --uninstall`, then delete the folder. Keys stay in your keyring under "RickWire".
