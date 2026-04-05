from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageColor

DEFAULT_GIF_BACKGROUND_COLOR = "#000000"


@dataclass(slots=True)
class GifExportOptions:
    output_path: Path
    fps: float = 6.0
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


GifExportProgressCallback = Callable[[GifExportProgress], None]


def validate_gif_export_options(options: GifExportOptions) -> GifExportOptions:
    if options.output_path is None:
        raise ValueError("GIF 输出路径不能为空。")

    try:
        fps = float(options.fps)
    except Exception as exc:
        raise ValueError("GIF FPS 无效。") from exc
    if fps <= 0:
        raise ValueError("GIF FPS 必须大于 0。")

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

    output_path = validated.normalized_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _emit_progress(
        progress_callback,
        phase="scan",
        current=0,
        total=len(normalized_frame_paths),
        message=f"正在检查 GIF 帧尺寸，共 {len(normalized_frame_paths)} 帧。",
    )
    target_size = resolve_gif_target_size(normalized_frame_paths)

    output_specs = [(1.0, output_path)]
    output_specs.extend(build_gif_variant_output_paths(output_path, validated.scale_factors))

    total_outputs = len(output_specs)
    written_paths: list[Path] = []
    for index, (scale, variant_output_path) in enumerate(output_specs, start=1):
        variant_target_size = _scaled_target_size(target_size, scale)
        _emit_progress(
            progress_callback,
            phase="encode",
            current=index - 1,
            total=total_outputs,
            message=f"正在合成 GIF {index}/{total_outputs}: {variant_output_path.name}",
        )
        _save_gif_variant(
            normalized_frame_paths,
            variant_output_path,
            fps=validated.fps,
            loop=validated.loop,
            target_size=variant_target_size,
            background_color=validated.background_color,
        )
        written_paths.append(variant_output_path)
        _emit_progress(
            progress_callback,
            phase="encode",
            current=index,
            total=total_outputs,
            message=f"已生成 GIF {index}/{total_outputs}: {variant_output_path.name}",
        )

    return written_paths


def _save_gif_variant(
    frame_paths: Sequence[Path],
    output_path: Path,
    *,
    fps: float,
    loop: int,
    target_size: tuple[int, int],
    background_color: str,
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
        if not frames:
            raise ValueError("GIF 帧为空。")

        duration_ms = max(1, int(round(1000.0 / max(0.001, float(fps)))))
        primary = frames[0]
        append_frames = frames[1:]
        primary.save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=append_frames,
            duration=duration_ms,
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
) -> None:
    if callback is None:
        return
    callback(
        GifExportProgress(
            phase=phase,
            current=max(0, int(current)),
            total=max(0, int(total)),
            message=str(message or "").strip(),
        )
    )


__all__ = [
    "DEFAULT_GIF_BACKGROUND_COLOR",
    "GifExportOptions",
    "GifExportProgress",
    "GifExportProgressCallback",
    "build_gif_variant_output_paths",
    "export_gif",
    "normalize_gif_frame_size",
    "resolve_gif_target_size",
    "validate_gif_export_options",
]
