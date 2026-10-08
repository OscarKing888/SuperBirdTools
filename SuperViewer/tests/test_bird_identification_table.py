"""Photo-group priority, persistent candidate identity and adopt-action contrast."""
import pytest
from PyQt6.QtCore import QPersistentModelIndex, QRect
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from SuperViewer.superviewer.bird_identification import BirdIDResult
from SuperViewer.superviewer.bird_identification_table import AdoptDelegate, BirdIDResultsTable

_APP = QApplication.instance() or QApplication([])


def result(name, status):
    return BirdIDResult(name + '.jpg', status, response={'results': [
        {'cn_name': name + '低分', 'confidence': 20},
        {'cn_name': name + '高分', 'confidence': 80},
    ]}, saved_fingerprint=(1,), source_fingerprint=(1,),
        accepted_index=0 if status == 'success' else None)


def assert_groups(model, entries):
    assert model.entries == entries
    row = 0
    for entry in entries:
        assert entry.first_row == row
        assert model.rows[row:row+2] == [(entry, 1), (entry, 0)]
        row += 2


def test_pending_groups_first_keep_selection_and_candidate_identity_after_adoption():
    table = BirdIDResultsTable()
    model = table.results
    try:
        a = table.append_result(result('甲', 'success'))
        selected = QPersistentModelIndex(table.currentIndex())
        b = table.append_result(result('乙', 'candidate'))
        c = table.append_result(result('丙', 'success'))
        d = table.append_result(result('丁', 'candidate'))
        assert_groups(model, [b,d,a,c])
        assert selected.row() == a.first_row + 1
        assert table.currentIndex().row() == selected.row()
        assert model.rows[selected.row()] == (a,0)
        pending_candidate = QPersistentModelIndex(model.index(b.first_row, model.ACTION_COLUMN))
        b.result.status, b.result.accepted_index = 'success', 1
        model.refresh_entry(b)
        assert_groups(model, [d,a,b,c])
        assert pending_candidate.row() == b.first_row
        assert pending_candidate.data() == '已采纳'
        assert selected.row() == a.first_row + 1
        requested = []
        table.adopt_requested.connect(lambda entry,index: requested.append((entry,index)))
        table._request(d.first_row)
        assert requested == [(d,1)]
        # A retryable write error needs attention; a stale result is not actionable.
        a.error = '保存失败'
        model.refresh_entry(a)
        assert_groups(model, [a,d,b,c])
        a.error = ''
        model.refresh_entry(a)
        d.stale = True
        model.refresh_entry(d)
        assert_groups(model, [a,b,c,d])
        assert not model.can_adopt(d.first_row)
    finally:
        table.close()
        table.deleteLater()
        _APP.processEvents()


def test_incoming_confirmed_results_do_not_hide_leading_pending_groups():
    table = BirdIDResultsTable()
    table.resize(800,200)
    table.show()
    try:
        table.append_result(result('待确认', 'candidate'))
        for i in range(8):
            table.append_result(result(str(i), 'success'))
            _APP.processEvents()
        assert table.verticalScrollBar().maximum() > 0
        assert table.rowAt(0) == 0
        assert table.results.rows[table.rowAt(0)][0].result.status == 'candidate'
    finally:
        table.close()
        table.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize('dark', [False,True])
@pytest.mark.parametrize('selected', [False,True])
def test_green_adopt_button_survives_theme_selection_and_reverts_when_disabled(dark, selected):
    table = BirdIDResultsTable()
    try:
        entry = table.append_result(result('待确认', 'candidate'))
        index = table.results.index(entry.first_row, table.results.ACTION_COLUMN)
        option = QStyleOptionViewItem()
        option.rect = QRect(0,0,110,60)
        option.palette.setColor(QPalette.ColorRole.Base, QColor('#202020' if dark else '#FFFFFF'))
        option.palette.setColor(QPalette.ColorRole.Button, QColor('#333333' if dark else '#EEEEEE'))
        option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_HasFocus
        if selected:
            option.state |= QStyle.StateFlag.State_Selected
        delegate = AdoptDelegate()
        def colors():
            image = QImage(110,60,QImage.Format.Format_ARGB32)
            image.fill(QColor('transparent'))
            painter = QPainter(image)
            try:
                delegate.paint(painter,option,index)
            finally:
                painter.end()
            return {image.pixelColor(x,y).name() for x in range(110) for y in range(60)}
        assert '#15803d' in colors()
        option.state |= QStyle.StateFlag.State_MouseOver
        assert '#166534' in colors()
        table.results.set_pending(True)
        assert not {'#15803d','#166534'} & colors()
        table.results.set_pending(False)
        entry.result.accepted_index = 1
        assert not {'#15803d','#166534'} & colors()
    finally:
        table.close()
        table.deleteLater()
        _APP.processEvents()
