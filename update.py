# -*- coding: utf-8 -*-
"""检查更新：问 GitHub 要最新 Release，和本地 version.VERSION 比一下。

为什么不做「自动下载替换」：
  - 我们是 onedir 分发，程序跑着的时候 BinFloater.exe 和 _internal 里的 dll
    都被占用，自己替换自己必然失败（Windows 不允许改写正在运行的映像）。
  - 就算先退出再替换，也得起一个外部脚本蹲着等进程结束——这台机器上装着
    360 主动防御和火绒，一个「自己下载 exe 然后替换自己」的行为，被拦是
    大概率事件，而且拦了用户什么提示都看不到。
  - 所以这里只做「发现有新版 → 告诉用户 → 打开下载页让用户自己装」。
    升级本来就走安装包，双击装新版本会自动覆盖，比自更新可靠得多。

为什么不抓 Releases 网页而是走 API：
  网页是给人看的，标签结构随时可能改；API 一次请求就把 tag、更新说明、
  下载页地址全给了，解析成本几乎为零。

仓库是私有的会怎样：
  api.github.com 对私有仓库的匿名请求返回 404（不带 UA 是 403），这是
  GitHub 故意的——不告诉你仓存在不存在。所以本模块把 404 单独归类成
  "not_found"，界面上说清楚是「仓库还没公开」，而不是「连不上网」。
"""

import json
import threading
import urllib.error
import urllib.request

import version

REPO = "BlestHeart/BinFloater"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_PAGE = "https://github.com/%s/releases/latest" % REPO

# GitHub API 不带 User-Agent 会直接 403，这是硬要求不是礼貌
USER_AGENT = "BinFloater/%s (+%s)" % (version.VERSION, RELEASES_PAGE)

READ_TIMEOUT_S = 8
# 响应体上限。releases/latest 正常 10KB 上下，给 512KB 足够宽松，
# 但能挡住「代理返回一个巨大的错误页」这种把内存吃光的情况。
MAX_BODY_BYTES = 512 * 1024

# 状态码
OK = "ok"                  # 查到了，并且确实有新版
UP_TO_DATE = "up_to_date"  # 查到了，已经是最新
NOT_FOUND = "not_found"    # 404：仓库私有 / 不存在 / 还没发过 Release
FORBIDDEN = "forbidden"    # 403：限流或 UA 被拒
HTTP_ERROR = "http_error"  # 其他 4xx/5xx
NETWORK = "network"        # 连不上、超时、DNS、代理挂了
BAD_JSON = "bad_json"      # 拿回来的不是预期结构


class UpdateInfo(object):
    """检查结果。字段全给了默认值，调用方不用挨个判 None。"""

    __slots__ = ("status", "latest", "current", "name", "body",
                 "html_url", "published_at", "message")

    def __init__(self, status, message="", latest="", current=None,
                 name="", body="", html_url="", published_at=""):
        self.status = status
        self.message = message
        self.latest = latest
        self.current = current or version.VERSION
        self.name = name
        self.body = body
        self.html_url = html_url or RELEASES_PAGE
        self.published_at = published_at

    @property
    def has_update(self):
        return self.status == OK

    def __repr__(self):
        return "<UpdateInfo %s latest=%r current=%r>" % (
            self.status, self.latest, self.current)


