"""两应用共用的离线 ExifTool 检查；只在临时目录创建样本，不构造主窗口。"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile


def _portable_mac_environment() -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items() if not name.startswith("PERL")}
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return environment


@contextmanager
def _isolated_mac_environment(directory: Path):
    if sys.platform != "darwin":
        yield
        return
    keys = lambda name: name.startswith("PERL") or name in {"PATH", "HOME"}
    previous = {name: value for name, value in os.environ.items() if keys(name)}
    try:
        for name in previous:
            os.environ.pop(name, None)
        os.environ["PATH"] = _portable_mac_environment()["PATH"]
        os.environ["HOME"] = str(directory)
        yield
    finally:
        for name in tuple(os.environ):
            if keys(name):
                os.environ.pop(name, None)
        os.environ.update(previous)


def _check_library_containment(executable: Path) -> None:
    runtime = executable.parent.resolve()
    pending = [runtime / "lib"]
    visited = set()
    while pending:
        candidate = pending.pop()
        actual = candidate.resolve(strict=True)
        if not actual.is_relative_to(runtime):
            raise RuntimeError(f"Bundled ExifTool library escapes its runtime: {candidate} -> {actual}")
        if actual.is_dir() and actual not in visited:
            visited.add(actual)
            pending.extend(actual.iterdir())


def check_exiftool(*, require_bundled: bool | None = None) -> dict:
    """检查真实解析结果；不得用环境覆盖把系统回退伪装成包内工具。"""
    from app_common.exif_io import get_exiftool_executable_path
    from app_common.exif_io import exiftool_path
    from app_common.exif_io.exiftool_runner import run_exiftool_once

    if require_bundled is None:
        require_bundled = sys.platform == "darwin" and bool(getattr(sys, "frozen", False))
    executable = get_exiftool_executable_path()
    if not executable:
        raise RuntimeError("ExifTool unavailable: bundled executable or its libraries are missing")
    selected = Path(executable).resolve()
    report = {"exiftool": str(selected), "require_bundled": require_bundled}
    if require_bundled:
        relative = ("exiftools_win", "exiftool.exe") if sys.platform.startswith("win") else ("exiftools_mac", "exiftool")
        expected = (Path(exiftool_path.__file__).parent.joinpath(*relative)).resolve()
        report["expected_exiftool"] = str(expected)
        if selected != expected:
            raise RuntimeError(f"ExifTool resolved outside bundled module: {selected}; expected {expected}")
        if sys.platform == "darwin" and getattr(sys, "frozen", False):
            bundle = next((path for path in Path(sys.executable).resolve().parents if path.suffix == ".app"), None)
            if bundle is None or not selected.is_relative_to(bundle):
                raise RuntimeError(f"ExifTool is not contained in the running app bundle: {selected}")
            report["bundle"] = str(bundle)
        if not sys.platform.startswith("win"):
            if not (selected.parent / "lib" / "Image" / "ExifTool.pm").is_file():
                raise RuntimeError(f"Bundled ExifTool library missing: {selected.parent / 'lib' / 'Image' / 'ExifTool.pm'}")
            _check_library_containment(selected)
    # 独立调用也不能让 PERL5LIB/PERL5OPT 或已缓存的健康检查掩盖库缺失。
    environment = _portable_mac_environment() if require_bundled and sys.platform == "darwin" else None
    result = run_exiftool_once([str(selected), "-config", "", "-ver"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15, env=environment)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"ExifTool could not run: {result.stderr or result.stdout}")
    report["exiftool_version"] = result.stdout.strip()
    return report


def check_viewer_export(directory: Path, *, export_png: Callable) -> dict:
    """使用共享侧车写入和 Viewer 原尺寸导出核心验证真实中文往返。"""
    from PIL import Image
    from app_common.exif_io import PhotoMetaDataXMP, get_exiftool_executable_path
    from app_common.exif_io.exiftool_runner import run_exiftool

    source = directory / "中文 原图.jpg"
    with Image.new("RGB", (120, 80), "#6395bd") as original:
        original.save(source)
    before = source.read_bytes()
    store = PhotoMetaDataXMP()
    fields = {"EXIF:Model": "中文相机 Ω", "EXIF:ISO": "1600",
              "XMP-dc:Description": "小勺子，原始拍摄信息。", "XMP-dc:Subject": ["飞行", "捕食"],
              "XMP-superpicky:bird_species_cn": "普通翠鸟"}
    if not store.write(str(source), fields):
        raise RuntimeError("Viewer XMP sidecar metadata write failed")
    metadata = store.read(str(source))
    if (metadata.get("XMP-tiff:Model") != fields["EXIF:Model"]
            or metadata.get("Description") != fields["XMP-dc:Description"]
            or metadata.get("bird_species_cn") != "普通翠鸟"
            or store.read_subjects(str(source), strict=True) != fields["XMP-dc:Subject"]):
        raise RuntimeError("Viewer Chinese XMP sidecar read-back mismatch")
    sidecar = store.sidecar_path_for(str(source))
    sidecar_before = sidecar.read_bytes()
    result = run_exiftool(get_exiftool_executable_path(), ["-j", "-s", "-n", str(sidecar)], timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    tags = json.loads(result.stdout)[0]
    if tags.get("Model") != fields["EXIF:Model"] or tags.get("Description") != fields["XMP-dc:Description"]:
        raise RuntimeError("ExifTool Chinese XMP sidecar read-back mismatch")
    exported = export_png(source, directory / "导出")
    if exported.error or exported.cancelled or not exported.output_path:
        raise RuntimeError(f"Viewer PNG export failed: {exported.error}")
    with Image.open(exported.output_path) as image:
        image.load()
        if image.format != "PNG" or image.size != (120, 80):
            raise RuntimeError("Viewer PNG export dimensions or format mismatch")
    if source.read_bytes() != before or sidecar.read_bytes() != sidecar_before:
        raise RuntimeError("Viewer export modified the source photo or sidecar")
    return {"png_export": "ok", "chinese_xmp": "ok", "source_unchanged": True}


def main(app_id: str, argv: list[str] | None = None, *, check_export: Callable[[Path], dict]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="JSON report for windowed executables")
    args = parser.parse_args(argv)
    report = {"app_id": app_id, "status": "error"}
    close = None
    try:
        from app_common.exif_io import close_exiftool_process
        close = close_exiftool_process
        with tempfile.TemporaryDirectory(prefix="superbird-exiftool-check-") as directory:
            with _isolated_mac_environment(Path(directory)):
                # 保留 EXIFTOOL_EXE/PATH，使真实解析器的外部覆盖依然明确失败。
                report.update(check_exiftool(require_bundled=sys.platform == "darwin"))
                report.update(check_export(Path(directory)))
        report["status"] = "ok"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if close is not None:
            close()
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return int(report["status"] != "ok")
