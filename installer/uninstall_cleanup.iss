const
  ProfilesKey = 'SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList';
  DataDirectoryName = 'PDFUtilitySuite';

function FileAttributes(Path: String): DWORD;
  external 'GetFileAttributesW@kernel32.dll stdcall uninstallonly';

function ExpandEnvironment(Source, Destination: String; Size: DWORD): DWORD;
  external 'ExpandEnvironmentStringsW@kernel32.dll stdcall uninstallonly';

function IsLocalDirectoryTree(Path: String): Boolean;
var
  Attributes: DWORD;
begin
  Result := False;
  if (Length(Path) < 3) or (Copy(Path, 2, 2) <> ':\') then
    Exit;

  { An elevated uninstall must not follow a junction in a user-writable folder. }
  while Length(Path) > 3 do
  begin
    Attributes := FileAttributes(Path);
    if (Attributes = DWORD($FFFFFFFF)) or
      ((Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0) or
      ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0) then
      Exit;
    Path := ExtractFileDir(Path);
  end;
  Result := True;
end;

procedure DeleteAppFile(const Path: String);
var
  Attributes: DWORD;
begin
  Attributes := FileAttributes(Path);
  if (Attributes <> DWORD($FFFFFFFF)) and
    ((Attributes and (FILE_ATTRIBUTE_DIRECTORY or FILE_ATTRIBUTE_REPARSE_POINT)) = 0) then
  begin
    if not DeleteFile(Path) then
      Log('Could not delete application data file: ' + Path);
  end;
end;

procedure CleanLogs(const Directory: String);
var
  BasePath: String;
  Index: Integer;
begin
  if not IsLocalDirectoryTree(Directory) then
    Exit;

  BasePath := AddBackslash(Directory);
  DeleteAppFile(BasePath + 'app.txt');
  for Index := 1 to 3 do
    DeleteAppFile(BasePath + 'app.txt.' + IntToStr(Index));
  DeleteAppFile(BasePath + '.app.lock');
  RemoveDir(Directory);
end;

procedure CleanUserData(const LocalAppData: String);
var
  Directory, BasePath: String;
  FindRec: TFindRec;
begin
  if LocalAppData = '' then
    Exit;

  Directory := AddBackslash(LocalAppData) + DataDirectoryName;
  if not IsLocalDirectoryTree(Directory) then
    Exit;

  BasePath := AddBackslash(Directory);
  DeleteAppFile(BasePath + 'settings.json');
  { The app creates these while saving settings atomically. }
  if FindFirst(BasePath + '.settings-*.json', FindRec) then
  begin
    try
      repeat
        DeleteAppFile(BasePath + FindRec.Name);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  CleanLogs(BasePath + 'Logs');
  RemoveDir(Directory);
end;

procedure CleanLocalProfiles;
var
  ProfileIds: TArrayOfString;
  Index: Integer;
  ProfileId, Value, ProfilePath: String;
  Required: DWORD;
begin
  if not RegGetSubkeyNames(HKLM64, ProfilesKey, ProfileIds) then
    Exit;

  for Index := 0 to GetArrayLength(ProfileIds) - 1 do
  begin
    ProfileId := Uppercase(ProfileIds[Index]);
    if ((Pos('S-1-5-21-', ProfileId) = 1) or (Pos('S-1-12-1-', ProfileId) = 1)) and
      (Pos('.', ProfileId) = 0) and
      RegQueryStringValue(HKLM64, ProfilesKey + '\' + ProfileId, 'ProfileImagePath', Value) then
    begin
      Required := ExpandEnvironment(Value, '', 0);
      if Required > 1 then
      begin
        SetLength(ProfilePath, Required);
        if ExpandEnvironment(Value, ProfilePath, Required) = Required then
          CleanUserData(AddBackslash(Copy(ProfilePath, 1, Required - 1)) + 'AppData\Local');
      end;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    CleanLogs(ExpandConstant('{app}\Logs'));
    CleanUserData(ExpandConstant('{localappdata}'));
    if IsAdminInstallMode then
      CleanLocalProfiles;
  end;
end;
