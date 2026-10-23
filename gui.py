# -*- coding: utf-8 -*-
"""BIN 二进制悬浮窗 —— 无边框、置顶、可拖动。

功能：
  1. 监视剪切板，检测到 0/1 字符串自动翻译成文字；
  2. 内置输入框，把输入的文字实时转成二进制并可一键复制。
"""
import json
import os
import queue
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

import clipwin
import core
import hotkey as hotkey_mod
import keys
import tray
import trigger

APP_NAME = "BinFloater"
W_WIDTH = 404
W_HEIGHT = 468  # 兜底值，实际启动后按内容自适应
COLLAPSED_H = 52
PLACEHOLDER_RESULT = "复制一段 01001000 01101001 ……\n这里会自动翻译成 Hi"

# 触发方式。默认快捷键：空格连按虽然顺手，但它在任何程序里都会吃掉那几个空格，
# 对不想要这个功能的人来说代价太大，所以让用户自己开。
TRIGGER_HOTKEY = "hotkey"
TRIGGER_SPACE = "space"
TRIGGER_OFF = "off"
TRIGGER_ORDER = [TRIGGER_HOTKEY, TRIGGER_SPACE, TRIGGER_OFF]
# 名字不能叫「双击空格」：连按次数 1~5 可调，写死「双击」是错的，
# 用户把次数调到 3 之后，按钮上还写着「双击空格」，看着就不对。
TRIGGER_TEXT = {
    TRIGGER_HOTKEY: "快捷键",
    TRIGGER_SPACE: "空格连按",
    TRIGGER_OFF: "关闭",
}
# 每种模式下「代价」是什么，写在切换按钮右边。用户切换前应该知道要付出什么。
TRIGGER_HINT = {
    TRIGGER_HOTKEY: "不占用空格",
    TRIGGER_SPACE: "会吃掉这几个空格",
    TRIGGER_OFF: "不占键盘钩子",
}

# 转换方向。auto 用 core.auto_detect 判断，跟剪切板卡片的行为一致。
DIR_AUTO = "auto"
DIR_TEXT2BIN = "text2bin"
DIR_BIN2TEXT = "bin2text"
DIR_ORDER = [DIR_AUTO, DIR_TEXT2BIN, DIR_BIN2TEXT]
DIR_TEXT = {
    DIR_AUTO: "自动判断",
    DIR_TEXT2BIN: "文字→二进制",
    DIR_BIN2TEXT: "二进制→文字",
}

# ---- 配色（深色，低饱和，悬浮不刺眼） ----
BG = "#0f1117"
CARD = "#171b24"
CARD_HI = "#1d2230"
STROKE = "#2a3142"
TEXT = "#e6ebf5"
MUTED = "#8992a7"
ACCENT = "#7aa2ff"
TEAL = "#5eead4"
OK = "#4ade80"
WARN = "#fbbf24"
ERR = "#f87171"


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


def _log(msg: str) -> None:
    """写一行诊断日志。配置读写出问题时，没有日志就只能靠猜。"""
    try:
        path = os.path.join(os.path.dirname(config_path()), "error.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_config() -> dict:
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return {}
    except Exception as exc:
        _log("load_config 读取失败: %r" % (exc,))
        return {}
    if not raw.strip():
        # 空文件说明上次写入被中断了。留个备份，否则下次一保存就永久丢了。
        _log("load_config: 配置文件是空的")
        return {}
    try:
        data = json.loads(raw)
    except Exception as exc:
        # 坏掉的配置别直接覆盖掉，先备份一份再退回默认值
        _log("load_config 解析失败: %r / 原文: %s" % (exc, raw[:400]))
        try:
            os.replace(config_path(), config_path() + ".bad")
        except Exception:
            pass
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict) -> None:
    """先写临时文件再原子替换。

    直接以 "w" 打开原文件会先把它截断，万一进程在写入中途被结束（用户杀进程、
    崩溃、安全软件拦截写盘），留下的就是一个空文件——下次启动所有设置回到
    默认值，用户会以为是「没记住」。os.replace 在 Windows 上也是原子的：
    要么是旧内容，要么是新内容，不会出现写了一半的状态。
    """
    path = config_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception as exc:
        _log("save_config 失败: %r / 数据: %r" % (exc, data))
        try:
            os.remove(tmp)
        except Exception:
            pass


