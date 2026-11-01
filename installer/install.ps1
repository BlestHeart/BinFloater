# 编码悬浮窗（BinFloater）安装脚本 —— 不经过任何安装器框架，直接铺文件。
#
# 为什么要有这个脚本：Inno Setup 做的安装器在部分机器上起不来，报
#   "安装程序无法创建目录 ...\Temp\is-XXXX.tmp。错误 5: 拒绝访问"
# 那套流程必须先把安装引擎释放到临时目录再执行，一旦临时目录被安全软件
# 或权限策略挡住就彻底没救（换 TEMP 位置、换盘符、改完整性级别都试过）。
# 这个脚本不释放任何可执行文件，只复制文件、建快捷方式、登记卸载项，
# 整条链路都绕开了。只写 HKCU 和用户目录，不需要管理员权限。
#
# 用法：
#   双击同目录下的「安装.bat」
#   或 powershell -ExecutionPolicy Bypass -File install.ps1 [-Dir 目标目录] [-Desktop] [-NoShortcut] [-Quiet]

param(
    [string]$Dir = "",
    [string]$Version = "",
    [switch]$Desktop,
    [switch]$NoShortcut,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$AppName = "编码悬浮窗"
$AppId   = "BinFloater"
$ExeName = "BinFloater.exe"
$Nl = [Environment]::NewLine

function Say([string]$msg) {
    if (-not $Quiet) { Write-Host $msg }
}

# 脚本所在目录。压缩包里脚本和 BinFloater 文件夹是并排的。
$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Source = $null
foreach ($c in @((Join-Path $Here $AppId), (Join-Path $Here "dist\$AppId"), $Here)) {
    if (Test-Path (Join-Path $c $ExeName)) { $Source = $c; break }
}
if (-not $Source) {
    Write-Host "找不到 $ExeName。" -ForegroundColor Red
    Write-Host "请把本脚本和 $AppId 文件夹放在同一个目录里，也就是解压出来的那个目录。" -ForegroundColor Red
    exit 2
}

# 默认位置：优先非 C 盘，其次用户目录。目的就是「别往 C 盘塞」。
if (-not $Dir) {
    $default = ""
    foreach ($d in @("D", "E", "F")) {
        if (Test-Path ($d + ":\")) { $default = $d + ":\Programs\" + $AppId; break }
    }
    if (-not $default) { $default = Join-Path $env:LOCALAPPDATA ("Programs\" + $AppId) }

    if ($Quiet) {
        $Dir = $default
    } else {
        Add-Type -AssemblyName System.Windows.Forms | Out-Null
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = "选一个安装位置（不想要 C 盘就改成别的盘）"
        $dlg.SelectedPath = $default
        $dlg.ShowNewFolderButton = $true
        if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            Write-Host "已取消。"
            exit 0
        }
        $Dir = $dlg.SelectedPath
    }
}

$Dir = [System.IO.Path]::GetFullPath($Dir)
Say ("安装到：" + $Dir)
if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }

# 先清掉旧的 exe 和 _internal，不然 onedir 升级会残留旧模块
Get-ChildItem $Dir -Filter $ExeName -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
$oldInternal = Join-Path $Dir "_internal"
if (Test-Path $oldInternal) { Remove-Item $oldInternal -Recurse -Force -ErrorAction SilentlyContinue }

Say "复制文件…"
Copy-Item -Path (Join-Path $Source "*") -Destination $Dir -Recurse -Force

$ExePath = Join-Path $Dir $ExeName
if (-not (Test-Path $ExePath)) {
    Write-Host ("复制失败：" + $ExePath + " 不存在") -ForegroundColor Red
    exit 3
}

# 快捷方式
$wsh = New-Object -ComObject WScript.Shell
function New-Lnk([string]$path) {
    $parent = Split-Path -Parent $path
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $lnk = $wsh.CreateShortcut($path)
    $lnk.TargetPath = $ExePath
    $lnk.WorkingDirectory = $Dir
    $lnk.IconLocation = $ExePath + ",0"
    $lnk.Description = $AppName
    $lnk.Save()
}
if (-not $NoShortcut) {
    $startDir = [Environment]::GetFolderPath("Programs")
    $lnkPath = Join-Path $startDir ($AppName + ".lnk")
    New-Lnk $lnkPath
    Say ("开始菜单：" + $lnkPath)
}
if ($Desktop) {
    $deskPath = Join-Path ([Environment]::GetFolderPath("Desktop")) ($AppName + ".lnk")
    New-Lnk $deskPath
    Say ("桌面：" + $deskPath)
}

# 版本号：先看命令行传的，再看打包时写好的 version.txt，
# 最后才去翻 version.py（那是源码版才有的文件，PyInstaller 打包后没有）。
if (-not $Version) {
    $txt = Join-Path $Here "version.txt"
    if (Test-Path $txt) {
        $Version = (Get-Content $txt -TotalCount 1).Trim()
    }
}
if (-not $Version) {
    $verFile = Join-Path $Here "version.py"
    if (Test-Path $verFile) {
        $m = Select-String -Path $verFile -Pattern '^\s*VERSION\s*=\s*"([0-9.]+)"' | Select-Object -First 1
        if ($m) { $Version = $m.Matches[0].Groups[1].Value }
    }
}
if (-not $Version) { $Version = "0.0.0" }

# 卸载脚本 + 控制面板里的卸载项
$uninst = Join-Path $Dir "卸载.bat"
# 脚本自己就住在要删的目录里，所以先 cd 到别处再删，
# 否则 Remove-Item 会因为「目录正在被使用」失败（cmd 的当前目录锁着它）。
$lnkForUninst = Join-Path ([Environment]::GetFolderPath("Programs")) ($AppName + ".lnk")
# reg.exe 要的完整路径是 HKCU\Software\Microsoft\...，用 [char]92 拼反斜杠省得转义出错
$regSubKey = [string]::Join([char]92, @("Software", "Microsoft", "Windows", "CurrentVersion", "Uninstall", $AppId))
$regFullPath = 'HKCU' + [char]92 + $regSubKey
# .bat 必须是 CRLF 换行，纯 LF 会让 cmd 把每行开头的几个字符吃掉
$crlf = [string]([char]13) + [string]([char]10)
# 删自己住的目录不能同步做：cmd 的当前目录锁着它。所以先登记卸载项、删快捷方式，
# 再用 start 起一个后台 PowerShell 去删目录，bat 立刻退出，锁自然就没了。
# 不用 cd %TEMP% 那套：%TEMP% 在某些环境下是空的，cd 会直接报「找不到指定的路径」。
$uninstLines = @(
  '@echo off',
  'chcp 65001 >nul',
  'echo 正在卸载编码悬浮窗...',
  'taskkill /IM BinFloater.exe /F >nul 2>nul',
  'reg delete "__REG__" /f >nul 2>nul',
  'if exist "__LNK__" del /f /q "__LNK__"',
  'start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 600; Remove-Item -LiteralPath ''__DIR__'' -Recurse -Force -ErrorAction SilentlyContinue"',
  'echo 已卸载。'
)
$uninstBody = [string]::Join($crlf, $uninstLines).Replace('__DIR__', $Dir).Replace('__LNK__', $lnkForUninst).Replace('__REG__', $regFullPath)
# 写 UTF-8 无 BOM：文件里 chcp 65001 之后中文就正常显示了，GBK 反而会乱码
[System.IO.File]::WriteAllText($uninst, $uninstBody + $crlf, (New-Object System.Text.UTF8Encoding $false))

$key = "HKCU:" + [char]92 + $regSubKey
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name "DisplayName"     -Value $AppName
Set-ItemProperty -Path $key -Name "DisplayVersion"  -Value $Version
Set-ItemProperty -Path $key -Name "DisplayIcon"     -Value $ExePath
Set-ItemProperty -Path $key -Name "InstallLocation" -Value $Dir
Set-ItemProperty -Path $key -Name "Publisher"       -Value "BlestHeart"
Set-ItemProperty -Path $key -Name "NoModify"        -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name "NoRepair"        -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name "UninstallString" -Value ('"' + $uninst + '"')

Say ""
Say "装好了。"
Say ("  程序：" + $ExePath)
Say ("  版本：" + $Version)
Say ("  卸载：" + $uninst + "  或控制面板的「应用」里删")
exit 0
