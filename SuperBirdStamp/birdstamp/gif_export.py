from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageColor

DEFAULT_GIF_BACKGROUND_COLOR = "#000000"


@dataclass(slots=True)
class GifExportOptions:
    output_path: Path
    fps: float = 24.0
    loop: int = 0
    scale_factors: tuple[float, ...] = ()
    background_color: str = DEFAULT_GIF_BACKGROUND_COLOR

    def normalized_output_path(self) -> Path:
        output_path = self.output_path.resolve(strict=False)
        if output_path.suffix.lower() != ".gif":
            output_path = output_path.with_suffix(".gif")
        return output_path


@dataclass(slots=True)
class GifExportProgress:
    phase: str
    current: int
    total: int
    message: str
    requested_fps: float = 0.0
    effective_fps: float = 0.0
    input_frame_count: int = 0
    encoded_frame_count: int = 0
    duration_ms: int = 0
    output_index: int = 0
    total_outputs: int = 0


@dataclass(frozen=True, slots=True)
class GifFrameTiming:
    requested_fps: float
    input_frame_count: int
    frame_indices: tuple[int, ...]
    durations_ms: tuple[int, ...]
    duration_ms: int

    @property
    def encoded_frame_count(self) -> int:
        return len(self.frame_indices)

    @property
    def effective_fps(self) -> float:
        return self.encoded_frame_count * 1000.0 / self.duration_ms

    def summary(self) -> str:
        sampling = "，已按时间采样" if self.requested_fps > 100 else ""
        return (
            f"请求 {self.requested_fps:g} FPS，GIF 实际 {self.effective_fps:.3f} FPS"
            f"（{self.encoded_frame_count} 帧，{self.duration_ms / 1000.0:.3f} 秒{sampling}）"
        )


GifExportProgressCallback = Callable[[GifExportProgress], None]


def validate_gif_export_options(options: GifExportOptions) -> GifExportOptions:
    if options.output_path is None:
        raise ValueError("GIF 输出路径不能为空。")

    try:
        fps = float(options.fps)
    except Exception as exc:
        raise ValueError("GIF FPS 无效。") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("GIF FPS 必须为大于 0 的有限数值。")

    try:
        loop = int(options.loop)
    except Exception as exc:
        raise ValueError("GIF 循环次数无效。") from exc
    loop = max(0, loop)

    scales: list[float] = []
    seen_scales: set[float] = set()
    for scale in options.scale_factors:
        try:
            parsed = float(scale)
        except Exception:
            continue
        if parsed <= 0 or parsed >= 1:
            continue
        normalized = round(parsed, 6)
        if normalized in seen_scales:
            continue
        seen_scales.add(normalized)
        scales.append(parsed)

    background_color = _safe_background_color(options.background_color)
    return GifExportOptions(
        output_path=options.normalized_output_path(),
        fps=fps,
        loop=loop,
        scale_factors=tuple(scales),
        background_color=background_color,
    )


def build_gif_frame_timing(frame_count: int, fps: float) -> GifFrameTiming:
    """Quantize cumulative time to GIF's 10 ms ticks, sampling above 100 FPS.

    A nonempty clip always has at least one 10 ms frame. Otherwise its total
    duration differs from the requested timeline by at most half a tick.
    """
    if frame_count <= 0:
        raise ValueError("GIF 帧为空。")
    requested_fps = float(fps)
    if not math.isfinite(requested_fps) or requested_fps <= 0:
        raise ValueError("GIF FPS 必须为大于 0 的有限数值。")
    rate = Fraction(str(requested_fps))
    if rate > 100:
        encoded_count = max(1, round(frame_count * 100 / rate))
        indices = tuple(min(frame_count - 1, round(index * rate / 100)) for index in range(encoded_count))
        durations = (10,) * encoded_count
    else:
        indices = tuple(range(frame_count))
        boundaries = [round(index * 100 / rate) for index in range(frame_count + 1)]
        durations = tuple((end - start) * 10 for start, end in zip(boundaries, boundaries[1:]))
    if max(durations) > 655350:
        raise ValueError("GIF 单帧时长不能超过 655.35 秒，请提高 FPS。")
    return GifFrameTiming(requested_fps, frame_count, indices, durations, sum(durations))


