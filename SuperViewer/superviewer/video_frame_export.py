"""Export every decoded video frame to PNG; no Qt dependency."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from app_common.video import _VIDEO_SLOTS, find_ffmpeg
from app_common.superviewer_user_options import valid_video_frame_suffix


@dataclass(frozen=True)
class FrameExportResult:
    source: str
    output_dir: str = ""
    frames: int = 0
    cancelled: bool = False
    error: str = ""


def _new_output_directory(source, parent, suffix):
    """Reserve a fresh directory atomically; never overwrite a previous export."""
    parent = Path(parent).absolute()
    parent.mkdir(parents=True, exist_ok=True)
    name = Path(source).stem + suffix
    index = 1
    while True:
        directory = parent / (name if index == 1 else f"{name} ({index})")
        try:
            directory.mkdir()
            return directory
        except FileExistsError:
            index += 1


def export_video_frames(source, output_parent=None, *, suffix="_Frames", cancelled=lambda: False,
                        on_progress=lambda frames, directory: None):
    """Sequentially decode the first non-cover video track, without FPS conversion.

    Progress and diagnostics spool to temporary files so neither a full pipe nor
    a slow UI can block cancellation. PNGs are atomically renamed by image2;
    cancellation/failure retains completed images and reports their directory.
    """
    source = os.path.abspath(os.fspath(source))
    directory = None
    acquired = False
    stopped = False
    error = ""
    frames = 0
    try:
        if cancelled():
            return FrameExportResult(source, cancelled=True)
        if not valid_video_frame_suffix(suffix):
            raise ValueError("无效的视频帧目录名后缀")
        if not Path(source).is_file():
            raise FileNotFoundError(f"找不到视频文件：{source}")
        executable = find_ffmpeg()
        while not acquired:
            if cancelled():
                return FrameExportResult(source, cancelled=True)
            acquired = _VIDEO_SLOTS.acquire(timeout=.05)
        directory = _new_output_directory(source, output_parent or Path(source).parent, suffix)
        on_progress(0, str(directory))
        # Escape literal percent signs in directories for image2's pattern parser.
        pattern = str(directory).replace("%", "%%") + "/frame_%08d.png"
        args = [executable, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-nostats", "-xerror", "-threads", "2", "-i", source,
                "-map", "0:V:0", "-an", "-sn", "-dn", "-vsync", "0",
                "-c:v", "png", "-threads", "2", "-f", "image2",
                "-start_number", "1", "-atomic_writing", "1",
                "-progress", "pipe:1", pattern]
        with tempfile.TemporaryDirectory(prefix="superviewer-frames-") as scratch:
            progress_path = Path(scratch) / "progress"
            with progress_path.open("wb") as progress, \
                    (Path(scratch) / "error").open("w+b") as errors, \
                    progress_path.open("rb") as reader:
                if cancelled():
                    stopped = True
                else:
                    with subprocess.Popen(
                        args, stdin=subprocess.DEVNULL, stdout=progress, stderr=errors,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    ) as process:
                        pending = b""
                        try:
                            while True:
                                finished = process.poll() is not None
                                pending += reader.read(65536)
                                lines = pending.split(b"\n")
                                pending = lines.pop()
                                for line in lines:
                                    if line.startswith(b"frame="):
                                        value = int(line.split(b"=", 1)[1].strip())
                                        if value != frames:
                                            frames = value
                                            on_progress(frames, str(directory))
                                if finished:
                                    break
                                if cancelled():
                                    stopped = True
                                    break
                                time.sleep(.1)
                        finally:
                            if process.poll() is None:
                                process.kill()
                            process.wait()
                        if process.returncode and not stopped:
                            errors.seek(0, os.SEEK_END)
                            errors.seek(max(0, errors.tell() - 4000))
                            error = errors.read().decode("utf-8", errors="replace").strip()
                            error = error or f"FFmpeg 提取失败（{process.returncode}）"
    except Exception as exc:
        error = f"视频帧提取失败：{exc}"
    finally:
        if acquired:
            _VIDEO_SLOTS.release()
    if directory is not None:
        # Count committed PNGs, not FFmpeg's possibly buffered progress count.
        frames = 0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if re.fullmatch(r"frame_\d+\.png", entry.name) and entry.is_file():
                        frames += 1
                    elif re.fullmatch(r"frame_\d+\.png\.tmp", entry.name):
                        os.unlink(entry.path)
        except OSError as exc:
            error = f"{error}\n无法检查输出目录：{exc}".strip()
    if not stopped and not error and not frames:
        error = "视频中没有可提取的帧。"
    return FrameExportResult(source, str(directory or ""), frames, stopped, error)


def main(argv=None):
    import argparse
    import json
    import signal
    import threading
    from dataclasses import asdict

    parser = argparse.ArgumentParser(description="逐帧导出完整视频为原分辨率 PNG")
    parser.add_argument("videos", nargs="+")
    parser.add_argument("--output", help="输出父目录；默认每个视频所在目录")
    parser.add_argument("--suffix", default="_Frames", help="帧目录名后缀，默认 _Frames")
    args = parser.parse_args(argv)
    stop = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda *_: stop.set())
    failed = False
    try:
        for source in args.videos:
            result = export_video_frames(source, args.output, suffix=args.suffix, cancelled=stop.is_set)
            print(json.dumps(asdict(result), ensure_ascii=False))
            failed |= bool(result.error)
            if stop.is_set():
                return 130
    finally:
        signal.signal(signal.SIGINT, previous)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
