"""逐只汇总按照片分组、独立鸟编号与真实离屏布局验证。"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from SuperViewer.superviewer.bird_identification import BirdIDResult
from SuperViewer.superviewer.bird_identification_controller import BirdIDProgressDialog
from SuperViewer.superviewer.per_bird_results_table import PerBirdResultsModel

_APP = QApplication.instance() or QApplication([])


def birds_result(source='中文鸟片.jpg', status='partial'):
    birds = [dict(index=0, cn_name='白鹭', en_name='Egret', confidence=25, status='candidate',
                  detection_confidence=.8, box_px=[0, 0, 80, 100], gbif_rarity_100=0, iucn_category='LC'),
             dict(index=3, cn_name='苍鹭', confidence=95, status='confirmed', detection_confidence=.9,
                  box_px=[100, 0, 200, 100]),
             dict(index=4, status='failed', message='<识别失败>', box_px=[200, 0, 300, 100])]
    return BirdIDResult(source, status, '找到 3 只鸟，失败 1 只', response={'individuals': birds})


def test_individual_rows_keep_bird_identity_and_failure_details():
    model = PerBirdResultsModel()
    entry = model.append_result(birds_result())
    assert model.rowCount() == 3
    assert [model.index(i, 3).data() for i in range(3)] == ['#1', '#4', '#5']
    assert [model.index(i, 1).data() for i in range(3)] == ['待确定', '已确认', '失败']
    assert model.index(0, 9).data() == '0 / 100'
    assert model.index(0, 12).data() == '80.0%'
    assert model.index(0, 13).data() == '0, 0, 80, 100'
    assert model.index(2, 11).data() == '<识别失败>'
    assert all(not model.can_adopt(i) for i in range(3))
    assert entry.row_count == 3 and entry.candidate_indices == (0, 1, 2)
    for status in ('success', 'failed', 'cancelled', 'skipped'):
        model.append_result(BirdIDResult(status + '.jpg', status, '无鸟或未完成'))
    assert model.rowCount() == 7 and len(model.entries) == 5
    skipped = model.append_result(birds_result('已有.jpg', 'skipped'))
    assert '已有' in model.index(skipped.first_row, 1).data()
    unsaved = model.append_result(birds_result('取消.jpg', 'cancelled'))
    assert '未保存' in model.index(unsaved.first_row, 1).data()


@pytest.mark.parametrize('dark,large', [(False, False), (True, False), (False, True), (True, True)])
def test_small_dialog_scroll_keyboard_and_render(tmp_path, dark, large):
    dialog = BirdIDProgressDialog(None, results_table=True, per_bird=True)
    if dark:
        palette = QPalette(dialog.palette())
        for role, color in ((QPalette.ColorRole.Window, '#202020'), (QPalette.ColorRole.Base, '#282828'),
                            (QPalette.ColorRole.AlternateBase, '#383838'), (QPalette.ColorRole.Text, '#eeeeee'),
                            (QPalette.ColorRole.WindowText, '#eeeeee'), (QPalette.ColorRole.ButtonText, '#eeeeee'),
                            (QPalette.ColorRole.Button, '#383838')):
            palette.setColor(role, QColor(color))
        dialog.setPalette(palette)
    if large:
        font = QFont(dialog.font()); font.setPointSize(18); dialog.setFont(font)
    dialog.setWindowTitle('批量逐只识别 · 结果汇总')
    dialog.bar.setRange(0, 12); dialog.bar.setValue(10); dialog.bar.setFormat('%v / %m 张（%p%）')
    dialog.summary.setWordWrap(True)
    dialog.summary.setText('照片：已完成 8，待确定 0，部分失败 2，跳过 0，失败 0，取消 0\n'
                           '本批保存鸟体：已确认 10，待确定 10，已过滤 0，失败 10')
    table = dialog.details
    for i in range(10): table.append_result(birds_result(f'中文照片{i}.jpg'))
    dialog.resize(640, 420)
    dialog.show(); _APP.processEvents()
    try:
        assert table.isColumnHidden(2)
        assert table.horizontalScrollBar().maximum() > 0
        assert table.verticalScrollBar().maximum() > 0
        assert dialog.rect().contains(dialog.button.geometry())
        assert table.geometry().bottom() < dialog.button.geometry().top()
        table.setCurrentIndex(table.results.index(0, 4)); table.setFocus()
        QTest.keyClick(table, Qt.Key.Key_Down)
        assert table.currentIndex().row() == 1
        assert dialog.summary.geometry().bottom() < table.geometry().top()
        assert table.viewport().height() >= 80
        table.scrollToTop(); table.horizontalScrollBar().setValue(0)
        assert dialog.grab().save(str(tmp_path / f'per-bird-left-{dark}-{large}.png'))
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        _APP.processEvents()
        assert table.columnViewportPosition(13) < table.viewport().width()
        stops = []
        dialog.cancel_requested.connect(lambda: stops.append(True))
        dialog.button.setFocus(); QTest.keyClick(dialog.button, Qt.Key.Key_Space)
        assert stops == [True] and not dialog.button.isEnabled()
        dialog.finish('识鸟已停止，已保存结果保留。')
        assert dialog.button.isEnabled() and table.results.rowCount() == 30
        assert dialog.grab().save(str(tmp_path / f'per-bird-{dark}-{large}.png'))
    finally:
        dialog.close()
