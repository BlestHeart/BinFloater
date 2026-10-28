# BinFloater 开发笔记

这份是开发向文档：怎么构建、怎么测、代码怎么分块、踩过哪些坑。
只想用这个工具的话看 [README.md](README.md)。

## 环境

- Windows 10 / 11
- Python 3.11（3.8+ 应该都能跑，但只在 3.11 上验过）
- 打包用 PyInstaller 6.x

## 构建

```bash
pip install pyinstaller pillow
python make_icon.py
python -m PyInstaller build_onedir.spec --noconfirm --clean
```

产物在 `dist/BinFloater/`，约 26 MB，整个文件夹拷走就能用。

> 用 **文件夹模式（onedir）**，不要用单文件模式（onefile）。
> onefile 每次启动都要把自身解包到 `%TEMP%\_MEIxxxxxx`，这一步会被
> 360安全卫士 / 火绒的文件实时防护拦截，弹出 `Could not create temporary directory!`。
> onedir 启动时完全不解包，实测启动耗时从 2.5 秒降到 0.5 秒。

### 出安装包

```bash
bash build_installer.sh              # 打包 + 生成安装包
bash build_installer.sh --no-build   # 只重新生成安装包，用现有 dist/BinFloater
```

需要 [Inno Setup 6](https://jrsoftware.org/isinfo.php)（`winget install JRSoftware.InnoSetup`）。
脚本从 `version.py` 读版本号，通过 `BINF_VERSION` 环境变量传给 `installer/BinFloater.iss`，
产物是 `dist/BinFloater-Setup-<版本>.exe`，约 9 MB（lzma2/max 压缩）。

版本号的唯一来源是 `version.py`，安装包里的 `AppVersion`、`--version` 输出、以后的
检查更新都读它，别在别处再写一份。

`installer/BinFloater.iss` 里几个不能动的点：

- `AppId` 是固定 GUID，Inno Setup 靠它认「同一个软件」，升级时才能直接覆盖而不
  变成装了两份。**永远不要改**。
- `DefaultDirName={code:GetDefaultDir}`：装的时候现算默认目录，挑第一个「是本机固定
  硬盘、且真的能写文件」的非 C 盘；一块都没有才退回 `{localappdata}`。
  只看盘符存在不够——有些数据盘根目录只给管理员写权限，所以 `CanWriteUnder` 是
  真建目录、真写一个探针文件再删掉。
- `DisableDirPage=no`：目录页永远显示。Inno 默认是 `auto`，在「已经是升级」的场景
  会把它藏起来，但恰恰是升级时用户最可能想把安装位置改掉。
- `[InstallDelete]` 先清 `_internal`：onedir 升级时旧版本会留下已经不存在的模块
  文件，不清的话可能导入到旧的 `.pyd/.pyc`。
- `AppName` 是显示名，**改它不会动安装目录**（`DefaultDirName` 里写死的是
  `BinFloater` 而不是 `{#AppName}`），所以 1.7.2 从「二进制悬浮窗」改名成
  「编码悬浮窗」时，老用户升级后配置和文件都还在原地。
  但开始菜单里会多出一个新名字的文件夹、旧的那份留在原处，看着像没卸干净 ——
  所以 `[Code]` 里加了个 `CleanOldShortcuts()`，在 `PrepareToInstall` 里
  把旧名字的开始菜单文件夹、桌面快捷方式和开机启动项删掉。
  `PrivilegesRequired=lowest` 装的是当前用户的开始菜单，所以路径用 `{userprograms}`。

`tests/install_check.py` 会真装一遍（静默）、启动、发 WM_CLOSE 优雅退出、再静默卸载，
核对默认位置确实不在 C 盘、配置确实写到了安装目录而不是 `%APPDATA%`。
它需要先 `bash build_installer.sh` 出安装包。

## 源码分块

| 文件 | 职责 |
| --- | --- |
| `main.py` | 入口：单实例互斥体、命令行直转、`--version` |
| `gui.py` | 悬浮窗界面（tkinter）、配置读写 |
| `core.py` | 编解码逻辑，纯函数、无副作用 |
| `clipwin.py` | Win32 剪切板读写与变化监听（ctypes） |
| `keys.py` | 模拟键盘：选中 / 复制 / 粘贴（SendInput） |
| `trigger.py` | 全局键盘钩子（WH_KEYBOARD_LL） |
| `hotkey.py` | 快捷键的解析 / 存储 / 显示 |
| `tray.py` | 系统托盘 + 气泡通知（ctypes 自绘） |
| `wakeup.py` | 命名事件，第二个实例把已有窗口叫回来 |
| `version.py` | 版本号唯一来源，安装器和 `--version` 都读它 |
| `update.py` | 检查更新 + 应用内下载安装包：网络、sha256 校验、原子落盘全在这里，不碰界面 |

只有 `gui.py` 碰 tkinter，其余模块都能在纯命令行下单独跑。

## 测试

```bash
python tests/test_all.py       # 151 项：编解码四种格式 + 格式识别 + 性能优化等价性 + 触发逻辑 + 快捷键解析 + 版本号 + 检查更新 + 应用内下载 + 源码约定
python tests/test_config.py    #  64 项：配置原子写盘、坏文件备份、类型校验、错误日志、配置目录选择、安装脚本
python tests/smoke_gui.py      # 198 项：真建窗口，控件、钩子、托盘、历史、滑块、录制回显、长文本保护、检查更新、应用内下载、自动检查开关可见性
python tests/e2e_convert.py    #  23 项：真 Win32 窗口，真按键、真粘贴、选区边界
python tests/e2e_notepad.py    #   9 项：开真记事本，验证剪切板还原
python tests/e2e_roundtrip.py  #  18 项：文字 → 二进制 → 文字 往返（含超长会换行的）
python tests/install_check.py  #  12 项：真装一遍、启动、优雅退出、卸载，核对装到非 C 盘
```

合计 463 项（含安装实测 12 项）。`e2e_*.py` 会真的动键盘和剪切板，
`install_check.py` 会真的往你的 D 盘装一份再卸掉，跑之前先把正在编辑的东西存一下。

### 不想让测试抢前台焦点

玩游戏、开会的时候跑测试很烦——`e2e_*` 会 `SendInput` 敲键盘抢前台，`smoke_gui` / `shot` 会弹置顶窗口。设一个环境变量就全跳过：

```bash
BINF_NO_FOCUS=1 python tests/test_all.py    # 这条本来就不碰焦点，照常跑
BINF_NO_FOCUS=1 python tests/smoke_gui.py   # 直接打印「已跳过」退出
```

`test_all.py` 和 `test_config.py` 全程不开窗口（GUI 只做静态检查），不受影响。

### 性能：为什么 core.py 里有几条「快速通道」

`detect_format` 每次剪切板一变就要跑，而且要对四种格式各判一次。原来的写法是「先全量扫一遍再说」，几万字的文本要几十毫秒。现在加了几条**结果完全等价**的提前退出：

| 位置 | 快速通道 | 触发条件 |
|---|---|---|
| `_prefix_ok` | 只看开头 400 字符 | 中英文一上来就有非该格式字符，直接否掉，省掉后面的全长正则 |
| `_clean_bits` | 已是纯 0/1 就直接返回 | 没有分隔符要清 |
| `_clean_bits` | 没有 `b`/`B` 就不跑 `(?i)0b` | 那条正则没法用前缀加速，白扫一遍很浪费 |
| `bytes_to_binary` | `group == 8` 时按字节查表直接 join | 一个字节正好 8 位，切点必然落在字节边界 |
| `bits_to_bytes` | `int(bits, 2).to_bytes()` 整串转 | 90 万位要切 11 万次，逐段 `int()` 太慢 |

实测（同一台机器）：

| 场景 | 优化前 | 优化后 |
|---|---|---|
| `detect_format` 1 千字 | 0.54 ms | 0.01 ms |
| `detect_format` 1 万字 | 5.55 ms | 0.01 ms |
| `detect_format` 5 万字 | 26.35 ms | 0.04 ms |
| `looks_like_binary` 5 万字 | 4.96 ms | 0.00 ms |
| `encode_as` 2 万字 | 14.13 ms | 5.12 ms |
| `binary_to_text` 90 万位 | 105 ms | 67 ms |

**这些优化只有在结果完全一致时才成立**，所以 `tests/test_all.py` 里的 `TestPerf` 用老实现逐条对照：`_clean_bits` 16 个边界串、`_prefix_ok` 的空白语义、`bytes_to_binary` 的四种 group、`bits_to_bytes` 的前导 0 与末尾补齐。改这些函数前先看那组测试。

`smoke_gui.py` 和 `test_config.py` 都会把 `BINF_CONFIG_DIR` / `APPDATA` 指到临时目录
再建窗口，否则测试会把 `tray_hint_shown`、`space_count` 之类的状态写进你真实的
`config.json`，导致「第一次收进托盘时弹提示」这种一次性的行为永远看不到。

`e2e_*` 里的 `convert_pumped` 有三处防抖，都是踩出来的，别随手删：

1. `editor.focus()` + 等 120ms：窗口刚建好时 `GetForegroundWindow` 已经返回它，
   但系统还没走完激活流程，这中间发的按键会丢。
2. `settle()`：`convert_line` 里 `Ctrl+V` 后只 sleep 一小会儿，那只保证按键
   发出去了，不保证目标程序处理完。要连续两次读到一样的内容才算稳。
3. `Target.focus()` 用 `GetForegroundWindow() == main and GetFocus() == edit`
   双重确认，且 `GetForegroundWindow.restype` 必须声明成 `HWND`（64 位句柄
   不声明会被截成 32 位 int，高地址句柄永远比不上）。

## 配置怎么存的

纯 JSON，键很少（`trigger_mode`、`space_count`、`hotkey`、`dir_mode`、`format`、
`separator`、`x`、`y`、`alpha`、`topmost`、`auto_copy`、`clip_paused`、
`collapsed`、`width`、`first_run_done`、`tray_hint_shown`、`update_check`、
`update_last_day`）。改动即时落盘，不等退出。

**放哪儿按这个顺序挑**（`gui.config_dir()`）：

1. 环境变量 `BINF_CONFIG_DIR` —— 显式指定，最高优先。测试靠它隔离。
2. 打包成 exe 且程序目录可写 → 放 exe 旁边，也就是安装目录。
   装到 D 盘，`config.json` 就在 `D:\Programs\BinFloater\`，不往 C 盘的
   `%APPDATA%` 里丢东西。
3. 从源码跑、或程序目录不可写（装在 Program Files 这种要提权的目录）→
   退回 `%APPDATA%\BinFloater`。**这是兜底，不是首选。**

判断「可写」不能用 `os.access`——它在 Windows 上只看只读属性，装在 Program Files
时照样返回 True。`_dir_writable` 是真去建目录、写一个 `.write-probe`、再删掉。

从 1.3 及更早版本升级上来时，`_migrate_legacy_config()` 会在「新位置没有配置、
老位置 `%APPDATA%\BinFloater\config.json` 有」时复制一份过来，老文件保留不动。

设置卡片底部有一行小字显示实际目录，点一下用 `os.startfile` 打开。
用户不需要知道 `%APPDATA%` 是什么，但想知道「设置存哪了」时必须能查到。

四个地方是特意这么写的：

1. **原子写**：先写 `config.json.tmp`，`flush` + `fsync`，再 `os.replace`
   覆盖。直接以 `"w"` 打开原文件会先把它截断，万一进程在写入中途被结束
   （用户杀进程、崩溃、安全软件拦写盘），留下的就是空文件，下次启动所有
   设置回到默认值——用户看到的现象就是「怎么没记住」。
2. **坏文件先备份**：解析失败时把原文件改名成 `config.json.bad` 再退回默认值，
   不直接覆盖掉，否则用户的改动和问题现场一起没了。
3. **出错写日志**：同目录的 `error.log`。以前 `except: pass`
   把所有异常吞掉，线上出问题只能靠猜。
4. **写失败要说出来**：`_save()` 里发现落盘失败会直接打到状态栏，
   不能只在日志里记一笔——用户以为设置存上了，下次打开又是旧值。

启动时状态栏会主动念一遍恢复出来的设置（`已恢复：空格连按 ×4`），
这样「到底记没记住」一眼就能看到，不用去翻配置文件。

## 几个关键设计

### 选中「当前行」不能用 Shift+Home

`Shift+Home` 只选到**视觉行首**。文字转成二进制后长度膨胀约 9 倍
（UTF-8 每字节 8 位、再加分隔空格），随便一句中文都会自动换行，
于是只抓到末尾那一小截，反解出来必然是乱码——「转过去再转回来」彻底失效。

正确做法：`Ctrl+Shift+Home` 选到文档开头，按换行切出最后一行，
再用 `Shift+Right` 把选区收回到行首。收回是**一次 SendInput 发一整批**
（`tap_many`），逐个发的话往左收 350 个字符就是 350 次系统调用。

### 导航键必须带 KEYEVENTF_EXTENDEDKEY

Home / End / 方向键 / Delete / Insert / PageUp / PageDown 是**扩展键**。
不带 `KEYEVENTF_EXTENDEDKEY` 时系统把它们当成小键盘上的同名键，
而小键盘键不参与 Shift 选区逻辑——现象是 `Shift+Home` 只移动光标、
什么都不选中，转换静默失败。

这个坑在 `Shift+A` 上测不出来（字母键不是扩展键），只有导航键才会踩。

### 钩子回调里不能干重活

Windows 有 `LowLevelHooksTimeout`（默认 300ms）。回调超时会被系统
**静默摘掉钩子且不报错**。所以钩子回调只记时间戳 / 判条件，
真正的转换通过 `queue.Queue` 丢给 Tk 主线程。

### 前台锁定

点面板上的「转换这一行」按钮时，前台是悬浮窗自己，得先把焦点还给用户刚才
用的窗口。Windows 有前台锁定：不在前台的进程调 `SetForegroundWindow`
会被静默忽略（返回 False，也不弹提示）。所以 `keys.focus_window` 会依次尝试
直接切换、用 `AttachThreadInput` 挂到当前前台线程借资格、还原最小化窗口
再提到最前；全都失败才提示改用快捷键。

### 托盘不用 pystray

自己用 ctypes 写 `Shell_NotifyIcon`，两个理由：打包时少一个 hiddenimport；
pystray 不支持气泡通知（`NIF_INFO`），而「收进托盘时告诉用户触发还能用」
正靠它。

### 检查更新 + 应用内下载

`update.py` 分两层：检查（`fetch` / `parse_release`）和下载（`download_asset`），
两层都只返回结果对象，不碰 tkinter。

检查侧三个硬规则：

- **一天只查一次。** 匿名调 `api.github.com` 限 60 次/小时（按 IP）。查过就把
  日期写进 `update_last_day`，同一天再启动直接跳过。
- **默认开，但必须能关。** 见下面「开关」一段。
- **后台线程只入队，不碰 Tk。** `_check_update` 把回调包成往 `self._updates`
  里 `put_nowait`，主线程 `_drain_updates` 每 200ms 取。进度和下载结果同样走
  各自队列（`_update_progress_queue` / `_update_dl_queue`）。
- **能在设置里关掉。** 开关在设置卡片的**标题行**（不是铺在下面）：卡片内容
  已比一屏高，铺底部要滚动才看得到，而想关它的人恰恰最需要第一时间找到。
  关掉后 `update_check=false`，`_maybe_auto_check_update` 直接 return，
  一次请求都不发；重启也从配置读回来，不会自己开回去。
- **draft / prerelease 不推给普通用户。** `/releases/latest` 本身已经排除，
  但代码里再挡一道，以后换接口不会踩。

私有仓库的匿名请求会得到 404（GitHub 不区分「不存在」和「没权限」），所以 404
单独归成 `NOT_FOUND`，界面上写「仓库还没公开」而不是「网络故障」——后者会让人
去查防火墙，方向全错。

下载侧四个必做的事：

- **先写 `.part`，验过才 `os.replace` 改成真名。** 半途断网不会留下一个看着像
  安装包、双击却报「文件损坏」的碎文件，也不会把已经下好的那份弄坏。
- **边下边算 sha256。** GitHub 从 2024 年起给每个 asset 返回 `digest` 字段
  （`sha256:...`），白送的完整性校验。对不上就删 `.part` 并报 `DL_BAD_HASH`。
- **卡大小上限。** `Content-Length` 声明超过 `MAX_DOWNLOAD_BYTES`（200MB）
  直接不下；下到一半超了也立刻停，防重定向到一个无限流把磁盘写满。
- **可取消。** `should_stop` 每读一块查一次，用户点取消立刻跳出来删 `.part`。

### 为什么「应用内下载」可以，但「自己替换自己」不行

这是两个不同的事，之前混在一起才得出了「只能跳浏览器」的结论：

- **自己替换自己不行**：程序跑着时 `BinFloater.exe` 和 `_internal` 里的 dll
  都被占用，Windows 不允许改写正在运行的映像。先退出再换得靠外部脚本蹲着等
  进程结束，而这种行为在装了 360 / 火绒的机器上大概率被拦，拦了还没提示。
- **下载安装包 + 拉起安装程序可以**：真正动手覆盖文件的是 Inno Setup，不是
  我们。我们要做的只是先把自己完整关掉（`_launch_installer` 里 `os.replace`
  之前就 `after(400, self._quit)`），把位置让出来。

`_launch_installer` 用 `DETACHED_PROCESS` 拉起安装程序：不脱离父进程的话，
我们退出时安装向导可能被一起带走，UAC 提权对话框都弹不出来。

安装包下载到 `config_dir()/updates/`，`BINF_DOWNLOAD_DIR` 环境变量可以覆盖
（测试靠它隔离）。

### 自动检查开关

`update_check` 是唯一总闸，三处必须同时成立才算「真关」：

1. **默认值**：`_as_bool(self.cfg.get("update_check", True), True)` —— 缺键时
   当开（新装用户默认能收到更新提醒）。用 `_as_bool` 而不是 `bool()`：字符串
   `"false"` 在 Python 里是真值，用户手改配置写成 `"false"` 反而会把开关打开。
2. **执行处**：`_maybe_auto_check_update` 开头就 `if not ...: return` —— 不是
   「少查一次」，而是整个函数不进。`_check_update` 只有手动点按钮才会调。
3. **开关自己写盘**：`_toggle_auto_update` 里 `self.cfg["update_check"] =
   self._update_auto` + `_save()`，重启从配置读回来，不会自己开回去。

实测（假 `check_async` 计数器 + 真实 GUI 起 5 秒）：

| 配置 | 启动后请求次数 |
|---|---|
| 无配置文件（默认） | 1 |
| `update_check: false` | **0** |
| `update_check: true` | 1 |
| `update_check: "false"`（手改成字符串） | **0** |
| `update_check: "不知道"`（垃圾值） | 1（回退默认） |

**UI 位置也是需求的一部分**。最初这个开关铺在设置卡片最下面，而卡片内容
（`body_req` 约 1025px）已超过一屏可视区（`canvas` 约 898px），要滚动才看
得到——“不想让程序联网”的人恰恰是最需要第一眼看到它的人。现在放在设置
卡片标题行，跟「恢复默认」并排。`smoke_gui.py` 里有一条断言比对
`winfo_rooty` 和 canvas 可视区，`test_all.py` 里有一条断言它出现在版本行
之前，两道防线。

另外，关掉的当下会把 `_update_result` 清掉并重刷版本行：避免刚好在跑的
那一轮查完之后还跳一个「发现新版本」出来骚扰人。

## 踩过的坑

- **Windows 控制台是 GBK**：Python 脚本里的中文 `print` 到管道里会乱码，
  排查时要么写 UTF-8 文件再读，要么别依赖控制台输出。
- **PyInstaller 重建前要先关掉旧进程**，否则 `PermissionError: [WinError 5]`。
- **`SendInput` 的 INPUT 结构体**在 x64 上 `ULONG_PTR` 是 8 字节，写死 4 字节
  会让结构体大小算错，`SendInput` 直接返回 0 且不报错。
- **测试里塞文字别用 SendInput 敲字母键**：测试机挂着中文输入法时，
  字母键会先进输入法的组合状态被吃掉，读回来是空的。用 `PostMessage WM_CHAR`。
  产品本身不受影响——`convert_line` 只用导航键和 `Ctrl+C/V`，不经过输入法。
- **窗口过程必须是类属性**：`wc.lpfnWndProc = WNDPROC(lambda ...)` 用临时对象时，
  对象被 GC 回收后窗口过程指针悬空，窗口一收消息就 access violation。
- **Inno Setup 的 Pascal Script 没有字符 for 循环**：`for drive := 'D' to 'Z'`
  报 `Type mismatch`，只能用整型循环 `for code := 68 to 90` + `Chr(code)`。
- **Inno 的 `continue` 也不稳**：早期写法用了 `if ... then continue;`，编译能过但
  在 `GetDefaultDir` 里行为不对，改成嵌套 `if` 更保险。
- **静默安装测试不能让程序被 `taskkill /F`**：程序只在正常退出时才写 `config.json`，
  强杀掉就没写出来，测试会误判成「配置没写到安装目录」。要 `PostMessage WM_CLOSE`。
- **Inno 的 `[Languages]` 引官方中文包**：`ChineseSimplified.isl` 在 issrc 仓库的
  `Files/Languages/`（不是 `Unofficial/`），本地要和 `.iss` 放同一目录，或者写全路径。
- **emoji 在 Tk + 微软雅黑下渲染成方块**：`⌖`、`⌄`、`🔒`、`⏸` 都踩过。
  换成 ASCII 或 BMP 区符号（`◉`、`▼`、`‖`）。
- **`urllib` 会自动读系统代理**，而本地回环测试不能走代理。测本地 HTTP
  服务时要先 `urlreq.install_opener(build_opener(ProxyHandler({})))` 并临时
  清掉 `http(s)_proxy` 环境变量，否则请求会被导到代理上去。
- **`PyInstaller` 静态分析扫不到延迟 import**：`wakeup` 和 `update` 都在函数体里
  才 import（或只被 `import update as update_mod` 这种别名形式引用），必须写进
  `build_onedir.spec` 的 `hiddenimports`，否则打包后运行到那一步才 ImportError。
  验证方法：`CArchiveReader` 先 `extract("PYZ.pyz")`，再 `ZlibArchiveReader` 读；
  直接拿 CArchiveReader 去当 ZlibArchiveReader 用会报
  `ArchiveReadError: PYZ magic pattern mismatch!`。
- **查 PYZ 里有没有某函数，`repr(module_object)` 是不行的**：要递归走
  `co_consts` 和 `co_names` 收集字符串。直接对模块对象做 `repr` 再看关键字
  全是 False，容易误判成「没打包进去」。
- **测试里等后台线程结果时别 `root.update()`**：那会把主循环跑起来，而
  `_drain_updates` 是靠 `after(200)` 自动跑的，它会先把队列里的结果吃掉并
  调好处理函数——测试自己就永远取不到了，跑到超时才死。只 poll 队列，拿到
  后自己调一次处理函数，行为才确定（`smoke_gui.py` 里写了注释）。
- **测试脚本顶层变量名别随便占**：`smoke_gui.py` 顶层 `ok` / `bad` 是
  通过/失败的列表，中间用 `bad = update.download_asset(...)` 会把它覆盖成
  一个结果对象，后面 `check()` 去 `.append` 就报
  `AttributeError: 'DownloadResult' object has no attribute 'append'`。
