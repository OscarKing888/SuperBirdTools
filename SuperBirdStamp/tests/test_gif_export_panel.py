"""GIF export controls remain usable when the sidebar width changes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from birdstamp.gui import editor_gif_panel


_APP = QApplication.instance() or QApplication([])


def test_scale_options_wrap_to_fit_sidebar_and_configured_count(monkeypatch) -> None:
    options = [
        ("1/2", 0.5),
        ("1/4", 0.25),
        ("1/8", 0.125),
        ("1/16", 0.0625),
        ("1/32", 0.03125),
    ]
    monkeypatch.setattr(editor_gif_panel, "GIF_SCALE_OPTIONS", options)
    panel = editor_gif_panel.GifExportPanel()
    try:
        checks = [check for _scale, check in panel._scale_checks]
        assert [check.text() for check in checks] == [label for label, _scale in options]
        field = checks[0].parentWidget()

        checks = [panel.wechat_sticker_check, *checks]
        for width in (220, 720, 220):
            panel.resize(width, 300)
            panel.show()
            _APP.processEvents()
            _APP.processEvents()
            for check in checks:
                assert field.rect().contains(check.geometry())
            if width == 220:
                assert len({check.y() for check in checks}) > 1
            else:
                assert len({check.y() for check in checks}) == 1

        checks[-1].setChecked(True)
        assert panel.current_request().scale_factors == [options[-1][1]]
    finally:
        panel.close()


def test_wechat_default_order_and_state_restore(monkeypatch, tmp_path) -> None:
    from birdstamp import config
    from birdstamp.gui.editor_workspace import _BirdStampWorkspaceMixin

    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path)
    panel = editor_gif_panel.GifExportPanel()
    signals = []
    panel.optionsChanged.connect(lambda: signals.append(True))
    try:
        assert panel.current_request().wechat_sticker is True
        assert panel.wechat_sticker_check.text() == "微信表情"
        assert panel.wechat_sticker_check.parentWidget().layout().itemAt(0).widget() is panel.wechat_sticker_check
        panel.set_state(wechat_sticker=False)
        assert panel.current_request().wechat_sticker is False
        assert not signals
        panel.wechat_sticker_check.setChecked(True)
        assert signals == [True]

        class Harness(_BirdStampWorkspaceMixin):
            gif_export_panel = panel
            _image_export_last_output_dir = None
            _batch_export_last_output_dir = None

            def _selected_output_suffix(self):
                return "gif"

            def _selected_export_stage_id(self):
                return "export_gif"

            def _current_pipeline_stage_order(self):
                return []

            def _current_pipeline_stage_enabled_map(self):
                return {}

            def _save_image_export_preferences(self):
                pass

            def _refresh_image_export_action_states(self):
                pass

        harness = Harness()
        workspace = tmp_path / "session.json"
        panel.set_state(wechat_sticker=False)
        state = harness._collect_workspace_image_export_state(workspace)
        panel.set_state(wechat_sticker=True)
        harness._apply_workspace_image_export_state(state, workspace)
        assert panel.current_request().wechat_sticker is False
        del state["gif_wechat_sticker"]
        harness._apply_workspace_image_export_state(state, workspace)
        assert panel.current_request().wechat_sticker is True
    finally:
        panel.close()


def test_wechat_default_comes_from_editor_options(monkeypatch) -> None:
    options = editor_gif_panel.editor_options
    monkeypatch.setattr(options, "_load_builtin_editor_options_raw", lambda: {"default_gif_wechat_sticker": False})
    loaded = options.load_editor_options()
    assert loaded["default_gif_wechat_sticker"] is False
    monkeypatch.setattr(options, "DEFAULT_GIF_WECHAT_STICKER", loaded["default_gif_wechat_sticker"])
    panel = editor_gif_panel.GifExportPanel()
    try:
        assert panel.current_request().wechat_sticker is False
    finally:
        panel.close()


def test_scale_label_keeps_room_for_native_checkbox_paint() -> None:
    panel = editor_gif_panel.GifExportPanel()
    panel.setStyleSheet("QWidget { font-size: 13px; }")
    try:
        panel.resize(270, 300)
        panel.show()
        _APP.processEvents()
        _APP.processEvents()

        checks = [check for _scale, check in panel._scale_checks]
        field = checks[0].parentWidget()
        assert checks[2].y() > checks[0].y()
        for check in checks:
            assert check.width() >= check.sizeHint().width() + 12
            assert field.rect().contains(check.geometry())
    finally:
        panel.close()


def test_repeat_passes_add_halved_fps_remove_and_restore(monkeypatch, tmp_path) -> None:
    from birdstamp import config
    from birdstamp.gui.editor_workspace import _BirdStampWorkspaceMixin

    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path)
    panel = editor_gif_panel.GifExportPanel()
    signals = []
    panel.optionsChanged.connect(lambda: signals.append(True))
    try:
        panel.set_state(fps=20)
        assert panel.current_request().repeat_fps == []
        panel.add_repeat_button.click()
        panel.add_repeat_button.click()
        assert panel.current_request().repeat_fps == [10.0, 5.0]
        assert [label.text() for _row, label, _spin in panel._repeat_rows] == ["第 2 遍", "第 3 遍"]
        assert len(signals) == 2

        panel._repeat_rows[1][2].setValue(3)
        assert panel.current_request().repeat_fps == [10.0, 3.0]
        assert len(signals) == 3

        remove_first = panel._repeat_rows[0][0].findChildren(editor_gif_panel.QPushButton)[0]
        remove_first.click()
        assert panel.current_request().repeat_fps == [3.0]
        assert [label.text() for _row, label, _spin in panel._repeat_rows] == ["第 2 遍"]
        assert len(signals) == 4

        signals.clear()
        panel.set_state(repeat_fps=[12, "bad", 0, 999, float("inf")])
        assert panel.current_request().repeat_fps == [12.0, 240.0]
        assert not signals

        class Harness(_BirdStampWorkspaceMixin):
            gif_export_panel = panel
            _image_export_last_output_dir = None
            _batch_export_last_output_dir = None

            def _selected_output_suffix(self):
                return "gif"

            def _selected_export_stage_id(self):
                return "export_gif"

            def _current_pipeline_stage_order(self):
                return []

            def _current_pipeline_stage_enabled_map(self):
                return {}

            def _save_image_export_preferences(self):
                pass

            def _refresh_image_export_action_states(self):
                pass

        harness = Harness()
        workspace = tmp_path / "session.json"
        state = harness._collect_workspace_image_export_state(workspace)
        assert state["gif_repeat_fps"] == [12.0, 240.0]
        panel.set_state(repeat_fps=[])
        harness._apply_workspace_image_export_state(state, workspace)
        assert panel.current_request().repeat_fps == [12.0, 240.0]
        del state["gif_repeat_fps"]
        harness._apply_workspace_image_export_state(state, workspace)
        assert panel.current_request().repeat_fps == []
        assert not signals
    finally:
        panel.close()


def test_repeat_pass_count_is_limited() -> None:
    panel = editor_gif_panel.GifExportPanel()
    try:
        limit = editor_gif_panel.GIF_REPEAT_PASS_LIMIT
        panel.set_state(repeat_fps=[10] * (limit + 3))
        assert len(panel.current_request().repeat_fps) == limit
        assert not panel.add_repeat_button.isEnabled()
    finally:
        panel.close()
