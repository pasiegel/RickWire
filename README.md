# RickWire 🌀

A Rick & Morty–themed, desktop-first download client in the spirit of [Decypharr](https://github.com/sirrobot01/decypharr),
written in Python. It has one queue for:

> **New to this?** Start with the illustrated [Getting started guide](docs/GETTING-STARTED.md).

| Source | How |
|---|---|
| **Magnets / .torrent** | Sent to your debrid provider (Real-Debrid, AllDebrid, TorBox, Premiumize), then pulled down locally |
| **Hoster links** (MEGA, Rapidgator, 1fichier, Mediafire, …) | Unrestricted through debrid |
| **MEGA** | Debrid first (no MEGA quota). Falls back to a native decrypting downloader for files and folders |
| **Gofile** | Debrid, or the official API with a Gofile *premium* token (see below) |
| **YouTube, Tubi, Internet Archive, 1000+ sites, raw `.mpd` / `.m3u8`** | yt-dlp + ffmpeg (DRM-protected streams are not supported) |
| **Anything else** | Resumable direct HTTP |

If you have more than one debrid key set up, RickWire asks which provider to use. Tick **"Don't ask again"**
(or change it in Settings) and it uses your default from then on. The question pops up on the PC and on any open
phone; answering it on one closes it on the others (the same goes for *Pick files*).

**🔍 Search** uses BT4G's public RSS feed (top 15 results, sortable by seeders, size or date, filterable by type).
**🆕 Latest** shows new YTS movies (posters, rating, a button per quality) and EZTV TV releases from EZTV's API.
*Older releases…* pages back through EZTV's whole catalogue, 50 at a time. **Search a show** looks the name up
on IMDb, and picking a show lists every EZTV release of it, which you can filter by episode (e.g. `S02E07`) or
quality. The ➕ button on any result sends it into the queue through your debrid. These sites change domains
often, so you can edit the source URLs in Settings › *Search & Latest sources*.

## Queue features

- **⚡ Check cache** (Search and Latest): asks your debrid which results it already has, so they download
  instantly. Results get a **⚡ cached**, **not cached** or **🚫 blocked** tag (Real-Debrid's content filter now
  refuses some releases outright). TorBox and Premiumize answer directly, so their tags appear on their own.
  Real-Debrid and AllDebrid removed their cache lookup, so the button adds each torrent, checks whether it's
  ready straight away, and deletes it again (a few seconds per result, nothing is left in your account).
- **Pick files:** when a torrent has several files (season packs, extras), RickWire asks which ones to download,
  with *All / None / Videos only* and a filter box. Samples and `.nfo`/`.txt` files start unticked. The
  debrid still takes the whole torrent (on Real-Debrid a partial selection loses the instant cache), but only
  the ticked files come down to your PC. Turn it off in Settings › *Debrid providers*. Real-Debrid packs
  torrents made of many tiny files into one `.rar`; those can't be split, so they download whole.
- **Retries:** network drops, busy servers (HTTP 429/5xx) and expired debrid links are retried automatically
  (3 times by default, after 10 s, 30 s and 90 s; set it in Settings › *Downloads*, 0 = off). Failed jobs get a
  **↻** button, and **↻ Retry failed** retries all of them.
- **🏷 Rename & sort** (checkbox next to *Download*, also on Search and Latest; off by default and remembered):
  when the download finishes, video files are renamed and moved to `Movies\Title (Year)\Title (Year).mkv` or
  `TV\Show\Season 02\Show - S02E07.mkv` inside your download folder, with matching subtitles. Samples, games
  and anything it can't recognise are left alone, and existing files are never overwritten. Finished jobs that
  weren't sorted have a 🏷 button that previews the new names before moving anything.

## Install

Four packages. `.\build.ps1` builds the first three on Windows; the Mac one is built on a Mac (or by GitHub Actions):

| | File | Notes |
|---|---|---|
| **Installer** | `dist\RickWire-Setup-<version>.exe` (34 MB) | Per-user install, no admin prompt. Adds a Start menu entry, and optionally a desktop shortcut, the magnet/.torrent handler and start-with-Windows. Uninstall from *Settings › Apps*. Data lives in `%APPDATA%\RickWire`. |
| **Portable** | `dist\RickWire\` or `dist\RickWire-portable.zip` (201 MB) | Copy anywhere and run `RickWire.exe`. Includes ffmpeg + Deno, so it works offline. Each PC keeps its own data in `data\<PC-NAME>\`, so you can copy it around or run it from a network share. |
| **Linux** | `dist\RickWire-linux-portable.tar.gz` (1 MB) | Unpack and run `./run.sh`. The first run sets up its own Python environment (needs Python 3.10+ and internet once). `./run.sh --install` adds a menu entry and the magnet/.torrent handler. See [LINUX.md](packaging/linux/LINUX.md). |
| **macOS** | `dist/RickWire-<version>-mac-arm64.dmg` | Apple Silicon (M1 and newer), macOS 12+. Drag RickWire into Applications. See [macOS](#macos) below. |

```powershell
.\build.ps1                # installer + portable folder + zip + Linux tarball
.\build.ps1 -NoTools       # portable folder without ffmpeg/Deno (first-run setup downloads them)
.\build.ps1 -NoInstaller   # skip Inno Setup
```

The installer needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).

**First run:** a short setup explains what a debrid service is and lets you pick yours (sign-up and API-key
links plus step-by-step instructions), or **skip it** and add one later in ⚙ Settings. It then offers to download
the media tools. You can run it again from *Settings › Window*.

**Updating:** run the new `RickWire-Setup-<version>.exe`. It installs over the old copy, closing it first if
it's running. Settings, queue, paired phones and certificates (`%APPDATA%\RickWire`), API keys (Credential
Manager), downloaded tools and the magnet handler are all kept. For the portable copy, replace everything except
the `data\` and `tools\` folders. To check an upgrade yourself:
`.venv\Scripts\python tests\check_upgrade.py --installer dist\RickWire-Setup-<version>.exe`
compares all of the above before and after.

Notes for both:
- **API keys are never stored in the app folder.** They live in each PC's Credential Manager, so paste them once per PC.
- Keep `RickWire.exe.config` next to the exe. Without it, a copy unzipped from a downloaded zip (or run from a
  network share) fails to start with a .NET "Failed to resolve Python.Runtime…" / "Could not load file" error.
- Windows may show a SmartScreen "Windows protected your PC" box the first time (the exe isn't code-signed).
  Click *More info › Run anyway*.

### macOS

1. Open the `.dmg` and drag **RickWire** into **Applications**.
2. **The first time, right-click RickWire › Open, then Open again.** The app isn't notarized by Apple (that needs a
   paid developer account), so a plain double-click says it "can't be opened". If macOS still refuses:
   *System Settings › Privacy & Security* › scroll down › **Open Anyway**. After that it opens normally.
3. RickWire lives in the **menu bar** (the portal icon) and the Dock. Closing the window keeps it running; quit
   from the menu-bar icon or with ⌘Q.
4. When you first pair a phone, macOS asks whether RickWire may **accept incoming network connections**: click
   *Allow*, or phones can't reach it.

- API keys go in your Mac's **Keychain**. After an upgrade macOS may ask once whether RickWire may use them
  (*Always Allow*), because each build has a new ad-hoc signature.
- Settings, queue and certificates: `~/Library/Application Support/RickWire`. Media tools go in its `tools` folder.
- **ffmpeg:** RickWire uses Homebrew's (`brew install ffmpeg`) if you have it, otherwise *Settings › Media tools*
  downloads a static build from [eugeneware/ffmpeg-static](https://github.com/eugeneware/ffmpeg-static) (checked
  against GitHub's SHA-256, like everything else).
- magnet: links and .torrent files: tick the option in first-run setup or *Settings*. If another app still grabs
  .torrent files: select one in Finder › *Get Info* › *Open with* › RickWire › **Change All**.
- **iPhone, home address:** the LAN address uses RickWire's own certificate. On the iPhone, open
  `https://<the-pair-address>/rickwire-ca.crt` in Safari (the *Pair phone* dialog links it), then
  *Settings › General › VPN & Device Management* › install it, and *Settings › General › About › Certificate Trust
  Settings* › turn on full trust. Remote access through your proxy doesn't need this.
- Building it yourself: `bash packaging/mac/build_mac.sh` on a Mac with Python 3.12+. To run the login/proxy
  tests on a Mac, first `sudo ifconfig lo0 alias 127.0.0.2 up` (they use that address as the proxy).

## Media tools (yt-dlp, ffmpeg, Deno)

YouTube, Tubi, archive.org, raw `.mpd` / `.m3u8` manifests and 1000+ other sites go through **yt-dlp**.
**ffmpeg** joins the separate video and audio streams and converts audio-only downloads. **Deno** is the
JavaScript runtime that YouTube now requires.

*Settings › Media tools* shows each tool's version and where it comes from, with Install / Update buttons:
- Downloads come only from the projects' official GitHub releases (yt-dlp/yt-dlp, BtbN/FFmpeg-Builds stable
  builds, denoland/deno), over HTTPS, and only from GitHub hosts.
