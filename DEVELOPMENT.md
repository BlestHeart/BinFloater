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

## 源码分块

| 文件 | 职责 |
| --- | --- |
| `main.py` | 入口：单实例互斥体、命令行直转 |
| `gui.py` | 悬浮窗界面（tkinter）、配置读写 |
| `core.py` | 编解码逻辑，纯函数、无副作用 |
| `clipwin.py` | Win32 剪切板读写与变化监听（ctypes） |
| `keys.py` | 模拟键盘：选中 / 复制 / 粘贴（SendInput） |
| `trigger.py` | 全局键盘钩子（WH_KEYBOARD_LL） |
| `hotkey.py` | 快捷键的解析 / 存储 / 显示 |
| `tray.py` | 系统托盘 + 气泡通知（ctypes 自绘） |

只有 `gui.py` 碰 tkinter，其余模块都能在纯命令行下单独跑。

## 测试

```bash
python tests/test_all.py       # 75 项：编解码 + 触发逻辑 + 快捷键解析 + 源码约定
python tests/test_config.py    # 18 项：配置原子写盘、坏文件备份、错误日志
python tests/smoke_gui.py      # 51 项：真建窗口，检查控件、钩子、托盘状态、启动回显
python tests/e2e_convert.py    # 17 项：真 Win32 窗口，真按键、真粘贴
python tests/e2e_notepad.py    #  9 项：开真记事本，验证剪切板还原
python tests/e2e_roundtrip.py  # 18 项：文字 → 二进制 → 文字 往返（含超长会换行的）
```

`e2e_*.py` 会真的动键盘和剪切板，跑之前先把正在编辑的东西存一下。

`smoke_gui.py` 会把 `APPDATA` 指到临时目录再建窗口，否则测试会把
`tray_hint_shown`、`space_count` 之类的状态写进你真实的 `config.json`，
导致「第一次收进托盘时弹提示」这种一次性的行为永远看不到。

## 配置怎么存的

`%APPDATA%\BinFloater\config.json`，纯 JSON，键很少（`trigger_mode`、
`space_count`、`hotkey`、`dir_mode`、`x`、`y`、`alpha`、`topmost`、
`compact`、`auto_copy`、`tray_hint_shown`）。改动即时落盘，不等退出。

三个地方是特意这么写的：

1. **原子写**：先写 `config.json.tmp`，`flush` + `fsync`，再 `os.replace`
   覆盖。直接以 `"w"` 打开原文件会先把它截断，万一进程在写入中途被结束
   （用户杀进程、崩溃、安全软件拦写盘），留下的就是空文件，下次启动所有
   设置回到默认值——用户看到的现象就是「怎么没记住」。
2. **坏文件先备份**：解析失败时把原文件改名成 `config.json.bad` 再退回默认值，
   不直接覆盖掉，否则用户的改动和问题现场一起没了。
3. **出错写日志**：`%APPDATA%\BinFloater\error.log`。以前 `except: pass`
   把所有异常吞掉，线上出问题只能靠猜。

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
