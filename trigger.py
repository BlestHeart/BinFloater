# -*- coding: utf-8 -*-
"""全局键盘监听：空格连按 或 自定义组合热键 触发「转换当前行」。

用 WH_KEYBOARD_LL 低级键盘钩子。选它的理由：
  RegisterHotKey 只能注册固定的组合键，且要在窗口过程里收 WM_HOTKEY，
  tkinter 不方便接管；更重要的是它做不了「连按两次空格」这种时序判断，
  也没法在用户自定义快捷键时动态换绑。

**钩子回调里绝对不能干重活**：Windows 有个 LowLevelHooksTimeout（默认 300ms），
回调超时会被系统静默摘掉钩子，之后就再也不触发了，而且不报错。
所以回调只做「记录时间戳 / 判断条件」，真正的活通过队列丢给 Tk 主线程去做。
"""
import ctypes
import os
import time
from ctypes import wintypes

import hotkey as hk

IS_WINDOWS = os.name == "nt"

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

LLKHF_INJECTED = 0x10

VK_SPACE = 0x20
VK_ESCAPE = 0x1B
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_B = 0x42

# 两次空格的间隔上限。太长会误触（正常打字相邻两个空格很常见），
# 太短用户按不出来。350ms 是试出来比较顺手的值。
DOUBLE_SPACE_MS = 350

# 连按次数的取值范围。1 下等于「按一下空格就触发」，正常打字根本没法用，
# 但留着是因为有人可能配合别的开关用；上限 5 下，再多用户按不过来。
SPACE_COUNT_MIN = 1
SPACE_COUNT_MAX = 5
SPACE_COUNT_DEFAULT = 2


def clamp_space_count(n) -> int:
    """把连按次数夹到合法区间。配置坏了不该让功能失效，静默回默认值。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return SPACE_COUNT_DEFAULT
    if n < SPACE_COUNT_MIN or n > SPACE_COUNT_MAX:
        return SPACE_COUNT_DEFAULT
    return n


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
    """低级键盘钩子。检测到触发条件时调用 on_trigger(kind)。

    on_trigger 会在钩子回调的上下文里被调用 —— 调用方必须保证它只做入队
    之类的轻量操作，不能在里面做剪切板读写或 SendInput。

    hotkey        要监听的组合键（hotkey.Hotkey 实例），None 表示用默认值
    on_capture    录制新快捷键时的回调，收到 hotkey spec 字符串；
                  收到 "" 表示用户按 Esc 取消了录制
    capture_mode  为 True 时进入录制状态：不触发转换，只把按键组合报出去
    """

    def __init__(self, on_trigger, space_mode=True, hotkey_mode=True,
                 hotkey=None, on_capture=None, space_count=None):
        self.on_trigger = on_trigger
        self.on_capture = on_capture
        # 两个开关独立：关掉的那个绝不能吞按键，否则用户会莫名其妙丢空格。
        self.space_mode = space_mode
        self.hotkey_mode = hotkey_mode
        self.hotkey = hotkey if hotkey is not None else hk.load(hk.DEFAULT_HOTKEY)
        self.capture_mode = False
        # 连按几下空格才触发。默认 2 下；玩游戏的人容易被空格误触，
        # 可以调到 3 下甚至 4 下——代价是正常打字时更不容易凑巧按到。
        self.space_count = clamp_space_count(space_count)
        self._hook = None
        self._proc = None
        self._last_space = 0.0
        # 已经连按了几下空格。到 space_count 就触发。
        self._space_streak = 0

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

    def _current_mods(self):
        """当前正被按住的修饰键集合。"""
        return set(hk.describe_mods_from_state(self._down))

    def _on_key(self, vk: int) -> bool:
        """返回 True 表示这个按键要吞掉。"""
        # 录制模式优先：这时所有按键都用来组成新快捷键，绝不触发转换
        if self.capture_mode:
            return self._on_key_capture(vk)

        now = time.time()
        if vk == VK_SPACE and self.space_mode:
            fresh = (now - self._last_space) * 1000 <= DOUBLE_SPACE_MS
            if self._space_streak and fresh:
                self._space_streak += 1
            else:
                self._space_streak = 1
            self._last_space = now
            if self._space_streak >= self.space_count:
                self._space_streak = 0
                self._last_space = 0.0
                self.on_trigger("space")
                # 吞掉这一下空格：不然它会在我们发 Shift+Home 之前抢先插进去，
                # 万一它比 Shift+Home 晚到，就会把选中的文字覆盖掉，整行报废。
                return True
            return False

        # 除了空格，其他按键都会打断「连按空格」的判定：
        # 用户打了字之后又按空格，不该和上一次的空格凑成一对。
        self._space_streak = 0

        if self.hotkey_mode and self.hotkey is not None and vk == self.hotkey.vk:
            # 修饰键必须完全一致：Ctrl+Alt+B 绑的键，按 Ctrl+Alt+Shift+B 不触发。
            # 严格比较是为了可预期——否则用户永远不知道多按一个键算不算数。
            if self._current_mods() == set(self.hotkey.mods):
                self.on_trigger("hotkey")
                return True
        return False

    def _on_key_capture(self, vk: int) -> bool:
        """录制模式下的按键处理。"""
        if self.on_capture is None:
            self.capture_mode = False
            return False

        mods = self._current_mods()

        # 单独按 Esc = 取消。带修饰键的 Esc（Ctrl+Esc）仍然可以当快捷键用。
        if vk == VK_ESCAPE and not mods:
            self.on_capture("")
            return True

        # 修饰键本身不构成快捷键，等主键按下来
        if vk in hk.MODIFIER_VKS:
            return False

        h = hk.make(mods, vk)
        if h is None:
            return False  # 不认识的键（小键盘、多媒体键等），忽略
        self.on_capture(h.spec())
        # 吞掉：不然这个键会落进用户正在编辑的程序里
        return True
