#ifndef AppVersion
  #error Pass the version from config.py as AppVersion.
#endif
#ifndef AppExe
  #error Pass the path to the packaged application as AppExe.
#endif

#define AppName "PDF Utility Suite"
#define AppGuid "06D43D9E-FA9D-4AD6-A999-BB3405A747F1"
#define InstalledExe "PDF-Utility-Suite.exe"
#define LegacyExe "PDF-Merger-Compressor.exe"

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DisableDirPage=auto
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
AppMutex=PDFUtilitySuite.Running,Global\PDFUtilitySuite.Running
RedirectionGuard=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
InfoBeforeFile=privacy-notice.txt
UninstallDisplayIcon={app}\{#InstalledExe}
OutputBaseFilename=PDF-Utility-Suite-Setup-{#AppVersion}

[Messages]
WizardInfoBefore=Privacy notice
InfoBeforeLabel=Read how PDF Utility Suite handles your documents and diagnostic logs.

[Dirs]
Name: "{app}\Logs"; Permissions: users-modify

[Files]
Source: "{#AppExe}"; DestDir: "{app}"; DestName: "{#InstalledExe}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#InstalledExe}"; WorkingDir: "{app}"

[Code]
const
  InstallRecordKey = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppGuid}}_is1';

var
  LegacyExeToRemove: String;

procedure InitializeWizard;
var
  RootKey: HKEY;
  InstallDir, DisplayIcon, LegacyPath: String;
begin
  RootKey := HKCU;
  if IsAdminInstallMode then
    RootKey := HKLM;

  if RegQueryStringValue(RootKey, InstallRecordKey, 'InstallLocation', InstallDir) and
    (InstallDir <> '') and
    RegQueryStringValue(RootKey, InstallRecordKey, 'DisplayIcon', DisplayIcon) then
  begin
    LegacyPath := AddBackslash(InstallDir) + '{#LegacyExe}';
    if CompareText(DisplayIcon, LegacyPath) = 0 then
      LegacyExeToRemove := LegacyPath;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  LegacyPath: String;
begin
  if (CurStep <> ssDone) or (LegacyExeToRemove = '') then
    Exit;

  LegacyPath := ExpandConstant('{app}\{#LegacyExe}');
  if (CompareText(LegacyPath, LegacyExeToRemove) = 0) and FileExists(LegacyPath) and
    FileExists(ExpandConstant('{app}\{#InstalledExe}')) then
  begin
    if DeleteFile(LegacyPath) then
      Log('Removed the superseded installed executable: ' + LegacyPath)
    else
      Log('Could not remove the superseded installed executable: ' + LegacyPath);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpInfoBefore) and
    (GetPreviousData('PrivacyNoticeShown', '') = 'yes');
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'PrivacyNoticeShown', 'yes');
end;

#include "uninstall_cleanup.iss"
