# -*- coding: utf-8 -*-
"""冒烟测试：真的把 GUI 建起来，检查控件和钩子是否就绪，然后退出。"""
import os
import sys
import tempfile
import tkinter as tk

# 必须在 import gui 之前把 APPDATA 指到临时目录：gui.config_path() 读的就是它。
# 否则这个测试会写真实配置——曾经把 tray_hint_shown 置成 true，
# 结果用户永远看不到「已收进托盘」那条气泡通知。
_TMP_APPDATA = tempfile.mkdtemp(prefix="binfloater_smoke_")
os.environ["APPDATA"] = _TMP_APPDATA

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clipwin
import core
import gui
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
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))

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
