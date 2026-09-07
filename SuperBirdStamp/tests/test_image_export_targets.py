from pathlib import Path
import unicodedata

from PIL import Image

from birdstamp.gui.editor_exporter import _BirdStampExporterMixin


def test_case_colliding_batch_outputs_retain_every_image(tmp_path):
    sources = [Path("first/a.jpg"), Path("second/A.png"), Path("third/a.hif")]
    targets = _BirdStampExporterMixin._build_batch_image_targets(
        None, sources, out_dir=tmp_path, suffix="png",
    )
    assert [path.name for path in targets] == [
        "a__birdstamp.png", "A__birdstamp_2.png", "a__birdstamp_3.png",
    ]
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    for target, color in zip(targets, colors):
        Image.new("RGB", (2, 2), color).save(target)
    assert len(list(tmp_path.glob("*.png"))) == 3
    for target, color in zip(targets, colors):
        with Image.open(target) as image:
            assert image.getpixel((0, 0)) == color


def test_batch_targets_handle_unicode_equivalence_and_leave_distinct_stems_alone(tmp_path):
    sources = [Path("É.jpg"), Path("E\u0301.png"), Path("白鹭.hif")]
    targets = _BirdStampExporterMixin._build_batch_image_targets(
        None, sources, out_dir=tmp_path, suffix="jpg",
    )
    keys = [unicodedata.normalize("NFC", path.name).casefold() for path in targets]
    assert len(set(keys)) == len(sources)
    assert targets[2].name == "白鹭__birdstamp.jpg"
