# -*- coding: utf-8 -*-
"""Windows 托盘图标 + 气泡通知（纯 ctypes，不依赖第三方库）。

为什么自己写而不是用 pystray：
  1. 打包成 exe 后少一个 hiddenimport，少一处可能踩的坑；
  2. 我们只要「一个图标 + 右键菜单 + 气泡通知」，而 pystray 恰好不支持气泡
     通知（Shell_NotifyIcon 的 NIF_INFO 分支），可「最小化到托盘时告诉用户
     快捷键还能用」正是靠气泡通知；
  3. 自己控制消息循环，能确保托盘线程绝不碰 tkinter。

**托盘线程绝不能碰 tkinter**：所有回调都只是往队列里塞事件，主线程用
after 轮询取（和 trigger.py 的键盘钩子同一个套路）。
"""
import ctypes
import os
import sys
import threading
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_TRAY_NOTIFY = WM_APP + 2
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_NULL = 0x0000

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

IDI_APPLICATION = 32512

ID_SHOW = 1001
ID_QUIT = 1002

MENU_SHOW = "打开悬浮窗"
MENU_QUIT = "退出"


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeout", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
        wintypes.WPARAM, wintypes.LPARAM
    )

    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.DefWindowProcW.restype = ctypes.c_void_p
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
    ]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = ctypes.c_void_p
    _user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = [
        wintypes.HMENU, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR
    ]
    _user32.AppendMenuW.restype = wintypes.BOOL
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
    ]
    _user32.TrackPopupMenu.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _shell32.Shell_NotifyIconW.argtypes = [
        wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)
    ]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    MENU_SEPARATOR = 0x00000800
    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100
    SM_CXSMICON = 49


def resource_path(name: str) -> str:
    """打包成 exe 后资源在 _MEIPASS 下，开发时就在源码旁边。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cand = os.path.join(base, name)
        if os.path.exists(cand):
            return cand
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, name)


class TrayIcon:
    """托盘图标。

    on_event(kind) 会在托盘线程里被调用，kind 是 "show" 或 "quit"。
    调用方必须只做入队，不能碰 tkinter。
    """

    def __init__(self, on_event, tooltip="二进制悬浮窗"):
        self.on_event = on_event
        self.tooltip = tooltip[:127]
        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()
        self._proc = None
        self._class_atom = None
        self._icon = None
        self._lock = threading.Lock()
        self._pending_notify = None
        self._failed = False

    # ---------- 生命周期 ----------
    def start(self, timeout=2.0) -> bool:
        if not IS_WINDOWS:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._thread = threading.Thread(
            target=self._run, name="BinFloaterTray", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout)
        return self._hwnd is not None

    def stop(self) -> None:
        hwnd = self._hwnd
        if hwnd and IS_WINDOWS:
            try:
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
        t = self._thread
        if t is not None and t.is_alive():
            t.join(1.5)
        self._thread = None
        self._hwnd = None

    @property
    def running(self) -> bool:
        return self._hwnd is not None

    # ---------- 气泡通知 ----------
    def notify(self, title: str, text: str, warning=False) -> bool:
        """弹一条 Windows 气泡通知。任意线程可调。"""
        if not IS_WINDOWS or not self._hwnd:
            return False
        self._pending_notify = (title, text, bool(warning))
        try:
            _user32.PostMessageW(self._hwnd, WM_TRAY_NOTIFY, 0, 0)
            return True
        except Exception:
            self._pending_notify = None
            return False

    # ---------- 内部 ----------
    def _run(self):
        try:
            self._create_window()
        except Exception:
            self._failed = True
            self._ready.set()
            return
        if not self._hwnd:
            self._ready.set()
            return
        self._add_icon()
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
        finally:
            self._remove_icon()
            self._hwnd = None

    def _create_window(self):
        hinst = _kernel32.GetModuleHandleW(None)
        cls_name = "BinFloaterTrayWnd"
        self._proc = WNDPROC(self._wndproc)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = self._load_icon(hinst)
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = cls_name
        self._class_atom = _user32.RegisterClassW(ctypes.byref(wc))
        if not self._class_atom:
            return
        # 普通顶层窗口但从不 ShowWindow：气泡通知要求窗口是真的顶层窗口，
        # 用 HWND_MESSAGE（仅消息窗口）在部分 Windows 版本上气泡不显示。
        self._hwnd = _user32.CreateWindowExW(
            0, cls_name, "BinFloaterTray", 0,
            0, 0, 0, 0, None, None, hinst, None
        )

    def _load_icon(self, hinst):
        try:
            path = resource_path("icon.ico")
            if os.path.exists(path):
                size = _user32.GetSystemMetrics(SM_CXSMICON) or 16
                h = _user32.LoadImageW(
                    None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE
                )
                if h:
                    self._icon = h
                    return h
        except Exception:
            pass
        try:
            h = _user32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))
            self._icon = h
            return h
        except Exception:
            return None

    def _nid(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        return nid

    def _add_icon(self):
        nid = self._nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._icon
        nid.szTip = self.tooltip
        try:
            _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        except Exception:
            pass

    def _remove_icon(self):
        if not self._hwnd:
            return
        nid = self._nid()
        try:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass

    def _show_balloon(self):
        payload = self._pending_notify
        self._pending_notify = None
        if not payload or not self._hwnd:
            return
        title, text, warning = payload
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = (title or "")[:63]
        nid.szInfo = (text or "")[:255]
        nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        nid.uTimeout = 8000
        try:
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    def _popup_menu(self):
        try:
            menu = _user32.CreatePopupMenu()
            if not menu:
                return
            _user32.AppendMenuW(menu, 0, ID_SHOW, MENU_SHOW)
            _user32.AppendMenuW(menu, MENU_SEPARATOR, 0, None)
            _user32.AppendMenuW(menu, 0, ID_QUIT, MENU_QUIT)
            pt = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            # 不先抢前台的话菜单点外面不会消失，这是托盘菜单的老毛病
            _user32.SetForegroundWindow(self._hwnd)
            cmd = _user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                pt.x, pt.y, 0, self._hwnd, None
            )
            _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
            _user32.DestroyMenu(menu)
            if cmd == ID_SHOW:
                self._emit("show")
            elif cmd == ID_QUIT:
                self._emit("quit")
        except Exception:
            pass

    def _emit(self, kind):
        try:
            self.on_event(kind)
        except Exception:
            pass

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                # 低字是鼠标消息
                evt = lparam & 0xFFFF
                if evt in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._emit("show")
                elif evt == WM_RBUTTONUP:
                    self._popup_menu()
                return 0
            if msg == WM_TRAY_NOTIFY:
                self._show_balloon()
                return 0
            if msg == WM_COMMAND:
                cmd = wparam & 0xFFFF
                if cmd == ID_SHOW:
                    self._emit("show")
                    return 0
                if cmd == ID_QUIT:
                    self._emit("quit")
                    return 0
            if msg == WM_CLOSE:
                _user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
