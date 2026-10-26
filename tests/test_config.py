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

# 必须在 import gui 之前把配置目录指到临时目录，否则会写用户的真实配置。
# BINF_CONFIG_DIR 是 1.4 起的显式覆盖；APPDATA 留着是因为还要测老位置迁移。
_TMP_APPDATA = tempfile.mkdtemp(prefix="binfloater_cfg_")
os.environ["APPDATA"] = _TMP_APPDATA
os.environ["BINF_CONFIG_DIR"] = _TMP_APPDATA

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

# 12. 类型转换辅助函数：配置是纯文本，用户手改、旧版本遗留、写到一半断电
#     都可能留下奇怪的值。早先这里是裸的 float(cfg.get(...))，一个
#     alpha:"abc" 就让程序在建窗口时抛 ValueError，双击 exe 毫无反应。
check("有 _as_float", "def _as_float(" in src)
check("有 _as_int", "def _as_int(" in src)
check("有 _as_bool", "def _as_bool(" in src)
check("alpha 走 _as_float", '_as_float(self.cfg.get("alpha"' in src)
check("x 走 _as_int", '_as_int(self.cfg.get("x")' in src)
check("y 走 _as_int", '_as_int(self.cfg.get("y")' in src)
def _bare_casts(text):
    """找出裸的 float(cfg.get / int(cfg.get，排除 _as_float / _as_int 包裹。"""
    hits = []
    for line in text.splitlines():
        for fn in ("float", "int"):
            needle = fn + "(self.cfg.get("
            pos = line.find(needle)
            while pos >= 0:
                # _as_float( / _as_int( 里的那个不算
                if not line[:pos].endswith("_as_"):
                    hits.append(line.strip())
                    break
                pos = line.find(needle, pos + 1)
    return hits


check("没有裸 float(cfg.get", not _bare_casts(src), _bare_casts(src)[:2])
check("没有裸 int(cfg.get", True if not any("int(self.cfg.get(" in h for h in _bare_casts(src)) else False)

# _as_bool 的语义：字符串 "false" 在 Python 里是真值，用户手改配置写成
# "false" 反而会打开开关，跟直觉相反，所以必须显式识别。
check("_as_bool 认得 false 字符串", gui._as_bool("false", True) is False)
check("_as_bool 认得 off 字符串", gui._as_bool("off", True) is False)
check("_as_bool 认得 no 字符串", gui._as_bool("no", True) is False)
check("_as_bool 认得 0 字符串", gui._as_bool("0", True) is False)
check("_as_bool 认得 yes 字符串", gui._as_bool("yes", False) is True)
check("_as_bool 保留真 bool", gui._as_bool(True, False) is True)
check("_as_bool 乱值走默认", gui._as_bool("嗯", True) is True)
check("_as_bool 列表走默认", gui._as_bool([1], False) is False)

check("_as_float 坏值走默认", gui._as_float("abc", 0.9) == 0.9)
check("_as_float 收 None", gui._as_float(None, 0.9) == 0.9)
check("_as_float 夹上界", gui._as_float(5.0, 1.0, 0.2, 1.0) == 1.0)
check("_as_float 夹下界", gui._as_float(0.0, 1.0, 0.2, 1.0) == 0.2)
check("_as_float 挡 NaN", gui._as_float(float("nan"), 0.9) == 0.9)

check("_as_int 认浮点字符串", gui._as_int("12.0", 0) == 12)
check("_as_int 坏值走默认", gui._as_int("左边", 7) == 7)
check("_as_int 收 None", gui._as_int(None, 7) == 7)
check("_as_int 收列表", gui._as_int([1], 7) == 7)
check("_as_int 夹上界", gui._as_int(99999999, 0, 0, 100) == 100)

# 13. 配置放哪儿：用户的硬要求是「不准在 C 盘拉屎」。
#     1.4 起配置跟着安装目录走——装到 D 盘，config.json 就在 D 盘，
#     不再往 %APPDATA%（C 盘）里丢。这里把三种分支都钉住。
check("有 config_dir", "def config_dir(" in src)
check("有 _exe_dir", "def _exe_dir(" in src)
check("有 _dir_writable", "def _dir_writable(" in src)


def _with_env(**kw):
    """临时改环境变量 + 清掉目录缓存，用完还原。"""
    saved = {k: os.environ.get(k) for k in kw}
    saved_dir = gui._CONFIG_DIR
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    gui._CONFIG_DIR = None
    return saved, saved_dir


def _restore(saved, saved_dir):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    gui._CONFIG_DIR = saved_dir


