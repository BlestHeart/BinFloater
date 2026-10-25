# -*- coding: utf-8 -*-
"""二进制/文本互转核心逻辑（纯标准库，方便单测与打包）。

除了二进制，还提供十六进制 / Base64 / 摩斯码三种编码。三种都做成
「文本 <-> 字符串」的对称函数，界面上的转换方向逻辑因此不用改。
"""

import base64
import binascii
import re
from typing import Optional, Tuple

# 可识别的二进制文本形态：
#   01001000 01101001 / 0100100001101001 / 0b01001000_0b01101001 / 0100-1000 ...
_BIN_CHARS = set("01")

# 「像二进制」的长度上限，纯粹是防超大文本卡界面用的，不是判定规则。
# 早先定的是 20000，但文字转二进制后长度涨约 25 倍（UTF-8 每字节 8 位 + 分隔空格），
# 750 个汉字转出来就是 20249 字符，直接超出上限 → 再按一次快捷键时它不认得这是二进制，
# 又编码了一遍，「按两次转回来」在长文本上彻底失效。放到 100 万字符后，
# 约 4 万个汉字的位串仍能正确识别；而 27 万字符的位串实测解码只要 24ms，不会卡。
MAX_BINARY_CHARS = 1000000


def _clean_bits(text: str) -> str:
    """从一段“疑似二进制”的字符串里提取纯 0/1 位串。

    先去掉分隔符，再去掉 0b 前缀（顺序反了 '010_0b01' 这种会残留 b）。
    """
    t = text.strip()
    t = re.sub(r"[\s,;:_\-|/.\\]+", "", t)
    t = re.sub(r"(?i)0b", "", t)
    return t


def looks_like_binary(text: str, min_len: int = 8) -> bool:
    """判断一段文本是否“像二进制”：只由 0/1 及分隔符组成，且位数 >= min_len。"""
    if not text or not text.strip():
        return False
    raw = text.strip()
    if len(raw) > MAX_BINARY_CHARS:  # 防止超大文本卡界面
        return False
    bits = _clean_bits(raw)
    if len(bits) < min_len:
        return False
    if not set(bits) <= _BIN_CHARS:
        return False
    # 排除“纯数字像日期/普通数”的极端情况：至少要包含 0 和 1 两者，或长度足够长
    if len(bits) < 16 and not ("0" in bits and "1" in bits):
        return False
    return True


def bits_to_bytes(bits: str) -> bytes:
    """把纯位串按 8 位一组转字节；末尾不足 8 位时在**右边**补 0。

    补在右边（低位）而不是左边：不完整的那个字节是数据的高位部分，
    补 0 到低位才是它本来的值。
    """
    if not bits:
        return b""
    pad = (-len(bits)) % 8
    if pad:
        bits = bits + "0" * pad
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def bytes_to_text(data: bytes) -> str:
    """字节转文本，依次尝试 utf-8 / gbk / big5 / latin-1。"""
    return bytes_to_text_ex(data)[0]


def bytes_to_text_ex(data: bytes) -> Tuple[str, str]:
    """字节转文本，同时返回实际用的是哪种编码。

    单独返回编码是给界面用的：latin-1 永远不会解码失败，任何字节都能「成功」
    转出一个字符串，于是一段乱七八糟的字节会安静地变成乱码，界面却报「已解码
    N 字节」。调用方拿到 'latin-1' 就该提醒用户结果可能不可信。
    """
    for enc in ("utf-8", "gbk", "big5", "latin-1"):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace"), "utf-8/replace"


def escape_controls(text: str) -> str:
    """把不可见控制字符转成可见的 \\xNN，免得界面上看着是空的。

    NUL 这类字符能进字符串，但显示出来什么都没有，用户会以为解码失败了。
    """
    if not text:
        return text
    out = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 and ch not in "\t\n\r":
            out.append("\\x%02x" % code)
        elif code == 0x7F:
            out.append("\\x7f")
        else:
            out.append(ch)
    return "".join(out)


