# -*- coding: utf-8 -*-
"""快捷键的表示、解析与显示（纯标准库，不依赖 Windows）。

为什么单独一个模块：
  快捷键要能在三处用同一套规则——保存到 config.json、装钩子时比较按键、
  界面上显示给人看。如果各处各写一份映射表，迟早出现「存的是 Ctrl+Alt+B、
  钩子比较的是 Ctrl+Alt+K、界面显示的是 Ctrl+Shift+B」这种对不上的问题。

存储格式统一用字符串，例如 "ctrl+alt+b"：
  - 全小写，修饰键在前，固定顺序 ctrl / alt / shift / win
  - 主键用 hotkey 名称表里的名字（b、f8、space、left…）
  这样 config.json 人眼可读，出问题能直接改。
"""
import os

IS_WINDOWS = os.name == "nt"

# 修饰键的虚拟键码
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# 单独按下的修饰键。捕获快捷键时要跳过它们，否则用户按 Ctrl 的瞬间就被当成
# 「快捷键 = Ctrl」结束了录制。
MODIFIER_VKS = frozenset((VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))

# 修饰键在字符串里的固定顺序。Windows 习惯把 Ctrl 放最前、Win 放最后，
# 固定顺序是为了让同一个组合只有一种写法，比较时不用考虑排列。
MOD_ORDER = ("ctrl", "alt", "shift", "win")
MOD_TEXT = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}

# 主键名称 -> 虚拟键码。只收常用键，够覆盖绝大多数人的选择。
# 名字一律小写：parse() 会把用户输入统一转小写再查表，
# 大写的键名在这里会让 "ctrl+alt+b" 解析失败（曾经踩过）。
KEY_VKS = {
    # 字母
    **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},
    # 数字（主键盘）
    **{chr(c): c for c in range(ord("0"), ord("9") + 1)},
    # 功能键
    **{"f%d" % i: 0x6F + i for i in range(1, 25)},
    # 编辑键
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    # 符号（美式布局的 OEM 键）
    "semicolon": 0xBA,
    "equals": 0xBB,
    "comma": 0xBC,
    "minus": 0xBD,
    "period": 0xBE,
    "slash": 0xBF,
    "backtick": 0xC0,
    "lbracket": 0xDB,
    "backslash": 0xDC,
    "rbracket": 0xDD,
    "quote": 0xDE,
}

# 反查表：虚拟键码 -> 名称。同一个 vk 只对应一个名字（字母只有大写形式）。
VK_NAMES = {}
for _name, _vk in KEY_VKS.items():
    if _vk not in VK_NAMES:
        VK_NAMES[_vk] = _name

# 界面上给人看的写法。字母数字直接大写，其余用约定的短名。
KEY_TEXT = {
    "backspace": "Backspace", "tab": "Tab", "enter": "Enter", "esc": "Esc",
    "space": "Space", "pageup": "PgUp", "pagedown": "PgDn", "end": "End",
    "home": "Home", "left": "←", "up": "↑", "right": "→", "down": "↓",
    "insert": "Insert", "delete": "Delete", "semicolon": ";", "equals": "=",
    "comma": ",", "minus": "-", "period": ".", "slash": "/",
    "backtick": "`", "lbracket": "[", "backslash": "\\", "rbracket": "]",
    "quote": "'",
}

DEFAULT_HOTKEY = "ctrl+alt+b"

# 只有单个键、没有修饰键的快捷键要特别小心：钩子会吞掉这个键的所有按下，
# 用户以后就没法正常打这个字了。允许但必须提醒。
WARN_NO_MODIFIER = "没有修饰键的快捷键会吞掉这个键的所有按下，建议至少加 Ctrl 或 Alt"


class Hotkey(object):
    """一个解析好的快捷键。"""

    __slots__ = ("mods", "key", "vk")

    def __init__(self, mods, key, vk):
        self.mods = frozenset(mods)
        self.key = key
        self.vk = vk

    def __eq__(self, other):
        return (isinstance(other, Hotkey)
                and self.mods == other.mods and self.vk == other.vk)

    def __hash__(self):
        return hash((self.mods, self.vk))

    def __repr__(self):
        return "Hotkey(%r)" % (self.spec(),)

    def spec(self) -> str:
        """存储用字符串，如 "ctrl+alt+b"。"""
        parts = [m for m in MOD_ORDER if m in self.mods]
        parts.append(self.key)
        return "+".join(parts)

    def text(self) -> str:
        """显示用字符串，如 "Ctrl+Alt+B"。"""
        parts = [MOD_TEXT[m] for m in MOD_ORDER if m in self.mods]
        parts.append(KEY_TEXT.get(self.key, self.key.upper()))
        return "+".join(parts)

    def has_modifier(self) -> bool:
        return bool(self.mods)


def parse(spec):
    """把 "ctrl+alt+b" 解析成 Hotkey，失败返回 None。

    容忍大小写、空格、多余的分隔符（"Ctrl + Alt + B" 也行），因为用户
    可能会手改 config.json，不该因为一个空格就整个功能失效。
    """
    if not spec or not isinstance(spec, str):
        return None
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+")]
    parts = [p for p in parts if p]
    if not parts:
        return None

    mods = set()
    key = None
    for p in parts:
        if p in ("ctrl", "control"):
            mods.add("ctrl")
        elif p in ("alt", "menu"):
            mods.add("alt")
        elif p in ("shift",):
            mods.add("shift")
        elif p in ("win", "super", "meta"):
            mods.add("win")
        elif p in KEY_VKS:
            if key is not None:
                return None  # 两个主键，非法
            key = p
        else:
            return None

    if key is None:
        return None  # 只有修饰键，非法
    return Hotkey(mods, key, KEY_VKS[key])


def make(mods, vk):
    """从「按下的修饰键集合 + 主键 vk」构造 Hotkey，失败返回 None。

    钩子捕获时用这个：钩子只能给出主键的 vkCode，修饰键状态要另外问系统。
    """
    key = VK_NAMES.get(int(vk))
    if key is None:
        return None
    mods = set(m for m in mods if m in MOD_ORDER)
    return Hotkey(mods, key, KEY_VKS[key])


def load(spec):
    """解析配置里的快捷键，坏了就退回默认值，绝不抛异常。"""
    hk = parse(spec)
    if hk is None:
        hk = parse(DEFAULT_HOTKEY)
    return hk


def describe_mods_from_state(down):
    """从「哪些修饰键正被按住」的判定函数生成 mods 集合。

    down 是 callable(vk) -> bool，传入 VK_CONTROL 等返回是否按下。
    """
    mods = []
    if down(VK_CONTROL):
        mods.append("ctrl")
    if down(VK_MENU):
        mods.append("alt")
    if down(VK_SHIFT):
        mods.append("shift")
    if down(VK_LWIN) or down(VK_RWIN):
        mods.append("win")
    return mods
