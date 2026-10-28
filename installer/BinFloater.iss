; -*- mode: python ; coding: utf-8 -*-
; Inno Setup 脚本：把 dist\BinFloater\ 打成一个安装包。
;
; 为什么不用 Program Files：
;   - 程序不需要管理员权限，装到用户可写的位置就不弹 UAC；
;   - 用户自己拥有安装目录的写权限，升级时解压覆盖不会被拒；
;   - 卸载不需要提权，控制面板里点一下就干净了。
;
; 默认安装位置由 GetDefaultDir 在装的时候现算：挑第一个「不是 C 盘、且是硬盘」
; 的盘符装到 <盘>:\Programs\BinFloater；只有一个 C 盘才退回用户目录。
; 目录页永远显示（DisableDirPage=no），用户随时可以改成别的位置。
;
; 为什么升级能直接覆盖：AppId 是一个固定 GUID，Inno Setup 靠它认「同一个软件」。
; 新版本安装包双击后走的是「升级」分支，先关掉正在跑的进程再覆盖文件，
; 用户不用先手动卸载。改 AppId 会导致新旧版本同时装着。

#define AppName "编码悬浮窗"
#define AppExe "BinFloater.exe"
#define AppVersion GetEnv("BINF_VERSION")
#if AppVersion == ""
  #define AppVersion "0.0.0"
#endif

