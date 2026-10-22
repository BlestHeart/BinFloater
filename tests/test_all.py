# -*- coding: utf-8 -*-
"""PC 端回归测试。跑法：python tests/test_all.py

不依赖 tkinter（GUI 只做静态检查），所以能在任何 Python 3.8+ 上跑。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import keys as keys_mod
import trigger


class TestCore(unittest.TestCase):
    """编解码核心 —— 和安卓端 BinCore 是同一套规则。"""

    def test_text_to_binary_ascii(self):
        self.assertEqual(core.text_to_binary("hi"), "01101000 01101001")

    def test_text_to_binary_compact(self):
        self.assertEqual(core.text_to_binary("hi", sep=""), "0110100001101001")

    def test_text_to_binary_chinese_is_utf8(self):
        # 「中」= E4 B8 AD
        self.assertEqual(core.text_to_binary("中"), "11100100 10111000 10101101")

    def test_round_trip_ascii(self):
        text = "Hello, World!"
        back, _msg = core.binary_to_text(core.text_to_binary(text))
        self.assertEqual(back, text)

    def test_round_trip_chinese(self):
        text = "你好，世界"
        back, _msg = core.binary_to_text(core.text_to_binary(text))
        self.assertEqual(back, text)

    def test_round_trip_emoji(self):
        text = "🎉"
        back, _msg = core.binary_to_text(core.text_to_binary(text))
        self.assertEqual(back, text)

    def test_clean_bits_strips_separators(self):
        self.assertEqual(core._clean_bits("0110 1000,0110-1001"), "0110100001101001")

    def test_clean_bits_strips_0b_prefix(self):
        self.assertEqual(core._clean_bits("0b01101000"), "01101000")

    def test_looks_like_binary_true(self):
        self.assertTrue(core.looks_like_binary("01101000 01101001"))

    def test_looks_like_binary_rejects_short(self):
        self.assertFalse(core.looks_like_binary("0101"))

    def test_looks_like_binary_rejects_text(self):
        self.assertFalse(core.looks_like_binary("hello world"))

    def test_looks_like_binary_rejects_huge(self):
        # 超过 20000 字符直接判否，避免误伤大段文本
        self.assertFalse(core.looks_like_binary("01" * 20000))

    def test_binary_to_text_pads_tail(self):
        # 位数不是 8 的倍数时末尾补 0，不该抛异常
        text, msg = core.binary_to_text("011010001")  # 9 位
        self.assertIsNotNone(text)
        self.assertIn("补", msg)

    def test_binary_to_text_rejects_too_short(self):
        # 不足 8 位连一个字节都组不成，应该明确失败而不是返回乱码
        text, msg = core.binary_to_text("0110100")  # 7 位
        self.assertIsNone(text)
        self.assertIn("不足", msg)

    def test_binary_to_text_empty(self):
        text, _msg = core.binary_to_text("")
        self.assertIsNone(text)

    def test_describe(self):
        self.assertEqual(core.describe("hi"), "2 字符 · 2 字节")
        self.assertEqual(core.describe("中"), "1 字符 · 3 字节")

    def test_auto_detect(self):
        self.assertEqual(core.auto_detect("01101000 01101001"), "bin2text")
        self.assertEqual(core.auto_detect("hello"), "text2bin")

    def test_binary_to_hex(self):
        self.assertEqual(core.binary_to_hex("01101000"), "68")


class TestTriggerLogic(unittest.TestCase):
    """双击空格 / 快捷键的状态机。直接调 _on_key，不需要真的装钩子。"""

    def setUp(self):
        self.fired = []
        self.w = trigger.KeyboardWatcher(lambda kind: self.fired.append(kind))

    def _space(self):
        return self.w._on_key(trigger.VK_SPACE)

    def test_single_space_does_not_fire(self):
        self.assertFalse(self._space())
        self.assertEqual(self.fired, [])

    def test_double_space_fires(self):
        self.assertFalse(self._space())
        self.assertTrue(self._space())  # 第二下要吞掉
        self.assertEqual(self.fired, ["space"])

    def test_triple_space_only_fires_once(self):
        self._space()
        self._space()
        self._space()
        self.assertEqual(self.fired, ["space"])

    def test_space_too_slow_does_not_fire(self):
        self._space()
        # 手动把时间戳往前推，模拟间隔超过 DOUBLE_SPACE_MS
        self.w._last_space -= (trigger.DOUBLE_SPACE_MS + 100) / 1000.0
        self.assertFalse(self._space())
        self.assertEqual(self.fired, [])

    def test_other_key_breaks_space_pair(self):
        self._space()
        self.w._on_key(0x41)  # 按了个 A
        self.assertFalse(self._space())
        self.assertEqual(self.fired, [])

    def test_space_mode_off_never_swallows(self):
        self.w.space_mode = False
        self.assertFalse(self._space())
        self.assertFalse(self._space())
        self.assertEqual(self.fired, [])

    def test_hotkey_off_does_not_fire(self):
        self.w.hotkey_mode = False
        # 模拟 Ctrl+Alt 都按着
        self.w._down = lambda vk: True
        self.assertFalse(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, [])

    def test_hotkey_fires_and_is_swallowed(self):
        self.w.hotkey_mode = True
        self.w._down = lambda vk: True
        self.assertTrue(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, ["hotkey"])

    def test_hotkey_requires_modifiers(self):
        self.w.hotkey_mode = True
        self.w._down = lambda vk: False
        self.assertFalse(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, [])


class TestStaticChecks(unittest.TestCase):
    """源码层面的约定。这些是踩过坑写下的，改动时别悄悄破坏。"""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.gui = open(os.path.join(root, "gui.py"), encoding="utf-8").read()
        self.trig = open(os.path.join(root, "trigger.py"), encoding="utf-8").read()

    def test_gui_does_not_import_from_hook_thread(self):
        # 钩子回调里绝不能碰 tkinter：_on_hotkey_trigger 只许入队
        idx = self.gui.find("def _on_hotkey_trigger")
        self.assertGreater(idx, 0)
        body = self.gui[idx:idx + 400]
        self.assertIn("put_nowait", body)
        self.assertNotIn("self.root.", body.split("def ", 2)[0])

    def test_hook_ignores_injected_keys(self):
        self.assertIn("LLKHF_INJECTED", self.trig)
        self.assertIn("info.flags & LLKHF_INJECTED", self.trig)

    def test_default_trigger_is_hotkey(self):
        self.assertIn('self.cfg.get("trigger_mode", TRIGGER_HOTKEY)', self.gui)

    def test_no_escape_used_to_collapse(self):
        # Esc 在有些程序里会关掉输入框，收选区必须用右方向键
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        k = open(os.path.join(root, "keys.py"), encoding="utf-8").read()
        idx = k.find("def collapse_selection")
        body = k[idx:idx + 400]
        self.assertIn("tap(VK_RIGHT)", body)
        self.assertNotIn("VK_ESCAPE", body)

    def test_extended_keys_have_extended_flag(self):
        # 导航键不带 KEYEVENTF_EXTENDEDKEY 会被当成小键盘键，
        # Shift+Home 就变成单纯把光标移到行首，整行转换静默失败。
        self.assertEqual(keys_mod.KEYEVENTF_EXTENDEDKEY, 0x0001)
        self.assertIn(keys_mod.VK_HOME, keys_mod.EXTENDED_VKS)
        self.assertIn(keys_mod.VK_LEFT, keys_mod.EXTENDED_VKS)
        self.assertIn(keys_mod.VK_RIGHT, keys_mod.EXTENDED_VKS)
        self.assertIn(keys_mod.VK_DELETE, keys_mod.EXTENDED_VKS)
        # 字母键不是扩展键，加了反而会让某些程序认不出
        self.assertNotIn(keys_mod.VK_A, keys_mod.EXTENDED_VKS)
        self.assertNotIn(keys_mod.VK_C, keys_mod.EXTENDED_VKS)
        self.assertNotIn(keys_mod.VK_V, keys_mod.EXTENDED_VKS)

    def test_extended_flag_actually_set(self):
        # 真的构造一个 INPUT 出来，确认标志位落在 dwFlags 上
        inp = keys_mod._key(keys_mod.VK_HOME, False)
        self.assertTrue(inp.u.ki.dwFlags & keys_mod.KEYEVENTF_EXTENDEDKEY)
        up = keys_mod._key(keys_mod.VK_HOME, True)
        self.assertTrue(up.u.ki.dwFlags & keys_mod.KEYEVENTF_EXTENDEDKEY)
        self.assertTrue(up.u.ki.dwFlags & keys_mod.KEYEVENTF_KEYUP)
        plain = keys_mod._key(keys_mod.VK_A, False)
        self.assertFalse(plain.u.ki.dwFlags & keys_mod.KEYEVENTF_EXTENDEDKEY)

    def test_focus_window_has_foreground_lock_fallback(self):
        # 光调 SetForegroundWindow 不够：Windows 有前台锁定，后台进程调它
        # 会被静默忽略（返回 False 不报错）。必须再走 AttachThreadInput 绕行，
        # 否则点「转换这一行」时切不回用户窗口，整行转换静默失效。
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        k = open(os.path.join(root, "keys.py"), encoding="utf-8").read()
        idx = k.find("def focus_window")
        self.assertGreater(idx, 0)
        body = k[idx:k.find("\ndef ", idx + 10)]
        self.assertIn("SetForegroundWindow", body)
        self.assertIn("AttachThreadInput", body)
        self.assertIn("BringWindowToTop", body)
        self.assertIn("IsIconic", body)
        # 必须校验调用后的真实前台，不能只看返回值
        self.assertIn("GetForegroundWindow() == hwnd", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
