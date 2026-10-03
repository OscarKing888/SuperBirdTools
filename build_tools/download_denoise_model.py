"""预下载固定版本 NAFNet 权重，校验完成后才替换正式文件。"""
from __future__ import annotations

import argparse
import http.client
import os
from pathlib import Path
import ssl
import sys
import tempfile
import time
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from image_denoise.models import (
    MODEL_NAME, MODEL_SIZE, MODEL_URL, DenoiseModelError,
    development_model_path, verify_model,
)


def _open_model(url: str, offset: int, end: int):
    import certifi
    request = Request(url, headers={
        "User-Agent": "SuperBirdTools-model-downloader/1", "Range": f"bytes={offset}-{end}",
    })
    response = urlopen(request, timeout=60, context=ssl.create_default_context(cafile=certifi.where()))
    expected_range = f"bytes {offset}-{end}/{MODEL_SIZE}"
    if response.status == 206 and response.headers.get("Content-Range") == expected_range:
        return response
    # 少数镜像不支持 Range；只有从头读取的 200 响应可以安全接受。
    if offset == 0 and response.status == 200:
        return response
    response.close()
    raise DenoiseModelError(f"NAFNet 下载服务器未返回所需分段：{expected_range}")


def download_model(target: Path, *, force: bool = False, opener=None) -> Path:
    """失败保留旧文件；临时文件与目标同盘，以 os.replace 原子发布。"""
    target = Path(target).resolve()
    if target.exists() and not force:
        try:
            verify_model(target)
        except DenoiseModelError:
            print(f"[NAFNet] 现有模型校验失败，将重新下载：{target}", flush=True)
        else:
            print(f"[NAFNet] 模型已通过校验：{target}", flush=True)
            return target
    target.parent.mkdir(parents=True, exist_ok=True)
    open_remote = opener or _open_model
    last_error = None
    for attempt in range(1, 4):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=f".{MODEL_NAME}.", suffix=".part", dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                print(f"[NAFNet] 下载模型，第 {attempt}/3 次：{target}", flush=True)
                written = 0
                next_report = 64 * 1024**2
                stalled = 0
                # 分段请求规避网关对长响应的截断；每次从实际收到的偏移继续。
                while written < MODEL_SIZE:
                    before = written
                    end = min(written + 8 * 1024**2, MODEL_SIZE) - 1
                    try:
                        with open_remote(MODEL_URL, written, end) as response:
                            while True:
                                try:
                                    chunk = response.read(1024 * 1024)
                                except http.client.IncompleteRead as exc:
                                    chunk = exc.partial
                                if not chunk:
                                    break
                                written += len(chunk)
                                if written > MODEL_SIZE:
                                    raise DenoiseModelError("NAFNet 下载内容超过预期大小")
                                output.write(chunk)
                                if written >= next_report:
                                    print(f"[NAFNet] {written // 1024**2} / {MODEL_SIZE // 1024**2} MiB", flush=True)
                                    next_report += 64 * 1024**2
                    except (OSError, http.client.HTTPException) as exc:
                        last_error = exc
                    if written == before:
                        stalled += 1
                        if stalled >= 3:
                            raise DenoiseModelError(f"NAFNet 下载中断在 {written} 字节：{last_error or '响应为空'}")
                        time.sleep(stalled)
                    else:
                        stalled = 0
                output.flush()
                os.fsync(output.fileno())
            if written != MODEL_SIZE:
                raise DenoiseModelError(f"NAFNet 下载未完成：收到 {written} 字节，预期 {MODEL_SIZE} 字节")
            verify_model(temporary)
            os.replace(temporary, target)
            print(f"[NAFNet] 下载并校验完成：{target}", flush=True)
            return target
        except (OSError, http.client.HTTPException, DenoiseModelError) as exc:
            last_error = exc
            print(f"[NAFNet] 本次下载失败：{exc}", file=sys.stderr, flush=True)
            if attempt < 3:
                time.sleep(attempt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise DenoiseModelError(f"NAFNet 模型下载失败，已有模型未被修改：{last_error}") from last_error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="预下载并校验 NAFNet SIDD width64 模型")
    parser.add_argument("--dest", type=Path, default=development_model_path(REPO_ROOT))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="仅离线校验已有模型")
    parser.add_argument("--dry-run", action="store_true", help="只显示下载计划，不写文件或联网")
    args = parser.parse_args(argv)
    if args.dry_run:
        print(f"[NAFNet] {MODEL_URL}\n[NAFNet] -> {args.dest}")
        return 0
    try:
        if args.check_only:
            verify_model(args.dest)
        else:
            download_model(args.dest, force=args.force)
    except (OSError, DenoiseModelError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
