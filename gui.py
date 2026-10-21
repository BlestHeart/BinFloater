# -*- coding: utf-8 -*-
"""BIN 二进制悬浮窗 —— 无边框、置顶、可拖动。

功能：
  1. 监视剪切板，检测到 0/1 字符串自动翻译成文字；
  2. 内置输入框，把输入的文字实时转成二进制并可一键复制。
"""
import json
import os
import sys
import tkinter as tk
import tkinter.font as tkfont

import clipwin
import core

APP_NAME = "BinFloater"
W_WIDTH = 404
W_HEIGHT = 468  # 兜底值，实际启动后按内容自适应
COLLAPSED_H = 52
PLACEHOLDER_RESULT = "复制一段 01001000 01101001 ……\n这里会自动翻译成 Hi"

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


def load_config() -> dict:
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(data: dict) -> None:
    try:
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
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

        self._init_window()
        self._init_fonts()
        self._build()
        self._restore_geometry()
        self._bind_keys()
        # 首帧拿到窗口句柄后再切圆角
        self.root.after(30, self._apply_round)
        self.root.after(200, self._poll)
        self._set_status("正在监视剪切板…", TEAL)

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
        try:
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            save_config(self.cfg)
            self.root.destroy()
        except Exception:
            pass

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
    def _poll(self):
        if not self._alive:
            return
        try:
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
