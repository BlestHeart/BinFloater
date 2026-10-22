# -*- coding: utf-8 -*-
"""全局键盘监听：双击空格 或 组合热键 触发「转换当前行」。

用 WH_KEYBOARD_LL 低级键盘钩子。选它的理由：
  RegisterHotKey 只能注册单次组合键，做不了「连按两次空格」这种时序判断；
  而且 RegisterHotKey 要在窗口过程里收 WM_HOTKEY，tkinter 不方便接管。

**钩子回调里绝对不能干重活**：Windows 有个 LowLevelHooksTimeout（默认 300ms），
回调超时会被系统静默摘掉钩子，之后就再也不触发了，而且不报错。
所以回调只做「记录时间戳 / 判断条件」，真正的活通过队列丢给 Tk 主线程去做。
"""
import ctypes
import os
import time
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

LLKHF_INJECTED = 0x10

VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_B = 0x42

# 两次空格的间隔上限。太长会误触（正常打字相邻两个空格很常见），
# 太短用户按不出来。350ms 是试出来比较顺手的值。
DOUBLE_SPACE_MS = 350


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
    ]
    _user32.SetWindowsHookExW.restype = ctypes.c_void_p
    _user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.CallNextHookEx.restype = ctypes.c_void_p
    _user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class KeyboardWatcher:
    """低级键盘钩子。检测到触发条件时调用 on_trigger()。

    on_trigger 会在钩子回调的上下文里被调用 —— 调用方必须保证它只做入队
    之类的轻量操作，不能在里面做剪切板读写或 SendInput。
    """

    def __init__(self, on_trigger, space_mode=True, hotkey_mode=True):
        self.on_trigger = on_trigger
        # 两个开关独立：关掉的那个绝不能吞按键，否则用户会莫名其妙丢空格。
        self.space_mode = space_mode
        self.hotkey_mode = hotkey_mode
        self._hook = None
        self._proc = None
        self._last_space = 0.0
        # 空格连按模式下，第一下空格按下时记时间；第二下才算触发
        self._pending_space = False

    def install(self) -> bool:
        if not IS_WINDOWS or self._hook:
            return False
        try:
            self._proc = HOOKPROC(self._callback)
            hmod = _kernel32.GetModuleHandleW(None)
            self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
            return bool(self._hook)
        except Exception:
            self._hook = None
            return False

    def uninstall(self) -> None:
        if self._hook:
            try:
                _user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._proc = None

    def _down(self, vk: int) -> bool:
        try:
            return bool(_user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def _callback(self, n_code, w_param, l_param):
        if n_code < 0:
            return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)
        try:
            if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                # 注入的按键（我们自己 SendInput 发的）不能再触发一次，否则会死循环
                if not (info.flags & LLKHF_INJECTED):
                    if self._on_key(int(info.vkCode)):
                        return 1  # 吞掉这个按键，别让它落到目标程序里
        except Exception:
            pass
        return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def _on_key(self, vk: int) -> bool:
        """返回 True 表示这个按键要吞掉。"""
        now = time.time()
        if vk == VK_SPACE:
            if not self.space_mode:
                return False
            if self._pending_space and (now - self._last_space) * 1000 <= DOUBLE_SPACE_MS:
                self._pending_space = False
                self._last_space = 0.0
                self.on_trigger("space")
                # 吞掉第二个空格：不然它会在我们发 Shift+Home 之前抢先插进去，
                # 万一它比 Shift+Home 晚到，就会把选中的文字覆盖掉，整行报废。
                return True
            self._pending_space = True
            self._last_space = now
            return False

        # 除了空格，其他按键都会打断「连按空格」的判定：
        # 用户打了字之后又按空格，不该和上一次的空格凑成一对。
        self._pending_space = False

        if self.hotkey_mode and vk == VK_B and self._down(VK_CONTROL) and self._down(VK_MENU):
            self.on_trigger("hotkey")
            return True
        return False