def binary_to_text(text: str) -> Tuple[Optional[str], str]:
    """二进制字符串 -> 文本。返回 (结果, 提示信息)；失败时结果为 None。"""
    if not text or not text.strip():
        return None, "剪切板是空的"
    bits = _clean_bits(text)
    if not bits:
        return None, "没有可识别的 0/1 位串"
    if not set(bits) <= _BIN_CHARS:
        bad = next(ch for ch in bits if ch not in _BIN_CHARS)
        return None, f"包含非二进制字符：{bad!r}"
    n = len(bits)
    if n < 8:
        return None, f"位数不足 8 位（{n} 位），无法组成一个字节"
    rest = n % 8
    data = bits_to_bytes(bits)
    raw, enc = bytes_to_text_ex(data)
    # latin-1 兜底永远不会失败，任何字节都能「解码成功」。这时候必须说清楚
    # 结果可能不可信，否则用户看到乱码而界面报「已解码 N 字节」会一头雾水。
    dubious = enc == "latin-1" and any(b >= 0x80 for b in data)
    result = escape_controls(raw)
    if rest:
        msg = f"位数 {n} 不是 8 的倍数，末尾 {rest} 位已补 0"
    else:
        msg = f"已解码 {n // 8} 字节 / {n} 位"
    if dubious:
        msg += " · 含非 UTF-8 字节，按 latin-1 显示，可能是乱码"
    return result, msg


def text_to_binary(text: str, group: int = 8, sep: str = " ") -> str:
    """文本 -> 二进制位串，按 group 位分组、用 sep 连接。

    sep 可以是空格、逗号、换行、空串（紧凑），或者 "0b "（每段加 0b 前缀，
    由调用方自己拼）。分组大小 group 也能改，比如 4 位一组读起来更清楚。
    """
    if text == "":
        return ""
    return bytes_to_binary(text.encode("utf-8"), group, sep)


def binary_to_hex(text: str) -> str:
    bits = _clean_bits(text)
    data = bits_to_bytes(bits)
    return " ".join(f"{b:02X}" for b in data)


def bytes_to_binary(data: bytes, group: int = 8, sep: str = " ") -> str:
    """字节 -> 二进制位串。用于把任意文件（图片等）编成 0/1 文本。"""
    if not data:
        return ""
    bits = "".join(f"{b:08b}" for b in data)
    group = max(1, int(group))
    chunks = [bits[i : i + group] for i in range(0, len(bits), group)]
    return sep.join(chunks)


# ---------------------------------------------------------------------------
# 十六进制
# ---------------------------------------------------------------------------
_HEX_CHARS = set("0123456789abcdefABCDEF")


