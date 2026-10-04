"""去抖动预览区浮动状态面板：步骤、下一步提示、进度、图例和范围示意，不占参数面板高度。"""
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QToolButton, QVBoxLayout

from .color_key_rows import ColorKeyRows
from .sequence_bounds_overview import SequenceBoundsOverview


STEPS = ('方式', '选区', '分析', '导出')
_MARGIN = 8
_MAX_WIDTH = 360

_STYLE = """
#DejitterHud { background: rgba(18, 18, 20, 215); border: 1px solid rgba(255, 255, 255, 40); border-radius: 8px; }
#DejitterHud QLabel { color: #EDEDED; background: transparent; }
#DejitterHud QLabel#DejitterHudHint { color: #A8A8A8; }
#DejitterHud QToolButton { color: #EDEDED; background: transparent; border: none; padding: 0 4px; }
#DejitterHud QToolButton:hover { background: rgba(255, 255, 255, 30); border-radius: 4px; }
#DejitterHud QProgressBar { background: rgba(255, 255, 255, 30); border: none; border-radius: 2px; }
#DejitterHud QProgressBar::chunk { background: #2F80ED; border-radius: 2px; }
"""


class DejitterHud(QFrame):
    """浮在预览画布左上角；画布尺寸变化时自动贴边，可折叠成一行。"""

    def __init__(self, canvas=None):
        super().__init__(canvas)
        self.setObjectName('DejitterHud')
        self.setStyleSheet(_STYLE)
        self.setMaximumWidth(_MAX_WIDTH)
        self.setAccessibleName('去抖动状态')
        self._step = 0
        self._collapsed = False
        self._view = 'edit'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 8)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(6)
        self.steps = QLabel()
        self.steps.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.steps, 1)
        self.count = QLabel()
        header.addWidget(self.count)
        self.toggle = QToolButton()
        self.toggle.setAccessibleName('折叠去抖动状态')
        self.toggle.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        header.addWidget(self.toggle)
        layout.addLayout(header)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.hint = QLabel()
        self.hint.setObjectName('DejitterHudHint')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.tracking_key = ColorKeyRows((
            ('#FFB703', False, '跟踪成功'),
            ('#FF5252', True, '未匹配（预计位置）'),
        ))
        layout.addWidget(self.tracking_key)
        self.intersection_status = ColorKeyRows((
            ('#F5A623', False, '整组完整范围（并集）：待分析'),
            ('#45D6E8', True, '共同无黑边范围（交集）：待分析'),
        ))
        layout.addWidget(self.intersection_status)
        self.bounds_overview = SequenceBoundsOverview()
        self.bounds_overview.setFixedHeight(80)
        layout.addWidget(self.bounds_overview)
        self._edit_hint = ''
        self._result_hint = ''
        if canvas is not None:
            canvas.installEventFilter(self)
        self._render_steps()
        self._apply_visibility()
        self.hide()

    def attach(self, canvas):
        """去抖动页先于预览区构建；画布就绪后再挂上去。"""
        visible = self.isVisibleTo(self.parentWidget()) if self.parentWidget() else False
        self.setParent(canvas)
        canvas.installEventFilter(self)
        self.setVisible(visible)
        self._fit()

    # -- 内容 ---------------------------------------------------------------
    def set_step(self, step):
        step = max(0, min(len(STEPS) - 1, int(step)))
        if step != self._step:
            self._step = step
            self._render_steps()

    def _render_steps(self):
        parts = []
        for index, name in enumerate(STEPS):
            text = f'{"①②③④"[index]} {name}'
            if index == self._step:
                parts.append(f'<b style="color:#5AA9FF">{text}</b>')
            elif index < self._step:
                parts.append(f'<span style="color:#9BD49B">{text}</span>')
            else:
                parts.append(f'<span style="color:#8A8A8A">{text}</span>')
        self.steps.setText('&nbsp;&nbsp;'.join(parts))

    def set_message(self, text, tooltip=''):
        self.message.setText(text or '')
        self.message.setToolTip(tooltip or '')
        self._apply_visibility()

    def set_hints(self, edit_hint, result_hint=''):
        """编辑构图与成片预览各自的提示；只显示当前视图那一条。"""
        self._edit_hint, self._result_hint = edit_hint or '', result_hint or ''
        self._apply_visibility()

    def set_progress(self, current=None, total=None, *, busy=False):
        if total:
            self.progress.setRange(0, int(total))
            self.progress.setValue(int(current or 0))
            self.count.setText(f'{int(current or 0)}/{int(total)}')
            self.progress.show()
        elif busy:
            self.progress.setRange(0, 0)
            self.count.setText('')
            self.progress.show()
        else:
            self.count.setText('')
            self.progress.hide()
        self._fit()

    def set_view(self, view):
        self._view = 'result' if view == 'result' else 'edit'
        self._apply_visibility()

    def set_bounds(self, union_box, intersection_box):
        self.bounds_overview.set_bounds(union_box, intersection_box)
        self._apply_visibility()

    # -- 折叠与布局 -----------------------------------------------------------
    def is_collapsed(self):
        return self._collapsed

    def set_collapsed(self, collapsed):
        self._collapsed = bool(collapsed)
        self._apply_visibility()

    def _apply_visibility(self):
        expanded = not self._collapsed
        result = self._view == 'result'
        self.toggle.setText('＋' if self._collapsed else '－')
        self.toggle.setToolTip('展开状态面板' if self._collapsed else '折叠状态面板')
        hint = self._result_hint if result else self._edit_hint
        self.hint.setText(hint)
        self.message.setVisible(expanded and bool(self.message.text()))
        self.hint.setVisible(expanded and bool(hint))
        self.tracking_key.setVisible(expanded and result)
        self.intersection_status.setVisible(expanded and result)
        self.bounds_overview.setVisible(expanded and result and self.bounds_overview.union_box is not None)
        self._fit()

    def _fit(self):
        layout = self.layout()
        layout.invalidate()
        layout.activate()
        parent = self.parentWidget()
        limit = _MAX_WIDTH if parent is None else max(160, min(_MAX_WIDTH, parent.width() - 2 * _MARGIN))
        if self._collapsed:
            # 折叠时只占步骤行宽度，尽量少遮挡画面。
            limit = min(limit, layout.sizeHint().width())
        self.setFixedWidth(limit)
        self.setFixedHeight(layout.heightForWidth(limit) if layout.hasHeightForWidth()
                            else layout.sizeHint().height())
        self.move(_MARGIN, _MARGIN)
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._fit()
        return False
