# -*- coding: utf-8 -*-
"""冒烟测试：真的把 GUI 建起来，检查控件和钩子是否就绪，然后退出。"""
import os
import sys
import tempfile
import time
import tkinter as tk

# 必须在 import gui 之前把配置目录指到临时目录：gui.config_path() 会读它。
# 否则这个测试会写真实配置——曾经把 tray_hint_shown 置成 true，
# 结果用户永远看不到「已收进托盘」那条气泡通知。
_TMP_APPDATA = tempfile.mkdtemp(prefix="binfloater_smoke_")
os.environ["APPDATA"] = _TMP_APPDATA
os.environ["BINF_CONFIG_DIR"] = _TMP_APPDATA

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clipwin
import core
import gui
import hotkey as hotkey_mod
import keys
import trigger

if clipwin.IS_WINDOWS:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

root = tk.Tk()
app = gui.BinFloater(root)
root.update()
root.update_idletasks()

ok = []
bad = []


def check(name, cond, detail=""):
    (ok if cond else bad).append(name)
    line = ("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else "")
    # 控制台是 GBK，「▶」「⏸」「⇄」这类符号打不出来会直接抛
    # UnicodeEncodeError 把整个测试带崩，能打的打、打不了的问号顶上
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("gbk", "replace").decode("gbk", "replace"))

print("=== 控件 ===")
check("转换当前行卡片", hasattr(app, "trigger_btn"))
check("方向按钮", hasattr(app, "dir_btn"))
check("触发按钮文字", app.trigger_btn.cget("text") == "快捷键", app.trigger_btn.cget("text"))
check("方向按钮文字", app.dir_btn.cget("text") == "自动判断", app.dir_btn.cget("text"))
# 触发方式必须能被认出来是「可以点的地方」：有底框、有自己的标签行。
# 以前它只是卡片标题右边一个没有边框的小字，用户根本没发现可以点。
check("触发按钮有底框", app.trigger_btn.cget("bg") == gui.CARD_HI, app.trigger_btn.cget("bg"))
check("触发按钮可点", "hand2" in str(app.trigger_btn.cget("cursor")), app.trigger_btn.cget("cursor"))
check("触发方式提示", hasattr(app, "trigger_hint") and app.trigger_hint.cget("text") == "不占用空格",
      app.trigger_hint.cget("text") if hasattr(app, "trigger_hint") else "缺失")
# 名字不能叫「双击空格」：连按次数可调，调到 3 之后名字就是错的
check("空格模式名字不带双击",
      "双击" not in gui.TRIGGER_TEXT[gui.TRIGGER_SPACE], gui.TRIGGER_TEXT[gui.TRIGGER_SPACE])

print("=== 钩子 ===")
check("默认模式是快捷键", app.trigger_mode == gui.TRIGGER_HOTKEY, app.trigger_mode)
check("watcher 已装", app._watcher is not None)
check("hotkey_mode 开", app._watcher is not None and app._watcher.hotkey_mode)
check("space_mode 关", app._watcher is not None and not app._watcher.space_mode)

print("=== 模式切换 ===")
app._cycle_trigger_mode()  # hotkey -> space
root.update()
check("切到空格连按", app.trigger_mode == gui.TRIGGER_SPACE, app.trigger_mode)
check("空格模式按钮文字", app.trigger_btn.cget("text") == "空格连按", app.trigger_btn.cget("text"))
check("空格模式提示说代价", "空格" in app.trigger_hint.cget("text"), app.trigger_hint.cget("text"))
check("space_mode 开", app._watcher is not None and app._watcher.space_mode)
check("hotkey_mode 关", app._watcher is not None and not app._watcher.hotkey_mode)
app._cycle_trigger_mode()  # space -> off
root.update()
check("切到关闭", app.trigger_mode == gui.TRIGGER_OFF, app.trigger_mode)
check("关闭模式提示", app.trigger_hint.cget("text") == "不占键盘钩子", app.trigger_hint.cget("text"))
check("关闭后钩子已卸", app._watcher is None)
app._cycle_trigger_mode()  # off -> hotkey
root.update()
check("转回快捷键", app.trigger_mode == gui.TRIGGER_HOTKEY, app.trigger_mode)
check("钩子重新装上", app._watcher is not None)

