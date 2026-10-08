"""真实 Qt 徽章、配置预览与保存；测试不打开原生窗口。"""
from PyQt6.QtWidgets import QApplication, QTabWidget, QScrollArea
from PyQt6.QtGui import QColor
from app_common import superviewer_user_options as options
from app_common.bird_rarity import RARITY_DEFAULT_OPTIONS
from app_common.metadata_badges import IUCN_DEFAULT_OPTIONS
from SuperViewer.superviewer import rarity_badge as ui
from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog

_APP = QApplication.instance() or QApplication([])


def test_list_badge_paint_uses_configured_colors_and_unknown_has_no_badge(monkeypatch):
    from PyQt6.QtCore import QRect, Qt
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QStyleOptionViewItem
    from SuperViewer.superviewer.rarity_file_table import RarityFileTableModel, RarityBadgeDelegate

    monkeypatch.setattr(options, '_RUNTIME_OPTIONS', options.normalize_user_options({
        'rarity_badge_epic_text': '<史诗>', 'rarity_badge_epic_background': '#654321',
        'rarity_badge_epic_foreground': '#FEDCBA',
    }))
    model = RarityFileTableModel()
    paths = ['known.jpg', 'unknown.jpg', 'stale.jpg', 'zero.jpg']
    model.rebuild(paths, meta_cache={
        paths[0]: {'XMP-superpicky:gbif_rarity_100': '50'},
        paths[1]: {'gbif_rarity_100': 'NaN'},
        paths[2]: {'title': '新鸟名', 'birdid_rarity_source': '旧鸟名', 'gbif_rarity_100': 90},
        paths[3]: {'report.gbif_rarity_100': 0},
    }, tooltip_fn=lambda _: (_ for _ in ()).throw(AssertionError('unexpected file tooltip')), mismatch_fn=None)
    col = model.rarity_column
    assert [model.index(row, col).data() for row in range(4)] == ['<史诗>', '', '', '普通']
    delegate = RarityBadgeDelegate()
    for row in range(3):
        index = model.index(row, col)
        image = QImage(120, 36, QImage.Format.Format_ARGB32)
        image.fill(QColor('white'))
        painter = QPainter(image)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 120, 36)
        try:
            delegate.paint(painter, option, index)
        finally:
            painter.end()
        colors = {image.pixelColor(x, y).name() for x in range(120) for y in range(36)}
        assert ('#654321' in colors) == (row == 0)
        if row == 0:
            assert '#fedcba' in colors
            assert '50/100' in index.data(Qt.ItemDataRole.ToolTipRole)
        else:
            assert colors == {'#ffffff'}


def test_badge_zero_unknown_custom_plain_text_and_theme_refresh(monkeypatch):
    monkeypatch.setattr(options, '_RUNTIME_OPTIONS', dict(RARITY_DEFAULT_OPTIONS))
    badge = ui.RarityBadge()
    try:
        badge.set_score(None)
        assert badge.text() == '未知'
        badge.set_score(0)
        assert badge.text() == '普通' and '0/100' in badge.toolTip()
        options.apply_runtime_user_options({'rarity_badge_epic_text': '<史诗>',
            'rarity_badge_epic_background': '#654321', 'rarity_badge_epic_foreground': '#FEDCBA'})
        badge.set_score(50)
        assert badge.text() == '<史诗>'
        assert '#654321' in badge.styleSheet() and '#FEDCBA' in badge.styleSheet()
        assert badge.textFormat().name == 'PlainText'
        badge.refresh_style()
        assert badge.text() == '<史诗>'
    finally:
        badge.close()
        badge.deleteLater()
        _APP.processEvents()


