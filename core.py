# -*- coding: utf-8 -*-
"""二进制/文本互转核心逻辑（纯标准库，方便单测与打包）。"""

import re
from typing import Optional, Tuple

# 可识别的二进制文本形态：
#   01001000 01101001 / 0100100001101001 / 0b01001000_0b01101001 / 0100-1000 ...
_BIN_CHARS = set("01")


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
    if len(raw) > 20000:  # 防止超大文本卡界面
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
    """把纯位串按 8 位一组转字节；末尾不足 8 位时左侧补 0。"""
    if not bits:
        return b""
    pad = (-len(bits)) % 8
    if pad:
        bits = bits + "0" * pad
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def bytes_to_text(data: bytes) -> str:
    """字节转文本，依次尝试 utf-8 / gbk / latin-1。"""
    for enc in ("utf-8", "gbk", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


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
    result = bytes_to_text(data)
    if rest:
        return result, f"位数 {n} 不是 8 的倍数，末尾 {rest} 位已补 0"
    return result, f"已解码 {n // 8} 字节 / {n} 位"


def text_to_binary(text: str, group: int = 8, sep: str = " ") -> str:
    """文本 -> 二进制位串，按 group 位分组、用 sep 连接。"""
    if text == "":
        return ""
    data = text.encode("utf-8")
    bits = "".join(f"{b:08b}" for b in data)
    group = max(1, int(group))
    chunks = [bits[i : i + group] for i in range(0, len(bits), group)]
    return sep.join(chunks)


def binary_to_hex(text: str) -> str:
    bits = _clean_bits(text)
    data = bits_to_bytes(bits)
    return " ".join(f"{b:02X}" for b in data)


def describe(text: str) -> str:
    """给界面用的一句话描述：字符数 / 字节数。"""
    return f"{len(text)} 字符 · {len(text.encode('utf-8'))} 字节"


def auto_detect(text: str) -> str:
    """自动判定方向：'bin2text' 或 'text2bin' 或 'unknown'。"""
    if looks_like_binary(text):
        return "bin2text"
    if text.strip():
        return "text2bin"
    return "unknown"
