# -*- coding: utf-8 -*-
"""BinFloater 入口：单实例 + 悬浮窗启动。

命令行：
  python main.py                       正常启动
  python main.py --version             打印版本号
  python main.py --decode "01001000"   直接解码打印到控制台（不进界面）
  python main.py --encode "Hi"         直接编码打印到控制台
"""
import sys

import clipwin
import core
import version

_MUTEX_HANDLE = None  # 持有互斥体句柄，进程存活期间有效


def _setup_stdout() -> None:
    """让控制台输出不再受系统代码页限制。

    Windows 控制台默认是 GBK，解出来的文字里只要有一个 GBK 表示不了的字符
    （比如按 latin-1 兜底显示的 0xFF），print 就抛 UnicodeEncodeError，
    用户看到的是整段 traceback 而不是结果。改成 UTF-8 + backslashreplace：
    实在表示不了就转义显示，至少不崩。
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


def _cli(argv) -> bool:
    """处理命令行直转模式，返回 True 表示已处理完、不进 GUI。"""
    for i, arg in enumerate(argv):
        # 只要版号就完事，不用为了它去 import gui（那会拉起整套 tkinter）
        if arg in ("--version", "-v"):
            print("BinFloater %s" % version.VERSION)
            return True
        if arg in ("--decode", "-d") and i + 1 < len(argv):
            text = argv[i + 1]
            result, msg = core.binary_to_text(text)
            if result is None:
                print(f"[失败] {msg}")
            else:
                print(result)
                # 只有在结果可能不可信时才多嘴：按 latin-1 兜底能「解码成功」
                # 任何字节，不提醒的话用户会以为解出了一句话。
                if "乱码" in msg:
                    print(f"[提示] {msg}", file=sys.stderr)
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
            # 已经在跑了。让它把窗口叫回来，而不是自己再开一个。
            # 不能只靠 FindWindow + ShowWindow：收进托盘用的是 withdraw()，
            # 那种窗口 ShowWindow(SW_RESTORE) 叫不回来，而且主进程内部的
            # _in_tray 状态不会跟着改，用户看到的是「双击 exe 没反应」。
            import wakeup
            wakeup.request_show()
            return False
        # 句柄要一直持有，否则互斥体被释放，防多开就失效了
        global _MUTEX_HANDLE
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


def main():
    _setup_stdout()
    if _cli(sys.argv[1:]):
        return
    if not _single_instance():
        return
    import gui
    gui.main()


if __name__ == "__main__":
    main()
