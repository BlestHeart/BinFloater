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

import hashlib
import json
import os
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

# 下载安装包时的状态码（和检查更新分开，两边语义不一样）
DL_OK = "dl_ok"
DL_NETWORK = "dl_network"
DL_HTTP = "dl_http"
DL_TOO_BIG = "dl_too_big"
DL_BAD_HASH = "dl_bad_hash"
DL_DISK = "dl_disk"
DL_CANCELLED = "dl_cancelled"

# 只认这两种资产名。旧版 Release 只有 zip，没有 Setup.exe，也要能处理。
ASSET_SETUP = "setup"
ASSET_ZIP = "zip"

# 安装包下载上限。现在是 10MB 上下，给 200MB 够宽松，但能挡住「服务器
# 返回一个无限流」把磁盘写满。
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class Asset(object):
    """Release 里的一个附件。只需要下载安装包，所以字段挑着拿。"""

    __slots__ = ("name", "size", "url", "digest")

    def __init__(self, name, size=0, url="", digest=""):
        self.name = name
        self.size = int(size or 0)
        self.url = url
        self.digest = digest or ""

    @property
    def is_setup(self):
        """是不是安装包。大小写不敏感，兼容早期命名。"""
        low = self.name.lower()
        return low.endswith(".exe") and "setup" in low

    @property
    def is_zip(self):
        return self.name.lower().endswith(".zip")

    @property
    def sha256(self):
        """从 digest 里把十六进制抠出来。GitHub 给的是 "sha256:abc..."。"""
        d = (self.digest or "").strip()
        if d.lower().startswith("sha256:"):
            return d[7:].strip().lower()
        return d.lower()

    def __repr__(self):
        return "<Asset %r %d>" % (self.name, self.size)


class DownloadResult(object):
    """下载结果。path 只在成功时有值。"""

    __slots__ = ("status", "path", "message", "bytes")

    def __init__(self, status, path="", message="", wrote=0):
        self.status = status
        self.path = path
        self.message = message
        self.bytes = wrote

    @property
    def ok(self):
        return self.status == DL_OK

    def __repr__(self):
        return "<DownloadResult %s %r>" % (self.status, self.path)


class UpdateInfo(object):
    """检查结果。字段全给了默认值，调用方不用挨个判 None。"""

    __slots__ = ("status", "latest", "current", "name", "body",
                 "html_url", "published_at", "message", "assets")

    def __init__(self, status, message="", latest="", current=None,
                 name="", body="", html_url="", published_at="", assets=None):
        self.status = status
        self.message = message
        self.latest = latest
        self.current = current or version.VERSION
        self.name = name
        self.body = body
        self.html_url = html_url or RELEASES_PAGE
        self.published_at = published_at
        self.assets = list(assets or [])

    def asset(self, kind):
        """按类型挑附件。挑不到返回 None。"""
        for a in self.assets:
            if kind == ASSET_SETUP and a.is_setup:
                return a
            if kind == ASSET_ZIP and a.is_zip:
                return a
        return None

    @property
    def installer(self):
        """能直接双击跑的安装包。没有就返回 None（旧版 Release 只有 zip）。"""
        return self.asset(ASSET_SETUP)

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


def parse_assets(payload):
    """把 Release JSON 里的 assets 数组转成 Asset 列表。"""
    raw = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out.append(Asset(
            name,
            item.get("size") or 0,
            str(item.get("browser_download_url") or ""),
            str(item.get("digest") or ""),
        ))
    return out


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
        assets=parse_assets(payload),
    )


