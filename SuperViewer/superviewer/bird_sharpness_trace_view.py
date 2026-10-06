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
from bird_sharpness.image_source import SOURCE_LABELS, SOURCE_RAW
from bird_sharpness.params import AnalysisParams
from bird_sharpness.scoring import SIGMA_SHARP_MAX
from bird_sharpness.trace import C_PEAKING, hex_color, peaking_overlay

from .bird_sharpness_params_form import (ESTIMATOR_CHOICES, SOURCE_CHOICES, AnalysisParamsForm,  # noqa: F401
                                         TileParamsForm, download_models, missing_models, params_summary, tile_summary)
from .qt_compat import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget, pyqtSignal,
)

try:
    from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
    from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
    from PyQt6.QtWidgets import (QButtonGroup, QFrame, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                                 QDoubleSpinBox, QGraphicsScene, QGraphicsView, QGridLayout, QSizePolicy, QSlider,
                                 QSpinBox, QTabWidget)
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
    from PyQt5.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
    from PyQt5.QtWidgets import (QButtonGroup, QFrame, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                                 QDoubleSpinBox, QGraphicsScene, QGraphicsView, QGridLayout, QSizePolicy, QSlider,
                                 QSpinBox, QTabWidget)

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
_SCROLL_OFF = getattr(getattr(Qt, "ScrollBarPolicy", Qt), "ScrollBarAlwaysOff")
_EVENT = getattr(QEvent, "Type", QEvent)

STEP_ICONS = {
    "decode": "解码", "detect": "识别", "recheck": "复检", "birds": "逐只鸟", "bird": "鸟体", "head": "头部", "edges": "边缘",
    "distribution": "分布", "focus": "焦点", "tiles": "分块", "manual": "焦平面", "enhanced": "找鸟", "result": "结论",
}
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_TOOLTIP_ROLE = getattr(getattr(Qt, "ItemDataRole", Qt), "ToolTipRole")
DEFAULT_TRACE_PARAMS = AnalysisParams().as_params()

ALL_BIRDS = -1  # bird selector item: every bird's steps in turn
PEAKING_MIN, PEAKING_MAX = 0.4, 2.5  # 焦平面 threshold range (px)
PEAKING_SLIDER_SCALE = 100  # slider ticks per px: 0.01 px steps


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
        # Hover highlight: everything outside the box dimmed, the box outlined (screen-width pen).
        self._shade = QGraphicsPathItem()
        self._shade.setPen(QPen(_NO_PEN))
        self._shade.setBrush(QColor(0, 0, 0, 130))
        self._outline = QGraphicsRectItem()
        pen = QPen(QColor(255, 255, 255), 3)
        pen.setCosmetic(True)
        self._outline.setPen(pen)
        for z, item in enumerate((self._shade, self._outline), start=1):
            item.setZValue(z)
            item.setVisible(False)
            self._scene.addItem(item)
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
        self.set_highlight(None)
        if image is None:
            self._item.setPixmap(QPixmap())
            self._has_image = False
            return
        pixmap = numpy_to_pixmap(image)
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._has_image = True

    def set_highlight(self, box) -> None:
        """Outline ``box`` (x1, y1, x2, y2, image coords) and dim the rest; ``None`` clears."""
        if box is None or not self._has_image:
            self._shade.setVisible(False)
            self._outline.setVisible(False)
            return
        x1, y1, x2, y2 = box
        rect = QRectF(x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1))
        path = QPainterPath()
        path.addRect(self._item.boundingRect())
        path.addRect(rect)  # odd-even fill: the box stays undimmed
        self._shade.setPath(path)
        self._outline.setRect(rect)
        self._shade.setVisible(True)
        self._outline.setVisible(True)
        self.ensureVisible(rect, 24, 24)  # zoomed in: bring the bird into view (no-op when already shown)

    def highlight_rect(self) -> Optional[QRectF]:
        return self._outline.rect() if self._outline.isVisible() else None

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


