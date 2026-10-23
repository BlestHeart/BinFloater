# -*- coding: utf-8 -*-
"""配置读写测试。跑法：python tests/test_config.py

专门盯一个用户的真实反馈：「每次打开都要重新设」。排查下来最可能的原因是
配置写盘不是原子的——直接以 "w" 打开会先把原文件截断，万一进程在写入中途
被结束（杀进程、崩溃、安全软件拦写盘），留下的就是空文件，下次启动所有
设置回到默认值，用户看到的现象就是「没记住」。这里把这条链路钉住。
"""
import json
import os
import shutil
import sys
import tempfile

# 必须在 import gui 之前把 APPDATA 指到临时目录，否则会写用户的真实配置
_TMP_APPDATA = tempfile.mkdtemp(prefix="binfloater_cfg_")
os.environ["APPDATA"] = _TMP_APPDATA

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui  # noqa: E402

ok = []
bad = []


def check(name, cond, detail=""):
    (ok if cond else bad).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + str(detail)) if detail else ""))


CFG = gui.config_path()


def write_raw(text):
    with open(CFG, "w", encoding="utf-8") as fh:
        fh.write(text)


def reset():
    folder = os.path.dirname(CFG)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)


# 1. 存进去能读回来
reset()
gui.save_config({"trigger_mode": "space", "space_count": 4})
got = gui.load_config()
check("存了能读回来", got == {"trigger_mode": "space", "space_count": 4}, got)

# 2. 不留下临时文件（原子写必须把 .tmp 换掉而不是留着）
leftovers = [n for n in os.listdir(os.path.dirname(CFG)) if n.endswith(".tmp")]
check("没有残留 .tmp 文件", not leftovers, leftovers)

# 3. 写入不会破坏已有内容：模拟「旧配置在、新配置写一半」的中间态不可能出现
reset()
gui.save_config({"a": 1})
first = open(CFG, encoding="utf-8").read()
gui.save_config({"a": 2})
second = open(CFG, encoding="utf-8").read()
check("覆盖写成功", json.loads(second) == {"a": 2}, second)
check("覆盖写不是追加", first != second)

# 4. 文件不存在时返回空字典，不抛异常
reset()
check("配置文件不存在返回 {}", gui.load_config() == {})

# 5. 空文件（上次写到一半被打断）返回空字典，不抛异常
reset()
write_raw("")
check("空文件返回 {}", gui.load_config() == {})

# 6. 坏 JSON 返回空字典，并且把坏文件备份成 .bad —— 不能直接覆盖掉，
#    否则用户的手工修改就永久没了，也没法诊断到底坏在哪
reset()
write_raw('{"trigger_mode": "spa')
check("坏 JSON 返回 {}", gui.load_config() == {})
bak = CFG + ".bad"
check("坏文件被备份成 .bad", os.path.isfile(bak))
if os.path.isfile(bak):
    check(".bad 保留了原文", open(bak, encoding="utf-8").read() == '{"trigger_mode": "spa')

# 7. 坏 JSON 之后的正常保存要能恢复，不能被 .bad 影响
gui.save_config({"trigger_mode": "off"})
check("坏文件之后还能正常保存", gui.load_config() == {"trigger_mode": "off"})

# 8. 顶层不是对象（数组/字符串）也当空处理，避免 .get 崩掉
reset()
write_raw('[1, 2, 3]')
check("顶层是数组时返回 {}", gui.load_config() == {})
reset()
write_raw('"just a string"')
check("顶层是字符串时返回 {}", gui.load_config() == {})

# 9. 出错会写 error.log（没有日志的话线上问题只能靠猜）
reset()
write_raw("{broken")
gui.load_config()
log_path = os.path.join(os.path.dirname(CFG), "error.log")
check("坏配置写了 error.log", os.path.isfile(log_path))
if os.path.isfile(log_path):
    txt = open(log_path, encoding="utf-8").read()
    check("error.log 里有解析失败记录", "解析失败" in txt, txt[:120])

# 10. 源码层约定：save_config 必须是「临时文件 + os.replace」，
#     不能直接用 open(path, "w") 截断原文件
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gui.py"),
           encoding="utf-8").read()
idx = src.find("def save_config")
body = src[idx:idx + 900]
check("save_config 用 os.replace 原子替换", "os.replace(tmp, path)" in body)
check("save_config 先写 .tmp", 'tmp = path + ".tmp"' in body)
check("save_config 不再吞掉异常", "except Exception as exc" in body)

# 11. load_config 不能把坏文件直接丢掉
idx = src.find("def load_config")
body = src[idx:idx + 900]
check("load_config 会备份坏文件", '" .bad"' in body or '".bad"' in body)

shutil.rmtree(_TMP_APPDATA, ignore_errors=True)

print("")
print("通过 %d 项，失败 %d 项" % (len(ok), len(bad)))
if bad:
    print("失败项：" + " / ".join(bad))
    sys.exit(1)