print("=== 方向切换 ===")
for _ in range(len(gui.DIR_ORDER)):
    app._cycle_dir_mode()
check("转一圈回到自动", app.dir_mode == gui.DIR_AUTO, app.dir_mode)

print("=== 触发队列（模拟钩子线程） ===")
app._on_hotkey_trigger("space")
check("事件已入队", not app._triggers.empty())
# 注意：不真的执行 _drain_triggers，否则会去动真实前台窗口

print("=== 托盘 ===")
check("托盘已启动", app._tray is not None and app._tray.running)
check("默认不在托盘", app._in_tray is False)
# 通知文案必须跟当前模式走：快捷键模式下要说快捷键
check("快捷键模式提示提到快捷键",
      app.hotkey.text() in app._trigger_text(), app._trigger_text())
check("托盘 tooltip 跟模式一致",
      app.hotkey.text() in app._tray.tooltip, app._tray.tooltip)
app._minimize_to_tray()
root.update()
check("最小化后进入托盘", app._in_tray is True)
check("最小化后窗口隐藏", root.state() == "withdrawn", root.state())
check("最小化后钩子仍在", app._watcher is not None)
check("托盘提示只弹一次", app._tray_hint_shown is True)
app._show_from_tray()
root.update()
check("从托盘恢复", app._in_tray is False)
check("恢复后窗口可见", root.state() == "normal", root.state())

print("=== 连按次数 ===")
check("默认 2 下", app.space_count == 2, app.space_count)
app._cycle_space_count()
check("非空格模式不生效", app.space_count == 2, app.space_count)
app.trigger_mode = "space"
app._apply_trigger_mode(silent=True)
# 空格模式下通知不能再说快捷键——用户实测踩过这个坑：
# 空格模式收进托盘，通知却写「Ctrl+Alt+B 依然可用」，按了根本没反应。
check("空格模式提示不提快捷键",
      app.hotkey.text() not in app._trigger_text(), app._trigger_text())
check("空格模式提示写连按次数",
      "%d" % app.space_count in app._trigger_text(), app._trigger_text())
app._cycle_space_count()
check("切到 3 下", app.space_count == 3, app.space_count)
check("同步给钩子", app._watcher.space_count == 3, app._watcher.space_count)
check("持久化", app.cfg.get("space_count") == 3, app.cfg.get("space_count"))
check("改次数后提示跟着变", "3" in app._trigger_text(), app._trigger_text())
check("改次数后 tooltip 跟着变", "3" in app._tray.tooltip, app._tray.tooltip)

print("=== 触发关闭 ===")
app.trigger_mode = "off"
app._apply_trigger_mode(silent=True)
check("关闭模式提示为空", app._trigger_text() == "", app._trigger_text())
check("关闭模式 tooltip 说明已关闭",
      "关闭" in app._tray.tooltip, app._tray.tooltip)

print("=== 窗口 ===")
check("窗口有尺寸", root.winfo_width() > 300, root.winfo_width())
check("窗口有高度", root.winfo_height() > 400, root.winfo_height())

print("=== 启动回显 ===")
# 用户抱怨过「每次打开都要重新设」，所以启动时状态栏要主动念出恢复的设置。
check("启动文案带前缀", app._boot_text().startswith("已恢复："), app._boot_text())
check("启动文案说明触发方式", bool(app._boot_text().strip()), app._boot_text())
app.trigger_mode = gui.TRIGGER_SPACE
app.space_count = 4
check("空格模式下启动文案写次数", "×4" in app._boot_text(), app._boot_text())
app.trigger_mode = gui.TRIGGER_OFF
check("关闭模式下启动文案说已关闭", "已关闭" in app._boot_text(), app._boot_text())

