#ifndef AppVersion
  #error Pass the version from config.py as AppVersion.
#endif
#ifndef AppExe
  #error Pass the path to the packaged application as AppExe.
#endif

#define AppName "PDF Utility Suite"
#define InstalledExe "PDF-Merger-Compressor.exe"

[Setup]
AppId={{06D43D9E-FA9D-4AD6-A999-BB3405A747F1}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DisableDirPage=auto
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
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
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpInfoBefore) and
    (GetPreviousData('PrivacyNoticeShown', '') = 'yes');
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'PrivacyNoticeShown', 'yes');
end;
