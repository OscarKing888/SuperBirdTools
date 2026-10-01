from __future__ import annotations

import json
import http.client
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import certifi

from .common import BLOCK_SIZE, Cancelled, UpdateError, hash_file
from .manifest import MAX_MANIFEST_BYTES, checked_path, load, validate


class RangeUnsupported(UpdateError):
    pass


def _url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.scheme not in {"https", "http"}:
        raise UpdateError("下载地址必须使用 HTTPS")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise UpdateError("仅本地测试服务可使用 HTTP")
    return url


def _open(url: str, headers=None):
    response = urlopen(Request(_url(url), headers={"User-Agent": "SuperBirdTools-Updater/1",
                                                 "Accept-Encoding": "identity", **(headers or {})}), timeout=20,
                       context=ssl.create_default_context(cafile=certifi.where()))
    try:
        _url(response.url)
        if response.headers.get("Content-Encoding", "identity") not in {"", "identity"}:
            raise UpdateError("下载服务返回了不兼容的传输编码")
        return response
    except Exception:
        response.close()
        raise


def get_json(url: str, limit: int = MAX_MANIFEST_BYTES):
    for attempt in range(3):
        try:
            with _open(url, {"Accept": "application/vnd.github+json"}) as response:
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise UpdateError("远程清单或 API 响应过大")
            return json.loads(data.decode("utf-8"))
        except HTTPError as exc:
            if exc.code < 500:
                raise
            if attempt == 2:
                raise UpdateError(f"更新服务器错误: HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            if attempt == 2:
                raise UpdateError(f"更新网络请求失败: {exc}") from exc
        except (ValueError, UnicodeError) as exc:
            raise UpdateError("服务器未返回有效的 JSON") from exc
        time.sleep(0.2 * (attempt + 1))


class LocalSource:
    def __init__(self, directory: Path, target: str, arch: str):
        self.directory = directory.resolve()
        self.target, self.arch = target, arch
        self.notes = "本地更新目录"

    def latest(self) -> dict:
        return load(self.directory / f"update-{self.target}-{self.arch}.json")

    def chunks(self, name: str, offset: int, length: int, asset: dict, cancel, cache: Path,
               allow_full: bool = False):
        path = checked_path(self.directory, name)
        if path.is_symlink() or path.stat().st_size != asset["size"]:
            raise UpdateError(f"本地更新附件无效: {name}")
        with path.open("rb") as stream:
            stream.seek(offset)
            while length:
                if cancel.is_set():
                    raise Cancelled("更新已取消")
                block = stream.read(min(BLOCK_SIZE, length))
                if not block:
                    raise UpdateError(f"更新附件不完整: {name}")
                length -= len(block)
                yield block


class HttpSource:
    """HTTP 传输实现；地址由发布源解析，清单不能指定任意下载 URL。"""

    def __init__(self, manifest_url: str, asset_urls: dict[str, str]):
        self.manifest_url = _url(manifest_url)
        self.asset_urls = {name: _url(url) for name, url in asset_urls.items()}
        self.notes = ""

    def latest(self) -> dict:
        data = validate(get_json(self.manifest_url))
        if not set(data["assets"]).issubset(self.asset_urls):
            raise UpdateError("Release 更新附件尚未上传完整")
        return data

    def _full_asset(self, name: str, asset: dict, cache: Path, cancel) -> Path:
        path = cache / f"archive-{asset['sha256']}"
        if path.is_file() and not path.is_symlink() and hash_file(path, cancel)["sha256"] == asset["sha256"]:
            return path
        part = path.with_suffix(".part")
        try:
            size = 0
            with _open(self.asset_urls[name]) as response, part.open("wb") as dest:
                if response.status != 200:
                    raise UpdateError("整包下载响应无效")
                while block := response.read1(BLOCK_SIZE):
                    if cancel.is_set():
                        raise Cancelled("更新已取消")
                    size += len(block)
                    if size > asset["size"]:
                        raise UpdateError("附件超过清单声明大小")
                    dest.write(block)
            hashes = hash_file(part, cancel)
            if hashes["size"] != asset["size"] or hashes["sha256"] != asset["sha256"]:
                raise UpdateError(f"整包校验失败: {name}")
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
        return path

    def chunks(self, name: str, offset: int, length: int, asset: dict, cancel, cache: Path,
               allow_full: bool = False):
        if length == 0:
            return
        response = _open(self.asset_urls[name], {"Range": f"bytes={offset}-{offset + length - 1}"})
        with response:
            if response.status == 200:
                response.close()
                if not allow_full:
                    raise RangeUnsupported("下载服务不支持范围下载，需要下载完整分卷。是否继续？")
                path = self._full_asset(name, asset, cache, cancel)
                with path.open("rb") as stream:
                    stream.seek(offset)
                    remaining = length
                    while remaining:
                        if cancel.is_set():
                            raise Cancelled("更新已取消")
                        block = stream.read(min(BLOCK_SIZE, remaining))
                        if not block:
                            raise UpdateError("整包内文件范围不完整")
                        remaining -= len(block)
                        yield block
                return
            expected = f"bytes {offset}-{offset + length - 1}/{asset['size']}"
            if response.status != 206 or response.headers.get("Content-Range") != expected:
                raise UpdateError("范围下载响应位置或附件大小不匹配")
            if response.headers.get("Content-Length") not in {None, str(length)}:
                raise UpdateError("范围下载响应长度不匹配")
            remaining = length
            while remaining:
                if cancel.is_set():
                    raise Cancelled("更新已取消")
                block = response.read1(min(BLOCK_SIZE, remaining))
                if not block:
                    raise UpdateError("范围下载中断")
                remaining -= len(block)
                yield block
            if response.read(1):
                raise UpdateError("范围下载包含额外数据")


class GitHubSource(HttpSource):
    def __init__(self, repository: str, target: str, arch: str, channel: str = "stable"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise UpdateError("GitHub 仓库格式应为 owner/repository")
        if channel not in {"stable", "prerelease"}:
            raise UpdateError("发布通道应为 stable 或 prerelease")
        self.repository, self.target, self.arch, self.channel = repository, target, arch, channel
        self.notes = ""
        self.asset_urls = {}

    def latest(self) -> dict | None:
        base = f"https://api.github.com/repos/{self.repository}"
        try:
            if self.channel == "stable":
                release = get_json(base + "/releases/latest")
            else:
                releases = get_json(base + "/releases?per_page=100")
                eligible = [r for r in releases if not r.get("draft")]
                if not eligible:
                    return None
                release = max(eligible, key=lambda r: r.get("published_at") or "")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise UpdateError(f"GitHub 检查失败: HTTP {exc.code}") from exc
        if release.get("draft") or (self.channel == "stable" and release.get("prerelease")):
            return None
        self.notes = str(release.get("body") or "")[:20000]
        assets = []
        for page in range(1, 12):
            batch = get_json(base + f"/releases/{int(release['id'])}/assets?per_page=100&page={page}")
            assets.extend(batch)
            if len(batch) < 100:
                break
        self.asset_urls = {a["name"]: _url(a["browser_download_url"]) for a in assets if a.get("state") == "uploaded"}
        manifest_name = f"update-{self.target}-{self.arch}.json"
        if manifest_name not in self.asset_urls:
            raise UpdateError("最新 Release 尚未提供当前平台的更新清单")
        self.manifest_url = self.asset_urls[manifest_name]
        return super().latest()


def source_from_config(config: dict, target: str, arch: str):
    if config.get("source", "github") == "github":
        return GitHubSource(config.get("repository", "OscarKing888/SuperBirdTools"), target, arch,
                            config.get("channel", "stable"))
    if config["source"] == "local":
        return LocalSource(Path(config["directory"]), target, arch)
    raise UpdateError(f"未实现的更新源: {config['source']}")
