# -*- coding: utf-8 -*-
"""PC 端回归测试。跑法：python tests/test_all.py

不依赖 tkinter（GUI 只做静态检查），所以能在任何 Python 3.8+ 上跑。
"""
import json
import os
import shutil
import sys
import tempfile
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
        # 超过 MAX_BINARY_CHARS 才判否。这个上限纯粹是防超大文本卡界面用的，
        # 不是判定规则：早先定 20000，结果 750 个汉字转出来的位串就有 20249 字符，
        # 再按一次快捷键时认不出这是二进制，「按两次转回来」在长文本上直接失效。
        self.assertTrue(core.looks_like_binary("01" * 20000))
        self.assertFalse(core.looks_like_binary("01" * (core.MAX_BINARY_CHARS // 2 + 1)))

    def test_roundtrip_long_text_still_detected(self):
        # 回归：长文本转成二进制后必须还能被 auto_detect 认成二进制
        src = "今天天气不错我们出去走走吧" * 60  # 780 字符
        bits = core.text_to_binary(src, sep=" ")
        self.assertGreater(len(bits), 20000)
        self.assertTrue(core.looks_like_binary(bits))
        self.assertEqual(core.auto_detect(bits), "bin2text")
        self.assertEqual(core.binary_to_text(bits)[0], src)

    def test_latin1_fallback_is_flagged(self):
        # latin-1 永远不会解码失败，任何字节都能「成功」转出字符串。
        # 这时必须明说结果可能不可信，否则用户看到乱码而界面报「已解码 N 字节」。
        text, msg = core.binary_to_text("11111111 11111111")
        self.assertIsNotNone(text)
        self.assertIn("乱码", msg)

    def test_control_chars_are_escaped(self):
        # NUL 这类字符能进字符串但显示出来什么都没有，要转成可见的 \x00
        text, _msg = core.binary_to_text("00000000 01001000 01101001")
        self.assertIsNotNone(text)
        self.assertIn("\\x00", text)
        self.assertIn("Hi", text)

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


class TestCodecs(unittest.TestCase):
    """十六进制 / Base64 / 摩斯码三种新编码，以及分隔符预设。"""

    # ---- 十六进制 ----
    def test_hex_round_trip(self):
        for text in ("hi", "你好Hi", "abc 123"):
            enc = core.encode_as(text, "hex")
            back, msg = core.decode_as(enc, "hex")
            self.assertEqual(back, text, "hex 往返失败: %r -> %r (%s)" % (text, back, msg))

    def test_hex_empty_stays_empty(self):
        # 空文本编出来是空串，解空串返回 None 是合理的（提示「内容是空的」）
        self.assertEqual(core.encode_as("", "hex"), "")
        self.assertIsNone(core.decode_as("", "hex")[0])

    def test_hex_uppercase_two_digits(self):
        self.assertEqual(core.text_to_hex("hi"), "68 69")

    def test_hex_accepts_prefix_and_separators(self):
        # 0x 前缀、冒号、短横线、下划线、逗号都该能吃
        for s in ("48 69", "0x48 0x69", "48:69", "48-69", "48_69", "48,69"):
            self.assertEqual(core.hex_to_text(s)[0], "Hi", "解析不了: %r" % s)

    def test_hex_rejects_odd_length(self):
        text, msg = core.hex_to_text("486")
        self.assertIsNone(text)
        self.assertIn("奇数", msg)

    def test_hex_rejects_bad_char(self):
        text, msg = core.hex_to_text("48 6G")
        self.assertIsNone(text)
        self.assertIn("非十六进制", msg)

    def test_hex_rejects_empty(self):
        self.assertIsNone(core.hex_to_text("")[0])
        self.assertIsNone(core.hex_to_text("   ")[0])

    def test_looks_like_hex(self):
        self.assertTrue(core.looks_like_hex("48 69"))
        self.assertTrue(core.looks_like_hex("0x48 0x69"))
        self.assertTrue(core.looks_like_hex("48:69:6a"))
        # 这些不该被判成十六进制
        self.assertFalse(core.looks_like_hex("2024"))
        self.assertFalse(core.looks_like_hex("hello"))
        self.assertFalse(core.looks_like_hex("4869"))
        # `deadbeef` 字面上是合法的 `de ad be ef`，但解出来是 4 个非法
        # UTF-8 字节。只查字符集的话用户随手复制个英文单词（DeadBeef、
        # A1B2C3D4、build2024x）就会跳一屏乱码，所以加了一道「解出来
        # 得像给人看的文字」的关，它过不了。
        self.assertFalse(core.looks_like_hex("deadbeef"))

    # ---- Base64 ----
    def test_base64_round_trip(self):
        for text in ("hi", "你好Hi", "abc 123"):
            enc = core.encode_as(text, "base64")
            back, msg = core.decode_as(enc, "base64")
            self.assertEqual(back, text, "base64 往返失败: %r (%s)" % (back, msg))

    def test_base64_known_value(self):
        self.assertEqual(core.text_to_base64("Hello"), "SGVsbG8=")

    def test_base64_urlsafe_accepted(self):
        # URL-safe 变体用 - 和 _，得能认出来
        self.assertEqual(core.base64_to_text("5L2g5aW9")[0], "你好")

    def test_base64_missing_padding(self):
        # 少了末尾的 = 也该能解（用户手抄时经常会漏）
        self.assertEqual(core.base64_to_text("SGVsbG8")[0], "Hello")

    def test_base64_rejects_plain_words(self):
        self.assertFalse(core.looks_like_base64("hello"))
        self.assertFalse(core.looks_like_base64("helloworld"))
        self.assertFalse(core.looks_like_base64("hello world"))

    def test_base64_rejects_hex_prefix(self):
        # 「0x48 0x69」去分隔符后每个字符都在 Base64 字符集里，别误判
        self.assertFalse(core.looks_like_base64("0x48 0x69"))

    def test_base64_accepts_real_ones(self):
        self.assertTrue(core.looks_like_base64("SGVsbG8="))
        self.assertTrue(core.looks_like_base64("5L2g5aW9SGk="))

    # ---- 摩斯码 ----
    def test_morse_round_trip_ascii(self):
        enc = core.encode_as("SOS hi", "morse")
        self.assertEqual(enc, "... --- ... / .... ..")
        back, msg = core.decode_as(enc, "morse")
        self.assertEqual(back, "SOS HI", "摩斯往返失败 (%s)" % msg)

    def test_morse_chinese_becomes_placeholder(self):
        # 中文没有摩斯码，编成 ? 占位，解码回到 ?，用户能看出丢了哪个字
        enc = core.encode_as("你好Hi", "morse")
        self.assertIn("?", enc)
        back, _msg = core.decode_as(enc, "morse")
        self.assertEqual(back, "??HI")

    def test_morse_accepts_unicode_dots_and_dashes(self):
        # 用户可能用「·」和「—」手打摩斯码
        self.assertEqual(core.morse_to_text("··· ––– ···")[0], "SOS")

    def test_morse_uppercases(self):
        # 摩斯本身不分大小写，解出来一律大写
        self.assertEqual(core.morse_to_text(".... . .-.. .-.. ---")[0], "HELLO")

    def test_morse_rejects_bad_char(self):
        text, msg = core.morse_to_text("... xyz")
        self.assertIsNone(text)
        self.assertIn("非摩斯码", msg)

    def test_morse_rejects_empty(self):
        self.assertIsNone(core.morse_to_text("")[0])

    def test_looks_like_morse(self):
        self.assertTrue(core.looks_like_morse(".... .."))
        self.assertTrue(core.looks_like_morse("... --- ..."))
        self.assertTrue(core.looks_like_morse("? .... .."))
        self.assertFalse(core.looks_like_morse("-"))
        self.assertFalse(core.looks_like_morse("hello"))

    # ---- 分隔符预设 ----
    def test_separator_presets(self):
        self.assertEqual(core.encode_as("hi", "binary", "space"), "01101000 01101001")
        self.assertEqual(core.encode_as("hi", "binary", "comma"), "01101000, 01101001")
        self.assertEqual(core.encode_as("hi", "binary", "newline"), "01101000\n01101001")
        self.assertEqual(core.encode_as("hi", "binary", "none"), "0110100001101001")
        self.assertEqual(core.encode_as("hi", "binary", "0b"), "0b01101000 0b01101001")

    def test_separator_round_trip(self):
        # 每种分隔符编出来的串都得能解回去
        for sep in core.SEPARATORS:
            enc = core.encode_as("hi", "binary", sep)
            back, msg = core.decode_as(enc, "binary")
            self.assertEqual(back, "hi", "分隔符 %s 往返失败 (%s)" % (sep, msg))

    def test_separator_fallback(self):
        # 配置里写了个不认识的名字，退回空格而不是崩
        self.assertEqual(core.separator_for("nonsense"), " ")
        self.assertEqual(core.encode_as("hi", "binary", "nonsense"), "01101000 01101001")

    def test_separator_registry_complete(self):
        # 每个分隔符都要有显示名，界面上才能画出来
        for name in core.SEPARATORS:
            self.assertIn(name, core.SEPARATOR_TEXT)

    def test_format_registry_complete(self):
        for name in core.FORMATS:
            self.assertIn(name, core.FORMAT_TEXT)

    # ---- 格式识别 ----
    def test_detect_format(self):
        self.assertEqual(core.detect_format("01001000 01101001"), "binary")
        self.assertEqual(core.detect_format("48 69"), "hex")
        self.assertEqual(core.detect_format("0x48 0x69"), "hex")
        self.assertEqual(core.detect_format(core.text_to_hex("Hello")), "hex")
        self.assertEqual(core.detect_format("SGVsbG8="), "base64")
        self.assertEqual(core.detect_format(".... .."), "morse")
        # 普通文字认不出格式
        self.assertEqual(core.detect_format("hello world"), "")
        self.assertEqual(core.detect_format("2024"), "")
        self.assertEqual(core.detect_format(""), "")
        # `deadbeef` 也是 `de ad be ef` 的写法，但它解出来不是 UTF-8，
        # 而且更像人随手打的英文，宁可认不出也不跳一屏乱码
        self.assertEqual(core.detect_format("deadbeef"), "")

    def test_detect_format_rejects_lookalike_words(self):
        """普通英文单词不能被当成 Base64 / 十六进制。

        这是真踩过的坑：`HelloWorld`、`password123`、`Supercalifragilistic`
        全在 Base64 的字符集里，`DeadBeef`、`A1B2C3D4`、`2024-01-01` 全是
        合法的十六进制字符。早先只查字符集，用户随手复制个单词就跳出一屏
        乱码。现在多一道「真解一遍，解出来得像给人看的文字」的关。
        """
        for word in ("HelloWorld", "Congratulations", "abcdefgh",
                     "Supercalifragilistic", "test1234", "DeadBeef",
                     "12345678901234567890", "A1B2C3D4", "ThisIsATest",
                     "password123", "ABCD1234EFGH5678", "order20240115",
                     "Cancel123", "build2024x", "2024-01-01", "12:30:45",
                     "1,234,567", "AA:BB:CC:DD", "ab-cd-ef", "Mr. Smith"):
            self.assertEqual(core.detect_format(word), "",
                             "误判成编码了: %r" % word)

    def test_detect_format_short_base64_with_padding(self):
        """带 = 填充的短 Base64 要认得出来，包括只有一个字符的。

        `YQ==` 是字母 a 的 Base64。尾部那个 = 是普通英文单词不会有的，
        所以它比长度门槛更可靠。
        """
        self.assertEqual(core.detect_format("YQ=="), "base64")
        self.assertEqual(core.decode_as("YQ==", "base64")[0], "a")
        self.assertEqual(core.detect_format("SGk="), "base64")
        self.assertEqual(core.detect_format(core.text_to_base64("Hi")), "base64")

    def test_all_formats_roundtrip_through_detect(self):
        """四种格式编码后都要能被认出并解回原文。

        摩斯码只测大写：国际码表没有小写字母码，`text_to_morse` 会先转大写，
        所以 `Hi` 进、`HI` 出——这是码表本身的限制，不是 bug。
        摩斯也不测中文：码表里没有中文码，只能编成 ? 占位，
        那条路径由 `test_morse_chinese_placeholder_roundtrip` 单独覆盖。
        """
        for fmt in core.FORMATS:
            seps = core.SEPARATORS if fmt == "binary" else ("space",)
            texts = ("HI", "HELLO WORLD", "TEST 123") if fmt == "morse" \
                else ("Hi", "hello world", "test 123")
            for sep in seps:
                for txt in texts:
                    enc = core.encode_as(txt, fmt, sep)
                    self.assertEqual(core.detect_format(enc), fmt,
                                     "%s/%s 没认出来: %r" % (fmt, sep, enc))
                    got = core.decode_as(enc, fmt)[0]
                    self.assertEqual(got, txt, "%s/%s 往返失败" % (fmt, sep))

    def test_morse_chinese_placeholder_roundtrip(self):
        """中文编成摩斯是 ? 占位，但这个结果必须还能被认出是摩斯码。

        「你好」→ `? ?`，一个点划都没有。早先 looks_like_morse 只数点划，
        这种串认不出来，用户转过去就再也转不回来了。
        连单个汉字都要能回来：「你」→ `?` 也认。
        """
        enc = core.text_to_morse("你好")
        self.assertEqual(enc, "? ?")
        self.assertEqual(core.detect_format(enc), "morse")
        got, msg = core.morse_to_text(enc)
        self.assertEqual(got, "??")
        self.assertIn("码表里没有", msg)
        # 单个汉字也不能漏
        self.assertEqual(core.text_to_morse("你"), "?")
        self.assertEqual(core.detect_format("?"), "morse")
        self.assertEqual(core.decode_as("?", "morse")[0], "?")

    def test_morse_rejects_repeated_symbols(self):
        """日常输入里的 `...` `---` 不能被当成摩斯码。

        `...` 解出来是 SSS，`---` 解出来是 O。用户随手打个省略号或者
        一条分隔线，剪切板里就跳出一个「解码结果」，很闹。
        """
        for junk in ("...", "---", "....", "----", "..", "--"):
            self.assertEqual(core.detect_format(junk), "",
                             "误判成摩斯码了: %r" % junk)
        # 带空格或混合的就还是摩斯（这些是正常的码）
        self.assertEqual(core.detect_format(". . ."), "morse")
        self.assertEqual(core.detect_format(".... .."), "morse")

    def test_auto_detect_covers_all_formats(self):
        for enc in ("01001000 01101001", "48 69", "SGVsbG8=", ".... .."):
            self.assertEqual(core.auto_detect(enc), "bin2text", "没认出来: %r" % enc)
        self.assertEqual(core.auto_detect("hello"), "text2bin")
        self.assertEqual(core.auto_detect(""), "unknown")


def _only(*vks):
    """返回一个 _down 替身：只有列出的虚拟键算按住。

    早期测试直接写 `lambda vk: True`，那意味着所有修饰键都按着 —— 快捷键
    改成「修饰键必须完全一致」之后这种替身就失真了，必须精确模拟。
    """
    pressed = set(vks)
    return lambda vk: vk in pressed


class TestTriggerLogic(unittest.TestCase):
    """空格连按 / 快捷键的状态机。直接调 _on_key，不需要真的装钩子。

    注意：_on_key 现在带自动重复去抖——同一个键没松开之前再收到 KEYDOWN
    会被忽略（按住不放时系统会一直发）。所以这里每次按完都补一次 _release，
    模拟真实的「按下-松开」。
    """

    def setUp(self):
        self.fired = []
        self.w = trigger.KeyboardWatcher(lambda kind: self.fired.append(kind))

    def _space(self):
        r = self.w._on_key(trigger.VK_SPACE)
        self.w._release(trigger.VK_SPACE)
        return r

    def _press(self, vk):
        r = self.w._on_key(vk)
        self.w._release(vk)
        return r

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

    @staticmethod
    def _press(w, vk):
        """按一下再松开。_on_key 有自动重复去抖，不松开的话第二下会被忽略。"""
        r = w._on_key(vk)
        w._release(vk)
        return r

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
        self.assertTrue(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_three_presses_needed_when_set_to_three(self):
        w = self._mk(3)
        self.assertFalse(self._press(w, trigger.VK_SPACE))
        self.assertFalse(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, [])
        self.assertTrue(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_four_presses_needed_when_set_to_four(self):
        w = self._mk(4)
        for _ in range(3):
            self.assertFalse(self._press(w, trigger.VK_SPACE))
        self.assertTrue(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_streak_resets_after_fire(self):
        # 触发之后要重新数，不能第四下又立刻触发
        w = self._mk(3)
        self._press(w, trigger.VK_SPACE)
        self._press(w, trigger.VK_SPACE)
        self.assertTrue(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])
        self.assertFalse(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_other_key_resets_streak(self):
        w = self._mk(3)
        self._press(w, trigger.VK_SPACE)
        self._press(w, trigger.VK_SPACE)
        self._press(w, 0x41)  # 打了个 A
        self.assertFalse(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, [])

    def test_too_slow_resets_streak(self):
        w = self._mk(3)
        self._press(w, trigger.VK_SPACE)
        w._last_space -= (trigger.DOUBLE_SPACE_MS + 100) / 1000.0
        self.assertFalse(self._press(w, trigger.VK_SPACE))
        self.assertEqual(self.fired, [])

    # --- 自动重复去抖（P1-2）---
    def test_held_space_does_not_fire(self):
        # 按住空格不放，系统会一直发 KEYDOWN。真实的「第二下」必须发生在
        # 松开之后，否则按住空格就会直接触发转换。
        w = self._mk(2)
        self.assertFalse(w._on_key(trigger.VK_SPACE))
        for _ in range(20):
            self.assertFalse(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, [])

    def test_release_allows_next_press(self):
        w = self._mk(2)
        self.assertFalse(w._on_key(trigger.VK_SPACE))
        w._release(trigger.VK_SPACE)
        self.assertTrue(w._on_key(trigger.VK_SPACE))
        self.assertEqual(self.fired, ["space"])

    def test_held_hotkey_fires_once(self):
        # 按住 Ctrl+Alt+B 不放，自动重复不该连着触发十几次
        self.fired = []
        w = trigger.KeyboardWatcher(lambda kind: self.fired.append(kind),
                                    space_mode=False, hotkey_mode=True)
        w._down = _only(hotkey_mod.VK_CONTROL, hotkey_mod.VK_MENU)
        self.assertTrue(w._on_key(trigger.VK_B))
        for _ in range(10):
            self.assertFalse(w._on_key(trigger.VK_B))
        self.assertEqual(self.fired, ["hotkey"])


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

    def test_conflict_warning(self):
        # 和系统抢的组合要能报出来：Ctrl+Alt+Del 按下去直接锁屏，
        # 用户以为设成功了，实际每次用都在锁屏。
        for spec in ("ctrl+alt+delete", "win+l", "alt+tab", "alt+f4",
                     "ctrl+shift+esc"):
            self.assertTrue(
                hotkey_mod.conflict_warning(hotkey_mod.load(spec)), spec)

    def test_conflict_warning_clean(self):
        # 常用的自定义组合不该被误报，否则警告等于噪音
        for spec in ("ctrl+alt+b", "ctrl+shift+k", "ctrl+space"):
            self.assertEqual(
                hotkey_mod.conflict_warning(hotkey_mod.load(spec)), "", spec)

    def test_conflict_warning_none(self):
        self.assertEqual(hotkey_mod.conflict_warning(None), "")
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

    def test_grab_line_uses_ctrl_shift_home(self):
        # Shift+Home 只选到视觉行首。文字转成二进制后长度膨胀约 9 倍，
        # 必然自动换行，Shift+Home 只抓到末尾一小截，反解必然是乱码——
        # 「转过去再转回来」这条路会彻底失效。必须用 Ctrl+Shift+Home。
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        k = open(os.path.join(root, "keys.py"), encoding="utf-8").read()
        idx = k.find("def grab_line")
        self.assertGreater(idx, 0)
        body = k[idx:k.find("\ndef ", idx + 10)]
        self.assertIn("tap(VK_HOME, (VK_CONTROL, VK_SHIFT))", body)
        self.assertNotIn("tap(VK_HOME, (VK_SHIFT,))", body)
        # 复制完要按换行切出逻辑行，再把选区收到行首
        self.assertIn('got.rfind("\\n")', body)
        # 收回的步数要按「光标步数」算，不能按字符数：CRLF 一个换行是 2 个字符
        # 但光标跨过去只算 1 步。按字符数会多走几步，把行首后面的字符也选进去。
        self.assertIn('got[:cut].count("\\r\\n")', body)
        self.assertIn("tap_many(VK_RIGHT, (VK_SHIFT,), moves)", body)
        # 光标在文档开头时复制不到东西是正常结果，不能重试时先按右方向键
        # （那会把光标推后一格，第二次就抓到第一个字符，把文档搞乱）
        self.assertNotIn("tap(VK_RIGHT)\n", body.split("for attempt")[1]
                         if "for attempt" in body else "")

    def test_grab_line_retries_on_empty_copy(self):
        # 选区要靠目标程序自己处理，程序正忙时 Ctrl+C 可能早于选区建立，
        # 复制到空的。实测偶发率约五分之一，不重试就静默返回空行。
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        k = open(os.path.join(root, "keys.py"), encoding="utf-8").read()
        idx = k.find("def grab_line")
        body = k[idx:k.find("\ndef ", idx + 10)]
        self.assertIn("attempts", body)
        self.assertIn("for attempt in range", body)
        self.assertIn("if got:", body)

    def test_tap_many_batches_send_input(self):
        # 往左收 350 个字符就是 350 次 SendInput 系统调用，太慢且容易丢；
        # 必须一次发一整批。chunk 分批是为了不超 SendInput 的容量上限。
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        k = open(os.path.join(root, "keys.py"), encoding="utf-8").read()
        idx = k.find("def tap_many")
        self.assertGreater(idx, 0)
        body = k[idx:k.find("\ndef ", idx + 10)]
        self.assertIn("chunk", body)
        self.assertIn("_send(seq)", body)
        # 一组里修饰键要先按下、后松开
        self.assertIn("reversed(mods)", body)

    def test_tap_many_builds_expected_sequence(self):
        # 真构造一批出来数一数：n 次 vk + m 个修饰键 = n*(1+m)*2 个事件
        sent = []
        orig = keys_mod._send
        try:
            keys_mod._send = lambda seq: (sent.append(list(seq)), True)[1]
            keys_mod.tap_many(keys_mod.VK_RIGHT, (keys_mod.VK_SHIFT,), 5)
        finally:
            keys_mod._send = orig
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0]), 5 * (1 + 1) * 2)
        # 每个动作都是 shift 下 → right 下 → right 上 → shift 上
        self.assertEqual(sent[0][0].u.ki.wVk, keys_mod.VK_SHIFT)
        self.assertEqual(sent[0][1].u.ki.wVk, keys_mod.VK_RIGHT)
        self.assertEqual(sent[0][2].u.ki.wVk, keys_mod.VK_RIGHT)
        self.assertTrue(sent[0][2].u.ki.dwFlags & keys_mod.KEYEVENTF_KEYUP)
        self.assertEqual(sent[0][3].u.ki.wVk, keys_mod.VK_SHIFT)
        self.assertTrue(sent[0][3].u.ki.dwFlags & keys_mod.KEYEVENTF_KEYUP)

    def test_tap_many_splits_into_chunks(self):
        # 超大批量要分批发，别一次塞给 SendInput
        sent = []
        orig = keys_mod._send
        try:
            keys_mod._send = lambda seq: (sent.append(len(seq)), True)[1]
            keys_mod.tap_many(keys_mod.VK_RIGHT, (), 10, chunk=4)
        finally:
            keys_mod._send = orig
        self.assertEqual(len(sent), 3)   # 4 + 4 + 2
        self.assertEqual(sent, [8, 8, 4])

    def test_tap_many_zero_is_noop(self):
        sent = []
        orig = keys_mod._send
        try:
            keys_mod._send = lambda seq: (sent.append(len(seq)), True)[1]
            self.assertTrue(keys_mod.tap_many(keys_mod.VK_RIGHT, (), 0))
        finally:
            keys_mod._send = orig
        self.assertEqual(sent, [])



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

    def test_tooltip_goes_through_set_tooltip(self):
        # 只改 self._tray.tooltip 属性没用：NIM_ADD 时系统已经拿走一份副本，
        # 后面改属性不会同步过去，用户悬停看到的永远是启动时那句话。
        # 必须调 set_tooltip，让它发一次 NIM_MODIFY。
        idx = self.gui.find("def _refresh_tray_tooltip")
        self.assertGreater(idx, 0)
        body = self.gui[idx:self.gui.find("\n    def ", idx + 10)]
        self.assertIn("self._tray.set_tooltip(", body)
        self.assertNotIn("self._tray.tooltip =", body)

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        t = open(os.path.join(root, "tray.py"), encoding="utf-8").read()
        self.assertIn("def set_tooltip(", t)
        self.assertIn("NIM_MODIFY", t)
        self.assertIn("WM_TRAY_TIP", t)
        # NIM_MODIFY 那一份必须只带 NIF_TIP，别把气泡通知的字段也塞进去
        idx = t.find("def _update_tip")
        self.assertGreater(idx, 0)
        body = t[idx:t.find("\n    def ", idx + 10)]
        self.assertIn("nid.uFlags = NIF_TIP", body)

    def test_single_instance_uses_wakeup_event(self):
        # 收进托盘走的是 withdraw()，那种窗口 ShowWindow(SW_RESTORE) 叫不回来，
        # 而且主进程内部的 _in_tray 不会跟着改。改用命名事件通知主实例自己恢复。
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        m = open(os.path.join(root, "main.py"), encoding="utf-8").read()
        self.assertIn("wakeup.request_show()", m)
        self.assertNotIn("ShowWindow(hwnd, 9)", m)

        w = open(os.path.join(root, "wakeup.py"), encoding="utf-8").read()
        self.assertIn("CreateEventW", w)
        self.assertIn("OpenEventW", w)
        self.assertIn("SetEvent", w)
        self.assertIn("ResetEvent", w)
        self.assertIn("WaitForSingleObject", w)

        g = open(os.path.join(root, "gui.py"), encoding="utf-8").read()
        self.assertIn("wakeup.Waker()", g)
        self.assertIn("self._waker.poll()", g)
        self.assertIn("self._stop_waker()", g)


if __name__ == "__main__":
    unittest.main(verbosity=2)
