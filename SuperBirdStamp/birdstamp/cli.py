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


def _save_image(image, path: Path, pil_format: str, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pil_format == "JPEG":
        image.save(path, format="JPEG", quality=max(1, min(100, quality)), optimize=True, progressive=True)
    else:
        image.save(path, format="PNG", optimize=True)


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
    log_level: str = typer.Option("info", "--log-level"),
) -> None:
    """Render BirdStamp banner overlay onto images using a JSON template."""
    _setup_logging(log_level)
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
            apply_editor_crop,
            parse_bool_value as _parse_bool,
            parse_ratio_value as _parse_ratio,
            resize_fit,
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
            effective_max_edge = max_edge_val if max_edge_val > 0 else tpl_max_edge

            if tpl_ratio is not None:
                image = apply_editor_crop(
                    image,
                    source_path=source,
                    raw_metadata=raw_meta,
                    ratio=tpl_ratio,
                    center_mode=tpl_center,
                    crop_padding_px=0,   # 模板的 crop_padding_* 是鸟检测内缩偏移，不是外边距
                    max_long_edge=effective_max_edge,
                    fill_color=tpl_fill,
                )
            elif effective_max_edge > 0:
                image = resize_fit(image, effective_max_edge)

            metadata_ctx = build_metadata_context(source, raw_meta)
            rendered = render_template_overlay(
                image,
                raw_metadata=raw_meta,
                metadata_context=metadata_ctx,
                template_payload=template_payload,
                draw_banner=draw_banner,
                draw_text=draw_text,
            )
            rendered = rendered.convert("RGB")
            _save_image(rendered, output_file, pil_format=pil_format, quality=quality_val)
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
