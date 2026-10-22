# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir（文件夹）模式。

为什么不用 onefile：
  onefile 每次启动都要把 11MB 内容解包到 %TEMP%\_MEIxxxx，
  360安全卫士 / 火绒 这类安全软件容易拦截这个行为，弹
  "Could not create temporary directory!"。
  onedir 直接在本目录加载依赖，完全不解包，启动更快也更稳。

产物：dist/BinFloater/BinFloater.exe
"""
import os

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    # tray.py 用 ctypes 直接调 Shell_NotifyIconW，没有第三方依赖；
    # icon.ico 作为 data 带上，托盘图标从 sys._MEIPASS 里读。
    datas=[("icon.ico", ".")] if os.path.exists("icon.ico") else [],
    hiddenimports=["clipwin", "core", "gui", "keys", "trigger", "tray", "hotkey"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "PIL", "pytest", "setuptools"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # 依赖放到文件夹里，不打进 exe
    name="BinFloater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico" if os.path.exists("icon.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BinFloater",
)
