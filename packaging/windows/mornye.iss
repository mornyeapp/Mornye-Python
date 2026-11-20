; Inno Setup script of the Windows installer (Mornye-Windows-Setup.exe).
;
;   iscc /DAppVersion=2.2.1 packaging\windows\mornye.iss
;
; Packs dist\Mornye (the one-folder build of packaging\mornye-desktop.spec).
; Installs per user by default, so no administrator prompt; the first page
; still offers an install for all users. Start menu entry, optional desktop
; icon, and an uninstaller in Settings > Apps.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Mornye"
#define AppExe "Mornye.exe"
#define Root "..\.."

[Setup]
; Never change AppId: it is how a newer installer finds the installed copy
; to upgrade instead of installing a second one beside it.
AppId={{5B0E6C1D-9A0E-4E3B-8D7C-6F2A1B4C9E21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Mornye
AppPublisherURL=https://github.com/mornyeapp/Mornye-Python
AppSupportURL=https://github.com/mornyeapp/Mornye-Python/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\dist
OutputBaseFilename=Mornye-Windows-Setup
SetupIconFile={#Root}\mornye\frontend\assets\icons\logo.ico
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile={#Root}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#Root}\dist\Mornye\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An upgrade replaces the bundled runtime wholesale: a module left over from
; the previous version could shadow the new one.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "Mornye.Desktop"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "Mornye.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