# 分支一：BINF_CONFIG_DIR 显式指定，优先于一切
_want = tempfile.mkdtemp(prefix="binfloater_where_")
_saved, _sdir = _with_env(BINF_CONFIG_DIR=_want)
check("BINF_CONFIG_DIR 生效", gui.config_dir() == _want, gui.config_dir())
check("config.json 在那个目录下",
      gui.config_path() == os.path.join(_want, "config.json"))
_restore(_saved, _sdir)

# 分支二：打包成 exe 且程序目录可写 → 配置放 exe 旁边（安装目录）
#         用假的 sys.frozen + sys.executable 模拟，不用真打包。
_fake_dir = tempfile.mkdtemp(prefix="binfloater_exe_")
_saved, _sdir = _with_env(BINF_CONFIG_DIR=None)
_saved_exe = sys.executable
_old_frozen = getattr(sys, "frozen", None)
sys.frozen = True
sys.executable = os.path.join(_fake_dir, "BinFloater.exe")
gui._CONFIG_DIR = None
check("打包后配置放 exe 旁边（安装目录）", gui.config_dir() == _fake_dir, gui.config_dir())
check("打包后不再写 %APPDATA%",
      not gui.config_path().lower().startswith(os.environ.get("APPDATA", "\0").lower() + os.sep
                                               + "binfloater"),
      gui.config_path())
_restore(_saved, _sdir)
sys.executable = _saved_exe
if _old_frozen is None:
    try:
        del sys.frozen
    except AttributeError:
        pass
else:
    sys.frozen = _old_frozen
gui._CONFIG_DIR = None

# 分支三：_dir_writable 真的会去写，写不了就返回 False
check("_dir_writable 可写目录返回 True", gui._dir_writable(_fake_dir) is True)
check("_dir_writable 非法路径返回 False",
      gui._dir_writable(os.path.join(_fake_dir, "a\0b")) is False)
# 探针文件用完必须删掉，不能留在目录里
check("探针文件没残留", not os.path.exists(os.path.join(_fake_dir, ".write-probe")))

# 分支四：老位置的配置要能自动搬过来，否则老用户升级后「设置全没了」
_legacy_root = tempfile.mkdtemp(prefix="binfloater_legacy_")
_new_root = tempfile.mkdtemp(prefix="binfloater_new_")
os.makedirs(os.path.join(_legacy_root, "BinFloater"), exist_ok=True)
_legacy_cfg = os.path.join(_legacy_root, "BinFloater", "config.json")
with open(_legacy_cfg, "w", encoding="utf-8") as _fh:
    _fh.write('{"space_count": 5}')
_saved, _sdir = _with_env(APPDATA=_legacy_root, BINF_CONFIG_DIR=_new_root)
gui._migrate_legacy_config()
_migrated = os.path.join(_new_root, "config.json")
check("老配置被搬到新位置", os.path.isfile(_migrated))
check("搬过来的内容一致", gui.load_config().get("space_count") == 5,
      gui.load_config())
# 新位置已有配置时不覆盖
with open(_migrated, "w", encoding="utf-8") as _fh:
    _fh.write('{"space_count": 9}')
gui._migrate_legacy_config()
check("新位置有配置就不覆盖", gui.load_config().get("space_count") == 9)
_restore(_saved, _sdir)
for _d in (_legacy_root, _new_root, _fake_dir):
    shutil.rmtree(_d, ignore_errors=True)

# 14. 安装脚本：目录页必须出现，默认值不能是写死的 C 盘路径
_iss_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "installer", "BinFloater.iss")
if os.path.isfile(_iss_path):
    with open(_iss_path, "r", encoding="utf-8-sig") as _fh:
        _iss = _fh.read()
    check("安装包有目录页", "DisableDirPage=no" in _iss)
    check("默认目录由代码算", "DefaultDirName={code:GetDefaultDir}" in _iss)
    check("有 GetDefaultDir", "function GetDefaultDir(" in _iss)
    check("挑盘时看 DriveType", "GetDriveTypeA(" in _iss)
    check("真的试写一个文件", "SaveStringToFile(" in _iss)
    check("固定 AppId 不能改", "{6E078910-A416-4F0B-91CE-0B0130B04265" in _iss)
    check("iss 里没有写死本机路径",
          "G:\\" not in _iss and "C:\\Users" not in _iss and "Administrator" not in _iss)
else:
    check("找得到安装脚本", False, _iss_path)

shutil.rmtree(_TMP_APPDATA, ignore_errors=True)

print("")
print("通过 %d 项，失败 %d 项" % (len(ok), len(bad)))
if bad:
    print("失败项：" + " / ".join(bad))
    sys.exit(1)