[Setup]
; 固定 GUID —— 升级识别全靠它，永远不要改。
AppId={{6E078910-A416-4F0B-91CE-0B0130B04265}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=BlestHeart
AppPublisherURL=https://github.com/BlestHeart/BinFloater
AppSupportURL=https://github.com/BlestHeart/BinFloater/issues
AppUpdatesURL=https://github.com/BlestHeart/BinFloater/releases
; 默认目录现算：由 [Code] 里的 GetDefaultDir 挑第一个非 C 盘的固定硬盘。
; 目录页永远显示（DisableDirPage=no），用户随时能改。
DefaultDirName={code:GetDefaultDir}
DisableDirPage=no
DefaultGroupName={#AppName}
; 不需要管理员：装到用户可写的位置
PrivilegesRequired=lowest
; 装到别的盘时不用管理员也能装，但别让 Inno 因为「目录在 Program Files」就提权
PrivilegesRequiredOverridesAllowed=dialog
; 输出
OutputDir=..\dist
OutputBaseFilename=BinFloater-Setup-{#AppVersion}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
; 26MB 的 onedir 用 lzma2/max 能压到 10MB 左右
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 只支持 64 位 Windows 10/11
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; 升级时允许覆盖：检测到文件被占用会让用户先关掉
CloseApplications=yes
RestartApplications=no
; 允许在卸载列表里显示大小
DisableProgramGroupPage=yes
DisableWelcomePage=no
LicenseFile=..\LICENSE

[Languages]
Name: "cn"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked
Name: "autostart"; Description: "开机自动启动（最小化到托盘）"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; 整个 onedir 目录打进去。excludes 排除掉上次安装可能残留的东西。
Source: "..\dist\BinFloater\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; onedir 升级时旧版本会留下已经不存在的模块文件，不删掉的话下次启动
; 可能导入到旧的 .pyd/.pyc 上，出些说不清的毛病。整个 _internal 先清空。
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\BinFloater.exe"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: autostart

[Run]
; 勾选「立即运行」才启动。不勾也能在开始菜单找到。
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; 写一个卸载信息键，控制面板「应用和功能」里能看到版本号。
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\BinFloater_is1"; ValueType: string; ValueName: "DisplayVersion"; ValueData: "{#AppVersion}"; Flags: uninsdeletekey

[UninstallDelete]
; 安装目录里程序运行产生的临时文件（正常情况下没有），保险起见清掉。
Type: filesandordirs; Name: "{app}"

[Code]
{ ---------- 挑一个不在 C 盘的默认安装位置 ----------

  用户的硬要求是「不准在 C 盘拉屎」。做法：从 D 盘开始往后找，
  只要满足这两条就用它：
    1. 是「本机固定硬盘」（DRIVE_FIXED）。排除光驱、U 盘、网络盘 ——
       装到这些地方要么拔了就跑不起来，要么慢得要死；
    2. 真的能往里写文件。有些数据盘根目录只给管理员写权限，
       光看盘符存在没用，得实测。
  第一块合格的盘就装到 <盘>:\Programs\BinFloater。
  一块都没有（只有 C 盘的机器）才退回用户目录。 }

function GetDriveTypeA(aRootPath: AnsiString): Cardinal;
  external 'GetDriveTypeA@kernel32.dll stdcall';

{ 真的建目录、真的写文件、再删掉。比只看盘符存在可靠得多。 }
function CanWriteUnder(Root: String): Boolean;
var
  Folder, Probe: String;
begin
  Result := False;
  Folder := Root + 'Programs';
  if not ForceDirectories(Folder) then
    Exit;
  Probe := Folder + '\.write-probe';
  if not SaveStringToFile(Probe, 'x', False) then
    Exit;
  DeleteFile(Probe);
  Result := True;
end;

function GetDefaultDir(Param: String): String;
var
  code: Integer;
  root: String;
begin
  { Pascal Script 的 for 只吃整数，所以按字符编码循环：D=68 .. Z=90 }
  for code := 68 to 90 do
  begin
    root := Chr(code) + ':\';
    { DRIVE_FIXED = 3：本机固定硬盘，排除光驱(5)/可移动(2)/网络(4)/内存盘(6) }
    if GetDriveTypeA(root) = 3 then
    begin
      if CanWriteUnder(root) then
      begin
        Result := root + 'Programs\BinFloater';
        Exit;
      end;
    end;
  end;
  { 一个非 C 盘都没有：退回用户目录，至少不用管理员权限 }
  Result := ExpandConstant('{localappdata}\Programs\BinFloater');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Target, Root: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;

  Target := WizardForm.DirEdit.Text;

  { 装到 C 盘不拦死，但要提醒 —— 用户可能就是想装那儿，别硬顶。
     默认值已经避开 C 盘了，走到这里说明是用户自己改的。 }
  if Pos('C:\', UpperCase(Target)) = 1 then
  begin
    if MsgBox('你选的是 C 盘。' + #13#10 + #13#10 +
              '装在 C 盘会占系统盘空间，以后重装系统还得再装一遍。' + #13#10 +
              '要回去换个位置吗？', mbConfirmation, MB_YESNO) = IDYES then
    begin
      Result := False;
      Exit;
    end;
  end;

  { 光驱/U 盘/网络盘装不进去，提前说清楚，别等解压到一半才报错 }
  if (Length(Target) >= 3) and (Target[2] = ':') and (Target[3] = '\') then
  begin
    Root := Copy(Target, 1, 3);
    if GetDriveTypeA(Root) <> 3 then
    begin
      MsgBox('这个位置不可用：' + Root + ' 不是本机的固定硬盘' + #13#10 +
             '（可能是 U 盘、光驱或网络位置）。换一个目录吧。',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ 老版本在开始菜单里叫「二进制悬浮窗」。改名前装过的用户升级上来，
  新名字会建一个新文件夹，旧文件夹和里面的快捷方式留在原地 —— 还能用，
  但同一个软件在开始菜单里出现两份，看着像没卸干净。

  所以装的时候顺手把旧名字的文件夹删掉。AppId 没变，安装目录也没变
  （DefaultDirName 里写死的是 BinFloater，不是 AppName），删掉的只是快捷方式。
  PrivilegesRequired=lowest 装的是当前用户的开始菜单，所以路径取 userprograms。 }
procedure CleanOldShortcuts();
var
  OldDir: String;
begin
  OldDir := ExpandConstant('{userprograms}\二进制悬浮窗');
  if DirExists(OldDir) then
    DelTree(OldDir, True, True, True);
  DeleteFile(ExpandConstant('{autodesktop}\二进制悬浮窗.lnk'));
  DeleteFile(ExpandConstant('{userstartup}\二进制悬浮窗.lnk'));
end;

{ 升级前先优雅关掉正在运行的实例。

  为什么不在 [Files] 里靠 CloseApplications：那个机制是给 install 阶段用的，
  而我们的程序是托盘常驻，点了关闭只是收进托盘不会退出。这里在
  PrepareToInstall 阶段直接发 WM_CLOSE 给它，等它自己存完配置再动手，
  然后再兜底强杀。 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM BinFloater.exe /F', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  { 给它一点时间把 config.json 写完 }
  Sleep(300);
  CleanOldShortcuts();
  Result := '';
end;

{ 卸载时如果程序还在跑，同样先关掉，否则文件删不干净。 }
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM BinFloater.exe /F', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Sleep(200);
  Result := True;
end;
