# -*- coding: utf-8 -*-
"""Bird sharpness analysis parameters form, shared by 设置 → 鸟清晰度 (batch detection)
and the trace window's 参数 tab (one window), so both edit the same
``bird_sharpness.params.AnalysisParams`` the same way. Also downloads models.
"""
from __future__ import annotations

import threading
from typing import Iterable, Optional

from bird_sharpness import model_catalog
from bird_sharpness.image_source import SOURCE_DENOISED, SOURCE_JPEG, SOURCE_LABELS, SOURCE_RAW
from bird_sharpness.metrics import MF_MIN_TILES, TileOptions
from bird_sharpness.params import (ENH_MANUAL, ENH_NOBIRD, ENH_OFF, PIXELS_BOX, PIXELS_OUTLINE, SAM_SCOPE_ALL,
                                   SAM_SCOPE_RECHECKED, AnalysisParams)

from .qt_compat import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
    QWidget, pyqtSignal,
)

try:
    from PyQt6.QtCore import QObject, Qt
    from PyQt6.QtGui import QFont, QPalette
    from PyQt6.QtWidgets import QProgressDialog
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QObject, Qt
    from PyQt5.QtGui import QFont, QPalette
    from PyQt5.QtWidgets import QProgressDialog

_ROLE = getattr(QPalette, "ColorRole", QPalette)
_TOOLTIP_ROLE = getattr(getattr(Qt, "ItemDataRole", Qt), "ToolTipRole")
_WINDOW_MODAL = getattr(getattr(Qt, "WindowModality", Qt), "WindowModal")
_ADJUST_MIN = getattr(getattr(QComboBox, "SizeAdjustPolicy", QComboBox), "AdjustToMinimumContentsLengthWithIcon")


def _narrow(combo: QComboBox, chars: int = 14) -> QComboBox:
    """Long model names must not force the form wider than a side panel (full text in the popup)."""
    combo.setSizeAdjustPolicy(_ADJUST_MIN)
    combo.setMinimumContentsLength(chars)
    return combo

# Edge estimator choices (bird_sharpness.metrics.EDGE_ESTIMATORS keys).
ESTIMATOR_CHOICES = (
    ("standard", "标准（默认）",
     "最强 30 条边缘（或前 5%）的模糊半径中位数。清晰/可用/失焦门槛按它与人工判断标定。"),
    ("dense", "密集（实验性）",
     "至少 60 条最强边缘，取第 40 百分位。小鸟头部边缘少时判定更稳，整体不偏移；"
     "但在已标注照片上有 2/15 张在清晰与可用之间对调，结果仅供对比。"),
)
ENH_CHOICES = ((ENH_OFF, "关闭（默认）"), (ENH_MANUAL, "仅手动对焦的照片"), (ENH_NOBIRD, "所有没找到鸟的照片"))
SAM_SCOPE_CHOICES = ((SAM_SCOPE_ALL, "全部鸟（默认；每只鸟都经 SAM 抠一次，鸟群较慢）"),
                     (SAM_SCOPE_RECHECKED, "仅复检/增强找到的鸟（更快）"))
PIXELS_CHOICES = ((PIXELS_OUTLINE, "抠出的鸟体像素（轮廓内，默认）"), (PIXELS_BOX, "整个鸟框区域"))
# Which pixels are measured (AnalysisParams.image_source); SuperViewer's user option defaults to the JPEG.
SOURCE_CHOICES = (
    (SOURCE_JPEG, "相机内嵌 JPEG", "RAW 里相机生成的全尺寸 JPEG（JPEG 照片就是它本身）：解码快，"
                                 "但经过机内锐化/降噪/压缩；门槛按 RAW 解码标定，结果会有偏移。算法版本带 -jpeg 后缀。"),
    (SOURCE_RAW, "RAW 解码", "LibRaw 全分辨率解码：清晰度门槛按它标定，最可靠，但每张多花约 1–2 秒。"),
    (SOURCE_DENOISED, "降噪成片", "按当前降噪输出设置查找降噪后的成片。批量检测时没有成片的照片记为失败（不会自动降噪），"
                                "计算过程窗口里会先自动降噪。算法版本带 -denoised 后缀。"),
)


def _heading(text: str, parent) -> QLabel:
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setBold(True)
    label.setFont(font)
    return label


