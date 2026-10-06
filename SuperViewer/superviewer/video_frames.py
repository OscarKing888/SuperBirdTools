# -*- coding: utf-8 -*-
"""Bounded, cancellable video timeline samples, independent of Qt."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, OrderedDict
from dataclasses import dataclass, replace
import io
import math
import os
import re
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
# 不超过该时长且有多格共用关键帧时，顺序解码一遍取精确帧，避免逐格从关键帧重复解码。
SEQUENTIAL_REFINE_MAX_SECONDS = 30.0
_PTS_TIME = re.compile(rb'Parsed_showinfo.*?\bpts_time:\s*(-?[\d.]+)')


def _coarse_to_fine(count):
    """0, 16, 8, 24, 4 …: every prefix covers the whole timeline evenly."""
    return sorted(range(count), key=lambda i: (-(i & -i) if i else -count - 1, i))


def _cancelled_error():
    return RuntimeError('序列帧提取已取消')


def video_frames(path, duration, *, fps=0, cancelled=lambda: False,
                 on_progress=lambda frames: None):
    """Sample the entire timeline using input seeks, never a full-video decode.

    Keyframe-only seeks fill the strip coarse-to-fine first (no GOP decoding);
    samples that share a keyframe are then refined to exact frames. on_progress
    receives immutable tuples (None for pending frames). Only complete results
    enter the bounded LRU; file size/mtime invalidate replaced sources.
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
        raise _cancelled_error()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
    if cached is not None:
        on_progress(cached)
        return cached

    count = min(FRAME_COUNT, max(1, math.ceil(duration * fps)))
    # 采样每格中点；末帧留一帧余量，兼容很短的片段及舍入后的时长。
    times = [min((index + .5) * duration / count, max(0.0, duration - 1 / fps))
             for index in range(count)]
    frames = [None] * count
    jpegs = [None] * count
    ffmpeg = find_ffmpeg()
    deadline = time.monotonic() + 45.0
    scale = (f"scale=w='if(gte(iw,ih),min(iw,{FRAME_SIZE}),-2)':"
             f"h='if(gte(iw,ih),-2,min(ih,{FRAME_SIZE}))'")

    def grab(seconds, keyframe):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('序列帧提取超时')
        # 关键帧模式只解码定位点前的关键帧；-copyts 保留其真实时间戳，
        # 否则早于定位点的关键帧在尾段会被当作负时间丢弃。
        fast = ['-skip_frame', 'nokey', '-noaccurate_seek', '-copyts'] if keyframe else []
        code, data, error = run_video_tool([
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-threads', '1' if keyframe else '4', *fast, '-ss', f'{seconds:.6f}', '-i', path,
            '-map', '0:V:0', '-an', '-sn', '-frames:v', '1', '-vf', scale,
            '-threads', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1',
        ], cancelled=cancelled, timeout=min(12.0, remaining))
        if cancelled():
            raise _cancelled_error()
        return code, data, error

    def decode(seconds, data):
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert('RGB')
            return VideoFrame(seconds, image.tobytes(), image.width, image.height)

    for index in _coarse_to_fine(count):
        if cancelled():
            raise _cancelled_error()
        code, data, _ = grab(times[index], True)
        if code or not data:
            # 关键帧标记缺失或不可靠的封装（如部分 TS）退回精确定位。
            code, data, error = grab(times[index], False)
            if code:
                raise RuntimeError(error.decode('utf-8', errors='replace').strip()[:500]
                                   or '无法读取视频序列帧')
        if data:
            frames[index], jpegs[index] = decode(times[index], data), data
            on_progress(tuple(frames))
    if not any(frames):
        raise RuntimeError('无法读取视频序列帧')
    for index in range(count):
        if frames[index] is None:
            # 音轨可能比画面长；无视频帧的尾部沿用最近的可解码画面。
            source = min((i for i in range(count) if jpegs[i]),
                         key=lambda i: (i > index, abs(i - index)))
            frames[index] = replace(frames[source], seconds=times[index])
    on_progress(tuple(frames))

    # 多格解出同一关键帧说明 GOP 长于格距，这些格（含首格）都需精确帧。
    repeats = Counter(data for data in jpegs if data)
    shared = [index for index, data in enumerate(jpegs) if data and repeats[data] > 1]
    if shared:
        try:
            if duration <= SEQUENTIAL_REFINE_MAX_SECONDS:
                _refine_sequential(ffmpeg, path, times, duration / count, frames,
                                   deadline, cancelled)
                on_progress(tuple(frames))
            else:
                for index in _coarse_to_fine(len(shared)):
                    index = shared[index]
                    code, data, _ = grab(times[index], False)
                    if not code and data:
                        frames[index] = decode(times[index], data)
                        on_progress(tuple(frames))
        except Exception:
            if cancelled():
                raise _cancelled_error()
            # 精确帧只是锦上添花：超时或旧版 FFmpeg 不支持时保留关键帧结果。

    result = tuple(frames)
    if cancelled():
        raise _cancelled_error()
    with _CACHE_LOCK:
        _CACHE[key] = result
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return result


def _refine_sequential(ffmpeg, path, times, step, frames, deadline, cancelled):
    """One decode of a short clip, keeping the first frame of each sample interval."""
    width, height = frames[0].width, frames[0].height
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    # 第 i 格自 (i + .5) * step 起，末格从留余量后的末采样点起（仍在倒数第二格内）；
    # select 只输出每格首帧，showinfo 给出其时间。
    last = len(times) - 1
    bucket = (f'(min(floor((t-{step / 2:.6f}+0.0005)/{step:.6f}),{last - 1})'
              f'+gte(t,{times[-1] - 0.0005:.6f}))')
    code, data, error = run_video_tool([
        ffmpeg, '-hide_banner', '-loglevel', 'info', '-nostdin', '-threads', '4',
        '-i', path, '-map', '0:V:0', '-an', '-sn',
        '-vf', f"select='gte({bucket},ld(0))*st(0,{bucket}+1)',"
               f'scale={width}:{height},showinfo',
        '-fps_mode', 'passthrough', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1',
    ], cancelled=cancelled, timeout=remaining)
    if cancelled():
        raise _cancelled_error()
    size = width * height * 3
    stamps = [float(value) for value in _PTS_TIME.findall(error)]
    if code or not stamps or len(data) != size * len(stamps):
        return
    for index, seconds in enumerate(times):
        # 末格可能为留余量而提前、画面也可能早于音轨结束：都取最后输出的画面。
        position = min(bisect_left(stamps, seconds - 0.0005), len(stamps) - 1)
        frames[index] = VideoFrame(seconds, data[position * size:(position + 1) * size],
                                   width, height)

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
