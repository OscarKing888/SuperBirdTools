"""视频「重复播放」时间线：完整序列按各自 FPS 追加若干遍，并映射到恒定输出帧率。

视频以单一帧率编码，因此每遍的帧时长按累计时间量化为输出帧数（四舍五入，非银行家
舍入），慢速遍通过重复同一渲染帧实现。输出帧率取各遍最高 FPS；若其不超过
``MAX_REPEAT_OUTPUT_FPS`` 的小倍数能让所有遍整除，则改用该倍数，使每帧时长精确。
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import os
from pathlib import Path
import shutil
from typing import Sequence

MAX_REPEAT_OUTPUT_FPS = 120
_MAX_OUTPUT_FPS_MULTIPLIER = 4


@dataclass(frozen=True, slots=True)
class VideoRepeatTimeline:
    segment_fps: tuple[float, ...]
    output_fps: float
    input_frame_count: int
    # 每个输出帧对应的输入帧序号（0 起）。
    frame_indices: tuple[int, ...]

    @property
    def encoded_frame_count(self) -> int:
        return len(self.frame_indices)

    @property
    def duration_seconds(self) -> float:
        return self.encoded_frame_count / self.output_fps

    def summary(self) -> str:
        fps_text = " → ".join(f"{fps:g}" for fps in self.segment_fps)
        return (
            f"重复播放 {fps_text} FPS（完整序列 {len(self.segment_fps)} 遍），"
            f"输出 {self.output_fps:g} FPS，共 {self.encoded_frame_count} 帧，{self.duration_seconds:.3f} 秒"
        )


def validated_fps(value: object, label: str) -> float:
    try:
        fps = float(value)  # type: ignore[arg-type]
    except Exception as exc:
        raise ValueError(f"{label} 必须为数字。") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"{label} 必须为大于 0 的有限数值。")
    return fps


def resolve_repeat_output_fps(segment_fps: Sequence[float]) -> float:
    rates = [Fraction(str(float(fps))) for fps in segment_fps]
    fastest = max(rates)
    for multiplier in range(1, _MAX_OUTPUT_FPS_MULTIPLIER + 1):
        candidate = fastest * multiplier
        if multiplier > 1 and candidate > MAX_REPEAT_OUTPUT_FPS:
            break
        if all((candidate / rate).denominator == 1 for rate in rates):
            return float(candidate)
    return float(fastest)


def build_video_repeat_timeline(frame_count: int, fps: float, repeat_fps: Sequence[float]) -> VideoRepeatTimeline:
    if frame_count <= 0:
        raise ValueError("没有可用于生成视频的图片。")
    segment_fps = (validated_fps(fps, "FPS"),) + tuple(
        validated_fps(value, f"第 {index} 遍 FPS") for index, value in enumerate(repeat_fps, start=2)
    )
    output_fps = resolve_repeat_output_fps(segment_fps)
    output_rate = Fraction(str(output_fps))
    half = Fraction(1, 2)
    indices: list[int] = []
    for fps_value in segment_fps:
        ticks_per_frame = output_rate / Fraction(str(fps_value))
        # floor(x + 1/2)：每帧至少 1 个输出帧（ticks_per_frame ≥ 1），整遍时长误差不超过半帧。
        boundaries = [math.floor(index * ticks_per_frame + half) for index in range(frame_count + 1)]
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            indices.extend([index] * (end - start))
    return VideoRepeatTimeline(segment_fps, output_fps, frame_count, tuple(indices))


def link_timeline_frames(frame_paths: Sequence[Path], timeline: VideoRepeatTimeline, target_dir: Path) -> None:
    """在 ``target_dir`` 生成 ``frame_%06d.png`` 序列；优先硬链接，不支持时复制。"""
    if len(frame_paths) != timeline.input_frame_count:
        raise ValueError(f"视频帧数量不一致: {len(frame_paths)} != {timeline.input_frame_count}")
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    can_link = True
    for output_index, frame_index in enumerate(timeline.frame_indices, start=1):
        source = frame_paths[frame_index]
        target = target_dir / f"frame_{output_index:06d}.png"
        if can_link:
            try:
                os.link(source, target)
                continue
            except OSError:
                can_link = False
        shutil.copyfile(source, target)


__all__ = [
    "MAX_REPEAT_OUTPUT_FPS",
    "VideoRepeatTimeline",
    "build_video_repeat_timeline",
    "link_timeline_frames",
    "resolve_repeat_output_fps",
]
