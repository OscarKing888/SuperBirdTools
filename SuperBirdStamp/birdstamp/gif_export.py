from __future__ import annotations

from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from fractions import Fraction
import logging
import math
import os
from pathlib import Path
import tempfile
from time import perf_counter
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageColor

DEFAULT_GIF_BACKGROUND_COLOR = "#000000"
WECHAT_GIF_MAX_BYTES = 5_000_000
WECHAT_GIF_MAX_LONG_EDGE = 480
_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class GifExportOptions:
    output_path: Path
    fps: float = 24.0
    loop: int = 0
    # 完整序列之后追加的重复段，每段整组播放一遍并使用各自的 FPS。
    repeat_fps: tuple[float, ...] = ()
    scale_factors: tuple[float, ...] = ()
    background_color: str = DEFAULT_GIF_BACKGROUND_COLOR
    wechat_sticker: bool = False

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
    segment_fps: tuple[float, ...] = ()

    @property
    def encoded_frame_count(self) -> int:
        return len(self.frame_indices)

    @property
    def effective_fps(self) -> float:
        return self.encoded_frame_count * 1000.0 / self.duration_ms

    @property
    def segment_count(self) -> int:
        return max(1, len(self.segment_fps))

    def summary(self) -> str:
        segment_fps = self.segment_fps or (self.requested_fps,)
        sampling = "，已按时间采样" if max(segment_fps) > 100 else ""
        if len(segment_fps) > 1:
            fps_text = " → ".join(f"{fps:g}" for fps in segment_fps)
            return (
                f"请求 {fps_text} FPS（完整序列 {len(segment_fps)} 遍），GIF 实际平均 {self.effective_fps:.3f} FPS"
                f"（{self.encoded_frame_count} 帧，{self.duration_ms / 1000.0:.3f} 秒{sampling}）"
            )
        return (
            f"请求 {self.requested_fps:g} FPS，GIF 实际 {self.effective_fps:.3f} FPS"
            f"（{self.encoded_frame_count} 帧，{self.duration_ms / 1000.0:.3f} 秒{sampling}）"
        )


GifExportProgressCallback = Callable[[GifExportProgress], None]


def validate_gif_export_options(options: GifExportOptions) -> GifExportOptions:
    if options.output_path is None:
        raise ValueError("GIF 输出路径不能为空。")

    fps = _validated_fps(options.fps, "GIF FPS")
    repeat_fps = tuple(
        _validated_fps(value, f"GIF 第 {index} 遍 FPS")
        for index, value in enumerate(options.repeat_fps or (), start=2)
    )

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
        repeat_fps=repeat_fps,
        scale_factors=tuple(scales),
        background_color=background_color,
        wechat_sticker=bool(options.wechat_sticker),
    )


def build_gif_frame_timing(frame_count: int, fps: float, repeat_fps: Sequence[float] = ()) -> GifFrameTiming:
    """Quantize cumulative time to GIF's 10 ms ticks, sampling above 100 FPS.

    ``repeat_fps`` appends further passes over the complete sequence, each with
    its own rate. Every pass is quantized independently, so each pass keeps its
    own timeline: a nonempty pass always has at least one 10 ms frame, otherwise
    its duration differs from the requested one by at most half a tick.
    """
    if frame_count <= 0:
        raise ValueError("GIF 帧为空。")
    requested_fps = _validated_fps(fps, "GIF FPS")
    segment_fps = (requested_fps,) + tuple(
        _validated_fps(value, f"GIF 第 {index} 遍 FPS") for index, value in enumerate(repeat_fps or (), start=2)
    )
    indices: list[int] = []
    durations: list[int] = []
    for rate in segment_fps:
        segment_indices, segment_durations = _build_segment_timing(frame_count, rate)
        indices.extend(segment_indices)
        durations.extend(segment_durations)
    return GifFrameTiming(
        requested_fps, frame_count, tuple(indices), tuple(durations), sum(durations), segment_fps,
    )