def _decode_is_text(data: bytes) -> bool:
    """解出来的字节像不像「给人看的文字」。

    判「这段是不是 Base64 / 十六进制」光看字符集远远不够：`HelloWorld`、
    `password123`、`Supercalifragilistic` 这些普通英文单词全都落在 Base64
    的字符集里；十六进制更宽松，`DeadBeef`、`A1B2C3D4`、`12345678901234567890`
    全都合法。光看字符集就会把用户随手复制的单词、订单号当成编码解出一堆乱码。

    真正的判据在**解码结果**上：本工具的编码器一律用 UTF-8，所以能解成合法
    UTF-8、并且控制字符不多的才算。这条规则一次就能把上面那些假阳性全挡掉
    （`HelloWorld` 解出 `\\x1dée¡j+\\x95`、`password123` 解出 `µë-×mø`、
    `DeadBeef` 解出 `de ad be ef`、`2024-01-01` 解出 `\\x20\\x24\\x01\\x01`，
    都不是合法 UTF-8）。

    只认 UTF-8、不认 GBK 是有意的：GBK 的编码空间几乎覆盖所有字节序列，
    任何随机字节都能「成功」解出一两个汉字，拿它当判据等于没有判据。
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    bad = 0
    for ch in text:
        code = ord(ch)
        if code < 0x20 and ch not in "\t\n\r":
            bad += 1
        elif code == 0x7F:
            bad += 1
    # 允许少量控制字符（有些文本自带），超过 10% 就当是二进制垃圾
    return bad * 10 <= len(text)


def looks_like_hex(text: str, min_len: int = 4) -> bool:
    """判断一段文本是否像十六进制：只由 0-9a-fA-F 和分隔符组成。"""
    if not text or not text.strip():
        return False
    raw = text.strip()
    if len(raw) > MAX_BINARY_CHARS:
        return False
    has_prefix = bool(re.search(r"(?i)0x", raw))
    # 有没有分隔符很关键：「4869」和「48 69」都可能是十六进制，但「2024」
    # 更像年份。带分隔符或 0x 前缀时基本可以确定是人工写的十六进制。
    has_sep = bool(re.search(r"[\s:,_\-]+", raw))
    body = re.sub(r"(?i)0x", "", raw)
    body = re.sub(r"[\s:,_\-]+", "", body)
    if len(body) < min_len:
        return False
    if not set(body) <= _HEX_CHARS:
        return False
    # 十六进制必须能解成合法 UTF-8 才算——这条把关放在最前面，因为它
    # 同时管着「有没有分隔符」那条捷径：`2024-01-01` 带分隔符，字符集
    # 也全是十六进制合法字符，但解出来是 `\x20\x24\x01\x01` 一堆控制字符。
    if len(body) % 2:
        return False
    try:
        data = binascii.unhexlify(body)
    except (binascii.Error, ValueError):
        return False
    if not _decode_is_text(data):
        return False
    if has_prefix or has_sep:
        return True
    # 没有前缀也没有分隔符时从严：要有一位 a-f，或者长到不像普通数字
    if len(body) < 16 and not any(c in "abcdefABCDEF" for c in body):
        return False
    return True


def text_to_hex(text: str, sep: str = " ") -> str:
    """文本 -> 十六进制（按 UTF-8 字节）。"""
    if text == "":
        return ""
    data = text.encode("utf-8")
    return sep.join(f"{b:02X}" for b in data)


def hex_to_text(text: str) -> Tuple[Optional[str], str]:
    """十六进制 -> 文本。返回 (结果, 提示)；失败时结果为 None。"""
    if not text or not text.strip():
        return None, "内容是空的"
    body = re.sub(r"(?i)0x", "", text.strip())
    body = re.sub(r"[\s:,_\-]+", "", body)
    if not body:
        return None, "没有可识别的十六进制字符"
    bad = next((c for c in body if c not in _HEX_CHARS), None)
    if bad is not None:
        return None, f"包含非十六进制字符：{bad!r}"
    if len(body) % 2:
        return None, f"十六进制位数为奇数（{len(body)} 位），无法组成完整字节"
    try:
        data = binascii.unhexlify(body)
    except (binascii.Error, ValueError) as exc:
        return None, f"十六进制解析失败：{exc}"
    raw, enc = bytes_to_text_ex(data)
    dubious = enc == "latin-1" and any(b >= 0x80 for b in data)
    msg = f"已解码 {len(data)} 字节 / {len(body)} 个十六进制位"
    if dubious:
        msg += " · 含非 UTF-8 字节，按 latin-1 显示，可能是乱码"
    return escape_controls(raw), msg


# ---------------------------------------------------------------------------
# Base64
# ---------------------------------------------------------------------------
_B64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def looks_like_base64(text: str, min_len: int = 4) -> bool:
    """判断一段文本是否像 Base64。

    标准 Base64 只用 A-Z a-z 0-9 + / =，但 URL-safe 变体用 - 和 _，
    这两种都认。

    长度门槛定在 4 而不是 8：「Hi」的 Base64 是 `SGk=`，只有 3 个有效字符，
    门槛 8 会把它漏掉（用户拿它当反例试过一次）。放这么低的前提是下面
    最后那道「真解一遍看结果」的关足够严——光靠字符集的话 4 个字母的
    英文单词全都会中招。
    """
    if not text or not text.strip():
        return False
    # 「0x48 0x69」这种明显是十六进制的写法别当 Base64：去掉分隔符后
    # 每个字符都在 Base64 字符集里，光看字符集分辨不出来。
    if re.search(r"(?i)0x", text):
        return False
    raw = re.sub(r"\s+", "", text.strip())
    if len(raw) > MAX_BINARY_CHARS:
        return False
    if len(raw) < min_len:
        return False
    body = raw.rstrip("=")
    if not body:
        return False
    if not set(body) <= (_B64_CHARS - {"="}) | {"-", "_"}:
        return False
    # 纯字母（没有数字、没有 +/、没有大小写混排）的普通英文不该算 Base64。
    # 例："hello" 长度 5 不够，"helloworld" 长度 10 全是小写字母 —— 排除掉。
    has_digit = any(c.isdigit() for c in body)
    has_symbol = any(c in "+/-_" for c in body)
    has_upper = any(c.isupper() for c in body)
    has_lower = any(c.islower() for c in body)
    # 尾部有 = 填充是 Base64 最硬的指纹（普通英文单词不会以 = 结尾），
    # 这种就不走下面那条「纯字母」排除，否则 `YQ==`（就是字母 a）永远认不出。
    padded = raw.endswith("=") and len(body) >= 1
    if not padded and not (has_digit or has_symbol or (has_upper and has_lower)):
        return False
    # 最后一道关：真解一遍。Base64 的字符集覆盖了大半个可打印 ASCII，
    # `HelloWorld`、`password123`、`Supercalifragilistic`、`ThisIsATest`
    # 全都「看起来像」——它们的共同点是解出来不是合法 UTF-8 文本。
    # 本工具的编码器一律用 UTF-8，所以解不出文本就一定不是它编的。
    try:
        padded = body + "=" * ((-len(body)) % 4)
        data = base64.b64decode(padded.replace("-", "+").replace("_", "/"),
                                validate=True)
    except (binascii.Error, ValueError):
        return False
    return _decode_is_text(data)


def text_to_base64(text: str) -> str:
    """文本 -> Base64（UTF-8）。"""
    if text == "":
        return ""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def base64_to_text(text: str) -> Tuple[Optional[str], str]:
    """Base64 -> 文本。返回 (结果, 提示)；失败时结果为 None。"""
    if not text or not text.strip():
        return None, "内容是空的"
    raw = re.sub(r"\s+", "", text.strip())
    # URL-safe 变体换成标准字符
    raw = raw.replace("-", "+").replace("_", "/")
    bad = next((c for c in raw if c not in _B64_CHARS), None)
    if bad is not None:
        return None, f"包含非 Base64 字符：{bad!r}"
    pad = (-len(raw)) % 4
    if pad:
        raw += "=" * pad
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        return None, f"Base64 解析失败：{exc}"
    raw_text, enc = bytes_to_text_ex(data)
    dubious = enc == "latin-1" and any(b >= 0x80 for b in data)
    msg = f"已解码 {len(data)} 字节"
    if dubious:
        msg += " · 含非 UTF-8 字节，按 latin-1 显示，可能是乱码"
    return escape_controls(raw_text), msg


# ---------------------------------------------------------------------------
# 摩斯码
# ---------------------------------------------------------------------------
# 国际摩斯码。点用 "."，划用 "-"，字母之间空格，单词之间 "/"。
_MORSE_TABLE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.", "!": "-.-.--",
    "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...", ":": "---...",
    ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "-": "-....-", "_": "..--.-",
    '"': ".-..-.", "$": "...-..-", "@": ".--.-.",
}
_MORSE_REVERSE = {v: k for k, v in _MORSE_TABLE.items()}
# 摩斯码里点和划的常见替代写法：· 和 ・ 当点，— – ― 当划
_MORSE_NORMALIZE = str.maketrans({"·": ".", "・": ".", "—": "-", "–": "-", "―": "-"})


def looks_like_morse(text: str, min_len: int = 3) -> bool:
    """判断一段文本是否像摩斯码：只由 . - / 空格组成，且至少有一个点或划。

    也认 ? —— 那是我们自己编码时给「码表里没有的字符」留的占位符，
    不认的话带 ? 的摩斯串就识别不出来，往返会断。
    """
    if not text or not text.strip():
        return False
    body = text.strip().translate(_MORSE_NORMALIZE)
    if len(body) > MAX_BINARY_CHARS:
        return False
    if not set(body) <= {".", "-", "/", " ", "\t", "\n", "?"}:
        return False
    # ? 也算内容：中文这类码表里没有的字符整句都编成 ?（「你好」→「? ?」），
    # 一个点划都没有。只数点划的话这种串会被判成「不像摩斯码」，
    # 用户转过去就再也转不回来了。
    marks = body.count(".") + body.count("-")
    holes = body.count("?")
    if marks + holes < min_len:
        # 全是占位符的短串也要认：单个汉字编出来就是一个 "?"，
        # 按 min_len 卡掉的话「你」→「?」这一趟就回不来了。
        if holes >= 1 and marks == 0:
            return True
        return False
    # 至少要有一个分隔符或足够多的点划，否则 "-" 这种单个符号会被误判
    if " " not in body and "\t" not in body and "\n" not in body:
        # 没有空格时，"..."（省略号）、"---"（分隔线）、"----" 这种
        # 纯重复符号是日常输入里很常见的东西，全认成摩斯会解出 "SSS"/"O"。
        # 真正的摩斯字母里，同一个字符重复成串的只有 E(.) / T(-) / H(....)
        # / 5(.....) / 0(-----) 这几种，撞上就撞上，宁可不认。
        dashed = body.count(".")
        if dashed == 0 or dashed == len(body) or body.count("-") == len(body):
            return False
    return True


def text_to_morse(text: str) -> str:
    """文本 -> 摩斯码。字母之间空格，单词之间 /。

    摩斯的国际码表只覆盖拉丁字母、数字和少量标点，中文完全没有对应码。
    这类字符统一编成 ?（国际惯例的「无法表示」占位）：解码时原样回到 ?，
    用户一眼能看出哪几个字没转过去，而不是看到一个静默丢字的串。
    """
    if text == "":
        return ""
    out = []
    for word in text.upper().split(" "):
        if not word:
            out.append("")
            continue
        letters = []
        for ch in word:
            code = _MORSE_TABLE.get(ch)
            letters.append(code if code is not None else "?")
        out.append(" ".join(letters))
    return " / ".join(out)


def morse_to_text(text: str) -> Tuple[Optional[str], str]:
    """摩斯码 -> 文本。返回 (结果, 提示)；失败时结果为 None。

    摩斯码本身不区分大小写，解出来一律大写——这是编码的限制，不是丢信息。
    """
    if not text or not text.strip():
        return None, "内容是空的"
    body = text.strip().translate(_MORSE_NORMALIZE)
    bad = next((c for c in body if c not in ".-/ ?\t\n"), None)
    if bad is not None:
        return None, f"包含非摩斯码字符：{bad!r}"
    words = re.split(r"\s*/\s*", body.strip())
    out_words = []
    unknown = 0
    holes = 0
    for word in words:
        letters = []
        for code in word.split():
            if code == "?":
                # 这是我们自己的占位符（码表里没有的字符，比如中文）
                holes += 1
                letters.append("?")
                continue
            ch = _MORSE_REVERSE.get(code)
            if ch is None:
                unknown += 1
                letters.append("?")
            else:
                letters.append(ch)
        out_words.append("".join(letters))
    result = " ".join(out_words)
    # 只有占位符也算解码成功：「你好」整句编成 `? ?`，一个点划都没有，
    # 这是「码表里没这几个字」的正常结果，不是解码失败。早先这里按
    # 「去掉 ? 和空格就空了 → 报没有可识别的摩斯码」处理，导致中文
    # 编成摩斯就再也转不回来了。
    if not result.strip():
        return None, "没有可识别的摩斯码"
    if not result.strip(" ?") and not holes and not unknown:
        return None, "没有可识别的摩斯码"
    msg = f"已解码 {len(result.replace(' ', ''))} 个字符"
    if unknown:
        msg += f" · 有 {unknown} 组码表里没有，用 ? 占位"
    elif holes:
        msg += f" · 有 {holes} 个字符码表里没有（中文等），用 ? 占位"
    return result, msg



def describe(text: str) -> str:
    """给界面用的一句话描述：字符数 / 字节数。"""
    return f"{len(text)} 字符 · {len(text.encode('utf-8'))} 字节"


# ---------------------------------------------------------------------------
# 编码格式注册表
# ---------------------------------------------------------------------------
# 界面按这个表来生成「格式」切换按钮，新增一种编码只要往这里加一项。
# encode 是「文本 -> 字符串」，decode 是「字符串 -> (结果, 提示)」。
FORMATS = ("binary", "hex", "base64", "morse")
FORMAT_TEXT = {
    "binary": "二进制",
    "hex": "十六进制",
    "base64": "Base64",
    "morse": "摩斯码",
}

# 分隔符预设。key 存进配置文件，值是实际用的字符串。
SEPARATORS = ("space", "comma", "newline", "none", "0b")
SEPARATOR_TEXT = {
    "space": "空格",
    "comma": "逗号",
    "newline": "换行",
    "none": "紧凑",
    "0b": "0b 前缀",
}
SEPARATOR_VALUE = {
    "space": " ",
    "comma": ", ",
    "newline": "\n",
    "none": "",
    "0b": " ",
}


def separator_for(name: str) -> str:
    """把分隔符名字换成实际字符串。不认识的一律当空格。

    故意从严：配置里写了个错名字就当空格，而不是把那串字母原样塞进
    位串中间（那会产出一个谁也解不回来的东西）。
    """
    return SEPARATOR_VALUE.get(name, " ")


def apply_separator(bits_grouped: str, name: str) -> str:
    """给已经用空格分好组的位串换分隔符。

    单独一步是因为 text_to_binary 的 sep 只能是固定串，而「0b 前缀」
    需要在每段前面加东西，没法用一个分隔符表达。
    """
    if name == "0b":
        return " ".join("0b" + p for p in bits_grouped.split() if p)
    return bits_grouped.replace(" ", separator_for(name))


def encode_as(text: str, fmt: str = "binary", sep: str = " ", group: int = 8) -> str:
    """按指定格式把文本编成字符串。

    sep 优先按预设名理解（"space"/"comma"/"newline"/"none"/"0b"），
    不是预设名就当「配置里写错了」，退回空格 —— 不能把 "nonsense"
    这串字母原样当地分隔符插进位串中间，那编出来的东西谁也解不回来。
    """
    if fmt == "hex":
        return text_to_hex(text, sep=separator_for(sep))
    if fmt == "base64":
        return text_to_base64(text)
    if fmt == "morse":
        return text_to_morse(text)
    # 「0b 前缀」用 sep 表达不了（每段前面要补东西），先按空格分组再改写
    if sep == "0b":
        return apply_separator(text_to_binary(text, group=group, sep=" "), "0b")
    return text_to_binary(text, group=group, sep=separator_for(sep))


def decode_as(text: str, fmt: str = "binary") -> Tuple[Optional[str], str]:
    """按指定格式把字符串解回文本。返回 (结果, 提示)。"""
    if fmt == "hex":
        return hex_to_text(text)
    if fmt == "base64":
        return base64_to_text(text)
    if fmt == "morse":
        return morse_to_text(text)
    return binary_to_text(text)


def detect_format(text: str) -> str:
    """猜这段文字是哪种编码编出来的。猜不出就返回空串。"""
    if not text or not text.strip():
        return ""
    # 摩斯码优先：它的字符集（.-/ ）和其他三种完全不重叠，不会误判。
    if looks_like_morse(text):
        return "morse"
    if looks_like_binary(text):
        return "binary"
    # 十六进制排在 Base64 前面：纯数字串（"12345678901234567890"）两种都符合，
    # 但把它当十六进制解出来至少是字节，当 Base64 解出来更可能是乱码。
    if looks_like_hex(text):
        return "hex"
    if looks_like_base64(text):
        return "base64"
    return ""


def auto_detect(text: str) -> str:
    """自动判定方向：'bin2text' 或 'text2bin' 或 'unknown'。

    名字保留是为了兼容旧调用方；判定逻辑现在覆盖全部四种编码。
    """
    if detect_format(text):
        return "bin2text"
    if text.strip():
        return "text2bin"
    return "unknown"