def _spin(parent, low, high, step, suffix, tip, special: str = "") -> QSpinBox:
    box = QSpinBox(parent)
    box.setRange(low, high)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setToolTip(tip)
    if special:
        box.setSpecialValueText(special)
    return box


def _grid(parent, rows, expand_fields: bool) -> QGridLayout:
    grid = QGridLayout()
    grid.setVerticalSpacing(8)
    for row, (label, widget) in enumerate(rows):
        if label is None:
            grid.addWidget(widget, row, 0, 1, 2)
        else:
            grid.addWidget(QLabel(label, parent), row, 0)
            grid.addWidget(widget, row, 1)
    grid.setColumnStretch(1 if expand_fields else 2, 1)  # compact fields: the empty 3rd column stretches
    return grid


class TileParamsForm(QWidget):
    """No-bird tiling controls (``TileOptions``)."""

    def __init__(self, parent=None, *, expand_fields: bool = True) -> None:
        super().__init__(parent)
        self.full_tile = _spin(self, 128, 4096, 64, " px", "没有鸟、也没有相机焦点框时，全图按此边长分块，汇总全部分块的中位数。")
        self.mf_center = QCheckBox("手动对焦时测中心焦平面（不是鸟）", self)
        self.mf_center.setToolTip("没有鸟、没有焦点框且相机记录为手动对焦时启用：焦平面是画面里最清晰的部分，"
                                  "只取最清晰的分块，前景/背景虚化不会拉低结果。鸟可能并不在焦平面上。")
        self.mf_center_percent = _spin(self, 10, 100, 5, " %", "中心区域每边占画幅的比例（100% = 整个画幅）。")
        self.mf_tile = _spin(self, 32, 2048, 32, " px", "中心区域的分块边长。越小越能只框住焦平面，但每块边缘越少。")
        self.mf_sharpest = _spin(self, 1, 100, 5, " %",
                                 f"按分块模糊半径排序，取最清晰的这部分有效分块（至少 {MF_MIN_TILES} 块）汇总中位数。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(_grid(self, (("全图分块（无焦点框）", self.full_tile), (None, self.mf_center),
                                      ("中心区域（每边占画幅）", self.mf_center_percent), ("中心分块", self.mf_tile),
                                      ("取最清晰的分块", self.mf_sharpest)), expand_fields))
        self.mf_center.toggled.connect(self._update_enabled)
        self.set_params(TileOptions().as_params())

    def _update_enabled(self, *_args) -> None:
        for widget in (self.mf_center_percent, self.mf_tile, self.mf_sharpest):
            widget.setEnabled(self.mf_center.isChecked())

    def set_params(self, params: dict) -> None:
        o = TileOptions.from_params(params)
        self.full_tile.setValue(o.full_tile)
        self.mf_center.setChecked(o.mf_center)
        self.mf_center_percent.setValue(o.mf_center_percent)
        self.mf_tile.setValue(o.mf_tile)
        self.mf_sharpest.setValue(o.mf_sharpest_percent)
        self._update_enabled()

    def params(self) -> dict:
        return TileOptions(int(self.full_tile.value()), self.mf_center.isChecked(), int(self.mf_center_percent.value()),
                           int(self.mf_tile.value()), int(self.mf_sharpest.value())).as_params()


def tile_summary(params: dict) -> str:
    o = TileOptions.from_params(params)
    mf = (f"手动对焦焦平面 {o.mf_center_percent}% · {o.mf_tile} px · 最清晰 {o.mf_sharpest_percent}%"
          if o.mf_center else "手动对焦不单独处理")
    return f"全图 {o.full_tile} px；{mf}"


def params_summary(params: dict) -> str:
    """One line for status texts: models, enhanced search, tiling."""
    p = AnalysisParams.from_params(params)
    source = SOURCE_LABELS.get(p.image_source, p.image_source)
    detector = "内置（自动）" if p.detector == "auto" else p.detector
    sam = "关" if not p.sam_model else f"{p.sam_model}（{dict(SAM_SCOPE_CHOICES)[p.sam_scope]}）"
    pixels = ("轮廓内" if p.bird_pixels == PIXELS_OUTLINE else "整个鸟框") + ("，鸟以外涂灰" if p.grey_fill else "")
    if p.min_bird_side:
        pixels += f"，忽略长边 < {p.min_bird_side} px 的鸟"
    e = p.enhanced
    enh = ("关" if e.mode == ENH_OFF else
           f"{dict(ENH_CHOICES)[e.mode]} · 区域 {e.region_percent}% · {e.grid}×{e.grid} 窗口 · 输入 {e.imgsz} px"
           f" · 门槛 {e.min_conf_percent / 100:.2f}")
    return f"图像 {source}；检测模型 {detector}；SAM 精修 {sam}；测量像素 {pixels}；增强找鸟 {enh}；分块 {tile_summary(params)}"


