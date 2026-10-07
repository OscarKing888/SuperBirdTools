# -*- coding: utf-8 -*-
"""本机 SuperPicky BirdID 客户端及 XMP 写入；GUI/CLI 共用，不依赖 Qt。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import http.client
import json
import math
import os
from pathlib import Path
import socket
import threading
from urllib.parse import urlsplit

from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.bird_pinyin import PINYIN_FIELD, PINYIN_SOURCE_FIELD
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS

DEFAULT_URL = "http://127.0.0.1:5156"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class BirdIDError(RuntimeError):
    pass


class BirdIDCancelled(BirdIDError):
    pass


@dataclass(frozen=True)
class BirdIDOptions:
    url: str = DEFAULT_URL
    threshold: float = 50.0
    skip_existing: bool = False

    def validate(self):
        address = urlsplit(self.url)
        if (address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost", "::1"}
                or address.username or address.password or address.query or address.fragment
                or address.path not in {"", "/"}):
            raise ValueError("识鸟服务必须是本机 HTTP 地址，例如 http://127.0.0.1:5156")
        if address.port is not None and not 1 <= address.port <= 65535:
            raise ValueError("服务端口必须为 1–65535")
        if not math.isfinite(self.threshold) or not 0 <= self.threshold <= 100:
            raise ValueError("确认阈值必须为 0–100%")


class BirdIDClient:
    """只调用健康检查和识别接口；取消关闭本客户端的连接，不停止外部服务。"""

    def __init__(self, options: BirdIDOptions):
        options.validate()
        self.options = options
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._connection = None
        self._socket = None

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            connection = self._connection
            sock = self._socket or (connection.sock if connection is not None else None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise BirdIDCancelled("识鸟已停止")

    def _request(self, endpoint: str, payload=None):
        self.check_cancelled()
        address = urlsplit(self.options.url)
        connection = http.client.HTTPConnection(address.hostname, address.port or 80, timeout=3)
        with self._lock:
            self._connection = connection
        try:
            connection.connect()
            with self._lock:
                self._socket = connection.sock
            self.check_cancelled()
            connection.sock.settimeout(120 if payload is not None else 3)
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            connection.request("GET" if body is None else "POST", endpoint, body,
                               {"Content-Type": "application/json; charset=utf-8"})
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            self.check_cancelled()
            if len(raw) > MAX_RESPONSE_BYTES:
                raise BirdIDError("SuperPicky 返回内容过大")
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise BirdIDError("SuperPicky 未返回有效 JSON") from exc
            if not isinstance(data, dict):
                raise BirdIDError("SuperPicky 返回格式错误")
            if response.status != 200:
                raise BirdIDError(f"SuperPicky HTTP {response.status}：{data.get('error', response.reason)}")
            return data
        except (OSError, http.client.HTTPException) as exc:
            self.check_cancelled()
            raise BirdIDError(f"无法访问 SuperPicky 识鸟服务 {self.options.url}：{exc}。请启动 SuperPicky 的 BirdID 服务。") from exc
        finally:
            connection.close()
            with self._lock:
                if self._connection is connection:
                    self._connection = None
                    self._socket = None

    def health(self):
        data = self._request("/health")
        if data.get("status") != "ok" or data.get("service") != "SuperPicky BirdID API":
            raise BirdIDError("该地址不是 SuperPicky BirdID 服务")
        return data

    def recognize(self, path: str):
        return self._request("/recognize", {
            "image_path": os.path.abspath(path), "top_k": 3, "use_yolo": True, "use_gps": True,
        })


@dataclass
class BirdIDResult:
    source: str
    status: str
    message: str = ""
    response: dict = field(default_factory=dict)
    updates: dict = field(default_factory=dict)
    saved_fingerprint: tuple | None = None


def collect_paths(inputs, *, recursive=False, cancelled=lambda: False):
    """后台扫描；同名 RAW/JPEG 共用 XMP，每个侧车只识别一次（优先 RAW）。"""
    from app_common.image_formats import RAW_IMAGE_EXTENSIONS

    paths = []
    for raw in inputs:
        if cancelled():
            break
        path = Path(raw).absolute()
        if not path.is_dir():
            paths.append(path)
            continue
        for root, dirs, names in os.walk(path):
            if cancelled():
                break
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not Path(root, d).is_symlink()) if recursive else []
            paths.extend(Path(root, n) for n in sorted(names) if Path(n).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS)
    grouped = {}
    for path in paths:
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        key = os.path.normcase(str(PhotoMetaDataXMP().sidecar_path_for(str(path))))
        old = grouped.get(key)
        if old is None or (old.suffix.lower() not in RAW_IMAGE_EXTENSIONS and path.suffix.lower() in RAW_IMAGE_EXTENSIONS):
            grouped[key] = path
    return [str(p) for p in grouped.values()]


def parse_response(response: dict):
    if not isinstance(response, dict) or response.get("success") is not True:
        raise BirdIDError(str(response.get("error", "识鸟失败")) if isinstance(response, dict) else "识鸟响应格式错误")
    candidates = response.get("results")
    if not isinstance(candidates, list) or not candidates:
        raise BirdIDError("SuperPicky 没有返回候选鸟种")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise BirdIDError("识鸟候选格式错误")
        for key in ("cn_name", "en_name", "scientific_name", "description", "pinyin_name"):
            if not isinstance(candidate.get(key, ""), str):
                raise BirdIDError(f"识鸟候选字段格式错误：{key}")
        if not (candidate.get("cn_name", "").strip() or candidate.get("en_name", "").strip()):
            raise BirdIDError("候选鸟名为空")
        try:
            confidence = float(candidate["confidence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BirdIDError("识鸟置信度格式错误") from exc
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            raise BirdIDError("识鸟置信度超出 0–100%")
    return max(candidates, key=lambda c: float(c["confidence"]))


def _fingerprint(path):
    try:
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


def identify_file(source: str, client: BirdIDClient) -> BirdIDResult:
    """慢请求期间不持写锁；提交前拒绝被移动、替换或编辑过的文件。"""
    source = os.path.normpath(os.path.abspath(source))
    store = PhotoMetaDataXMP()
    try:
        client.check_cancelled()
        if Path(source).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS or not Path(source).is_file():
            raise BirdIDError("不是存在的受支持照片")
        sidecar = store.sidecar_path_for(source)
        with xmp_sidecar_write_lock(source):
            before = _fingerprint(source), _fingerprint(sidecar)
            # 严格检查已有 XML，不能把损坏文件当作空元数据。
            if store._load_or_create_xmp_tree(sidecar) is None:
                raise BirdIDError("已有 XMP 损坏，识鸟未写入")
            existing = store.read(source)
            if client.options.skip_existing and (existing.get("bird_species_cn") or existing.get("bird_species_en") or existing.get("birdid_response")):
                return BirdIDResult(source, "skipped", "已有鸟种/识别记录")
        response = client.recognize(source)
        best = parse_response(response)
        confidence = float(best["confidence"])
        confirmed = confidence >= client.options.threshold
        values = {"birdid_response": json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":"))}
        if confirmed:
            values.update(bird_species_cn=best.get("cn_name", ""), bird_species_en=best.get("en_name", ""),
                          birdid_confidence=confidence, alt_species_cn="", alt_species_en="", alt_confidence="")
        else:
            values.update(alt_species_cn=best.get("cn_name", ""), alt_species_en=best.get("en_name", ""), alt_confidence=confidence)
        if confirmed and best.get("pinyin_name", "").strip():
            values[PINYIN_FIELD] = best["pinyin_name"].strip()
            values[PINYIN_SOURCE_FIELD] = best.get("cn_name") or best["en_name"]
        fields = {f"XMP-superpicky:{key}": value for key, value in values.items()}
        if confirmed:
            values["title"] = best.get("cn_name") or best["en_name"]
            fields["XMP-dc:Title"] = values["title"]
            fields["XMP-superpicky:title"] = values["title"]
        with xmp_sidecar_write_lock(source):
            client.check_cancelled()
            if before != (_fingerprint(source), _fingerprint(store.sidecar_path_for(source))):
                return BirdIDResult(source, "skipped", "识别期间照片或 XMP 已变化，请重试")
            if not store.write(source, fields):
                raise BirdIDError("XMP 保存失败，原元数据已保留")
            saved_fingerprint = _fingerprint(store.sidecar_path_for(source))
        updates = dict(values)
        updates.update(fields)
        if confirmed:
            updates["Title"] = values["title"]
        name = best.get("cn_name") or best["en_name"]
        return BirdIDResult(source, "success" if confirmed else "candidate", f"{name} {confidence:.1f}%" + ("" if confirmed else "（待确定）"), response, updates, saved_fingerprint)
    except BirdIDCancelled:
        return BirdIDResult(source, "cancelled", "已停止，未写入此照片")
    except Exception as exc:
        return BirdIDResult(source, "failed", f"[BirdID] {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="通过本机 SuperPicky 识鸟并保存到 XMP")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--threshold", type=float, default=50)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args(argv)
    try:
        client = BirdIDClient(BirdIDOptions(args.url, args.threshold, args.skip_existing))
    except ValueError as exc:
        parser.error(str(exc))
    try:
        client.health()
        paths = collect_paths(args.paths, recursive=args.recursive)
        if not paths:
            raise BirdIDError("没有找到受支持的照片")
        failed = False
        for path in paths:
            result = identify_file(path, client)
            print(json.dumps({"source": path, "status": result.status, "message": result.message, "response": result.response}, ensure_ascii=False))
            failed |= result.status == "failed"
        return int(failed)
    except (BirdIDError, KeyboardInterrupt) as exc:
        print(f"[BirdID] {exc}")
        return 1
    finally:
        client.cancel()
        from app_common.exif_io import close_exiftool_process
        close_exiftool_process()


if __name__ == "__main__":
    raise SystemExit(main())