print("=== 锁定译文 ===")
# 剪切板一变就把译文冲掉，用户正在读的东西当场消失。锁上之后正文不许动。
app._clear_result()
app._result_locked = False
app._on_clipboard("01001000 01101001")
check("解锁时正常显示译文", "Hi" in app.result.get("1.0", "end-1c"),
      app.result.get("1.0", "end-1c"))
app._toggle_result_lock()
check("锁定状态切换", app._result_locked is True)
check("锁定按钮文案", app.lock_btn.cget("text") == "已锁定", app.lock_btn.cget("text"))
_before = app.result.get("1.0", "end-1c")
app._on_clipboard("01101000 01100101 01101100 01101100 01101111")
check("锁定时正文不变", app.result.get("1.0", "end-1c") == _before,
      app.result.get("1.0", "end-1c"))
check("锁定时提示剪切板已变", "锁定" in app.clip_badge.cget("text"),
      app.clip_badge.cget("text"))
app._toggle_result_lock()
check("解锁后补上最新内容", "hello" in app.result.get("1.0", "end-1c"),
      app.result.get("1.0", "end-1c"))
check("解锁后按钮文案复原", app.lock_btn.cget("text") == "锁定", app.lock_btn.cget("text"))

print("=== 输入框不劫持全局快捷键 ===")
# Tk 的事件会从控件冒泡到 toplevel。以前在输入框里按 Ctrl+Q 会把程序关掉，
# 按 Esc 会把窗口折叠，按 Ctrl+B 会覆盖剪切板 —— 用户只是想打个字。
app.trigger_mode = gui.TRIGGER_HOTKEY


class _FakeEvent(object):
    def __init__(self, widget):
        self.widget = widget


check("输入框算文本控件", app._in_text_widget(_FakeEvent(app.entry)) is True)
check("外壳不算文本控件", app._in_text_widget(_FakeEvent(app.shell)) is False)

_fired = []
_h = app._guard_text(lambda: _fired.append(1))
_r = _h(_FakeEvent(app.entry))
check("输入框里不触发全局快捷键", len(_fired) == 0, len(_fired))
check("输入框里不吞键", _r is None, _r)
_h(_FakeEvent(app.shell))
check("非输入框正常触发", len(_fired) == 1, len(_fired))

_before = app._collapsed
app._guard_text(app._toggle_collapse)(_FakeEvent(app.entry))
check("输入框里 Esc 不折叠", app._collapsed == _before, app._collapsed)
app._guard_text(app._toggle_collapse)(_FakeEvent(app.shell))
check("非输入框 Esc 会折叠", app._collapsed != _before, app._collapsed)
if app._collapsed:
    app._toggle_collapse()

# ---------- 暂停监视剪切板 ----------
print("=== 暂停监视 ===")
_before_pause = app._clip_paused
app._toggle_clip_pause()
check("暂停后状态翻转", app._clip_paused != _before_pause, app._clip_paused)
check("暂停写进配置", app.cfg.get("clip_paused") == app._clip_paused,
      app.cfg.get("clip_paused"))
check("暂停按钮说恢复", "恢复" in app.pause_btn.cget("text"),
      app.pause_btn.cget("text"))
check("暂停时角标提示", "暂停" in app.clip_badge.cget("text"),
      app.clip_badge.cget("text"))
app._toggle_clip_pause()
check("恢复后状态复原", app._clip_paused == _before_pause, app._clip_paused)
check("恢复按钮说暂停", "暂停" in app.pause_btn.cget("text"),
      app.pause_btn.cget("text"))

# ---------- 反向转换 ----------
print("=== 反向转换 ===")
app.fmt = "binary"
app.sep = "space"
app.result.configure(fg=gui.TEXT)
app.result.set_text("Hello")
app._reverse_result()
_rev = app.result.get("1.0", "end-1c")
check("反向产物是二进制", bool(_rev.strip()) and all(c in "01 " for c in _rev), _rev[:40])
check("反向回到原文", core.binary_to_text(_rev)[0] == "Hello", core.binary_to_text(_rev)[0])