def missing_models(params: dict) -> list:
    """Model files the parameters need that are not installed (catalog names only)."""
    p = AnalysisParams.from_params(params)
    names = [n for n in (p.detector if p.detector != "auto" else "", p.sam_model) if n]
    return [n for n in names if model_catalog.locate(n) is None]


class _DownloadBridge(QObject):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(str)  # "" = ok, else the error


def download_models(parent, names: Iterable[str]) -> bool:
    """Ask, then download ``names`` (catalog models) with a cancellable progress dialog.

    The download runs on a worker thread that is always joined before returning,
    so nothing keeps running after the dialog closes. Returns True when all succeeded.
    """
    models = [m for m in (model_catalog.catalog_model(n) for n in names) if m is not None]
    if not models:
        return False
    total_mb = sum(m.megabytes for m in models)
    lines = "\n".join(f"· {m.name}  {m.megabytes:g} MB" for m in models)
    answer = QMessageBox.question(
        parent, "下载模型",
        f"将下载以下模型（共约 {total_mb:g} MB）：\n{lines}\n\n来源：{model_catalog.RELEASE_URL}\n"
        f"保存到：{model_catalog.user_model_dir()}\n\n继续吗？")
    yes = getattr(getattr(QMessageBox, "StandardButton", QMessageBox), "Yes")
    if answer != yes:
        return False
    for model in models:
        dialog = QProgressDialog(f"正在下载 {model.name}…", "取消", 0, 1000, parent)
        dialog.setWindowTitle("下载模型")
        dialog.setWindowModality(_WINDOW_MODAL)
        dialog.setMinimumDuration(0)
        bridge = _DownloadBridge()
        cancel = threading.Event()
        outcome = {"error": None}

        def on_progress(done, total, d=dialog):
            d.setMaximum(1000)
            d.setValue(int(1000 * done / total) if total else 0)
            d.setLabelText(f"正在下载 {model.name}… {done / 1e6:.1f} / {(total or 0) / 1e6:.1f} MB")

        bridge.progress.connect(on_progress)
        bridge.done.connect(lambda err, d=dialog: (outcome.__setitem__("error", err), d.reset()))
        dialog.canceled.connect(cancel.set)

        def run(name=model.name, b=bridge):
            try:
                model_catalog.download(name, progress=lambda d, t: b.progress.emit(int(d), int(t)),
                                       cancelled=cancel.is_set)
                b.done.emit("")
            except model_catalog.DownloadCancelled:
                b.done.emit("已取消")
            except Exception as exc:  # network, disk
                b.done.emit(str(exc) or type(exc).__name__)

        worker = threading.Thread(target=run, name="bird-sharpness-model-download", daemon=True)
        worker.start()
        dialog.exec()
        if dialog.wasCanceled():
            cancel.set()
        worker.join()  # deterministic: the download thread never outlives the dialog
        QApplication.processEvents()  # deliver the final queued signals
        if outcome["error"]:
            if outcome["error"] != "已取消":
                QMessageBox.warning(parent, "下载模型", f"{model.name} 下载失败：\n{outcome['error']}")
            return False
    return True


