"""真实 Qt 焦点下预览转发到 Viewer 列表，原生选择/快捷键与长按状态只有一个所有者。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLineEdit

from SuperViewer.superviewer.preview_panel import PreviewPanel
from SuperViewer.superviewer.preview_key_router import PreviewKeyRouter
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel

_APP = QApplication.instance() or QApplication([])


class Browser(SuperViewerTaggedFileListPanel):
    def __init__(self, tmp_path):
        self.full, self.fast, self.actions = [], [], []
        super().__init__(tag_config_path=tmp_path/'tags.cfg')

    def _emit_file_selected_for_path(self, path):
        self.full.append(path)

    def _emit_fast_preview_for_path(self, path):
        self.fast.append(path)

    def _copy_current_selection_to_clipboard(self):
        self.actions.append('copy')

    def _cut_current_selection_to_clipboard(self):
        self.actions.append('cut')

    def _paste_clipboard_to_current_dir(self):
        self.actions.append('paste')


@pytest.fixture(params=['tree', 'thumb'])
def ui(request, tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'cache'))
    host = QWidget()
    layout = QHBoxLayout(host)
    browser, preview, edit = Browser(tmp_path), PreviewPanel(), QLineEdit()
    for widget in (browser, preview, edit):
        layout.addWidget(widget)
    paths = [str(tmp_path/f'photo-{i:02}.jpg') for i in range(32)]
    mode = browser._MODE_THUMB if request.param == 'thumb' else browser._MODE_LIST
    browser._set_view_mode(mode)
    model = browser._thumb_list_model if request.param == 'thumb' else browser._file_table_model
    model.rebuild(paths, meta_cache={}, tooltip_fn=lambda p: '', mismatch_fn=lambda p: False)
    view = browser._list_widget if request.param == 'thumb' else browser._tree_widget
    view.setCurrentIndex(view.model().index(0, 0))
    pix = QPixmap(200, 140); pix.fill(Qt.GlobalColor.blue)
    preview._canvas.set_source_pixmap(pix)
    router = PreviewKeyRouter(preview._canvas, browser, host)
    host.resize(1500, 600); host.show(); host.activateWindow(); _APP.processEvents()
    preview._canvas.setFocus()
    browser.full.clear(); browser.fast.clear()
    yield host, browser, preview._canvas, edit, view, paths
    browser.request_shutdown()
    browser.shutdown()
    browser.close()
    preview.shutdown()
    host.close(); host.deleteLater(); _APP.processEvents()


def send(widget, kind, key, repeat=False, modifiers=Qt.KeyboardModifier.NoModifier, text=''):
    _APP.sendEvent(widget, QKeyEvent(kind, key, modifiers, text, repeat))


def test_canvas_click_focus_and_native_navigation_matches_file_view(ui):
    _, browser, canvas, edit, view, paths = ui
    edit.setFocus()
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton)
    assert _APP.focusWidget() is canvas
    for code in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Left, Qt.Key.Key_Right,
                 Qt.Key.Key_End, Qt.Key.Key_Home, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
        view.setCurrentIndex(view.model().index(2, 0))
        QTest.keyClick(view, code)
        expected = (view.currentIndex().row(), view.currentIndex().column())
        view.setCurrentIndex(view.model().index(2, 0))
        QTest.keyClick(canvas, code)
        assert (view.currentIndex().row(), view.currentIndex().column()) == expected
        assert _APP.focusWidget() is canvas


@pytest.mark.parametrize('fps', [8, 24, 60])
def test_repeat_release_and_timer_are_owned_by_file_list(ui, fps):
    _, browser, canvas, _, view, paths = ui
    browser._set_key_navigation_fps(fps, persist=False)
    # Down 在列表和缩略图两种布局都使用实际文件视图自己的步长。
    send(canvas, QEvent.Type.KeyPress, Qt.Key.Key_Down)
    assert browser.full and not browser.fast
    full_count = len(browser.full)
    send(canvas, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    assert browser._key_navigation_playback_active and browser.fast
    timer = browser._key_navigation_playback_timer
    assert timer.interval() == round(1000/fps)
    timer.stop()
    fast_count = len(browser.fast)
    send(canvas, QEvent.Type.KeyRelease, Qt.Key.Key_Down, True)
    send(canvas, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    assert len(browser.full) == full_count and len(browser.fast) == fast_count
    browser._on_key_navigation_playback_tick()
    assert len(browser.fast) == fast_count+1
    send(canvas, QEvent.Type.KeyRelease, Qt.Key.Key_Down)
    assert not browser._key_navigation_playback_active
    assert len(browser.full) == full_count+1 and browser.full[-1] == browser.fast[-1]
    send(canvas, QEvent.Type.KeyRelease, Qt.Key.Key_Down)
    assert len(browser.full) == full_count+1 and _APP.focusWidget() is canvas


def test_rating_delete_and_clipboard_reuse_list_actions(ui, monkeypatch):
    _, browser, canvas, _, view, paths = ui
    calls = []
    monkeypatch.setattr(browser, '_rating_writes_allowed', lambda *a, **kw: True)
    monkeypatch.setattr(browser, '_toggle_rating_for_paths', lambda p, n: calls.append(('rating', list(p), n)))
    monkeypatch.setattr(browser, '_file_operation_paths_allowed', lambda *a, **kw: True)
    monkeypatch.setattr(browser, '_move_paths_to_trash', lambda p: calls.append(('delete', list(p))))
    monkeypatch.setattr(browser, '_toggle_reject_for_paths', lambda p: calls.append(('reject', list(p))))
    monkeypatch.setattr(browser, '_toggle_pick_for_paths', lambda p: calls.append(('pick', list(p))))
    QTest.keyClick(canvas, Qt.Key.Key_3)
    QTest.keyClick(canvas, Qt.Key.Key_Delete)
    assert calls == [('rating', [paths[0]], 3), ('delete', [paths[0]])]
    QTest.keyClick(canvas, Qt.Key.Key_Q)
    QTest.keyClick(canvas, Qt.Key.Key_QuoteLeft)
    assert calls[2:] == [('reject', [paths[0]]), ('pick', [paths[0]])]
    for shortcut in browser._file_action_shortcuts[:3]:
        combination = shortcut.key()[0]
        QTest.keyClick(canvas, combination.key(), combination.keyboardModifiers())
    assert browser.actions == ['copy', 'cut', 'paste']
    assert _APP.focusWidget() is canvas


def test_shift_selection_and_input_focus_remain_native(ui):
    _, browser, canvas, edit, view, _ = ui
    QTest.keyClick(canvas, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert len(browser._paths_for_active_shortcut_action()) >= 2
    send(canvas, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    assert browser._key_navigation_playback_active
    count = len(browser.full)
    edit.setFocus()
    assert not browser._key_navigation_playback_active and len(browser.full) == count
    edit.setText('before')
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key.Key_3)
    assert edit.text() == '3' and browser.actions == []
    # 焦点离开后迟来的重复松键不能升级清晰图。
    send(edit, QEvent.Type.KeyRelease, Qt.Key.Key_Down, True)
    assert len(browser.full) == count


@pytest.mark.parametrize('event_type', [QEvent.Type.WindowDeactivate, QEvent.Type.Hide])
def test_canvas_lifecycle_stops_owned_timer(ui, event_type):
    _, browser, canvas, _, _, _ = ui
    send(canvas, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    _APP.sendEvent(canvas, QEvent(event_type))
    assert not browser._key_navigation_playback_active
    assert not browser._key_navigation_playback_timer.isActive()
