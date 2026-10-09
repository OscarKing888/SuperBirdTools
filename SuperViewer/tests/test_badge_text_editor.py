"""通过真实鼠标/键盘编辑徽章草稿，走主窗口保存入口并重新打开。"""
import importlib
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont, QInputMethodEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QInputDialog, QLineEdit

from app_common import superviewer_user_options as options
from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
from SuperViewer.superviewer.rarity_badge import RarityBadge, ConservationBadge
from SuperViewer.superviewer.ui_theme import build_palette

_APP = QApplication.instance() or QApplication([])


def click(widget):
    point = widget.mapToGlobal(widget.rect().center())
    assert _APP.widgetAt(point) is widget
    window = widget.window()
    window.activateWindow()
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton,
                     pos=window.mapFromGlobal(point))
    _APP.processEvents()


def edit_text(form, level, text, *, accept=True):
    _APP.processEvents()
    errors = []

    def enter_text():
        popup = _APP.activeModalWidget()
        try:
            assert isinstance(popup, QInputDialog)
            _APP.processEvents()
            editor = popup.findChild(QLineEdit)
            click(editor)
            assert _APP.focusWidget() is editor
            QTest.keyClick(popup.windowHandle(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            if not text:
                QTest.keyClick(editor, Qt.Key.Key_Backspace)
            elif text.isascii():
                QTest.keyClicks(editor, text)
            else:
                event = QInputMethodEvent()
                event.setCommitString(text)
                _APP.sendEvent(editor, event)
            assert editor.text() == text[:32]
            buttons = popup.findChild(QDialogButtonBox)
            role = QDialogButtonBox.StandardButton.Ok if accept else QDialogButtonBox.StandardButton.Cancel
            click(buttons.button(role))
        except BaseException as exc:
            errors.append(exc)
            if popup is not None:
                popup.reject()

    QTimer.singleShot(0, enter_text)
    click(form.edit_buttons[level])
    if errors:
        raise errors[0]


@pytest.fixture
def isolated_options(tmp_path, monkeypatch):
    from SuperViewer.superviewer import bird_archive_ui
    monkeypatch.setattr(options, '_get_app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(options, 'get_user_config_dir', lambda: str(tmp_path))
    monkeypatch.setattr(options, '_RUNTIME_OPTIONS', options.normalize_user_options({}))
    monkeypatch.setattr(bird_archive_ui, 'settings_path', lambda: tmp_path / 'archive.json')
    return tmp_path / options.USER_OPTIONS_FILENAME


def test_edit_confirm_save_refresh_and_reopen(isolated_options, monkeypatch):
    main = importlib.import_module('SuperViewer.main')
    dialog = SuperViewerUserOptionsDialog(options=options.normalize_user_options({}))
    errors, refreshed = [], []

    def interact():
        try:
            for index, form, level, text in (
                (3, dialog._rarity_badges_form, 'legendary', 'SSR'),
                (4, dialog._conservation_badges_form, 'en', '重点保护'),
            ):
                dialog.tabs.setCurrentIndex(index)
                edit_text(form, level, text)
                assert form.previews[level].text() == text
            assert not isolated_options.exists()
            click(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok))
        except BaseException as exc:
            errors.append(exc)
            dialog.reject()

    monkeypatch.setattr(main, 'SuperViewerUserOptionsDialog', lambda *a, **k: dialog)
    monkeypatch.setattr(main.QMessageBox, 'information', lambda *a: None)
    viewport = SimpleNamespace(set_keep_view_on_switch=lambda value: None)
    host = SimpleNamespace(
        image_info_panel=SimpleNamespace(refresh_metadata_fields=lambda: refreshed.append('info')),
        _sync_perf_probe_action=lambda: None,
        _file_list=SimpleNamespace(apply_user_options=lambda: refreshed.append('files')),
        preview_panel=viewport, preview_a=viewport,
    )
    try:
        QTimer.singleShot(0, interact)
        main.MainWindow._open_user_options_dialog(host)
        if errors:
            raise errors[0]
        assert refreshed == ['info', 'files']
        saved = options.load_user_options()
        assert saved['rarity_badge_legendary_text'] == 'SSR'
        assert saved['iucn_badge_en_text'] == '重点保护'
        rarity, conservation = RarityBadge(), ConservationBadge()
        rarity.set_score(80)
        conservation.set_category('EN')
        assert rarity.text() == 'SSR'
        assert conservation.text() == '重点保护'
        reopened = SuperViewerUserOptionsDialog(options=saved)
        assert reopened._rarity_badges_form.edits['legendary'].text() == 'SSR'
        assert reopened._conservation_badges_form.edits['en'].text() == '重点保护'
        reopened.deleteLater()
        rarity.deleteLater()
        conservation.deleteLater()
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize('scheme', ['light', 'dark'])
@pytest.mark.parametrize('font_size', [10, 16])
def test_editor_cancel_keyboard_collapse_and_small_window(isolated_options, scheme, font_size):
    palette, font = _APP.palette(), _APP.font()
    _APP.setPalette(build_palette(scheme))
    larger = QFont(font)
    larger.setPointSize(font_size)
    _APP.setFont(larger)
    dialog = SuperViewerUserOptionsDialog(options=options.normalize_user_options({}))
    try:
        dialog.resize(640, 420)
        dialog.tabs.setCurrentIndex(3)
        dialog.show()
        _APP.processEvents()
        form = dialog._rarity_badges_form
        page = dialog.tabs.currentWidget()
        page.ensureWidgetVisible(form.edit_buttons['legendary'])
        edit_text(form, 'legendary', 'SSR')
        edit_text(form, 'legendary', 'discard', accept=False)
        assert form.edits['legendary'].text() == 'SSR'
        for _ in range(3):
            group = dialog.option_groups['稀有度徽章']
            group.set_expanded(False)
            group.set_expanded(True)
        page.ensureWidgetVisible(form.edits['legendary'])
        _APP.processEvents()
        click(form.edits['legendary'])
        assert _APP.focusWidget() is form.edits['legendary']
        QTest.keyClick(dialog.windowHandle(), Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(_APP.focusWidget(), 'UR')
        assert form.previews['legendary'].text() == 'UR'
        # Enter in the inline editor must save settings, never accidentally open an edit button.
        assert not form.edit_buttons['legendary'].autoDefault()
        assert dialog.rect().contains(dialog.buttons.geometry())
        click(dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel))
        assert not isolated_options.exists()
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()
        _APP.setPalette(palette)
        _APP.setFont(font)


def test_editor_length_limit_and_empty_restore(isolated_options):
    dialog = SuperViewerUserOptionsDialog(options=options.normalize_user_options({}))
    try:
        dialog.tabs.setCurrentIndex(3)
        dialog.show()
        _APP.processEvents()
        form = dialog._rarity_badges_form
        edit_text(form, 'legendary', 'S' * 40)
        assert form.selected_options()['rarity_badge_legendary_text'] == 'S' * 32
        edit_text(form, 'legendary', '')
        assert form.previews['legendary'].text() == '传奇'
        assert not isolated_options.exists()
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()
