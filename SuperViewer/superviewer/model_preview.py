# -*- coding: utf-8 -*-
"""Model chain: models' raw output on the trace window's photo, one window per model.

The trace window's right edge hosts a :class:`ModelChainHost`: any number of
:class:`ChainStage` windows, docked side by side left to right (each can float).
Every window picks a model (any YOLO detector or SAM model; its parameters switch
with it) and an input:

- 上一窗口的结果: the previous window's results. A detector zooms into each one
  (optionally only the pixels inside its mask, e.g. SAM's cut-out); SAM takes each
  box as its own object. Or 「抠出上一步结果的像素」: the results' pixels become a new
  image (the rest grey) that the window shows and runs its model on once (SAM with
  prompts drawn on it, else the whole new image as one box).
- 计算过程识别到的鸟: the trace's detected birds, used the same way.
- 原图: a detector sees the whole frame or the current view; SAM takes drawn boxes
  and keep/exclude points.

When a window finishes, the next one that takes 上一窗口的结果 runs on the new
results (toolbar 「自动传给下一窗口」), so YOLO → SAM → YOLO combinations can be
compared. The 「预览」 buttons next to the model lists in the 参数 tab append a window
running that model. No sharpness is measured here: only what the models output.

With a store (``model_chain_state.ModelChainStore``, set by the controller) every change
is saved and the next trace window rebuilds the same chain once its trace is shown.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional

from bird_sharpness import model_catalog

from .bird_sharpness_params_form import AnalysisParamsForm
from .bird_sharpness_trace_view import TraceBirdList, TraceImageView
from .qt_compat import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget, pyqtSignal,
)
from app_common.toggle_button import ToggleToolButton

try:
    from PyQt6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer
    from PyQt6.QtGui import QBrush, QColor, QGuiApplication, QPen
    from PyQt6.QtWidgets import (QButtonGroup, QDockWidget, QGraphicsEllipseItem, QGraphicsRectItem,
                                 QGraphicsView, QToolBar, QToolButton)
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer
    from PyQt5.QtGui import QBrush, QColor, QGuiApplication, QPen
    from PyQt5.QtWidgets import (QButtonGroup, QDockWidget, QGraphicsEllipseItem, QGraphicsRectItem,
                                 QGraphicsView, QToolBar, QToolButton)

_BUTTON = getattr(Qt, "MouseButton", Qt)
_LEFT, _RIGHT = _BUTTON.LeftButton, _BUTTON.RightButton
_DASH = getattr(getattr(Qt, "PenStyle", Qt), "DashLine")
_NO_DRAG = getattr(getattr(QGraphicsView, "DragMode", QGraphicsView), "NoDrag")
_SCROLL_DRAG = getattr(getattr(QGraphicsView, "DragMode", QGraphicsView), "ScrollHandDrag")
_CROSS = getattr(getattr(Qt, "CursorShape", Qt), "CrossCursor")
_HORIZONTAL = getattr(getattr(Qt, "Orientation", Qt), "Horizontal")
_RIGHT_AREA = getattr(getattr(Qt, "DockWidgetArea", Qt), "RightDockWidgetArea")
_DELETE_ON_CLOSE = getattr(getattr(Qt, "WidgetAttribute", Qt), "WA_DeleteOnClose")
_SCROLL_OFF = getattr(getattr(Qt, "ScrollBarPolicy", Qt), "ScrollBarAlwaysOff")

DETECTOR, SAM = "detector", "sam"
PAN, BOX, POINTS = "pan", "box", "points"
INPUT_PREVIOUS, INPUT_TRACE, INPUT_IMAGE = "previous", "trace", "image"
USE_CROP, USE_MASK, USE_CUTOUT = "crop", "mask", "cutout"
USE_BOXES = "boxes"  # SAM: each input box its own object
_CUTOUT_LABEL = "抠出上一步结果的像素（一张新图）"
_CUTOUT_TIP = ("抠出上一步结果的像素：把输入结果的像素（有轮廓按轮廓，没有按框）抠成一张新图，其余涂灰、"
               "裁到它们的范围（不外扩），模型在这张新图上运行一次；窗口显示这张新图。")
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_LOG = logging.getLogger(__name__)
STAGE_MIN_WIDTH = 320
STAGE_WIDTH = 360  # the trace window makes this much room per new chain window
C_PROMPT, C_KEEP, C_EXCLUDE = QColor(255, 214, 10), QColor(60, 220, 90), QColor(240, 70, 70)


def model_kind(name: str) -> str:
    """``SAM`` for a SAM model (catalog, else by file name), else ``DETECTOR``."""
    if any(m.name == name for m in model_catalog.SAM_MODELS):
        return SAM
    return SAM if (name or "").lower().startswith(("sam", "mobile_sam")) else DETECTOR


def circled(n: int) -> str:
    """1-based window number as ①②…, plain digits past 20."""
    return _CIRCLED[n - 1] if 1 <= n <= len(_CIRCLED) else f"({n})"


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
        self._auto_fit = False  # fitted and not zoomed since: refit when the window resizes

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

    def fit(self) -> None:
        super().fit()
        self._auto_fit = True

    def zoom_to(self, rect) -> None:
        self._auto_fit = False
        super().zoom_to(rect)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._auto_fit = False
        super().wheelEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - a dock fitted while still tiny refits once laid out
        super().resizeEvent(event)
        if self._auto_fit:
            super().fit()

    def visible_scene_rect(self):
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        return rect.left(), rect.top(), rect.right(), rect.bottom()


class _Bridge(QObject):
    done = pyqtSignal(object, object, object)  # stage (None = image load), generation, result or Exception


class ChainStage(QWidget):
    """One window of the chain: a model, its input, its parameters, the result on the photo."""

    changed = pyqtSignal(object)        # self: model / input switched (title, chain wiring)
    run_requested = pyqtSignal(object)  # self
    move_requested = pyqtSignal(object, int)  # self, -1 / +1
    config_changed = pyqtSignal()       # a parameter changed (the chain is saved)
    analyze_requested = pyqtSignal(object)  # self: measure sharpness on this window's result pixels

    def __init__(self, model: str, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.kind = model_kind(model)
        self.index = 0             # 0-based position in the chain (set by the host)
        self.display = None        # (rgb, scale) shared by the chain
        self.result = None         # PreviewResult of the last run
        self.error: Optional[str] = None
        self.inputs: list = []     # PreviewItems the last run was fed
        self.fed_by = None         # upstream stage of the last run (to notice reordering)
        self.busy = False
        self.pending = False       # run again once the current run / the upstream finishes
        self.generation = 0
        self.input_picked = False  # the user chose the input; else it follows the chain position
        self.preset_input: Optional[str] = None  # a restored window's saved input, applied when placed
        self.width_hint: Optional[int] = None    # docked width to keep (saved, or as last laid out)
        self.cut_base = None      # display frame of the last cut-out (「抠出上一步结果的像素」), else None
        self.cut_region = None    # its region, photo px
        self.boxes: list = []      # manual SAM prompts, image px
        self.points: list = []     # (x, y, keep), image px

        self.setMinimumWidth(STAGE_MIN_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.model_combo = QComboBox(self)
        self.model_combo.setMinimumContentsLength(14)
        self._fill_models(model)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.left_btn, self.right_btn = QToolButton(self), QToolButton(self)
        for button, text, tip, delta in ((self.left_btn, "◀", "前移（在链中更早运行）", -1),
                                         (self.right_btn, "▶", "后移（在链中更晚运行）", 1)):
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(lambda _c=False, d=delta: self.move_requested.emit(self, d))
        top.addWidget(QLabel("模型", self))
        top.addWidget(self.model_combo, 1)
        top.addWidget(self.left_btn)
        top.addWidget(self.right_btn)
        layout.addLayout(top)
        source_row = QHBoxLayout()
        self.input_combo = QComboBox(self)
        self.input_combo.currentIndexChanged.connect(self._on_input_changed)
        source_row.addWidget(QLabel("输入", self))
        source_row.addWidget(self.input_combo, 1)
        layout.addLayout(source_row)

        self.tools_box = QWidget(self)
        tools = QHBoxLayout(self.tools_box)
        tools.setContentsMargins(0, 0, 0, 0)
        self.tool_group = QButtonGroup(self)
        self.tool_buttons = {}
        for mode, label, tip in ((PAN, "平移", "拖动平移、滚轮缩放"), (BOX, "画框", "拖出一个框：框里的物体"),
                                 (POINTS, "点选", "左键：要的部分；右键：不要的部分")):
            button = ToggleToolButton(label, self.tools_box)
            button.setToolTip(tip)
            button.clicked.connect(lambda _c=False, m=mode: self.view.set_mode(m))
            self.tool_group.addButton(button)
            self.tool_buttons[mode] = button
            tools.addWidget(button)
        self.clear_btn = QPushButton("清除提示", self.tools_box)
        self.clear_btn.clicked.connect(self.clear_prompts)
        tools.addStretch(1)
        tools.addWidget(self.clear_btn)
        layout.addWidget(self.tools_box)
        self.view = PromptImageView(self)
        self.view.setMinimumSize(280, 260)
        self.view.box_drawn.connect(self._on_box)
        self.view.point_added.connect(self._on_point)
        layout.addWidget(self.view, 1)

        self.params_stack = QStackedWidget(self)
        self.params_stack.addWidget(self._build_detector_page())
        self.params_stack.addWidget(self._build_sam_page())
        layout.addWidget(self.params_stack)
        row = QHBoxLayout()
        self.run_btn = QPushButton("运行", self)
        self.run_btn.setToolTip("运行这个窗口；开着「自动传给下一窗口」时，后面的窗口接着用新结果运行")
        self.run_btn.clicked.connect(lambda: self.run_requested.emit(self))
        self.status = QLabel("正在载入图像…", self)
        self.status.setWordWrap(True)
        self.analyze_btn = QPushButton("测清晰度", self)
        self.analyze_btn.setToolTip("把这个窗口的结果当作鸟送去清晰度检测：从原图裁出它们周围的一块像素作为临时图，"
                                    "只测结果像素（轮廓，没有轮廓按框），不重新识别；在新的「清晰度计算过程」窗口里显示。")
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.clicked.connect(lambda: self.analyze_requested.emit(self))
        row.addWidget(self.run_btn)
        row.addWidget(self.analyze_btn)
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        self.results = TraceBirdList(self)
        self.results.hovered.connect(lambda r: self.view.set_highlight(None if r is None else r.box))
        layout.addWidget(self.results)
        # the host places it next (``set_position``): the input defaults depend on the chain

    # ── parameter pages ──
    def _build_detector_page(self) -> QWidget:
        page = QWidget(self)
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(6)
        self.scope = QComboBox(page)
        self.scope.addItem("全图", "full")
        self.scope.addItem("当前视图区域", "view")
        self.scope.setToolTip("当前视图区域：先在图上放大到鸟附近再运行，相当于手动做一次放大检测")
        self.use = QComboBox(page)
        self.use.addItem("放大到每个输入框", USE_CROP)
        self.use.addItem("只留输入轮廓内像素", USE_MASK)
        self.use.addItem(_CUTOUT_LABEL, USE_CUTOUT)
        self.use.setToolTip("放大到每个输入框：在原图上每个框（外扩后）里各检测一次。\n"
                            "只留输入轮廓内像素：同上，但轮廓外涂成灰色，看模型能否只凭抠出的部分认出鸟（输入需带轮廓，如 SAM）。\n"
                            + _CUTOUT_TIP)
        self.margin = QSpinBox(page)
        self.margin.setRange(0, 200)
        self.margin.setSingleStep(10)
        self.margin.setValue(30)
        self.margin.setSuffix(" %")
        self.margin.setToolTip("输入框每边向外扩它边长的这个比例，给模型留一点背景")
        self.imgsz = QSpinBox(page)
        self.imgsz.setRange(320, 2048)
        self.imgsz.setSingleStep(32)
        self.imgsz.setValue(640)
        self.imgsz.setSuffix(" px")
        self.min_conf = QSpinBox(page)
        self.min_conf.setRange(1, 95)
        self.min_conf.setValue(10)
        self.min_conf.setSuffix(" %")
        self.min_conf.setToolTip("低于它的结果不显示，也不传给下一窗口（10% = 0.10，可看到弱候选）")
        self.classes = QComboBox(page)
        self.classes.addItem("只看鸟", True)
        self.classes.addItem("全部类别", False)
        self.classes.setToolTip("全部类别：可看到被认成了什么（如 potted plant）")
        self.lift = QCheckBox("画面暗时先提亮", page)
        self.lift.setChecked(True)
        self._detector_rows = {}
        for row, (key, label, widget) in enumerate((
                ("scope", "输入范围", self.scope), ("use", "输入用法", self.use), ("margin", "框外扩", self.margin),
                ("imgsz", "网络输入", self.imgsz), ("min_conf", "置信度下限", self.min_conf),
                ("classes", "类别", self.classes))):
            caption = QLabel(label, page)
            grid.addWidget(caption, row, 0)
            grid.addWidget(widget, row, 1)
            self._detector_rows[key] = (caption, widget)
        grid.addWidget(self.lift, 6, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        for combo in (self.scope, self.use, self.classes):
            combo.currentIndexChanged.connect(lambda _i: self.config_changed.emit())
        self.use.currentIndexChanged.connect(lambda _i: self._on_use_changed())
        for spin in (self.margin, self.imgsz, self.min_conf):
            spin.valueChanged.connect(lambda _v: self.config_changed.emit())
        self.lift.toggled.connect(lambda _c: self.config_changed.emit())
        return page

    def _build_sam_page(self) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        self.sam_use_row = QWidget(page)
        row = QHBoxLayout(self.sam_use_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.sam_use = QComboBox(self.sam_use_row)
        self.sam_use.addItem("每个输入框单独作为一个对象", USE_BOXES)
        self.sam_use.addItem(_CUTOUT_LABEL, USE_CUTOUT)
        self.sam_use.setToolTip(_CUTOUT_TIP + "\n没有画框或点选时，整张新图作为一个框。")
        self.sam_use.currentIndexChanged.connect(lambda _i: self.config_changed.emit())
        self.sam_use.currentIndexChanged.connect(lambda _i: self._on_use_changed())
        row.addWidget(QLabel("输入用法", self.sam_use_row))
        row.addWidget(self.sam_use, 1)
        box.addWidget(self.sam_use_row)
        self.prompt_label = QLabel("", page)
        self.prompt_label.setWordWrap(True)
        box.addWidget(self.prompt_label)
        return page

    def _fill_models(self, keep: str) -> None:
        combo = self.model_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("YOLO 内置（自动：yolo11l-seg 等）", model_catalog.AUTO_DETECTOR)
        for model in model_catalog.DETECTORS:
            combo.addItem("YOLO · " + AnalysisParamsForm._model_text(model), model.name)
        combo.insertSeparator(combo.count())
        for model in model_catalog.SAM_MODELS:
            combo.addItem(AnalysisParamsForm._model_text(model), model.name)
        index = combo.findData(keep)
        if index < 0:  # a model file outside the catalog (e.g. a fine-tuned one)
            combo.addItem(keep + ("" if model_catalog.locate(keep) else "（未找到）"), keep)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    # ── chain position / switching ──
    @property
    def input(self) -> str:
        return self.input_combo.currentData() or INPUT_IMAGE

    def set_position(self, index: int, *, has_previous: bool, has_trace: bool) -> None:
        """Called by the host after adding / moving / removing windows."""
        self.index = index
        keep = self.input_combo.currentData() if self.input_picked else None
        if self.preset_input is not None:
            keep, self.preset_input = self.preset_input, None
        self.input_combo.blockSignals(True)
        self.input_combo.clear()
        if has_previous:
            self.input_combo.addItem(f"上一窗口 {circled(index)} 的结果", INPUT_PREVIOUS)
        self.input_combo.addItem("计算过程识别到的鸟" + ("" if has_trace else "（没有）"), INPUT_TRACE)
        self.input_combo.addItem("原图" + ("（手动提示）" if self.kind == SAM else ""), INPUT_IMAGE)
        if keep is None:  # chain onto the previous window, else the trace's birds for SAM, else the photo
            keep = INPUT_PREVIOUS if has_previous else (INPUT_TRACE if self.kind == SAM and has_trace else INPUT_IMAGE)
        elif keep == INPUT_PREVIOUS and not has_previous:
            keep = INPUT_TRACE if self.kind == SAM and has_trace else INPUT_IMAGE
        self.input_combo.setCurrentIndex(max(0, self.input_combo.findData(keep)))
        self.input_combo.blockSignals(False)
        self._update_controls()

    # ── saved configuration ──
    def config(self) -> dict:
        """What the chain saves for this window (see ``model_chain_state``)."""
        return {"model": self.model, "input": self.input if self.input_picked else None,
                "scope": self.scope.currentData(), "use": self.use.currentData(), "sam_use": self.sam_use.currentData(),
                "margin": int(self.margin.value()),
                "imgsz": int(self.imgsz.value()), "min_conf": int(self.min_conf.value()),
                "birds_only": bool(self.classes.currentData()), "lift": self.lift.isChecked()}

    def apply_config(self, config: dict) -> None:
        """A restored window: parameters now, the input when the host places it."""
        widgets = (self.scope, self.use, self.sam_use, self.classes, self.margin, self.imgsz, self.min_conf, self.lift)
        for widget in widgets:  # quietly: the host places the window (and updates its controls) next
            widget.blockSignals(True)
        for combo, value in ((self.scope, config.get("scope")), (self.use, config.get("use")),
                             (self.sam_use, config.get("sam_use")), (self.classes, config.get("birds_only"))):
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
        for spin, key in ((self.margin, "margin"), (self.imgsz, "imgsz"), (self.min_conf, "min_conf")):
            if isinstance(config.get(key), int):
                spin.setValue(config[key])
        self.lift.setChecked(bool(config.get("lift", True)))
        for widget in widgets:
            widget.blockSignals(False)
        if config.get("input"):
            self.preset_input, self.input_picked = config["input"], True
        if isinstance(config.get("width"), int):
            self.width_hint = config["width"]

    def _on_model_changed(self, _index: int) -> None:
        model = self.model_combo.currentData()
        if not model or model == self.model:
            return
        previous, self.model = self.model, model
        if not self.window_host().ensure_model(model):
            self.model = previous
            self._fill_models(previous)
            return
        self._fill_models(model)  # drop 「未下载」 after a download
        kind = model_kind(model)
        if kind != self.kind:
            self.kind = kind
            text = "原图" + ("（手动提示）" if kind == SAM else "")
            self.input_combo.setItemText(self.input_combo.findData(INPUT_IMAGE), text)
        self._reset("模型已切换。")
        self._update_controls()
        self.changed.emit(self)

    def _on_input_changed(self, _index: int) -> None:
        self.input_picked = True
        self._reset("输入已切换。")
        self._update_controls()
        self.changed.emit(self)

    def window_host(self) -> "ModelChainHost":
        widget = self.parent()
        while widget is not None and not isinstance(widget, ModelChainHost):
            widget = widget.parent()
        return widget

    @property
    def cutout_mode(self) -> bool:
        """Input results cut out as a new image (「抠出上一步结果的像素」)."""
        if self.input == INPUT_IMAGE:
            return False
        return (self.use if self.kind == DETECTOR else self.sam_use).currentData() == USE_CUTOUT

    def _on_use_changed(self) -> None:
        if self.cut_base is not None and not self.cutout_mode:
            self._reset("输入用法已切换，点「运行」。")
        else:
            self.status.setText("输入用法已切换，点「运行」。")
        self._update_controls()

    def _base(self):
        """The image results are drawn on: the cut-out (cut-out runs) or the photo."""
        return self.cut_base if self.cut_base is not None else self.display[0]

    def _update_controls(self) -> None:
        detector, from_image, cut = self.kind == DETECTOR, self.input == INPUT_IMAGE, self.cutout_mode
        self.params_stack.setCurrentIndex(0 if detector else 1)
        for key, (caption, widget) in self._detector_rows.items():
            show = {"scope": from_image or cut, "use": not from_image, "margin": not from_image and not cut}.get(key, True)
            caption.setVisible(show)
            widget.setVisible(show)
        self.sam_use_row.setVisible(not from_image)
        manual = not detector and (from_image or cut)
        self.tools_box.setVisible(manual)
        if manual:
            if self.view.mode == PAN and not self.boxes and not self.points:
                self.tool_buttons[BOX].setChecked(True)
                self.view.set_mode(BOX)
        else:
            self.view.set_mode(PAN)
        self._draw_inputs()

    def auto_runs(self) -> bool:
        """Runs by itself when opened / switched (manual SAM waits for prompts)."""
        return self.kind == DETECTOR or self.input != INPUT_IMAGE or bool(self.boxes or self.points)

    def _view_region(self):
        """The visible part of the view in photo px."""
        scale = self.display[1]
        x1, y1, x2, y2 = self.view.visible_scene_rect()
        return (max(0.0, x1 / scale), max(0.0, y1 / scale), x2 / scale, y2 / scale)

    # ── runs (driven by the host) ──
    def make_job(self, image, inputs) -> Callable:
        """The worker-thread callable for this window's run; ValueError when it cannot run."""
        from bird_sharpness import preview

        if self.kind == SAM:
            if self.cutout_mode:
                params = preview.SamPreview(self.model, tuple(self.boxes), tuple(self.points))
                return lambda: preview.run_sam_cutout(image, params, inputs)
            if self.input == INPUT_IMAGE:
                if not self.boxes and not self.points:
                    raise ValueError("请先画框或点选。")
                params = preview.SamPreview(self.model, tuple(self.boxes), tuple(self.points))
                return lambda: preview.run_sam(image, params)
            model = self.model
            return lambda: preview.run_sam_on(image, model, inputs)
        region = None
        if (self.input == INPUT_IMAGE or self.cutout_mode) and self.scope.currentData() == "view" \
                and self.display is not None:
            region = self._view_region()
        params = preview.DetectorPreview(self.model, region, int(self.imgsz.value()), self.min_conf.value() / 100.0,
                                         bool(self.classes.currentData()), self.lift.isChecked())
        if self.input == INPUT_IMAGE:
            return lambda: preview.run_detector(image, params)
        if self.cutout_mode:
            return lambda: preview.run_detector_cutout(image, params, inputs)
        margin, mask_only = self.margin.value() / 100.0, self.use.currentData() == USE_MASK
        return lambda: preview.run_detector_on(image, params, inputs, margin=margin, mask_only=mask_only)

    def set_display(self, display) -> None:
        self.display = display
        self.view.set_image(display[0])
        self.view.fit()
        self._draw_inputs()
        if not self.auto_runs():
            self.status.setText("画框或点选后点「运行」。")

    def _update_analyze(self) -> None:
        self.analyze_btn.setEnabled(not self.busy and bool(self.result is not None and self.result.items))

    def set_busy(self, busy: bool, inputs=None, fed_by=None) -> None:
        self.busy = busy
        self.run_btn.setEnabled(not busy)
        self._update_analyze()
        if busy:
            self.inputs, self.fed_by = list(inputs or []), fed_by
            self.status.setText("正在运行…")
            self._draw_inputs()

    def show_result(self, result) -> None:
        from bird_sharpness.preview import cutout_display, render

        self.result, self.error = result, None
        self._update_analyze()
        if self.display is None:
            return
        cut = getattr(result, "cutout", None)
        first_cut = cut is not None and cut.region != self.cut_region
        self.cut_base = None if cut is None else cutout_display(self.display[0], self.display[1], cut)
        self.cut_region = None if cut is None else cut.region
        img, rows = render(self._base(), self.display[1], result.items)
        self.view.set_image(img)
        if first_cut:  # a new cut-out: show it whole
            self.view.zoom_to(tuple(v * self.display[1] for v in cut.region))
        self._draw_inputs()
        self.results.set_rows(rows)
        lift = "" if result.gamma is None else f"，提亮 γ {result.gamma:.2f}"
        device = f"{result.device or '—'} · {result.elapsed_s:.2f} s · " if result.model else ""
        self.status.setText(f"{result.model + ' · ' if result.model else ''}{device}"
                            f"{result.input_desc}{lift} · {len(result.items)} 个结果")

    def show_error(self, message: str) -> None:
        self.error, self.result = message, None
        self._update_analyze()
        self.results.set_rows([])
        if self.display is not None:
            self.view.set_image(self._base())
            self._draw_inputs()
        self.status.setText(f"失败：{message}")

    def show_message(self, message: str) -> None:
        self.status.setText(message)

    def _reset(self, why: str) -> None:
        self.generation += 1  # a run still in flight is dropped
        self.result, self.error, self.inputs = None, None, []
        self._update_analyze()
        self.cut_base = self.cut_region = None
        self.results.set_rows([])
        if self.display is not None:
            self.view.set_image(self.display[0])
        self.status.setText(why)

    # ── prompts / inputs drawn on the view ──
    def _scale(self) -> float:
        return self.display[1] if self.display else 1.0

    def _on_box(self, box) -> None:
        s = self._scale()
        self.boxes = [tuple(v / s for v in box)]  # a drawn box is one object (replaces earlier boxes)
        self._draw_inputs()

    def _on_point(self, x: float, y: float, keep: bool) -> None:
        s = self._scale()
        self.points.append((x / s, y / s, keep))
        self._draw_inputs()

    def clear_prompts(self) -> None:
        self.boxes, self.points = [], []
        self._draw_inputs()

    def _draw_inputs(self) -> None:
        """Dashed yellow: what this window is fed (manual prompts, or the input results' boxes)."""
        s = self._scale()
        if self.kind == SAM and (self.input == INPUT_IMAGE or self.cutout_mode):
            boxes, points = self.boxes, self.points
            keep = sum(1 for p in points if p[2])
            self.prompt_label.setText(
                f"提示：{len(boxes)} 个框，{keep} 个保留点，{len(points) - keep} 个排除点。"
                + ("在抠出的新图上画框或点选；没有提示时整张新图作为一个框。" if self.cutout_mode else "")
                + "画框：拖动；点选：左键保留、右键排除；点选时最多配合一个框。")
        else:
            boxes, points = [item.box for item in self.inputs], []
            self.prompt_label.setText("每个输入框单独作为一个对象（虚线框为本窗口收到的输入）。")
        self.view.set_prompts([tuple(v * s for v in b) for b in boxes], [(x * s, y * s, k) for x, y, k in points])


