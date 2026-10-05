# -*- coding: utf-8 -*-
"""Model preview panels: one model's raw output on the trace window's photo.

Opened from the 「预览」 buttons next to the model lists in the trace window's 参数
tab, docked side by side at the window's right edge (each can float as its own
window). A detector panel runs the YOLO model with its own input size, confidence
floor, classes and input region; a SAM panel segments what the user points at
(drawn box, keep/exclude points, or the trace's detected birds). No sharpness is
measured here: the panels show only what the model outputs.
"""
from __future__ import annotations

import threading
from typing import Callable, List, Optional

from .bird_sharpness_trace_view import TraceBirdList, TraceImageView
from .qt_compat import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    pyqtSignal,
)
from app_common.toggle_button import ToggleToolButton

try:
    from PyQt6.QtCore import QObject, QPointF, QRectF, Qt
    from PyQt6.QtGui import QBrush, QColor, QPen
    from PyQt6.QtWidgets import QButtonGroup, QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsView
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QObject, QPointF, QRectF, Qt
    from PyQt5.QtGui import QBrush, QColor, QPen
    from PyQt5.QtWidgets import QButtonGroup, QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsView

_BUTTON = getattr(Qt, "MouseButton", Qt)
_LEFT, _RIGHT = _BUTTON.LeftButton, _BUTTON.RightButton
_DASH = getattr(getattr(Qt, "PenStyle", Qt), "DashLine")
_NO_DRAG = getattr(getattr(QGraphicsView, "DragMode", QGraphicsView), "NoDrag")
_SCROLL_DRAG = getattr(getattr(QGraphicsView, "DragMode", QGraphicsView), "ScrollHandDrag")
_CROSS = getattr(getattr(Qt, "CursorShape", Qt), "CrossCursor")

DETECTOR, SAM = "detector", "sam"
PAN, BOX, POINTS = "pan", "box", "points"
C_PROMPT, C_KEEP, C_EXCLUDE = QColor(255, 214, 10), QColor(60, 220, 90), QColor(240, 70, 70)


def _pos(event) -> "QPointF":
    return event.position() if hasattr(event, "position") else QPointF(event.pos())


