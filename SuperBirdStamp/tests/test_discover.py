from pathlib import Path

from birdstamp.discover import discover_inputs


def test_discover_inputs_skips_apple_double_metadata_files(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    nested = image_dir / "nested"
    nested.mkdir(parents=True)
    photo = image_dir / "DSC06705.jpg"
    apple_double = image_dir / "._DSC06705.jpg"
    nested_photo = nested / "DSC06706.png"
    nested_apple_double = nested / "._DSC06706.png"
    for path in (photo, apple_double, nested_photo, nested_apple_double):
        path.write_bytes(b"image")

    flat = [path.name for path in discover_inputs(image_dir, recursive=False)]
    recursive = {
        path.relative_to(image_dir).as_posix()
        for path in discover_inputs(image_dir, recursive=True)
    }

    assert flat == ["DSC06705.jpg"]
    assert recursive == {"DSC06705.jpg", "nested/DSC06706.png"}
    assert discover_inputs(apple_double, recursive=False) == []
