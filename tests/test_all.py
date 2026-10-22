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
import hotkey as hotkey_mod
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


def _only(*vks):
    """返回一个 _down 替身：只有列出的虚拟键算按住。

    早期测试直接写 `lambda vk: True`，那意味着所有修饰键都按着 —— 快捷键
    改成「修饰键必须完全一致」之后这种替身就失真了，必须精确模拟。
    """
    pressed = set(vks)
    return lambda vk: vk in pressed


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
        self.w._down = _only(trigger.VK_CONTROL, trigger.VK_MENU)
        self.assertFalse(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, [])

    def test_hotkey_fires_and_is_swallowed(self):
        self.w.hotkey_mode = True
        self.w._down = _only(trigger.VK_CONTROL, trigger.VK_MENU)
        self.assertTrue(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, ["hotkey"])

    def test_hotkey_requires_modifiers(self):
        self.w.hotkey_mode = True
        self.w._down = lambda vk: False
        self.assertFalse(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, [])

    def test_hotkey_requires_exact_modifiers(self):
        # 多按一个 Shift 不该触发：绑的是 Ctrl+Alt+B，那 Ctrl+Alt+Shift+B 就不算。
        # 严格比较是为了可预期，否则用户永远不知道多按一个键算不算数。
        self.w.hotkey_mode = True
        self.w._down = _only(hotkey_mod.VK_CONTROL, hotkey_mod.VK_MENU, hotkey_mod.VK_SHIFT)
        self.assertFalse(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, [])

    def test_hotkey_follows_custom_binding(self):
        # 换成别的键之后，原来的 B 不该再触发
        self.w.hotkey = hotkey_mod.parse("ctrl+shift+k")
        self.w._down = _only(hotkey_mod.VK_CONTROL, hotkey_mod.VK_SHIFT)
        self.assertFalse(self.w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, [])
        self.assertTrue(self.w._on_key(0x4B))  # K
        self.assertEqual(self.fired, ["hotkey"])


