; RickWire installer (Inno Setup 6). Built by build.ps1:  ISCC /DAppVersion=x.y.z installer.iss
; Per-user install - no admin prompt. Media tools (ffmpeg / Deno) are downloaded by the first-run setup,
; which keeps the installer small; yt-dlp is built in.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F4C2B1E-9D3A-4E7B-8A51-52C1D0A7E3B9}
AppName=RickWire
AppVersion={#AppVersion}
AppVerName=RickWire {#AppVersion}
AppPublisher=RickWire
DefaultDirName={localappdata}\Programs\RickWire
DefaultGroupName=RickWire
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=RickWire-Setup-{#AppVersion}
SetupIconFile=rickwire\web\icons\rickwire.ico
UninstallDisplayIcon={app}\RickWire.exe
UninstallDisplayName=RickWire
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; a running RickWire is asked to quit by the [Code] below; Restart Manager is the fallback
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "associate"; Description: "Open magnet links and .torrent files with RickWire"
Name: "startup"; Description: "Start RickWire in the tray when I sign in"; Flags: unchecked

[Files]
; the portable marker, per-PC data and bundled tools stay out of the installed copy
Source: "dist\RickWire\*"; DestDir: "{app}"; Excludes: "\PORTABLE.txt,\data\*,\tools\*"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\RickWire"; Filename: "{app}\RickWire.exe"
Name: "{autodesktop}\RickWire"; Filename: "{app}\RickWire.exe"; Tasks: desktopicon
Name: "{userstartup}\RickWire"; Filename: "{app}\RickWire.exe"; Parameters: "--minimized"; Tasks: startup

[Run]
Filename: "{app}\RickWire.exe"; Parameters: "--register"; Tasks: associate; Flags: runhidden waituntilterminated; \
  StatusMsg: "Registering magnet and .torrent handler..."
Filename: "{app}\RickWire.exe"; Description: "Launch RickWire"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\RickWire.exe"; Parameters: "--unregister"; Flags: runhidden waituntilterminated; RunOnceId: "Unregister"

[UninstallDelete]
; tools downloaded after install (ffmpeg, Deno, yt-dlp updates)
Type: filesandordirs; Name: "{app}\tools"

[Messages]
FinishedLabel=RickWire is installed. The first time it opens, a short setup walks you through adding a debrid service (or skipping it) and downloading the media tools.

[Code]
procedure QuitRickWire(Exe: String);
var
  Rc: Integer;
begin
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Rc);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitRickWire(ExpandConstant('{app}\RickWire.exe'));   { upgrading over a running copy }
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  QuitRickWire(ExpandConstant('{app}\RickWire.exe'));
  Result := True;
end;