app.fmt = "hex"
app.result.configure(fg=gui.TEXT)
app.result.set_text("Hi")
app._reverse_result()
_rev_hex = app.result.get("1.0", "end-1c")
check("十六进制反向", core.decode_as(_rev_hex, "hex")[0] == "Hi", _rev_hex[:40])

app.fmt = "binary"
app.sep = "space"
app.result.configure(fg=gui.TEXT)
app.result.set_text("")
app._reverse_result()
check("空译文不反向", app.result.get("1.0", "end-1c").strip() == "",
      app.result.get("1.0", "end-1c"))

# ---------- 转换历史 ----------
print("=== 转换历史 ===")
app._history = []
app._push_history("encode", "binary", "abc", "01100001")
check("历史记了一条", len(app._history) == 1, len(app._history))
check("历史有取值", app._history[0][3] == "01100001", app._history[0][3])
for _i in range(30):
    app._push_history("decode", "hex", str(_i), str(_i))
check("历史上限 %d 条" % app.HISTORY_MAX, len(app._history) == app.HISTORY_MAX,
      len(app._history))
check("历史最新在最前", app._history[0][3] == "29", app._history[0][3])
check("历史区渲染出行", len(app.history_box.winfo_children()) > 0,
      len(app.history_box.winfo_children()))
app._clear_history()
check("清空后没有历史", len(app._history) == 0, len(app._history))
check("清空后显示占位", len(app.history_box.winfo_children()) == 1,
      len(app.history_box.winfo_children()))

# ---------- 方向判断抽公共 ----------
print("=== 转换方向公共方法 ===")
app.fmt = "binary"
app.sep = "space"
app.dir_mode = gui.DIR_TEXT2BIN
_t2b = app._convert_text("Hi")
check("文字转编码方向", _t2b and all(c in "01 " for c in _t2b), _t2b[:40])
app.dir_mode = gui.DIR_BIN2TEXT
check("编码转文字方向", app._convert_text(_t2b) == "Hi", app._convert_text(_t2b))
app.dir_mode = gui.DIR_AUTO
check("自动判断转编码", all(c in "01 " for c in app._convert_text("Hi")),
      app._convert_text("Hi")[:40])
check("自动判断转文字", app._convert_text(_t2b) == "Hi", app._convert_text(_t2b))
check("自动判断认编码", bool(app._convert_text(_t2b).strip()),
      app._convert_text(_t2b)[:20])

app.dir_mode = gui.DIR_TEXT2BIN

# ---------- 批量转换 ----------
print("=== 多行批量 ===")
app._clip_paused = True          # 别让轮询把剪切板改回去
clipwin.write_text("ab\ncd")
app._last_clip = "ab\ncd"
app._last_seq = clipwin.sequence()
app._batch_convert()
_batch = app.bin_out.get("1.0", "end-1c")
check("批量有两行", _batch.count("\n") == 1, _batch.count("\n"))
_lines = [ln for ln in _batch.split("\n") if ln.strip()]
check("批量行都转成二进制",
      all(all(c in "01 " for c in ln) for ln in _lines), _lines)
check("批量第一行是 ab",
      core.binary_to_text(_lines[0])[0] == "ab", core.binary_to_text(_lines[0])[0])
check("批量第二行是 cd",
      core.binary_to_text(_lines[1])[0] == "cd", core.binary_to_text(_lines[1])[0])
check("批量记了历史", app._history and app._history[0][2] == "ab\ncd",
      app._history[0][2] if app._history else None)

# 空剪切板不该炸
clipwin.write_text("")
app._last_clip = ""
app._batch_convert()
check("空剪切板给提示", "剪切板" in app.status.cget("text"),
      app.status.cget("text"))

# ---------- 导出与读文件 ----------
print("=== 导出 / 读文件 ===")
_out_path = os.path.join(_TMP_APPDATA, "out.txt")
app.bin_out.set_text("导出内容 abc")
_orig_save = gui.filedialog.asksaveasfilename
gui.filedialog.asksaveasfilename = lambda **kw: _out_path
try:
    app._export_result()
