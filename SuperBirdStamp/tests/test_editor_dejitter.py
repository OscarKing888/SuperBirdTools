"""参考区状态、预览坐标和全局导出设置的集成回归。"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from birdstamp import config
from birdstamp.gui import editor
from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'cache'))
    for method in ('_start_bird_detector_preload', '_run_deferred_startup_tasks',
                   '_restart_photo_list_metadata_loader', '_schedule_workspace_photo_selection'):
        monkeypatch.setattr(editor.BirdStampEditorWindow, method, lambda *args, **kwargs: None)
    instance = editor.BirdStampEditorWindow()
    # 固定预览像素，避免测试启动磁盘解码/识别；工作区存储已在构造前隔离。
    instance.current_path = tmp_path / '参考.png'
    instance.current_source_image = Image.new('RGB', (200, 100))
    instance.current_source_full_size = (2000, 1000)
    instance._preview_outer_pad = (10, 30, 20, 40)
    monkeypatch.setattr(instance, '_on_output_settings_changed', lambda *args: (
        instance._refresh_global_export_settings_snapshot(), instance._mark_all_photo_exports_dirty()))
    yield instance
    instance.close()
    instance.deleteLater()
    _APP.processEvents()


def test_selection_with_padding_roundtrips_but_is_separate_from_template_export(window):
    box = (.2, .3, .7, .8)
    preview_box = window._reference_regions_source_to_preview((box,))[0]
    window._on_canvas_reference_region_changed((preview_box,))
    np.testing.assert_allclose(window._dejitter_reference_regions[0], box)
    assert window.dejitter_reference_check.isChecked()
    assert not hasattr(window, "dejitter_reference_clear_btn")
    assert window.dejitter_region_list.count() == 1
    settings = window._build_current_render_settings()
    assert settings['dejitter_reference_strength'] == 100
    assert window._current_global_export_settings()['dejitter_reference_source'] == str(window.current_path)
    target = {'dejitter_reference_source': 'old.png', 'dejitter_reference_regions': []}
    window._apply_global_export_settings_to_render_settings(target)
    assert target['dejitter_reference_source'] is None
    assert target['dejitter_reference_regions'] == []
    assert target['dejitter_strategy'] == 'none'
    assert not any(key.startswith('dejitter_') for key in window._photo_override_settings_from_snapshot(settings))


def test_auto_add_regions_keeps_existing_reference_and_invalidates_analysis(window):
    rng = np.random.default_rng(83)
    window.current_source_image.close()
    window.current_source_image = Image.fromarray(rng.integers(15, 240, (300, 450, 3), dtype=np.uint8))
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    original = (.03, .03, .28, .28)
    window._commit_source_reference_regions(window.current_path, (original,))
    window.dejitter_auto_regions_btn.click()
    assert window._dejitter_reference_regions[0] == original
    assert 1 < len(window._dejitter_reference_regions) <= 5
    assert window._dejitter_reference_source == str(window.current_path)
    assert window.dejitter_region_list.count() == len(window._dejitter_reference_regions)
    assert window._sequence_preview is None


def test_disabling_preserves_regions_and_workspace_snapshot(window):
    window._on_canvas_reference_region_changed(((.2, .3, .7, .8),))
    regions = window._dejitter_reference_regions
    window.dejitter_reference_strength_slider.setValue(35)
    window.dejitter_reference_check.setChecked(False)
    snapshot = window._clone_render_settings(window._build_current_render_settings())
    assert not snapshot['dejitter_reference_enabled']
    assert snapshot['dejitter_reference_regions']
    window._on_dejitter_reference_clear()
    window._restore_dejitter_reference_from_settings(snapshot)
    assert window._dejitter_reference_regions == regions
    assert window.dejitter_reference_strength_slider.value() == 35
    assert not window.dejitter_reference_check.isChecked()
    window.dejitter_reference_check.setChecked(True)
    assert window._build_current_render_settings()['dejitter_reference_enabled']


def test_switching_photo_does_not_relabel_reference_or_overlay_old_coordinates(window):
    window._on_canvas_reference_region_changed(((.2, .3, .7, .8),))
    source = window._dejitter_reference_source
    snapshot = window._build_current_render_settings()
    window.current_path = window.current_path.with_name('another.png')
    assert window._visible_dejitter_reference_regions() == ()
    # 旧照片快照中的全局参考区不能覆盖当前设置。
    window._apply_render_settings_to_ui({**snapshot, 'dejitter_reference_regions': [], 'dejitter_reference_enabled': False})
    assert window._dejitter_reference_source == source
    assert window._dejitter_reference_regions
    window._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
    window._apply_preview_overlay_options_from_ui()
    assert not window.preview_label.canvas.reference_regions()


def test_padding_only_selection_is_not_a_reference(window):
    window._on_canvas_reference_region_changed(((0, 0, .05, .05),))
    assert not window._dejitter_reference_regions
    assert not window.dejitter_reference_check.isChecked()


def test_clearing_photo_list_resets_reference(window):
    window._on_canvas_reference_region_changed(((.2, .3, .7, .8),))
    window._clear_photos_state(show_placeholder=False)
    assert not window._dejitter_reference_regions
    assert window._dejitter_reference_source is None
    assert not window.dejitter_reference_check.isChecked()


def test_reference_outline_survives_refresh_and_mode_changes(window):
    from PyQt6.QtGui import QColor, QPixmap
    from birdstamp.gui.edit_modes import EDIT_MODE_NONE, EDIT_MODE_CROP_ADJUST
    from birdstamp.gui.editor_preview_canvas import EditorPreviewOverlayState
    window.preview_pixmap = QPixmap(260, 140)
    window.preview_pixmap.fill(QColor('black'))
    window.preview_overlay_state = EditorPreviewOverlayState()
    window._on_canvas_reference_region_changed(((.2, .3, .7, .8),))
    expected = window.preview_label.canvas.reference_regions()
    for mode in (EDIT_MODE_NONE, EDIT_MODE_REFERENCE_REGION, EDIT_MODE_CROP_ADJUST):
        window._set_edit_mode_button_checked(mode)
        # 渲染器重建的叠加状态没有参考区，不能覆盖持久源坐标。
        window.preview_overlay_state = EditorPreviewOverlayState()
        window._refresh_preview_label(preserve_view=True)
        assert window.preview_label.canvas.reference_regions() == expected
        assert window.preview_label.canvas._show_reference_regions
        image = window.preview_label.canvas.render_source_pixmap_with_overlays().toImage()
        assert image.pixelColor(52, 60) != QColor('black')
    window.dejitter_reference_check.setChecked(False)
    window._refresh_preview_label(preserve_view=True)
    assert window.preview_label.canvas.reference_regions() == expected


def test_retired_composition_ui_and_workspace_flags_are_ignored(window):
    assert not hasattr(window, 'uniform_auto_crop_check')
    assert not hasattr(window, 'auto_crop_stabilization_slider')
    old = dict(uniform_auto_crop=True, auto_crop_stabilization=100, dejitter_strategy='median')
    window._apply_workspace_global_export_state(old)
    settings = window._normalize_render_settings(old, fallback=window._build_current_render_settings())
    assert 'uniform_auto_crop' not in settings and 'auto_crop_stabilization' not in settings
    assert settings['dejitter_strategy'] == 'none'
    assert 'uniform_auto_crop' not in window._current_global_export_settings()
