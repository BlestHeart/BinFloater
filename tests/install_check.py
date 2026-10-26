# -*- coding: utf-8 -*-
"""装/卸实测：确认装到非 C 盘、目录页存在、配置跟着安装目录走。

跑法：python tests/install_check.py
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time

k32 = ctypes.windll.kernel32
TYPES = {2: "可移动", 3: "固定", 4: "网络", 5: "光驱", 6: "内存盘", 0: "未知", 1: "无根目录"}


def drives():
    out = []
    for code in range(ord("C"), ord("Z") + 1):
        root = chr(code) + ":\\"
        out.append((chr(code), k32.GetDriveTypeW(root)))
    return out


def find_dir_choice():
    """GetDefaultDir 会挑第一个「固定硬盘且可写」的 D..Z 盘。"""
    for letter, t in drives():
        if letter == "C":
            continue
        if t != 3:
            continue
        probe_dir = os.path.join(letter + ":\\", "Programs")
        try:
            os.makedirs(probe_dir, exist_ok=True)
            p = os.path.join(probe_dir, ".write-probe")
            with open(p, "w") as fh:
                fh.write("x")
            os.remove(p)
            return letter + ":\\Programs\\BinFloater"
        except Exception:
            continue
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "BinFloater")


def main():
    ok, bad = [], []

    def check(name, cond, detail=""):
        (ok if cond else bad).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))

    print("盘符情况：")
    for letter, t in drives():
        if t:
            print("  %s: %s" % (letter, TYPES.get(t, t)))

    want = find_dir_choice()
    print("\n预期安装到：%s" % want)
    check("默认位置不在 C 盘", not want.upper().startswith("C:"), want)

    setup = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "dist", "BinFloater-Setup-1.4.0.exe")
    check("安装包存在", os.path.isfile(setup), setup)
    if not os.path.isfile(setup):
        return finish(ok, bad)

    print("\n静默安装到 %s ..." % want)
    shutil.rmtree(want, ignore_errors=True)
    rc = subprocess.call([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                          "/DIR=" + want], timeout=300)
    time.sleep(2)
    check("静默安装返回 0", rc == 0, "rc=%d" % rc)
    check("主程序装好了", os.path.isfile(os.path.join(want, "BinFloater.exe")),
          want)
    check("_internal 装好了", os.path.isdir(os.path.join(want, "_internal")))

    # 配置该落在安装目录，而不是 %APPDATA%。
    # 注意：程序只在「改了设置」或「正常退出」时才写 config.json，
    # 所以这里必须发 WM_CLOSE 让它自己收尾，不能直接 taskkill /F。
    cfg = os.path.join(want, "config.json")
    appdata_cfg = os.path.join(os.environ.get("APPDATA", ""), "BinFloater", "config.json")
    before = os.path.getmtime(appdata_cfg) if os.path.isfile(appdata_cfg) else None

    proc = subprocess.Popen([os.path.join(want, "BinFloater.exe")])
    time.sleep(4)

    hwnd = 0
    try:
        u = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(h, _l):
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, buf, 256)
            if buf.value == "BinFloater":
                found.append(h)
            return True

        u.EnumWindows(_cb, 0)
        hwnd = found[0] if found else 0
    except Exception:
        pass
    check("程序窗口起来了", hwnd != 0, "hwnd=%s" % hwnd)

    if hwnd:
        u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE，走正常退出流程
    try:
        proc.wait(timeout=15)
    except Exception:
        subprocess.call(["taskkill", "/IM", "BinFloater.exe", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)

    check("启动后配置写到安装目录", os.path.isfile(cfg), cfg)
    after = os.path.getmtime(appdata_cfg) if os.path.isfile(appdata_cfg) else None
    check("没往 %APPDATA% 里新写配置", before == after, appdata_cfg)

    # 卸载
    print("\n卸载 ...")
    uninst = os.path.join(want, "unins000.exe")
    check("卸载程序存在", os.path.isfile(uninst), uninst)
    if os.path.isfile(uninst):
        rc = subprocess.call([uninst, "/VERYSILENT", "/SUPPRESSMSGBOXES",
                              "/NORESTART"], timeout=300)
        time.sleep(3)
        check("静默卸载返回 0", rc == 0, "rc=%d" % rc)
        check("主程序被删掉", not os.path.isfile(os.path.join(want, "BinFloater.exe")))
        check("安装目录基本清空",
              not os.path.isdir(want) or len(os.listdir(want)) <= 2,
              os.listdir(want) if os.path.isdir(want) else "已删除")

    return finish(ok, bad)


def finish(ok, bad):
    print("")
    print("通过 %d 项，失败 %d 项" % (len(ok), len(bad)))
    if bad:
        print("失败项：" + " / ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