def _set_text(widget: tk.Text, value: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    widget.configure(state="disabled")


class ReadonlyText(tk.Text):
    """只读文本框。Ctrl+C / 右键复制走 Win32 剪切板，进程关掉后内容也还在。"""

    def __init__(self, master, **kw):
        kw.setdefault("state", "disabled")
        super().__init__(master, **kw)
        self.on_copy = None  # 由主窗口注入：(text) -> None
        self.bind("<Control-c>", self._copy_sel)
        self.bind("<Button-3>", lambda e: self._copy_sel())

    def _copy_sel(self, event=None):
        try:
            sel = self.get("sel.first", "sel.last")
        except tk.TclError:
            sel = self.get("1.0", "end-1c")
        if sel.strip():
            if self.on_copy:
                self.on_copy(sel)
            else:
                self.clipboard_clear()
                self.clipboard_append(sel)
        return "break"

    def set_text(self, value: str):
        _set_text(self, value)


class BinFloater:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self._alive = True
        self._last_seq = -1
        self._last_clip = None
        self._drag = None
        self._collapsed = False
        self._flash_after = None
        self._status_reset_after = None
        self.compact = bool(self.cfg.get("compact", False))
        self.topmost = bool(self.cfg.get("topmost", True))
        # 触发方式与转换方向
        self.trigger_mode = self.cfg.get("trigger_mode", TRIGGER_HOTKEY)
        if self.trigger_mode not in TRIGGER_ORDER:
            self.trigger_mode = TRIGGER_HOTKEY
        self.dir_mode = self.cfg.get("dir_mode", DIR_AUTO)
        if self.dir_mode not in DIR_ORDER:
            self.dir_mode = DIR_AUTO
        # 自定义快捷键。解析失败就静默退回默认值——配置文件被人手改坏
        # 不该让整个程序起不来。
        self.hotkey_spec = self.cfg.get("hotkey", hotkey_mod.DEFAULT_HOTKEY)
        self.hotkey = hotkey_mod.load(self.hotkey_spec)
        self.hotkey_spec = self.hotkey.spec()
        self._capturing = False
        self._capture_backup = None
        # 连按几下空格触发。玩游戏的人容易被空格误触，调大一点更安全。
        self.space_count = trigger.clamp_space_count(
            self.cfg.get("space_count", trigger.SPACE_COUNT_DEFAULT)
        )
        # 托盘图标 + 最小化状态
        self._tray = None
        self._tray_events = queue.Queue()
        self._in_tray = False
        self._tray_hint_shown = bool(self.cfg.get("tray_hint_shown", False))
        # 钩子回调在系统线程里跑，绝对不能直接碰 tkinter（会崩）。
        # 回调只往队列里塞一个事件，主线程用 after 轮询取。
        self._triggers = queue.Queue()
        self._watcher = None
        # 用户最近用的那个窗口。面板按钮触发时要先把焦点还回去。
        self._last_foreign_hwnd = 0

        self._init_window()
        self._init_fonts()
        self._build()
        self._restore_geometry()
        self._bind_keys()
        # 首帧拿到窗口句柄后再切圆角
        self.root.after(30, self._apply_round)
        self.root.after(200, self._poll)
        self.root.after(120, self._drain_triggers)
        self.root.after(120, self._drain_tray_events)
        self._apply_trigger_mode(silent=True)
        self._start_tray()
        # 启动时把恢复出来的设置念一遍。用户抱怨过「每次打开都要重新设」，
        # 状态栏直接写清楚这次恢复成了什么，一眼就能确认到底记没记住。
        self._set_status(self._boot_text(), TEAL, 3200)

    def _boot_text(self):
        if self.trigger_mode == TRIGGER_SPACE:
            trig = "空格连按 ×%d" % self.space_count
        elif self.trigger_mode == TRIGGER_HOTKEY:
            trig = self.hotkey.text()
        else:
            trig = "触发已关闭"
        parts = [trig]
        if self.dir_mode != DIR_AUTO:
            parts.append(DIR_TEXT.get(self.dir_mode, ""))
        return "已恢复：" + " · ".join(p for p in parts if p)

    # ---------- 窗口 ----------
    def _init_window(self):
        r = self.root
        r.title(APP_NAME)
        r.overrideredirect(True)
        r.attributes("-topmost", self.topmost)
        r.attributes("-alpha", float(self.cfg.get("alpha", 1.0)))
        r.configure(bg=BG)
        r.resizable(False, False)

    def _init_fonts(self):
        fams = set(tkfont.families(self.root))
        ui = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI") if f in fams), "TkDefaultFont")
        mono = next((f for f in ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New") if f in fams), "TkFixedFont")
        self.f_ui = ui
        self.f_mono = mono
        self.font_title = tkfont.Font(family=ui, size=10, weight="bold")
        self.font_meta = tkfont.Font(family=ui, size=9)
        self.font_body = tkfont.Font(family=ui, size=10)
        self.font_result = tkfont.Font(family=mono, size=10)
        self.font_bin = tkfont.Font(family=mono, size=9)

    def _restore_geometry(self):
        """先让 Tk 量出内容实际高度，再按内容锁定窗口大小，避免底部被截断。"""
        self.root.geometry(f"{W_WIDTH}x{W_HEIGHT}")
        self.root.update_idletasks()
        height = max(300, self.shell.winfo_reqheight())
        self.height = height
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = int(self.cfg.get("x", sw - W_WIDTH - 40))
        y = int(self.cfg.get("y", 90))
        x = max(0, min(x, sw - W_WIDTH))
        y = max(0, min(y, sh - height))
        self.root.geometry(f"{W_WIDTH}x{height}+{x}+{y}")
        self.root.update_idletasks()

    def _apply_round(self):
        """Tk 的 winfo_id() 是内层子窗口，圆角区域要加在顶层窗口上才生效。"""
        try:
            self.root.update_idletasks()
            hwnd = int(self.root.wm_frame(), 16)
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            clipwin.round_window(hwnd, width, height, 16)
        except Exception:
            pass

    # ---------- 界面 ----------
    def _build(self):
        root = self.root
        self.shell = tk.Frame(root, bg=BG)
        self.shell.pack(fill="both", expand=True)
        self._draggable(self.shell)

        self._build_header(self.shell)

        self.body = tk.Frame(self.shell, bg=BG)
        self.body.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self._draggable(self.body)

        self._build_clip_card(self.body)
        self._build_manual_card(self.body)
        self._build_convert_card(self.body)

        self.status = tk.Label(self.shell, text="", bg=BG, fg=MUTED,
                               font=self.font_meta, anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 8))

    def _draggable(self, widget):
        """让空白区域也能拖动窗口（输入框/按钮自己绑定了点击，不会被覆盖）。"""
        widget.bind("<Button-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<Double-Button-1>", lambda e: self._toggle_collapse())
        return widget

    def _build_header(self, parent):
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=12, pady=(10, 8))
        head.bind("<Button-1>", self._drag_start)
        head.bind("<B1-Motion>", self._drag_move)
        head.bind("<Double-Button-1>", lambda e: self._toggle_collapse())

        dot = tk.Canvas(head, width=10, height=10, bg=BG, highlightthickness=0)
        dot.create_oval(1, 1, 9, 9, fill=TEAL, outline="")
        dot.pack(side="left", padx=(2, 8))
        self.dot = dot

        title = tk.Label(head, text="二进制悬浮窗", bg=BG, fg=TEXT, font=self.font_title)
        title.pack(side="left")
        self.title_lbl = title
        title.bind("<Button-1>", self._drag_start)
        title.bind("<B1-Motion>", self._drag_move)
        title.bind("<Double-Button-1>", lambda e: self._toggle_collapse())

        self._hbtn(head, "✕", self._quit, hover=ERR)
        self._hbtn(head, "◐", self._cycle_alpha)
        self._hbtn(head, "▲", self._toggle_collapse, keep="collapse")
        self._hbtn(head, "—", self._minimize_to_tray, hover=TEAL)
        self.pin_btn = self._hbtn(head, "◉", self._toggle_topmost)

    def _hbtn(self, parent, text, cmd, hover=ACCENT, keep=None):
        lbl = tk.Label(parent, text=text, bg=BG, fg=MUTED, font=self.font_meta,
                       padx=7, pady=2, cursor="hand2")
        lbl.pack(side="right", padx=(2, 0))
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.configure(fg=hover, bg=CARD_HI))
        lbl.bind("<Leave>", lambda e: lbl.configure(fg=MUTED, bg=BG))
        if keep == "collapse":
            self.collapse_btn = lbl
        return lbl

    def _card(self, parent, pady=(0, 10)):
        wrap = tk.Frame(parent, bg=STROKE)  # 1px 描边效果
        wrap.pack(fill="x", pady=pady)
        inner = tk.Frame(wrap, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return inner

    def _build_clip_card(self, parent):
        card = self._card(parent)
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=12, pady=(10, 6))

        tk.Label(top, text="剪切板识别", bg=CARD, fg=TEXT,
                 font=self.font_title).pack(side="left")
        self.clip_badge = tk.Label(top, text="等待 0/1 串", bg=CARD, fg=MUTED,
                                   font=self.font_meta)
        self.clip_badge.pack(side="right")

        box = tk.Frame(card, bg=CARD_HI)
        box.pack(fill="x", padx=12)
        self.result = ReadonlyText(
            box, height=5, wrap="char", bg=CARD_HI, fg=TEXT,
            font=self.font_result, relief="flat", bd=0,
            highlightthickness=0, padx=10, pady=8,
            insertbackground=TEXT, selectbackground="#33507e",
            selectforeground="#ffffff", spacing1=1, spacing3=1,
        )
        self.result.pack(fill="x")
        self.result.on_copy = self._copy_any
        self.result.insert("1.0", PLACEHOLDER_RESULT)
        self.result.configure(state="disabled", fg="#5b6579")

        rows = tk.Frame(card, bg=CARD)
        rows.pack(fill="x", padx=12, pady=(8, 10))
        self._btn(rows, "复制译文", self._copy_result, primary=True).pack(side="left")
        self._btn(rows, "→ 转二进制", self._clip_to_bin).pack(side="left", padx=6)
        self._btn(rows, "清空", self._clear_result).pack(side="left")
        self.auto_btn = tk.Label(rows, text="", bg=CARD, fg=MUTED,
                                 font=self.font_meta, padx=6, cursor="hand2")
        self.auto_btn.pack(side="right")
        self.auto_btn.bind("<Button-1>", lambda e: self._toggle_auto_copy())
        self.auto_btn.bind("<Enter>", lambda e: self.auto_btn.configure(fg=ACCENT))
        self.auto_btn.bind("<Leave>", lambda e: self.auto_btn.configure(fg=MUTED))
        self._paint_auto_btn()

    def _build_manual_card(self, parent):
        card = self._card(parent, pady=(0, 8))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(top, text="文字 → 二进制", bg=CARD, fg=TEXT,
                 font=self.font_title).pack(side="left")
        self.len_badge = tk.Label(top, text="", bg=CARD, fg=MUTED, font=self.font_meta)
        self.len_badge.pack(side="right")
        self.compact_btn = tk.Label(top, text="紧凑", bg=CARD, fg=MUTED,
                                    font=self.font_meta, padx=6, cursor="hand2")
        self.compact_btn.pack(side="right", padx=(0, 8))
        self.compact_btn.bind("<Button-1>", lambda e: self._toggle_compact())
        self.compact_btn.bind("<Enter>", lambda e: self.compact_btn.configure(fg=ACCENT))
        self.compact_btn.bind("<Leave>", lambda e: self.compact_btn.configure(fg=MUTED))
        self._paint_compact_btn()

        # 输入框（带占位提示）
        entry_wrap = tk.Frame(card, bg=CARD_HI)
        entry_wrap.pack(fill="x", padx=12)
        self.input_var = tk.StringVar()
        self.entry = tk.Entry(
            entry_wrap, textvariable=self.input_var, bg=CARD_HI, fg=TEXT,
            font=self.font_body, relief="flat", bd=0, insertbackground=TEAL,
            highlightthickness=0, selectbackground="#33507e",
            selectforeground="#ffffff",
        )
        self.entry.pack(fill="x", padx=10, pady=8)
        self.entry.bind("<Return>", lambda e: self._copy_binary())
        self.input_var.trace_add("write", lambda *a: self._on_input())

        out_wrap = tk.Frame(card, bg=CARD_HI)
        out_wrap.pack(fill="x", padx=12, pady=(6, 0))
        self.bin_out = ReadonlyText(
            out_wrap, height=3, wrap="char", bg=CARD_HI, fg=TEAL,
            font=self.font_bin, relief="flat", bd=0, highlightthickness=0,
            padx=10, pady=8, insertbackground=TEXT,
            selectbackground="#33507e", selectforeground="#ffffff",
        )
        self.bin_out.pack(fill="x")
        self.bin_out.on_copy = self._copy_any

        rows = tk.Frame(card, bg=CARD)
        rows.pack(fill="x", padx=12, pady=(8, 10))
        self._btn(rows, "复制二进制", self._copy_binary, primary=True).pack(side="left")
        self._btn(rows, "← 从剪切板", self._paste_text).pack(side="left", padx=6)
        self._btn(rows, "清空", self._clear_manual).pack(side="left")
        self._hint = tk.Label(rows, text="Enter 复制", bg=CARD, fg="#5b6579",
                              font=self.font_meta)
        self._hint.pack(side="right")

    def _build_convert_card(self, parent):
        card = self._card(parent, pady=(0, 8))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="转换当前行", bg=CARD, fg=TEXT,
                 font=self.font_title).pack(side="left")

        desc = tk.Label(card, text="把光标所在行（行首到光标）转好后原地粘回，任何程序都能用",
                        bg=CARD, fg=MUTED, font=self.font_meta, anchor="w",
                        justify="left", wraplength=340)
        desc.pack(fill="x", padx=12)

        rows = tk.Frame(card, bg=CARD)
        rows.pack(fill="x", padx=12, pady=(8, 4))
        self._btn(rows, "转换这一行", self._convert_line_now, primary=True).pack(side="left")
        self.dir_btn = tk.Label(rows, text="", bg=CARD, fg=MUTED,
                                font=self.font_meta, padx=6, cursor="hand2")
        self.dir_btn.pack(side="right")
        self.dir_btn.bind("<Button-1>", lambda e: self._cycle_dir_mode())
        self.dir_btn.bind("<Enter>", lambda e: self.dir_btn.configure(fg=ACCENT))
        self.dir_btn.bind("<Leave>", lambda e: self._paint_dir_btn())
        self._paint_dir_btn()

        # 触发方式行。放在最前面并且单独成行：之前它挤在卡片标题右边，
        # 只是一个没有边框的彩色小字，用户根本没意识到那里可以点、更不知道
        # 点一下是在切换触发方式。现在给它自己的行 + 明确标签 + 按钮底框，
        # 右边补一句「这么做要付出什么」，切换前就知道代价。
        moderow = tk.Frame(card, bg=CARD)
        moderow.pack(fill="x", padx=12, pady=(2, 6))
        tk.Label(moderow, text="触发方式", bg=CARD, fg=MUTED,
                 font=self.font_meta).pack(side="left")
        self.trigger_btn = tk.Label(moderow, text="", bg=CARD_HI, fg=TEAL,
                                    font=self.font_meta, padx=8, pady=2,
                                    cursor="hand2")
        self.trigger_btn.pack(side="left", padx=(8, 0))
        self.trigger_btn.bind("<Button-1>", lambda e: self._cycle_trigger_mode())
        self.trigger_btn.bind("<Enter>",
                              lambda e: self.trigger_btn.configure(bg=STROKE))
        self.trigger_btn.bind("<Leave>",
                              lambda e: self.trigger_btn.configure(bg=CARD_HI))
        self.trigger_hint = tk.Label(moderow, text="", bg=CARD, fg=MUTED,
                                     font=self.font_meta)
        self.trigger_hint.pack(side="right")
        self._paint_trigger_btn()

        # 快捷键行。单独一行放，因为录制时这里要变成提示文字，
        # 挤在按钮行里会把「转换这一行」挤变形。
        keyrow = tk.Frame(card, bg=CARD)
        keyrow.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(keyrow, text="快捷键", bg=CARD, fg=MUTED,
                 font=self.font_meta).pack(side="left")
        self.hotkey_btn = tk.Label(keyrow, text="", bg=CARD_HI, fg=TEAL,
                                   font=self.font_meta, padx=8, pady=2,
                                   cursor="hand2")
        self.hotkey_btn.pack(side="left", padx=(8, 0))
        self.hotkey_btn.bind("<Button-1>", lambda e: self._start_capture())
        self.hotkey_btn.bind("<Enter>",
                             lambda e: self.hotkey_btn.configure(bg=STROKE)
                             if not self._capturing else None)
        self.hotkey_btn.bind("<Leave>",
                             lambda e: self.hotkey_btn.configure(bg=CARD_HI)
                             if not self._capturing else None)
        self.hotkey_hint = tk.Label(keyrow, text="", bg=CARD, fg=MUTED,
                                    font=self.font_meta)
        self.hotkey_hint.pack(side="right")
        self._paint_hotkey_btn()

        # 连按次数。只在「空格连按」模式下有意义，切到快捷键模式时整行变灰，
        # 免得用户以为调了没用（其实是不生效）。
        spacerow = tk.Frame(card, bg=CARD)
        spacerow.pack(fill="x", padx=12, pady=(0, 10))
        self.space_lbl = tk.Label(spacerow, text="连按空格", bg=CARD, fg=MUTED,
                                  font=self.font_meta)
        self.space_lbl.pack(side="left")
        self.space_btn = tk.Label(spacerow, text="", bg=CARD_HI, fg=TEAL,
                                  font=self.font_meta, padx=8, pady=2,
                                  cursor="hand2")
        self.space_btn.pack(side="left", padx=(8, 0))
        self.space_btn.bind("<Button-1>", lambda e: self._cycle_space_count())
        self.space_btn.bind("<Enter>",
                            lambda e: self.space_btn.configure(bg=STROKE))
        self.space_btn.bind("<Leave>",
                            lambda e: self.space_btn.configure(bg=CARD_HI))
        self.space_hint = tk.Label(spacerow, text="", bg=CARD, fg=MUTED,
                                   font=self.font_meta)
        self.space_hint.pack(side="right")
        self._paint_space_btn()

    def _btn(self, parent, text, cmd, primary=False):
        bg = "#2b3a5e" if primary else CARD_HI
        fg = "#cfe0ff" if primary else MUTED
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=self.font_meta,
                       padx=10, pady=4, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: cmd())
        hover_bg = "#38507e" if primary else STROKE
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=hover_bg))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=bg))
        return lbl

    # ---------- 交互：拖动 / 按钮 ----------
    def _drag_start(self, event):
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        if not self._drag:
            return
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        x = max(-40, min(x, self.root.winfo_screenwidth() - 80))
        y = max(0, min(y, self.root.winfo_screenheight() - 40))
        self.root.geometry(f"+{x}+{y}")

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.body.pack_forget()
            self.status.pack_forget()
            self.root.geometry(f"{W_WIDTH}x{COLLAPSED_H}")
            self.collapse_btn.configure(text="▼")
            self._paint_collapsed_title()
        else:
            body_args = dict(fill="both", expand=True, padx=12, pady=(0, 10))
            self.body.pack(**body_args)
            self.status.pack(fill="x", padx=16, pady=(0, 8))
            self.root.geometry(f"{W_WIDTH}x{self.height}")
            self.collapse_btn.configure(text="▲")
            self.title_lbl.configure(text="二进制悬浮窗")
        self.root.after(40, self._apply_round)

    def _paint_collapsed_title(self):
        """折叠时标题栏顺带显示译文，不展开也能看到结果。"""
        raw = self.result.get("1.0", "end-1c").strip()
        if not raw or raw == PLACEHOLDER_RESULT:
            self.title_lbl.configure(text="二进制悬浮窗")
            return
        text = raw.replace("\n", " ")
        if len(text) > 22:
            text = text[:22] + "…"
        self.title_lbl.configure(text=f"→ {text}")

    def _cycle_alpha(self):
        steps = [1.0, 0.92, 0.8, 0.65]
        cur = float(self.root.attributes("-alpha"))
        nxt = steps[(steps.index(min(steps, key=lambda s: abs(s - cur))) + 1) % len(steps)]
        self.root.attributes("-alpha", nxt)
        self.cfg["alpha"] = nxt
        save_config(self.cfg)
        self._set_status(f"透明度 {int(nxt * 100)}%", MUTED)

    def _toggle_topmost(self):
        self.topmost = not self.topmost
        self.root.attributes("-topmost", self.topmost)
        self.pin_btn.configure(fg=TEAL if self.topmost else MUTED)
        self.pin_btn.configure(text="◉" if self.topmost else "○")
        self.cfg["topmost"] = self.topmost
        save_config(self.cfg)
        self._set_status("已置顶" if self.topmost else "已取消置顶", TEAL if self.topmost else MUTED)

    def _toggle_compact(self):
        self.compact = not self.compact
        self.cfg["compact"] = self.compact
        save_config(self.cfg)
        self._paint_compact_btn()
        self._on_input()

    def _paint_compact_btn(self):
        self.compact_btn.configure(fg=TEAL if self.compact else MUTED,
                                   text="紧凑 ✓" if self.compact else "紧凑")

    def _toggle_auto_copy(self):
        self.cfg["auto_copy"] = not self.cfg.get("auto_copy", False)
        save_config(self.cfg)
        self._paint_auto_btn()
        self._set_status(
            "识别到二进制后将自动把译文写回剪切板" if self.cfg["auto_copy"] else "已关闭自动回写译文",
            OK if self.cfg["auto_copy"] else MUTED,
        )

    def _paint_auto_btn(self):
        on = bool(self.cfg.get("auto_copy", False))
        self.auto_btn.configure(fg=TEAL if on else MUTED,
                                text="自动回写 ✓" if on else "自动回写")

    # ---------- 转换当前行 ----------
    def _paint_trigger_btn(self):
        self.trigger_btn.configure(
            fg=MUTED if self.trigger_mode == TRIGGER_OFF else TEAL,
            text=TRIGGER_TEXT.get(self.trigger_mode, "快捷键"),
        )
        self.trigger_hint.configure(
            text=TRIGGER_HINT.get(self.trigger_mode, ""),
            fg=MUTED if self.trigger_mode != TRIGGER_OFF else "#5b6579",
        )

    def _cycle_trigger_mode(self):
        idx = TRIGGER_ORDER.index(self.trigger_mode)
        self.trigger_mode = TRIGGER_ORDER[(idx + 1) % len(TRIGGER_ORDER)]
        self.cfg["trigger_mode"] = self.trigger_mode
        save_config(self.cfg)
        self._paint_trigger_btn()
        self._apply_trigger_mode()
        self._paint_space_btn()
        # 托盘提示和气泡通知都按当前模式说话，切模式就得跟着改
        self._refresh_tray_tooltip()

    def _apply_trigger_mode(self, silent=False):
        """按当前模式装/卸键盘钩子。"""
        space = self.trigger_mode == TRIGGER_SPACE
        hotkey = self.trigger_mode == TRIGGER_HOTKEY
        if space or hotkey:
            if self._watcher is None:
                self._watcher = trigger.KeyboardWatcher(
                    self._on_hotkey_trigger, space_mode=space, hotkey_mode=hotkey,
                    hotkey=self.hotkey, on_capture=self._on_hotkey_captured,
                    space_count=self.space_count,
                )
                if not self._watcher.install():
                    self._watcher = None
                    if not silent:
                        self._set_status("键盘钩子装不上，可能被安全软件拦了", WARN, 6000)
                    return
            else:
                self._watcher.space_mode = space
                self._watcher.hotkey_mode = hotkey
                self._watcher.hotkey = self.hotkey
                self._watcher.space_count = self.space_count
        else:
            self._release_watcher()
        # 托盘 tooltip 写的是「怎么触发」，模式变了就得跟着变。
        # 放在这里而不是 _cycle_trigger_mode 里：开机恢复配置、程序内部改
        # trigger_mode 都会走这个函数，放这里才不会有漏网的路径。
        self._refresh_tray_tooltip()
        if not silent:
            if self.trigger_mode == TRIGGER_OFF:
                self._set_status("已关闭快捷键触发", MUTED)
            elif self.trigger_mode == TRIGGER_SPACE:
                self._set_status("连按 %d 下空格触发（不按 %s）"
                                 % (self.space_count, self.hotkey.text()), TEAL)
            else:
                self._set_status("按 %s 触发转换当前行" % self.hotkey.text(), TEAL)

    def _release_watcher(self):
        if self._watcher is not None:
            self._watcher.uninstall()
            self._watcher = None

    # ---------- 连按次数 ----------
    def _paint_space_btn(self):
        active = self.trigger_mode == TRIGGER_SPACE
        self.space_btn.configure(
            text="%d 下" % self.space_count,
            fg=TEAL if active else "#5b6579",
        )
        self.space_lbl.configure(fg=MUTED if active else "#5b6579")
        if not active:
            self.space_hint.configure(text="仅空格连按模式", fg="#5b6579")
        elif self.space_count <= 2:
            self.space_hint.configure(text="容易误触就调大", fg=MUTED)
        else:
            self.space_hint.configure(text="更不容易误触", fg=MUTED)

    def _cycle_space_count(self):
        if self.trigger_mode != TRIGGER_SPACE:
            self._set_status("先把触发方式切成「空格连按」", WARN, 5000)
            return
        n = self.space_count + 1
        if n > trigger.SPACE_COUNT_MAX:
            n = trigger.SPACE_COUNT_MIN
        self.space_count = n
        self.cfg["space_count"] = n
        save_config(self.cfg)
        if self._watcher is not None:
            self._watcher.space_count = n
        self._paint_space_btn()
        self._set_status("连按 %d 下空格触发" % n, TEAL)
        # 空格模式下托盘提示写的是「连按 N 下空格」，改了次数也要同步
        self._refresh_tray_tooltip()

    # ---------- 自定义快捷键 ----------
    def _paint_hotkey_btn(self):
        if self._capturing:
            self.hotkey_btn.configure(text="按下新的组合键…", fg=WARN, bg=CARD_HI)
            self.hotkey_hint.configure(text="Esc 取消")
            return
        self.hotkey_btn.configure(text=self.hotkey.text(), fg=TEAL, bg=CARD_HI)
        if not self.hotkey.has_modifier():
            self.hotkey_hint.configure(text="建议加修饰键", fg=WARN)
        else:
            self.hotkey_hint.configure(text="点一下改", fg=MUTED)

    def _start_capture(self):
        """进入录制状态：钩子开始把按键组合报回来。"""
        if self._capturing:
            self._stop_capture()
            return
        if self.trigger_mode == TRIGGER_OFF:
            self._set_status("先把触发方式切成「快捷键」", WARN, 5000)
            return
        if self._watcher is None:
            self._set_status("键盘钩子没装上，改不了快捷键", WARN, 5000)
            return
        # 记下原值：录制失败或被取消时要还原，不能让用户白点一场。
        self._capture_backup = self.hotkey_spec
        self._capturing = True
        self._watcher.capture_mode = True
        self._paint_hotkey_btn()
        self._set_status("按下想用的组合键，Esc 取消", TEAL, 0)

    def _stop_capture(self):
        self._capturing = False
        self._capture_backup = None
        if self._watcher is not None:
            self._watcher.capture_mode = False
        self._paint_hotkey_btn()
        self._idle_status()

    def _on_hotkey_captured(self, spec):
        """在钩子线程里被调用 —— 只能入队，交给主线程处理。"""
        try:
            self._triggers.put_nowait(("capture", spec))
        except Exception:
            pass

    def _apply_captured(self, spec):
        self._capturing = False
        if self._watcher is not None:
            self._watcher.capture_mode = False

        if not spec:
            self.hotkey_spec = self._capture_backup or hotkey_mod.DEFAULT_HOTKEY
            self.hotkey = hotkey_mod.load(self.hotkey_spec)
            self._capture_backup = None
            self._paint_hotkey_btn()
            self._set_status("已取消，快捷键没变", MUTED)
            return

        parsed = hotkey_mod.parse(spec)
        if parsed is None:
            self._paint_hotkey_btn()
            self._set_status("这个键不支持，换一个", WARN, 5000)
            return

        self.hotkey = parsed
        self.hotkey_spec = parsed.spec()
        self.cfg["hotkey"] = self.hotkey_spec
        save_config(self.cfg)
        self._capture_backup = None
        if self._watcher is not None:
            self._watcher.hotkey = parsed
        self._paint_hotkey_btn()
        self._refresh_tray_tooltip()
        if parsed.has_modifier():
            self._set_status("快捷键已改为 %s" % parsed.text(), OK, 5000)
        else:
            self._set_status("%s —— %s" % (parsed.text(), hotkey_mod.WARN_NO_MODIFIER),
                             WARN, 7000)

    def _on_hotkey_trigger(self, kind):
        """在系统钩子线程里被调用 —— 这里只能入队，不能碰 tkinter。"""
        try:
            self._triggers.put_nowait(kind)
        except Exception:
            pass

    def _drain_triggers(self):
        if not self._alive:
            return
        try:
            while True:
                item = self._triggers.get_nowait()
                # 队列里混着两种事件：转换触发（字符串）和快捷键录制结果（元组）。
                # 用类型区分，免得以后再加事件类型时又得改一轮。
                if isinstance(item, tuple) and item and item[0] == "capture":
                    self._apply_captured(item[1])
                else:
                    self._do_convert_line(item)
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(120, self._drain_triggers)
        except Exception:
            pass

    def _convert_line_now(self):
        """面板上的按钮：先把焦点还给刚才那个窗口，再转换。

        切窗口不是一次就成的：Windows 有前台锁定，SetForegroundWindow 常被
        静默忽略。keys.focus_window 内部已经会走 AttachThreadInput 兜底，但
        偶尔还是要多试两次，所以这里重试并在失败时明确告诉用户改用什么。
        """
        if not self._last_foreign_hwnd:
            self._do_convert_line("button")
            return

        target = self._last_foreign_hwnd

        def attempt(left):
            if keys.focus_window(target):
                # 焦点切换是异步的，等系统真的把前台还回去再发按键
                self.root.after(120, lambda: self._do_convert_line("button"))
                return
            if left > 0:
                self.root.after(60, lambda: attempt(left - 1))
            else:
                self._set_status("切不回刚才的窗口，请用 %s 触发"
                                 % self.hotkey.text(), WARN, 6000)

        attempt(2)

    def _do_convert_line(self, kind):
        if not keys.IS_WINDOWS:
            self._set_status("这个功能只在 Windows 上可用", WARN)
            return
        mode = self.dir_mode

        def convert(text):
            if mode == DIR_TEXT2BIN:
                return core.text_to_binary(text, sep="" if self.compact else " ")
            if mode == DIR_BIN2TEXT:
                out, _msg = core.binary_to_text(text)
                return out or ""
            how = core.auto_detect(text)
            if how == "bin2text":
                out, _msg = core.binary_to_text(text)
                return out or ""
            if how == "text2bin":
                return core.text_to_binary(text, sep="" if self.compact else " ")
            return ""

        try:
            status, src, out, msg = keys.convert_line(convert)
        except Exception as exc:
            self._set_status(f"转换失败：{exc}", ERR, 6000)
            return

        if status == "ok":
            preview = out if len(out) <= 46 else out[:46] + "…"
            self._set_status(f"已转换 {len(src)} 字符 → {preview}", OK, 5000)
        elif status == "empty":
            self._set_status("光标前面这一行没有文字", WARN)
        elif status == "same":
            self._set_status("转换结果和原文一样，没动", MUTED)
        elif status == "clipfail":
            self._set_status("写剪切板失败，可能被别的程序占着", ERR, 6000)
        else:
            self._set_status("当前窗口不支持这个操作", WARN)

    def _paint_dir_btn(self):
        self.dir_btn.configure(text=DIR_TEXT.get(self.dir_mode, "自动判断"),
                               fg=TEAL if self.dir_mode != DIR_AUTO else MUTED)

    def _cycle_dir_mode(self):
        idx = DIR_ORDER.index(self.dir_mode)
        self.dir_mode = DIR_ORDER[(idx + 1) % len(DIR_ORDER)]
        self.cfg["dir_mode"] = self.dir_mode
        save_config(self.cfg)
        self._paint_dir_btn()

    def _bind_keys(self):
        r = self.root
        r.bind("<Control-q>", lambda e: self._quit())
        r.bind("<Control-b>", lambda e: self._clip_to_bin())
        r.bind("<Escape>", lambda e: self._toggle_collapse())
        self.entry.bind("<Escape>", lambda e: self.entry.selection_clear())
        # 关闭窗口时保存位置
        r.protocol("WM_DELETE_WINDOW", self._quit)

    def _quit(self):
        self._alive = False
        self._release_watcher()
        self._stop_tray()
        try:
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            save_config(self.cfg)
            self.root.destroy()
        except Exception:
            pass

    # ---------- 托盘 ----------
    def _start_tray(self):
        """建托盘图标。失败不影响主功能，只是没有托盘。"""
        try:
            trig = self._trigger_text()
            self._tray = tray.TrayIcon(
                self._on_tray_event,
                tooltip=(("二进制悬浮窗 · %s" % trig) if trig
                         else "二进制悬浮窗 · 触发已关闭"),
            )
            self._tray.start()
        except Exception:
            self._tray = None

    def _stop_tray(self):
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
            self._tray = None

    def _on_tray_event(self, kind):
        """托盘线程回调 —— 只入队，绝不碰 tkinter。"""
        try:
            self._tray_events.put_nowait(kind)
        except Exception:
            pass

    def _drain_tray_events(self):
        if not self._alive:
            return
        try:
            while True:
                kind = self._tray_events.get_nowait()
                if kind == "show":
                    self._show_from_tray()
                elif kind == "quit":
                    self._quit()
                    return
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(120, self._drain_tray_events)
        except Exception:
            pass

    def _minimize_to_tray(self):
        """最小化：窗口藏起来，托盘常驻，键盘钩子不动。

        关键点：只是 withdraw 窗口，**不卸钩子**。用户要的就是「看不见它
        但快捷键还能用」。
        """
        if self._tray is None or not self._tray.running:
            # 没有托盘就退化成收成一条标题栏，至少不占屏幕
            if not self._collapsed:
                self._toggle_collapse()
            self._set_status("托盘没起来，先收成一条", WARN, 5000)
            return
        self._in_tray = True
        try:
            self.root.withdraw()
        except Exception:
            pass
        # 只弹一次：每次最小化都弹会烦人。用户可在设置里清掉这个标记。
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.cfg["tray_hint_shown"] = True
            save_config(self.cfg)
            trig = self._trigger_text()
            if trig:
                # 快捷键末尾是 ASCII 字母，跟后面的中文之间要留个空格；
                # 空格模式的「连按 2 下空格」以汉字结尾，再留空格就松了。
                sep = " " if self.trigger_mode == TRIGGER_HOTKEY else ""
                msg = "悬浮窗收到托盘了，%s%s依然可用。" % (trig, sep)
            else:
                msg = "悬浮窗收到托盘了。触发已关闭，需要时从托盘叫回来。"
            self._notify_tray("已收进托盘", msg)

    def _notify_tray(self, title, text, warning=False):
        if self._tray is None:
            return False
        try:
            return self._tray.notify(title, text, warning)
        except Exception:
            return False

    def _trigger_text(self):
        """当前生效的触发方式的说法，给托盘提示和气泡通知用。

        必须按 trigger_mode 现算，不能写死快捷键：用户在「空格连按」模式下
        收到的通知却写着「Ctrl+Alt+B 依然可用」，那是在骗人——空格模式下
        按快捷键根本不会触发。
        """
        if self.trigger_mode == TRIGGER_SPACE:
            return "连按 %d 下空格" % self.space_count
        if self.trigger_mode == TRIGGER_HOTKEY:
            return "按 %s" % self.hotkey.text()
        return ""

    def _refresh_tray_tooltip(self):
        """托盘提示要跟着当前模式走，不能一直写快捷键。"""
        if self._tray is None:
            return
        try:
            trig = self._trigger_text()
            self._tray.tooltip = (("二进制悬浮窗 · %s" % trig) if trig
                                  else "二进制悬浮窗 · 触发已关闭")[:127]
        except Exception:
            pass

    def _show_from_tray(self):
        if not self._in_tray:
            self._bring_to_front()
            return
        self._in_tray = False
        try:
            self.root.deiconify()
        except Exception:
            pass
        self._bring_to_front()

    def _bring_to_front(self):
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            if not self.topmost:
                self.root.attributes("-topmost", False)
            self.root.after(60, self._apply_round)
        except Exception:
            pass
        self._set_status("已从托盘恢复", TEAL, 2500)

    # ---------- 状态提示 ----------
    def _set_status(self, text, color=MUTED, reset_after=4000):
        try:
            self.status.configure(text=text, fg=color)
        except tk.TclError:
            return
        if self._status_reset_after:
            try:
                self.root.after_cancel(self._status_reset_after)
            except Exception:
                pass
            self._status_reset_after = None
        if reset_after:
            self._status_reset_after = self.root.after(reset_after, self._idle_status)

    def _idle_status(self):
        self._status_reset_after = None
        try:
            self.status.configure(text="监视中 · 复制 0/1 串即可自动翻译", fg=MUTED)
        except tk.TclError:
            pass

    def _flash_result(self):
        try:
            self.result.configure(bg="#22304a")
            if self._flash_after:
                self.root.after_cancel(self._flash_after)
            self._flash_after = self.root.after(220, self._unflash)
        except tk.TclError:
            pass

    def _unflash(self):
        try:
            self.result.configure(bg=CARD_HI)
        except tk.TclError:
            pass

    # ---------- 剪切板监视 ----------
    def _own_hwnd(self) -> int:
        try:
            return int(self.root.wm_frame(), 16)
        except Exception:
            return 0

    def _remember_foreground(self):
        """记住用户最近用的那个窗口。

        点面板上的「转换这一行」时前台已经变成悬浮窗自己了，所以要提前记住。
        气泡是 FLAG_NOT_FOCUSABLE 的，点它不会抢焦点，所以这里拿到的基本都是
        用户真正在打字的那个窗口。
        """
        try:
            hwnd = keys.foreground_window()
            if hwnd and hwnd != self._own_hwnd():
                self._last_foreign_hwnd = hwnd
        except Exception:
            pass

    def _poll(self):
        if not self._alive:
            return
        try:
            self._remember_foreground()
            seq = clipwin.sequence()
            if seq != self._last_seq:
                self._last_seq = seq
                text = clipwin.read_text()
                if text and text != self._last_clip:
                    self._last_clip = text
                    self._on_clipboard(text)
        except Exception as exc:  # 监视线程永不死
            self._set_status(f"读取剪切板失败：{exc}", ERR)
        try:
            self.root.after(320, self._poll)
        except tk.TclError:
            pass

    def _on_clipboard(self, text: str):
        if core.looks_like_binary(text):
            decoded, msg = core.binary_to_text(text)
            if decoded is None:
                self.clip_badge.configure(text="识别失败", fg=WARN)
                self._set_status(f"{msg} · 可手动修改后再试", WARN, 6000)
                return
            self.result.configure(fg=TEXT)
            self.result.set_text(decoded)
            self.clip_badge.configure(text=f"已翻译 · {msg}", fg=OK)
            self._set_status(f"检测到二进制 → 已翻译：{msg}", OK, 6000)
            self._flash_result()
            if self._collapsed:
                self._paint_collapsed_title()
            if self.cfg.get("auto_copy", False) and decoded.strip():
                self._write_clip(decoded)
        else:
            # 普通文字：不打断，只提示可一键转二进制
            shown = text.strip().replace("\n", " ")
            if len(shown) > 26:
                shown = shown[:26] + "…"
            self.clip_badge.configure(text=f"普通文字：{shown}", fg=MUTED)
            self._set_status("剪切板是普通文字 · 按 Ctrl+B 或点「→ 转二进制」", MUTED, 5000)

    def _write_clip(self, text: str):
        """程序自己写剪切板时登记，避免被自己的轮询再次识别。"""
        if clipwin.write_text(text):
            self._last_clip = text
            self._last_seq = clipwin.sequence()
            return True
        self._set_status("写入剪切板失败，请稍后重试", ERR)
        return False

    # ---------- 按钮行为 ----------
    def _copy_any(self, text: str):
        """文本框 Ctrl+C / 右键统一走这里，写的是真·系统剪切板。"""
        if self._write_clip(text):
            self._set_status("已复制到剪切板", OK)

    def _copy_result(self):
        text = self.result.get("1.0", "end-1c")
        if not text.strip() or text.strip() == PLACEHOLDER_RESULT.strip():
            self._set_status("没有可复制的译文", WARN)
            return
        self._copy_any(text)

    def _clear_result(self):
        self.result.configure(fg="#5b6579")
        self.result.set_text(PLACEHOLDER_RESULT)
        self.clip_badge.configure(text="等待 0/1 串", fg=MUTED)
        if self._collapsed:
            self.title_lbl.configure(text="二进制悬浮窗")

    def _clip_to_bin(self):
        text = self._last_clip
        if not text:
            text = clipwin.read_text()
        if not text or not text.strip():
            self._set_status("剪切板没有内容", WARN)
            return
        self._fill_manual(text)
        self._set_status("已把剪切板文字转成二进制", OK)

    def _paste_text(self):
        text = clipwin.read_text()
        if not text:
            self._set_status("剪切板没有文字", WARN)
            return
        self._fill_manual(text)

    def _fill_manual(self, text: str):
        self.entry.delete(0, "end")
        self.entry.insert(0, text.replace("\n", " "))
        self._on_input()

    def _on_input(self):
        text = self.input_var.get()
        if not text:
            self.bin_out.set_text("")
            self.len_badge.configure(text="")
            return
        out = core.text_to_binary(text, sep="" if self.compact else " ")
        self.bin_out.set_text(out)
        self.len_badge.configure(text=core.describe(text))

    def _copy_binary(self):
        bits = self.bin_out.get("1.0", "end-1c").strip()
        if not bits:
            self._set_status("先输入文字", WARN)
            return
        self._copy_any(bits)

    def _clear_manual(self):
        self.entry.delete(0, "end")
        self.bin_out.set_text("")
        self.len_badge.configure(text="")
        self.entry.focus_set()


def main():
    if clipwin.IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = tk.Tk()
    app = BinFloater(root)
    app.pin_btn.configure(fg=TEAL if app.topmost else MUTED,
                          text="◉" if app.topmost else "○")
    root.mainloop()


if __name__ == "__main__":
    main()
