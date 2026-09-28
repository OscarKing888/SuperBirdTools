"""同时显示整组完整范围和共同裁切，不受成片已经裁切的影响。"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class SequenceBoundsOverview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.union_box = None
        self.intersection_box = None
        self.setFixedHeight(100)
        self.setAccessibleName('整组并集和交集范围示意图')
        self.setToolTip('橙色：包含整组全部画面的完整范围。\n青色：每张照片都有内容的最大无黑边矩形。\n示意图始终按完整范围显示，不受成片裁切影响。')
        self.hide()

    def set_bounds(self, union_box, intersection_box):
        self.union_box, self.intersection_box = union_box, intersection_box
        self.setVisible(union_box is not None)
        self.update()

    def paintEvent(self, event):
        if self.union_box is None:
            return
        left, top, right, bottom = self.union_box
        width, height = right-left, bottom-top
        if min(width, height) <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(max(1, self.width()-16)/width, max(1, self.height()-16)/height)
        x, y = (self.width()-width*scale)/2, (self.height()-height*scale)/2
        outer = QRectF(x, y, width*scale, height*scale)
        painter.setBrush(self.palette().base())
        painter.setPen(QPen(QColor('#F5A623'), 4))
        painter.drawRect(outer)
        if self.intersection_box is not None:
            l, t, r, b = self.intersection_box
            inner = QRectF(x+(l-left)*scale, y+(t-top)*scale, (r-l)*scale, (b-t)*scale)
            painter.setBrush(QColor(69, 214, 232, 45))
            painter.setPen(QPen(QColor('#45D6E8'), 2, Qt.PenStyle.DashLine))
            painter.drawRect(inner)
        painter.end()
