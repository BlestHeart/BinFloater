# -*- coding: utf-8 -*-
"""端到端测试：真的建一个 Win32 窗口当目标，用 SendInput 打字，然后跑 convert_line。

这是最有价值的一组测试 —— 它验证的是「真按键 + 真剪切板 + 真粘贴」整条链路，
而不是把 tap() 换成假实现。之前误判 SendInput 不可用，是因为建原生控件时
没有确认 SetFocus 真的落到子控件上（dbg_activate.py 证明按键消息能进本进程，
dbg_edit2.py 证明焦点设对后原生 EDIT 能收到字符）。

跑法：python tests/e2e_convert.py
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

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
# 句柄是 64 位的，不声明 restype 会被 ctypes 截成 32 位 int，
# 高地址窗口句柄一截就永远不等于 self.main，白白多等好几轮
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

WM_SETTEXT = 0x000C
WM_GETTEXTLENGTH = 0x000E
EM_SETSEL = 0x00B1
EM_GETSEL = 0x00B0


def _get_sel(hwnd):
    """读编辑控件当前的选区 (起, 止)，用来确认光标有没有被误推走。

    用 EM_GETSEL 的返回值形式（lParam 传 0）：低 16 位是起点、高 16 位是终点。
    传传出指针要走跨进程封送，这里不折腾。
    """
    r = int(user32.SendMessageW(hwnd, EM_GETSEL, 0, 0)) & 0xFFFFFFFF
    return (r & 0xFFFF, (r >> 16) & 0xFFFF)


def _type_char(hwnd, ch):
    """往编辑控件里塞一个字符（用 WM_CHAR，绕过输入法），用来探光标落点。"""
    user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
    time.sleep(0.06)

# 控件样式
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
ES_LEFT = 0x0000
ES_MULTILINE = 0x0004
WS_EX_CLIENTEDGE = 0x00000200


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class Target:
    """一个真的 Win32 窗口 + 一个真的 EDIT 控件，当作「用户正在打字的输入框」。"""

    _registered = False
    # 必须存在类属性上！临时 WNDPROC 对象被 GC 回收后，窗口过程指针就悬空了，
    # 窗口一收消息（ShowWindow / SetForegroundWindow 都会发）就 access violation。
    _PROC = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))

    def __init__(self):
        hinst = k32.GetModuleHandleW(None)
        if not Target._registered:
            wc = WNDCLASSW()
            wc.lpfnWndProc = Target._PROC
            wc.hInstance = hinst
            wc.hbrBackground = 6
            wc.lpszClassName = "E2ETargetWnd"
            user32.RegisterClassW(ctypes.byref(wc))
            Target._registered = True
        self.main = user32.CreateWindowExW(
            0, "E2ETargetWnd", "e2e-target", 0x00CF0000,
            380, 280, 560, 240, None, None, hinst, None)
        self.edit = user32.CreateWindowExW(
            WS_EX_CLIENTEDGE, "EDIT", "",
            WS_CHILD | WS_VISIBLE | ES_LEFT | ES_MULTILINE | WS_BORDER,
            10, 10, 520, 170, self.main, None, hinst, None)
        user32.ShowWindow(self.main, 5)
        user32.UpdateWindow(self.main)
        time.sleep(0.3)

    def focus(self):
        """把焦点给 EDIT 控件。必须逐步确认，否则按键会丢。

        注意不能只看 GetFocus —— 它只说明「本线程内的焦点控件」，哪怕整个
        窗口在后台 GetFocus 也照样返回 EDIT。可 SendInput 是往**前台**窗口
        投的，窗口不在前台时按键全打到别处去，表现就是 grab_line 复制到空。
        所以这里要用 GetForegroundWindow 验证，并走 keys.focus_window 的
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
        # 光标挪到末尾
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
    """处理窗口消息。SendInput 投递的消息需要有人泵出来才会进控件。"""
    end = time.time() + ms / 1000.0
    msg = MSG()
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.006)