def _build_segment_timing(frame_count: int, fps: float) -> tuple[tuple[int, ...], tuple[int, ...]]:
    rate = Fraction(str(fps))
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
    return indices, durations


def _validated_fps(value: object, label: str) -> float:
    try:
        fps = float(value)  # type: ignore[arg-type]
    except Exception as exc:
        raise ValueError(f"{label} 无效。") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"{label} 必须为大于 0 的有限数值。")
    return fps


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
        resized = frame.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
        frame.close()
        frame = resized

    background = Image.new("RGB", (target_width, target_height), ImageColor.getrgb(_safe_background_color(background_color)))
    offset_x = max(0, (target_width - frame.width) // 2)
    offset_y = max(0, (target_height - frame.height) // 2)
    background.paste(frame, (offset_x, offset_y))
    frame.close()
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
    timing = build_gif_frame_timing(len(normalized_frame_paths), validated.fps, validated.repeat_fps)
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
    target_size = resolve_gif_target_size(list(dict.fromkeys(sampled_frame_paths)))

    output_specs = [(1.0, output_path)]
    if validated.wechat_sticker:
        output_specs.append((None, output_path.with_name(f"{output_path.stem}__wechat.gif")))
    output_specs.extend(build_gif_variant_output_paths(output_path, validated.scale_factors))

    total_outputs = len(output_specs)
    written_paths: list[Path] = []
    for index, (scale, variant_output_path) in enumerate(output_specs, start=1):
        variant_target_size = _scaled_target_size(target_size, scale if scale is not None else 1.0)
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
        save_variant = _save_wechat_gif_variant if scale is None else _save_gif_variant
        save_variant(
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
            message=f"已生成 GIF {index}/{total_outputs}: {variant_output_path.name}"
            f" ({variant_output_path.stat().st_size / 1_000_000:.2f} MB) | {timing.summary()}",
            timing=timing,
            output_index=index,
            total_outputs=total_outputs,
        )

    return written_paths


def _save_wechat_gif_variant(
    frame_paths: Sequence[Path],
    output_path: Path,
    *,
    durations_ms: Sequence[int],
    loop: int,
    target_size: tuple[int, int],
    background_color: str,
    frame_prepared_callback: Callable[[int], None] | None = None,
) -> None:
    """Encode and measure each candidate; publish only a size-checked GIF.

    Keep the entire timeline and aspect ratio. Area-based estimates accelerate
    convergence but never substitute for measuring the encoded file. Even an
    unusually long clip whose frame overhead exceeds the budget fails safely.
    """
    size = _scaled_target_size(target_size, min(1.0, WECHAT_GIF_MAX_LONG_EDGE / max(target_size)))
    with tempfile.TemporaryDirectory(prefix=".birdstamp-wechat-", dir=output_path.parent) as temporary:
        candidate = Path(temporary) / "candidate.gif"
        while True:
            _save_gif_variant(
                frame_paths, candidate, durations_ms=durations_ms, loop=loop,
                target_size=size, background_color=background_color,
                frame_prepared_callback=frame_prepared_callback, optimize=True,
            )
            byte_count = candidate.stat().st_size
            if byte_count <= WECHAT_GIF_MAX_BYTES:
                os.replace(candidate, output_path)
                return
            if size == (1, 1):
                raise ValueError("微信表情 GIF 无法在保留全部帧和时长的情况下压缩到 5 MB，请减少照片数量。")
            ratio = max(0.5, min(0.85, math.sqrt(WECHAT_GIF_MAX_BYTES / byte_count) * 0.95))
            # Scale from the original canvas to avoid accumulating aspect-ratio rounding.
            long_edge = max(1, int(max(size) * ratio))
            size = _scaled_target_size(target_size, long_edge / max(target_size))


def _gif_prepare_memory_budget() -> int:
    # 只限制在途 RGB/量化临时像素；Pillow 最终仍需持有全部调色板帧。
    try:
        import psutil

        return min(2 * 1024**3, max(0, int(psutil.virtual_memory().available)) // 4)
    except (ImportError, OSError, AttributeError, ValueError, TypeError):
        return 512 * 1024**2


def _gif_prepare_workers(frame_paths: Sequence[Path], target_size: tuple[int, int]) -> int:
    # 小尺寸变体也必须计入原 PNG 的解码内存，不能只按 480px 成片估算。
    max_pixels = max(1, target_size[0] * target_size[1])
    for path in frame_paths:
        with Image.open(path) as source:
            max_pixels = max(max_pixels, source.width * source.height)
    cpu_count = max(1, getattr(os, "process_cpu_count", os.cpu_count)() or 1)
    memory_workers = max(1, _gif_prepare_memory_budget() // (max_pixels * 24))
    return max(1, min(8, cpu_count, memory_workers, len(frame_paths)))


def _prepare_gif_palette_frame(
    path: Path, target_size: tuple[int, int], background_color: str,
) -> Image.Image:
    with Image.open(path) as source:
        with normalize_gif_frame_size(source, target_size, background_color=background_color) as frame:
            # 与 Pillow GIF 的 RGB 默认转换完全相同；提前量化才能并行，
            # 重复播放复用 P 帧，避免 save_all 对每一遍重新执行昂贵的量化。
            return frame.convert("P", palette=Image.Palette.ADAPTIVE)


def _prepare_gif_palette_frames(
    frame_paths: Sequence[Path], target_size: tuple[int, int], background_color: str,
    prepared: dict[Path, Image.Image], frame_prepared_callback: Callable[[int], None] | None,
) -> int:
    counts = Counter(frame_paths)
    workers = _gif_prepare_workers(list(counts), target_size)
    paths = iter(counts)
    pending = {}
    completed = 0
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="birdstamp-gif-palette")
    try:
        def refill() -> None:
            while len(pending) < workers:
                path = next(paths, None)
                if path is None:
                    break
                pending[pool.submit(_prepare_gif_palette_frame, path, target_size, background_color)] = path

        refill()
        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                path = pending[future]
                prepared[path] = future.result()
                del pending[future]
                completed += counts[path]
                # 回调只在协调线程执行；完成顺序不影响最后按时间表排列的帧序。
                if frame_prepared_callback is not None:
                    frame_prepared_callback(completed)
            refill()
    finally:
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        # 失败/进度回调异常时，等待在途任务，再关闭未交接给调用方的图像。
        for future in pending:
            if not future.cancelled() and future.exception() is None:
                future.result().close()
    return workers


def _save_gif_variant(
    frame_paths: Sequence[Path],
    output_path: Path,
    *,
    durations_ms: Sequence[int],
    loop: int,
    target_size: tuple[int, int],
    background_color: str,
    frame_prepared_callback: Callable[[int], None] | None = None,
    optimize: bool = False,
) -> None:
    # 一个变体只保留每张源图的 P 帧（1 字节/像素），不保留整组 RGB 副本。
    prepared: dict[Path, Image.Image] = {}
    started = perf_counter()
    try:
        if not frame_paths:
            raise ValueError("GIF 帧为空。")
        workers = _prepare_gif_palette_frames(
            frame_paths, target_size, background_color, prepared, frame_prepared_callback,
        )
        prepared_at = perf_counter()
        frames = [prepared[path] for path in frame_paths]

        primary = frames[0]
        append_frames = frames[1:]
        primary.save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=append_frames,
            duration=list(durations_ms),
            loop=max(0, int(loop)),
            optimize=optimize,
            disposal=2,
        )
        _LOG.info("gif encode frames=%s unique_frames=%s size=%sx%s workers=%s "
                  "prepare_s=%.3f write_s=%.3f output=%s",
                  len(frames), len(prepared), *target_size, workers,
                  prepared_at - started, perf_counter() - prepared_at, output_path)
    finally:
        for frame in prepared.values():
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
    "WECHAT_GIF_MAX_BYTES",
    "WECHAT_GIF_MAX_LONG_EDGE",
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
