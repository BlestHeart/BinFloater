# -*- coding: utf-8 -*-
"""BIN 二进制悬浮窗 —— 无边框、置顶、可拖动。

功能：
  1. 监视剪切板，检测到 0/1 字符串自动翻译成文字；
  2. 内置输入框，把输入的文字实时转成二进制并可一键复制。
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog

import clipwin
import core
import hotkey as hotkey_mod
import keys
import tray
import trigger
import update as update_mod
import version

APP_NAME = "BinFloater"
W_WIDTH = 404
# 宽度可调范围。太窄内容会换行挤成一团，太宽就成了一块盖住半个屏幕的板子。
MIN_WIDTH = 340
MAX_WIDTH = 760
W_HEIGHT = 468  # 兜底值，实际启动后按内容自适应
COLLAPSED_H = 52
PLACEHOLDER_RESULT = "复制一段 01001000 01101001 ……\n这里会自动翻译成 Hi"
# 输入框最多塞多少字。Entry.insert 是超线性的（实测 5 千字 0.44 秒、
# 2 万字 5.9 秒、5 万字 36 秒），不设上限的话拖个大文本进来就假死。
MAX_ENTRY_CHARS = 2000

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


# 配置目录算一次就够。save_config 每次落盘都要问一次路径，而下面判断可写是
# 真去写个探针文件的，每次都做太浪费。
_CONFIG_DIR = None


def _exe_dir() -> str:
    """程序所在目录。打包成 exe 时是 exe 旁边，从源码跑时是项目目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _dir_writable(folder: str) -> bool:
    """真写一个文件试试。别用 os.access，它在 Windows 上只看只读属性，
    装在 Program Files 这种要提权的目录时照样返回 True。"""
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write-probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except Exception:
        return False


def config_dir() -> str:
    """配置放哪个目录。

    优先级：
      1. 环境变量 BINF_CONFIG_DIR —— 显式指定，最高优先（测试也用它隔离）；
      2. 打包成 exe 时，放在 exe 旁边，也就是安装目录。装到 D 盘配置就留在 D 盘，
         不往 C 盘的 %APPDATA% 里丢东西；
      3. 从源码跑、或者程序目录不可写（比如装到了要管理员权限的地方），
         退回 %APPDATA%\\BinFloater。这是兜底，不是首选。
    """
    global _CONFIG_DIR
    if _CONFIG_DIR:
        return _CONFIG_DIR

    override = os.environ.get("BINF_CONFIG_DIR")
    if override:
        folder = override
    elif getattr(sys, "frozen", False) and _dir_writable(_exe_dir()):
        folder = _exe_dir()
    else:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, APP_NAME)

    os.makedirs(folder, exist_ok=True)
    _CONFIG_DIR = folder
    return folder


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def _legacy_config_path() -> str:
    """老版本放配置的地方（%APPDATA%\\BinFloater\\config.json）。

    1.4 起配置改成跟着安装目录走。老用户升级上来配置还在 C 盘，得搬一次，
    否则看到的就是「设置全没了」。
    """
    base = os.environ.get("APPDATA")
    if not base:
        return ""
    return os.path.join(base, APP_NAME, "config.json")


def _migrate_legacy_config() -> None:
    """把老位置的配置搬到新位置。只在「新位置没有、老位置有」时搬一次，
    老文件留着不动（万一用户想退回去用旧版本）。"""
    new = config_path()
    old = _legacy_config_path()
    if not old or old == new or os.path.exists(new) or not os.path.isfile(old):
        return
    try:
        shutil.copyfile(old, new)
        _log("已把老配置从 %s 搬到 %s" % (old, new))
    except Exception as exc:
        _log("搬老配置失败: %r" % (exc,))