def on_screen(rect: "QRect") -> "QRect":
    """``rect`` moved / shrunk onto the screen under its centre (else the primary screen), so a
    floating window saved on a monitor that is gone comes back where it can be seen."""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    if screen is None:
        return rect
    area = screen.availableGeometry()
    w, h = min(rect.width(), area.width()), min(rect.height(), area.height())
    x = min(max(rect.x(), area.left()), area.left() + area.width() - w)
    y = min(max(rect.y(), area.top()), area.top() + area.height() - h)
    return QRect(x, y, w, h)


class _ChainDock(QDockWidget):
    """A chain window's dock (scrolls when short); closing it removes the window from the chain."""

    closed = pyqtSignal(object)  # the stage
    geometry_changed = pyqtSignal()  # resized, or moved while floating

    def __init__(self, stage: ChainStage, parent) -> None:
        super().__init__("", parent)
        self.stage = stage
        scroll = QScrollArea(self)
        scroll.setWidget(stage)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(_SCROLL_OFF)
        scroll.setFrameShape(getattr(getattr(QScrollArea, "Shape", QScrollArea), "NoFrame"))
        scroll.setMinimumWidth(STAGE_MIN_WIDTH + scroll.verticalScrollBar().sizeHint().width())
        self.setWidget(scroll)
        self.setAttribute(_DELETE_ON_CLOSE, True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.closed.emit(self.stage)
        super().closeEvent(event)

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().moveEvent(event)
        if self.isFloating():
            self.geometry_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.geometry_changed.emit()  # docked: its width is saved too


class ModelChainHost(QMainWindow):
    """The chain's windows (docks, left to right = run order), the shared photo, the runs.

    ``image_provider`` (worker thread) returns the trace window's ``AnalysisImage``;
    ``boxes_provider`` (GUI thread) the trace's detected birds in image px;
    ``download(parent, names)`` offers missing models. Model runs are serialized on one
    lock (one model on the GPU at a time); results of a window that was closed,
    switched or rerun meanwhile are dropped.
    """

    emptied = pyqtSignal()
    stage_added = pyqtSignal()
    analyze_requested = pyqtSignal(object)  # stage: 测清晰度 on its result pixels
    SAVE_DELAY_MS = 300

    def __init__(self, image_provider: Callable, boxes_provider: Callable[[], list],
                 download: Callable[..., bool], parent=None, *, store=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(getattr(getattr(Qt, "WindowType", Qt), "Widget"))
        self.setDockNestingEnabled(True)
        self._image_provider, self._boxes_provider, self._download = image_provider, boxes_provider, download
        self.stages: List[ChainStage] = []
        self._docks: Dict[ChainStage, _ChainDock] = {}
        self.image = None
        self.display = None
        self._loading = False
        self._load_generation = 0  # a newer image (another source) drops an older load
        self._alive = True
        self._lock = threading.Lock()
        self._threads: List[threading.Thread] = []
        self._bridge = _Bridge()
        self._bridge.done.connect(self._on_done)
        self.store = store          # ModelChainStore: save every change (None = not saved)
        self._restoring = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_now)
        self._layout_timer = QTimer(self)  # widths are applied once the docks' layout has settled
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self.lay_out)

        bar = QToolBar("模型链", self)
        bar.setMovable(False)
        self.add_action = bar.addAction("＋ 添加窗口")
        self.add_action.setToolTip("在链末尾加一个窗口（默认与上一窗口换一类模型：YOLO 后接 SAM，SAM 后接 YOLO）")
        self.add_action.triggered.connect(lambda: self.add_stage())
        self.run_all_action = bar.addAction("运行整条链")
        self.run_all_action.setToolTip("从第一个窗口起依次运行，每个窗口的结果交给下一个窗口")
        self.run_all_action.triggered.connect(self.run_all)
        self.auto_check = ToggleToolButton("自动传给下一窗口", bar)
        self.auto_check.setFocusPolicy(getattr(getattr(Qt, "FocusPolicy", Qt), "NoFocus"))  # like the toolbar's buttons
        self.auto_check.setChecked(True)
        self.auto_check.setToolTip("一个窗口有了新结果，就让后面输入为「上一窗口的结果」的窗口接着运行")
        self.auto_check.toggled.connect(lambda _c: self.save_soon())
        bar.addWidget(self.auto_check)
        bar.addSeparator()
        self.close_all_action = bar.addAction("全部关闭")
        self.close_all_action.setToolTip("关闭所有窗口（下次打开计算过程窗口时也不再恢复）")
        self.close_all_action.triggered.connect(self.close_all)
        self.note = QLabel("", bar)
        bar.addWidget(self.note)
        self.addToolBar(bar)
        self.toolbar = bar
        # Widgets added to a toolbar keep the default font; on macOS the action buttons are smaller.
        font = bar.widgetForAction(self.add_action).font()
        for widget in (self.auto_check, self.note):
            widget.setFont(font)

    # ── windows ──
    def ensure_model(self, model: str) -> bool:
        return model == model_catalog.AUTO_DETECTOR or model_catalog.locate(model) is not None \
            or bool(self._download(self, [model]))

    def default_model(self) -> str:
        """For 「＋ 添加窗口」: the other kind than the last window, an installed model first."""
        want = SAM if self.stages and self.stages[-1].kind == DETECTOR else DETECTOR
        if want == DETECTOR:
            return model_catalog.AUTO_DETECTOR
        installed = [m.name for m in model_catalog.SAM_MODELS if model_catalog.locate(m.name)]
        return installed[0] if installed else "sam2.1_t.pt"

    def add_stage(self, model: Optional[str] = None, config: Optional[dict] = None) -> Optional[ChainStage]:
        model = model or self.default_model()
        if not self.ensure_model(model):
            return None
        if not self._restoring:
            self._capture_widths()  # the windows already open keep their widths
        stage = ChainStage(model, self)
        if config:
            stage.apply_config(config)
        stage.changed.connect(self._on_stage_changed)
        stage.config_changed.connect(self.save_soon)
        stage.analyze_requested.connect(self.analyze_requested)
        stage.run_requested.connect(self.request_run)
        stage.move_requested.connect(self.move_stage)
        dock = _ChainDock(stage, self)
        dock.closed.connect(self._on_dock_closed)
        dock.topLevelChanged.connect(lambda _floating: self.save_soon())
        dock.geometry_changed.connect(self.save_soon)
        docked = [self._docks[s] for s in self.stages if not self._docks[s].isFloating()]
        if docked:
            self.splitDockWidget(docked[-1], dock, _HORIZONTAL)
        else:
            self.addDockWidget(_RIGHT_AREA, dock)
        self.stages.append(stage)
        self._docks[stage] = dock
        if config and config.get("floating"):
            self._float(dock, config.get("geometry"))
        self._renumber()
        self.lay_out()
        self.lay_out_soon()
        self.stage_added.emit()
        self.save_soon()
        if self.display is not None:
            stage.set_display(self.display)
            if stage.auto_runs():
                self.request_run(stage)
        else:
            stage.pending = stage.auto_runs()
            self._load_image()
        return stage

    def move_stage(self, stage: ChainStage, delta: int) -> None:
        i = self.stages.index(stage)
        j = i + delta
        if not 0 <= j < len(self.stages):
            return
        self.stages[i], self.stages[j] = self.stages[j], self.stages[i]
        self._capture_widths()  # widths travel with their windows
        for s in self.stages:
            dock = self._docks[s]
            if not dock.isFloating():
                self.removeDockWidget(dock)
        previous = None
        for s in self.stages:
            dock = self._docks[s]
            if dock.isFloating():
                continue
            if previous is None:
                self.addDockWidget(_RIGHT_AREA, dock)
            else:
                self.splitDockWidget(previous, dock, _HORIZONTAL)
            dock.show()
            previous = dock
        self._renumber()
        self.lay_out()
        self.lay_out_soon()
        self._rewired()
        self.save_soon()

    def _float(self, dock: _ChainDock, geometry) -> None:
        """A restored floating window: float it again at its saved place (kept on a screen)."""
        dock.setFloating(True)
        if geometry:
            dock.setGeometry(on_screen(QRect(*geometry)))
        dock.show()

    def docked_count(self) -> int:
        return sum(1 for s in self.stages if not self._docks[s].isFloating())

    def _docked(self) -> List[ChainStage]:
        return [s for s in self.stages if not self._docks[s].isFloating()]

    def _laid_out_width(self, stage: ChainStage) -> Optional[int]:
        """The docked window's real width, once it has been laid out (None before)."""
        dock = self._docks[stage]
        return dock.width() if dock.isVisible() and dock.width() >= STAGE_MIN_WIDTH else None

    def _capture_widths(self) -> None:
        for stage in self._docked():
            stage.width_hint = self._laid_out_width(stage) or stage.width_hint

    def wanted_width(self) -> int:
        """Room the docked windows want: their kept widths, ``STAGE_WIDTH`` for new ones."""
        return max(STAGE_WIDTH, sum(s.width_hint or STAGE_WIDTH for s in self._docked()))

    def lay_out_soon(self) -> None:
        """``lay_out`` after pending layout changes (new docks, the area resized) have settled."""
        self._layout_timer.start(0)

    def lay_out(self) -> None:
        """Give each docked window its kept width (``STAGE_WIDTH`` when it has none)."""
        docked = self._docked()
        if docked:
            self.resizeDocks([self._docks[s] for s in docked], [s.width_hint or STAGE_WIDTH for s in docked],
                             _HORIZONTAL)

    def _on_dock_closed(self, stage: ChainStage) -> None:
        if stage not in self.stages:
            return
        stage.generation += 1
        self.stages.remove(stage)
        self._docks.pop(stage, None)
        self._renumber()
        self._rewired()
        self.save_soon()
        if not self.stages:
            self.emptied.emit()

    def close_all(self) -> None:
        for stage in list(self.stages):
            self._docks[stage].close()

    def shutdown(self) -> None:
        """The trace window is closing: save the chain as it is, close every window, drop late results."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.save_now()
        self._alive = False
        self.close_all()

    # ── saved chain ──
    def state(self) -> dict:
        stages = []
        for stage in self.stages:
            config, dock = stage.config(), self._docks[stage]
            if dock.isFloating():
                g = dock.geometry()
                config.update(floating=True, geometry=[g.x(), g.y(), g.width(), g.height()])
            else:
                config["width"] = self._laid_out_width(stage) or stage.width_hint
            stages.append(config)
        return {"auto": self.auto_check.isChecked(), "stages": stages}

    def save_soon(self) -> None:
        if self.store is not None and self._alive and not self._restoring:
            self._save_timer.start(self.SAVE_DELAY_MS)

    def save_now(self) -> None:
        if self.store is None or not self._alive:
            return
        try:
            self.store.save(self.state())
        except OSError as exc:  # a read-only profile: the chain just is not remembered
            _LOG.warning("model chain not saved: %s", exc)

    def restore(self) -> int:
        """Rebuild the saved chain (windows whose model is gone are skipped); returns the count."""
        if self.store is None:
            return 0
        state = self.store.load()
        skipped = []
        self._restoring = True
        try:
            self.auto_check.setChecked(state["auto"])
            for config in state["stages"]:
                model = config["model"]
                if model != model_catalog.AUTO_DETECTOR and model_catalog.locate(model) is None:
                    skipped.append(model)
                    continue
                self.add_stage(model, config)
        finally:
            self._restoring = False
        self.note.setText(f"未恢复（模型未下载）：{'、'.join(skipped)}" if skipped else "")
        return len(self.stages)

    def trace_changed(self, image_changed: bool = False) -> None:
        """The trace window has a new trace: rerun what depends on it (all of it for another image)."""
        if not self.stages:
            if image_changed:
                self.image = self.display = None
                self._loading = False
            return
        self._renumber()  # 「计算过程识别到的鸟（没有）」
        if image_changed:
            ran = [s for s in self.stages if s.result is not None or s.error is not None or s.busy]
            self.image = self.display = None
            self._loading = False
            for s in self.stages:
                s.generation += 1
                s.set_busy(False)
                s.pending = s in ran
                s.show_message("正在载入新图像…")
            self._load_image()
            return
        for s in self.stages:
            if s.input == INPUT_TRACE and (s.result is not None or s.error is not None or s.busy):
                if self.auto_check.isChecked():
                    self.request_run(s)
                else:
                    s.show_message("计算过程已更新，点「运行」用新的鸟框。")

    def _renumber(self) -> None:
        has_trace = bool(self._boxes_provider())
        for i, stage in enumerate(self.stages):
            stage.set_position(i, has_previous=i > 0, has_trace=has_trace)
            stage.left_btn.setEnabled(i > 0)
            stage.right_btn.setEnabled(i < len(self.stages) - 1)
            self._docks[stage].setWindowTitle(f"{circled(i + 1)} {stage.model}")

    def _on_stage_changed(self, stage: ChainStage) -> None:
        self.save_soon()
        self._renumber()
        self._mark_downstream_stale(stage)
        if stage.auto_runs():
            self.request_run(stage)

    def _rewired(self) -> None:
        """After a move / close: rerun windows whose previous window changed (once per chain)."""
        changed = [s for s in self.stages if s.input == INPUT_PREVIOUS and s.fed_by is not self.upstream_of(s)
                   and (s.result is not None or s.error is not None or s.busy)]
        for stage in changed:
            if self.upstream_of(stage) in changed:
                continue
            if self.auto_check.isChecked():
                self.request_run(stage)
            else:
                stage.show_message("上一窗口已变化，点「运行」更新。")

    def _mark_downstream_stale(self, stage: ChainStage) -> None:
        if self.auto_check.isChecked() and stage.auto_runs():
            return  # they rerun once this window has its new results
        for down in self.stages[self.stages.index(stage) + 1:]:
            if down.input != INPUT_PREVIOUS:
                break
            if down.result is not None:
                down.show_message("上一窗口已变化，点「运行」更新。")

    def upstream_of(self, stage: ChainStage) -> Optional[ChainStage]:
        i = self.stages.index(stage)
        return self.stages[i - 1] if i > 0 else None

    def downstream_of(self, stage: ChainStage) -> Optional[ChainStage]:
        i = self.stages.index(stage)
        return self.stages[i + 1] if i + 1 < len(self.stages) else None

    # ── image ──
    def _load_image(self) -> None:
        if self._loading or self.display is not None:
            return
        self._loading = True
        provider = self._image_provider

        def load():
            from bird_sharpness.preview import display_image

            image = provider()
            return image, display_image(image)

        self._load_generation += 1
        self._spawn(None, self._load_generation, load)

    # ── runs ──
    def run_all(self) -> None:
        """Run the first window; each finished window hands its results on (also when auto is off)."""
        for stage in self.stages:
            stage.pending = stage.input == INPUT_PREVIOUS
        if self.stages:
            self.stages[0].pending = False
            for stage in self.stages[1:]:
                if stage.input != INPUT_PREVIOUS:
                    self.request_run(stage)  # does not depend on the chain: run on its own
            self.request_run(self.stages[0])

    def request_run(self, stage: ChainStage) -> None:
        if not self._alive or stage not in self.stages:
            return
        if self.display is None:
            stage.pending = True
            self._load_image()
            return
        if stage.busy:
            stage.pending = True
            return
        inputs, fed_by = [], None
        if stage.input == INPUT_PREVIOUS:
            up = fed_by = self.upstream_of(stage)
            if up is None:
                return
            if up.busy or (up.result is None and up.error is None and up.auto_runs()):
                stage.pending = True
                stage.show_message(f"等待上一窗口 {circled(up.index + 1)}…")
                if not up.busy:
                    self.request_run(up)
                return
            if up.error is not None:
                stage.show_error(f"上一窗口 {circled(up.index + 1)} 运行失败")
                return
            if up.result is None:
                stage.show_message(f"上一窗口 {circled(up.index + 1)} 还没有结果。")
                return
            inputs = list(up.result.items)
            if not inputs:
                self._finish(stage, self._empty_result("上一窗口没有结果"), fed_by)
                return
        elif stage.input == INPUT_TRACE:
            from bird_sharpness.preview import items_from_boxes

            inputs = items_from_boxes(self._boxes_provider())
            if not inputs:
                self._finish(stage, self._empty_result("计算过程没有识别到鸟"), None)
                return
        try:
            job = stage.make_job(self.image, inputs)
        except ValueError as exc:
            stage.pending = False
            stage.show_message(str(exc))
            return
        stage.pending = False
        stage.generation += 1
        stage.set_busy(True, inputs, fed_by)
        self._spawn(stage, stage.generation, job)

    @staticmethod
    def _empty_result(why: str):
        from bird_sharpness.preview import PreviewResult

        return PreviewResult("", "", 0.0, why, [])

    def _spawn(self, stage: Optional[ChainStage], generation: int, work: Callable) -> None:
        bridge, lock = self._bridge, self._lock

        def run():
            try:
                with lock:  # one model at a time
                    result = work()
            except Exception as exc:  # shown in the window's status line
                result = exc
            bridge.done.emit(stage, generation, result)

        thread = threading.Thread(target=run, name="model-chain", daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def _on_done(self, stage, generation, result) -> None:
        if not self._alive:
            return
        if stage is None:  # the photo
            if generation != self._load_generation:
                return
            self._loading = False
            if isinstance(result, Exception):
                for s in self.stages:
                    s.show_error(f"载入图像失败：{result}")
                return
            self.image, self.display = result
            waiting = [s for s in self.stages if s.pending]
            for s in self.stages:
                s.set_display(self.display)
            for s in waiting:  # a window chained onto a waiting one runs when that one finishes
                if s.input != INPUT_PREVIOUS or self.upstream_of(s) not in waiting:
                    s.pending = False
                    self.request_run(s)
            return
        if stage not in self.stages or generation != stage.generation:
            return
        stage.set_busy(False)
        if isinstance(result, Exception):
            stage.show_error(str(result))
            for down in self.stages[self.stages.index(stage) + 1:]:
                if down.input != INPUT_PREVIOUS:
                    break
                down.pending = False
                down.show_error(f"上一窗口 {circled(stage.index + 1)} 运行失败")
            return
        self._finish(stage, result, stage.fed_by)

    def _finish(self, stage: ChainStage, result, fed_by) -> None:
        stage.fed_by = fed_by
        stage.show_result(result)
        if stage.pending:  # its input changed while it ran
            stage.pending = False
            self.request_run(stage)
            return
        down = self.downstream_of(stage)
        if down is None or down.input != INPUT_PREVIOUS:
            return
        if down.pending or self.auto_check.isChecked():
            down.pending = False
            self.request_run(down)
        elif down.result is not None or down.error is not None:
            down.show_message("上一窗口已更新，点「运行」用新结果。")
