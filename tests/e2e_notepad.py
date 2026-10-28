# -*- coding: utf-8 -*-
"""端到端测试：开一个记事本，模拟打字，跑真实的 convert_line，检查文字是否被换掉。

这个测试会真的动键盘和剪切板，所以跑之前先把你手头正在编辑的东西存一下。
"""
import ctypes
import os
import subprocess
import sys
import time
import tkinter as tk

# 玩游戏/开会时别让测试抢前台焦点：设 BINF_NO_FOCUS=1 直接跳过。
# 这些测试要 SendInput + SetForegroundWindow，会把焦点从你正在用的窗口抢走。
if os.environ.get("BINF_NO_FOCUS"):
    print("BINF_NO_FOCUS=1：跳过（这个测试会抢前台焦点）")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clipwin
import core
import keys

if clipwin.IS_WINDOWS:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.c_void_p
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.restype = ctypes.c_bool
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsWindow.restype = ctypes.c_bool

ok, bad = [], []


def check(name, cond, detail=""):
    (ok if cond else bad).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))


class _GTI(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", ctypes.c_long * 4),
    ]


user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
user32.GetGUIThreadInfo.argtypes = [ctypes.c_ulong, ctypes.POINTER(_GTI)]
user32.GetGUIThreadInfo.restype = ctypes.c_bool
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
user32.PostMessageW.restype = ctypes.c_bool
WM_CHAR = 0x0102


def edit_control(hwnd):
    """拿到记事本窗口里真正接收文字的 EDIT 子控件。

    直接往顶层窗口发消息没用——文字是进子控件的。用 GetGUIThreadInfo 问系统
    「这个窗口线程现在的焦点在谁身上」，比 EnumChildWindows 猜类名可靠。
    """
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    gi = _GTI()
    gi.cbSize = ctypes.sizeof(_GTI)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gi)):
        return None
    return gi.hwndFocus


def type_text(s, hwnd=None):
    """往编辑控件里塞文字。

    这里刻意不用 SendInput 敲字母键：这台机器的输入法键盘布局是 0x0804（中文），
    字母键会先进输入法的组合状态，被输入法吃掉，控件收不到字符——实测敲 'hi'
    读回来是空字符串，排查了很久才发现是 IME 而不是 SendInput 本身的问题。
    PostMessage WM_CHAR 直接投递到控件，绕开输入法，测试想验的是 convert_line
    的键序列，不是打字功能。

    产品本身不受影响：convert_line 只用 Shift+Home / Ctrl+C / Ctrl+V，
    这些导航键和修饰键组合不经过 IME。
    """
    target = edit_control(hwnd) if hwnd else None
    if not target:
        raise RuntimeError("找不到记事本的编辑控件")
    for ch in s:
        user32.PostMessageW(target, WM_CHAR, ord(ch), 0)
        time.sleep(0.012)


def read_editor():
    """Ctrl+A + Ctrl+C 读回整个编辑框内容。"""
    keys.tap(0x41, (keys.VK_CONTROL,))  # VK_A
    time.sleep(0.06)
    seq0 = clipwin.sequence()
    keys.tap(keys.VK_C, (keys.VK_CONTROL,))
    for _ in range(40):
        if clipwin.sequence() != seq0:
            return clipwin.read_text()
        time.sleep(0.02)
    return ""


def prepare_editor(hwnd, text):
    """抢到前台、敲入 text，并且**读回来确认真的敲进去了**。

    记事本在 Win11 上是 UWP 外壳 + 独立进程，进程起来不等于编辑控件就绪：
    窗口已经能找到、也能切前台，但这时敲的字符会被丢掉。所以不能「敲完就
    假设成功」，必须读回来核对，不成就重试。

    核对用的 read_editor 是 Ctrl+A + Ctrl+C，退出时整段文字处于全选状态。
    必须把光标收到行尾清掉选区，否则 convert_line 里的 Shift+Home 会先把
    选区取消掉（变成什么都没选），Ctrl+C 复制出来就是空的——实测就是这样
    拿到 status='empty' 的。
    """
    for attempt in range(6):
        if not grab_focus(hwnd):
            time.sleep(0.2)
            continue
        time.sleep(0.12)
        type_text(text, hwnd)
        time.sleep(0.15)
        if read_editor() == text:
            # 取消全选，把光标放到行尾——这才是用户真正打字时的状态
            keys.tap(keys.VK_END)
            time.sleep(0.1)
            return True
        # 清干净再来
        keys.tap(0x41, (keys.VK_CONTROL,))
        time.sleep(0.05)
        keys.tap(keys.VK_DELETE)
        time.sleep(0.12)
    return False


