# -*- coding: utf-8 -*-
"""BinFloater 入口：单实例 + 悬浮窗启动。

命令行：
  python main.py           正常启动
  python main.py --decode "01001000"   直接解码打印到控制台（不进界面）
  python main.py --encode "Hi"         直接编码打印到控制台
"""
import sys

import clipwin
import core

_MUTEX_HANDLE = None  # 持有互斥体句柄，进程存活期间有效


def _cli(argv) -> bool:
    """处理命令行直转模式，返回 True 表示已处理完、不进 GUI。"""
    for i, arg in enumerate(argv):
        if arg in ("--decode", "-d") and i + 1 < len(argv):
            text = argv[i + 1]
            result, msg = core.binary_to_text(text)
            print(result if result is not None else f"[失败] {msg}")
            return True
        if arg in ("--encode", "-e") and i + 1 < len(argv):
            print(core.text_to_binary(argv[i + 1]))
            return True
    return False


def _single_instance() -> bool:
    """用命名互斥体防多开（Windows 下重复双击会聚焦已有窗口）。"""
    if not clipwin.IS_WINDOWS:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, "Global\\BinFloater_SingleInstance")
        if not handle:
            return True
        # 必须用 get_last_error() 读，直接调 GetLastError() 可能被后续调用覆盖
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowW.restype = wintypes.HWND
            hwnd = user32.FindWindowW(None, "BinFloater")
            if hwnd:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            return False
        # 句柄要一直持有，否则互斥体被释放，防多开就失效了
        global _MUTEX_HANDLE
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def main():
    if _cli(sys.argv[1:]):
        return
    if not _single_instance():
        return
    import gui
    gui.main()


if __name__ == "__main__":
    main()
