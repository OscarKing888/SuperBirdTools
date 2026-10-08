from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import typer

from birdstamp.config import load_config, write_default_config
from birdstamp.constants import SUPPORTED_EXTENSIONS
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.discover import discover_inputs
from app_common.exif_io import (
    extract_many_with_xmp_priority,
    extract_metadata_with_xmp_priority,
    get_exiftool_executable_path,
    find_xmp_sidecar,
)
from birdstamp.meta.normalize import normalize_metadata
from birdstamp.naming import build_output_name
from birdstamp.gui.template_context import (
    AutoProxyTemplateContextProvider,
    PhotoInfo,
    TEMPLATE_SOURCE_AUTO,
    build_template_context_provider,
)

app = typer.Typer(add_completion=False, no_args_is_help=True, help="极速鸟框 photo banner CLI.")
LOGGER = logging.getLogger("birdstamp")


@app.command("recommend-regions")
def recommend_regions_command(
    frames: list[Path] = typer.Argument(..., exists=True, dir_okay=False),
    reference: Path = typer.Option(..., "--reference", exists=True, dir_okay=False),
    output: Path = typer.Option(..., "--output", "-o"),
    method: str = typer.Option("reference_region"),
    part: str = typer.Option("auto", help="auto/head/torso/legs"),
    target: int | None = typer.Option(None, min=1, help="多鸟时使用报告中从 1 开始的候选编号。"),
    count: int = typer.Option(9, min=1, max=36),
    experimental_parts: bool = typer.Option(False, "--experimental-parts"),
    model_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
):
    """按去抖动算法推荐选区，并抽样预检；不导出或修改原片。"""
    from birdstamp.region_recommendation_cli import recommend_files
    try:
        result = recommend_files(frames,reference,output,method=method,part=part,target_index=target,
            target_count=count,experimental=experimental_parts,model_file=model_file,progress=typer.echo)
    except (ValueError,OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(result.message)
    if result.status != 'ready':
        raise typer.Exit(2)


@app.command("bird-parts-model")
def bird_parts_model_command(source: Path | None = typer.Option(None, exists=True, dir_okay=False)):
    """显式下载 109 MiB 官方部位模型，或用 --source 离线导入。"""
    from birdstamp.image_dejitter.bird_parts.model_store import install_model
    try:
        typer.echo(str(install_model(source)))
    except (ValueError,OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("stabilize")
def stabilize_command(
    frames: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="按顺序排列的源照片。"),
    reference: Path = typer.Option(..., "--reference", exists=True, dir_okay=False),
    regions: Path = typer.Option(..., "--regions", exists=True, dir_okay=False, help="归一化矩形 ROI / 关键帧 JSON。"),
    output: Path = typer.Option(..., "--output", "-o", help="结果父目录；每次建立独立子目录。"),
    method: str = typer.Option("subject_local", help="reference_region 或 subject_local。"),
    mode: str = typer.Option("lock", help="lock 局部锁定；follow 自然跟随。"),
    strength: int = typer.Option(100, min=0, max=100),
    window: int = typer.Option(5, min=3, max=31),
    pad: bool = typer.Option(False, "--pad/--no-pad"),
    debug: bool = typer.Option(False, "--debug", help="附加真实对应点 tracks.npz。"),
    follow_bird: bool = typer.Option(False, "--follow-bird", help="仅基本方法：背景去抖后画框平滑跟随目标鸟（两段式）。"),
    follow_window: int = typer.Option(9, min=3, max=31, help="目标鸟跟随窗口（帧）。"),
) -> None:
    """按基本或高级识别策略去抖动，导出 PNG 和数值诊断。"""
    from birdstamp.subject_stabilization_cli import stabilize_files
    try:
        folder = stabilize_files(frames,reference,regions,output,method=method,mode=mode,
                                 strength=strength,window=window,pad=pad,debug=debug,progress=typer.echo,
                                 follow_bird=follow_bird,follow_window=follow_window)
    except (ValueError,OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(str(folder))


@app.command("gif")
def gif_command(
    frames: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="按播放顺序排列的已渲染图片。"),
    output: Path = typer.Option(..., "--output", "-o", help="GIF 输出路径。"),
    fps: float = typer.Option(24.0, help="每秒帧数。"),
    repeat_fps: list[float] = typer.Option(
        [], "--repeat-fps", help="完整序列再播放一遍的 FPS，可重复指定，例如 --repeat-fps 10 --repeat-fps 5。",
    ),
    loop: int = typer.Option(0, help="循环次数，0 表示无限循环。"),
    wechat: bool = typer.Option(False, "--wechat/--no-wechat", help="额外生成不超过 5 MB 的微信表情版本。"),
) -> None:
    """将已渲染图片合成为 GIF，可额外导出微信表情。"""
    from birdstamp.gif_export import GifExportOptions, export_gif

    try:
        outputs = export_gif(frames, GifExportOptions(output, fps=fps, loop=loop, repeat_fps=tuple(repeat_fps or ()), wechat_sticker=wechat))
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    for path in outputs:
        typer.echo(str(path))


@dataclass(slots=True)
class _Result:
    source: Path
    status: str          # ok | skipped | failed
    output: Path | None = None
    elapsed: float = 0.0
    error: str | None = None


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_multi_values(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        for item in str(value).split(","):
            token = item.strip().lower()
            if token:
                items.append(token)
    return items


def _resolve_output_format(fmt: str) -> tuple[str, str]:
    f = fmt.lower()
    if f in {"jpeg", "jpg"}:
        return "jpg", "JPEG"
    if f == "png":
        return "png", "PNG"
    raise ValueError(f"output format must be jpeg/jpg or png, got: {fmt!r}")


def _save_image(image, path: Path, pil_format: str, quality: int, *, source_path: Path) -> None:
    from birdstamp.export_metadata import save_export_image
    path.parent.mkdir(parents=True, exist_ok=True)
    if pil_format == "JPEG":
        save_export_image(image, path, source_path=source_path, format="JPEG", quality=max(1, min(100, quality)), optimize=True, progressive=True)
    else:
        save_export_image(image, path, source_path=source_path, format="PNG", optimize=True)


def _find_template_path(template_arg: str | None) -> Path | None:
    """Resolve template name or path to a .json file.

    Returns None to signal "use built-in default".
    """
    if not template_arg:
        return None
    candidate = Path(template_arg)
    if candidate.exists():
        return candidate
    # Look in config/templates/
    try:
        from birdstamp.gui.editor_template import ensure_template_repository, template_directory
        cfg_dir = template_directory()
        ensure_template_repository(cfg_dir)
        cfg_path = cfg_dir / f"{template_arg}.json"
        if cfg_path.exists():
            return cfg_path
    except Exception:
        pass
    return None


@app.command()
def render(
    input_path: Path = typer.Argument(..., exists=True, resolve_path=True),
    out: Path | None = typer.Option(None, "--out", help="Output directory."),
    recursive: bool = typer.Option(False, "--recursive", help="Recursively scan input directories."),
    template: str | None = typer.Option(None, "--template", help="Template name or .json file path (default: built-in default)."),
    max_long_edge: int | None = typer.Option(None, "--max-long-edge", min=0, help="Resize long edge to this value (0=unlimited)."),
    output_format: str | None = typer.Option(None, "--format", help="Output format: jpeg|png"),
    quality: int | None = typer.Option(None, "--quality", min=1, max=100),
    name_template: str | None = typer.Option(None, "--name", help='Output filename template, e.g. "{stem}__banner.{ext}"'),
    use_exiftool: str | None = typer.Option(None, "--use-exiftool", help="auto|on|off"),
    skip_existing: bool = typer.Option(True, "--skip-existing/--no-skip-existing"),
    draw_banner: bool = typer.Option(True, "--draw-banner/--no-draw-banner", help="Draw banner background."),
    draw_text: bool = typer.Option(True, "--draw-text/--no-draw-text", help="Draw text fields."),
    draw_images: bool = typer.Option(True, "--draw-images/--no-draw-images", help="Draw image overlays."),
    text_scale: float = typer.Option(1.0, "--text-scale", min=0.25, max=3.0, help="Text scale multiplier after automatic canvas scaling."),
    platform_safe_area: str = typer.Option('off', '--platform-safe-area', help='Fullscreen overlay safe area: off|xiaohongshu|bilibili|douyin.'),
    log_level: str = typer.Option("info", "--log-level"),
) -> None:
    """Render BirdStamp banner overlay onto images using a JSON template."""
    _setup_logging(log_level)
    from birdstamp.overlays.safe_area import PLATFORMS, normalize_platform
    if str(platform_safe_area).strip().lower() not in PLATFORMS:
        raise typer.BadParameter('Choose off, xiaohongshu, bilibili or douyin.', param_hint='--platform-safe-area')
    platform_safe_area = normalize_platform(platform_safe_area)
    cfg = load_config()

    fmt_str = output_format or str(cfg.get("output_format", "jpeg"))
    try:
        out_ext, pil_format = _resolve_output_format(fmt_str)
    except ValueError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(1)

    quality_val = int(quality if quality is not None else cfg.get("quality", 92))
    max_edge_val = int(max_long_edge if max_long_edge is not None else cfg.get("max_long_edge", 0))
    name_tmpl = name_template or str(cfg.get("name_template", "{stem}__banner.{ext}"))
    exiftool_mode = (use_exiftool or str(cfg.get("use_exiftool", "auto"))).lower()

    # Lazy-import GUI rendering modules (PIL-only, no display required)
    try:
        from birdstamp.gui.editor_template import (
            default_template_payload,
            load_template_payload,
            normalize_template_payload,
            render_template_overlay,
        )
        from birdstamp.gui.editor_utils import build_metadata_context
        from birdstamp.gui.editor_core import (
            apply_full_crop,
            parse_padding_value as _parse_padding,
            parse_bool_value as _parse_bool,
            parse_ratio_value as _parse_ratio,
            should_use_crop_box_override,
        )
    except Exception as exc:
        typer.secho(f"Render engine unavailable: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)

    # Load template payload
    tpl_path = _find_template_path(template)
    if tpl_path is not None:
        try:
            raw_payload = load_template_payload(tpl_path)
            template_payload = normalize_template_payload(raw_payload, fallback_name=tpl_path.stem)
            LOGGER.info("Template: %s", tpl_path)
        except Exception as exc:
            typer.secho(f"Template load failed: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(1)
    else:
        template_payload = default_template_payload(name=template or "default")
        LOGGER.info("Template: built-in default")

    # Resolved template name used in output filename {template} placeholder
    tpl_name: str = str(template_payload.get("name") or template or "banner")

    # Discover files
    files = discover_inputs(input_path, recursive=recursive)
    if not files:
        typer.echo("No supported image files found.")
        raise typer.Exit(0)

    out_dir = out
    if out_dir is None:
        out_dir = (input_path / "output") if input_path.is_dir() else (input_path.parent / "output")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Batch metadata extraction
    resolved_files = [p.resolve(strict=False) for p in files]
    try:
        raw_meta_map = extract_many_with_xmp_priority(resolved_files, mode=exiftool_mode)
    except Exception as exc:
        typer.secho(f"Metadata extraction setup failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)

    def process_one(source: Path) -> _Result:
        t0 = time.perf_counter()
        try:
            resolved = source.resolve(strict=False)
            raw_meta = raw_meta_map.get(resolved) or extract_metadata_with_xmp_priority(source, mode=exiftool_mode)
            norm_meta = normalize_metadata(
                source,
                raw_meta,
                bird_arg=None,
                bird_priority=["meta", "filename"],
                bird_regex=r"(?P<bird>[^_]+)_",
            )
            output_name = build_output_name(name_tmpl, source, norm_meta, extension=out_ext, template_name=tpl_name)
            output_file = out_dir / output_name
            if skip_existing and output_file.exists():
                return _Result(source=source, status="skipped", output=output_file, elapsed=time.perf_counter() - t0)
            image = decode_image(source)

            # Apply ratio crop from template (e.g. 9:16 portrait)
            tpl_ratio = _parse_ratio(template_payload.get("ratio"))
            tpl_center = str(template_payload.get("center_mode") or "image")
            tpl_fill = str(template_payload.get("crop_padding_fill") or "#FFFFFF")
            # Effective max_long_edge: CLI arg overrides template; 0 = unlimited
            tpl_max_edge = max(0, int(template_payload.get("max_long_edge") or 0))
            effective_max_edge = max_edge_val if max_long_edge is not None or max_edge_val > 0 else tpl_max_edge

            image = apply_full_crop(
                image,
                raw_metadata=raw_meta,
                ratio=tpl_ratio,
                center_mode=tpl_center,
                inner_top=_parse_padding(template_payload.get("crop_padding_top"), 0),
                inner_bottom=_parse_padding(template_payload.get("crop_padding_bottom"), 0),
                inner_left=_parse_padding(template_payload.get("crop_padding_left"), 0),
                inner_right=_parse_padding(template_payload.get("crop_padding_right"), 0),
                max_long_edge=effective_max_edge,
                fill_color=tpl_fill,
                crop_box_override=template_payload.get("crop_box")
                if should_use_crop_box_override(template_payload) else None,
                custom_center=(template_payload["custom_center_x"], template_payload["custom_center_y"]),
            )

            metadata_ctx = build_metadata_context(source, raw_meta)
            rendered = render_template_overlay(
                image,
                raw_metadata=raw_meta,
                metadata_context=metadata_ctx,
                template_payload=template_payload,
                draw_banner=draw_banner,
                draw_text=draw_text,
                draw_images=draw_images,
                text_scale=text_scale,
                platform_safe_area=platform_safe_area,
            )
            rendered = rendered.convert("RGB")
            _save_image(rendered, output_file, pil_format=pil_format, quality=quality_val, source_path=source)
            return _Result(source=source, status="ok", output=output_file, elapsed=time.perf_counter() - t0)
        except Exception as exc:
            return _Result(source=source, status="failed", error=str(exc), elapsed=time.perf_counter() - t0)

    results: list[_Result] = []
    for f in files:
        r = process_one(f)
        results.append(r)
        if r.status == "ok":
            LOGGER.info("OK   %s -> %s  (%.2fs)", r.source.name, r.output.name if r.output else "-", r.elapsed)
        elif r.status == "skipped":
            LOGGER.info("SKIP %s (exists)", r.source.name)
        else:
            LOGGER.error("FAIL %s  %s", r.source.name, r.error)

    ok = sum(1 for r in results if r.status == "ok")
    skip = sum(1 for r in results if r.status == "skipped")
    failed = [r for r in results if r.status == "failed"]
    typer.echo(f"Done. success={ok} skipped={skip} failed={len(failed)}")
    if failed:
        typer.secho("Failures:", fg=typer.colors.RED)
        for r in failed:
            typer.secho(f"  {r.source}: {r.error}", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("inspect")
def inspect_file(
    file: Path = typer.Argument(..., exists=True, resolve_path=True, dir_okay=False),
    use_exiftool: str = typer.Option("auto", "--use-exiftool", help="auto|on|off"),
    bird: str | None = typer.Option(None, "--bird"),
    bird_from: str = typer.Option("arg,meta,filename", "--bird-from"),
    bird_regex: str = typer.Option(r"(?P<bird>[^_]+)_", "--bird-regex"),
    time_format: str = typer.Option("%Y-%m-%d %H:%M", "--time-format"),
    raw: bool = typer.Option(False, "--raw", help="Include raw metadata payload."),
    sources: bool = typer.Option(False, "--sources", help="Include metadata source diagnostics."),
) -> None:
    resolved = file.resolve(strict=False)
    mode = use_exiftool.lower()
    try:
        raw_metadata = extract_metadata_with_xmp_priority(file, mode=mode)
    except Exception as exc:
        typer.secho(f"Metadata extraction failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)
    metadata = normalize_metadata(
        file,
        raw_metadata,
        bird_arg=bird,
        bird_priority=_parse_multi_values([bird_from]) or ["arg", "meta", "filename"],
        bird_regex=bird_regex,
        time_format=time_format,
    )
    payload = metadata.to_dict()
    if raw:
        payload["raw_metadata"] = raw_metadata
    if sources:
        payload["metadata_sources"] = {
            "requested_mode": mode,
            "resolved_exiftool": get_exiftool_executable_path(),
            "sidecar_xmp": find_xmp_sidecar(str(file)),
        }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("inspect-auto-proxy")
def inspect_auto_proxy(
    file: Path = typer.Argument(..., exists=True, resolve_path=True, dir_okay=False),
    field: str = typer.Argument("bird_species_cn", help="Logical field key resolved by AutoProxy."),
    use_exiftool: str = typer.Option("auto", "--use-exiftool", help="auto|on|off"),
) -> None:
    """Inspect how AutoProxy resolves a logical template field for one file."""
    resolved = file.resolve(strict=False)
    mode = use_exiftool.lower()
    try:
        raw_metadata = extract_metadata_with_xmp_priority(file, mode=mode)
    except Exception as exc:
        typer.secho(f"Metadata extraction failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)

    photo_info = PhotoInfo.from_path(
        resolved,
        sidecar_path=find_xmp_sidecar(str(file)),
        raw_metadata=raw_metadata,
    )
    provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, field)
    payload = {
        "file": str(resolved),
        "sidecar_path": str(photo_info.sidecar_path) if photo_info.sidecar_path else None,
        "field": field,
        "display_caption": provider.get_display_caption(photo_info),
        "text_content": provider.get_text_content(photo_info),
    }
    if isinstance(provider, AutoProxyTemplateContextProvider):
        payload["candidates"] = [
            {
                "provider_id": candidate.provider_id,
                "provider_name": candidate.provider_name,
                "source_key": candidate.source_key,
                "display_caption": candidate.display_caption,
                "text_content": candidate.text_content,
            }
            for candidate in provider.inspect_candidates(photo_info)
        ]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("init-config")
def init_config(
    force: bool = typer.Option(False, "--force", help="Overwrite existing config file."),
) -> None:
    path = write_default_config(force=force)
    typer.echo(f"Config initialized: {path}")


@app.command()
def gui(
    file: Path | None = typer.Option(
        None,
        "--file",
        exists=True,
        resolve_path=True,
        dir_okay=False,
        help="Open this image file on startup.",
    ),
) -> None:
    try:
        from birdstamp.gui import launch_gui
    except Exception as exc:
        typer.secho(f"GUI is unavailable: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)

    try:
        launch_gui(startup_file=file)
    except Exception as exc:
        typer.secho(f"GUI failed to start: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
