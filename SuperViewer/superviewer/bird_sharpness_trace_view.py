# -*- coding: utf-8 -*-
"""Step-by-step viewer for one photo's bird sharpness computation.

Shows an :class:`bird_sharpness.trace.AnalysisTrace`: a zoomable main view, an
optional compare view (another step side by side, zoom/pan synchronised when both
steps share a coordinate frame), a side panel with the step's explanation,
numbers, charts and colour legend, and a step bar (slider, ◀ ▶ buttons, step
chips, ←/→ keys). Pure presentation: the trace is computed by a worker action.
"""
from __future__ import annotations

import math
import os
from typing import List, Optional

import numpy as np

from app_common.bird_sharpness_fields import VERDICT_STYLES
from app_common.toggle_button import ToggleToolButton

from .qt_compat import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget, pyqtSignal,
)

try:
    from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
    from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
    from PyQt6.QtWidgets import (QButtonGroup, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                                 QGridLayout, QSizePolicy, QSlider)
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
    from PyQt5.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
    from PyQt5.QtWidgets import (QButtonGroup, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                                 QGridLayout, QSizePolicy, QSlider)

_Qt = getattr(Qt, "AlignmentFlag", Qt)
_KEEP_ASPECT = getattr(getattr(Qt, "AspectRatioMode", Qt), "KeepAspectRatio")
_SMOOTH = getattr(getattr(Qt, "TransformationMode", Qt), "SmoothTransformation")
_HORIZONTAL = getattr(getattr(Qt, "Orientation", Qt), "Horizontal")
_NO_PEN = getattr(getattr(Qt, "PenStyle", Qt), "NoPen")
_DASH = getattr(getattr(Qt, "PenStyle", Qt), "DashLine")
_KEY = getattr(Qt, "Key", Qt)
_SCROLL_DRAG = getattr(getattr(QGraphicsView, "DragMode", QGraphicsView), "ScrollHandDrag")
_ANCHOR_MOUSE = getattr(getattr(QGraphicsView, "ViewportAnchor", QGraphicsView), "AnchorUnderMouse")
_ANTIALIAS = getattr(getattr(QPainter, "RenderHint", QPainter), "Antialiasing")
_SMOOTH_PIXMAP = getattr(getattr(QPainter, "RenderHint", QPainter), "SmoothPixmapTransform")
_FMT_RGB888 = getattr(getattr(QImage, "Format", QImage), "Format_RGB888")
_ROLE = getattr(QPalette, "ColorRole", QPalette)
_RICH = getattr(getattr(Qt, "TextFormat", Qt), "RichText")
_FRAME_NONE = getattr(getattr(QFrame, "Shape", QFrame), "NoFrame")

STEP_ICONS = {
    "decode": "解码", "detect": "识别", "bird": "鸟体", "head": "头部", "edges": "边缘",
    "distribution": "分布", "focus": "焦点", "tiles": "分块", "result": "结论",
}
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


