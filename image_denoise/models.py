"""固定的 NAFNet SIDD 权重清单与离线验证；运行时不下载资源。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

MODEL_NAME = "NAFNet-SIDD-width64.pth"
MODEL_REVISION = "5964ed4955416df99210106b708e4a2df9e9eca0"
MODEL_URL = (
    "https://huggingface.co/spaces/chuxiaojie/NAFNet/resolve/"
    f"{MODEL_REVISION}/{MODEL_NAME}"
)
MODEL_SHA256 = "cd685efaae01f7c4e9951f2deab05780079c8eb1e49ed664b72f6db04dabb445"
MODEL_SIZE = 464154961
MODEL_DIR_ENV = "SUPERBIRD_DENOISE_MODEL_DIR"


class DenoiseModelError(RuntimeError):
    """必需的离线权重缺失或损坏。"""


def development_model_path(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[1]
    return root / "SuperViewer" / "models" / "denoise" / MODEL_NAME


def resolve_model_path() -> Path:
    override = os.environ.get(MODEL_DIR_ENV, "").strip()
    if override:
        path = Path(override).expanduser() / MODEL_NAME
    elif getattr(sys, "frozen", False):
        resource_root = getattr(sys, "_MEIPASS", None)
        if resource_root:
            path = Path(resource_root) / "models" / "denoise" / MODEL_NAME
        else:
            exe_dir = Path(sys.executable).resolve().parent
            root = exe_dir.parent / "Resources" if sys.platform == "darwin" else exe_dir / "_internal"
            path = root / "models" / "denoise" / MODEL_NAME
    else:
        path = development_model_path()
    if not path.is_file():
        raise DenoiseModelError(f"缺少 NAFNet 降噪模型：{path}。请重新安装完整程序，开发环境请运行 init_dev.py。")
    return path


def verify_model(path: Path | str) -> Path:
    path = Path(path)
    try:
        if path.stat().st_size != MODEL_SIZE:
            raise DenoiseModelError(f"NAFNet 模型大小不符或下载未完成：{path}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise DenoiseModelError(f"NAFNet 模型 SHA-256 校验失败：{path}")
    except OSError as exc:
        raise DenoiseModelError(f"无法读取 NAFNet 降噪模型：{path}：{exc}") from exc
    return path