def settle(editor, timeout=1.2):
    """等编辑框内容稳定下来。

    convert_line 发完 Ctrl+V 只 sleep 一小会儿，那只保证「按键发出去了」，
    不保证目标程序已经把粘贴内容处理完。紧接着读内容会读到粘贴前的旧值
    （「中文 二进制已粘回 实际 '你好'」就是这么来的）。连续两次采样一致才算稳。
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

    这一步是必需的：convert_line 内部会 tap 按键然后 sleep 等剪切板变化，
    而 SendInput 投递的键消息只有被泵出来才会进控件。真实 GUI 里 tkinter 的
    mainloop 一直在泵，所以产品没问题；测试里得自己造这个条件。

    editor 传进来时会先重新确认一次前台再开跑：窗口刚建好时
    GetForegroundWindow 返回的已经是它，但系统还要走完激活流程才真正接收
    键盘输入，这中间的空档里发按键会丢，grab_line 拿到空行。
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
        print("  FAIL  %s   %s" % (name, detail))


def main():
    print("=== 端到端：真按键 + 真剪切板 + 真粘贴 ===")
    print()

    # 保存用户原本的剪切板，测完还原
    saved_clip = clipwin.read_text()
    t = Target()
    try:
        ok_focus = t.focus()
        check("目标窗口拿到焦点", ok_focus)

        # ---- 1. 文字 → 二进制 ----
        t.set_text("hi")
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.text_to_binary(s, sep=" "), restore_clip=True, editor=t)
        pump(250)
        got = t.get_text()
        expect = "01101000 01101001"
        check("文字→二进制 状态 ok", st == "ok", "实际 %s / %s" % (st, msg))
        check("文字→二进制 原文正确", src == "hi", "实际 %r" % src)
        check("文字→二进制 结果正确", out == expect, "实际 %r" % out)
        check("文字→二进制 已粘回输入框", got.strip() == expect,
              "输入框实际 %r" % got)

        # ---- 2. 二进制 → 文字 ----
        t.set_text(expect)
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.binary_to_text(s)[0] or "", restore_clip=True, editor=t)
        pump(250)
        got = t.get_text()
        check("二进制→文字 状态 ok", st == "ok", "实际 %s / %s" % (st, msg))
        check("二进制→文字 结果正确", out == "hi", "实际 %r" % out)
        check("二进制→文字 已粘回输入框", got.strip() == "hi",
              "输入框实际 %r" % got)

        # ---- 3. 中文往返（多字节） ----
        t.set_text("你好")
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.text_to_binary(s, sep=" "), restore_clip=True, editor=t)
        pump(250)
        bintext = t.get_text().strip()
        expect_cn = core.text_to_binary("你好", sep=" ")
        check("中文 文字→二进制 结果正确", out == expect_cn, "实际 %r" % out)
        check("中文 二进制已粘回", bintext == expect_cn, "实际 %r" % bintext)

        t.set_text(bintext)
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.binary_to_text(s)[0] or "", restore_clip=True, editor=t)
        pump(250)
        check("中文 二进制→文字 往返", t.get_text().strip() == "你好",
              "实际 %r" % t.get_text())

        # ---- 4. 只转换光标前那一段 ----
        t.set_text("abcdef")
        # 光标放在第 3 个字符后：选中区间 [3,3]
        user32.SendMessageW(t.edit, EM_SETSEL, 3, 3)
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.text_to_binary(s, sep=" "), restore_clip=True, editor=t)
        pump(250)
        check("只取行首到光标", src == "abc", "实际 %r" % src)
        # 粘回后应该是「abc 的二进制」+ 原来光标后的 def
        got = t.get_text().strip()
        check("粘贴保留光标后内容", got.endswith("def"),
              "实际 %r" % got)

        # ---- 5. 空行：不该乱发按键 ----
        t.set_text("")
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.text_to_binary(s, sep=" "), restore_clip=True, editor=t)
        pump(200)
        check("空行返回 empty", st == "empty", "实际 %s" % st)

        # ---- 5b. 光标在文档最开头：不能把第一个字吃掉 ----
        # 这是真 bug 的回归：Ctrl+Shift+Home 在文档开头选不中任何东西，
        # 早期版本把这当成失败去重试，重试前按了一下右方向键把光标推后一格，
        # 第二次就抓到第一个字符 —— 文档变成 "01101000ello world"。
        t.set_text("hello world")
        pump(150)
        user32.SendMessageW(t.edit, EM_SETSEL, 0, 0)
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.text_to_binary(s, sep=" "), restore_clip=True, editor=t)
        pump(250)
        check("光标在文档开头返回 empty", st == "empty", "实际 %s / %s" % (st, msg))
        check("光标在文档开头文档没被改", t.get_text().strip() == "hello world",
              "实际 %r" % t.get_text())
        sel = _get_sel(t.edit)
        check("光标在文档开头光标没被推走", sel == (0, 0), "实际 %r" % (sel,))

        # ---- 5c. 光标在第二行行首（行首有字）：同样不能吃掉上一行/本行内容 ----
        t.set_text("abc\r\nxyz")
        pump(150)
        user32.SendMessageW(t.edit, EM_SETSEL, 5, 5)
        pump(150)
        st, src, out, msg = convert_pumped(
            lambda s: core.text_to_binary(s, sep=" "), restore_clip=True, editor=t)
        pump(250)
        check("第二行行首返回 empty", st == "empty", "实际 %s" % st)
        check("第二行行首文档没被改", t.get_text().strip() == "abc\r\nxyz",
              "实际 %r" % t.get_text())
        sel = _get_sel(t.edit)
        check("第二行行首光标没被推走", sel == (5, 5), "实际 %r" % (sel,))

        # ---- 6. 转换后和原文一样：不该粘贴 ----
        t.set_text("hello")
        pump(150)
        st, src, out, msg = convert_pumped(lambda s: s, restore_clip=True, editor=t)
        pump(200)
        check("结果同原文返回 same", st == "same", "实际 %s" % st)

        # ---- 7. 剪切板还原 ----
        marker = "E2E_MARKER_%d" % int(time.time())
        clipwin.write_text(marker)
        time.sleep(0.05)
        t.set_text("hi")
        pump(150)
        convert_pumped(lambda s: core.text_to_binary(s, sep=" "),
                          restore_clip=True, editor=t)
        pump(200)
        check("剪切板已还原", clipwin.read_text() == marker,
              "实际 %r" % clipwin.read_text())

        # ---- 8. 不还原剪切板时，结果留在剪切板 ----
        t.set_text("hi")
        pump(150)
        convert_pumped(lambda s: core.text_to_binary(s, sep=" "),
                          restore_clip=False, editor=t)
        pump(200)
        check("restore_clip=False 时结果留在剪切板",
              clipwin.read_text().strip() == expect,
              "实际 %r" % clipwin.read_text())

    finally:
        t.destroy()
        if saved_clip:
            clipwin.write_text(saved_clip)

    print()
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    if FAILED:
        print("失败项：")
        for n in FAILED:
            print("  -", n)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
