"""The source CR3 supplies one consistent box to thumbnails, previews and exports."""
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from app_common import raw_focus_metadata
from app_common.file_browser import _workers
from SuperViewer.superviewer.focus_box_loader import FocusBoxLoader
from SuperViewer.superviewer.focus_preview_loader import _load_focus_box_for_preview
from birdstamp.gui.editor_core import extract_focus_box_for_display as stamp_focus_box
from bird_sharpness.focus import default_focus_box


EXPECTED = (2909.5/6000, 1619.5/4000, 3068.5/6000, 1778.5/4000)


@pytest.fixture
def canon_source(tmp_path, monkeypatch):
    path = tmp_path / "中文照片(1).CR3"
    path.write_bytes(b"Canon source placeholder")
    metadata = {
        "SourceFile": str(path), "IFD0:Make": "Canon", "IFD0:Model": "Canon EOS R6m2",
        "IFD0:Orientation": 1, "ExifIFD:ExifImageWidth": 6000, "ExifIFD:ExifImageHeight": 4000,
        "Canon:NumAFPoints": 1053, "Canon:ValidAFPoints": 1, "Canon:AFAreaMode": 9,
        "Canon:AFImageWidth": 6000, "Canon:AFImageHeight": 4000,
        "Canon:AFPointsInFocus": [1] + [0]*65, "Canon:AFPointsSelected": [1] + [0]*65,
    }
    for name, first in (("AFAreaWidths", 159), ("AFAreaHeights", 159),
                        ("AFAreaXPositions", -11), ("AFAreaYPositions", 301)):
        metadata["Canon:" + name] = [first] + [0]*1052
    monkeypatch.setattr(raw_focus_metadata, "get_exiftool_executable_path", lambda: "exiftool")
    monkeypatch.setattr(raw_focus_metadata, "run_exiftool", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps([metadata]), stderr=""))
    return str(path), metadata


def test_cached_thumbnail_and_preview_share_the_canon_box(canon_source):
    path, _metadata = canon_source
    loader = _workers.MetadataLoader([path], object())
    raw_batch = loader._read_raw_embedded_focus_batch([path])
    assert loader._parse_rec(raw_batch[path])["focus_box"] == pytest.approx(EXPECTED)
    cached = loader._build_focus_cache_batch([path])
    assert cached[path]["focus_box"] == pytest.approx(EXPECTED)
    assert cached[path]["used_path"] == os.path.normpath(path)
    assert _load_focus_box_for_preview(path, 256, 171, allow_report_db_fallback=False) == pytest.approx(EXPECTED)


def test_focus_worker_reads_cr3_before_metadata_free_preview(canon_source, tmp_path):
    path, _metadata = canon_source
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (1200, 800)).save(preview)
    worker = FocusBoxLoader(7, path, str(preview), path, 1200, 800)
    emitted = []
    worker.focus_loaded.connect(lambda *args: emitted.append(args))
    worker.run()
    assert emitted[0][0] == 7 and emitted[0][2] == path
    assert emitted[0][1] == pytest.approx(EXPECTED)
    assert worker.result_size_independent


def test_birdstamp_and_sharpness_consume_shared_canon_geometry(canon_source):
    path, metadata = canon_source
    assert stamp_focus_box(metadata, 1200, 800) == pytest.approx(EXPECTED)
    assert default_focus_box(path, 1200, 800) == pytest.approx(EXPECTED)
    # 现有 RAW 可见区映射仍只由实际像素携带的几何决定。
    expected_raw = (.1+.8*EXPECTED[0], .05+.9*EXPECTED[1],
                    .1+.8*EXPECTED[2], .05+.9*EXPECTED[3])
    assert stamp_focus_box(metadata, 6100, 4100, camera_crop_box=(.1, .05, .9, .95)) == pytest.approx(expected_raw)