def test_options_dialog_badge_edit_color_picker_save_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(options, '_get_app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(options, 'get_user_config_dir', lambda: str(tmp_path))
    monkeypatch.setattr(options, '_RUNTIME_OPTIONS', dict(options.normalize_user_options({})))
    dialog = SuperViewerUserOptionsDialog(options=options.normalize_user_options({}))
    try:
        form = dialog._rarity_badges_form
        form.edits['legendary'].setText('传说')
        monkeypatch.setattr(ui.QColorDialog, 'getColor', lambda *_: QColor('#123456'))
        form.colors[('legendary','background')].click()
        monkeypatch.setattr(ui.QColorDialog, 'getColor', lambda *_: QColor('#ABCDEF'))
        form.colors[('legendary','foreground')].click()
        assert form.previews['legendary'].text() == '传说'
        assert '#123456' in form.previews['legendary'].styleSheet()
        selected = dialog.selected_options()
        assert selected['rarity_badge_legendary_foreground'] == '#ABCDEF'
        options.save_user_options(selected)
        assert options.load_user_options()['rarity_badge_legendary_text'] == '传说'
        form.reset_defaults()
        assert form.selected_options() == RARITY_DEFAULT_OPTIONS
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()


def test_conservation_options_sidebar_persistence_and_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(options, '_get_app_dir', lambda: str(tmp_path))
    monkeypatch.setattr(options, 'get_user_config_dir', lambda: str(tmp_path))
    dialog = SuperViewerUserOptionsDialog(options=options.normalize_user_options({}))
    try:
        assert dialog.tabs.tabPosition() == QTabWidget.TabPosition.West
        assert dialog.tabs.tabBar().tabSizeHint(0).width() > dialog.tabs.tabBar().tabSizeHint(0).height()
        form = dialog._conservation_badges_form
        form.edits['en'].setText('重点保护')
        form.colors[('en','background')].set_color('#123456')
        form._refresh_previews()
        assert form.previews['en'].text() == '重点保护'
        assert '#123456' in form.previews['en'].styleSheet()
        options.save_user_options(dialog.selected_options())
        loaded = options.load_user_options()
        assert loaded['iucn_badge_en_text'] == '重点保护'
        assert loaded['iucn_badge_en_background'] == '#123456'
        assert loaded['rarity_badge_legendary_background'] == RARITY_DEFAULT_OPTIONS['rarity_badge_legendary_background']
        pages = [dialog.tabs.widget(i) for i in range(dialog.tabs.count())]
        scroll = next(page for page in pages if isinstance(page,QScrollArea) and page.widget() is form)
        assert scroll.widgetResizable()
        form.reset_defaults()
        assert form.selected_options() == IUCN_DEFAULT_OPTIONS
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()


def test_information_badges_paint_colors_and_keep_drafts_on_theme_and_config(tmp_path, monkeypatch):
    from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
    monkeypatch.setattr(options, '_RUNTIME_OPTIONS', options.normalize_user_options({
        'rarity_badge_epic_background':'#123456', 'iucn_badge_en_background':'#ABCDEF',
    }))
    reads = []
    panel = ImageInfoTabPanel_ImageInfo(lambda: [],lambda _path: [],lambda *_args: None,
        lambda path,_name:path,metadata_provider=lambda path: reads.append(path) or {})
    try:
        # Both rows are colored badges even before the first photo arrives.
        for key in ('稀有度','保护等级'):
            assert isinstance(panel.basic_rows[key],ui.MetadataBadge)
            assert 'background-color' in panel.basic_rows[key].styleSheet()
        panel._set_basic_info({'稀有度':'50','保护等级':'EN · 濒危'})
        panel.comment_edit.setPlainText('未保存的备注')
        panel.filename_edit.setText('未保存的文件名')
        panel.apply_theme()
        for key,expected in (('稀有度','#123456'),('保护等级','#abcdef')):
            label = panel.basic_rows[key]
            label.resize(label.sizeHint())
            rendered = label.grab().toImage()
            assert expected in {rendered.pixelColor(x,y).name()
                                for x in range(rendered.width()) for y in range(rendered.height())}
        options.apply_runtime_user_options({'iucn_badge_en_text':'保护物种','iucn_badge_en_background':'#654321'})
        panel.apply_theme()
        assert panel.basic_rows['保护等级'].text() == '保护物种'
        assert '#654321' in panel.basic_rows['保护等级'].styleSheet()
        assert panel.comment_edit.toPlainText() == '未保存的备注'
        assert panel.filename_edit.text() == '未保存的文件名'
        assert reads == []
    finally:
        panel.close()
        panel.deleteLater()
        _APP.processEvents()