class AnalysisParamsForm(QWidget):
    """Every bird sharpness analysis option (``AnalysisParams``) in one form.

    ``preview``: the 「预览」 buttons next to the model lists are live (the trace window,
    which has a photo) and emit ``preview_requested(kind, model)``; elsewhere they are
    shown disabled with a hint. ``source``: show the image source choice (the trace
    window has its own switch above the steps and hides it here).
    """

    preview_requested = pyqtSignal(str, str)  # "detector" | "sam", model file name

    def __init__(self, parent=None, *, expand_fields: bool = True, preview: bool = False,
                 source: bool = True) -> None:
        super().__init__(parent)
        self._preview = bool(preview)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.image_source = QComboBox(self)
        for key, label, tip in SOURCE_CHOICES:
            self.image_source.addItem(label, key)
            self.image_source.setItemData(self.image_source.count() - 1, tip, _TOOLTIP_ROLE)
        self.image_source.setToolTip("测量哪种图像。默认相机内嵌 JPEG（快）；RAW 解码最准（门槛按它标定）；"
                                     "降噪成片需先降噪。不同来源的结果带不同的算法版本，「跳过已检测」不会混用。")
        self.image_source_note = QLabel("", self)
        self.image_source_note.setWordWrap(True)
        self.image_source_note.setForegroundRole(_ROLE.PlaceholderText)
        self.image_source.currentIndexChanged.connect(self._update_source_note)
        _narrow(self.image_source)
        if source:
            layout.addLayout(_grid(self, (("图像来源", self.image_source),), expand_fields))
            layout.addWidget(self.image_source_note)
        else:
            self.image_source.hide()
            self.image_source_note.hide()

        self.max_birds = _spin(self, 0, 999, 1, " 只", "0 = 不限制。设了上限时，压在相机焦点框上的鸟优先测量。", "不限制")
        self.min_bird_side = _spin(self, 0, 4096, 8, " px",
                                   "识别出的鸟框长边（全分辨率像素）小于此值时忽略这只鸟：不计数、不测量，复检、增强找鸟和"
                                   "模型链给定的鸟同样适用。0 = 不忽略。注意鸟群补检专门找 20–50 px 的小鸟，设得太大会把它们丢掉。"
                                   "非默认值记入算法版本后缀。", "不忽略")
        self.estimator = QComboBox(self)
        for key, label, tip in ESTIMATOR_CHOICES:
            self.estimator.addItem(label, key)
            self.estimator.setItemData(self.estimator.count() - 1, tip, _TOOLTIP_ROLE)
        self.estimator_note = QLabel("", self)
        self.estimator_note.setWordWrap(True)
        self.estimator_note.setForegroundRole(_ROLE.PlaceholderText)
        self.estimator.currentIndexChanged.connect(self._update_estimator_note)
        for combo in (self.estimator,):
            _narrow(combo)
        layout.addLayout(_grid(self, (("每张最多测量鸟数", self.max_birds), ("忽略小鸟（框长边小于）", self.min_bird_side),
                                      ("边缘统计方式", self.estimator)), expand_fields))
        layout.addWidget(self.estimator_note)

        layout.addSpacing(6)
        layout.addWidget(_heading("识别模型", self))
        self.detector = QComboBox(self)
        self.detector.setToolTip("识别鸟体的 YOLO 模型。分割模型给出鸟的像素轮廓，检测框模型只有鸟框（取框内核）。")
        self.sam_model = QComboBox(self)
        self.sam_model.setToolTip("用 SAM 按鸟框重新抠出看得见的鸟体，排除挡在前面的枝叶。SAM 不认识鸟，只抠框里的东西。")
        self.sam_scope = QComboBox(self)
        for key, label in SAM_SCOPE_CHOICES:
            self.sam_scope.addItem(label, key)
        self.pixels = QComboBox(self)
        for key, label in PIXELS_CHOICES:
            self.pixels.addItem(label, key)
        self.pixels.setToolTip("测哪些像素：分割模型 / SAM 抠出的鸟体轮廓内（没有轮廓时为鸟框内缩 8%），"
                               "或忽略轮廓、测整个鸟框内缩 8% 的区域（背景的枝叶会一起算进去）。选「整个鸟框区域」时不做 SAM 精修。")
        self.grey_fill = QCheckBox("鸟以外涂灰再测量（灰 114）", self)
        self.grey_fill.setToolTip("先把裁切里鸟以外的像素涂成灰色 114（与模型链的抠图一致），再定位鸟眼、测边缘。"
                                  "灰色与轮廓之间是一条人工的锐利边缘；头部和身体区域都向内收，通常测不到它，"
                                  "但鸟眼模型看到的是抠图而不是原图。非默认值记入算法版本后缀。")
        for combo in (self.detector, self.sam_model, self.sam_scope, self.pixels):
            _narrow(combo)
        self.models_status = QLabel("", self)
        self.models_status.setWordWrap(True)
        self.download_btn = QPushButton("下载所选模型…", self)
        self.download_btn.clicked.connect(self._download_selected)
        status_row = QHBoxLayout()
        status_row.addWidget(self.models_status, 1)
        status_row.addWidget(self.download_btn)
        self.detector_preview_btn = self._preview_button("detector", self.detector)
        self.sam_preview_btn = self._preview_button("sam", self.sam_model)
        layout.addLayout(_grid(self, (("检测模型", self._with_button(self.detector, self.detector_preview_btn)),
                                      ("SAM 精修", self._with_button(self.sam_model, self.sam_preview_btn)),
                                      ("SAM 精修范围", self.sam_scope), ("测量像素", self.pixels),
                                      (None, self.grey_fill)), expand_fields))
        layout.addLayout(status_row)
        self._fill_model_combos()
        for combo in (self.detector, self.sam_model):
            combo.currentIndexChanged.connect(self._update_models_status)
            combo.currentIndexChanged.connect(lambda _i: self._update_preview_buttons())
        self.sam_model.currentIndexChanged.connect(self._update_enabled)
        self.pixels.currentIndexChanged.connect(self._update_enabled)

        layout.addSpacing(6)
        layout.addWidget(_heading("增强找鸟（没找到鸟时放大找）", self))
        self.enh_mode = QComboBox(self)
        for key, label in ENH_CHOICES:
            self.enh_mode.addItem(label, key)
        self.enh_mode.setToolTip("前面几遍都没找到鸟时，把中心区域分成重叠的放大窗口逐个识别。"
                                 "放大后的树叶也常被认成鸟，计算过程窗口会列出所有候选供调整门槛。")
        _narrow(self.enh_mode)
        self.enh_region = _spin(self, 20, 100, 5, " %", "中心区域每边占画幅的比例（有焦点框时以焦点为中心）。")
        self.enh_grid = _spin(self, 1, 6, 1, " × N", "区域分成 N × N 个重叠 25% 的窗口。窗口比鸟小时只能看到鸟的一部分。")
        self.enh_imgsz = _spin(self, 320, 2048, 32, " px", "每个窗口送入网络的尺寸。")
        self.enh_conf = _spin(self, 5, 95, 5, " %", "采纳门槛：置信度达到它的候选才当成鸟（50% = 0.50）。")
        self.enh_lift = QCheckBox("画面暗时提亮再识别", self)
        self.enh_lift.setToolTip("逆光、暮色、夜景时先把暗部提亮再识别；提亮只用于识别，不参与测量。")
        layout.addLayout(_grid(self, (("启用", self.enh_mode), ("区域（每边占画幅）", self.enh_region),
                                      ("窗口网格", self.enh_grid), ("网络输入", self.enh_imgsz),
                                      ("采纳门槛", self.enh_conf), (None, self.enh_lift)), expand_fields))
        self.enh_mode.currentIndexChanged.connect(self._update_enabled)

        layout.addSpacing(6)
        layout.addWidget(_heading("无鸟时的分块", self))
        self.tiles = TileParamsForm(self, expand_fields=expand_fields)
        layout.addWidget(self.tiles)
        self.set_params(AnalysisParams().as_params())

    # ── model preview ──
    def _preview_button(self, kind: str, combo: QComboBox) -> QPushButton:
        button = QPushButton("预览", self)
        if self._preview:
            button.setToolTip("单独运行所选模型，查看它在这张照片上的原始结果")
            button.clicked.connect(lambda: self.preview_requested.emit(kind, combo.currentData() or ""))
        else:
            button.setToolTip("在计算过程窗口中可用（需要一张照片）")
        return button

    def _with_button(self, combo: QComboBox, button: QPushButton) -> QWidget:
        box = QWidget(self)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(combo, 1)
        row.addWidget(button)
        return box

    def _update_preview_buttons(self) -> None:
        self.detector_preview_btn.setEnabled(self._preview)
        outline = self.pixels.currentData() != PIXELS_BOX
        self.sam_preview_btn.setEnabled(self._preview and outline and bool(self.sam_model.currentData()))

    # ── models ──
    def _fill_model_combos(self) -> None:
        keep_det, keep_sam = self.detector.currentData(), self.sam_model.currentData()
        for combo in (self.detector, self.sam_model):
            combo.blockSignals(True)
            combo.clear()
        self.detector.addItem("内置（自动：yolo11l-seg 等）", "auto")
        for model in model_catalog.DETECTORS:
            self.detector.addItem(self._model_text(model), model.name)
        self.sam_model.addItem("关闭（默认）", "")
        for model in model_catalog.SAM_MODELS:
            self.sam_model.addItem(self._model_text(model), model.name)
        for combo, keep in ((self.detector, keep_det), (self.sam_model, keep_sam)):
            if keep is not None:
                index = combo.findData(keep)
                combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    @staticmethod
    def _model_text(model) -> str:
        return model.label + ("" if model_catalog.locate(model.name) else "（未下载）")

    def _select_model(self, combo: QComboBox, name: str) -> None:
        index = combo.findData(name)
        if index < 0 and name:  # a model file outside the catalog (e.g. a fine-tuned one)
            combo.addItem(name + ("" if model_catalog.locate(name) else "（未找到）"), name)
            index = combo.count() - 1
        combo.setCurrentIndex(max(0, index))

    def _update_models_status(self, *_args) -> None:
        missing = missing_models(self.params())
        if missing:
            self.models_status.setText("未下载：" + "、".join(missing) + "。下载前检测会报错。")
        else:
            self.models_status.setText("所选模型均已就绪。")
        self.download_btn.setEnabled(any(model_catalog.catalog_model(n) for n in missing))

    def _download_selected(self) -> None:
        if download_models(self, missing_models(self.params())):
            self._fill_model_combos()
        self._update_models_status()

    # ── values ──
    def _update_source_note(self, *_args) -> None:
        index = self.image_source.currentIndex()
        self.image_source_note.setText(SOURCE_CHOICES[index][2] if 0 <= index < len(SOURCE_CHOICES) else "")

    def _update_estimator_note(self, *_args) -> None:
        index = self.estimator.currentIndex()
        self.estimator_note.setText(ESTIMATOR_CHOICES[index][2] if 0 <= index < len(ESTIMATOR_CHOICES) else "")

    def _update_enabled(self, *_args) -> None:
        on = self.enh_mode.currentData() != ENH_OFF
        for widget in (self.enh_region, self.enh_grid, self.enh_imgsz, self.enh_conf, self.enh_lift):
            widget.setEnabled(on)
        outline = self.pixels.currentData() != PIXELS_BOX  # the whole box has no outline to refine
        self.sam_model.setEnabled(outline)
        self.sam_scope.setEnabled(outline and bool(self.sam_model.currentData()))
        self._update_preview_buttons()

    def set_params(self, params: Optional[dict]) -> None:
        p = AnalysisParams.from_params(params)
        self.image_source.setCurrentIndex(max(0, self.image_source.findData(p.image_source)))
        self.max_birds.setValue(p.max_birds)
        self.min_bird_side.setValue(p.min_bird_side)
        self.estimator.setCurrentIndex(max(0, self.estimator.findData(p.edge_estimator)))
        self._select_model(self.detector, p.detector)
        self._select_model(self.sam_model, p.sam_model)
        self.sam_scope.setCurrentIndex(max(0, self.sam_scope.findData(p.sam_scope)))
        self.pixels.setCurrentIndex(max(0, self.pixels.findData(p.bird_pixels)))
        self.grey_fill.setChecked(p.grey_fill)
        e = p.enhanced
        self.enh_mode.setCurrentIndex(max(0, self.enh_mode.findData(e.mode)))
        self.enh_region.setValue(e.region_percent)
        self.enh_grid.setValue(e.grid)
        self.enh_imgsz.setValue(e.imgsz)
        self.enh_conf.setValue(e.min_conf_percent)
        self.enh_lift.setChecked(e.lift)
        self.tiles.set_params(p.tiles.as_params())
        self._update_source_note()
        self._update_estimator_note()
        self._update_enabled()
        self._update_models_status()
        self._update_preview_buttons()

    def params(self) -> dict:
        return AnalysisParams.from_params({
            "image_source": self.image_source.currentData() or SOURCE_RAW,
            "max_birds": int(self.max_birds.value()), "min_bird_side": int(self.min_bird_side.value()),
            "edge_estimator": self.estimator.currentData() or "standard",
            "detector": self.detector.currentData() or "auto", "sam_model": self.sam_model.currentData() or "",
            "sam_scope": self.sam_scope.currentData() or SAM_SCOPE_ALL,
            "bird_pixels": self.pixels.currentData() or PIXELS_OUTLINE, "grey_fill": self.grey_fill.isChecked(),
            "enh_mode": self.enh_mode.currentData() or ENH_OFF, "enh_region_percent": int(self.enh_region.value()),
            "enh_grid": int(self.enh_grid.value()), "enh_imgsz": int(self.enh_imgsz.value()),
            "enh_min_conf_percent": int(self.enh_conf.value()), "enh_lift": self.enh_lift.isChecked(),
            **self.tiles.params()}).as_params()