finally:
    gui.filedialog.asksaveasfilename = _orig_save
check("导出写了文件", os.path.exists(_out_path), _out_path)
check("导出内容正确",
      open(_out_path, encoding="utf-8").read() == "导出内容 abc",
      open(_out_path, encoding="utf-8").read() if os.path.exists(_out_path) else "")

_empty_out = os.path.join(_TMP_APPDATA, "empty.txt")
app.bin_out.set_text("")
gui.filedialog.asksaveasfilename = lambda **kw: _empty_out
try:
    app._export_result()
finally:
    gui.filedialog.asksaveasfilename = _orig_save
check("空结果不导出", not os.path.exists(_empty_out), os.path.exists(_empty_out))

_src_path = os.path.join(_TMP_APPDATA, "src.txt")
with open(_src_path, "w", encoding="utf-8") as fh:
    fh.write("第一行\n第二行\n")
_orig_open = gui.filedialog.askopenfilename
gui.filedialog.askopenfilename = lambda **kw: _src_path
try:
    app._load_from_file()
finally:
    gui.filedialog.askopenfilename = _orig_open
check("读文件进输入框", "第一行" in app.input_var.get(), app.input_var.get())

app._clip_paused = _before_pause

# ---------- 设置卡片 ----------
print("=== 设置卡片 ===")
check("有透明度滑块", hasattr(app, "alpha_scale"), hasattr(app, "alpha_scale"))
check("滑块范围 25-100",
      (int(float(app.alpha_scale.cget("from"))),
       int(float(app.alpha_scale.cget("to")))) == (25, 100),
      (app.alpha_scale.cget("from"), app.alpha_scale.cget("to")))

app.alpha_var.set(50)
app._on_alpha_slide(50)
check("拖到 50% 标签跟着变", app.alpha_lbl.cget("text") == "50%",
      app.alpha_lbl.cget("text"))
check("拖到 50% 窗口真的变了",
      abs(float(app.root.attributes("-alpha")) - 0.5) < 0.02,
      app.root.attributes("-alpha"))
app._on_alpha_release()
check("松手才写进配置", abs(float(app.cfg.get("alpha") or 0) - 0.5) < 0.02,
      app.cfg.get("alpha"))

# 下限保护：拖到最左也不能全透明
app.alpha_var.set(1)
app._on_alpha_slide(1)
check("透明度有下限",
      float(app.root.attributes("-alpha")) >= 0.24,
      app.root.attributes("-alpha"))

# ---------- 恢复默认 ----------
print("=== 恢复默认 ===")
app.trigger_mode = gui.TRIGGER_OFF
app.space_count = 5
app.dir_mode = gui.DIR_BIN2TEXT
app.fmt = "hex"
app.sep = "newline"
app._clip_paused = True
app.topmost = False
app.cfg["alpha"] = 0.5
# x/y 平时是拖动后才写进配置的，这里先塞个值，验证恢复默认不会把它俩清掉
app.cfg["x"] = 123
app.cfg["y"] = 456
app._reset_defaults()
check("恢复触发方式", app.trigger_mode == gui.TRIGGER_HOTKEY, app.trigger_mode)
check("恢复空格次数", app.space_count == 2, app.space_count)
check("恢复快捷键", app.hotkey_spec == hotkey_mod.DEFAULT_HOTKEY, app.hotkey_spec)
check("恢复转换方向", app.dir_mode == gui.DIR_AUTO, app.dir_mode)
check("恢复编码格式", app.fmt == "binary", app.fmt)
check("恢复分隔符", app.sep == "space", app.sep)
check("恢复监视开关", app._clip_paused is False, app._clip_paused)
check("恢复置顶", app.topmost is True, app.topmost)
check("恢复不透明度", abs(float(app.root.attributes("-alpha")) - 1.0) < 0.02,
      app.root.attributes("-alpha"))