def summary_line(result, limit=400):
    """把更新说明压成一行能塞进通知/状态栏的文字。

    更新说明是多行 markdown，托盘气泡只显示得了一两行，Tk 状态栏更窄。
    这里去掉标题符号、多余空行，截断到 limit 个字符。
    """
    if result is None:
        return ""
    raw = (result.body or "").strip()
    if not raw:
        return ""
    lines = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            continue
        # 去掉 markdown 的 # 标题前缀和 - * 列表符号，纯文本里它们只是噪音
        s = s.lstrip("#").strip()
        if s[:1] in ("-", "*", "+"):
            s = s[1:].strip()
        if s:
            lines.append(s)
    text = " / ".join(lines)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def parse_release(payload, current=None):
    """把 API 返回的 JSON 转成 UpdateInfo。单独拎出来是为了能离线测。"""
    current = current or version.VERSION
    if not isinstance(payload, dict):
        return UpdateInfo(BAD_JSON, "返回的不是一个 Release 对象", current=current)

    tag = payload.get("tag_name") or payload.get("name") or ""
    tag = str(tag).strip()
    if not tag:
        return UpdateInfo(BAD_JSON, "Release 里没有 tag_name", current=current)

    # draft / prerelease 不该推给普通用户：草稿是还没发的，
    # 预发布是给测试用的。GitHub 的 /releases/latest 本身已经排除了这两类，
    # 但万一换成 /releases 列表接口就得靠自己挡。
    if payload.get("draft"):
        return UpdateInfo(UP_TO_DATE, "最新的是一个未发布的草稿", latest=tag,
                          current=current)
    if payload.get("prerelease"):
        return UpdateInfo(UP_TO_DATE, "最新的是一个预发布版", latest=tag,
                          current=current)

    newer = version.is_newer(tag, current)
    return UpdateInfo(
        OK if newer else UP_TO_DATE,
        "" if newer else "已经是最新版本",
        latest=tag,
        current=current,
        name=str(payload.get("name") or tag),
        body=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or RELEASES_PAGE),
        published_at=str(payload.get("published_at") or ""),
    )


def _read_body(resp):
    """按上限读响应体。"""
    chunks = []
    total = 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise ValueError("响应体超过 %d 字节" % MAX_BODY_BYTES)
        chunks.append(chunk)
    return b"".join(chunks)


def fetch(current=None, url=None, timeout=None):
    """同步查一次。网络调用，别在 Tk 主线程里直接调（见 check_async）。"""
    current = current or version.VERSION
    target = url or API_LATEST
    if timeout is None:
        timeout = READ_TIMEOUT_S

    req = urllib.request.Request(target, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        try:
            raw = _read_body(resp)
        finally:
            try:
                resp.close()
            except Exception:
                pass
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code == 404:
            # 私有仓库的匿名请求就是 404，GitHub 不区分「不存在」和「没权限」
            return UpdateInfo(
                NOT_FOUND,
                "仓库还没公开，读不到发布信息",
                current=current,
            )
        if code == 403:
            # 403 有两种：UA 被拒（我们带了，不会）、匿名限流 60 次/小时
            return UpdateInfo(
                FORBIDDEN,
                "GitHub 拒绝了这次请求（可能是访问太频繁）",
                current=current,
            )
        return UpdateInfo(HTTP_ERROR, "服务器返回 HTTP %d" % code, current=current)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return UpdateInfo(NETWORK, "连不上 GitHub：%s" % (reason,), current=current)
    except ValueError as exc:
        # urlopen 遇到非法 URL、或者我们自己的体积检查抛的
        return UpdateInfo(NETWORK, "请求出错：%s" % (exc,), current=current)
    except Exception as exc:
        # socket.timeout 在 Python 3.10+ 是 TimeoutError，走不到 URLError 分支
        return UpdateInfo(NETWORK, "请求出错：%r" % (exc,), current=current)

    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        return UpdateInfo(BAD_JSON, "返回的内容不是 JSON：%s" % (exc,), current=current)

    return parse_release(payload, current)


def check_async(callback, current=None, url=None):
    """在后台线程里查一次，查完在后台线程回调 callback(UpdateInfo)。

    callback 会在**后台线程**里被调用，调用方自己负责转回 Tk 主线程
    （往里塞 queue，让 after() 的循环去取）。这里不替调用方做，是因为
    本模块不想依赖 tkinter——测试和命令行都能直接用。

    返回那个线程对象，调用方可以 join 或者不管。
    """

    def run():
        try:
            result = fetch(current=current, url=url)
        except Exception as exc:  # fetch 内部已兜底，这里是最后一道
            result = UpdateInfo(NETWORK, "检查更新时出错：%r" % (exc,), current=current)
        try:
            callback(result)
        except Exception:
            # 回调里出错不该影响线程退出，更不能把异常甩到 threading 的钩子上
            pass

    t = threading.Thread(target=run, name="BinFloaterUpdateCheck")
    # 守护线程：用户关程序时不用等这次网络请求超时
    t.daemon = True
    t.start()
    return t
