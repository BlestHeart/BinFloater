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
from typing import Tuple

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


def tap_many(vk: int, mods=(), count: int = 1, chunk: int = 400) -> bool:
    """按住 mods 连按 vk 共 count 次，一次 SendInput 发一整批。

    逐个 tap 的话，往左扩 350 个字符就是 350 次 SendInput 系统调用，
    慢（几百毫秒）而且中途被别的程序抢走输入就会丢。分批发送快得多，
    实测 350 组 Shift+Right 一次发完能准确落在目标位置。
    """
    if count <= 0:
        return True
    mods = list(mods)
    ok = True
    left = count
    while left > 0:
        n = min(left, chunk)
        seq = []
        for _ in range(n):
            seq.extend(_key(m, False) for m in mods)
            seq.append(_key(vk, False))
            seq.append(_key(vk, True))
            seq.extend(_key(m, True) for m in reversed(mods))
        ok = _send(seq) and ok
        left -= n
    return ok



def foreground_window() -> int:
    """当前前台窗口句柄。用来跳过「自己在前台」的情况。"""
    if not IS_WINDOWS:
        return 0
    try:
        return int(_user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def focus_window_ex(hwnd: int) -> Tuple[bool, str]:
    """把焦点还给指定窗口，返回 (是否成功, 失败原因)。

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

    失败原因是给界面显示用的：只说「切不回去」用户不知道该干什么，
    说清是哪一步断的才有用。
    """
    if not IS_WINDOWS:
        return False, "只有 Windows 支持"
    if not hwnd:
        return False, "还没记住刚才在哪个窗口"
    try:
        if not _user32.IsWindow(hwnd):
            return False, "刚才那个窗口已经关掉了"
        if _user32.GetForegroundWindow() == hwnd:
            return True, ""

        # 最小化的窗口要先还原，否则切过去也是个缩在任务栏的状态
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.05)

        if _user32.SetForegroundWindow(hwnd) and _user32.GetForegroundWindow() == hwnd:
            return True, ""

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

        if _user32.GetForegroundWindow() == hwnd:
            return True, ""
        return False, "系统不允许切回去（前台窗口被别的程序锁住了）"
    except Exception as exc:
        return False, "切窗口时出错：%r" % (exc,)


def focus_window(hwnd: int) -> bool:
    """把焦点还给指定窗口。只要成功与否时用这个，要看失败原因用 focus_window_ex。"""
    return focus_window_ex(hwnd)[0]


def collapse_selection(had_selection: bool = True) -> None:
    """把选区收起来（光标回到原处），不留选中高亮。

    转换失败或没东西可转时用。不能用 Esc：有些程序里 Esc 会关掉整个输入框。

    为什么要有 had_selection 参数：Windows 编辑控件里按一下不带 Shift 的右方向键，
    「有选区」时是把选区收起到右端（正好是原光标位置，正确），「没有选区」时
    却是把光标右移一格。grab_line 之后如果没有选区（光标就在行首/文档开头），
    再按右方向键就会把用户的光标推后一格 —— 什么都不做才是对的。
    """
    if had_selection:
        tap(VK_RIGHT)


def grab_selection(timeout: float = COPY_TIMEOUT):
    """如果当前有选中内容，复制出来返回 (文字, 原来的剪切板)；没选中返回 ("", old)。

    和 grab_line 的区别：这个不碰光标。用户自己选好的一段文字，Ctrl+C
    复制出来就是那段。多行转换靠它——选中三行，整体当一段文字处理，
    而不是只取光标所在那一行。

    判「有没有选中」不靠 Selection 检测（各家实现不一样），靠剪切板序列号：
    Ctrl+C 之后序列号没动就说明没选中任何东西（复制不出内容）。
    """
    old = clipwin.read_text()
    seq0 = clipwin.sequence()
    tap(VK_C, (VK_CONTROL,))
    got = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if clipwin.sequence() != seq0:
            got = clipwin.read_text()
            break
        time.sleep(0.012)
    return got, old


def convert_selection(convert, restore_clip: bool = True):
    """把用户选中的一段文字转换后原地粘回。没有选中就返回 empty。

    为什么不复用 convert_line：那条路会发 Ctrl+Shift+Home 重选，把用户
    自己划的选区毁掉。这里只在「确实有选区」时才动手，没选区直接退出，
    光标和文档一个字都不动。
    """
    if not IS_WINDOWS:
        return ("unsupported", "", "", "只有 Windows 支持全局转换")

    text, old = grab_selection()
    if not text.strip():
        return ("empty", "", "", "没有选中文字")

    src = text.rstrip()
    try:
        out = convert(src)
    except Exception as exc:
        return ("same", src, "", f"转换出错：{exc}")
    out = (out or "").strip()
    if not out or out == src:
        return ("same", src, "", "转换前后没有变化")
    if not clipwin.write_text(out):
        return ("clipfail", src, "", "写剪切板失败，可能有程序正占用")
    tap(VK_V, (VK_CONTROL,))
    time.sleep(PASTE_SETTLE)
    if restore_clip and old:
        clipwin.write_text(old)
    return ("ok", src, out, "")