def _clear_layout(layout) -> None:
    """Remove every item now; deleteLater alone leaves old widgets painted until the next loop."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def numpy_to_pixmap(image: np.ndarray) -> QPixmap:
    rgb = np.ascontiguousarray(image[..., :3] if image.ndim == 3 else np.repeat(image[..., None], 3, axis=2))
    h, w = rgb.shape[:2]
    qimage = QImage(rgb.data, w, h, 3 * w, _FMT_RGB888)
    return QPixmap.fromImage(qimage.copy())


class TraceImageView(QGraphicsView):
    """Zoom (wheel) / pan (drag) image view with a view-state API for synchronisation."""

    view_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(_SMOOTH)
        self._scene.addItem(self._item)
        self.setDragMode(_SCROLL_DRAG)
        self.setTransformationAnchor(_ANCHOR_MOUSE)
        self.setRenderHints(_ANTIALIAS | _SMOOTH_PIXMAP)
        self.setFrameShape(_FRAME_NONE)
        self.setBackgroundBrush(QBrush(QColor(28, 28, 30)))
        self._caption = ""
        self._has_image = False
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self.view_changed.emit())
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.view_changed.emit())

    def set_caption(self, text: str) -> None:
        self._caption = text
        self.viewport().update()

    def set_image(self, image: Optional[np.ndarray]) -> None:
        if image is None:
            self._item.setPixmap(QPixmap())
            self._has_image = False
            return
        pixmap = numpy_to_pixmap(image)
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._has_image = True

    def zoom_factor(self) -> float:
        return float(self.transform().m11())

    def fit(self) -> None:
        if self._has_image:
            self.fitInView(self._item, _KEEP_ASPECT)
            self.view_changed.emit()

    def one_to_one(self) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.centerOn(center)
        self.view_changed.emit()

    def zoom_to(self, rect) -> None:
        x1, y1, x2, y2 = rect
        self.fitInView(QRectF(x1, y1, max(1, x2 - x1), max(1, y2 - y1)), _KEEP_ASPECT)
        self.view_changed.emit()

    def view_state(self) -> tuple:
        center = self.mapToScene(self.viewport().rect().center())
        return self.zoom_factor(), center.x(), center.y()

    def apply_view_state(self, state) -> None:
        zoom, cx, cy = state
        self.resetTransform()
        self.scale(zoom, zoom)
        self.centerOn(QPointF(cx, cy))

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        delta = event.angleDelta().y()
        if not delta:
            return
        factor = 1.25 if delta > 0 else 0.8
        new_zoom = self.zoom_factor() * factor
        if 0.02 <= new_zoom <= 40:
            self.scale(factor, factor)
            self.view_changed.emit()

    def drawForeground(self, painter, rect) -> None:  # noqa: N802 - Qt API
        if not self._caption:
            return
        painter.save()
        painter.resetTransform()
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text = f"{self._caption}  ·  {self.zoom_factor() * 100:.0f}%"
        w = fm.horizontalAdvance(text) + 16
        box = QRectF(8, 8, w, fm.height() + 8)
        painter.setPen(_NO_PEN)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box, _Qt.AlignCenter, text)
        painter.restore()


class TraceChartWidget(QWidget):
    """Vector charts for a step: histogram, 8-direction blur bars, score curve."""

    def __init__(self, chart, parent=None) -> None:
        super().__init__(parent)
        self.chart = chart
        self.setMinimumHeight(200 if chart.kind != "directions" else 170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding if hasattr(QSizePolicy, "Policy") else QSizePolicy.Expanding,
                           QSizePolicy.Policy.Fixed if hasattr(QSizePolicy, "Policy") else QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        try:
            painter.setRenderHint(_ANTIALIAS)
            pal = self.palette()
            text = pal.color(_ROLE.WindowText)
            muted = pal.color(_ROLE.PlaceholderText)
            painter.setPen(text)
            title_font = QFont(self.font())
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.drawText(QRectF(0, 0, self.width(), 18), _Qt.AlignLeft | _Qt.AlignVCenter, self.chart.title)
            painter.setFont(self.font())
            # Bottom 40 px: tick labels (14 px) then the legend/caption row, never overlapping.
            plot = QRectF(34, 24, self.width() - 44, self.height() - 24 - 40)
            getattr(self, f"_paint_{self.chart.kind}", lambda *a: None)(painter, plot, text, muted)
        finally:
            painter.end()

    # σ axis shared by histogram and score curve
    @staticmethod
    def _x(plot: QRectF, sigma: float, lo: float = 0.3, hi: float = 2.6) -> float:
        return plot.left() + (min(max(sigma, lo), hi) - lo) / (hi - lo) * plot.width()

    def _sigma_axis(self, painter, plot, muted, lo=0.3, hi=2.6) -> None:
        painter.setPen(muted)
        for tick in (0.5, 1.0, 1.5, 2.0, 2.5):
            x = self._x(plot, tick, lo, hi)
            painter.drawLine(QPointF(x, plot.bottom()), QPointF(x, plot.bottom() + 3))
            painter.drawText(QRectF(x - 16, plot.bottom() + 3, 32, 14), _Qt.AlignCenter, f"{tick:g}")

    def _bands(self, painter, plot, data) -> None:
        # Verdict zones behind the bars: sharp | usable | soft (up to clearly blurred) | beyond.
        edges = [0.3, *(data.get("bands") or []), 2.6]
        colors = [*(data.get("band_colors") or []), "#7a1414"]
        for i in range(len(edges) - 1):
            color = QColor(colors[min(i, len(colors) - 1)])
            color.setAlpha(34)
            x1, x2 = self._x(plot, edges[i]), self._x(plot, edges[i + 1])
            painter.fillRect(QRectF(x1, plot.top(), x2 - x1, plot.height()), color)

    def _paint_histogram(self, painter, plot, text, muted) -> None:
        data = self.chart.data
        self._bands(painter, plot, data)
        series = [s for s in data.get("series", []) if np.asarray(s.get("values", [])).size]
        bins = np.linspace(0.3, 2.6, 47)
        hists = []
        for s in series:
            values = np.clip(np.asarray(s["values"], dtype=float), 0.3, 2.6 - 1e-6)
            counts, _ = np.histogram(values, bins=bins)
            hists.append((s, counts / max(1, counts.sum())))
        peak = max([float(h.max()) for _, h in hists] + [1e-6])
        bw = plot.width() / (len(bins) - 1)
        for k, (s, h) in enumerate(hists):
            color = QColor(s.get("color", "#888888"))
            color.setAlpha(200 if k == 0 else 110)
            painter.setPen(_NO_PEN)
            painter.setBrush(color)
            for i, v in enumerate(h):
                if v <= 0:
                    continue
                bh = v / peak * plot.height()
                painter.drawRect(QRectF(plot.left() + i * bw + 0.5, plot.bottom() - bh, max(1.0, bw - 1), bh))
        painter.setPen(QPen(muted, 1))
        painter.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        self._sigma_axis(painter, plot, muted)
        median = data.get("median")
        if median is not None:
            x = self._x(plot, float(median))
            painter.setPen(QPen(text, 2, _DASH))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawText(QRectF(x + 4, plot.top(), 120, 14), _Qt.AlignLeft | _Qt.AlignVCenter,
                             f"中位数 {float(median):.2f}")
        legend_x = plot.left()
        for s, _h in hists:
            n = np.asarray(s["values"]).size
            painter.setPen(_NO_PEN)
            painter.setBrush(QColor(s.get("color", "#888888")))
            painter.drawRect(QRectF(legend_x, self.height() - 12, 9, 9))
            painter.setPen(text)
            label = f"{s['name']}（{n} 点）"
            painter.drawText(QRectF(legend_x + 12, self.height() - 15, 140, 14), _Qt.AlignLeft | _Qt.AlignVCenter, label)
            legend_x += 14 + painter.fontMetrics().horizontalAdvance(label) + 14
        if not hists:
            painter.setPen(muted)
            painter.drawText(plot, _Qt.AlignCenter, "没有可测边缘")

    def _paint_directions(self, painter, plot, text, muted) -> None:
        data = self.chart.data
        values = data.get("bins") or []
        known = [v for v in values if v is not None]
        top = max(known + [1.0]) * 1.15
        n = max(1, len(values))
        bw = plot.width() / n
        for i, v in enumerate(values):
            x = plot.left() + i * bw
            if v is None:
                painter.setPen(muted)
                painter.drawText(QRectF(x, plot.bottom() - 16, bw, 14), _Qt.AlignCenter, "—")
            else:
                h = v / top * plot.height()
                from bird_sharpness.trace import hex_color, sigma_color

                painter.setPen(_NO_PEN)
                painter.setBrush(QColor(hex_color(sigma_color(v))))
                painter.drawRoundedRect(QRectF(x + 3, plot.bottom() - h, bw - 6, h), 2, 2)
                painter.setPen(text)
                painter.drawText(QRectF(x, plot.bottom() - h - 14, bw, 13), _Qt.AlignCenter, f"{v:.2f}")
            painter.setPen(muted)
            painter.drawText(QRectF(x, plot.bottom() + 2, bw, 14), _Qt.AlignCenter, f"{i * 180 // n}°")
        ratio = data.get("ratio")
        threshold = data.get("threshold", 1.5)
        verdict = "—" if ratio is None else (f"{ratio:.2f}（≥{threshold:g} 且身体模糊 → 运动模糊）"
                                            if ratio >= threshold else f"{ratio:.2f}（各方向接近，非运动模糊）")
        painter.setPen(text)
        painter.drawText(QRectF(plot.left(), self.height() - 14, plot.width(), 14), _Qt.AlignLeft, f"方向比 {verdict}")

    def _paint_score_curve(self, painter, plot, text, muted) -> None:
        data = self.chart.data
        anchors = data.get("anchors") or []

        def y_of(score):
            return plot.bottom() - max(0.0, min(1000.0, score)) / 1000.0 * plot.height()

        painter.setPen(QPen(muted, 1))
        painter.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        painter.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))
        for score in (0, 500, 1000):
            painter.drawText(QRectF(0, y_of(score) - 7, 30, 14), _Qt.AlignRight | _Qt.AlignVCenter, str(score))
        for score, label in data.get("gates") or []:
            y = y_of(score)
            painter.setPen(QPen(muted, 1, _DASH))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.drawText(QRectF(plot.left() + 4, y - 14, 110, 13), _Qt.AlignLeft, f"{label} {score}")
        if anchors:
            path = QPainterPath()
            for i, (sigma, score) in enumerate(anchors):
                pt = QPointF(self._x(plot, sigma), y_of(score))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            path.lineTo(QPointF(plot.right(), y_of(anchors[-1][1])))
            painter.setPen(QPen(text, 2))
            painter.setBrush(QBrush())
            painter.drawPath(path)
        self._sigma_axis(painter, plot, muted)
        for p in data.get("points") or []:
            if p.get("sigma") is None or p.get("score") is None:
                continue
            pt = QPointF(self._x(plot, float(p["sigma"])), y_of(float(p["score"])))
            r = 7 if p.get("best") else 5
            painter.setPen(QPen(QColor(255, 255, 255), 2 if p.get("best") else 1))
            painter.setBrush(QColor(p.get("color", "#ffffff")))
            painter.drawEllipse(pt, r, r)
            painter.setPen(text)
            painter.drawText(QRectF(pt.x() + 8, pt.y() - 16, 90, 14), _Qt.AlignLeft,
                             f"{p.get('label', '')} σ{float(p['sigma']):.2f} → {int(p['score'])}")


class BirdSharpnessTraceDialog(QDialog):
    """Non-modal window for one photo's trace; ``set_trace`` once the worker delivers it."""

    closed = pyqtSignal(object)

    def __init__(self, parent, path: str) -> None:
        super().__init__(parent)
        self.path = path
        self.trace = None
        self.steps: List = []
        self.index = 0
        self._syncing = False
        self.setWindowTitle(f"清晰度计算过程 - {os.path.basename(path)}")
        self.setModal(False)
        self.resize(1320, 840)
        # Step images are large; free them when the window closes (the request is cancelled first).
        self.setAttribute(getattr(getattr(Qt, "WidgetAttribute", Qt), "WA_DeleteOnClose"), True)

        self.stack = QStackedWidget(self)
        self.loading = QLabel(f"正在计算 {os.path.basename(path)} 的清晰度过程…\n（全分辨率解码与识别，约 2–5 秒）", self)
        self.loading.setAlignment(_Qt.AlignCenter)
        self.stack.addWidget(self.loading)
        self.content = QWidget(self)
        self.stack.addWidget(self.content)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(self.stack)
        self._build_content()

    # ── layout ────────────────────────────────────────────────────────────
    def _build_content(self) -> None:
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.title = QLabel(os.path.basename(self.path), self.content)
        title_font = QFont(self.title.font())
        title_font.setPointSizeF(title_font.pointSizeF() + 3)
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.verdict_chip = QLabel("", self.content)
        self.summary = QLabel("", self.content)
        self.summary.setForegroundRole(_ROLE.PlaceholderText)
        self.bird_combo = QComboBox(self.content)
        self.bird_combo.currentIndexChanged.connect(self._on_bird_changed)
        header.addWidget(self.title)
        header.addSpacing(10)
        header.addWidget(self.verdict_chip)
        header.addSpacing(10)
        header.addWidget(self.summary, 1)
        self.bird_label = QLabel("鸟：", self.content)
        header.addWidget(self.bird_label)
        header.addWidget(self.bird_combo)
        layout.addLayout(header)

        self.view_a = TraceImageView(self.content)
        self.view_b = TraceImageView(self.content)
        self.view_b.setVisible(False)
        self.views = QSplitter(_HORIZONTAL, self.content)
        self.views.addWidget(self.view_a)
        self.views.addWidget(self.view_b)
        self.view_a.view_changed.connect(lambda: self._sync_from(self.view_a, self.view_b))
        self.view_b.view_changed.connect(lambda: self._sync_from(self.view_b, self.view_a))

        side = QWidget(self.content)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 0, 4, 0)
        self.step_title = QLabel("", side)
        st_font = QFont(self.step_title.font())
        st_font.setPointSizeF(st_font.pointSizeF() + 2)
        st_font.setBold(True)
        self.step_title.setFont(st_font)
        self.step_desc = QLabel("", side)
        self.step_desc.setWordWrap(True)
        self.metrics_box = QWidget(side)
        self.metrics_grid = QGridLayout(self.metrics_box)
        self.metrics_grid.setContentsMargins(0, 4, 0, 4)
        self.metrics_grid.setHorizontalSpacing(12)
        self.charts_box = QWidget(side)
        self.charts_layout = QVBoxLayout(self.charts_box)
        self.charts_layout.setContentsMargins(0, 0, 0, 0)
        self.legend = QLabel("", side)
        self.legend.setTextFormat(_RICH)
        self.legend.setWordWrap(True)
        side_layout.addWidget(self.step_title)
        side_layout.addWidget(self.step_desc)
        side_layout.addWidget(self.metrics_box)
        side_layout.addWidget(self.charts_box)
        side_layout.addWidget(self.legend)
        side_layout.addStretch(1)
        scroll = QScrollArea(self.content)
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_FRAME_NONE)
        scroll.setMinimumWidth(360)

        body = QSplitter(_HORIZONTAL, self.content)
        body.addWidget(self.views)
        body.addWidget(scroll)
        body.setStretchFactor(0, 1)
        body.setSizes([940, 380])
        layout.addWidget(body, 1)

        # step bar
        bar = QHBoxLayout()
        self.prev_btn = QToolButton(self.content)
        self.prev_btn.setText("◀")
        self.prev_btn.setToolTip("上一步（←）")
        self.prev_btn.clicked.connect(lambda: self.go(self.index - 1))
        self.next_btn = QToolButton(self.content)
        self.next_btn.setText("▶")
        self.next_btn.setToolTip("下一步（→）")
        self.next_btn.clicked.connect(lambda: self.go(self.index + 1))
        self.slider = QSlider(_HORIZONTAL, self.content)
        self.slider.setPageStep(1)
        self.slider.valueChanged.connect(self.go)
        self.slider.setMinimumWidth(180)
        self.chips_box = QWidget(self.content)
        self.chips_layout = QHBoxLayout(self.chips_box)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(4)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.slider)
        bar.addWidget(self.next_btn)
        bar.addSpacing(8)
        bar.addWidget(self.chips_box, 1)
        layout.addLayout(bar)

        tools = QHBoxLayout()
        self.compare_btn = ToggleToolButton("对照", self.content)
        self.compare_btn.setToolTip("并排显示另一步骤，观察处理前后的变化")
        self.compare_btn.toggled.connect(self._on_compare_toggled)
        self.compare_combo = QComboBox(self.content)
        self.compare_combo.setEnabled(False)
        self.compare_combo.currentIndexChanged.connect(lambda _i: self._show_compare())
        self.sync_btn = ToggleToolButton("同步缩放/移动", self.content)
        self.sync_btn.setChecked(True)
        self.sync_btn.setToolTip("两侧坐标系相同（同一图像/裁切）时联动缩放和平移")
        self.fit_btn = QPushButton("适应窗口", self.content)
        self.fit_btn.clicked.connect(self._fit_all)
        self.one_btn = QPushButton("100%", self.content)
        self.one_btn.clicked.connect(lambda: (self.view_a.one_to_one(), self.view_b.isVisible() and self.view_b.one_to_one()))
        self.zoom_region_btn = QPushButton("放大到测量区域", self.content)
        self.zoom_region_btn.clicked.connect(self._zoom_to_region)
        tools.addWidget(self.compare_btn)
        tools.addWidget(self.compare_combo)
        tools.addWidget(self.sync_btn)
        tools.addStretch(1)
        tools.addWidget(self.zoom_region_btn)
        tools.addWidget(self.fit_btn)
        tools.addWidget(self.one_btn)
        layout.addLayout(tools)

    # ── data ──────────────────────────────────────────────────────────────
    def set_error(self, message: str) -> None:
        self.loading.setText(f"无法生成清晰度计算过程：\n{message}")

    def set_trace(self, trace) -> None:
        self.trace = trace
        result = trace.result
        style = VERDICT_STYLES.get(getattr(result, "verdict", ""))
        if style is not None:
            self.verdict_chip.setText(f"  {style.label}  ")
            self.verdict_chip.setStyleSheet(
                f"background:{style.color}; color:white; border-radius:9px; padding:2px 6px; font-weight:bold;")
        parts = []
        if result is not None:
            if result.score is not None:
                parts.append(f"分数 {result.score}")
            if result.sigma is not None:
                parts.append(f"模糊半径 {result.sigma:.2f} px")
            from app_common.bird_sharpness_fields import REGION_LABELS

            parts.append(f"区域 {REGION_LABELS.get(result.region, '—')}")
            parts.append(f"鸟 {result.bird_count} 只")
            parts.append(f"耗时 {result.elapsed_s:.1f} s")
        self.summary.setText("　·　".join(parts))
        self.bird_combo.blockSignals(True)
        self.bird_combo.clear()
        for bird in trace.birds:
            self.bird_combo.addItem(bird.label, bird.index)
        self.bird_combo.setCurrentIndex(trace.best_bird_index() if trace.birds else -1)
        self.bird_combo.blockSignals(False)
        self.bird_combo.setVisible(len(trace.birds) > 1)
        self.bird_label.setVisible(len(trace.birds) > 1)
        self._load_steps(trace.steps_for(None), keep_key=None)
        self.stack.setCurrentWidget(self.content)
        QTimer.singleShot(0, lambda: self.go(0, force=True))

    def _load_steps(self, steps, keep_key: Optional[str]) -> None:
        self.steps = list(steps)
        for button in list(self.chip_group.buttons()):
            self.chip_group.removeButton(button)
        _clear_layout(self.chips_layout)
        for i, step in enumerate(self.steps):
            chip = ToggleToolButton(f"{_CIRCLED[i] if i < len(_CIRCLED) else i + 1} {STEP_ICONS.get(step.key, step.title)}",
                                    self.chips_box)
            chip.setToolTip(step.title)
            chip.clicked.connect(lambda _c=False, k=i: self.go(k))
            self.chip_group.addButton(chip, i)
            self.chips_layout.addWidget(chip)
        self.chips_layout.addStretch(1)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self.steps) - 1))
        self.slider.blockSignals(False)
        self.compare_combo.blockSignals(True)
        self.compare_combo.clear()
        self.compare_combo.addItem("上一步", -1)
        for i, step in enumerate(self.steps):
            self.compare_combo.addItem(f"{i + 1}. {step.title}", i)
        self.compare_combo.blockSignals(False)
        if keep_key is not None:
            for i, step in enumerate(self.steps):
                if step.key == keep_key:
                    self.index = i
                    break

    def _on_bird_changed(self, combo_index: int) -> None:
        if self.trace is None or combo_index < 0:
            return
        key = self.steps[self.index].key if self.steps else None
        bird_index = self.bird_combo.itemData(combo_index)
        self._load_steps(self.trace.steps_for(bird_index), keep_key=key)
        self.go(self.index, force=True)

    # ── navigation ────────────────────────────────────────────────────────
    def go(self, index: int, force: bool = False) -> None:
        if not self.steps:
            return
        index = max(0, min(int(index), len(self.steps) - 1))
        previous = self.steps[self.index] if 0 <= self.index < len(self.steps) else None
        if index == self.index and not force:
            return
        self.index = index
        step = self.steps[index]
        same_frame = previous is not None and previous.frame == step.frame and not force
        state = self.view_a.view_state() if same_frame else None
        self.view_a.set_image(step.image)
        self.view_a.set_caption(f"{index + 1}. {step.title}")
        if state is not None:
            self.view_a.apply_view_state(state)  # continuity: same pixels, same place
        elif step.focus_rect:
            self.view_a.zoom_to(step.focus_rect)
        else:
            self.view_a.fit()
        self._show_side(step)
        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)
        button = self.chip_group.button(index)
        if button is not None:
            button.setChecked(True)
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < len(self.steps) - 1)
        self.zoom_region_btn.setEnabled(bool(step.focus_rect))
        self._show_compare()

    def _compare_index(self) -> int:
        data = self.compare_combo.currentData()
        if data is None or int(data) < 0:
            return max(0, self.index - 1)
        return int(data)

    def _show_compare(self) -> None:
        if not self.view_b.isVisible() or not self.steps:
            return
        i = self._compare_index()
        step = self.steps[i]
        self.view_b.set_image(step.image)
        self.view_b.set_caption(f"对照 {i + 1}. {step.title}")
        if self.sync_btn.isChecked() and step.frame == self.steps[self.index].frame:
            self._syncing = True
            try:
                self.view_b.apply_view_state(self.view_a.view_state())
            finally:
                self._syncing = False
        elif step.focus_rect:
            self.view_b.zoom_to(step.focus_rect)
        else:
            self.view_b.fit()

    def _on_compare_toggled(self, checked: bool) -> None:
        self.view_b.setVisible(checked)
        self.compare_combo.setEnabled(checked)
        if checked:
            self.views.setSizes([1, 1])
            QTimer.singleShot(0, self._show_compare)

    def _sync_from(self, source: TraceImageView, target: TraceImageView) -> None:
        if self._syncing or not target.isVisible() or not self.sync_btn.isChecked() or not self.steps:
            return
        a_frame = self.steps[self.index].frame
        b_frame = self.steps[self._compare_index()].frame
        if a_frame != b_frame:
            return
        self._syncing = True
        try:
            target.apply_view_state(source.view_state())
        finally:
            self._syncing = False

    def _fit_all(self) -> None:
        self.view_a.fit()
        if self.view_b.isVisible():
            self.view_b.fit()

    def _zoom_to_region(self) -> None:
        step = self.steps[self.index] if self.steps else None
        if step is not None and step.focus_rect:
            self.view_a.zoom_to(step.focus_rect)

    # ── side panel ────────────────────────────────────────────────────────
    def _show_side(self, step) -> None:
        self.step_title.setText(f"步骤 {self.index + 1} / {len(self.steps)} · {step.title}")
        self.step_desc.setText(step.description)
        _clear_layout(self.metrics_grid)
        for row, (label, value) in enumerate(step.metrics):
            key = QLabel(label, self.metrics_box)
            key.setForegroundRole(_ROLE.PlaceholderText)
            val = QLabel(str(value), self.metrics_box)
            val.setWordWrap(True)
            self.metrics_grid.addWidget(key, row, 0, _Qt.AlignTop)
            self.metrics_grid.addWidget(val, row, 1)
        self.metrics_grid.setColumnStretch(1, 1)
        _clear_layout(self.charts_layout)
        for chart in step.charts:
            self.charts_layout.addWidget(TraceChartWidget(chart, self.charts_box))
        self.legend.setText("<br>".join(
            f'<span style="color:{color}">■</span> {label}' for color, label in step.legend))

    # ── keyboard / lifecycle ──────────────────────────────────────────────
    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        key = event.key()
        if key in (_KEY.Key_Right, _KEY.Key_PageDown):
            self.go(self.index + 1)
        elif key in (_KEY.Key_Left, _KEY.Key_PageUp):
            self.go(self.index - 1)
        elif key == _KEY.Key_Home:
            self.go(0)
        elif key == _KEY.Key_End:
            self.go(len(self.steps) - 1)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.closed.emit(self)
        super().closeEvent(event)
