"""进入裁剪工具时启用当前照片的比例，保留逐图、模板及静默恢复边界。"""
from copy import deepcopy

import pytest

from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_utils import path_key
from test_editor_dejitter import window, _APP
from test_editor_preview_sources import _prepare, _select_again, _source
from test_reference_tracking import wait_until


def _select_photo(window, path):
    window.photo_list.blockSignals(True)
    try:
        window.photo_list.setCurrentItem(window._find_photo_item_by_path(path))
    finally:
        window.photo_list.blockSignals(False)
    _select_again(window, path)


@pytest.mark.parametrize("ratio", ["no_crop", None, "free", 1.0])
def test_crop_tool_updates_only_current_no_crop_photo(window, monkeypatch, tmp_path, ratio):
    first = _source(tmp_path, "白鹭.png")
    second = _source(tmp_path, "翠鸟.png")
    _prepare(window, monkeypatch, [first, second])
    # 通用窗口 fixture 简化了设置回写；此处验证真实信号到逐图保存和预览的完整路径。
    monkeypatch.setattr(window, "_on_output_settings_changed",
                        BirdStampEditorWindow._on_output_settings_changed.__get__(window))
    _select_photo(window, second)
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData("no_crop"))
    window._on_output_settings_changed()
    other_settings = deepcopy(window.photo_render_overrides[path_key(second)])
    _select_photo(window, first)
    window._set_center_mode_value("image", emit_changed=False)
    window._set_crop_padding_state(top=0, bottom=0, left=0, right=0, fill="#FFFFFF")
    window.ratio_combo.setCurrentIndex(window._ratio_combo_index_for_value(ratio))
    window._on_output_settings_changed()
    template_payload = deepcopy(window.current_template_payload)
    template_files = {path: path.read_bytes() for path in window.template_dir.glob("*.json")}

    window._edit_mode_buttons["crop_adjust"].click()
    expected = 9 / 16 if ratio == "no_crop" else ratio
    assert window._selected_ratio() == expected
    assert window.photo_render_overrides[path_key(first)]["ratio"] == expected
    assert window.photo_render_overrides[path_key(second)] == other_settings
    assert window.current_template_payload == template_payload
    assert {path: path.read_bytes() for path in window.template_dir.glob("*.json")} == template_files
    wait_until(lambda: not window._preview_debounce_timer.isActive())
    canvas = window.preview_label.canvas
    assert canvas.crop_edit_mode()
    if ratio == "no_crop":
        assert window.ratio_combo.currentText() == "9:16"
        context = canvas._crop_pixel_context
        width, height = context.crop_size(window.preview_overlay_state.crop_effect_box)
        assert (width, height) == (45, 80)

    window._edit_mode_buttons["none"].click()
    assert window._selected_ratio() == expected
    _select_photo(window, second)
    assert window._selected_ratio() == other_settings["ratio"]
    _select_photo(window, first)
    assert window._selected_ratio() == expected


def test_restored_crop_tool_keeps_no_crop_until_clicked(window, monkeypatch, tmp_path):
    source = _source(tmp_path)
    _prepare(window, monkeypatch, [source])
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData("no_crop"))
    window._apply_workspace_edit_mode({"edit_mode": "crop_adjust"})
    window.render_preview()
    assert window._selected_ratio() == "no_crop"
    assert not window.preview_label.canvas.crop_edit_mode()

    # 精简过的用户比例列表仍能提供明确请求的 9:16。
    window.ratio_combo.removeItem(window._ratio_combo_index_for_value(9 / 16))
    window._edit_mode_buttons["crop_adjust"].click()
    assert window._selected_ratio() == 9 / 16


def test_crop_tool_without_photo_keeps_no_crop(window):
    window.current_path = None
    window.current_source_image = None
    window.ratio_combo.setCurrentIndex(window.ratio_combo.findData("no_crop"))
    window._edit_mode_buttons["crop_adjust"].click()
    assert window._selected_ratio() == "no_crop"
