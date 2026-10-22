# -*- coding: utf-8 -*-
"""冒烟测试：真的把 GUI 建起来，检查控件和钩子是否就绪，然后退出。"""
import os
import sys
import tkinter as tk

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

print("=== 钩子 ===")
check("默认模式是快捷键", app.trigger_mode == gui.TRIGGER_HOTKEY, app.trigger_mode)
check("watcher 已装", app._watcher is not None)
check("hotkey_mode 开", app._watcher is not None and app._watcher.hotkey_mode)
check("space_mode 关", app._watcher is not None and not app._watcher.space_mode)

print("=== 模式切换 ===")
app._cycle_trigger_mode()  # hotkey -> space
root.update()
check("切到双击空格", app.trigger_mode == gui.TRIGGER_SPACE, app.trigger_mode)
check("space_mode 开", app._watcher is not None and app._watcher.space_mode)
check("hotkey_mode 关", app._watcher is not None and not app._watcher.hotkey_mode)
app._cycle_trigger_mode()  # space -> off
root.update()
check("切到关闭", app.trigger_mode == gui.TRIGGER_OFF, app.trigger_mode)
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

print("=== 窗口 ===")
check("窗口有尺寸", root.winfo_width() > 300, root.winfo_width())
check("窗口有高度", root.winfo_height() > 400, root.winfo_height())

app._release_watcher()
root.destroy()

print()
print("通过 %d 项，失败 %d 项" % (len(ok), len(bad)))
sys.exit(0 if not bad else 1)