class TraceBirdList(QWidget):
    """A step's birds: colour swatch, label and details per row (``TraceBirdRow``).

    Hovering a row tints it and emits ``hovered(row)`` so the views can highlight
    that bird; leaving the row or replacing the rows emits ``None``.
    """

    hovered = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 4, 0, 4)
        # No column/row gaps: the padding lives inside the labels, so the pointer never
        # falls between two cells of a row and the highlight does not flicker.
        self.grid.setHorizontalSpacing(0)
        self.grid.setVerticalSpacing(0)
        self.rows: List = []
        self.cells: List[tuple] = []  # per row: (swatch, label, value)
        self._row_of: dict = {}  # cell widget -> row index (stale widgets of old rows are absent)
        self.hovered_row: Optional[int] = None
        self.setVisible(False)

    def set_rows(self, rows) -> None:
        self._set_hovered(None)
        _clear_layout(self.grid)
        self.rows, self.cells, self._row_of = list(rows), [], {}
        for r, row in enumerate(self.rows):
            swatch = QLabel(f'<span style="color:{row.color}">■</span>', self)
            swatch.setTextFormat(_RICH)
            swatch.setContentsMargins(0, 3, 6, 3)
            label = QLabel(row.label, self)
            label.setForegroundRole(_ROLE.PlaceholderText)
            label.setContentsMargins(0, 3, 12, 3)
            value = QLabel(row.value, self)
            value.setWordWrap(True)
            value.setContentsMargins(0, 3, 0, 3)
            for c, widget in enumerate((swatch, label, value)):
                widget.setAlignment(_Qt.AlignLeft | _Qt.AlignTop)  # cells fill the row: the tint covers it whole
                self.grid.addWidget(widget, r, c)
                widget.installEventFilter(self)
                self._row_of[widget] = r
            self.cells.append((swatch, label, value))
        self.grid.setColumnStretch(2, 1)
        self.setVisible(bool(self.rows))

    def _set_hovered(self, r: Optional[int]) -> None:
        if r == self.hovered_row:
            return
        if self.hovered_row is not None and self.hovered_row < len(self.cells):
            for widget in self.cells[self.hovered_row]:
                widget.setStyleSheet("")
        self.hovered_row = r
        if r is not None:
            tint = self.palette().color(_ROLE.Highlight)
            for widget in self.cells[r]:
                widget.setStyleSheet(f"background: rgba({tint.red()}, {tint.green()}, {tint.blue()}, 90);")
        self.hovered.emit(None if r is None else self.rows[r])

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        kind = event.type()
        if kind in (_EVENT.Enter, _EVENT.Leave):
            r = self._row_of.get(obj)
            if r is not None:
                if kind == _EVENT.Enter:
                    self._set_hovered(r)
                elif self.hovered_row == r:
                    self._set_hovered(None)
        return super().eventFilter(obj, event)


