"""两个应用和打包脚本共用的身份配置；不依赖 Qt 或应用初始化。"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys


_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
)
_SEMVER_RE = re.compile(
    rf"""
    (?P<major>0|[1-9]\d*)
    \.
    (?P<minor>0|[1-9]\d*)
    \.
    (?P<patch>0|[1-9]\d*)
    (?:-{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*)?
    (?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?
    """,
    re.VERBOSE,
)
_PREFIX_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_COMMIT_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.[0-9a-f]{8}")


def normalize_version_prefix(value: str) -> str:
    """bump 只接收主、次版本；第三段由实际构建提交生成。"""
    value = str(value or "").strip().removeprefix("v")
    if not _PREFIX_RE.fullmatch(value):
        raise ValueError("version must use MAJOR.MINOR, for example 1.2; the commit hash is automatic")
    if any(int(part) > 65535 for part in value.split(".")):
        raise ValueError("Windows version components must not exceed 65535")
    return value


def version_prefix(value: str) -> str:
    """兼容迁移旧三段配置，但只保留用户维护的前两段。"""
    value = str(value).strip().removeprefix("v")
    if not _PREFIX_RE.fullmatch(value):
        normalize_version(value)
        value = ".".join(value.split(".")[:2])
    return normalize_version_prefix(value)


def git_version(prefix: str, repo_root: Path) -> str:
    """固定使用 HEAD 的前 8 位，避免本机 core.abbrev 与 CI 不同。"""
    prefix = normalize_version_prefix(prefix)
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD^{commit}"],
            text=True, encoding="utf-8", stderr=subprocess.PIPE, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Cannot resolve version from Git HEAD in {repo_root}: {exc}") from exc
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError(f"Invalid Git commit: {commit}")
    return f"{prefix}.{commit[:8]}"


def normalize_version(value: str) -> str:
    """读取完整 hash 版本；旧 SemVer 配置仍可迁移和加载。"""

    normalized = str(value or "").strip()
    if normalized[:1].lower() == "v":
        normalized = normalized[1:]
    if _COMMIT_VERSION_RE.fullmatch(normalized) is None and _SEMVER_RE.fullmatch(normalized) is None:
        raise ValueError(
            "version must use MAJOR.MINOR.HASH, for example 1.2.a1b2c3d4"
        )
    return normalized


def normalize_build_number(value: str | int) -> str:
    """Return an Apple-compatible, positive integer bundle build number."""

    normalized = str(value).strip()
    if re.fullmatch(r"[1-9]\d*", normalized) is None:
        raise ValueError("build number must be a positive integer")
    return normalized


@dataclass(frozen=True)
class AppIdentity:
    app_id: str
    product_name: str
    subtitle: str
    version: str
    build_number: str
    title_template: str

    @property
    def app_name(self) -> str:
        return " - ".join(part for part in (self.product_name, self.subtitle) if part)

    @property
    def bundle_version(self) -> str:
        # Apple/Windows 的数值字段不能包含十六进制字母；完整版本用于应用显示。
        if _COMMIT_VERSION_RE.fullmatch(self.version):
            return f"{version_prefix(self.version)}.0"
        return self.version.split("-", 1)[0].split("+", 1)[0]

    def about_info(self, info: dict) -> dict:
        # 旧 about.cfg 的字面量名称/版本不能覆盖统一身份配置。
        return {**info, "app_name": self.app_name, "version": self.version}

    def window_title(self, info: dict | None = None) -> str:
        return self.title_template.format(
            app_name=self.app_name,
            product_name=self.product_name,
            subtitle=self.subtitle,
            version=self.version,
            author=str((info or {}).get("作者", "")).strip(),
        ).strip(" -")


def metadata_path() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return root / "app_metadata.json"


def load_app_identity(app_id: str, path: Path | None = None) -> AppIdentity:
    """读取源码/包内同一份 JSON；配置错误须给出路径，不能悄悄使用旧版本。"""
    source = Path(path) if path is not None else metadata_path()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        app = raw["apps"][app_id]

        def field(data: dict, key: str, *, allow_empty: bool = False) -> str:
            value = data[key]
            if not isinstance(value, str) or (not value.strip() and not allow_empty):
                raise ValueError(f"{key} must be a string")
            if any(ord(char) < 32 for char in value):
                raise ValueError(f"{key} contains control characters")
            return value.strip()

        version = field(raw, "version")
        if _PREFIX_RE.fullmatch(version):
            if getattr(sys, "frozen", False):
                raise ValueError("Packaged metadata must contain the complete commit version")
            version = git_version(version, source.parent)
        identity = AppIdentity(
            app_id=app_id,
            product_name=field(app, "product_name"),
            subtitle=field(app, "subtitle", allow_empty=True),
            version=normalize_version(version),
            build_number=normalize_build_number(field(raw, "build_number")),
            title_template=field(app, "window_title") if "window_title" in app else field(raw, "window_title"),
        )
        identity.window_title()  # 在启动和打包前检查标题占位符。
        return identity
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f"Invalid app metadata {source}: {exc}") from exc
