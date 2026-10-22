# -*- coding: utf-8 -*-
"""模拟按键 + 「选中当前行并原地替换」的完整流程（纯 ctypes，打包友好）。

这一层只负责键盘和剪切板的事，具体转换规则由调用方传进来的函数决定，
所以同一个流程既能做「文字 → 二进制」，也能做「二进制 → 文字」。

为什么不读窗口文字、而是 Shift+Home + Ctrl+C：
  Windows 没有通用的「拿到别的程序输入框里文字」的接口。UI Automation 能读，
  但各家程序实现差异极大（微信、浏览器、Qt 程序各有各的坑），而且读到的
  往往只是控件值、拿不到光标位置。走选中+复制虽然土，但凡是能编辑的地方
  都支持，行为和用户手按 Ctrl+C 完全一致。
"""
import ctypes
import os
import time
from ctypes import wintypes

import clipwin

IS_WINDOWS = os.name == "nt"

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_HOME = 0x24
VK_END = 0x23
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_UP = 0x26
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_INSERT = 0x2D
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_A = 0x41
VK_C = 0x43
VK_V = 0x56

# 扩展键：这些小键盘外的导航键在发 SendInput 时必须带 KEYEVENTF_EXTENDEDKEY。
# 不带的话系统会当成小键盘上那个同名的键，而小键盘键不参与 Shift 选区逻辑——
# 现象是 Shift+Home 只把光标移到行首、什么都不选中，整行转换静默失败。
# 这个坑在 Shift+a 上测不出来（字母键不是扩展键），只有导航键才会踩。
EXTENDED_VKS = frozenset((
    VK_HOME, VK_END, VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN,
    VK_DELETE, VK_INSERT, VK_PRIOR, VK_NEXT,
))
KEYEVENTF_EXTENDEDKEY = 0x0001

# 每一步之间的间隔。太短的话有些程序还没处理完上一步，粘贴会落到错误位置。
STEP_DELAY = 0.025
# 发完 Ctrl+V 后等这么久再还原剪切板；太早还原会把旧内容粘进去。
PASTE_SETTLE = 0.18
# Ctrl+C 之后等剪切板变化的超时。程序响应慢时多等一会，别急着判失败。
COPY_TIMEOUT = 0.5

# x64 上 ULONG_PTR 是 8 字节，写死 4 字节会让整个 INPUT 结构体大小算错，
# SendInput 会直接失败（返回 0），而且不报错，很难查。
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _key(vk: int, up: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp.u.ki = KEYBDINPUT(
        wVk=vk,
        wScan=0,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    return inp


def _send(inputs) -> bool:
    if not IS_WINDOWS or not inputs:
        return False
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = _user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    return int(sent) == n


def tap(vk: int, mods=()) -> bool:
    """按一下 vk，期间按住 mods 里的修饰键。例如 tap(VK_C, (VK_CONTROL,)) 就是 Ctrl+C。"""
    seq = [_key(m, False) for m in mods]
    seq.append(_key(vk, False))
    seq.append(_key(vk, True))
    seq.extend(_key(m, True) for m in reversed(list(mods)))
    return _send(seq)


def foreground_window() -> int:
    """当前前台窗口句柄。用来跳过「自己在前台」的情况。"""
    if not IS_WINDOWS:
        return 0
    try:
        return int(_user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def focus_window(hwnd: int) -> bool:
    """把焦点还给指定窗口。

    点面板上的「转换这一行」时，前台是悬浮窗自己，直接发 Shift+Home 会选到
    悬浮窗里的文字。所以先切回用户刚才用的那个窗口。

    为什么不止一次 SetForegroundWindow：
      Windows 有「前台锁定」——只有当前拥有前台的进程才被允许随意切换前台。
      悬浮窗进程平时不在前台，光调 SetForegroundWindow 会被系统静默忽略
      （返回 False，且不弹任何提示）。所以按成功率从高到低依次尝试：
        1. 本来就在前台：直接成功
        2. SetForegroundWindow：最干净，能成就不折腾
        3. AttachThreadInput 到当前前台线程后再调：把自己的输入队列挂到前台
           线程上，绕开前台锁定。这是老牌托盘程序的标准做法
        4. 最小化时先还原，再 BringWindowToTop
      全部失败才返回 False，让调用方提示用户改用快捷键。
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        if not _user32.IsWindow(hwnd):
            return False
        if _user32.GetForegroundWindow() == hwnd:
            return True

        # 最小化的窗口要先还原，否则切过去也是个缩在任务栏的状态
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.05)

        if _user32.SetForegroundWindow(hwnd) and _user32.GetForegroundWindow() == hwnd:
            return True

        # 前台锁定绕行：挂到当前前台窗口的线程上，借它的「前台资格」
        fg = _user32.GetForegroundWindow()
        fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_tid = _kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(_user32.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _user32.AttachThreadInput(cur_tid, fg_tid, False)

        return _user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def collapse_selection() -> None:
    """把选区收起来（光标回到原处），不留选中高亮。

    转换失败或没东西可转时用。不能用 Esc：有些程序里 Esc 会关掉整个输入框。
    """
    tap(VK_RIGHT)


def grab_line(timeout: float = COPY_TIMEOUT):
    """Shift+Home 选中行首到光标，Ctrl+C 复制，返回 (文字, 原来的剪切板)。"""
    old = clipwin.read_text()
    seq0 = clipwin.sequence()

    tap(VK_HOME, (VK_SHIFT,))
    time.sleep(STEP_DELAY)
    tap(VK_C, (VK_CONTROL,))

    deadline = time.time() + timeout
    while time.time() < deadline:
        if clipwin.sequence() != seq0:
            got = clipwin.read_text()
            if got:
                return got, old
        time.sleep(0.012)
    return "", old


def convert_line(convert, restore_clip: bool = True):
    """把「光标所在行、行首到光标」的文字用 convert 转换后原地粘回。

    返回 (状态码, 原文, 结果, 说明)，状态码：
      ok         转换成功并已粘回
      empty      光标这一行没东西
      same       转换前后一样（空行、纯符号、编码失败等）
      clipfail   写剪切板失败
      unsupported 非 Windows
    """
    if not IS_WINDOWS:
        return ("unsupported", "", "", "只有 Windows 支持全局转换")

    text, old = grab_line()
    if not text.strip():
        collapse_selection()
        return ("empty", "", "", "光标这一行没有文字")

    # 行尾的空格/换行不该被编码进去，用户连按两次空格触发时尤其明显
    src = text.rstrip()
    try:
        out = convert(src)
    except Exception as exc:
        collapse_selection()
        return ("same", src, "", f"转换出错：{exc}")
    out = (out or "").strip()

    if not out or out == src:
        collapse_selection()
        return ("same", src, "", "转换前后没有变化")

    if not clipwin.write_text(out):
        collapse_selection()
        return ("clipfail", src, "", "写剪切板失败，可能有程序正占用")

    tap(VK_V, (VK_CONTROL,))
    time.sleep(PASTE_SETTLE)
    if restore_clip and old:
        # 尽量别弄丢用户原来的剪切板内容。这一步失败也无所谓，静默跳过。
        clipwin.write_text(old)
    return ("ok", src, out, "")
