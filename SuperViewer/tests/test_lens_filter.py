from __future__ import annotations

import os
from pathlib import Path

import pytest

from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def panel(qt_app, tmp_path: Path):
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("", encoding="utf-8")
    panel = SuperViewerTaggedFileListPanel(tag_config_path=cfg)
    yield panel
    panel.shutdown()
    panel.close()
    panel.deleteLater()
    qt_app.processEvents()


def _seed(panel, tmp_path: Path) -> list[str]:
    lenses = {
        "a.jpg": {"LensModel": "FE 200-600mm F5.6-6.3 G OSS"},
        "b.jpg": {"XMP-aux:LensModel": "FE 200-600mm F5.6-6.3 G OSS"},
        "c.jpg": {"lens_model": "RF 100-500mm F4.5-7.1 L IS USM"},
        "d.jpg": {},
    }
    paths = []
    for name, meta in lenses.items():
        path = os.path.normpath(str(tmp_path / name))
        panel._meta_cache[path] = meta
        paths.append(path)
    panel._all_files = list(paths)
    return paths


def _items(combo) -> list[tuple[str, str]]:
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


def test_lens_options_collected_from_listed_photos(panel, tmp_path: Path):
    _seed(panel, tmp_path)
    combo = panel._lens_filter_combo
    assert combo is not None
    combo.popupAboutToShow.emit()
    assert _items(combo) == [
        ("全部镜头", ""),
        ("FE 200-600mm F5.6-6.3 G OSS (2)", "FE 200-600mm F5.6-6.3 G OSS"),
        ("RF 100-500mm F4.5-7.1 L IS USM (1)", "RF 100-500mm F4.5-7.1 L IS USM"),
    ]


def test_lens_filter_limits_visible_photos(panel, tmp_path: Path):
    a, b, c, d = _seed(panel, tmp_path)
    combo = panel._lens_filter_combo
    combo.popupAboutToShow.emit()
    combo.setCurrentIndex(combo.findData("FE 200-600mm F5.6-6.3 G OSS"))
    assert panel._has_any_filter()
    assert panel._compute_filtered_files() == [a, b]
    # Options still cover every listed photo, not just the filtered ones.
    combo.popupAboutToShow.emit()
    assert combo.count() == 3
    assert combo.currentData() == "FE 200-600mm F5.6-6.3 G OSS"

    combo.setCurrentIndex(0)
    assert not panel._has_any_filter()
    assert panel._compute_filtered_files() == [a, b, c, d]


def test_active_lens_kept_when_absent_from_new_listing(panel, tmp_path: Path):
    _seed(panel, tmp_path)
    panel.set_lens_filter("RF 100-500mm F4.5-7.1 L IS USM")
    panel._all_files = []
    combo = panel._lens_filter_combo
    combo.popupAboutToShow.emit()
    assert _items(combo) == [
        ("全部镜头", ""),
        ("RF 100-500mm F4.5-7.1 L IS USM (0)", "RF 100-500mm F4.5-7.1 L IS USM"),
    ]
    assert combo.currentIndex() == 1
