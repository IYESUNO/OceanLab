; Stable AppId is deliberately separate from the Windows 10/11 edition.
#ifndef AppIdOverride
  #define AppIdOverride "OceanLab.Win7.x64"
#endif
#ifndef RunningMutex
  #define RunningMutex "Local\OceanLab.Win7.Running"
#endif
[Setup]
AppId={#AppIdOverride}
AppName=OceanLab (Win7)
AppVersion={#AppVersion}
AppPublisher=OceanLab
DefaultDirName={localappdata}\Programs\OceanLab-Win7
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=6.1
WizardStyle=modern
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableDirPage=auto
UninstallDisplayIcon={app}\OceanLab-{#AppVersion}.exe
OutputDir={#ReleaseDir}
OutputBaseFilename=OceanLab-{#AppVersion}-win7-x64-setup
SetupIconFile={#ProjectDir}\assets\app_icons\oceanlab-icon-spectrum.ico
Compression=lzma2
SolidCompression=yes
AppMutex={#RunningMutex}
SetupMutex=Local\{#AppIdOverride}.Setup
CloseApplications=no
RestartApplications=no
InfoBeforeFile={#ProjectDir}\packaging\INSTALLER_README_WIN7.txt

[LangOptions]
LanguageName=简体中文
LanguageID=$0804
LanguageCodePage=936
DialogFontName=Microsoft YaHei

[Messages]
SetupAppTitle=安装
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=卸载 %1
InformationTitle=信息
ConfirmTitle=确认
ErrorTitle=错误
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&B)...
ButtonWizardBrowse=浏览(&R)...
ButtonNewFolder=新建文件夹(&M)
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=将在此电脑安装 [name/ver]。%n%n升级前请保存需要的数据并退出 OceanLab。
ClickNext=点击“下一步”继续，或点击“取消”退出。
WizardInfoBefore=安装说明
InfoBeforeLabel=请阅读以下安装与升级说明。
InfoBeforeClickLabel=阅读后点击“下一步”继续。
WizardSelectDir=安装位置
SelectDirDesc=选择 [name] 的安装位置。
SelectDirLabel3=程序将安装到以下文件夹。
SelectDirBrowseLabel=点击“下一步”继续，或点击“浏览”选择其他位置。
DiskSpaceMBLabel=至少需要 [mb] MB 可用空间。
WizardReady=准备安装
ReadyLabel1=现在可以安装 [name]。
ReadyLabel2a=点击“安装”开始，或点击“上一步”检查设置。
ReadyLabel2b=点击“安装”开始。
ReadyMemoDir=安装位置：
WizardPreparing=准备安装
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候。
StatusCreateDirs=正在创建文件夹...
StatusExtractFiles=正在写入程序文件...
StatusCreateIcons=正在更新快捷方式...
StatusSavingUninstall=正在保存卸载信息...
StatusRunProgram=正在完成安装...
StatusRollback=正在撤销变更...
FinishedHeadingLabel=[name] 安装完成
FinishedLabel=已安装 [name]。以后可通过桌面或开始菜单快捷方式启动。
FinishedLabelNoIcons=已安装 [name]。
ClickFinish=点击“完成”退出安装程序。
SetupAppRunningError=%1 或其设备进程仍在运行。%n%n请保存数据并退出程序后继续安装。
UninstallAppRunningError=%1 或其设备进程仍在运行。%n%n请退出程序后继续卸载。
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。现在退出吗？
ConfirmUninstall=确定卸载 %1 吗？%n%n用户设置和光谱数据将保留。
UninstalledAll=已卸载 %1。用户设置和光谱数据已保留。
UninstalledMost=已完成 %1 卸载。部分文件未能删除，可以稍后手动处理。
WizardUninstalling=正在卸载
StatusUninstalling=正在卸载 %1...

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
#ifdef TestMode
Name: "{app}\test-shortcuts\OceanLab (Win7)"; Filename: "{app}\OceanLab-{#AppVersion}.exe"; WorkingDir: "{app}"
#else
Name: "{userdesktop}\OceanLab (Win7)"; Filename: "{app}\OceanLab-{#AppVersion}.exe"; WorkingDir: "{app}"
Name: "{userprograms}\OceanLab (Win7)"; Filename: "{app}\OceanLab-{#AppVersion}.exe"; WorkingDir: "{app}"
#endif

[Run]
Filename: "{app}\OceanLab-{#AppVersion}.exe"; Description: "启动 OceanLab"; Flags: nowait postinstall skipifsilent

[Code]
var
  PreviousFiles: TArrayOfString;

function SafeRelativeFile(const Name: String): Boolean;
begin
  Result := False;
  if Name = '' then Exit;
  Result := (Pos(':', Name) = 0) and
    (Name[1] <> '\') and (Name[1] <> '/') and
    (Pos('..', Name) = 0) and (Pos('/', Name) = 0);
end;

function VersionIsNewer(OldVersion, NewVersion: String): Boolean;
var
  I, A, B, P: Integer;
  S: String;
begin
  Result := False;
  for I := 1 to 4 do begin
    P := Pos('.', OldVersion + '.');
    S := Copy(OldVersion, 1, P - 1);
    A := StrToIntDef(S, 0);
    Delete(OldVersion, 1, P);
    P := Pos('.', NewVersion + '.');
    B := StrToIntDef(Copy(NewVersion, 1, P - 1), 0);
    Delete(NewVersion, 1, P);
    if A > B then begin Result := True; Exit; end;
    if A < B then Exit;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  OldVersion, Directory: String;
  Marker: AnsiString;
begin
  Result := '';
  if CheckForMutexes('{#RunningMutex}') then begin
    Result := 'OceanLab 或设备进程仍在运行，请退出程序后重试。';
    Exit;
  end;
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdOverride}_is1', 'DisplayVersion', OldVersion) then
    if VersionIsNewer(OldVersion, '{#AppVersion}') then begin
      Result := '已安装较新版本 ' + OldVersion + '，不能覆盖安装旧版本。';
      Exit;
    end;
  Directory := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  if (Length(Directory) <= 3) or
     (CompareText(Directory, ExpandConstant('{win}')) = 0) or
     (Pos(Lowercase(ExpandConstant('{win}')) + '\', Lowercase(Directory) + '\') = 1) or
     (Pos('c:\oceandirect\', Lowercase(Directory) + '\') = 1) or
     DirExists(Directory + '\Python\oceandirect') then begin
    Result := '请选择独立的程序文件夹，不要安装到系统或 OceanDirect 驱动目录。';
    Exit;
  end;
  if LoadStringFromFile(Directory + '\.oceanlab-installation.txt', Marker) then
    if Trim(Marker) = 'OceanLab.Win7.Managed.v1' then
      LoadStringsFromFile(Directory + '\.oceanlab-managed-files.txt', PreviousFiles);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  CurrentFiles: TArrayOfString;
  I, J: Integer;
  Found: Boolean;
begin
  if CurStep <> ssPostInstall then Exit;
  if not LoadStringsFromFile(ExpandConstant('{app}\.oceanlab-managed-files.txt'), CurrentFiles) then Exit;
  for I := 0 to GetArrayLength(PreviousFiles) - 1 do begin
    if SafeRelativeFile(PreviousFiles[I]) then begin
      Found := False;
      for J := 0 to GetArrayLength(CurrentFiles) - 1 do
        if CompareText(PreviousFiles[I], CurrentFiles[J]) = 0 then Found := True;
      if not Found then begin
        if not DeleteFile(ExpandConstant('{app}\') + PreviousFiles[I]) then
          Log('Old managed file could not be removed: ' + PreviousFiles[I]);
      end;
    end;
  end;
end;
