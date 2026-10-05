# -*- coding: utf-8 -*-
"""Bounded, cancellable video timeline samples, independent of Qt."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import io
import math
import os
import threading
import time

from app_common.video import find_ffmpeg, run_video_tool


@dataclass(frozen=True)
class VideoFrame:
    seconds: float
    rgb: bytes
    width: int
    height: int


_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 8  # 最多约 28 MB RGB 数据，不随浏览文件数增长。
FRAME_COUNT = 32
FRAME_SIZE = 192


def video_frames(path, duration, *, fps=0, cancelled=lambda: False,
                 on_progress=lambda frames: None):
    """Sample the entire timeline using input seeks, never a full-video decode.

    on_progress receives immutable tuples (None for pending frames). Only complete
    results enter the bounded LRU; file size/mtime invalidate replaced sources.
    """
    from PIL import Image

    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('无法读取视频时长')
    fps = float(fps or 0)
    fps = fps if math.isfinite(fps) and fps > 0 else 25.0
    path = os.path.abspath(os.fspath(path))
    stat = os.stat(path)
    key = (path, stat.st_size, stat.st_mtime_ns, duration, fps)
    if cancelled():
        raise RuntimeError('序列帧提取已取消')
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
    if cached is not None:
        on_progress(cached)
        return cached

    count = min(FRAME_COUNT, max(1, math.ceil(duration * fps)))
    frames = [None] * count
    ffmpeg = find_ffmpeg()
    deadline = time.monotonic() + 45.0
    scale = (f"scale=w='if(gte(iw,ih),min(iw,{FRAME_SIZE}),-2)':"
             f"h='if(gte(iw,ih),-2,min(ih,{FRAME_SIZE}))'")
    for index in range(count):
        if cancelled():
            raise RuntimeError('序列帧提取已取消')
        # 采样每格中点；末帧留一帧余量，兼容很短的片段及舍入后的时长。
        seconds = min((index + .5) * duration / count, max(0.0, duration - 1 / fps))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('序列帧提取超时')
        code, data, error = run_video_tool([
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-threads', '1', '-ss', f'{seconds:.6f}', '-i', path,
            '-map', '0:V:0', '-an', '-sn', '-frames:v', '1', '-vf', scale,
            '-threads', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1',
        ], cancelled=cancelled, timeout=min(12.0, remaining))
        if cancelled():
            raise RuntimeError('序列帧提取已取消')
        if code:
            raise RuntimeError(error.decode('utf-8', errors='replace').strip()[:500]
                               or '无法读取视频序列帧')
        if not data and index:
            # 音轨可能比画面长；无后续视频帧的尾部沿用最后可解码画面。
            previous = frames[index - 1]
            frame = VideoFrame(seconds, previous.rgb, previous.width, previous.height)
        else:
            if not data:
                raise RuntimeError('无法读取视频序列帧')
            with Image.open(io.BytesIO(data)) as image:
                image = image.convert('RGB')
                frame = VideoFrame(seconds, image.tobytes(), image.width, image.height)
        frames[index] = frame
        on_progress(tuple(frames))

    result = tuple(frames)
    if cancelled():
        raise RuntimeError('序列帧提取已取消')
    with _CACHE_LOCK:
        _CACHE[key] = result
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return result


if __name__ == '__main__':
    import argparse
    from PIL import Image
    from app_common.video import probe_video

    parser = argparse.ArgumentParser(description='提取视频序列帧预览图（不修改视频）')
    parser.add_argument('path')
    parser.add_argument('--output', required=True, help='PNG 预览图输出路径')
    args = parser.parse_args()
    info = probe_video(args.path)
    samples = video_frames(args.path, info.get('duration', 0), fps=info.get('fps', 0))
    canvas = Image.new('RGB', (FRAME_SIZE * len(samples), FRAME_SIZE), '#15171a')
    for i, frame in enumerate(samples):
        image = Image.frombytes('RGB', (frame.width, frame.height), frame.rgb)
        canvas.paste(image, (i * FRAME_SIZE + (FRAME_SIZE - frame.width) // 2,
                             (FRAME_SIZE - frame.height) // 2))
    canvas.save(args.output, format='PNG')
