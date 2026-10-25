# -*- coding: utf-8 -*-
"""Win32 剪切板读写与变化监听（纯标准库 ctypes，打包友好）。

只用 CF_UNICODETEXT，读取失败时短重试，避免被别的程序占用时漏读。
"""
import ctypes
import os
import time
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.argtypes = []
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    _user32.GetClipboardSequenceNumber.argtypes = []
    _user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    _user32.SetWindowRgn.restype = ctypes.c_int

    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalFree.restype = wintypes.HGLOBAL

    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _gdi32.CreateRoundRectRgn.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int,
    ]
    _gdi32.CreateRoundRectRgn.restype = wintypes.HRGN


def sequence() -> int:
    """剪切板变化计数，变化即代表内容被写入过。"""
    if not IS_WINDOWS:
        return 0
    return int(_user32.GetClipboardSequenceNumber())


def _open_clipboard(retries: int = 6, delay: float = 0.012) -> bool:
    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def read_text(retries: int = 6) -> str:
    """读剪切板纯文本；没有文本、打不开或为空都返回 ""。"""
    if not IS_WINDOWS:
        return ""
    if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    if not _open_clipboard(retries):
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def write_text(text: str, retries: int = 6) -> bool:
    """把文本写入剪切板（GlobalAlloc + SetClipboardData，进程退出后仍保留）。"""
    if not IS_WINDOWS:
        return False
    data = (text + "\0").encode("utf-16-le")
    if not _open_clipboard(retries):
        return False
    try:
        _user32.EmptyClipboard()
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            _kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            _kernel32.GlobalUnlock(handle)
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            return False
        # 成功后内存归系统所有，不能再 GlobalFree
        return True
    finally:
        _user32.CloseClipboard()


def round_window(hwnd: int, width: int, height: int, radius: int = 14) -> None:
    """给窗口套一个圆角区域（无边框悬浮窗用）。"""
    if not IS_WINDOWS or not hwnd or width <= 0 or height <= 0:
        return
    try:
        region = _gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
        if region:
            _user32.SetWindowRgn(hwnd, region, True)
    except Exception:
        pass


# 拖放相关。Tkinter 自己不做文件拖放，得走 Win32 的 WM_DROPFILES：
# 先 DragAcceptFiles 声明接收，用户拖进来时系统把这个消息递给窗口过程。
# 注意这几个函数在 shell32 里，不在 user32（猜错会 AttributeError）。
WM_DROPFILES = 0x0233

if IS_WINDOWS:
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    _shell32.DragAcceptFiles.restype = None
    _shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                        ctypes.c_wchar_p, wintypes.UINT]
    _shell32.DragQueryFileW.restype = wintypes.UINT
    _shell32.DragFinish.argtypes = [wintypes.HANDLE]
    _shell32.DragFinish.restype = None


def accept_dropped_files(hwnd: int) -> bool:
    """声明这个窗口接受文件拖放。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        _shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
        return True
    except Exception:
        return False


def read_dropped_files(hdrop) -> list:
    """从 WM_DROPFILES 的句柄里取出文件路径列表。

    调用方在窗口过程里拿到 wParam 后传进来。释放（DragFinish）在这里代做，
    因为这个句柄除了取路径没有别的用处，漏掉就泄漏了。
    """
    if not IS_WINDOWS or not hdrop:
        return []
    files = []
    try:
        # 第一参数传 0xFFFFFFFF 时返回文件个数
        count = _shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        for i in range(int(count or 0)):
            need = _shell32.DragQueryFileW(hdrop, i, None, 0)
            if not need:
                continue
            buf = ctypes.create_unicode_buffer(int(need) + 1)
            _shell32.DragQueryFileW(hdrop, i, buf, int(need) + 1)
            if buf.value:
                files.append(buf.value)
    except Exception:
        files = []
    finally:
        try:
            _shell32.DragFinish(wintypes.HANDLE(hdrop))
        except Exception:
            pass
    return files