check("恢复写进配置", app.cfg.get("trigger_mode") == gui.TRIGGER_HOTKEY
      and app.cfg.get("format") == "binary", app.cfg.get("trigger_mode"))
check("恢复后滑块归位", int(float(app.alpha_var.get())) == 100,
      app.alpha_var.get())
check("恢复后按钮文案跟着变",
      app.trigger_btn.cget("text") == gui.TRIGGER_TEXT[gui.TRIGGER_HOTKEY],
      app.trigger_btn.cget("text"))
check("恢复保留窗口位置",
      app.cfg.get("x") == 123 and app.cfg.get("y") == 456,
      (app.cfg.get("x"), app.cfg.get("y")))

# ---------- 宽度滑块 ----------
print()
print("== 宽度滑块 ==")
app._apply_width(600, persist=True)
root.update()
check("宽度生效", app.width == 600 and app.root.winfo_width() == 600,
      (app.width, app.root.winfo_width()))
check("宽度写进配置", app.cfg.get("width") == 600, app.cfg.get("width"))
check("宽度标签跟着变", app.width_lbl.cget("text") == "600px",
      app.width_lbl.cget("text"))

app._apply_width(100, persist=False)
check("低于下限被夹住", app.width == gui.MIN_WIDTH, app.width)
app._apply_width(9999, persist=False)
check("高于上限被夹住", app.width == gui.MAX_WIDTH, app.width)

app._apply_width(gui.W_WIDTH, persist=False)
check("拖回默认宽度", app.width == gui.W_WIDTH, app.width)

# ---------- 快捷键冲突警告 ----------
print()
print("== 快捷键冲突 ==")
check("系统组合被标出",
      bool(hotkey_mod.conflict_warning(hotkey_mod.load("ctrl+alt+delete"))),
      hotkey_mod.conflict_warning(hotkey_mod.load("ctrl+alt+delete")))
check("Win+L 被标出",
      bool(hotkey_mod.conflict_warning(hotkey_mod.load("win+l"))),
      hotkey_mod.conflict_warning(hotkey_mod.load("win+l")))
check("默认组合不冲突",
      hotkey_mod.conflict_warning(hotkey_mod.load(hotkey_mod.DEFAULT_HOTKEY)) == "",
      hotkey_mod.conflict_warning(hotkey_mod.load(hotkey_mod.DEFAULT_HOTKEY)))
check("None 不炸", hotkey_mod.conflict_warning(None) == "",
      hotkey_mod.conflict_warning(None))

# ---------- 录制实时回显 ----------
print()
print("== 录制回显 ==")
app._capturing = True
app._show_capture_hint("Ctrl+Alt")
check("回显显示已按住的修饰键",
      "Ctrl+Alt" in app.hotkey_btn.cget("text"), app.hotkey_btn.cget("text"))
check("回显提示再按主键",
      "主键" in app.hotkey_hint.cget("text"), app.hotkey_hint.cget("text"))
app._show_capture_hint("")
check("松开修饰键回到录制提示",
      app.hotkey_btn.cget("text") == "按下新的组合键…",
      app.hotkey_btn.cget("text"))
app._capturing = False
app._paint_hotkey_btn()

# ---------- 首次运行引导 ----------
print()
print("== 首次运行 ==")
check("首次引导已标记", app.cfg.get("first_run_done") is True,
      app.cfg.get("first_run_done"))

# ---------- 长文本保护 ----------
print()
print("== 长文本保护 ==")
# Entry.insert 的耗时是超线性的（实测 5 千字 0.44 秒、2 万字 5.9 秒、
# 5 万字 36 秒），所以超过上限的文字不进输入框，直接转换到结果区。
check("长度上限是个正整数", isinstance(gui.MAX_ENTRY_CHARS, int)
      and gui.MAX_ENTRY_CHARS > 0, gui.MAX_ENTRY_CHARS)
