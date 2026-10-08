"""两款应用使用同一可缩放外壳，所有真实设置页均靠左上且可以滚动。"""
import pytest
from PyQt6.QtCore import QPoint, QSize
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QStyleFactory, QWidget

from app_common.settings_dialog import SettingsDialog, SettingsPage
from app_common import superviewer_user_options
from birdstamp import config
from birdstamp.overlays import safe_area_options
from birdstamp.gui.user_options_dialog import UserOptionsDialog
from birdstamp.gui.editor import BirdStampEditorWindow
from SuperViewer.superviewer import bird_archive_ui
from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
from SuperViewer.superviewer.ui_theme import build_palette

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('style', QStyleFactory.keys())
@pytest.mark.parametrize('scheme', ['light', 'dark'])
@pytest.mark.parametrize('dialog_class', [SuperViewerUserOptionsDialog, UserOptionsDialog])
def test_real_options_pages_resize_and_cancel_without_writes(tmp_path, monkeypatch, style, scheme, dialog_class):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path/'stamp')
    monkeypatch.setattr(superviewer_user_options, '_get_app_dir', lambda: str(tmp_path/'viewer'))
    monkeypatch.setattr(superviewer_user_options, 'get_user_config_dir', lambda: str(tmp_path/'viewer'))
    archive_path = tmp_path/'archive.json'
    monkeypatch.setattr(bird_archive_ui, 'settings_path', lambda: archive_path)
    previous_style = _APP.style().objectName()
    previous_palette = _APP.palette()
    _APP.setStyle(style)
    _APP.setPalette(build_palette(scheme))
    host = QWidget()
    if dialog_class is UserOptionsDialog:
        # 使用真实编辑器样式，但不构造会恢复工作区的主窗口。
        BirdStampEditorWindow._apply_system_adaptive_style(host)
    dialog = dialog_class(host)
    try:
        assert isinstance(dialog, SettingsDialog)
        assert dialog.isSizeGripEnabled()
        expected = (['浏览与性能', '批量降噪', '珍禽入册', '稀有度徽章', '保护等级徽章', '鸟清晰度']
                    if dialog_class is SuperViewerUserOptionsDialog else ['安全区'])
        assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == expected
        dialog.show()
        for size in (QSize(640, 420), QSize(1200, 900)):
            dialog.resize(size)
            for i in range(dialog.tabs.count()):
                dialog.tabs.setCurrentIndex(i)
                _APP.processEvents()
                assert dialog.size() == size
                assert dialog.tabs.tabBar().y() <= 4
                assert not dialog.tabs.tabIcon(i).isNull()
                page = dialog.tabs.widget(i)
                assert isinstance(page, SettingsPage)
                page.horizontalScrollBar().setValue(0)
                page.verticalScrollBar().setValue(0)
                assert page.widget().pos() == QPoint(0, 0)
                assert dialog.buttons.geometry().top() > dialog.tabs.geometry().bottom()
                assert dialog.buttons.geometry().bottom() < dialog.height()
        if dialog_class is SuperViewerUserOptionsDialog:
            dialog._spin_thumb_loader_workers.setValue(3)
            assert dialog.selected_options()['thumbnail_loader_workers'] == 3
        else:
            dialog.name_edit.setText('尚未保存')
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        assert not archive_path.exists()
        assert not safe_area_options.options_path().exists()
        assert not (tmp_path/'viewer'/'SuperViewerUser.cfg').exists()
    finally:
        dialog.close()
        dialog.deleteLater()
        host.deleteLater()
        _APP.processEvents()
        safe_area_options._read_options.cache_clear()
        _APP.setStyle(previous_style)
        _APP.setPalette(previous_palette)
