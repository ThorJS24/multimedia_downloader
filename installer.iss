; Script generated for Spotify Downloader Studio Inno Setup Installer
; Non-elevated, per-user installation with clean upgrade & safe uninstall

#define MyAppName "Spotify Downloader Studio"
#ifndef MyAppVersion
  #define MyAppVersion "2.0.0"
#endif
#define MyAppPublisher "Spotify Downloader Studio Team"
#define MyAppURL "https://github.com/spotify-downloader-studio"
#define MyAppExeName "SpotifyDownloader.exe"
#define MySourceDir "dist\SpotifyDownloader"

[Setup]
; App Identity
AppId={{8B84F074-9149-4CB5-9E5E-C3401BCEBE62}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; Installation target - per user non-elevated
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Output configuration
OutputDir=dist
OutputBaseFilename=SpotifyDownloader-v{#MyAppVersion}-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; Compression & Architecture
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; UI and Wizard
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Distribute all files from dist\SpotifyDownloader except portable marker if present
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "portable.dat,portable"
; Include icon
Source: "assets\icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any logs or temporary artifacts in application directory without touching user data
Type: files; Name: "{app}\*.log"
Type: dirifempty; Name: "{app}\assets"
Type: dirifempty; Name: "{app}"

[Code]
// Pascal script to inform user if user data directory will be preserved during uninstall
function InitializeUninstall(): Boolean;
begin
  Result := True;
end;