app._fill_manual("Hi")
check("短文本仍然进输入框", app.input_var.get() == "Hi", app.input_var.get())
big = "你" * (gui.MAX_ENTRY_CHARS + 10)
_t0 = time.time()
app._fill_manual(big)
_elapsed = time.time() - _t0
check("长文本不卡（1 秒内返回）", _elapsed < 1.0, "%.2fs" % _elapsed)
check("长文本没有塞进输入框", app.input_var.get() == "", len(app.input_var.get()))
check("长文本已经转换到结果区",
      len(app.bin_out.get("1.0", "end-1c")) > gui.MAX_ENTRY_CHARS * 8,
      len(app.bin_out.get("1.0", "end-1c")))
check("长文本有提示", "没放进输入框" in app.status.cget("text"),
      app.status.cget("text"))

# ---------- 解码为文件的输入校验 ----------
print()
print("== 解码为文件 ==")
# 结果区里不是纯 0/1 的时候必须给提示，不能抛异常（早先 _clean_bits 放过了
# 字母，int(x, 2) 抛 ValueError 且没人接，用户点一下毫无反应）。
app.bin_out.set_text("zzz")
try:
    app._decode_to_file()
    check("非二进制内容不抛异常", True)
except Exception as exc:
    check("非二进制内容不抛异常", False, repr(exc))
check("非二进制内容有提示", "不是二进制" in app.status.cget("text"),
      app.status.cget("text"))
app.bin_out.set_text("")
app._decode_to_file()
check("空结果区有提示", "是空的" in app.status.cget("text"),
      app.status.cget("text"))

# ---------- 拖入长文本 ----------
print()
print("== 拖入长文本 ==")
import tempfile as _tf
_d = _tf.mkdtemp(prefix="binfloater_drop_")
_long = os.path.join(_d, "long.txt")
with open(_long, "w", encoding="utf-8") as fh:
    fh.write("你好世界" * 2500)
_t0 = time.time()
app._load_dropped(_long)
_elapsed = time.time() - _t0
check("拖长文本不卡（1 秒内）", _elapsed < 1.0, "%.2fs" % _elapsed)
check("长文本走了旁路（提示没被覆盖）",
      "没放进输入框" in app.status.cget("text"), app.status.cget("text"))
_d2 = _tf.mkdtemp(prefix="binfloater_drop_")
app._load_dropped(_d2)
check("拖文件夹有提示", "文件夹" in app.status.cget("text"),
      app.status.cget("text"))

# ---------- 检查更新 ----------
print()
print("== 检查更新 ==")
import update as update_mod
import version as version_mod

check("版本行控件已建", app._update_lbl is not None and app._update_btn is not None)
check("版本行显示当前版本", version_mod.VERSION in app._update_lbl.cget("text"),
      app._update_lbl.cget("text"))
check("自动检查开关已建", app.update_auto_btn is not None)
check("自动检查默认开", app._update_auto is True, app._update_auto)

# 初始态：没查过，按钮叫「检查更新」
app._update_result = None
app._update_checking = False
app._paint_update_row()
check("初始按钮文案", app._update_btn.cget("text") == "检查更新",
      app._update_btn.cget("text"))

# 查到新版：文字要点出新版本号，按钮变「去下载」
app._on_update_result(update_mod.UpdateInfo(
    update_mod.OK, latest="v9.9.9", current=version_mod.VERSION,
    body="# 更新\n- 修了 A", html_url="https://example.com/dl"))
check("查到新版时文字带新版本号", "v9.9.9" in app._update_lbl.cget("text"),
      app._update_lbl.cget("text"))
check("查到新版时按钮变去下载", app._update_btn.cget("text") == "去下载",
      app._update_btn.cget("text"))
check("查到新版时文字用亮色", app._update_lbl.cget("fg") == gui.TEAL,
      app._update_lbl.cget("fg"))
check("查到新版时状态栏有提示", "新版本" in app.status.cget("text"),
      app.status.cget("text"))

