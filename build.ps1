<#
  Builds RickWire:
    dist\RickWire\                   portable folder (ffmpeg + Deno included, works offline)
    dist\RickWire-portable.zip
    dist\RickWire-Setup-<ver>.exe    installer (per-user, no admin; downloads ffmpeg/Deno on first run)
    dist\RickWire-linux-portable.tar.gz   Linux: source + run.sh, sets itself up on first run

    .\build.ps1               everything
    .\build.ps1 -NoTools      portable folder without ffmpeg/Deno (first-run setup downloads them)
    .\build.ps1 -NoZip
    .\build.ps1 -NoInstaller

  Re-run any time to pick up newer yt-dlp / ffmpeg / Deno. The app can also update them itself
  (Settings > Media tools), so rebuilding isn't required for that any more.
#>
param([switch]$NoTools, [switch]$NoZip, [switch]$NoInstaller)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run setup.bat first" }
$version = (& $py -c "import rickwire; print(rickwire.__version__)").Trim()

Write-Host "== RickWire $version - updating build deps + yt-dlp" -ForegroundColor Green
& $py -m pip install -q --upgrade pyinstaller "yt-dlp[default]"
& $py -m rickwire.icons

Write-Host "== PyInstaller" -ForegroundColor Green
& $py -m PyInstaller --noconfirm --clean --windowed --log-level WARN --name RickWire `
    --icon rickwire\web\icons\rickwire.ico `
    --add-data "rickwire\web;rickwire\web" `
    --collect-submodules uvicorn `
    --collect-all yt_dlp_ejs `
    --collect-all webview `
    --collect-submodules wormhole `
    --collect-all autobahn `
    --collect-submodules twisted.internet `
    --copy-metadata yt-dlp `
    --hidden-import keyring.backends.Windows `
    --hidden-import pystray._win32 `
    --hidden-import qrcode.image.svg `
    --exclude-module tkinter `
    run.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "dist\RickWire\RickWire.exe")) { throw "PyInstaller failed" }

$dist = "dist\RickWire"
Copy-Item README.md "$dist\README.md"
Copy-Item docs "$dist\docs" -Recurse -Force   # beginner guide + screenshots (README links to it)
# lets pythonnet / WebView2 .NET DLLs load from a network share or a zip Windows marked as downloaded
Copy-Item rickwire.exe.config "$dist\RickWire.exe.config"

# The installer is built from the folder *before* tools and PORTABLE.txt are added.
if (-not $NoInstaller) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($iscc) {
        Write-Host "== Installer (Inno Setup)" -ForegroundColor Green
        Get-ChildItem dist -Filter "RickWire-Setup-*.exe" | Remove-Item
        & $iscc /Q "/DAppVersion=$version" installer.iss
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    } else { Write-Warning "Inno Setup 6 not found (winget install JRSoftware.InnoSetup) - skipping installer" }
}

if (-not $NoTools) {
    Write-Host "== Downloading ffmpeg + Deno (SHA-256 verified) into the portable folder" -ForegroundColor Green
    & $py -m rickwire.tools install ffmpeg deno --dest "$dist\tools"
    if ($LASTEXITCODE -ne 0) { throw "Tool download failed" }
}

@"
RickWire portable
=================
Run RickWire.exe. Copy this whole folder to any Windows 10/11 PC (WebView2 is built in).

* Settings, queue and certificates live in data\<PC NAME>\ - each PC keeps its own, so the folder
  can be copied around or even run from a network share on several PCs at once.
* API keys are stored in each PC's Windows Credential Manager (never in this folder) - paste them
  once per PC in Settings. Copying the folder never copies your keys.
* Keep RickWire.exe.config next to RickWire.exe.
* tools\ holds ffmpeg + Deno for yt-dlp. RickWire keeps them (and yt-dlp) updated: Settings > Media tools.
* Click Settings > Register RickWire on each PC to handle magnet links / .torrent files.
* Phone access: allow RickWire.exe through Windows Firewall on Private networks when asked.
* This file marks the folder as portable. Without it RickWire keeps its data in %APPDATA% instead.
"@ | Set-Content "$dist\PORTABLE.txt" -Encoding utf8

if (-not $NoZip) {
    Write-Host "== Zipping" -ForegroundColor Green
    $zip = "dist\RickWire-portable.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Optimal
}
Write-Host "== Linux portable (source + run.sh)" -ForegroundColor Green
& $py packaging\make_linux_portable.py
if ($LASTEXITCODE -ne 0) { throw "Linux package failed" }

$size = (Get-ChildItem $dist -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("== Done: {0}  ({1:N0} MB)" -f (Resolve-Path $dist), $size) -ForegroundColor Green
Get-ChildItem dist -File | ForEach-Object { Write-Host ("   {0}  ({1:N0} MB)" -f $_.Name, ($_.Length / 1MB)) }