def build_gif_variant_output_paths(output_path: Path, scale_factors: Iterable[float]) -> list[tuple[float, Path]]:
    base_output = output_path.resolve(strict=False)
    variants: list[tuple[float, Path]] = []
    seen_scales: set[float] = set()
    for scale in scale_factors:
        normalized = round(float(scale), 6)
        if normalized <= 0 or normalized >= 1 or normalized in seen_scales:
            continue
        seen_scales.add(normalized)
        suffix = _scale_suffix(normalized)
        variants.append((normalized, base_output.with_name(f"{base_output.stem}__{suffix}{base_output.suffix}")))
    return variants


def resolve_gif_target_size(frame_paths: Sequence[Path]) -> tuple[int, int]:
    width = 0
    height = 0
    for frame_path in frame_paths:
        with Image.open(frame_path) as image:
            width = max(width, int(image.width))
            height = max(height, int(image.height))
    if width <= 0 or height <= 0:
        raise ValueError("无法确定 GIF 帧尺寸。")
    return (width, height)


def normalize_gif_frame_size(
    image: Image.Image,
    target_size: tuple[int, int],
    *,
    background_color: str = DEFAULT_GIF_BACKGROUND_COLOR,
) -> Image.Image:
    target_width = max(1, int(target_size[0]))
    target_height = max(1, int(target_size[1]))
    frame = image.convert("RGB")
    if frame.width == target_width and frame.height == target_height:
        return frame

    scale = min(target_width / float(frame.width), target_height / float(frame.height))
    resized_width = max(1, min(target_width, int(round(frame.width * scale))))
    resized_height = max(1, min(target_height, int(round(frame.height * scale))))
    if (resized_width, resized_height) != frame.size:
        frame = frame.resize((resized_width, resized_height), Image.Resampling.LANCZOS)

    background = Image.new("RGB", (target_width, target_height), ImageColor.getrgb(_safe_background_color(background_color)))
    offset_x = max(0, (target_width - frame.width) // 2)
    offset_y = max(0, (target_height - frame.height) // 2)
    background.paste(frame, (offset_x, offset_y))
    return background


def export_gif(
    frame_paths: Sequence[Path],
    options: GifExportOptions,
    *,
    progress_callback: GifExportProgressCallback | None = None,
) -> list[Path]:
    validated = validate_gif_export_options(options)
    normalized_frame_paths = [Path(path).resolve(strict=False) for path in frame_paths]
    if not normalized_frame_paths:
        raise ValueError("没有可用于合成 GIF 的图片。")
    timing = build_gif_frame_timing(len(normalized_frame_paths), validated.fps)
    sampled_frame_paths = [normalized_frame_paths[index] for index in timing.frame_indices]

    output_path = validated.normalized_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _emit_progress(
        progress_callback,
        phase="scan",
        current=0,
        total=timing.encoded_frame_count,
        message=f"正在检查 GIF 编码帧尺寸。{timing.summary()}",
        timing=timing,
    )
    target_size = resolve_gif_target_size(sampled_frame_paths)

    output_specs = [(1.0, output_path)]
    output_specs.extend(build_gif_variant_output_paths(output_path, validated.scale_factors))

    total_outputs = len(output_specs)
    written_paths: list[Path] = []
    for index, (scale, variant_output_path) in enumerate(output_specs, start=1):
        variant_target_size = _scaled_target_size(target_size, scale)
        _emit_progress(
            progress_callback,
            phase="encode",
            current=0,
            total=timing.encoded_frame_count,
            message=f"正在合成 GIF {index}/{total_outputs}: {variant_output_path.name} | {timing.summary()}",
            timing=timing,
            output_index=index,
            total_outputs=total_outputs,
        )
        _save_gif_variant(
            sampled_frame_paths,
            variant_output_path,
            durations_ms=timing.durations_ms,
            loop=validated.loop,
            target_size=variant_target_size,
            background_color=validated.background_color,
            frame_prepared_callback=lambda current: _emit_progress(
                progress_callback,
                phase="encode",
                current=current,
                total=timing.encoded_frame_count,
                message=f"GIF {index}/{total_outputs} 已准备编码帧 {current}/{timing.encoded_frame_count} | {timing.summary()}",
                timing=timing,
                output_index=index,
                total_outputs=total_outputs,
            ),
        )
        written_paths.append(variant_output_path)
        _emit_progress(
            progress_callback,
            phase="done",
            current=timing.encoded_frame_count,
            total=timing.encoded_frame_count,
            message=f"已生成 GIF {index}/{total_outputs}: {variant_output_path.name} | {timing.summary()}",
            timing=timing,
            output_index=index,
            total_outputs=total_outputs,
        )

    return written_paths


def _save_gif_variant(
    frame_paths: Sequence[Path],
    output_path: Path,
    *,
    durations_ms: Sequence[int],
    loop: int,
    target_size: tuple[int, int],
    background_color: str,
    frame_prepared_callback: Callable[[int], None] | None = None,
) -> None:
    frames: list[Image.Image] = []
    try:
        for frame_path in frame_paths:
            with Image.open(frame_path) as image:
                frames.append(
                    normalize_gif_frame_size(
                        image,
                        target_size,
                        background_color=background_color,
                    )
                )
            if frame_prepared_callback is not None:
                frame_prepared_callback(len(frames))
        if not frames:
            raise ValueError("GIF 帧为空。")

        primary = frames[0]
        append_frames = frames[1:]
        primary.save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=append_frames,
            duration=list(durations_ms),
            loop=max(0, int(loop)),
            optimize=False,
            disposal=2,
        )
    finally:
        for frame in frames:
            try:
                frame.close()
            except Exception:
                pass


def _scaled_target_size(target_size: tuple[int, int], scale: float) -> tuple[int, int]:
    if scale >= 1.0:
        return (max(1, int(target_size[0])), max(1, int(target_size[1])))
    return (
        max(1, int(round(float(target_size[0]) * float(scale)))),
        max(1, int(round(float(target_size[1]) * float(scale)))),
    )


def _safe_background_color(color_text: str) -> str:
    text = str(color_text or "").strip() or DEFAULT_GIF_BACKGROUND_COLOR
    try:
        ImageColor.getrgb(text)
    except Exception:
        return DEFAULT_GIF_BACKGROUND_COLOR
    return text


def _scale_suffix(scale: float) -> str:
    fraction = Fraction(scale).limit_denominator(64)
    if fraction.denominator == 1:
        return f"{fraction.numerator}x"
    return f"{fraction.numerator}_{fraction.denominator}"


def _emit_progress(
    callback: GifExportProgressCallback | None,
    *,
    phase: str,
    current: int,
    total: int,
    message: str,
    timing: GifFrameTiming | None = None,
    output_index: int = 0,
    total_outputs: int = 0,
) -> None:
    if callback is None:
        return
    callback(
        GifExportProgress(
            phase=phase,
            current=max(0, int(current)),
            total=max(0, int(total)),
            message=str(message or "").strip(),
            requested_fps=timing.requested_fps if timing is not None else 0.0,
            effective_fps=timing.effective_fps if timing is not None else 0.0,
            input_frame_count=timing.input_frame_count if timing is not None else 0,
            encoded_frame_count=timing.encoded_frame_count if timing is not None else 0,
            duration_ms=timing.duration_ms if timing is not None else 0,
            output_index=output_index,
            total_outputs=total_outputs,
        )
    )


__all__ = [
    "DEFAULT_GIF_BACKGROUND_COLOR",
    "GifExportOptions",
    "GifExportProgress",
    "GifExportProgressCallback",
    "GifFrameTiming",
    "build_gif_frame_timing",
    "build_gif_variant_output_paths",
    "export_gif",
    "normalize_gif_frame_size",
    "resolve_gif_target_size",
    "validate_gif_export_options",
]