# 点「去下载」不应该重新发请求，而是直接开网页（这里拦掉 startfile）
_opened = []
_orig_startfile = os.startfile
os.startfile = lambda p: _opened.append(p)
try:
    app._check_update(manual=True)
    check("去下载直接开网页", _opened == ["https://example.com/dl"], _opened)
    check("去下载不重置成检查中", app._update_checking is False)
finally:
    os.startfile = _orig_startfile

# 已是最新：按钮回到「检查更新」
app._on_update_result(update_mod.UpdateInfo(
    update_mod.UP_TO_DATE, "已经是最新版本", latest="v1.0.0",
    current=version_mod.VERSION))
check("已是最新时文字", "已是最新" in app._update_lbl.cget("text"),
      app._update_lbl.cget("text"))
check("已是最新时按钮回到检查更新",
      app._update_btn.cget("text") == "检查更新", app._update_btn.cget("text"))

# 仓库没公开（404）：别说成「网络故障」，要直说读不到
app._on_update_result(update_mod.UpdateInfo(
    update_mod.NOT_FOUND, "仓库还没公开，读不到发布信息"))
check("没公开时文字说清楚", "还没公开" in app._update_lbl.cget("text"),
      app._update_lbl.cget("text"))
check("没公开时状态栏也不当成网络错误",
      "网络" not in app.status.cget("text"), app.status.cget("text"))

# 网络失败：原因要原样透出来，不能只说「检查失败」
app._on_update_result(update_mod.UpdateInfo(
    update_mod.NETWORK, "连不上 GitHub：timed out"))
check("网络失败带原因", "timed out" in app.status.cget("text"),
      app.status.cget("text"))

# 自动检查开关能关，且写进配置
app._toggle_auto_update()
check("关掉自动检查", app._update_auto is False)
check("关掉后写配置", app.cfg.get("update_check") is False, app.cfg.get("update_check"))
check("关掉后按钮文案", "关" in app.update_auto_btn.cget("text"),
      app.update_auto_btn.cget("text"))
app._toggle_auto_update()
check("再开回来", app._update_auto is True and app.cfg.get("update_check") is True)

# 关掉开关时 _maybe_auto_check_update 不应该发请求
app.cfg["update_check"] = False
_called = []
_orig_check = app._check_update
app._check_update = lambda manual=False: _called.append(manual)
try:
    app._maybe_auto_check_update()
    check("关掉后启动不自动查", _called == [], _called)
    # 打开开关，但今天已经查过，也不该再查
    app.cfg["update_check"] = True
    app._update_last_day = time.strftime("%Y-%m-%d")
    app._maybe_auto_check_update()
    check("同一天不重复查", _called == [], _called)
    # 换成昨天，应该会查一次（而且不弹手动提示）
    app._update_last_day = "1970-01-01"
    app._maybe_auto_check_update()
    check("换天后会查一次", _called == [False], _called)
    check("查过就记下日期", app._update_last_day == time.strftime("%Y-%m-%d"),
          app._update_last_day)
finally:
    app._check_update = _orig_check
    app.cfg["update_check"] = True
    app._update_auto = True
    app._paint_auto_update_btn()

# 后台线程 → 队列 → 主线程，这条通道要通。
# 用的是假数据，只验证队列能取到东西、状态被重置。
app._update_result = None
app._update_checking = True
app._updates.put_nowait(update_mod.UpdateInfo(
    update_mod.NETWORK, "假失败"))
app._drain_updates()
check("队列结果被处理", app._update_checking is False)
check("队列结果进了状态", app._update_result is not None
      and app._update_result.message == "假失败")

try:
    import shutil as _sh
    _sh.rmtree(_d, ignore_errors=True)
    _sh.rmtree(_d2, ignore_errors=True)
except Exception:
    pass

app._release_watcher()
root.destroy()

# 临时配置目录用完就删，别在系统临时区留垃圾
try:
    import shutil
    shutil.rmtree(_TMP_APPDATA, ignore_errors=True)
except Exception:
    pass

print()
print("通过 %d 项，失败 %d 项" % (len(ok), len(bad)))
sys.exit(0 if not bad else 1)