class TestSpaceCount(unittest.TestCase):
    """连按几下空格才触发。玩游戏的人容易被空格误触，所以要能调。"""

    def _mk(self, n):
        self.fired = []
        w = trigger.KeyboardWatcher(lambda kind: self.fired.append(kind),
                                    space_mode=True, hotkey_mode=False,
                                    space_count=n)
        w._down = lambda vk: False
        return w

    def test_default_is_two(self):
        w = self._mk(None)
        self.assertEqual(w.space_count, trigger.SPACE_COUNT_DEFAULT)
        self.assertEqual(w.space_count, 2)

    def test_clamp_bad_values(self):
        # 配置被手改坏时静默回默认值，不能让功能失效
        for bad in (0, -1, 99, "x", None, 2.7):
            self.assertEqual(trigger.clamp_space_count(bad),
                             trigger.SPACE_COUNT_DEFAULT, bad)
        self.assertEqual(trigger.clamp_space_count(1), 1)
        self.assertEqual(trigger.clamp_space_count(5), 5)

    def test_one_press_fires_when_set_to_one(self):
        w = self._mk(1)
        self.assertTrue(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_three_presses_needed_when_set_to_three(self):
        w = self._mk(3)
        self.assertFalse(w._on_key(trigger.VK_SPACE))
        self.assertFalse(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, [])
        self.assertTrue(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_four_presses_needed_when_set_to_four(self):
        w = self._mk(4)
        for _ in range(3):
            self.assertFalse(w._on_key(trigger.VK_SPACE))
        self.assertTrue(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_streak_resets_after_fire(self):
        # 触发之后要重新数，不能第四下又立刻触发
        w = self._mk(3)
        w._on_key(trigger.VK_SPACE)
        w._on_key(trigger.VK_SPACE)
        self.assertTrue(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])
        self.assertFalse(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_other_key_resets_streak(self):
        w = self._mk(3)
        w._on_key(trigger.VK_SPACE)
        w._on_key(trigger.VK_SPACE)
        w._on_key(0x41)  # 打了个 A
        self.assertFalse(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, [])

    def test_too_slow_resets_streak(self):
        w = self._mk(3)
        w._on_key(trigger.VK_SPACE)
        w._last_space -= (trigger.DOUBLE_SPACE_MS + 100) / 1000.0
        self.assertFalse(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, [])


class TestHotkeyCapture(unittest.TestCase):
    """录制新快捷键：钩子进入 capture_mode 后只报组合，不触发转换。"""

    def setUp(self):
        self.fired = []
        self.captured = []
        self.w = trigger.KeyboardWatcher(
            lambda kind: self.fired.append(kind), on_capture=self.captured.append
        )

    def test_capture_reports_modifiers_plus_key(self):
        self.w.capture_mode = True
        self.w._down = _only(hotkey_mod.VK_CONTROL, hotkey_mod.VK_SHIFT)
        self.assertTrue(self.w._on_key(0x4B))  # Ctrl+Shift+K
        self.assertEqual(self.captured, ["ctrl+shift+k"])
        self.assertEqual(self.fired, [])

    def test_capture_ignores_modifier_alone(self):
        # 只按 Ctrl 不该立刻结束录制
        self.w.capture_mode = True
        self.w._down = _only(trigger.VK_CONTROL)
        self.assertFalse(self.w._on_key(trigger.VK_CONTROL))
        self.assertEqual(self.captured, [])

    def test_capture_esc_cancels(self):
        self.w.capture_mode = True
        self.w._down = lambda vk: False
        self.assertTrue(self.w._on_key(trigger.VK_ESCAPE))
        self.assertEqual(self.captured, [""])

    def test_capture_esc_with_modifier_is_a_valid_hotkey(self):
        # Ctrl+Esc 是合法的快捷键组合，不该被当成取消
        self.w.capture_mode = True
        self.w._down = _only(trigger.VK_CONTROL)
        self.assertTrue(self.w._on_key(trigger.VK_ESCAPE))
        self.assertEqual(self.captured, ["ctrl+esc"])

    def test_capture_mode_never_triggers_convert(self):
        # 录制期间按到跟当前快捷键一样的键，也只能报组合，不能触发转换
        self.w.capture_mode = True
        self.w._down = _only(trigger.VK_CONTROL, trigger.VK_MENU)
        self.w._on_key(trigger.VK_B)
        self.assertEqual(self.fired, [])
        self.assertEqual(self.captured, ["ctrl+alt+b"])

    def test_capture_swallows_key(self):
        # 吞掉，否则录快捷键时这个键会落进用户正在编辑的程序里
        self.w.capture_mode = True
        self.w._down = _only(trigger.VK_CONTROL)
        self.assertTrue(self.w._on_key(0x46))  # Ctrl+F


class TestHotkeySpec(unittest.TestCase):
    """快捷键的字符串表示：存储 / 显示 / 解析必须是同一套规则。"""

    def test_parse_and_normalize(self):
        # 顺序和大小写都该归一，否则同一个组合会有多种写法，比较起来容易出错
        for raw in ("ctrl+alt+b", "Ctrl + Alt + B", "alt+ctrl+b", "b+ctrl+alt"):
            h = hotkey_mod.parse(raw)
            self.assertIsNotNone(h, raw)
            self.assertEqual(h.spec(), "ctrl+alt+b", raw)
            self.assertEqual(h.text(), "Ctrl+Alt+B", raw)

    def test_parse_rejects_invalid(self):
        for raw in ("", None, "ctrl", "b+c", "ctrl+nosuchkey", "ctrl+alt", 123):
            self.assertIsNone(hotkey_mod.parse(raw), raw)

    def test_default_hotkey(self):
        self.assertEqual(hotkey_mod.DEFAULT_HOTKEY, "ctrl+alt+b")
        self.assertEqual(hotkey_mod.load(None).spec(), "ctrl+alt+b")
        # 配置被手改坏时不能抛异常，静默退回默认值
        self.assertEqual(hotkey_mod.load("garbage").spec(), "ctrl+alt+b")

    def test_text_uses_readable_names(self):
        self.assertEqual(hotkey_mod.parse("ctrl+shift+left").text(), "Ctrl+Shift+←")
        self.assertEqual(hotkey_mod.parse("win+space").text(), "Win+Space")
        self.assertEqual(hotkey_mod.parse("alt+f8").text(), "Alt+F8")

    def test_make_from_vk(self):
        self.assertEqual(hotkey_mod.make(["ctrl"], 0x42).spec(), "ctrl+b")
        self.assertEqual(hotkey_mod.make([], 0x20).spec(), "space")
        self.assertIsNone(hotkey_mod.make([], 0xFF))  # 不认识的键
        # 修饰键顺序无关
        self.assertEqual(hotkey_mod.make(["alt", "ctrl"], 0x42).spec(), "ctrl+alt+b")

    def test_key_vks_are_lowercase(self):
        # parse() 会把输入转小写再查表，表里的名字必须也是小写。
        # 曾经写成大写 "B"，结果 "ctrl+alt+b" 解析直接失败。
        for name in hotkey_mod.KEY_VKS:
            self.assertEqual(name, name.lower(), name)

    def test_letter_vks_match_ascii(self):
        self.assertEqual(hotkey_mod.KEY_VKS["a"], 0x41)
        self.assertEqual(hotkey_mod.KEY_VKS["z"], 0x5A)
        self.assertEqual(hotkey_mod.KEY_VKS["0"], 0x30)
        self.assertEqual(hotkey_mod.KEY_VKS["f1"], 0x70)
        self.assertEqual(hotkey_mod.KEY_VKS["f12"], 0x7B)


def root_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


    def test_capture_callback_only_enqueues(self):
        # 录制回调同样跑在钩子线程里，绝不能直接改 tkinter 控件。
        # 方法体要用 "\n    def " 切（带缩进），用 "\ndef " 会一路切到文件末尾，
        # 把后面正常的方法也当成它的一部分（这个断言自己踩过）。
        idx = self.gui.find("def _on_hotkey_captured")
        self.assertGreater(idx, 0)
        body = self.gui[idx:self.gui.find("\n    def ", idx + 10)]
        self.assertIn("put_nowait", body)
        self.assertNotIn(".configure(", body)
        self.assertNotIn(".set(", body)

    def test_hotkey_persisted_and_restored(self):
        # 快捷键要落盘，重开程序还在
        self.assertIn('self.cfg.get("hotkey", hotkey_mod.DEFAULT_HOTKEY)', self.gui)
        self.assertIn('self.cfg["hotkey"] = self.hotkey_spec', self.gui)

    def test_capture_mode_guards_before_trigger(self):
        # 录制期间必须先走录制分支，否则按到跟当前快捷键一样的键会触发转换
        idx = self.trig.find("def _on_key(")
        body = self.trig[idx:self.trig.find("\n    def ", idx + 10)]
        cap = body.find("capture_mode")
        fire = body.find('self.on_trigger("space")')
        self.assertGreater(cap, 0)
        self.assertGreater(fire, cap, "capture_mode 判断必须在触发之前")

    def test_watcher_hotkey_is_mutable(self):
        # 改快捷键不能重建钩子（重装可能被安全软件拦），要能就地改
        self.assertIn("self.hotkey = hotkey", self.trig)
        self.assertIn("self._watcher.hotkey = self.hotkey", self.gui)

    def test_tray_callback_only_enqueues(self):
        # 托盘回调跑在托盘线程里，和键盘钩子一样只能入队
        idx = self.gui.find("def _on_tray_event")
        self.assertGreater(idx, 0)
        body = self.gui[idx:self.gui.find("\n    def ", idx + 10)]
        self.assertIn("put_nowait", body)
        self.assertNotIn(".configure(", body)

    def test_minimize_keeps_hook(self):
        # 最小化到托盘只 withdraw 窗口，绝不能卸钩子——用户要的就是
        # 「看不见它但快捷键还能用」
        idx = self.gui.find("def _minimize_to_tray")
        self.assertGreater(idx, 0)
        body = self.gui[idx:self.gui.find("\n    def ", idx + 10)]
        self.assertIn("withdraw", body)
        self.assertNotIn("_release_watcher", body)

    def test_tray_uses_shell_notifyicon(self):
        # 托盘走 Shell_NotifyIconW，气泡通知才有 NIF_INFO 那条路
        tr = open(os.path.join(root_dir(), "tray.py"), encoding="utf-8").read()
        self.assertIn("Shell_NotifyIconW", tr)
        self.assertIn("NIF_INFO", tr)
        self.assertIn("NIM_DELETE", tr)

    def test_space_count_persisted(self):
        self.assertIn('self.cfg.get("space_count"', self.gui)
        self.assertIn('self.cfg["space_count"] = n', self.gui)

    def test_no_hardcoded_hotkey_in_status_text(self):
        # 状态提示里的快捷键文案要跟着用户设置走，不能写死 Ctrl+Alt+B
        self.assertNotIn("请用 Ctrl+Alt+B", self.gui)

    def test_notify_text_follows_trigger_mode(self):
        # 用户实测踩过：空格模式下收进托盘，通知却写「Ctrl+Alt+B 依然可用」，
        # 按了根本没反应。通知和托盘提示都必须走 _trigger_text() 现算。
        self.assertIn("_trigger_text", self.gui)
        # 收进托盘那条通知里不能再直接拼 hotkey.text()
        idx = self.gui.find("def _minimize_to_tray")
        self.assertGreater(idx, 0)
        body = self.gui[idx:self.gui.find("\n    def ", idx + 10)]
        self.assertIn("self._trigger_text()", body)
        self.assertNotIn("self.hotkey.text()", body)

    def test_trigger_text_covers_all_modes(self):
        # 三种模式都要有说法，off 模式返回空串（通知里换成另一句话）
        idx = self.gui.find("def _trigger_text")
        self.assertGreater(idx, 0)
        body = self.gui[idx:self.gui.find("\n    def ", idx + 10)]
        self.assertIn("TRIGGER_SPACE", body)
        self.assertIn("TRIGGER_HOTKEY", body)
        self.assertIn("self.space_count", body)
        self.assertIn("self.hotkey.text()", body)

    def test_tooltip_refreshed_on_mode_change(self):
        # 托盘 tooltip 在 _apply_trigger_mode 里刷新，这样开机恢复配置、
        # 程序内部改模式都覆盖到，不会漏
        idx = self.gui.find("def _apply_trigger_mode")
        self.assertGreater(idx, 0)
        body = self.gui[idx:self.gui.find("\n    def ", idx + 10)]
        self.assertIn("self._refresh_tray_tooltip()", body)

if __name__ == "__main__":
    unittest.main(verbosity=2)
