from pathlib import Path

from PIL import Image

from birdstamp.gif_export import (
    GifExportOptions,
    build_gif_variant_output_paths,
    export_gif,
    normalize_gif_frame_size,
    resolve_gif_target_size,
    validate_gif_export_options,
)


def _build_sample_frame(path: Path, size: tuple[int, int], color: str) -> Path:
    image = Image.new("RGB", size, color)
    image.save(path, format="PNG")
    image.close()
    return path


def test_validate_gif_export_options_filters_invalid_scale_values(tmp_path) -> None:
    validated = validate_gif_export_options(
        GifExportOptions(
            output_path=tmp_path / "demo",
            fps=8,
            loop=-1,
            scale_factors=(0.5, 0.5, 1.0, -0.25, 0.25),
        )
    )
    assert validated.normalized_output_path().suffix == ".gif"
    assert validated.loop == 0
    assert validated.scale_factors == (0.5, 0.25)


def test_resolve_gif_target_size_uses_max_frame_envelope(tmp_path) -> None:
    frame_paths = [
        _build_sample_frame(tmp_path / "frame_1.png", (80, 60), "#FF0000"),
        _build_sample_frame(tmp_path / "frame_2.png", (120, 50), "#00FF00"),
    ]
    assert resolve_gif_target_size(frame_paths) == (120, 60)


def test_normalize_gif_frame_size_letterboxes_to_canvas() -> None:
    image = Image.new("RGB", (120, 60), "#FF0000")
    normalized = normalize_gif_frame_size(image, (120, 120), background_color="#000000")
    try:
        assert normalized.size == (120, 120)
        assert normalized.getpixel((10, 10)) == (0, 0, 0)
        assert normalized.getpixel((60, 60)) == (255, 0, 0)
    finally:
        normalized.close()
        image.close()


def test_build_gif_variant_output_paths_appends_scale_suffixes(tmp_path) -> None:
    variants = build_gif_variant_output_paths(tmp_path / "birdstamp.gif", [0.5, 0.25, 0.5])
    assert variants == [
        (0.5, tmp_path / "birdstamp__1_2.gif"),
        (0.25, tmp_path / "birdstamp__1_4.gif"),
    ]


def test_export_gif_writes_main_and_scaled_outputs(tmp_path) -> None:
    frame_paths = [
        _build_sample_frame(tmp_path / "frame_1.png", (80, 60), "#FF0000"),
        _build_sample_frame(tmp_path / "frame_2.png", (120, 50), "#0000FF"),
    ]
    written = export_gif(
        frame_paths,
        GifExportOptions(
            output_path=tmp_path / "birdstamp.gif",
            fps=5,
            loop=0,
            scale_factors=(0.5,),
            background_color="#101010",
        ),
    )
    assert written == [
        (tmp_path / "birdstamp.gif").resolve(),
        (tmp_path / "birdstamp__1_2.gif").resolve(),
    ]

    with Image.open(written[0]) as image:
        assert image.size == (120, 60)
        assert getattr(image, "n_frames", 1) == 2
        assert image.info.get("duration") == 200
        assert image.info.get("loop") == 0

    with Image.open(written[1]) as image:
        assert image.size == (60, 30)
        assert getattr(image, "n_frames", 1) == 2
