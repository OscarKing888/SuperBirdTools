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

        for width in (220, 520, 220):
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