class BirdSharpnessTraceDialog(QDialog):
    """Non-modal window for one photo's trace; ``set_trace`` once the worker delivers it."""

    closed = pyqtSignal(object)
    source_changed = pyqtSignal(object, str)  # (dialog, image source) — recompute requested
    params_changed = pyqtSignal(object)  # dialog — recompute with ``dialog.params``
    save_defaults_requested = pyqtSignal(object)  # dialog — store ``dialog.params`` as user options
    # dialog, temporary AnalysisImage, analyzer.GivenBirds, title — a model chain window's 测清晰度
    analyze_pixels_requested = pyqtSignal(object, object, object, str)

    def __init__(self, parent, path: str, image_source: str = SOURCE_RAW, params: Optional[dict] = None) -> None:
        super().__init__(parent)
        self.path = path
        self.image_source = image_source
        # Analysis options for this window only; "保存为默认设置" makes them the user's defaults
        # (the image source included: it follows the switch above the steps).
        self.params = {**DEFAULT_TRACE_PARAMS, **(params or {}), "image_source": image_source}
        self.trace = None
        self.given_input = None  # (temporary image, GivenBirds): measures a model chain window's results
        self.steps: List = []
        self.index = 0
        self._syncing = False
        self._hovered_bird = None  # TraceBirdRow under the pointer in the bird list
        self.setWindowTitle(f"清晰度计算过程 - {os.path.basename(path)}")
        self.setModal(False)
        self.resize(1320, 840)
        # Step images are large; free them when the window closes (the request is cancelled first).
        self.setAttribute(getattr(getattr(Qt, "WidgetAttribute", Qt), "WA_DeleteOnClose"), True)

        self.stack = QStackedWidget(self)
        self.loading = QLabel("", self)
        self.loading.setAlignment(_Qt.AlignCenter)
        self.loading.setWordWrap(True)
        # Which pixels are measured; switching recomputes (always visible, also while loading).
        self.source_combo = QComboBox(self)
        for key, label, tip in SOURCE_CHOICES:
            self.source_combo.addItem(label, key)
            self.source_combo.setItemData(self.source_combo.count() - 1, tip, _TOOLTIP_ROLE)
        self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(image_source)))
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(QLabel("图像：", self))
        top.addWidget(self.source_combo)
        self.set_loading()
        self.stack.addWidget(self.loading)
        self.content = QWidget(self)
        self.stack.addWidget(self.content)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addLayout(top)
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
        self.timing = QLabel("", self.content)  # per-stage seconds of this computation
        self.timing.setForegroundRole(_ROLE.PlaceholderText)
        self.timing.setVisible(False)
        layout.addWidget(self.timing)

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
        self.bird_list = TraceBirdList(side)
        self.bird_list.hovered.connect(self._on_bird_hovered)
        self.charts_box = QWidget(side)
        self.charts_layout = QVBoxLayout(self.charts_box)
        self.charts_layout.setContentsMargins(0, 0, 0, 0)
        self.legend = QLabel("", side)
        self.legend.setTextFormat(_RICH)
        self.legend.setWordWrap(True)
        side_layout.addWidget(self.step_title)
        side_layout.addWidget(self.step_desc)
        side_layout.addWidget(self.metrics_box)
        side_layout.addWidget(self.bird_list)
        side_layout.addWidget(self.charts_box)
        side_layout.addWidget(self.legend)
        side_layout.addStretch(1)
        scroll = QScrollArea(self.content)
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_FRAME_NONE)
        scroll.setMinimumWidth(360)
        self.side_tabs = QTabWidget(self.content)
        self.side_tabs.addTab(scroll, "步骤")
        self.side_tabs.addTab(self._build_params_tab(), "参数")

        body = QSplitter(_HORIZONTAL, self.content)
        body.addWidget(self.views)
        body.addWidget(self.side_tabs)
        # Model chain (参数 tab 「预览」 / 「＋ 添加窗口」): windows side by side at the right edge.
        from .model_preview import ModelChainHost

        self.preview_host = ModelChainHost(self._preview_image, self._preview_boxes,
                                           lambda parent, names: download_models(parent, names), self.content)
        self.preview_host.setVisible(False)
        self.preview_host.emptied.connect(lambda: self.preview_host.setVisible(False))
        self.preview_host.stage_added.connect(self._make_room_for_chain)
        self.preview_host.analyze_requested.connect(self._analyze_stage)
        self._chain_source: Optional[str] = None  # image source the chain was opened on (None = no trace yet)
        body.addWidget(self.preview_host)
        self._body = body
        body.setStretchFactor(0, 1)
        body.setSizes([940, 380, 0])
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
        # Several birds add a run of steps each: scroll instead of squeezing the chips.
        self.chips_scroll = QScrollArea(self.content)
        self.chips_scroll.setWidget(self.chips_box)
        self.chips_scroll.setWidgetResizable(True)
        self.chips_scroll.setFrameShape(_FRAME_NONE)
        self.chips_scroll.setVerticalScrollBarPolicy(_SCROLL_OFF)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.slider)
        bar.addWidget(self.next_btn)
        bar.addSpacing(8)
        bar.addWidget(self.chips_scroll, 1)
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
        # Focus peaking: edges whose own blur radius is within the threshold, tinted red.
        self.peaking_btn = ToggleToolButton("焦平面", self.content)
        self.peaking_btn.setToolTip(
            "像对焦时的峰值显示一样，把焦平面上的像素标红：全分辨率下逐个边缘测模糊半径（与清晰度同一算法，"
            "不受对比度影响），σ 不超过右侧阈值的边缘标红。逐只鸟总览没有此图。")
        self.peaking_btn.toggled.connect(self._on_peaking_changed)
        # Threshold: slider for dragging, spin box for typing an exact value; kept in step.
        threshold_tip = f"边缘模糊半径不超过此值即视为在焦平面上（默认 {SIGMA_SHARP_MAX:.2f} = 清晰门槛）"
        self.peaking_slider = QSlider(_HORIZONTAL, self.content)
        self.peaking_slider.setRange(int(round(PEAKING_MIN * PEAKING_SLIDER_SCALE)),
                                     int(round(PEAKING_MAX * PEAKING_SLIDER_SCALE)))
        self.peaking_slider.setSingleStep(1)
        self.peaking_slider.setPageStep(10)
        self.peaking_slider.setFixedWidth(180)
        self.peaking_slider.setToolTip(threshold_tip)
        self.peaking_spin = QDoubleSpinBox(self.content)
        self.peaking_spin.setRange(PEAKING_MIN, PEAKING_MAX)
        self.peaking_spin.setSingleStep(0.05)
        self.peaking_spin.setDecimals(2)
        self.peaking_spin.setPrefix("σ ≤ ")
        self.peaking_spin.setSuffix(" px")
        self.peaking_spin.setKeyboardTracking(False)  # typing: apply on Enter / focus out, not per digit
        self.peaking_spin.setToolTip(threshold_tip)
        self.peaking_spin.setValue(SIGMA_SHARP_MAX)
        self.peaking_slider.setValue(int(round(SIGMA_SHARP_MAX * PEAKING_SLIDER_SCALE)))
        for widget in (self.peaking_slider, self.peaking_spin):
            widget.setEnabled(False)
        self.peaking_slider.valueChanged.connect(
            lambda v: self.peaking_spin.setValue(v / PEAKING_SLIDER_SCALE))
        self.peaking_spin.valueChanged.connect(self._on_peaking_threshold)
        tools.addWidget(self.compare_btn)
        tools.addWidget(self.compare_combo)
        tools.addWidget(self.sync_btn)
        tools.addSpacing(12)
        tools.addWidget(self.peaking_btn)
        tools.addWidget(self.peaking_slider)
        tools.addWidget(self.peaking_spin)
        tools.addStretch(1)
        tools.addWidget(self.zoom_region_btn)
        tools.addWidget(self.fit_btn)
        tools.addWidget(self.one_btn)
        layout.addLayout(tools)

    def _build_params_tab(self) -> QWidget:
        page = QWidget(self.content)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 6, 8)
        heading = QLabel("清晰度计算参数", page)
        font = QFont(heading.font())
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        # The same form as 设置 → 鸟清晰度: batch detection and this window run the same pipeline.
        # The image source is the switch above the steps, not a second combo here.
        self.params_form = AnalysisParamsForm(page, preview=True, source=False)
        self.params_form.preview_requested.connect(self.open_model_preview)
        self.max_birds_spin = self.params_form.max_birds
        self.estimator_combo = self.params_form.estimator
        self.estimator_note = self.params_form.estimator_note
        self.tile_form = self.params_form.tiles
        layout.addWidget(self.params_form)
        self.params_status = QLabel("", page)
        self.params_status.setWordWrap(True)
        layout.addWidget(self.params_status)
        buttons = QHBoxLayout()
        self.rerun_btn = QPushButton("按此参数重新计算", page)
        self.rerun_btn.clicked.connect(self._rerun_with_params)
        self.save_defaults_btn = QPushButton("保存为默认设置", page)
        self.save_defaults_btn.setToolTip("写入用户选项（设置 → 用户选项 → 鸟清晰度），用于之后的检测和查看")
        self.save_defaults_btn.clicked.connect(lambda: self.save_defaults_requested.emit(self))
        buttons.addWidget(self.rerun_btn)
        buttons.addWidget(self.save_defaults_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        note = QLabel("重新计算只影响本窗口，不写入 XMP；「保存为默认设置」后，目录/文件检测也按此参数计算。", page)
        note.setWordWrap(True)
        note.setForegroundRole(_ROLE.PlaceholderText)
        layout.addWidget(note)
        layout.addStretch(1)
        self._show_params(self.params)
        scroll = QScrollArea(self.content)  # the form is long: scroll down rather than squeeze
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(_FRAME_NONE)
        scroll.setHorizontalScrollBarPolicy(_SCROLL_OFF)  # fit the panel's width
        return scroll

    def _show_params(self, params: dict) -> None:
        self.params_form.set_params(params)

    def selected_params(self) -> dict:
        return {**self.params_form.params(), "image_source": self.image_source}

    def _rerun_with_params(self) -> None:
        missing = missing_models(self.selected_params())
        if missing and not download_models(self, missing):
            self.params_form.set_params(self.selected_params())  # refresh the 未下载 status
            return
        self.params = self.selected_params()
        self.set_loading()
        self.params_changed.emit(self)

    def _params_status_text(self, result) -> str:
        labels = {key: label for key, label, _tip in ESTIMATOR_CHOICES}
        limit = int(self.params.get("max_birds", 0) or 0)
        parts = [f"当前结果：{labels.get(getattr(result, 'edge_estimator', ''), '—')}",
                 f"上限 {'不限制' if limit <= 0 else f'{limit} 只'}",
                 f"测量 {getattr(result, 'bird_count', 0)} 只鸟",
                 params_summary(self.params),
                 f"算法版本 {getattr(result, 'version', '—')}"]
        if getattr(result, "detector", ""):
            parts.insert(3, f"实际检测模型 {result.detector}")
        return "，".join(parts) + "。"

    # ── data ──────────────────────────────────────────────────────────────
    def set_loading(self, message: str = "") -> None:
        label = SOURCE_LABELS.get(self.image_source, self.image_source)
        self.loading.setText(message or f"正在按「{label}」计算 {os.path.basename(self.path)} 的清晰度过程…\n"
                                        "（全分辨率解码与识别，约 2–5 秒）")
        self.stack.setCurrentWidget(self.loading)

    def set_error(self, message: str) -> None:
        self.loading.setText(f"无法生成清晰度计算过程：\n{message}")
        self.stack.setCurrentWidget(self.loading)

    def _on_source_changed(self, index: int) -> None:
        source = self.source_combo.itemData(index)
        if source and source != self.image_source:
            self.image_source = source
            self.params = {**self.params, "image_source": source}
            self.set_loading()
            self.source_changed.emit(self, source)

    def set_trace(self, trace) -> None:
        self.trace = trace
        result = trace.result
        self.params_status.setText(self._params_status_text(result))
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
        stage_s = getattr(result, "stage_s", None) if result is not None else None
        if stage_s:
            from bird_sharpness.timing import stage_summary

            self.summary.setToolTip("分阶段耗时\n" + stage_summary(stage_s, result.elapsed_s, sep="\n"))
            self.timing.setText("分阶段耗时：" + stage_summary(stage_s, result.elapsed_s))
            self.timing.setVisible(True)
        else:
            self.summary.setToolTip("")
            self.timing.setText("")
            self.timing.setVisible(False)
        multi = len(trace.birds) > 1
        self.bird_combo.blockSignals(True)
        self.bird_combo.clear()
        if multi:
            self.bird_combo.addItem("全部鸟（逐只）", ALL_BIRDS)
        for bird in trace.birds:
            self.bird_combo.addItem(bird.label, bird.index)
        self.bird_combo.setCurrentIndex(0 if trace.birds else -1)
        self.bird_combo.blockSignals(False)
        self.bird_combo.setVisible(multi)
        self.bird_label.setVisible(multi)
        self._load_steps(trace.steps_all() if multi else trace.steps_for(None), keep_key=None)
        self.stack.setCurrentWidget(self.content)
        QTimer.singleShot(0, lambda: self.go(0, force=True))
        self._update_chain()

    def _update_chain(self) -> None:
        """First trace: rebuild the saved model chain; later traces: rerun what depends on them."""
        if self._chain_source is None:
            self._chain_source = self.image_source
            store = getattr(self, "chain_store", None)
            if store is not None:
                self.preview_host.store = store
                self.preview_host.restore()
            return
        changed, self._chain_source = self.image_source != self._chain_source, self.image_source
        self.preview_host.trace_changed(image_changed=changed)

    def _step_label(self, step) -> str:
        """Title with the bird number when several birds are traced."""
        if step.bird is None or len(getattr(self.trace, "birds", ())) < 2 or step.title.startswith("鸟 #"):
            return step.title
        return f"鸟 #{step.bird + 1} · {step.title}"

    def _load_steps(self, steps, keep_key: Optional[str], keep_bird: Optional[int] = None) -> None:
        self.steps = list(steps)
        multi = len(getattr(self.trace, "birds", ())) > 1
        for button in list(self.chip_group.buttons()):
            self.chip_group.removeButton(button)
        _clear_layout(self.chips_layout)
        for i, step in enumerate(self.steps):
            number = _CIRCLED[i] if i < len(_CIRCLED) else str(i + 1)
            icon = STEP_ICONS.get(step.key, step.title)
            if multi and step.bird is not None:
                icon = f"#{step.bird + 1}{icon}"
            chip = ToggleToolButton(f"{number} {icon}", self.chips_box)
            chip.setToolTip(self._step_label(step))
            chip.clicked.connect(lambda _c=False, k=i: self.go(k))
            self.chip_group.addButton(chip, i)
            self.chips_layout.addWidget(chip)
        self.chips_layout.addStretch(1)
        bar_h = self.chips_scroll.horizontalScrollBar().sizeHint().height()
        self.chips_scroll.setFixedHeight(self.chips_box.sizeHint().height() + bar_h + 2)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(self.steps) - 1))
        self.slider.blockSignals(False)
        self.compare_combo.blockSignals(True)
        self.compare_combo.clear()
        self.compare_combo.addItem("上一步", -1)
        for i, step in enumerate(self.steps):
            self.compare_combo.addItem(f"{i + 1}. {self._step_label(step)}", i)
        self.compare_combo.blockSignals(False)
        if keep_key is not None:
            matches = [i for i, step in enumerate(self.steps) if step.key == keep_key]
            same_bird = [i for i in matches if keep_bird is None or self.steps[i].bird in (None, keep_bird)]
            if same_bird or matches:
                self.index = (same_bird or matches)[0]

    def _on_bird_changed(self, combo_index: int) -> None:
        if self.trace is None or combo_index < 0:
            return
        current = self.steps[self.index] if self.steps else None
        bird_index = self.bird_combo.itemData(combo_index)
        if bird_index == ALL_BIRDS:
            # Land on the bird that was being viewed, at the same step.
            self._load_steps(self.trace.steps_all(), keep_key=getattr(current, "key", None),
                             keep_bird=getattr(current, "bird", None))
        else:
            self._load_steps(self.trace.steps_for(bird_index), keep_key=getattr(current, "key", None))
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
        self.view_a.set_image(self._step_image(step))
        self.view_a.set_caption(f"{index + 1}. {self._step_label(step)}")
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
            self.chips_scroll.ensureWidgetVisible(button, 24, 0)
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
        self.view_b.set_image(self._step_image(step))
        self.view_b.set_caption(f"对照 {i + 1}. {self._step_label(step)}")
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
        self.view_b.set_highlight(self._compare_highlight_box())

    def _step_image(self, step) -> np.ndarray:
        """The step image, with the focal plane tinted red while 焦平面 is on."""
        if not self.peaking_btn.isChecked() or self.trace is None:
            return step.image
        return peaking_overlay(step.image, self.trace.sigma_map(step), float(self.peaking_spin.value()))

    def _on_peaking_threshold(self, value: float) -> None:
        self.peaking_slider.blockSignals(True)
        self.peaking_slider.setValue(int(round(value * PEAKING_SLIDER_SCALE)))
        self.peaking_slider.blockSignals(False)
        self._on_peaking_changed()

    def _on_peaking_changed(self, *_args) -> None:
        for widget in (self.peaking_slider, self.peaking_spin):
            widget.setEnabled(self.peaking_btn.isChecked())
        if not self.steps:
            return
        step = self.steps[self.index]
        state = self.view_a.view_state()
        self.view_a.set_image(self._step_image(step))
        self.view_a.apply_view_state(state)
        self.view_a.set_highlight(None if self._hovered_bird is None else self._hovered_bird.box)
        self._show_compare()
        self._show_legend(step)

    def _on_bird_hovered(self, row) -> None:
        self._hovered_bird = row
        self.view_a.set_highlight(None if row is None else row.box)
        if self.view_b.isVisible():
            self.view_b.set_highlight(self._compare_highlight_box())

    def _compare_highlight_box(self):
        """The hovered bird in the compare step: its own row there (any frame), else the same box
        when both steps share a coordinate frame; ``None`` when it cannot be placed (bird crops)."""
        row = self._hovered_bird
        if row is None or not self.steps:
            return None
        step = self.steps[self._compare_index()]
        same = next((r for r in step.bird_rows if row.bird is not None and r.bird == row.bird), None)
        if same is not None:
            return same.box
        return row.box if step.frame == self.steps[self.index].frame else None

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
        self.step_title.setText(f"步骤 {self.index + 1} / {len(self.steps)} · {self._step_label(step)}")
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
        self.bird_list.set_rows(getattr(step, "bird_rows", None) or [])
        _clear_layout(self.charts_layout)
        for chart in step.charts:
            self.charts_layout.addWidget(TraceChartWidget(chart, self.charts_box))
        self._show_legend(step)

    def _show_legend(self, step) -> None:
        legend = list(step.legend)
        if self.peaking_btn.isChecked():
            legend.append((hex_color(C_PEAKING), f"焦平面：边缘 σ ≤ {self.peaking_spin.value():.2f} px"
                           if self.trace is not None and self.trace.sigma_map(step) is not None
                           else "焦平面：本步骤没有逐像素图"))
        self.legend.setText("<br>".join(
            f'<span style="color:{color}">■</span> {label}' for color, label in legend))

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

    # ── model chain ───────────────────────────────────────────────────────
    def open_model_preview(self, kind: str, model: str):
        """Append a model chain window running ``model`` (see ``model_preview``)."""
        model = model or ("auto" if kind == "detector" else "")
        if not model:
            return None
        return self.preview_host.add_stage(model)

    def _make_room_for_chain(self) -> None:
        """Show the chain and make room for its docked windows (kept widths, ``STAGE_WIDTH`` for new
        ones; floating windows need none): widen this window up to the screen, then take the
        rest from the image views."""
        self.preview_host.setVisible(True)
        sizes = self._body.sizes()
        want = self.preview_host.wanted_width()
        extra = want - sizes[2]
        if extra <= 0:
            return
        screen = self.screen()
        room = screen.availableGeometry().width() - self.frameGeometry().width() if screen else extra
        grow = max(0, min(extra, room))
        if grow:
            self.resize(self.width() + grow, self.height())
        total = sum(sizes) + grow
        views = max(400, total - sizes[1] - want)
        self._body.setSizes([views, sizes[1], max(0, total - views - sizes[1])])
        self.preview_host.lay_out_soon()  # once the area has its new width

    def _analyze_stage(self, stage) -> None:
        """「测清晰度」 in a chain window: its results as birds on a temporary image (new window)."""
        from bird_sharpness.preview import analysis_input

        from .model_preview import circled

        host = self.preview_host
        if host.image is None or stage.result is None or not stage.result.items:
            return
        title = f"模型链 {circled(stage.index + 1)} {stage.model} 的 {len(stage.result.items)} 个结果"
        if stage.analyze_fill:
            title += "（涂灰抠图）"
        try:
            image, given, _region = analysis_input(host.image, stage.result.items, title, fill=stage.analyze_fill)
        except ValueError as exc:
            stage.show_message(str(exc))
            return
        self.analyze_pixels_requested.emit(self, image, given, title)

    def set_given_input(self, image, given, title: str) -> None:
        """This window measures ``given`` on the temporary ``image`` (no decode, no detection)."""
        self.given_input = (image, given)
        self.setWindowTitle(f"清晰度计算过程 - {os.path.basename(self.path)} · {title}（临时图）")
        self.title.setText(f"{os.path.basename(self.path)} · {title}（临时图）")
        self.source_combo.setEnabled(False)
        self.source_combo.setToolTip("临时图来自模型链窗口的结果，不能切换图像来源")

    def _preview_image(self):
        """Worker thread: this window's decode when there is one."""
        if self.given_input is not None:
            return self.given_input[0]
        found = self.image_cache.latest(self.image_source) if getattr(self, "image_cache", None) is not None else None
        if found is not None:
            return found
        from bird_sharpness import analyzer as analyzer_mod

        return analyzer_mod.load_analysis_image(self.path)

    def _preview_boxes(self) -> list:
        result = getattr(getattr(self, "trace", None), "result", None)
        return [tuple(b["box"]) for b in (getattr(result, "birds", None) or []) if b.get("box")]

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.preview_host.shutdown()
        self.closed.emit(self)
        super().closeEvent(event)