- Every file is checked against the SHA-256 GitHub publishes for it. A mismatch is thrown away and the working
  copy is kept. ffmpeg and Deno are also test-run before they replace the old version.
- **yt-dlp updates itself** once a day by default, since sites change and old versions break. The new version is
  used after a restart (there's a *Restart* button). ffmpeg and Deno updates are announced with a notification.
- If you already have ffmpeg, Node or Deno on your PATH, RickWire uses them until you install its own copy.

`python -m rickwire.tools status` / `python -m rickwire.tools install ffmpeg deno` do the same from a terminal.

**Subtitles** (*Settings › Downloads*, on by default): for YouTube and other yt-dlp sites, RickWire fetches the
video's subtitles, converts them to SRT and embeds them in the video file, with no loose `.srt` files left over
(the same approach as YT-Playlist-Grabber).
- *Subtitle languages* takes yt-dlp codes, comma-separated: `en.*` (the default) means every English variant,
  and `en, es` gets English and Spanish.
- *Use auto-generated captions* adds YouTube's automatic captions for videos without real ones. YouTube often
  refuses these with "HTTP 429 Too Many Requests", so it's off by default.
- A subtitle that won't download never fails the job: the video is downloaded without it and the job says
  "no subtitles".
- Audio-only downloads skip subtitles. Without ffmpeg, subtitles can't be embedded, so they're saved next to the
  video instead.

## Run from source

```bat
setup.bat        :: first time only - creates .venv and installs requirements
RickWire.bat     :: launches the window (no console)
```

The first-run setup walks you through the rest. You can also do it in **⚙ Settings**:
1. Paste your debrid API key(s), then click **Test**.
2. Click **Register RickWire** under *Magnet & .torrent handler*. After that, magnet links in the browser and
   double-clicked `.torrent` files go straight into the queue. If another torrent app still grabs them, click
   **Windows Default apps…** and pick RickWire.

`run.py --headless` runs just the server and tray icon. `run.py --add "magnet:?..."` adds a link and hands it to the
running instance.

Closing the window sends RickWire to the tray, so your phone can still send it links. Right-click the portal icon to quit.
*Settings › Window* also has **Spin the portal logo**: untick it for a still logo (on paired phones too; it's also
still automatically when your system asks for reduced motion).

## Building on GitHub (Actions)

`.github/workflows/build.yml` builds every client version on GitHub's runners:

| When | What happens |
|---|---|
| Push or pull request | Security tests (provider key audit, login/proxy, phone address), then the Windows installer, the Windows portable zip, the Linux package and the Mac `.dmg`. Download them from the run's **Artifacts** box. |
| Push a tag like `v0.2.8` | Same, then a GitHub **Release** with the four files attached. The tag has to match `__version__` in `rickwire/__init__.py`. |
| *Actions › Build › Run workflow* | A manual build. |

- The Windows job downloads ffmpeg and Deno into the portable zip, using the workflow's own `GITHUB_TOKEN` to
  avoid GitHub's rate limit. No secrets need setting up.
- The Linux job also unpacks the package on Ubuntu, installs its requirements and imports the app, as a smoke test.
- **macOS** (Apple Silicon runner): `packaging/mac/build_mac.sh` builds the `.app` and `.dmg`, then the job tests
  the Keychain, the ffmpeg/Deno downloads, and the app itself (`tests/check_mac_app.py`: it starts, shows its window
  and menu-bar icon, takes magnet links and .torrent files the way Finder sends them, and quits). A screenshot of
  the test run is in the `macos-test` artifact.
- Publishing: `repo\` is a clean copy for GitHub, without builds, downloads or local data. From the project folder:
  ```powershell
  powershell -ExecutionPolicy Bypass -File publish.ps1 -RemoteUrl https://github.com/<you>/RickWire.git  # first time
  powershell -ExecutionPolicy Bypass -File publish.ps1                    # later: refresh repo\, commit, push
  powershell -ExecutionPolicy Bypass -File publish.ps1 -Release           # also tag v<version> -> Release
  ```
  It shows what will be committed and asks before pushing. `-Message "..."` sets the commit message and `-Yes`
  skips the question. It refuses to continue if private info (home IPs, your domain) would end up in `repo\`.
- Windows builds use the pinned versions in `requirements-windows.txt` (the set the releases were tested with).
  `build.ps1` still updates PyInstaller and yt-dlp to the newest versions on every build.

## Phone (PWA)

1. Desktop: **📱 Pair phone** shows a QR code and a 6-digit code (valid for 5 minutes).
2. Scan the QR with your phone on home Wi-Fi. The phone is now paired, and you can revoke it in Settings.
   (With [remote access](#remote-access-outside-your-home-with-nginx-proxy-manager) on, the QR points to your public
   address instead and works anywhere; see *One phone address* there.)
3. **One-time setup so it installs as an app and appears in the Share menu:** on the pairing screen, tap the
   *RickWire certificate* link and install it:
   *Android: Settings › Security › Encryption & credentials › Install a certificate › CA certificate.*
   This CA only works for private IP addresses and your PC's hostname, so it can't vouch for real websites.
4. In Chrome: ⋮ › **Install app**. Then use **Share › RickWire** from YouTube, a browser, or anywhere else to queue a link.

Windows Firewall: allow the LAN port for your home subnet only (run in an admin PowerShell):

```powershell
New-NetFirewallRule -DisplayName "RickWire LAN" -Direction Inbound -Protocol TCP -LocalPort 13701 -RemoteAddress LocalSubnet -Profile Private -Action Allow
```

## Remote access (outside your home) with Nginx Proxy Manager

Off by default. When it's on, the phone/web side of RickWire works from anywhere through your reverse proxy,
behind a username, password and (recommended) 2FA. Signed-in visitors get the same access as a paired phone
(downloads, search, Latest), **never** your API keys, settings or files.

1. **Settings › Remote access › Web login:** pick a username and a password (10+ characters). Then **Set up 2FA**:
   scan the QR code with an authenticator app (Google Authenticator, Aegis, 1Password, Bitwarden…) and type the
   6-digit code to confirm.
2. **Nginx Proxy Manager › Hosts › Proxy Hosts › Add Proxy Host:**
   - *Domain Names:* your public address, e.g. `rickwire.example.com`
   - *Scheme:* `https` (RickWire's default; NPM doesn't need to trust its certificate) or `http` if you turned
     off *Use HTTPS* under Phone / LAN access
   - *Forward Hostname / IP:* this PC's LAN IP. *Forward Port:* `13701`
   - *SSL* tab: *Request a new SSL Certificate* (Let's Encrypt), turn on **Force SSL** and **HSTS**
   - Optional second lock: an NPM *Access List* with its own password
3. **Settings › Remote access › Reverse proxy:** tick *Allow access from outside*, enter the **IP of the machine
   running NPM** (127.0.0.1 if it's this PC) and your **public address**, then Save.
4. Forward port 443 on your router to the NPM machine (you probably already have this for NPM).

How it's locked down:
- Only requests coming **from the NPM machine** are treated as proxied, and only over HTTPS (`X-Forwarded-Proto`).
  RickWire takes the visitor's address from the last `X-Forwarded-For` entry, the one NPM adds itself.
- **Everything through the proxy counts as remote**, even if it claims a home address, so a tunnel or misconfigured
  proxy can't pass as your LAN. The desktop session and phones paired on the LAN don't work through it; only the
  web login and phones paired through it (below) do. Pairing through the proxy needs the long one-time key inside
  the QR code; the 6-digit code alone only works on the LAN address.
- A request with proxy headers from anywhere else is refused. So if you set up NPM but leave remote access off,
  RickWire refuses it instead of mistaking the internet for your home network.
- **Brute force:** 5 wrong tries lock that address for 15 minutes; 30 failures in total pause all sign-ins for 15
  minutes (you get a desktop notification). Every remote sign-in also shows a notification with the address.
- The password is stored only as a scrypt hash (in `config.json`), the 2FA secret in Credential Manager. A 2FA code
  can't be used twice. Sessions last 30 days. *Signed-in browsers* lists them, with sign-out buttons and
  **Sign out everywhere**. Changing the password, or turning 2FA on, signs everyone out.
- *Remove login* switches remote access off as well.

**One phone address, home and away:** while remote access is on, the public address is the phone address. The
📱 Pair phone QR code points to it, and opening the LAN address on a phone forwards there. So a phone uses one
address (one installed app, one sign-in) on Wi-Fi and on mobile data. Your router has to let home devices reach the
public address (most do). Turn remote access off and phones go back to the LAN address.

The web login also works at home as an alternative to pairing. **Tailscale** remains the safer option if only your
own devices need access: nothing is exposed to the internet. Install it on the PC and phone, and add `100.64.0.0/10`
under *Extra allowed networks*.

## Security model (how your API keys stay put)

- **Keys live in Windows Credential Manager** (DPAPI, tied to your Windows account). They are never written to
  config files, never returned by the API (you only see `••••••••abcd`), and never sent to the phone. A log filter
  scrubs them from every log line. All debrid calls are made by the PC, so the phone never needs a key.
- **Two listeners:** `127.0.0.1:13700` (plain HTTP, desktop window only) and `:13701` (HTTPS, for the LAN).
- **Network allowlist:** only loopback and private/link-local IPs get a response. Everything else gets 403. Add extra
  ranges such as Tailscale's `100.64.0.0/10` in Settings if you want them. The one way in from outside is the
  opt-in remote access through your trusted reverse proxy (see above).
- **DNS-rebinding guard:** the `Host` header must be an IP address, `localhost`, your PC's own name, or a public
  address you entered under *Remote access*.
- **Sessions:** the desktop window gets an admin session through a single-use token passed at launch. Phones get a
  random 256-bit device token, which RickWire stores only as a SHA-256 hash, in an `HttpOnly` cookie. Pairing codes
  expire after 5 minutes and stop working after 5 wrong guesses. Web-login sessions (remote access) work the same
  way and last 30 days.
- **Phones (and web-login visitors) can't** view or change keys, settings, paired devices, or files on disk. They can
  only add and manage downloads.
- **CSRF:** every state-changing call needs an `X-RickWire` header plus a same-origin check. There's a strict CSP and no CORS.
- **Magnet handoff:** a random token in `%APPDATA%\RickWire\local.token`, accepted from loopback only.

### Talking to the debrid site

Every call that carries your key goes through a locked-down client:

- **HTTPS only, with certificate verification.** The client refuses plain HTTP before anything is sent.
- **Host allowlist per provider:** `api.real-debrid.com`, `api.alldebrid.com`, `api.torbox.app`, `www.premiumize.me`.
  The key can't be sent anywhere else.
- **Redirects are never followed,** so a redirect can't carry the key to another server.
- **System proxy variables are ignored** (`trust_env=False`), so `HTTP(S)_PROXY` can't route your key through a proxy.
- **The key travels only in the `Authorization: Bearer` header.** It never goes in the URL, so it can't end up in
  server logs. The one exception is TorBox's download-link endpoint, which requires `?token=` in the URL (still
  HTTPS-encrypted, and still only sent to `api.torbox.app`).
- **Magnets and .torrent files are sent in the POST body,** not the URL.
- **Downloads use a separate client with no credentials,** so the CDN serving your file never sees your key.

To re-check all of this yourself, run `.venv\Scripts\python tests\audit_provider_security.py`. It intercepts every
request each provider would make, without real keys or network access, and prints where the key, magnet and
torrent went.

## Where things live

- Settings, job DB, TLS certs, log: `%APPDATA%\RickWire\` (installed / source), `data\<PC-NAME>\` (portable),
  `~/Library/Application Support/RickWire` (Mac)
- Media tools: `tools\` in the app folder
- Downloads: `~\Downloads\RickWire` (change it in Settings)
- Docs: this README, [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) (beginner guide with screenshots; also
  as `docs/GETTING-STARTED.docx`, rebuilt with `python packaging/make_guide_docx.py`),
  [packaging/linux/LINUX.md](packaging/linux/LINUX.md)

## Notes / limits

- **Gofile** now restricts content listing to premium accounts and signs website requests with a rotating,
  obfuscated token. It bans IPs that send forged tokens, so RickWire doesn't imitate the website. Use a debrid
  that supports Gofile, or add a Gofile premium account token in Settings.
- **MEGA native** is subject to MEGA's free transfer quota (~5 GB per few hours). Routing through debrid avoids it.
- **YouTube** needs a JavaScript runtime and **ffmpeg** is needed for merging video + audio. Both can be
  installed from *Settings › Media tools*.
- **Not ported from Decypharr:** the qBittorrent-compatible API for Sonarr/Radarr, the WebDAV/rclone mount, and
  repair. The provider layer is built so these could be added later.
