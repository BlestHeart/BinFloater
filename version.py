# -*- coding: utf-8 -*-
"""版本号的唯一来源。

为什么单独开一个文件，而不是写在 gui.py 里：
  1. 安装包（installer/BinFloater.iss）要用同一个版本号，构建脚本从
     `python -c "import version; print(version.VERSION)"` 读它再传给 ISCC；
  2. main.py 的 `--version` 要能打印，不能为此去 import gui（那个会加载
     tkinter 全套，命令行下没必要）；
  3. 以后做「检查更新」时，要拿它和 GitHub Release 的 tag 对比。

改版本只改这里一处，别的地方都引用它。
语义化版本：主版本.次版本.修订号，和 git tag `v{ VERSION }` 保持一致。
"""

VERSION = "1.7.1"

# 程序显示名。installer 里也用它，保持一致。
DISPLAY_NAME = "二进制悬浮窗"


def version_tuple(text=None):
    """把 "1.4.0" 这类字符串转成可比较的元组 (1, 4, 0)。

    容忍前面的 v、后面带 -beta 之类的后缀，取不到数字的部分按 0 算。
    仅用于「谁更新」的判断，不做严格校验。
    """
    if text is None:
        text = VERSION
    buf = ""
    parts = []
    for ch in str(text).strip().lstrip("vV"):
        if ch.isdigit():
            buf += ch
        elif ch == ".":
            parts.append(buf)
            buf = ""
        elif buf:
            # 遇到 -beta / +build 之类，直接结束当前段的数字
            break
    parts.append(buf)
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while out and out[-1] == 0:
        out.pop()
    return tuple(out)


def is_newer(candidate, current=None):
    """candidate 是否比 current 新。用于判断「有新版本了」。"""
    if current is None:
        current = VERSION
    return version_tuple(candidate) > version_tuple(current)