def grab_line(timeout: float = COPY_TIMEOUT, attempts: int = 3):
    """选中「光标所在逻辑行、行首到光标」，Ctrl+C 复制，返回 (文字, 原来的剪切板)。

    为什么不用 Shift+Home：它只选到**视觉行首**。文字转成二进制后长度膨胀约
    9 倍（UTF-8 每字节 8 位、再加分隔空格），随便一句中文都会自动换行，
    于是 Shift+Home 只抓到末尾那一小截，反解出来必然是乱码 —— 「转过去再转
    回来」这条路会彻底失效。

    改用 Ctrl+Shift+Home 选到「文档开头 → 光标」，复制后按换行切出最后一行
    拿到真正的逻辑行；再把选区从远端收回 len(行首前缀) 个字符，正好剩下
    「行首 → 光标」。收回用 Shift+Right：Ctrl+Shift+Home 的锚点在光标、活动端
    在文档开头，所以 Shift+Right 是把活动端往光标方向收。

    光标恰好在文档最开头时，Ctrl+Shift+Home 选不中任何东西、Ctrl+C 复制不到，
    这是**正常结果**（这一行就是空的），不是失败。早先把它当失败去重试，
    重试前按了一下右方向键把光标推后一格，第二次就抓到了第一个字符，
    于是「空行转换」会把第一个字吃掉、文档变成乱码。现在空结果直接返回。

    重试的原因：选区是要靠目标程序自己处理的，选中的文字越多越慢。程序正忙
    的时候，Ctrl+C 可能在选区还没建立好就发出去了，复制到的是空的（或上一次
    的残留）。实测偶发率约五分之一，不重试就会静默返回空行、让转换失败。
    """
    old = clipwin.read_text()
    seq0 = clipwin.sequence()

    got = ""
    for attempt in range(max(1, attempts)):
        # 重试时只重新发一次 Ctrl+Shift+Home，**不要**先按右方向键。
        # 按右方向键会把光标推后一格：如果光标本来就在文档开头，第一次复制
        # 拿到空是正常的（这一行确实没内容），被当成失败去重试，第二次就抓到了
        # 第一个字符，文档变成 "01101000ello world"。Ctrl+Shift+Home 是幂等的，
        # 重复发不会移动光标，用它重试才安全。
        tap(VK_HOME, (VK_CONTROL, VK_SHIFT))
        # 选中一大段文字比按一下键慢得多，给它一点时间落定再复制
        time.sleep(STEP_DELAY * 2)
        tap(VK_C, (VK_CONTROL,))

        deadline = time.time() + timeout
        while time.time() < deadline:
            if clipwin.sequence() != seq0:
                got = clipwin.read_text()
                break
            time.sleep(0.012)
        if got:
            break
        seq0 = clipwin.sequence()

    if not got:
        # 复制不到东西有两种可能：光标就在文档最开头（真的没内容），
        # 或者目标程序没响应。分不清，但两种情况下都不该再动光标——
        # 按一下右方向键会把用户的光标推后一格。
        return "", old, False

    # 「文档开头 → 光标」里最后一个换行之后就是光标所在的逻辑行
    cut = got.rfind("\n") + 1
    line = got[cut:]

    # 把选区从文档开头收到行首。
    #
    # 这里不能直接按 cut 次：编辑控件里一个换行是**两个**字符（\r\n），
    # 但光标跨过换行只算**一步**。按字符数发按键会多走几步，把行首后面
    # 的字符也选进去 —— 光标在第二行行首时就会多选一个 'x'，粘贴时把
    # 它覆盖掉。所以要按「光标步数」算：字符数减去 \r\n 的个数。
    moves = cut - got[:cut].count("\r\n")
    if moves > 0:
        tap_many(VK_RIGHT, (VK_SHIFT,), moves)
        time.sleep(STEP_DELAY)

    # 收回之后还剩下选中的只有「行首 → 光标」这一段。这一段是空的（光标就在
    # 行首）就意味着此刻**没有选区**，后面 collapse_selection 不能再按右方向键，
    # 否则会把光标推后一格。
    return line, old, bool(line)


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

    text, old, had_sel = grab_line()
    if not text.strip():
        collapse_selection(had_sel)
        return ("empty", "", "", "光标这一行没有文字")

    # 行尾的空格/换行不该被编码进去，用户连按两次空格触发时尤其明显
    src = text.rstrip()
    try:
        out = convert(src)
    except Exception as exc:
        collapse_selection(had_sel)
        return ("same", src, "", f"转换出错：{exc}")
    out = (out or "").strip()

    if not out or out == src:
        collapse_selection(had_sel)
        return ("same", src, "", "转换前后没有变化")

    if not clipwin.write_text(out):
        collapse_selection(had_sel)
        return ("clipfail", src, "", "写剪切板失败，可能有程序正占用")

    tap(VK_V, (VK_CONTROL,))
    time.sleep(PASTE_SETTLE)
    if restore_clip and old:
        # 尽量别弄丢用户原来的剪切板内容。这一步失败也无所谓，静默跳过。
        clipwin.write_text(old)
    return ("ok", src, out, "")
