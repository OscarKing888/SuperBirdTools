"""只读候选草稿预览；显式采用后才进入现有人工选区编辑流程。"""
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox, QPushButton, QSizePolicy
from PIL import Image
from PIL.ImageQt import ImageQt

from birdstamp.image_dejitter.bird_parts.pose import PART_LABELS


class RegionCandidatePreview(QWidget):
    adopt = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
        self._base = None
        self._bounds = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(QLabel('部位候选（草稿，不是分析结果）'))
        self.choice = QComboBox()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel();self.detail.setWordWrap(True)
        self.detail.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Minimum)
        self.apply = QPushButton('采用此候选并手动修正')
        for control in (self.choice,self.image,self.detail,self.apply):layout.addWidget(control)
        self.choice.currentIndexChanged.connect(self._render)
        self.apply.clicked.connect(self._adopt)
        self.hide()

    def clear(self):
        if self.result is None:return
        self.result = None
        self._base = None
        self.image.clear()
        self.hide()
        from .editor_collapsible import refresh_layout_chain
        refresh_layout_chain(self)

    def set_result(self, result, image):
        if not result.candidates or image is None or result.target is None:
            self.clear();return
        self.result = result
        l,t,r,b = result.target
        dx,dy = (r-l)*.25,(b-t)*.25
        self._bounds = (max(0,l-dx),max(0,t-dy),min(1,r+dx),min(1,b+dy))
        x,y,z,w = self._bounds
        width,height = (z-x)*image.width,(w-y)*image.height
        scale = min(300/width,170/height)
        size = (max(1,round(width*scale)),max(1,round(height*scale)))
        with image.resize(size,Image.Resampling.LANCZOS,box=(x*image.width,y*image.height,z*image.width,w*image.height)).convert('RGB') as crop:
            # 保留独立 QImage 原底图；部分 macOS Qt 后端的 QPixmap.copy 仍共享绘制存储。
            self._base = ImageQt(crop).copy()
        index = self.choice.currentIndex()
        self.choice.blockSignals(True);self.choice.clear()
        for c in result.candidates:
            label = ('待预检' if c.status=='pending' else
                     f'{c.passed_frames}/{c.total_frames} 通过 · '+('可推荐' if c.status=='passed' else '需修正'))
            variant = f' · {c.variant}' if c.variant else ''
            self.choice.addItem(f'{PART_LABELS.get(c.part,c.part)}{variant} · {label}')
        self.choice.setCurrentIndex(max(0,min(index,self.choice.count()-1)))
        self.choice.blockSignals(False)
        self._render();self.show()
        from .editor_collapsible import refresh_layout_chain
        refresh_layout_chain(self)

    def _render(self):
        if self.result is None or self._base is None:return
        candidate = self.result.candidates[self.choice.currentIndex()]
        pixmap = self._base.copy()
        painter = QPainter(pixmap)
        pen = QPen(QColor('#25a55f' if candidate.status=='passed' else '#e4a62b'),2)
        if candidate.status!='passed':pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        l,t,r,b = self._bounds
        for x,y,z,w in candidate.regions:
            painter.drawRect(QRectF((x-l)/(r-l)*pixmap.width(),(y-t)/(b-t)*pixmap.height(),
                                   (z-x)/(r-l)*pixmap.width(),(w-y)/(b-t)*pixmap.height()))
        painter.end();self.image.setPixmap(QPixmap.fromImage(pixmap))
        self.image.setFixedHeight(pixmap.height())
        reasons = [f"{d['file']}：{d['reason']}" for d in candidate.diagnostics if d.get('reason') and not d.get('passed')]
        self.detail.setText('\n'.join(reasons[:2]) or ('正在核验；暂不加入正式选区。' if candidate.status=='pending' else '抽样通过；完整分析仍须逐张核验。'))
        self.detail.setToolTip('\n'.join(reasons))
        self.layout().invalidate()
        self.setMinimumHeight(self.layout().sizeHint().height())

    def _adopt(self):
        if self.result is not None:
            self.adopt.emit(self.result.candidates[self.choice.currentIndex()])
