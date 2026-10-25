# -*- coding: utf-8 -*-
"""跨进程唤醒：第二个实例让已经在跑的那个把窗口叫回来。

为什么不用 FindWindow + ShowWindow：收进托盘走的是 Tk 的 withdraw()，窗口的
WS_VISIBLE 被清掉，ShowWindow(SW_RESTORE) 对它不生效；而且主进程内部的
_in_tray 状态也不会跟着改，第二实例随即退出，用户看到的是「双击 exe 没反应」。

改用命名内核事件：第二实例 SetEvent，主实例在已有的 320ms 轮询里用
WaitForSingleObject(0) 检查，命中就调自己的 _show_from_tray()。事件是系统对象，
不落盘、不占磁盘、进程崩了自动回收。
"""
import ctypes
import os
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"

EVENT_NAME = "Global\\BinFloater_ShowRequest"
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

if IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                       wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.LPCWSTR]
    _kernel32.OpenEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.ResetEvent.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

EVENT_MODIFY_STATE = 0x0002


class Waker(object):
    """主实例持有的唤醒事件。"""

    def __init__(self):
        self._handle = None
        if not IS_WINDOWS:
            return
        try:
            # 手动重置事件：我们自己在 poll 里 ResetEvent，避免自动重置模式下
            # 两个 SetEvent 挤在一起被吞掉。
            self._handle = _kernel32.CreateEventW(None, True, False, EVENT_NAME)
        except Exception:
            self._handle = None

    def poll(self) -> bool:
        """有别的实例请求唤回窗口吗。命中时顺手把事件复位。"""
        if not self._handle:
            return False
        try:
            r = _kernel32.WaitForSingleObject(self._handle, 0)
            if r == WAIT_OBJECT_0:
                _kernel32.ResetEvent(self._handle)
                return True
        except Exception:
            pass
        return False

    def close(self):
        if self._handle:
            try:
                _kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


def request_show() -> bool:
    """第二实例调这个，让已经在跑的那个把窗口叫回来。"""
    if not IS_WINDOWS:
        return False
    try:
        h = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
        if not h:
            return False
        try:
            return bool(_kernel32.SetEvent(h))
        finally:
            _kernel32.CloseHandle(h)
    except Exception:
        return False
