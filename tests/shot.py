# -*- coding: utf-8 -*-
"""打开窗口截图，然后退出。用于人工确认界面。"""
import os
import subprocess
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clipwin
import gui

if clipwin.IS_WINDOWS:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshot.png")

root = tk.Tk()
app = gui.BinFloater(root)
root.update()
root.update_idletasks()
root.geometry("+120+80")
root.update()
time.sleep(0.6)
root.update()

x = root.winfo_x()
y = root.winfo_y()
w = root.winfo_width()
h = root.winfo_height()
print("geometry", x, y, w, h)

# 用 PowerShell 的 System.Drawing 截屏，不引入额外依赖
ps = (
    "Add-Type -AssemblyName System.Drawing;"
    "$bmp=New-Object System.Drawing.Bitmap({w},{h});"
    "$g=[System.Drawing.Graphics]::FromImage($bmp);"
    "$g.CopyFromScreen({x},{y},0,0,$bmp.Size);"
    "$bmp.Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png);"
    "$g.Dispose();$bmp.Dispose();"
).format(w=w, h=h, x=x, y=y, out=OUT.replace("\\", "\\\\"))

r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True)
print("rc", r.returncode, r.stderr[:300] if r.stderr else "")
app._release_watcher()
root.destroy()
print("saved", OUT, os.path.getsize(OUT) if os.path.exists(OUT) else "MISSING")