class PromptImageView(TraceImageView):
    """Trace image view that can also take prompts: drag a box, click keep/exclude points."""

    box_drawn = pyqtSignal(object)            # (x1, y1, x2, y2) in display (scene) coords
    point_added = pyqtSignal(float, float, bool)  # x, y (scene), keep

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.mode = PAN
        self._start: Optional[QPointF] = None
        self._rubber = QGraphicsRectItem()
        pen = QPen(C_PROMPT, 2, _DASH)
        pen.setCosmetic(True)
        self._rubber.setPen(pen)
        self._rubber.setZValue(5)
        self._rubber.setVisible(False)
        self.scene().addItem(self._rubber)
        self._prompt_items: list = []

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.setDragMode(_SCROLL_DRAG if mode == PAN else _NO_DRAG)
        if mode == PAN:
            self.viewport().unsetCursor()
        else:
            self.viewport().setCursor(_CROSS)

    def set_prompts(self, boxes, points) -> None:
        """Draw the prompts (display coords): boxes dashed, keep points green, exclude points red."""
        for item in self._prompt_items:
            self.scene().removeItem(item)
        self._prompt_items = []
        for x1, y1, x2, y2 in boxes:
            rect = QGraphicsRectItem(QRectF(x1, y1, x2 - x1, y2 - y1))
            pen = QPen(C_PROMPT, 2, _DASH)
            pen.setCosmetic(True)
            rect.setPen(pen)
            self._prompt_items.append(rect)
        radius = max(3.0, 6.0 / max(self.zoom_factor(), 1e-3))
        for x, y, keep in points:
            dot = QGraphicsEllipseItem(QRectF(x - radius, y - radius, 2 * radius, 2 * radius))
            pen = QPen(QColor(255, 255, 255), 1.5)
            pen.setCosmetic(True)
            dot.setPen(pen)
            dot.setBrush(QBrush(C_KEEP if keep else C_EXCLUDE))
            self._prompt_items.append(dot)
        for item in self._prompt_items:
            item.setZValue(4)
            self.scene().addItem(item)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.mode == BOX and event.button() == _LEFT:
            self._start = self.mapToScene(_pos(event).toPoint())
            self._rubber.setRect(QRectF(self._start, self._start))
            self._rubber.setVisible(True)
            return
        if self.mode == POINTS and event.button() in (_LEFT, _RIGHT):
            p = self.mapToScene(_pos(event).toPoint())
            self.point_added.emit(p.x(), p.y(), event.button() == _LEFT)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.mode == BOX and self._start is not None:
            self._rubber.setRect(QRectF(self._start, self.mapToScene(_pos(event).toPoint())).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.mode == BOX and self._start is not None and event.button() == _LEFT:
            rect = QRectF(self._start, self.mapToScene(_pos(event).toPoint())).normalized()
            self._start = None
            self._rubber.setVisible(False)
            if rect.width() >= 4 and rect.height() >= 4:
                self.box_drawn.emit((rect.left(), rect.top(), rect.right(), rect.bottom()))
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - right click is an exclude point
        if self.mode != POINTS:
            super().contextMenuEvent(event)

    def visible_scene_rect(self):
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        return rect.left(), rect.top(), rect.right(), rect.bottom()


class _Bridge(QObject):
    done = pyqtSignal(object, object)  # job name, result or Exception


class ModelPreviewPanel(QWidget):
    """One model's preview: parameters, run, the result drawn on the photo and listed."""

    def __init__(self, kind: str, model: str, image_provider: Callable, *,
                 boxes_provider: Optional[Callable[[], list]] = None, parent=None) -> None:
        super().__init__(parent)
        self.kind, self.model = kind, model
        self._image_provider = image_provider      # worker thread: -> AnalysisImage
        self._boxes_provider = boxes_provider or (lambda: [])  # GUI thread: detected birds, image px
        self._image = None
        self._display = None  # (rgb, scale)
        self._alive = True
        self._busy = False
        self._threads: List[threading.Thread] = []
        self.boxes: list = []   # SAM prompts, image px
        self.points: list = []  # (x, y, keep), image px
        self.result = None
        self._bridge = _Bridge()
        self._bridge.done.connect(self._on_done)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.view = PromptImageView(self)
        self.view.setMinimumSize(280, 220)
        if kind == SAM:
            tools = QHBoxLayout()
            self.tool_group = QButtonGroup(self)
            self.tool_buttons = {}
            for mode, label, tip in ((PAN, "平移", "拖动平移、滚轮缩放"), (BOX, "画框", "拖出一个框：框里的物体"),
                                     (POINTS, "点选", "左键：要的部分；右键：不要的部分")):
                button = ToggleToolButton(label, self)
                button.setToolTip(tip)
                button.clicked.connect(lambda _c=False, m=mode: self.view.set_mode(m))
                self.tool_group.addButton(button)
                self.tool_buttons[mode] = button
                tools.addWidget(button)
            self.tool_buttons[BOX].setChecked(True)
            self.view.set_mode(BOX)
            self.clear_btn = QPushButton("清除提示", self)
            self.clear_btn.clicked.connect(self.clear_prompts)
            self.use_boxes_btn = QPushButton("用检测框", self)
            self.use_boxes_btn.setToolTip("把计算过程中识别到的鸟框作为提示（每个框一个对象）")
            self.use_boxes_btn.clicked.connect(self.use_detected_boxes)
            tools.addStretch(1)
            tools.addWidget(self.clear_btn)
            tools.addWidget(self.use_boxes_btn)
            layout.addLayout(tools)
            self.view.box_drawn.connect(self._on_box)
            self.view.point_added.connect(self._on_point)
        layout.addWidget(self.view, 1)

        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        if kind == DETECTOR:
            self.scope = QComboBox(self)
            self.scope.addItem("全图", "full")
            self.scope.addItem("当前视图区域", "view")
            self.scope.setToolTip("当前视图区域：先在图上放大到鸟附近再运行，相当于手动做一次放大检测")
            self.imgsz = QSpinBox(self)
            self.imgsz.setRange(320, 2048)
            self.imgsz.setSingleStep(32)
            self.imgsz.setValue(640)
            self.imgsz.setSuffix(" px")
            self.min_conf = QSpinBox(self)
            self.min_conf.setRange(1, 95)
            self.min_conf.setValue(10)
            self.min_conf.setSuffix(" %")
            self.min_conf.setToolTip("低于它的结果不显示（10% = 0.10，可看到弱候选）")
            self.classes = QComboBox(self)
            self.classes.addItem("只看鸟", True)
            self.classes.addItem("全部类别", False)
            self.classes.setToolTip("全部类别：可看到被认成了什么（如 potted plant）")
            self.lift = QCheckBox("画面暗时先提亮", self)
            self.lift.setChecked(True)
            for row, (label, widget) in enumerate((("输入范围", self.scope), ("网络输入", self.imgsz),
                                                    ("置信度下限", self.min_conf), ("类别", self.classes))):
                grid.addWidget(QLabel(label, self), row, 0)
                grid.addWidget(widget, row, 1)
            grid.addWidget(self.lift, 4, 0, 1, 2)
        else:
            self.prompt_label = QLabel("", self)
            self.prompt_label.setWordWrap(True)
            grid.addWidget(self.prompt_label, 0, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        row = QHBoxLayout()
        self.run_btn = QPushButton("运行", self)
        self.run_btn.clicked.connect(self.run)
        self.status = QLabel("正在载入图像…", self)
        self.status.setWordWrap(True)
        row.addWidget(self.run_btn)
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        self.results = TraceBirdList(self)
        self.results.hovered.connect(lambda r: self.view.set_highlight(None if r is None else r.box))
        layout.addWidget(self.results)
        self._update_prompt_label()
        self._start("load", self._load)

    # ── background work ──
    def _start(self, name: str, work: Callable) -> None:
        self._busy = True
        self.run_btn.setEnabled(False)
        bridge = self._bridge

        def run():
            try:
                result = work()
            except Exception as exc:  # shown in the status line
                result = exc
            bridge.done.emit(name, result)

        thread = threading.Thread(target=run, name=f"model-preview-{self.kind}", daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def _load(self):
        from bird_sharpness.preview import display_image

        image = self._image_provider()
        return image, display_image(image)

    def _on_done(self, name: str, result) -> None:
        if not self._alive:
            return
        self._busy = False
        self.run_btn.setEnabled(True)
        if isinstance(result, Exception):
            self.status.setText(f"失败：{result}")
            return
        if name == "load":
            self._image, self._display = result
            self.view.set_image(self._display[0])
            self.view.fit()
            self._draw_prompts()
            if self.kind == DETECTOR:
                self.run()
            else:
                self.status.setText("画框或点选后点「运行」，或用检测框。")
            return
        from bird_sharpness.preview import render

        self.result = result
        img, rows = render(self._display[0], self._display[1], result.items)
        self.view.set_image(img)
        self._draw_prompts()
        self.results.set_rows(rows)
        lift = "" if result.gamma is None else f"，提亮 γ {result.gamma:.2f}"
        self.status.setText(f"{result.model} · {result.device or '—'} · {result.elapsed_s:.2f} s · "
                            f"{result.input_desc}{lift} · {len(result.items)} 个结果")

    def run(self) -> None:
        if self._busy or self._image is None:
            return
        from bird_sharpness import preview

        image = self._image
        if self.kind == DETECTOR:
            scale = self._display[1]
            region = None
            if self.scope.currentData() == "view":
                x1, y1, x2, y2 = self.view.visible_scene_rect()
                region = (max(0.0, x1 / scale), max(0.0, y1 / scale), x2 / scale, y2 / scale)
            params = preview.DetectorPreview(self.model, region, int(self.imgsz.value()),
                                             self.min_conf.value() / 100.0, bool(self.classes.currentData()),
                                             self.lift.isChecked())
            self.status.setText("正在运行…")
            self._start("run", lambda: preview.run_detector(image, params))
        else:
            params = preview.SamPreview(self.model, tuple(self.boxes), tuple(self.points))
            if not self.boxes and not self.points:
                self.status.setText("请先画框、点选，或用检测框。")
                return
            self.status.setText("正在运行…")
            self._start("run", lambda: preview.run_sam(image, params))

    # ── SAM prompts ──
    def _scale(self) -> float:
        return self._display[1] if self._display else 1.0

    def _on_box(self, box) -> None:
        s = self._scale()
        self.boxes = [tuple(v / s for v in box)]  # a drawn box is one object (replaces earlier boxes)
        self._draw_prompts()

    def _on_point(self, x: float, y: float, keep: bool) -> None:
        s = self._scale()
        self.points.append((x / s, y / s, keep))
        self._draw_prompts()

    def use_detected_boxes(self) -> None:
        boxes = [tuple(map(float, b)) for b in self._boxes_provider()]
        if not boxes:
            self.status.setText("当前计算过程中没有识别到鸟。")
            return
        self.boxes, self.points = boxes, []
        self._draw_prompts()
        self.run()

    def clear_prompts(self) -> None:
        self.boxes, self.points = [], []
        self._draw_prompts()

    def _draw_prompts(self) -> None:
        if self.kind != SAM:
            return
        s = self._scale()
        self.view.set_prompts([tuple(v * s for v in b) for b in self.boxes],
                              [(x * s, y * s, keep) for x, y, keep in self.points])
        self._update_prompt_label()

    def _update_prompt_label(self) -> None:
        if self.kind != SAM:
            return
        keep = sum(1 for p in self.points if p[2])
        self.prompt_label.setText(f"提示：{len(self.boxes)} 个框，{keep} 个保留点，{len(self.points) - keep} 个排除点。"
                                  "画框：拖动；点选：左键保留、右键排除；点选时最多配合一个框。")

    def shutdown(self) -> None:
        """The panel is closing: late results are dropped (threads finish on their own)."""
        self._alive = False