def _log(msg: str) -> None:
    """写一行诊断日志。配置读写出问题时，没有日志就只能靠猜。"""
    try:
        path = os.path.join(os.path.dirname(config_path()), "error.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_config() -> dict:
    _migrate_legacy_config()
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


def _as_float(value, default: float, lo=None, hi=None) -> float:
    """把配置里的值转成浮点数，坏了就用默认值。

    配置是纯文本文件，用户手改、旧版本遗留、写到一半断电都可能留下奇怪的值。
    早先这里是裸的类型转换，一个 alpha:"abc" 就让程序在建窗口时抛 ValueError，
    双击 exe 毫无反应 —— 用户根本不知道发生了什么。
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    if lo is not None and out < lo:
        return lo
    if hi is not None and out > hi:
        return hi
    return out


def _as_int(value, default: int, lo=None, hi=None) -> int:
    """把配置里的值转成整数，坏了就用默认值。理由同 _as_float。"""
    try:
        # 先过 float：字符串 "3.0" 直接 int() 会炸，但用户手写出来很常见
        out = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    if lo is not None and out < lo:
        return lo
    if hi is not None and out > hi:
        return hi
    return out


def _as_bool(value, default: bool) -> bool:
    """把配置里的值转成布尔。

    不用 bool(value)：字符串 "false" 在 Python 里是真值，用户手改配置写成
    "false" 反而会打开开关，跟直觉相反。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off", ""):
            return False
    return default


def _read_text_file(path: str):
    """把文件当文本读出来；不像文本就返回 None（交给二进制分支）。

    不能只靠「解码没报错」判断是不是文本：gbk 的编码空间几乎覆盖所有字节
    序列，随便一个 png 都能"成功"解出一堆乱码汉字。之前就是这样——拖 png
    进去得到一串无意义汉字，再编码回来的字节自然和原文件不一样。
    所以两道关：含 NUL 一律当二进制；解出来控制字符（换行制表符除外）
    超过 2% 也当二进制。
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if not data:
        return None
    if b"\x00" in data:
        return None
    for enc in ("utf-8", "gbk"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        bad = sum(1 for ch in text
                  if ch < " " and ch not in "\t\n\r" or ch == "\x7f")
        if text and bad * 50 > len(text):
            continue
        return text
    return None


def save_config(data: dict) -> bool:
    """先写临时文件再原子替换。返回是否写成功。

    直接以 "w" 打开原文件会先把它截断，万一进程在写入中途被结束（用户杀进程、
    崩溃、安全软件拦截写盘），留下的就是一个空文件——下次启动所有设置回到
    默认值，用户会以为是「没记住」。os.replace 在 Windows 上也是原子的：
    要么是旧内容，要么是新内容，不会出现写了一半的状态。

    返回布尔值是为了让界面能提示「设置没存上」——之前只写日志，用户完全
    不知道自己的改动丢了。
    """
    path = config_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except Exception as exc:
        _log("save_config 失败: %r / 数据: %r" % (exc, data))
        try:
            os.remove(tmp)
        except Exception:
            pass
        return False


def _set_text(widget: tk.Text, value: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    widget.configure(state="disabled")


def screen_bounds(widget) -> tuple:
    """返回虚拟桌面的 (left, top, width, height)，单位像素。

    多显示器时副屏可能在主屏左边或上边，那时 left/top 是负数。Tk 的
    winfo_screenwidth() 只给主屏尺寸，拿它算位置会把副屏上的窗口拽回主屏。
    这里优先问 Win32 的 SM_XVIRTUALSCREEN 那一套；拿不到就退回 Tk 的值
    （单屏情况下两者一致）。
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(76))    # SM_XVIRTUALSCREEN
        top = int(user32.GetSystemMetrics(77))     # SM_YVIRTUALSCREEN
        width = int(user32.GetSystemMetrics(78))   # SM_CXVIRTUALSCREEN
        height = int(user32.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return left, top, width, height
    except Exception:
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


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
        self._flash_after = None
        self._status_reset_after = None
        # 记住上次设的状态文案和颜色，重复设同一个就跳过重绘
        self._status_text = ""
        self._status_color = None
        self.topmost = _as_bool(self.cfg.get("topmost", True), True)
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
        self._tray_hint_shown = _as_bool(self.cfg.get("tray_hint_shown", False), False)
        # 锁定译文：锁上之后剪切板再变也不覆盖正文，避免用户正在读的内容被冲掉
        self._result_locked = False
        # 锁着的时候剪切板变过几次，解锁时提示一下
        self._pending_clip = None
        # 第二个实例启动时用它请求唤回窗口（收进托盘后双击 exe 才有反应）
        self._waker = None
        # 折叠状态记在配置里，启动时按上次的样子来
        self._collapsed = _as_bool(self.cfg.get("collapsed", False), False)
        # 窗口位置标脏：拖动后不立刻写盘，由 _poll 定时落盘（强杀也不丢位置）
        self._pos_dirty = False
        self._last_saved_pos = None
        # 剪切板监视暂停开关
        self._clip_paused = _as_bool(self.cfg.get("clip_paused", False), False)
        # 转换历史（最近若干条），存内存不落盘，进程关掉就清空
        self._history = []
        # 当前编码格式与分隔符
        self.fmt = self.cfg.get("format", "binary")
        if self.fmt not in core.FORMATS:
            self.fmt = "binary"
        self.sep = self.cfg.get("separator", "space")
        if self.sep not in core.SEPARATORS:
            self.sep = "space"
        # 钩子回调在系统线程里跑，绝对不能直接碰 tkinter（会崩）。
        # 回调只往队列里塞一个事件，主线程用 after 轮询取。
        self._triggers = queue.Queue()
        # 拖放进来的文件路径，同样由窗口过程入队、主线程取
        self._drops = queue.Queue()
        # 检查更新：网络请求在后台线程，结果塞这个队列由主线程取。
        # 钩子回调不能碰 tkinter 同理，网络线程也不能碰——Tk 不是线程安全的。
        self._updates = queue.Queue()
        self._update_checking = False
        self._update_result = None
        self._update_btn = None
        self._update_lbl = None
        # 上次自动检查的日期（YYYY-MM-DD）。一天只查一次，不然每启动一次
        # 都往 api.github.com 发请求，匿名限流 60 次/小时很快就用完。
        self._update_last_day = str(self.cfg.get("update_last_day", ""))
        # 应用内下载安装包。两个队列分开：进度会来几十上百次，结果只来一次，
        # 混在一起每次取都要判类型，没必要。
        self._update_progress_queue = queue.Queue()
        self._update_dl_queue = queue.Queue()
        self._update_downloading = False
        self._update_cancel = False
        self._update_got = 0
        self._update_total = 0
        # 下载完成后的安装包路径。有值就意味着按钮该显示「安装」。
        self._update_ready_path = ""
        self._watcher = None
        # 用户最近用的那个窗口。面板按钮触发时要先把焦点还回去。
        self._last_foreign_hwnd = 0

        self._init_window()
        self._init_fonts()
        # 窗口宽度可调：默认窄条贴在屏幕边上，宽一点能多放候选。
        # 必须在 _init_window 之后、_build 之前定下来，标题栏文案靠它排版。
        self.width = _as_int(self.cfg.get("width", W_WIDTH), W_WIDTH,
                             MIN_WIDTH, MAX_WIDTH)
        self._build()
        self._restore_geometry()
        self._bind_keys()
        # 上次是折起来的，这次也折着起来。放在 _restore_geometry 之后，
        # 因为折叠会把窗口高度改成 COLLAPSED_H，得先量出正常高度再折。
        if self._collapsed:
            self._set_collapsed(True)
        # 首帧拿到窗口句柄后再切圆角
        self.root.after(30, self._apply_round)
        # 圆角之后才能子类化（要先有稳定的 hwnd）
        self.root.after(60, self._setup_drop)
        self.root.after(180, self._drain_drops)
        self.root.after(200, self._poll)
        self.root.after(120, self._drain_triggers)
        self.root.after(120, self._drain_tray_events)
        self.root.after(200, self._drain_updates)
        # 透明度/宽度滑块只在松手时落盘，拖动中每次回调都写盘没必要
        try:
            self.alpha_scale.bind("<ButtonRelease-1>", self._on_alpha_release)
        except (AttributeError, tk.TclError):
            pass
        try:
            self.width_scale.bind("<ButtonRelease-1>", self._on_width_release)
        except (AttributeError, tk.TclError):
            pass
        self._apply_trigger_mode(silent=True)
        self._start_tray()
        # 唤醒事件要在托盘建好之后才有意义（没有托盘就没有「收起来叫不回」的问题）
        self._start_waker()
        # 启动时把恢复出来的设置念一遍。用户抱怨过「每次打开都要重新设」，
        # 状态栏直接写清楚这次恢复成了什么，一眼就能确认到底记没记住。
        self._set_status(self._boot_text(), TEAL, 3200)
        # 第一次用的人不知道那行「已恢复：Ctrl+Alt+B」是干嘛的，得说清楚
        self.root.after(600, self._maybe_first_run)
        # 自动检查更新放在最后：先把界面和托盘都建好，查到结果才有地方显示。
        # 延后 3 秒再发网络请求，不要跟启动时的剪切板读取抢时间。
        self.root.after(3000, self._maybe_auto_check_update)

    def _maybe_first_run(self):
        """首次运行提示：只在从没成功显示过时弹一次。

        判断依据是配置里有没有 first_run_done。用配置而不是「配置文件存在」，
        因为位置之类的设置也会写配置，装完随手拖一下窗口就再也不会提示了。
        """
        if _as_bool(self.cfg.get("first_run_done", False), False):
            return
        self.cfg["first_run_done"] = True
        self._save()
        self._set_status("第一次用：选中文字后按 %s 就地转换；也可以把剪切板里的内容转过来"
                         % self.hotkey.text(), TEAL, 9000)

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
        r.attributes("-alpha", _as_float(self.cfg.get("alpha", 1.0), 1.0, 0.2, 1.0))
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
        """先让 Tk 量出内容实际高度，再按内容锁定窗口大小，避免底部被截断。

        坐标用**虚拟桌面**范围（所有显示器拼起来的那一大块），不是主屏范围。
        Windows 上副屏在主屏左边时 x 是负数，早先用 `max(0, ...)` 夹一下
        就把窗口从副屏拽回主屏了 —— 用户上次放在副屏，下次启动突然跑到主屏。
        """
        self.root.geometry(f"{self.width}x{W_HEIGHT}")
        self.root.update_idletasks()
        # 不能直接取 shell.winfo_reqheight()：body 装在 Canvas 里，而 Canvas
        # 不会把内部内容请求的高度往上传，量出来会小一大截，窗口会缩成一条。
        # 做法是先用内容高度粗算一个值，量出画布还差多少，再精确补上。
        body_h = self.body.winfo_reqheight()
        self.root.geometry(f"{self.width}x{max(300, body_h + 120)}")
        self.root.update_idletasks()
        short = body_h - self.canvas.winfo_height()
        height = max(300, self.root.winfo_height() + max(0, short))
        _left, _top, _vw, _vh = screen_bounds(self.root)
        # 封顶到屏幕高度的 90%：内容现在 900 出头，1080p 屏刚好放得下；
        # 1366x768 的笔记本按 90% 算 691，放不下就滚着看（见 _build 的 canvas），
        # 总比把最下面几行按钮顶到屏幕外点不到好。
        cap = max(320, int(_vh * 0.90))
        height = min(height, cap)
        self.height = height
        left, top, vw, vh = screen_bounds(self.root)
        x = _as_int(self.cfg.get("x"), left + vw - self.width - 40)
        y = _as_int(self.cfg.get("y"), top + 90)
        # 夹到虚拟桌面范围内：太靠外会跑到屏幕之外点不到
        x = max(left, min(x, left + vw - self.width))
        y = max(top, min(y, top + vh - height))
        self.root.geometry(f"{self.width}x{height}+{x}+{y}")
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

    def _setup_drop(self):
        """接管窗口过程，接收拖进来的文件。

        Tkinter 不处理 WM_DROPFILES，只能自己子类化窗口过程：SetWindowLongPtr
        换掉原来的 WndProc，新过程里看到 WM_DROPFILES 就取路径入队，其余消息
        一律转给原过程。原过程句柄必须存住，否则被 GC 掉就成了野指针（会直接崩）。
        """
        if not clipwin.IS_WINDOWS:
            return
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            # 64 位要用 SetWindowLongPtrW
            is64 = ctypes.sizeof(ctypes.c_void_p) == 8
            setter = u.SetWindowLongPtrW if is64 else u.SetWindowLongW
            setter.restype = ctypes.c_void_p
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            u.CallWindowProcW.restype = ctypes.c_void_p
            u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                          wintypes.UINT, wintypes.WPARAM,
                                          wintypes.LPARAM]

            self.root.update_idletasks()
            hwnd = int(self.root.wm_frame(), 16)
            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, wintypes.HWND,
                                         wintypes.UINT, wintypes.WPARAM,
                                         wintypes.LPARAM)
            GWLP_WNDPROC = -4
            old = setter(hwnd, GWLP_WNDPROC, None)
            self._old_wndproc = old

            def _proc(h, msg, wp, lp):
                if msg == clipwin.WM_DROPFILES:
                    try:
                        paths = clipwin.read_dropped_files(wp)
                        if paths:
                            # 窗口过程跑在消息循环里，不能直接动 Tk 控件
                            self._drops.put_nowait(paths)
                    except Exception:
                        pass
                    return 0
                return u.CallWindowProcW(old, h, msg, wp, lp)

            # 必须存成实例属性，否则回调对象被回收后系统还会回调它
            self._wndproc = WNDPROC(_proc)
            setter(hwnd, GWLP_WNDPROC,
                   ctypes.cast(self._wndproc, ctypes.c_void_p))
            # DragAcceptFiles 在 shell32 里，不是 user32
            clipwin.accept_dropped_files(hwnd)
            self._drop_hwnd = hwnd
        except Exception:
            self._drop_hwnd = 0

    def _drain_drops(self):
        """主线程：把窗口过程收到的拖放路径取出来处理。"""
        if not self._alive:
            return
        try:
            while True:
                paths = self._drops.get_nowait()
                for path in paths:
                    self._load_dropped(path)
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(120, self._drain_drops)
        except tk.TclError:
            pass

    def _load_dropped(self, path):
        """把拖进来的文件读进输入框。

        文本文件走编码；图片之类读不了就当二进制文件处理（把内容编成 0/1 串），
        不然用户拖个 png 进来只会得到一句「读不了」。
        """
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            self._set_status("读不了这个文件：%s" % exc, ERR, 6000)
            return
        if os.path.isdir(path):
            self._set_status("拖的是文件夹，只能拖单个文件进来", WARN, 5000)
            return
        if size > 8 * 1024 * 1024:
            self._set_status("文件太大（%d MB），最多 8MB" % (size // (1024 * 1024)),
                             WARN, 6000)
            return
        text = _read_text_file(path)
        if text is not None:
            # 太长的话 _fill_manual 会走旁路（直接转好放结果区），
            # 它自己已经给过提示，这里别再覆盖成「已读入（文本）」，
            # 不然用户会以为没读到东西。
            if not self._fill_manual(text):
                self._set_status("已读入 %s（文本）" % os.path.basename(path),
                                 OK, 5000)
            return
        # 不是文本，当二进制文件：内容 -> 0/1 串。
        # 这一支要另外卡一道上限：每字节会变成 9 个字符（8 位 + 1 空格），
        # 8MB 的文件就是 75MB 的字符串塞进 Text 控件，界面会彻底卡死
        # （实测 1MB 已经要 0.64 秒，只算生成时间）。
        if size > 512 * 1024:
            self._set_status(
                "这个文件不是文本，转成 0/1 串会太长（%d KB），最多 512KB"
                % (size // 1024), WARN, 6000)
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            self._set_status("读不了这个文件：%s" % exc, ERR, 6000)
            return
        self.entry.delete(0, "end")
        self.input_var.set("")
        self.bin_out.set_text(
            core.apply_separator(core.bytes_to_binary(data), self.sep))
        # 别走 _on_input：那会把输入框的空内容再编一遍，把刚写进去的结果清掉
        self.len_badge.configure(text="%d 字节" % len(data))
        self._set_status("已把 %s 的字节编成 0/1 串（%d 字节）"
                         % (os.path.basename(path), len(data)), OK, 6000)

    # ---------- 界面 ----------
    def _build(self):
        root = self.root
        self.shell = tk.Frame(root, bg=BG)
        self.shell.pack(fill="both", expand=True)
        self._draggable(self.shell)

        self._build_header(self.shell)

        # 卡片越加越多，全摊开会比 1080p 屏还高。用一个可滚动容器装起来：
        # 窗口高度封顶到屏幕的 82%，超出的部分滚着看。
        wrap = tk.Frame(self.shell, bg=BG)
        wrap.pack(fill="both", expand=True)
        self.scroll_wrap = wrap
        self.scrollbar = tk.Scrollbar(wrap, orient="vertical", width=6,
                                      bg=CARD_HI, troughcolor=BG,
                                      activebackground=STROKE,
                                      highlightthickness=0, bd=0,
                                      relief="flat")
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0,
                                yscrollcommand=self.scrollbar.set)
        self.scrollbar.configure(command=self.canvas.yview)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        self._body_win = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw")
        self._draggable(self.body)

        def _sync_scroll(_event=None):
            """内容变高/变矮时同步可滚范围，并把画布宽度撑满。"""
            try:
                self.canvas.configure(scrollregion=self.canvas.bbox("all"))
                self.canvas.itemconfigure(self._body_win,
                                          width=self.canvas.winfo_width())
                need = self.body.winfo_reqheight()
                avail = self.canvas.winfo_height()
                if need > avail and avail > 1:
                    if not self.scrollbar.winfo_ismapped():
                        self.scrollbar.pack(side="right", fill="y")
                elif self.scrollbar.winfo_ismapped():
                    self.scrollbar.pack_forget()
            except tk.TclError:
                pass

        self.body.bind("<Configure>", _sync_scroll)
        self.canvas.bind("<Configure>", _sync_scroll)
        # 鼠标滚轮只在指针落在窗口上时才滚，别抢别处的滚轮
        for widget in (self.canvas, self.body):
            widget.bind("<Enter>", lambda e: self._bind_wheel())
            widget.bind("<Leave>", lambda e: self._unbind_wheel())
        self._scroll_sync = _sync_scroll

        self._build_clip_card(self.body)
        self._build_manual_card(self.body)
        self._build_convert_card(self.body)
        self._build_history_card(self.body)
        self._build_settings_card(self.body)

        self.status = tk.Label(self.shell, text="", bg=BG, fg=MUTED,
                               font=self.font_meta, anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 8))

    def _bind_wheel(self):
        self.root.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self):
        self.root.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        """滚轮翻内容。Windows 上一次滚动 delta 是 120 的倍数。"""
        try:
            if self.scrollbar.winfo_ismapped():
                self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    def _draggable(self, widget):
        """让空白区域也能拖动窗口（输入框/按钮自己绑定了点击，不会被覆盖）。"""
        widget.bind("<Button-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        widget.bind("<ButtonRelease-1>", self._drag_end)
        widget.bind("<Double-Button-1>", self._guard_text(lambda: self._toggle_collapse()))
        return widget

    def _build_header(self, parent):
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=12, pady=(10, 8))
        head.bind("<Button-1>", self._drag_start)
        head.bind("<B1-Motion>", self._drag_move)
        head.bind("<ButtonRelease-1>", self._drag_end)
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
        title.bind("<ButtonRelease-1>", self._drag_end)
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
        # 暂停开关：处理敏感内容时不想让程序一直读剪切板
        self.pause_btn = tk.Label(top, text="", bg=CARD, fg=MUTED,
                                  font=self.font_meta, padx=6, cursor="hand2")
        self.pause_btn.pack(side="right")
        self.pause_btn.bind("<Button-1>", lambda e: self._toggle_clip_pause())
        self.pause_btn.bind("<Enter>", lambda e: self.pause_btn.configure(fg=ACCENT))
        self.pause_btn.bind("<Leave>", lambda e: self._paint_pause_btn())
        self._paint_pause_btn()
        self.clip_badge = tk.Label(top, text="等待 0/1 串", bg=CARD, fg=MUTED,
                                   font=self.font_meta)
        self.clip_badge.pack(side="right", padx=(0, 8))

        box = tk.Frame(card, bg=CARD_HI)
        box.pack(fill="x", padx=12)
        self.result = ReadonlyText(
            box, height=4, wrap="char", bg=CARD_HI, fg=TEXT,
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
        rows.pack(fill="x", padx=12, pady=(8, 4))
        self._btn(rows, "复制译文", self._copy_result, primary=True).pack(side="left")
        self._btn(rows, "→ 转编码", self._clip_to_bin).pack(side="left", padx=6)
        # 反向：把当前译文当原文再转回去。之前想往返一次得先复制再粘贴，两步
        self._btn(rows, "⇄ 反向", self._reverse_result).pack(side="left")
        self._btn(rows, "清空", self._clear_result).pack(side="left", padx=6)

        # 锁定 / 自动回写单独一行：四个按钮加上它俩挤在一行，
        # 404 宽的窗口里「自动回写」会被压成两行，很难看
        opts = tk.Frame(card, bg=CARD)
        opts.pack(fill="x", padx=12, pady=(0, 10))
        # 锁：剪切板一变就把译文冲掉，用户正在读的东西当场消失。锁上之后
        # 只更新角标提示「剪切板变了」，正文不动，想换再看就解锁。
        self.lock_btn = tk.Label(opts, text="锁定", bg=CARD, fg=MUTED,
                                 font=self.font_meta, cursor="hand2")
        self.lock_btn.pack(side="left")
        self.lock_btn.bind("<Button-1>", lambda e: self._toggle_result_lock())
        self.lock_btn.bind("<Enter>", lambda e: self.lock_btn.configure(fg=ACCENT))
        self.lock_btn.bind("<Leave>", lambda e: self._paint_lock_btn())
        self._paint_lock_btn()
        self.auto_btn = tk.Label(opts, text="", bg=CARD, fg=MUTED,
                                 font=self.font_meta, cursor="hand2")
        self.auto_btn.pack(side="right")
        self.auto_btn.bind("<Button-1>", lambda e: self._toggle_auto_copy())
        self.auto_btn.bind("<Enter>", lambda e: self.auto_btn.configure(fg=ACCENT))
        self.auto_btn.bind("<Leave>", lambda e: self.auto_btn.configure(fg=MUTED))
        self._paint_auto_btn()

    def _build_manual_card(self, parent):
        card = self._card(parent, pady=(0, 8))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(top, text="文字 → 编码", bg=CARD, fg=TEXT,
                 font=self.font_title).pack(side="left")
        self.len_badge = tk.Label(top, text="", bg=CARD, fg=MUTED, font=self.font_meta)
        self.len_badge.pack(side="right")

        # 编码格式 + 分隔符行。放在标题下面单独一行：之前只有「紧凑」一个开关，
        # 想换个格式（十六进制/Base64/摩斯）根本没地方点。
        fmtrow = tk.Frame(card, bg=CARD)
        fmtrow.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(fmtrow, text="格式", bg=CARD, fg=MUTED,
                 font=self.font_meta).pack(side="left")
        self.fmt_btn = tk.Label(fmtrow, text="", bg=CARD_HI, fg=TEAL,
                                font=self.font_meta, padx=8, pady=2,
                                cursor="hand2")
        self.fmt_btn.pack(side="left", padx=(8, 0))
        self.fmt_btn.bind("<Button-1>", lambda e: self._cycle_format())
        self.fmt_btn.bind("<Enter>", lambda e: self.fmt_btn.configure(bg=STROKE))
        self.fmt_btn.bind("<Leave>", lambda e: self.fmt_btn.configure(bg=CARD_HI))
        self.sep_btn = tk.Label(fmtrow, text="", bg=CARD_HI, fg=MUTED,
                                font=self.font_meta, padx=8, pady=2,
                                cursor="hand2")
        self.sep_btn.pack(side="left", padx=(6, 0))
        self.sep_btn.bind("<Button-1>", lambda e: self._cycle_separator())
        self.sep_btn.bind("<Enter>", lambda e: self.sep_btn.configure(bg=STROKE))
        self.sep_btn.bind("<Leave>", lambda e: self.sep_btn.configure(bg=CARD_HI))
        self._paint_format_btn()
        self._paint_separator_btn()
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
        rows.pack(fill="x", padx=12, pady=(8, 4))
        self.copy_btn = self._btn(rows, "复制编码", self._copy_binary, primary=True)
        self.copy_btn.pack(side="left")
        self._btn(rows, "← 从剪切板", self._paste_text).pack(side="left", padx=6)
        self._btn(rows, "清空", self._clear_manual).pack(side="left")
        self._hint = tk.Label(rows, text="Enter 复制", bg=CARD, fg="#5b6579",
                              font=self.font_meta)
        self._hint.pack(side="right")

        # 第二行：多行批量 / 从文件读 / 导出 / 解码为文件
        # 挤在一行会爆（窗口只有 404 宽），所以「解码为文件」另起一行
        rows2 = tk.Frame(card, bg=CARD)
        rows2.pack(fill="x", padx=12, pady=(0, 8))
        self.batch_btn = self._btn(rows2, "多行批量", self._batch_convert)
        self.batch_btn.pack(side="left")
        self._btn(rows2, "从文件读", self._load_from_file).pack(side="left", padx=6)
        self._btn(rows2, "导出结果", self._export_result).pack(side="left")
        self.batch_hint = tk.Label(rows2, text="", bg=CARD, fg="#5b6579",
                                   font=self.font_meta)
        self.batch_hint.pack(side="right")

        # 第三行：把结果区的 0/1 串解成字节存文件（还原图片/压缩包用）
        rows3 = tk.Frame(card, bg=CARD)
        rows3.pack(fill="x", padx=12, pady=(0, 8))
        self._btn(rows3, "解码为文件", self._decode_to_file).pack(side="left")
        tk.Label(rows3, text="把 0/1 串还原成字节存盘",
                 bg=CARD, fg="#5b6579", font=self.font_meta).pack(side="right")

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
        self._btn(rows, "转换选中", self._convert_selection_now).pack(side="left",
                                                                   padx=(6, 0))
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
        keyrow.pack(fill="x", padx=12, pady=(0, 8))
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
        spacerow.pack(fill="x", padx=12, pady=(0, 8))
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

    def _build_history_card(self, parent):
        """转换历史。

        只放最近 6 条：这东西是「刚才那版是什么来着」的后悔药，不是日志。
        点一下就复制，不用再去结果区全选。
        """
        card = self._card(parent, pady=(0, 8))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="转换历史", bg=CARD, fg=TEXT,
                 font=self.font_title).pack(side="left")
        self.history_btn = tk.Label(top, text="清空", bg=CARD, fg=MUTED,
                                    font=self.font_meta, padx=6, cursor="hand2")
        self.history_btn.pack(side="right")
        self.history_btn.bind("<Button-1>", lambda e: self._clear_history())
        self.history_btn.bind("<Enter>",
                              lambda e: self.history_btn.configure(fg=ACCENT))
        self.history_btn.bind("<Leave>",
                              lambda e: self.history_btn.configure(fg=MUTED))

        self.history_box = tk.Frame(card, bg=CARD)
        self.history_box.pack(fill="x", padx=12, pady=(0, 8))
        self._paint_history()

    def _clear_history(self):
        self._history = []
        self._paint_history()
        self._set_status("转换历史已清空", MUTED, 3000)

    def _build_settings_card(self, parent):
        """设置：透明度滑块 + 恢复默认。

        透明度原来是标题栏那个 ◐ 按钮循环四档，用户得点开才知道现在几档、
        点几下能到自己想要的值。换成滑块直接拖，拖到哪就是哪。
        """
        card = self._card(parent, pady=(0, 8))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="设置", bg=CARD, fg=TEXT,
                 font=self.font_title).pack(side="left")
        self.reset_btn = tk.Label(top, text="恢复默认", bg=CARD, fg=MUTED,
                                  font=self.font_meta, padx=6, cursor="hand2")
        self.reset_btn.pack(side="right")
        self.reset_btn.bind("<Button-1>", lambda e: self._reset_defaults())
        self.reset_btn.bind("<Enter>",
                            lambda e: self.reset_btn.configure(fg=WARN))
        self.reset_btn.bind("<Leave>",
                            lambda e: self.reset_btn.configure(fg=MUTED))

        arow = tk.Frame(card, bg=CARD)
        arow.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(arow, text="不透明度", bg=CARD, fg=MUTED,
                 font=self.font_meta, width=6, anchor="w").pack(side="left")
        cur_alpha = _as_float(self.cfg.get("alpha", 1.0), 1.0, 0.2, 1.0)
        self.alpha_var = tk.DoubleVar(value=cur_alpha * 100)
        self.alpha_scale = tk.Scale(
            arow, from_=25, to=100, orient="horizontal",
            variable=self.alpha_var, showvalue=False,
            bg=CARD, fg=MUTED, troughcolor=CARD_HI, highlightthickness=0,
            bd=0, sliderrelief="flat", activebackground=ACCENT,
            length=180, command=self._on_alpha_slide,
        )
        self.alpha_scale.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.alpha_lbl = tk.Label(arow, text="%d%%" % int(cur_alpha * 100),
                                  bg=CARD, fg=TEAL, font=self.font_meta,
                                  width=4, anchor="e")
        self.alpha_lbl.pack(side="right")

        # 宽度滑块：默认 404 是照最长的按钮行定的，屏幕宽的人会觉得窄，
        # 想一屏多看几条译文就只能忍。拖动实时改，松手才落盘。
        wrow = tk.Frame(card, bg=CARD)
        wrow.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(wrow, text="宽度", bg=CARD, fg=MUTED,
                 font=self.font_meta, width=6, anchor="w").pack(side="left")
        self.width_var = tk.DoubleVar(value=self.width)
        self.width_scale = tk.Scale(
            wrow, from_=MIN_WIDTH, to=MAX_WIDTH, orient="horizontal",
            variable=self.width_var, showvalue=False,
            bg=CARD, fg=MUTED, troughcolor=CARD_HI, highlightthickness=0,
            bd=0, sliderrelief="flat", activebackground=ACCENT,
            length=180, command=self._on_width_slide,
        )
        self.width_scale.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.width_lbl = tk.Label(wrow, text="%dpx" % self.width,
                                  bg=CARD, fg=TEAL, font=self.font_meta,
                                  width=6, anchor="e")
        self.width_lbl.pack(side="right")

        # 配置文件放哪儿：装到 D 盘时它就在 D 盘，不会跑到 C 盘的 %APPDATA%。
        # 一行小字让用户能自己确认，不然「设置存哪了」永远是个谜。
        frow = tk.Frame(card, bg=CARD)
        frow.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(frow, text="配置", bg=CARD, fg=MUTED,
                 font=self.font_meta, width=6, anchor="w").pack(side="left")
        self.cfg_path_lbl = tk.Label(
            frow, text=config_dir(), bg=CARD, fg=MUTED,
            font=self.font_meta, anchor="w", justify="left",
            cursor="hand2",
        )
        self.cfg_path_lbl.pack(side="left", fill="x", expand=True, padx=(4, 0))
        # 点一下直接在资源管理器里打开，省得用户自己对着路径一级级找
        self.cfg_path_lbl.bind("<Button-1>", lambda e: self._open_config_dir())
        self.cfg_path_lbl.bind("<Enter>",
                               lambda e: self.cfg_path_lbl.configure(fg=ACCENT))
        self.cfg_path_lbl.bind("<Leave>",
                               lambda e: self.cfg_path_lbl.configure(fg=MUTED))

        # 版本与更新。用户提的「能不能做自动检查更新」就落在这一行：
        # 左边显示当前版本 + 检查结果，右边一个按钮手动再查一次。
        vrow = tk.Frame(card, bg=CARD)
        vrow.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(vrow, text="版本", bg=CARD, fg=MUTED,
                 font=self.font_meta, width=6, anchor="w").pack(side="left")
        self._update_btn = tk.Label(
            vrow, text="检查更新", bg=CARD_HI, fg=MUTED,
            font=self.font_meta, padx=8, pady=2, cursor="hand2",
        )
        self._update_btn.pack(side="right")
        self._update_btn.bind("<Button-1>", lambda e: self._check_update(manual=True))
        self._update_btn.bind("<Enter>",
                              lambda e: self._update_btn.configure(fg=ACCENT))
        self._update_btn.bind("<Leave>",
                              lambda e: self._update_btn.configure(fg=self._update_btn_fg()))
        self._update_lbl = tk.Label(
            vrow, text=self._version_text(), bg=CARD, fg=MUTED,
            font=self.font_meta, anchor="w", justify="left",
        )
        self._update_lbl.pack(side="left", fill="x", expand=True, padx=(4, 6))

        # 自动检查开关。默认开，但得能关：有人就是不想让程序联网。
        crow = tk.Frame(card, bg=CARD)
        crow.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(crow, text="更新", bg=CARD, fg=MUTED,
                 font=self.font_meta, width=6, anchor="w").pack(side="left")
        self.update_auto_btn = tk.Label(crow, bg=CARD_HI, font=self.font_meta,
                                        padx=8, pady=2, cursor="hand2")
        self.update_auto_btn.pack(side="left", padx=(4, 6))
        self.update_auto_btn.bind("<Button-1>", lambda e: self._toggle_auto_update())
        tk.Label(crow, text="每天启动查一次", bg=CARD, fg=MUTED,
                 font=self.font_meta, anchor="w").pack(side="left")
        self._update_auto = _as_bool(self.cfg.get("update_check", True), True)
        self._paint_auto_update_btn()

    def _paint_auto_update_btn(self):
        try:
            if self._update_auto:
                self.update_auto_btn.configure(text="自动检查 开", fg=TEAL)
            else:
                self.update_auto_btn.configure(text="自动检查 关", fg=MUTED)
        except tk.TclError:
            pass

    def _toggle_auto_update(self):
        self._update_auto = not self._update_auto
        self.cfg["update_check"] = self._update_auto
        self._save()
        self._paint_auto_update_btn()
        if self._update_auto:
            self._set_status("已开启自动检查更新（每天启动时查一次）", TEAL, 5000)
        else:
            self._set_status("已关闭自动检查更新，需要时点「检查更新」", MUTED, 5000)

    def _update_btn_fg(self):
        """「检查更新」按钮该用什么颜色：查到新版用亮色提醒。"""
        res = self._update_result
        if res is not None and res.has_update:
            return TEAL
        return MUTED

    def _version_text(self):
        """版本那一行显示什么。

        情况按优先级排：下载中 > 下载好待安装 > 检查中 > 查到新版 > 已最新 > 失败。
        失败时把原因写出来——「检查更新失败」这种话等于没说。
        """
        cur = "v%s" % version.VERSION
        if self._update_downloading:
            if self._update_total > 0:
                pct = int(self._update_got * 100 / self._update_total)
                return "%s · 下载中 %d%%" % (cur, pct)
            return "%s · 下载中…" % cur
        if self._update_ready_path:
            return "%s · 安装包已就绪" % cur
        if self._update_checking:
            return "%s · 正在检查…" % cur
        res = self._update_result
        if res is None:
            return cur
        if res.has_update:
            return "%s · 有新版本 %s" % (cur, res.latest)
        if res.status == update_mod.UP_TO_DATE:
            return "%s · 已是最新" % cur
        return "%s · %s" % (cur, res.message or "没查到")

    def _paint_update_row(self):
        """把版本行的文字和按钮状态刷一遍。"""
        if self._update_lbl is None or self._update_btn is None:
            return
        try:
            self._update_lbl.configure(
                text=self._version_text(),
                fg=TEAL if (self._update_result is not None
                            and self._update_result.has_update) else MUTED,
            )
            if self._update_downloading:
                self._update_btn.configure(text="取消", fg=WARN, cursor="hand2")
            elif self._update_ready_path:
                self._update_btn.configure(text="安装", fg=OK, cursor="hand2")
            elif self._update_checking:
                self._update_btn.configure(text="检查中…", fg=MUTED, cursor="arrow")
            elif self._update_result is not None and self._update_result.has_update:
                self._update_btn.configure(text="下载并安装", fg=TEAL, cursor="hand2")
            else:
                self._update_btn.configure(text="检查更新", fg=MUTED, cursor="hand2")
        except tk.TclError:
            pass

    # ---------------- 检查更新 / 应用内下载 ----------------

    def _check_update(self, manual=False):
        """查一次最新版本。manual=True 表示是用户点的，失败要明说。

        已经查到新版时，再点一下就是「下载并安装」——不重复发请求。
        这个分支必须先判，否则用户看到按钮写着「下载并安装」，点下去
        却变成重新检查。
        """
        res = self._update_result
        # 下载中点按钮 = 取消。这个分支必须排在「已查到新版」前面，
        # 否则点下去会又去开始一次下载。
        if self._update_downloading:
            self._update_cancel = True
            self._set_status("正在取消下载…", WARN, 4000)
            return
        if not self._update_checking and res is not None and res.has_update:
            self._start_download(res)
            return
        if self._update_checking:
            return
        self._update_checking = True
        self._paint_update_row()
        if manual:
            self._set_status("正在检查更新…", MUTED, 0)

        def on_done(result):
            # 这里在后台线程！只入队，绝不去碰任何 tkinter 对象
            try:
                self._updates.put_nowait(result)
            except Exception:
                pass

        try:
            update_mod.check_async(on_done)
        except Exception as exc:
            self._update_checking = False
            self._update_result = update_mod.UpdateInfo(
                update_mod.NETWORK, "检查更新时出错：%r" % (exc,))
            self._paint_update_row()
            if manual:
                self._set_status("检查更新时出错：%r" % (exc,), ERR, 6000)

    def download_dir(self):
        """安装包下到哪儿。

        优先 `BINF_DOWNLOAD_DIR` 环境变量（测试要用），否则放配置目录下的
        updates 子目录。为什么不放 %TEMP%：用户可能想自己再留一份，而且
        安全软件对系统临时目录里出现 exe 更敏感。
        """
        folder = os.environ.get("BINF_DOWNLOAD_DIR")
        if not folder:
            folder = os.path.join(config_dir(), "updates")
        return folder

    def _start_download(self, res):
        """点「下载并安装」后的动作。

        三条路：
          1. 已经下过了（_update_ready_path 有值）→ 直接启动安装
          2. Release 里有 Setup.exe → 应用内下载，完了自己启动安装
          3. 只有 zip（旧版 Release）→ 退回开浏览器，别把 zip 当安装包跑

        下载在后台线程，进度和结果都入队，主线程取。
        """
        if self._update_ready_path:
            self._launch_installer(self._update_ready_path)
            return

        asset = res.installer if res is not None else None
        if asset is None:
            # 没有安装包就开浏览器。不自动下 zip：解压到哪儿、要不要覆盖
            # 正在跑的程序目录，这些得用户自己决定。
            self._set_status("这个版本没有安装包，用浏览器打开下载页", WARN, 6000)
            self._open_release_page(res.html_url if res else None)
            return

        self._update_downloading = True
        self._update_cancel = False
        self._update_got = 0
        self._update_total = asset.size
        self._paint_update_row()
        self._set_status("正在下载 %s…" % asset.name, MUTED, 0)

        def on_progress(written, total):
            # 后台线程！只记数字，不碰 tkinter
            try:
                self._update_progress_queue.put_nowait((written, total))
            except Exception:
                pass

        def on_done(result):
            try:
                self._update_dl_queue.put_nowait(result)
            except Exception:
                pass

        try:
            update_mod.download_async(
                asset, self.download_dir(), on_done,
                progress=on_progress,
                should_stop=lambda: self._update_cancel)
        except Exception as exc:
            self._update_downloading = False
            self._paint_update_row()
            self._set_status("下载启动失败：%r" % (exc,), ERR, 8000)

    def _drain_updates(self):
        """主线程取后台线程的检查/下载结果与进度。"""
        if not self._alive:
            return
        try:
            while True:
                result = self._updates.get_nowait()
                self._on_update_result(result)
        except queue.Empty:
            pass
        except Exception:
            pass

        try:
            while True:
                written, total = self._update_progress_queue.get_nowait()
                self._on_download_progress(written, total)
        except queue.Empty:
            pass
        except Exception:
            pass

        try:
            while True:
                result = self._update_dl_queue.get_nowait()
                self._on_download_result(result)
        except queue.Empty:
            pass
        except Exception:
            pass

        try:
            self.root.after(200, self._drain_updates)
        except Exception:
            pass

    def _on_download_progress(self, written, total):
        """下载中：更新进度显示。"""
        self._update_got = written
        self._update_total = total
        if total > 0:
            pct = int(written * 100 / total)
            self._set_status(
                "正在下载安装包 %d%%（%.1f / %.1f MB）" % (
                    pct, written / 1048576.0, total / 1048576.0), MUTED, 0)
        else:
            self._set_status("正在下载安装包 %.1f MB…" % (written / 1048576.0), MUTED, 0)

    def _on_download_result(self, result):
        """下载完了：成功就等着用户点「安装」，失败把原因原样说出来。"""
        self._update_downloading = False
        self._update_cancel = False
        if result.ok:
            self._update_ready_path = result.path
            self._paint_update_row()
            self._set_status(
                "安装包已下载好（%.1f MB），点「安装」开始装" % (
                    result.bytes / 1048576.0), OK, 0)
            self._notify_tray(
                "安装包已下载好",
                "点「安装」开始装，装的时候会先关掉当前窗口。")
        else:
            self._paint_update_row()
            self._set_status("下载失败：%s" % (result.message or "未知原因"),
                             ERR, 8000)

    def _launch_installer(self, path):
        """启动安装程序，然后退出自己。

        为什么必须退出：安装包要覆盖 BinFloater.exe 和 _internal 里的文件，
        我们正占用着它们，不退出就会出现「文件正在使用」的报错。
        这就是「不自己替换自己」——真正动手的是 Inno Setup，不是我们。

        Inno 的 PrepareToInstall 里本来就会 taskkill 一次，但我们自己先退
        更优雅：能正常存配置、卸托盘、撤钩子。
        """
        if not path or not os.path.isfile(path):
            self._set_status("安装包不见了：%s" % path, ERR, 8000)
            self._update_ready_path = ""
            self._paint_update_row()
            return
        try:
            # 用 DETACHED_PROCESS 让安装程序脱离本进程，否则父进程退出时
            # 安装向导可能被一起带走（UAC 提权对话框弹不出来）。
            subprocess.Popen(
                [path],
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x8),
                close_fds=True,
            )
        except Exception as exc:
            self._set_status("启动安装程序失败：%r" % (exc,), ERR, 10000)
            return
        self._set_status("正在退出，准备安装…", TEAL, 2000)
        # 给窗口一点时间把状态栏画出来，然后正常退出流程走一遍
        try:
            self.root.after(400, self._quit)
        except Exception:
            self._quit()

    def _on_update_result(self, result):
        """拿到结果：刷界面、必要时弹托盘气泡。"""
        self._update_checking = False
        self._update_result = result
        self._paint_update_row()

        if result.has_update:
            line = update_mod.summary_line(result, 120)
            msg = "%s → %s" % (result.current, result.latest)
            if line:
                msg += "\n" + line
            self._set_status(
                "有新版本 %s，点「去下载」" % result.latest, TEAL, 12000)
            # 收在托盘里的时候窗口看不见，必须用气泡声告知一声
            self._notify_tray("发现新版本 %s" % result.latest, msg)
            return

        if result.status == update_mod.UP_TO_DATE:
            self._set_status("已是最新版本（v%s）" % version.VERSION, TEAL, 5000)
        elif result.status == update_mod.NOT_FOUND:
            # 仓库还是私有的，匿名请求拿不到任何东西。别把它说成「网络故障」
            self._set_status(
                "读不到发布信息：仓库还没公开（这不影响使用）", MUTED, 8000)
        else:
            self._set_status("检查更新失败：%s" % (result.message or "未知原因"),
                             WARN, 6000)

    def _maybe_auto_check_update(self):
        """启动时自动查一次，一天只查一次。

        为什么不每次启动都查：匿名调 api.github.com 限 60 次/小时（按 IP），
        用户一天开关十几次就把额度用完了，真需要查的时候反而 403。
        查过就记下日期，同一天再启动直接跳过。
        用户关掉开关（update_check=false）就完全不查。
        """
        if not _as_bool(self.cfg.get("update_check", True), True):
            return
        today = time.strftime("%Y-%m-%d")
        if self._update_last_day == today:
            return
        self._update_last_day = today
        self.cfg["update_last_day"] = today
        self._save()
        self._check_update(manual=False)

    def _open_release_page(self, url=None):
        """用系统默认浏览器打开下载页。"""
        target = url or update_mod.RELEASES_PAGE
        try:
            os.startfile(target)
        except Exception as exc:
            # 打不开就把地址塞进剪切板，至少用户能自己粘到浏览器里
            try:
                clipwin.write_text(target)
                self._set_status("打不开浏览器，下载地址已复制：%s" % target, WARN, 8000)
            except Exception:
                self._set_status("打不开下载页：%r" % (exc,), WARN, 8000)

    def _open_config_dir(self):
        """在资源管理器里打开配置所在目录。"""
        folder = config_dir()
        try:
            if os.path.isdir(folder):
                os.startfile(folder)
            else:
                self._set_status("配置目录不见了：%s" % folder, WARN, 6000)
        except Exception as exc:
            self._set_status("打不开目录：%r" % (exc,), WARN, 6000)

    def _on_alpha_slide(self, _value):
        """滑块拖动时实时改窗口透明度。

        不写盘：拖动过程中会来几十次回调，每次都落盘等于拿磁盘当草稿纸。
        松手（ButtonRelease）时才存，见 __init__ 里绑的那个事件。
        """
        try:
            pct = float(self.alpha_var.get())
        except (tk.TclError, ValueError):
            return
        alpha = max(0.25, min(1.0, pct / 100.0))
        try:
            self.root.attributes("-alpha", alpha)
            self.alpha_lbl.configure(text="%d%%" % int(round(alpha * 100)))
        except tk.TclError:
            pass

    def _on_alpha_release(self, _event=None):
        try:
            alpha = round(max(0.25, min(1.0, float(self.alpha_var.get()) / 100.0)), 2)
        except (tk.TclError, ValueError):
            return
        self.cfg["alpha"] = alpha
        self._save()

    def _on_width_slide(self, _value):
        """拖动时实时改窗口宽度。同透明度：拖动中不落盘。"""
        try:
            want = int(round(float(self.width_var.get())))
        except (tk.TclError, ValueError):
            return
        self._apply_width(want, persist=False)

    def _on_width_release(self, _event=None):
        try:
            want = int(round(float(self.width_var.get())))
        except (tk.TclError, ValueError):
            return
        self._apply_width(want, persist=True)

    def _apply_width(self, want, persist=False):
        want = max(MIN_WIDTH, min(MAX_WIDTH, int(want)))
        if want == self.width:
            if persist:
                self.cfg["width"] = want
                self._save()
            return
        self.width = want
        try:
            self.width_lbl.configure(text="%dpx" % want)
        except tk.TclError:
            pass
        # 折叠时高度是 COLLAPSED_H，展开时是自己的高度，两种情况都要重设，
        # 否则拖完滑块窗口宽度不变，看起来像没生效。
        h = COLLAPSED_H if self._collapsed else self.height
        try:
            self.root.geometry(f"{want}x{h}")
            self.root.update_idletasks()
            self.root.after(30, self._apply_round)
        except tk.TclError:
            return
        if persist:
            self.cfg["width"] = want
            self._save()

    def _reset_defaults(self):
        """把能改的设置全部恢复出厂值。

        只重置「设置」不重置窗口位置和转换历史：位置是用户的桌面布局，
        跟设置无关，重置掉反而更烦人。
        """
        self.trigger_mode = TRIGGER_HOTKEY
        self.space_count = 2
        self.hotkey_spec = hotkey_mod.DEFAULT_HOTKEY
        self.hotkey = hotkey_mod.parse(self.hotkey_spec)
        self.dir_mode = DIR_AUTO
        self.fmt = "binary"
        self.sep = "space"
        self._clip_paused = False
        self.topmost = True
        self.cfg["trigger_mode"] = self.trigger_mode
        self.cfg["space_count"] = self.space_count
        self.cfg["hotkey"] = self.hotkey_spec
        self.cfg["dir_mode"] = self.dir_mode
        self.cfg["format"] = self.fmt
        self.cfg["separator"] = self.sep
        self.cfg["clip_paused"] = False
        self.cfg["topmost"] = True
        self.cfg["alpha"] = 1.0
        self.cfg["width"] = W_WIDTH
        self._update_auto = True
        self.cfg["update_check"] = True
        self._save()
        try:
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 1.0)
            self.alpha_var.set(100)
            self.alpha_lbl.configure(text="100%")
            self.width_var.set(W_WIDTH)
        except tk.TclError:
            pass
        self._apply_width(W_WIDTH, persist=False)
        if self._watcher is not None:
            self._apply_trigger_mode()
        self._paint_trigger_btn()
        self._paint_hotkey_btn()
        self._paint_space_btn()
        self._paint_dir_btn()
        self._paint_format_btn()
        self._paint_separator_btn()
        self._paint_pause_btn()
        self._paint_auto_btn()
        self._paint_auto_update_btn()
        self._on_input()
        self.pin_btn.configure(fg=TEAL, text="◉")
        self._refresh_tray_tooltip()
        self._set_status("已恢复默认设置", TEAL, 5000)

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
        # 用虚拟桌面范围夹，副屏在左边时 x 可以是负数
        left, top, vw, vh = screen_bounds(self.root)
        x = max(left - 40, min(x, left + vw - 80))
        y = max(top, min(y, top + vh - 40))
        self.root.geometry(f"+{x}+{y}")
        # 拖动结束会走 _save_position，这里只标脏，别每次 MOVE 都写盘
        self._pos_dirty = True

    def _drag_end(self, event=None):
        """松手才算一次拖动结束，这时才真的写盘。"""
        if self._pos_dirty:
            self._save_position_if_dirty()
        self._drag = None

    def _toggle_collapse(self):
        self._set_collapsed(not self._collapsed, persist=True)

    def _set_collapsed(self, collapsed: bool, persist: bool = False):
        """折叠/展开。分成独立方法是为了启动时能按配置直接置位。"""
        self._collapsed = collapsed
        if collapsed:
            self.scroll_wrap.pack_forget()
            self.status.pack_forget()
            self.root.geometry(f"{self.width}x{COLLAPSED_H}")
            self.collapse_btn.configure(text="▼")
            self._paint_collapsed_title()
        else:
            self.scroll_wrap.pack(fill="both", expand=True)
            self.status.pack(fill="x", padx=16, pady=(0, 8))
            self.root.geometry(f"{self.width}x{self.height}")
            self.collapse_btn.configure(text="▲")
            self.title_lbl.configure(text="二进制悬浮窗")
        if persist:
            # 记住折叠状态：用户习惯折起来只露一条，每次启动都展开很烦
            self.cfg["collapsed"] = collapsed
            self._save()
        try:
            self.root.after(40, self._apply_round)
        except tk.TclError:
            pass

    def _paint_collapsed_title(self):
        """折叠时标题栏显示「最后一条状态」，不展开也能知道发生了什么。

        之前只显示译文，没有译文时就只剩四个字「二进制悬浮窗」，等于
        折叠状态下什么都看不到 —— 快捷键有没有生效、刚转了什么全不知道。
        """
        parts = []
        raw = self.result.get("1.0", "end-1c").strip()
        if raw and raw != PLACEHOLDER_RESULT:
            text = raw.replace("\n", " ")
            if len(text) > 18:
                text = text[:18] + "…"
            parts.append("→ " + text)
        # 状态栏的最后一句话（去掉自动复位的那句通用文案）
        try:
            tip = self.status.cget("text")
        except tk.TclError:
            tip = ""
        if tip:
            if len(tip) > 18:
                tip = tip[:18] + "…"
            parts.append(tip)
        if not parts:
            parts.append("二进制悬浮窗")
        self.title_lbl.configure(text=" · ".join(parts))

    def _cycle_alpha(self):
        steps = [1.0, 0.92, 0.8, 0.65]
        cur = float(self.root.attributes("-alpha"))
        nxt = steps[(steps.index(min(steps, key=lambda s: abs(s - cur))) + 1) % len(steps)]
        self.root.attributes("-alpha", nxt)
        self.cfg["alpha"] = nxt
        self._save()
        self._set_status(f"透明度 {int(nxt * 100)}%", MUTED)

    def _toggle_topmost(self):
        self.topmost = not self.topmost
        self.root.attributes("-topmost", self.topmost)
        self.pin_btn.configure(fg=TEAL if self.topmost else MUTED)
        self.pin_btn.configure(text="◉" if self.topmost else "○")
        self.cfg["topmost"] = self.topmost
        self._save()
        self._set_status("已置顶" if self.topmost else "已取消置顶", TEAL if self.topmost else MUTED)

    # ---------- 编码格式 / 分隔符 ----------
    def _cycle_format(self):
        idx = core.FORMATS.index(self.fmt) if self.fmt in core.FORMATS else 0
        self.fmt = core.FORMATS[(idx + 1) % len(core.FORMATS)]
        self.cfg["format"] = self.fmt
        self._save()
        self._paint_format_btn()
        self._on_input()
        self._set_status("编码格式改成 %s" % core.FORMAT_TEXT.get(self.fmt, self.fmt), TEAL)

    def _paint_format_btn(self):
        self.fmt_btn.configure(text=core.FORMAT_TEXT.get(self.fmt, self.fmt))
        # 分隔符只对二进制和十六进制有意义，Base64/摩斯有自己的分隔规则
        sep_ok = self.fmt in ("binary", "hex")
        self.sep_btn.configure(fg=MUTED if sep_ok else "#3d4655")
        # copy_btn 在卡片下半部分才建，首次绘制时还不存在
        btn = getattr(self, "copy_btn", None)
        if btn is not None:
            btn.configure(text="复制" + core.FORMAT_TEXT.get(self.fmt, "编码"))

    def _cycle_separator(self):
        if self.fmt not in ("binary", "hex"):
            self._set_status("%s 不需要分隔符" % core.FORMAT_TEXT.get(self.fmt, self.fmt), WARN, 4000)
            return
        idx = core.SEPARATORS.index(self.sep) if self.sep in core.SEPARATORS else 0
        self.sep = core.SEPARATORS[(idx + 1) % len(core.SEPARATORS)]
        self.cfg["separator"] = self.sep
        self._save()
        self._paint_separator_btn()
        self._on_input()
        self._set_status("分隔符改成 %s" % core.SEPARATOR_TEXT.get(self.sep, self.sep), TEAL)

    def _paint_separator_btn(self):
        self.sep_btn.configure(text=core.SEPARATOR_TEXT.get(self.sep, self.sep))

    def _toggle_auto_copy(self):
        self.cfg["auto_copy"] = not _as_bool(self.cfg.get("auto_copy", False), False)
        self._save()
        self._paint_auto_btn()
        self._set_status(
            "识别到二进制后将自动把译文写回剪切板" if self.cfg["auto_copy"] else "已关闭自动回写译文",
            OK if self.cfg["auto_copy"] else MUTED,
        )

    def _paint_auto_btn(self):
        on = _as_bool(self.cfg.get("auto_copy", False), False)
        self.auto_btn.configure(fg=TEAL if on else MUTED,
                                text="自动回写 ✓" if on else "自动回写")

    # ---------- 剪切板监视开关 ----------
    def _toggle_clip_pause(self):
        self._clip_paused = not self._clip_paused
        self.cfg["clip_paused"] = self._clip_paused
        self._save()
        self._paint_pause_btn()
        if self._clip_paused:
            self.clip_badge.configure(text="监视已暂停", fg=WARN)
            self._set_status("已暂停监视剪切板，点右上角恢复", WARN, 5000)
        else:
            # 恢复时把基线对齐当前值，否则暂停期间的改动会一股脑涌进来
            try:
                self._last_seq = clipwin.sequence()
                self._last_clip = clipwin.read_text()
            except Exception:
                pass
            self._set_status("已恢复监视剪切板", TEAL, 4000)

    def _paint_pause_btn(self):
        # 用 ASCII 的 ‖ / ▶ 而不是 ⏸ / ▶ 的 emoji 变体：Tk 配微软雅黑打不出
        # 彩色 emoji，会渲染成方块（这项目之前用 ⌖、⌄ 踩过两次）。
        self.pause_btn.configure(
            text="▶ 恢复" if self._clip_paused else "‖ 暂停",
            fg=WARN if self._clip_paused else MUTED,
        )

    # ---------- 反向转换 ----------
    def _reverse_result(self):
        """把当前译文当原文，按相反方向再转一次。

        卡在「转错了想反悔」这一步：原来得先复制译文、粘到输入框、再转回来。
        现在一键就回去。
        """
        text = self.result.get("1.0", "end-1c").strip()
        if not text or text == PLACEHOLDER_RESULT:
            self._set_status("译文区是空的，没有可反向的内容", WARN)
            return
        # 译文是「解码后的文字」，反向就是把它编回去
        out = core.encode_as(text, self.fmt, self.sep)
        if not out:
            self._set_status("反向转换没有产出", WARN)
            return
        self.result.configure(fg=TEXT)
        self.result.set_text(out)
        self.clip_badge.configure(
            text="已反向 · " + core.FORMAT_TEXT.get(self.fmt, self.fmt), fg=TEAL)
        self._push_history("encode", self.fmt, text, out)
        self._flash_result()
        self._set_status("已反向转换，点「复制译文」取走", TEAL, 5000)

    def _paint_lock_btn(self):
        on = self._result_locked
        self.lock_btn.configure(fg=WARN if on else MUTED,
                                text="已锁定" if on else "锁定")

    def _toggle_result_lock(self):
        self._result_locked = not self._result_locked
        self._paint_lock_btn()
        if self._result_locked:
            self._set_status("译文已锁定，剪切板变化不再覆盖", TEAL, 4000)
        else:
            self._set_status("已解锁，显示最新剪切板", TEAL, 4000)
            # 锁着的时候攒下的最新内容，解锁后补上
            if self._pending_clip is not None:
                pending = self._pending_clip
                self._pending_clip = None
                self._on_clipboard(pending)

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
        self._save()
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
                    on_capture_hint=self._on_capture_hint,
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
        self._save()
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

    def _on_capture_hint(self, mods_text):
        """钩子线程：录制中按住了哪些修饰键。同样只能入队。"""
        try:
            self._triggers.put_nowait(("hint", mods_text))
        except Exception:
            pass

    def _show_capture_hint(self, mods_text):
        """录制中实时显示「Ctrl+Alt+…」。"""
        if not self._capturing:
            return
        if mods_text:
            self.hotkey_btn.configure(text="%s + …" % mods_text, fg=WARN, bg=CARD_HI)
            self.hotkey_hint.configure(text="再按一个主键", fg=MUTED)
        else:
            self._paint_hotkey_btn()

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
        self._save()
        self._capture_backup = None
        if self._watcher is not None:
            self._watcher.hotkey = parsed
        self._paint_hotkey_btn()
        self._refresh_tray_tooltip()
        if parsed.has_modifier():
            clash = hotkey_mod.conflict_warning(parsed)
            if clash:
                self._set_status("已设为 %s，但它和系统冲突：%s"
                                 % (parsed.text(), clash), WARN, 9000)
            else:
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
                # 队列里混着三种事件：转换触发（字符串）、快捷键录制结果
                # 和录制中的修饰键回显（都是元组，靠第一项区分）。
                # 用类型区分，免得以后再加事件类型时又得改一轮。
                if isinstance(item, tuple) and item and item[0] == "capture":
                    self._apply_captured(item[1])
                elif isinstance(item, tuple) and item and item[0] == "hint":
                    self._show_capture_hint(item[1])
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

    def _convert_text(self, text):
        """按当前方向设置把一段文字转换一次。

        抽出来是因为「转换当前行」和输入框里的批量转换要走同一套判断，
        两边各写一份迟早出现「同一个字符串在两处结果不一样」。
        """
        mode = self.dir_mode
        if mode == DIR_TEXT2BIN:
            return core.encode_as(text, self.fmt, self.sep)
        if mode == DIR_BIN2TEXT:
            out, _msg = core.decode_as(text, self.fmt)
            return out or ""
        # 自动判断：先看内容像哪种编码，像就解码，不像就编码。
        # 格式选项为「二进制」时才允许自动换格式，否则用户明确选了
        # 十六进制/Auto 又乱跳会莫名其妙。
        how = core.auto_detect(text)
        if how == "bin2text":
            out, _msg = core.decode_as(text, core.detect_format(text) or self.fmt)
            return out or ""
        if how == "text2bin":
            return core.encode_as(text, self.fmt, self.sep)
        return ""

    def _do_convert_line(self, kind, convert_selection=False):
        if not keys.IS_WINDOWS:
            self._set_status("这个功能只在 Windows 上可用", WARN)
            return

        def convert(text):
            return self._convert_text(text)

        try:
            if convert_selection:
                status, src, out, msg = keys.convert_selection(convert)
            else:
                status, src, out, msg = keys.convert_line(convert)
        except Exception as exc:
            self._set_status(f"转换失败：{exc}", ERR, 6000)
            return

        if status == "ok":
            preview = out if len(out) <= 46 else out[:46] + "…"
            where = "选中内容" if convert_selection else "这一行"
            self._set_status(f"已转换{where} {len(src)} 字符 → {preview}", OK, 5000)
        elif status == "empty":
            if convert_selection:
                self._set_status(msg or "先在别的程序里选中要转换的文字，再点这里",
                                 WARN, 6000)
            else:
                self._set_status(msg or "光标前面这一行没有文字", WARN, 5000)
        elif status == "same":
            # 转换前后没区别时要知道是「本来就是这样」还是「转换器报错」
            if msg.startswith("转换出错"):
                self._set_status(f"{msg}（当前窗口可能拿不到文字）", WARN, 6000)
            else:
                self._set_status(msg or "转换结果和原文一样，没动", MUTED)
        elif status == "clipfail":
            self._set_status(msg or "写剪切板失败，可能被别的程序占着", ERR, 6000)
        else:
            self._set_status(msg or "当前窗口不支持这个操作", WARN)

    def _convert_selection_now(self):
        """选中一段（可以跨行）整体转换，而不是只取光标所在那一行。

        和「转换这一行」的区别写在按钮旁边：这个需要用户先手动选中，
        系统不知道光标在哪、也不去猜，没选区就什么都不动。
        """
        if not self._last_foreign_hwnd:
            self._do_convert_line("button", convert_selection=True)
            return

        target = self._last_foreign_hwnd

        def attempt(left):
            if keys.focus_window(target):
                self.root.after(120, lambda: self._do_convert_line(
                    "button", convert_selection=True))
                return
            if left > 0:
                self.root.after(60, lambda: attempt(left - 1))
            else:
                self._set_status("切不回刚才的窗口，请用 %s 触发"
                                 % self.hotkey.text(), WARN, 6000)

        attempt(2)

    def _paint_dir_btn(self):
        self.dir_btn.configure(text=DIR_TEXT.get(self.dir_mode, "自动判断"),
                               fg=TEAL if self.dir_mode != DIR_AUTO else MUTED)

    def _cycle_dir_mode(self):
        idx = DIR_ORDER.index(self.dir_mode)
        self.dir_mode = DIR_ORDER[(idx + 1) % len(DIR_ORDER)]
        self.cfg["dir_mode"] = self.dir_mode
        self._save()
        self._paint_dir_btn()

    def _bind_keys(self):
        r = self.root
        # 全局快捷键绑在 toplevel 上，Tk 的事件会从控件冒泡到 toplevel。
        # 于是用户在输入框里按 Ctrl+Q 会把程序关掉、按 Esc 会把窗口折叠、
        # 按 Ctrl+B 会把剪切板内容覆盖掉 —— 全是意外。
        # 解决办法：在入口处先看事件源，来自输入框就直接放行给输入框自己处理。
        r.bind("<Control-q>", self._guard_text(self._quit))
        r.bind("<Control-b>", self._guard_text(self._clip_to_bin))
        r.bind("<Escape>", self._guard_text(self._toggle_collapse))
        self.entry.bind("<Escape>", lambda e: self.entry.selection_clear())
        # 关闭窗口时保存位置
        r.protocol("WM_DELETE_WINDOW", self._quit)

    def _guard_text(self, action):
        """把全局快捷键包一层：焦点在文本输入区时不触发，让输入框自己消化。

        为什么要这么做：Tk 的 bindtags 里，控件事件处理完还会继续冒泡到
        toplevel（'.'）。输入框本身没有绑定 Ctrl+Q，事件就一路冒到 toplevel
        把程序关掉了。用户只是想打个字。
        """
        def handler(event):
            if self._in_text_widget(event):
                return None  # 不 return "break"，让输入框照常处理
            action()
            return "break"
        return handler

    def _in_text_widget(self, event) -> bool:
        """事件是不是发生在文本输入控件上。"""
        try:
            w = event.widget
        except Exception:
            return False
        return isinstance(w, (tk.Entry, tk.Text))

    def _quit(self):
        self._alive = False
        self._release_watcher()
        self._stop_tray()
        self._stop_waker()
        # 下到一半的 .part 文件别留着——下次启动也不会接着下，只会白占硬盘
        self._update_cancel = True
        try:
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            self._save()
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

    def _start_waker(self):
        """建「另一个实例请求唤回窗口」的接收端。失败不影响主功能。"""
        try:
            import wakeup
            self._waker = wakeup.Waker()
        except Exception:
            self._waker = None

    def _stop_waker(self):
        if self._waker is not None:
            try:
                self._waker.close()
            except Exception:
                pass
            self._waker = None

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
            self._save()
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
        """托盘提示要跟着当前模式走，不能一直写快捷键。

        只改 self._tray.tooltip 属性没用：NIM_ADD 时系统已经拿走一份副本，
        后面改属性不会同步过去。必须调 set_tooltip 让它发一次 NIM_MODIFY，
        否则用户悬停看到的永远是启动时那句话。
        """
        if self._tray is None:
            return
        try:
            trig = self._trigger_text()
            text = (("二进制悬浮窗 · %s" % trig) if trig
                    else "二进制悬浮窗 · 触发已关闭")[:127]
            self._tray.set_tooltip(text)
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
        # 同一句话反复设很常见（轮询、重绘都会顺手刷一下状态）。Tk 每次
        # configure 都要走一遍几何重算，白费。内容和颜色都没变就直接跳过。
        if text == self._status_text and color == self._status_color:
            return
        self._status_text = text
        self._status_color = color
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
        self._status_text = "监视中 · 复制 0/1 串即可自动翻译"
        self._status_color = MUTED
        try:
            self.status.configure(text=self._status_text, fg=MUTED)
        except tk.TclError:
            pass

    # ---------- 配置落盘 ----------
    def _save(self, **extra):
        """把 self.cfg 写盘。失败时明确告诉用户，不再只写日志。

        之前 save_config 吞掉异常只记日志，用户改完设置在磁盘满/被安全软件
        拦的情况下毫无察觉，下次启动发现设置没了还以为程序有 bug。
        """
        if extra:
            self.cfg.update(extra)
        if not save_config(self.cfg):
            self._set_status("设置没能保存到磁盘，检查一下磁盘空间或安全软件", ERR, 8000)
            return False
        return True

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
            # 有别的实例请求唤回窗口吗（用户又双击了一次 exe）
            if self._waker is not None and self._waker.poll():
                self._show_from_tray()
            self._save_position_if_dirty()
            if not self._clip_paused:
                seq = clipwin.sequence()
                if seq != self._last_seq:
                    self._last_seq = seq
                    text = clipwin.read_text()
                    if text and text != self._last_clip:
                        self._last_clip = text
                        self._on_clipboard(text)
        except Exception as exc:  # 监视线程永不死
            self._set_status(f"读取剪切板失败：{exc}", ERR)
        # 收进托盘后窗口根本看不见，没必要每 320ms 读一次剪切板。
        # 拉长到 1.2s，省下来的 CPU 对笔记本续航是有意义的。
        try:
            self.root.after(1200 if self._in_tray else 320, self._poll)
        except tk.TclError:
            pass

    def _save_position_if_dirty(self):
        """位置变了就找机会落盘。原来只在 _quit 里存，进程被强杀/崩溃就丢。

        每 320ms 都写盘太浪费，位置真的变了才写，而且变了之后最多等一拍
        就写下去——不攒着，攒着等于又回到「强杀就丢」。
        """
        if not self._pos_dirty:
            return
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
        except tk.TclError:
            return
        if (x, y) == self._last_saved_pos:
            self._pos_dirty = False
            return
        self._last_saved_pos = (x, y)
        self._pos_dirty = False
        self.cfg["x"] = x
        self.cfg["y"] = y
        self._save()

    def _on_clipboard(self, text: str):
        # 锁着的时候只记下最新内容，正文一个字都不动。用户正盯着译文看，
        # 剪切板一变就把正文冲掉，等于把人家读到一半的东西抢走。
        if self._result_locked:
            self._pending_clip = text
            self.clip_badge.configure(text="剪切板已变 · 译文锁定中", fg=WARN)
            return
        # 剪切板里的东西可能是四种编码之一，先认一下是哪种
        fmt = core.detect_format(text)
        if fmt:
            decoded, msg = core.decode_as(text, fmt)
            if decoded is None:
                self.clip_badge.configure(text="识别失败", fg=WARN)
                self._set_status(f"{msg} · 可手动修改后再试", WARN, 6000)
                return
            self.result.configure(fg=TEXT)
            self.result.set_text(decoded)
            label = core.FORMAT_TEXT.get(fmt, fmt)
            self.clip_badge.configure(text=f"{label} · {msg}", fg=OK)
            self._set_status(f"检测到{label} → 已翻译：{msg}", OK, 6000)
            self._clip_fmt = fmt
            self._flash_result()
            self._push_history("decode", fmt, text, decoded)
            if self._collapsed:
                self._paint_collapsed_title()
            if _as_bool(self.cfg.get("auto_copy", False), False) and decoded.strip():
                self._write_clip(decoded)
        else:
            # 普通文字：不打断，只提示可一键转编码
            #
            # 角标只放固定短词，不塞内容摘要。这一行是「标题 + 角标 + 暂停」
            # 三件并排，角标一长就把暂停键挤出窗口右边（截图里「‖ 暂停」
            # 直接消失了）。内容摘要交给状态栏，那里有整行宽度。
            self.clip_badge.configure(text="普通文字", fg=MUTED)
            shown = text.strip().replace("\n", " ")
            if len(shown) > 22:
                shown = shown[:22] + "…"
            self._set_status("剪切板是普通文字：%s · 按 Ctrl+B 或点「→ 转编码」"
                             % shown, MUTED, 5000)
            self._clip_fmt = ""

    # ---------- 转换历史 ----------
    HISTORY_MAX = 20

    def _push_history(self, kind, fmt, src, out):
        """记一条转换历史。转错了想找回上一版时用得着。

        只存内存不落盘：这里可能有用户剪切板里的私人内容，写进
        config.json 就等于永久留在磁盘上了。
        """
        try:
            entry = (kind, fmt, src, out, time.strftime("%H:%M:%S"))
            self._history.insert(0, entry)
            del self._history[self.HISTORY_MAX:]
            self._paint_history()
        except Exception:
            pass

    def _paint_history(self):
        box = getattr(self, "history_box", None)
        if box is None:
            return
        try:
            for w in box.winfo_children():
                w.destroy()
            if not self._history:
                tk.Label(box, text="还没有转换记录", bg=CARD, fg="#5b6579",
                         font=self.font_meta, anchor="w").pack(fill="x")
                return
            for kind, fmt, src, out, ts in self._history[:6]:
                row = tk.Frame(box, bg=CARD)
                row.pack(fill="x", pady=1)
                arrow = "解码" if kind == "decode" else "编码"
                label = "%s %s · %s" % (ts, arrow, core.FORMAT_TEXT.get(fmt, fmt))
                tk.Label(row, text=label, bg=CARD, fg="#5b6579",
                         font=self.font_meta, width=15, anchor="w").pack(side="left")
                shown = out.replace("\n", " ")
                if len(shown) > 30:
                    shown = shown[:30] + "…"
                lbl = tk.Label(row, text=shown, bg=CARD, fg=MUTED,
                               font=self.font_meta, anchor="w", cursor="hand2")
                lbl.pack(side="left", fill="x", expand=True)
                lbl.bind("<Button-1>", lambda e, t=out: self._copy_any(t))
                lbl.bind("<Enter>", lambda e, w=lbl: w.configure(fg=TEAL))
                lbl.bind("<Leave>", lambda e, w=lbl: w.configure(fg=MUTED))
        except tk.TclError:
            pass

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
        if not self._fill_manual(text):
            self._set_status("已把剪切板文字转成二进制", OK)

    def _paste_text(self):
        text = clipwin.read_text()
        if not text:
            self._set_status("剪切板没有文字", WARN)
            return
        self._fill_manual(text)

    def _fill_manual(self, text: str) -> bool:
        """把文字填进输入框并立即转换。返回 True 表示走了「太长不进输入框」的旁路。

        Entry.insert 的耗时是超线性的：实测 1 千字 0.03 秒、5 千字 0.44 秒、
        2 万字 5.9 秒、5 万字 36 秒。拖一个几 MB 的文本文件进来会直接把
        界面卡死（用户会以为程序崩了）。超长的就不进输入框了，直接转好
        放在结果区——Text 控件的插入是线性的，45 万字也只要 0.03 秒。
        """
        if len(text) > MAX_ENTRY_CHARS:
            self.entry.delete(0, "end")
            self.input_var.set("")   # 会触发 _on_input 清空结果，所以放在下面赋值之前
            self.bin_out.set_text(core.encode_as(text, self.fmt, self.sep))
            self.len_badge.configure(text=core.describe(text))
            self._set_status(
                "文字太长，没放进输入框，已经直接转换好了（%s）"
                % core.describe(text), TEAL, 7000)
            return True
        self.entry.delete(0, "end")
        self.entry.insert(0, text.replace("\n", " "))
        self._on_input()
        return False

    def _on_input(self):
        text = self.input_var.get()
        if not text:
            self.bin_out.set_text("")
            self.len_badge.configure(text="")
            return
        out = core.encode_as(text, self.fmt, self.sep)
        self.bin_out.set_text(out)
        self.len_badge.configure(text=core.describe(text))

    def _copy_binary(self):
        bits = self.bin_out.get("1.0", "end-1c").strip()
        if not bits:
            self._set_status("先输入文字", WARN)
            return
        self._copy_any(bits)

    # ---------- 批量 / 文件 ----------
    def _batch_convert(self):
        """把剪切板里的多行文本逐行转换。

        输入框是单行的，塞不进多行内容，所以批量走「从剪切板读、结果写进
        下面那块」这条路。空行原样保留，方便和原文对照。
        """
        text = clipwin.read_text()
        if not text:
            self._set_status("剪切板没有内容，先复制几行文字", WARN)
            return
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out = []
        ok = 0
        for line in lines:
            if not line.strip():
                out.append("")
                continue
            try:
                piece = self._convert_text(line)
            except Exception:
                piece = ""
            if piece:
                ok += 1
                out.append(piece)
            else:
                out.append(line)
        result = "\n".join(out)
        self.bin_out.set_text(result)
        self.len_badge.configure(text="%d 行 / 成功 %d 行" % (len(lines), ok))
        self.batch_hint.configure(text="批量 %d 行" % len(lines))
        self._push_history("encode" if self.dir_mode != DIR_BIN2TEXT else "decode",
                           self.fmt, text, result)
        self._set_status("批量转换完成：%d 行，成功 %d 行" % (len(lines), ok), OK, 5000)

    def _load_from_file(self):
        path = filedialog.askopenfilename(
            title="选一个文本文件",
            filetypes=[("文本文件", "*.txt *.md *.csv *.json *.log"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            try:
                with open(path, "r", encoding="gbk") as fh:
                    text = fh.read()
            except Exception as exc:
                self._set_status("这个文件读不了：%s" % exc, ERR, 6000)
                return
        except Exception as exc:
            self._set_status("打开文件失败：%s" % exc, ERR, 6000)
            return
        if not text.strip():
            self._set_status("文件是空的", WARN)
            return
        # 单行文本进输入框，多行走批量
        if "\n" in text.strip():
            self._fill_manual(text)
            self.batch_hint.configure(text="已读 %d 行" % text.count("\n"))
            self._set_status("已读入文件，点「多行批量」转换", TEAL, 5000)
        else:
            self._fill_manual(text)
            self._set_status("已读入文件", TEAL, 4000)

    def _export_result(self):
        """把结果区的内容存成文件。大段输出用复制粘贴太别扭。"""
        text = self.bin_out.get("1.0", "end-1c")
        if not text.strip():
            self._set_status("结果区是空的，没有可导出的内容", WARN)
            return
        path = filedialog.asksaveasfilename(
            title="保存结果",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except Exception as exc:
            self._set_status("写文件失败：%s" % exc, ERR, 6000)
            return
        self._set_status("已保存到 %s" % os.path.basename(path), OK, 5000)

    def _decode_to_file(self):
        """把二进制串解成字节，直接存成文件。

        「导出结果」存的是文本（把 0/1 串原样写进 .txt），这个存的是
        解码后的**字节**——别人发来一段二进制，还原成图片/压缩包就用它。
        """
        text = self.bin_out.get("1.0", "end-1c")
        if not text.strip():
            self._set_status("结果区是空的，没有可解码的内容", WARN, 5000)
            return
        # 必须先确认内容是纯 0/1：_clean_bits 只负责去分隔符和 0b 前缀，
        # 它不会替我们挡掉别的字符。直接丢给 bits_to_bytes 的话，
        # 结果区里但凡有个字母（手动改过、或者刚反向成了十六进制），
        # int(x, 2) 就抛 ValueError，代码里没人接，用户点一下毫无反应。
        bits = core._clean_bits(text)
        if not bits:
            self._set_status("这段内容不是二进制串，解不出字节", WARN, 5000)
            return
        if set(bits) - {"0", "1"}:
            self._set_status(
                "结果区里不是二进制串（有 0/1 以外的字符），先切到二进制格式再转换",
                WARN, 6000)
            return
        data = core.bits_to_bytes(bits)
        path = filedialog.asksaveasfilename(
            title="解码为文件（%d 字节）" % len(data),
            defaultextension=".bin",
            filetypes=[("二进制文件", "*.bin"), ("图片", "*.png"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except Exception as exc:
            self._set_status("写文件失败：%s" % exc, ERR, 6000)
            return
        # 能认出是图片就顺手说一句，用户才知道存对了
        head = data[:8]
        kind = ""
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            kind = "（PNG 图片）"
        elif head.startswith(b"\xff\xd8\xff"):
            kind = "（JPEG 图片）"
        elif head.startswith(b"PK\x03\x04"):
            kind = "（zip 压缩包）"
        self._set_status("已保存 %d 字节%s 到 %s"
                         % (len(data), kind, os.path.basename(path)), OK, 6000)

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
