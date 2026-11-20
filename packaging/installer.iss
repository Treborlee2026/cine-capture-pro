; Inno Setup script for the Cine Capture Pro direct-download installer.
; Built automatically by .github/workflows/build.yml.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F1C2B7A-3E54-4C21-9B8E-CC0A1D5F2E11}
AppName=Cine Capture Pro
AppVersion={#AppVersion}
AppPublisher=Cine Capture Pro contributors
AppPublisherURL=https://github.com/Treborlee2026/cine-capture-pro
DefaultDirName={autopf}\Cine Capture Pro
DefaultGroupName=Cine Capture Pro
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=CineCapturePro-{#AppVersion}-Setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\CineCapturePro.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\CineCapturePro\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Cine Capture Pro"; Filename: "{app}\CineCapturePro.exe"
Name: "{group}\Cine Capture Pro (demo, no hardware)"; Filename: "{app}\CineCapturePro.exe"; Parameters: "--demo"
Name: "{autodesktop}\Cine Capture Pro"; Filename: "{app}\CineCapturePro.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CineCapturePro.exe"; Description: "Start Cine Capture Pro"; Flags: nowait postinstall skipifsilent