def download_asset(asset, folder, progress=None, should_stop=None):
    """把附件下载到 folder，返回 DownloadResult。

    四个必须做的事：
      1. 先写 .part，校验通过才 os.replace 改成真名。半途断网不会留下一个
         看着像安装包、双击却报「文件损坏」的碎文件。
      2. 边下边算 sha256，下完和 Release 里声明的 digest 对。GitHub 从
         2024 年起给每个 asset 返回 digest 字段，白送的完整性校验。
      3. 声明了大小的就卡住上限，下超了立刻停（防重定向到一个无限流）。
      4. progress(已写, 总数) 在后台线程里被调，调用方自己往队列里塞。

    should_stop 是个无参可调用对象，返回 True 就中断（用户点了取消）。
    """
    if asset is None:
        return DownloadResult(DL_HTTP, message="没有可下载的附件")
    if not asset.url:
        return DownloadResult(DL_HTTP, message="附件没有下载地址")

    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as exc:
        return DownloadResult(DL_DISK, message="建不了下载目录：%s" % (exc,))

    # 文件名只取最后一段，防着服务端给个带路径的名字往外写
    safe = os.path.basename(asset.name) or "BinFloater-Setup.exe"
    final = os.path.join(folder, safe)
    part = final + ".part"

    req = urllib.request.Request(asset.url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/octet-stream")

    hasher = hashlib.sha256()
    written = 0
    total = asset.size
    try:
        resp = urllib.request.urlopen(req, timeout=READ_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        return DownloadResult(DL_HTTP, message="下载地址返回 HTTP %d" % (
            int(getattr(exc, "code", 0) or 0),))
    except Exception as exc:
        return DownloadResult(DL_NETWORK, message="连不上下载地址：%r" % (exc,))

    try:
        try:
            # Content-Length 可能没有（分块传输），有就用它
            clen = resp.headers.get("Content-Length") if resp.headers else None
            if clen:
                try:
                    total = max(int(clen), 0)
                except (TypeError, ValueError):
                    pass
            if total > MAX_DOWNLOAD_BYTES:
                return DownloadResult(
                    DL_TOO_BIG, message="安装包太大（%d MB），不下了" % (
                        total // 1048576,))

            fh = open(part, "wb")
            try:
                while True:
                    if should_stop is not None and should_stop():
                        raise _Cancelled()
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise _TooBig()
                    hasher.update(chunk)
                    fh.write(chunk)
                    if progress is not None:
                        try:
                            progress(written, total)
                        except Exception:
                            # 进度回调出错不能把下载搞死
                            pass
            finally:
                fh.close()
        finally:
            try:
                resp.close()
            except Exception:
                pass
    except _Cancelled:
        _quiet_remove(part)
        return DownloadResult(DL_CANCELLED, message="已取消")
    except _TooBig:
        _quiet_remove(part)
        return DownloadResult(DL_TOO_BIG, message="下载超过大小上限，已中断")
    except Exception as exc:
        _quiet_remove(part)
        return DownloadResult(DL_NETWORK, message="下载中断：%r" % (exc,))

    # 大小对得上吗（服务端明确给了总长才查）
    if total > 0 and written != total:
        _quiet_remove(part)
        return DownloadResult(
            DL_NETWORK, message="下载不完整（%d / %d 字节）" % (written, total))

    want = asset.sha256
    if want:
        got = hasher.hexdigest()
        if got != want:
            _quiet_remove(part)
            return DownloadResult(
                DL_BAD_HASH,
                message="校验和不匹配（下下来的是 %s…，应该是 %s…）" % (
                    got[:12], want[:12]))

    try:
        os.replace(part, final)
    except Exception as exc:
        _quiet_remove(part)
        return DownloadResult(DL_DISK, message="存盘失败：%s" % (exc,))

    return DownloadResult(DL_OK, path=final, wrote=written)


class _Cancelled(Exception):
    """用户点了取消。用异常是为了能从深处跳出来。"""


class _TooBig(Exception):
    """下超了上限。"""


def _quiet_remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def download_async(asset, folder, callback, progress=None, should_stop=None):
    """后台线程下载，完事回调 callback(DownloadResult)。

    progress 也是后台线程里被调，调用方自己转主线程。
    """

    def run():
        try:
            result = download_asset(asset, folder, progress=progress,
                                    should_stop=should_stop)
        except Exception as exc:
            result = DownloadResult(DL_NETWORK, message="下载出错：%r" % (exc,))
        try:
            callback(result)
        except Exception:
            pass

    t = threading.Thread(target=run, name="BinFloaterDownload")
    t.daemon = True
    t.start()
    return t


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
        # 备用查询只在「真的去问 api.github.com」时启用。传了 url 说明是
        # 测试用的本地假接口，不能顺手打到真网络上去。
        alt = fetch_by_redirect(current, timeout) if url is None else None
        if alt is not None and alt.status in (OK, UP_TO_DATE):
            return alt
        if code == 403:
            # 403 基本只有一种：匿名限流 60 次/小时（UA 我们带了）。
            # 这个额度按出口 IP 算，共用 NAT / 代理时很容易被别人用光，
            # 所以上面先退回网页跳转那条路试一次，别让用户白等一个错误提示。
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不让 urllib 自动跟随跳转，好让我们读到 Location 头。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_by_redirect(current=None, timeout=None, page=None):
    """不碰 API，直接看 releases/latest 跳到哪个 tag。

    为什么要这条备用路：api.github.com 的匿名额度是**按出口 IP** 算的，
    每小时只有 60 次。很多人共用同一个出口（公司 NAT、运营商 CGNAT、
    机场代理），别人用完了你这里就是 403，明明仓库是公开的却报「访问太频繁」。
    而 github.com 的网页跳转不占这个额度，HEAD 一次就能拿到版本号。

    代价是拿不到附件清单和更新说明，所以只能告诉用户「有新版本 vX」，
    点「去下载」时开浏览器。够用：这条路只在 API 被限流时才走。
    """
    current = current or version.VERSION
    if timeout is None:
        timeout = READ_TIMEOUT_S

    page = page or RELEASES_PAGE
    req = urllib.request.Request(page, method="HEAD")
    req.add_header("User-Agent", USER_AGENT)
    opener = urllib.request.build_opener(_NoRedirect)

    try:
        opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code in (301, 302, 303, 307, 308):
            loc = ""
            try:
                loc = exc.headers.get("Location") or ""
            except Exception:
                loc = ""
            tag = loc.rstrip("/").rsplit("/", 1)[-1] if loc else ""
            if tag and tag != "latest":
                if version.is_newer(tag, current):
                    return UpdateInfo(
                        OK,
                        "发现新版本 %s（GitHub 接口被限流，走的备用查询）" % tag,
                        latest=tag,
                        current=current,
                        name=tag,
                        html_url=loc,
                    )
                return UpdateInfo(
                    UP_TO_DATE,
                    "已经是最新版本（GitHub 接口被限流，走的备用查询）",
                    latest=tag,
                    current=current,
                    html_url=loc,
                )
            return UpdateInfo(HTTP_ERROR, "跳转地址里没有版本号", current=current)
        if code == 404:
            return UpdateInfo(NOT_FOUND, "仓库还没公开，读不到发布信息", current=current)
        return UpdateInfo(HTTP_ERROR, "服务器返回 HTTP %d" % code, current=current)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return UpdateInfo(NETWORK, "连不上 GitHub：%s" % (reason,), current=current)
    except Exception as exc:
        return UpdateInfo(NETWORK, "请求出错：%r" % (exc,), current=current)

    # 没跳转说明 /releases/latest 直接就是终态，理论上不会走到这里
    return UpdateInfo(HTTP_ERROR, "没有拿到跳转地址", current=current)


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
