# -*- coding: utf-8 -*-
"""端到端：在同一个输入框里「文字 → 二进制 → 文字」往返。

这是用户最常走的一条路：聊天框里打一句话，按一次快捷键变成二进制，
再按一次变回原话。自动判断模式（DIR_AUTO）靠 core.auto_detect 看内容
像不像 0/1 串来决定方向，所以两次按同一个键就该自己转回来。

跑法：python tests/e2e_roundtrip.py
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

# 玩游戏/开会时别让测试抢前台焦点：设 BINF_NO_FOCUS=1 直接跳过。
# 这些测试要 SendInput + SetForegroundWindow，会把焦点从你正在用的窗口抢走。
if os.environ.get("BINF_NO_FOCUS"):
    print("BINF_NO_FOCUS=1：跳过（这个测试会抢前台焦点）")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clipwin
import core
import keys

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_void_p
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetFocus.restype = wintypes.HWND
# 句柄是 64 位的，不声明 restype 会被截成 32 位 int，高地址句柄永远比不上
user32.GetForegroundWindow.restype = wintypes.HWND
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.restype = LRESULT
user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM
k32.GetModuleHandleW.restype = wintypes.HINSTANCE
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

WS_EX_CLIENTEDGE = 0x00000200
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
ES_LEFT = 0x0000
ES_MULTILINE = 0x0004
WM_SETTEXT = 0x000C
WM_GETTEXTLENGTH = 0x000E
EM_SETSEL = 0x00B1


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT)]


class Target:
    """一个真的 Win32 窗口 + 真的 EDIT 控件，当作聊天输入框。"""

    _registered = False
    _PROC = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))

    def __init__(self):
        hinst = k32.GetModuleHandleW(None)
        if not Target._registered:
            wc = WNDCLASSW()
            wc.lpfnWndProc = Target._PROC
            wc.hInstance = hinst
            wc.hbrBackground = 6
            wc.lpszClassName = "E2ERoundTripWnd"
            user32.RegisterClassW(ctypes.byref(wc))
            Target._registered = True
        self.main = user32.CreateWindowExW(
            0, "E2ERoundTripWnd", "e2e-roundtrip", 0x00CF0000,
            380, 280, 620, 240, None, None, hinst, None)
        self.edit = user32.CreateWindowExW(
            WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_LEFT | ES_MULTILINE | WS_BORDER,
            10, 10, 580, 170, self.main, None, hinst, None)
        user32.ShowWindow(self.main, 5)
        user32.UpdateWindow(self.main)
        time.sleep(0.3)

    def focus(self):
        """把焦点给 EDIT 控件，并确认窗口真的在前台。

        GetFocus 只反映「本线程内的焦点控件」，窗口在后台时它照样返回 EDIT，
        可 SendInput 是往**前台**窗口投的，窗口不在前台按键就全打歪了。
        所以要用 GetForegroundWindow 验证，并走 keys.focus_window 的
        前台锁定兜底（AttachThreadInput 那一套）。
        """
        for _ in range(6):
            try:
                keys.focus_window(self.main)
            except Exception:
                pass
            if user32.GetForegroundWindow() != self.main:
                time.sleep(0.12)
                continue
            user32.SetActiveWindow(self.main)
            user32.SetFocus(self.edit)
            time.sleep(0.15)
            if (user32.GetForegroundWindow() == self.main
                    and user32.GetFocus() == self.edit):
                return True
            time.sleep(0.1)
        return False

    def set_text(self, text):
        buf = ctypes.create_unicode_buffer(text)
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.addressof(buf))
        user32.SendMessageW(self.edit, EM_SETSEL, len(text), len(text))

    def get_text(self):
        n = user32.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(int(n) + 1)
        user32.GetWindowTextW(self.edit, buf, int(n) + 1)
        return buf.value

    def destroy(self):
        if self.main:
            user32.DestroyWindow(self.main)
            self.main = 0


def pump(ms=200):
    end = time.time() + ms / 1000.0
    msg = MSG()
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.006)


def settle(editor, timeout=1.2):
    """等编辑框内容稳定下来。

    convert_line 里发完 Ctrl+V 后会 sleep 一小会儿再返回，但那只保证「按键发出去了」，
    不保证目标程序已经把粘贴内容处理完。紧接着再跑一次转换，Shift+Home 可能赶在
    上一次粘贴之前发出，抓到的就是新旧混着的半截文字——这是本测试偶发失败的唯一原因
    （重跑三次全过，说明不是产品问题）。
    做法：盯住内容，连续两次采样一致才算稳。
    """
    end = time.time() + timeout
    prev = None
    while time.time() < end:
        pump(40)
        now = editor.get_text()
        if now == prev:
            return now
        prev = now
    return prev or ""


def convert_pumped(convert, restore_clip=True, timeout=8.0, editor=None):
    """在后台线程跑 convert_line，主线程持续泵消息。

    开跑前会重新确认一次前台：窗口刚建好时 GetForegroundWindow 已经是它，
    但 Windows 还要走完激活流程才真正把键盘输入接过来。这中间的空档里发
    Shift+Home，按键直接丢掉，grab_line 三次重试全落空、返回「光标这一行
    没有文字」——偶发失败就是这么来的（约六分之一概率）。
    """
    import threading
    box = {}

    if editor is not None:
        editor.focus()
        pump(120)

    def work():
        try:
            box["ret"] = keys.convert_line(convert, restore_clip=restore_clip)
        except BaseException as exc:  # noqa: BLE001
            box["exc"] = exc

    th = threading.Thread(target=work, daemon=True)
    th.start()
    end = time.time() + timeout
    while th.is_alive() and time.time() < end:
        pump(30)
    th.join(1.0)
    if "exc" in box:
        raise box["exc"]
    ret = box.get("ret", ("unsupported", "", "", "超时"))
    if editor is not None:
        settle(editor)
    return ret


PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        # Windows 控制台是 GBK，详情里带中文/特殊字符会直接抛 UnicodeEncodeError
        # 把测试本身炸掉，所以统一转成 ascii 安全的形式。
        safe = detail.encode("unicode_escape").decode("ascii") if detail else ""
        print("  FAIL  %s   %s" % (name, safe))


def auto_convert(text):
    """复刻 gui._do_convert_line 在 DIR_AUTO 模式下的行为。"""
    how = core.auto_detect(text)
    if how == "bin2text":
        out, _msg = core.binary_to_text(text)
        return out or ""
    if how == "text2bin":
        return core.text_to_binary(text, sep=" ")
    return ""


def main():
    print("=== 端到端：同一输入框里文字 -> 二进制 -> 文字 往返 ===")
    print()

    saved_clip = clipwin.read_text()
    t = Target()
    try:
        check("目标窗口拿到焦点", t.focus())

        # ---- 场景 1：中文短句往返 ----
        original = "你好"
        t.set_text(original)
        pump(150)
        st, src, out, msg = convert_pumped(auto_convert, editor=t)
        check("第 1 次（文字->二进制）状态 ok", st == "ok", "%s %s" % (st, msg))
        check("第 1 次抓到的原文正确", src == original, repr(src))
        check("第 1 次方向判定为 text2bin",
              core.auto_detect(original) == "text2bin",
              core.auto_detect(original))
        check("第 1 次结果全是 0/1 和分隔符",
              all(ch in "01 " for ch in out), repr(out))
        check("第 1 次结果已粘回输入框", t.get_text() == out, repr(t.get_text()))

        # 光标现在在二进制末尾，再按一次应该自动转回来
        st2, src2, out2, msg2 = convert_pumped(auto_convert, editor=t)
        check("第 2 次（二进制->文字）状态 ok", st2 == "ok", "%s %s" % (st2, msg2))
        check("第 2 次方向判定为 bin2text",
              core.auto_detect(src2) == "bin2text",
              core.auto_detect(src2))
        check("第 2 次抓到的原文就是那串二进制", src2 == out, repr(src2))
        check("第 2 次结果还原成原话", out2 == original, repr(out2))
        check("第 2 次结果已粘回输入框", t.get_text() == original,
              repr(t.get_text()))

        # ---- 场景 2：英文 + 紧凑模式往返 ----
        original2 = "hi there"
        t.set_text(original2)
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.text_to_binary(s, sep=""), editor=t)
        check("紧凑模式 状态 ok", st == "ok", "%s %s" % (st, msg))
        check("紧凑模式 结果无空格", " " not in out, repr(out))
        st2, src2, out2, msg2 = convert_pumped(auto_convert, editor=t)
        check("紧凑模式 自动判定为 bin2text",
              core.auto_detect(src2) == "bin2text", core.auto_detect(src2))
        check("紧凑模式 还原成原话", out2 == original2, repr(out2))

        # ---- 场景 3：长句（超过一屏宽度，验证能完整取回）----
        original3 = "今天天气不错我们出去走走吧"
        t.set_text(original3)
        pump(150)
        st, src, out, msg = convert_pumped(auto_convert, editor=t)
        check("长句 状态 ok", st == "ok", "%s %s" % (st, msg))
        check("长句 抓取完整", src == original3,
              "%r vs %r" % (src, original3))
        st2, src2, out2, msg2 = convert_pumped(auto_convert, editor=t)
        check("长句 还原成原话", out2 == original3, repr(out2))

    finally:
        t.destroy()
        if saved_clip:
            clipwin.write_text(saved_clip)

    print()
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    if FAILED:
        print("失败：%s" % "、".join(FAILED))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