def grab_focus(hwnd):
    """把前台切给记事本，并且确认真的切过去了。

    不能用裸 SetForegroundWindow：测试进程从后台 shell 启动，没有前台资格，
    系统会静默忽略这次切换（返回 False 也不报错），于是后面所有 SendInput
    都打到别的窗口上，测试全线报 empty。必须用 keys.focus_window，
    它会走 AttachThreadInput 绕开前台锁定。
    """
    for _ in range(6):
        if keys.focus_window(hwnd) and user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.12)
    return False


user32.GetForegroundWindow.restype = ctypes.c_void_p

print("=== 准备：打开记事本 ===")
proc = subprocess.Popen(["notepad.exe"])
time.sleep(1.4)
hwnd = user32.FindWindowW("Notepad", None)
if not hwnd:
    # Win11 的记事本类名变了，按标题找
    hwnd = user32.FindWindowW(None, "无标题 - 记事本") or user32.FindWindowW(None, "Untitled - Notepad")
print("   notepad hwnd =", hwnd)
if not hwnd:
    print("  FAIL  找不到记事本窗口，跳过")
    proc.kill()
    sys.exit(2)

if not prepare_editor(hwnd, "hi"):
    print("  FAIL  抢不到前台或敲不进字，跳过（这个环境不允许切前台）")
    proc.kill()
    sys.exit(2)
time.sleep(0.15)

print("=== 场景 1：文字 → 二进制（自动判断） ===")
# 先把剪切板设成一个已知值，验证转换后会还原
clipwin.write_text("SENTINEL-BEFORE")
time.sleep(0.1)


def convert_text2bin(t):
    return core.text_to_binary(t, sep=" ")


status, src, out, msg = keys.convert_line(convert_text2bin)
print("   status=%r src=%r out=%r msg=%r" % (status, src, out, msg))
check("状态是 ok", status == "ok", status)
check("抓到的原文是 hi", src == "hi", repr(src))
check("结果是二进制", out == "01101000 01101001", repr(out))

# 剪切板还原要在 read_editor 之前查——那个函数自己会写剪切板，
# 先查再复制，否则测的其实是测试脚本自己的行为。
print("=== 场景 2：剪切板已还原 ===")
now_clip = clipwin.read_text()
check("剪切板还原成原值", now_clip == "SENTINEL-BEFORE", repr(now_clip))

grab_focus(hwnd)
time.sleep(0.3)
got = read_editor()
print("   编辑框现在的内容 = %r" % got)
check("编辑框已被替换成二进制", got.strip() == "01101000 01101001", repr(got))

print("=== 场景 3：二进制 → 文字 ===")
grab_focus(hwnd)
time.sleep(0.25)
keys.tap(0x41, (keys.VK_CONTROL,))  # 全选
time.sleep(0.05)
keys.tap(keys.VK_END)              # 光标移到行尾，取消选择
time.sleep(0.15)


def convert_bin2text(t):
    r, _m = core.binary_to_text(t)
    return r or ""


status2, src2, out2, msg2 = keys.convert_line(convert_bin2text)
print("   status=%r src=%r out=%r msg=%r" % (status2, src2, out2, msg2))
check("二进制→文字状态 ok", status2 == "ok", status2)
check("解码回 hi", out2 == "hi", repr(out2))

grab_focus(hwnd)
time.sleep(0.3)
got2 = read_editor()
print("   编辑框现在的内容 = %r" % got2)
check("编辑框变回 hi", got2.strip() == "hi", repr(got2))

print("=== 场景 4：空行不该乱动 ===")
grab_focus(hwnd)
time.sleep(0.25)
keys.tap(0x41, (keys.VK_CONTROL,))
time.sleep(0.05)
keys.tap(keys.VK_DELETE)  # 清空
time.sleep(0.1)
status3, src3, out3, _m3 = keys.convert_line(convert_text2bin)
print("   status=%r" % (status3,))
check("空行返回 empty", status3 == "empty", status3)

print("=== 收尾 ===")
grab_focus(hwnd)
time.sleep(0.2)
keys.tap(0x41, (keys.VK_CONTROL,))
time.sleep(0.05)
keys.tap(keys.VK_DELETE)
time.sleep(0.1)
try:
    proc.kill()
except Exception:
    pass

print()
print("通过 %d 项，失败 %d 项" % (len(ok), len(bad)))
sys.exit(0 if not bad else 1)
