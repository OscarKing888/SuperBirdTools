# Editor UI utilities: color, font, screen picker, placeholder, metadata context.
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageColor, ImageDraw, ImageOps
from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFontDatabase, QGuiApplication, QImage, QPainter, QPen, QPixmap, QRawFont
from PyQt6.QtWidgets import QApplication, QAbstractSpinBox, QFormLayout, QLabel, QSizePolicy, QWidget

from birdstamp.config import resolve_bundled_path
from birdstamp.gui.template_context import (
    PhotoInfo,
    build_template_context,
    get_template_context_field_options as _provider_field_options,
)
from birdstamp.render.typography import list_available_font_paths, load_font

ALIGN_OPTIONS_VERTICAL = ("top", "center", "bottom")
ALIGN_OPTIONS_HORIZONTAL = ("left", "center", "right")

DEFAULT_TEMPLATE_BANNER_COLOR = "#111111"
TEMPLATE_BANNER_COLOR_NONE = "none"
TEMPLATE_BANNER_COLOR_CUSTOM = "custom"
TEMPLATE_BANNER_TOP_PADDING_PX = 16
DEFAULT_TEMPLATE_FONT_TYPE = "auto"
DEFAULT_CROP_EFFECT_ALPHA = 160
_CHINESE_FONT_NAME_ALIASES: tuple[tuple[str, str], ...] = (
    ("microsoft yahei", "微软雅黑"),
    ("msyh", "微软雅黑"),
    ("simhei", "黑体"),
    ("simsun", "宋体"),
    ("nsimsun", "新宋体"),
    ("fangsong", "仿宋"),
    ("dengxian", "等线"),
    ("pingfang", "苹方"),
    ("hiragino sans", "冬青黑体"),
    ("hiragino sans gb", "冬青黑体简体中文"),
    ("hiragino mincho", "冬青明朝"),
    ("songti sc", "宋体-简"),
    ("songti", "宋体"),
    ("heiti sc", "黑体-简"),
    ("heiti", "黑体"),
    ("kaiti sc", "楷体-简"),
    ("stfangsong", "华文仿宋"),
    ("stkaiti", "华文楷体"),
    ("stsong", "华文宋体"),
    ("source han sans", "思源黑体"),
    ("source han serif", "思源宋体"),
    ("noto sans cjk", "Noto Sans CJK"),
    ("noto serif cjk", "Noto Serif CJK"),
    ("sarasa", "更纱黑体"),
)


def safe_color(value: str, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    try:
        ImageColor.getrgb(text)
    except ValueError:
        return fallback
    return text


def build_color_preview_swatch() -> QLabel:
    swatch = QLabel()
    swatch.setFixedSize(24, 20)
    swatch.setAlignment(Qt.AlignmentFlag.AlignCenter)
    swatch.setStyleSheet("border: 1px solid #2A2A2A; border-radius: 2px;")
    swatch.setToolTip("")
    return swatch


def configure_form_layout(form: QFormLayout) -> None:
    """Normalize QFormLayout growth so fields behave consistently on macOS and Windows."""
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if form.horizontalSpacing() < 12:
        form.setHorizontalSpacing(12)
    if form.verticalSpacing() < 8:
        form.setVerticalSpacing(8)


def set_widget_minimum_width_from_text(
    widget: QWidget,
    text: str,
    *,
    extra_padding: int = 24,
    floor: int = 0,
) -> int:
    """Set a width floor from content text while preserving the native size hint."""
    width = max(int(floor), int(widget.sizeHint().width()))
    sample = str(text or "").strip()
    if sample:
        width = max(width, int(widget.fontMetrics().horizontalAdvance(sample) + extra_padding))
    widget.setMinimumWidth(width)
    return width


def configure_spinbox_minimum_width(
    spin: QAbstractSpinBox,
    *,
    sample_text: str,
    extra_padding: int = 40,
    expanding: bool = False,
) -> int:
    """Give spin boxes enough horizontal room for text plus native step buttons."""
    width = set_widget_minimum_width_from_text(
        spin,
        sample_text,
        extra_padding=extra_padding,
    )
    if expanding:
        policy = spin.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.MinimumExpanding)
        spin.setSizePolicy(policy)
    return width


def set_color_preview_swatch(
    swatch: QLabel | None,
    value: str | None,
    *,
    fallback: str = "#FFFFFF",
    allow_none: bool = False,
) -> None:
    if swatch is None:
        return
    raw = str(value or "").strip()
    lowered = raw.lower()
    if allow_none and lowered in {"", "none", "transparent", "off", "false", "0"}:
        swatch.setText("无")
        swatch.setToolTip("透明")
        swatch.setStyleSheet(
            "background: #E3E5E8; color: #4A4A4A; border: 1px dashed #7A7A7A; border-radius: 2px; font-size: 10px;"
        )
        return
    color_text = safe_color(raw, fallback).upper()
    swatch.setText("")
    swatch.setToolTip(color_text)
    swatch.setStyleSheet(f"background: {color_text}; border: 1px solid #2A2A2A; border-radius: 2px;")


def normalize_template_banner_color(value: Any, default: str = DEFAULT_TEMPLATE_BANNER_COLOR) -> str:
    fallback = safe_color(default, DEFAULT_TEMPLATE_BANNER_COLOR)
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    lowered = text.lower()
    if lowered in {"none", "transparent", "off", "false", "0"}:
        return TEMPLATE_BANNER_COLOR_NONE
    return safe_color(text, fallback)


def template_banner_fill_color(value: Any) -> str | None:
    color = normalize_template_banner_color(value)
    if color == TEMPLATE_BANNER_COLOR_NONE:
        return None
    return color


def _contains_cjk_char(text: str) -> bool:
    for ch in str(text or ""):
        code = ord(ch)
        if 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            return True
    return False


def _guess_chinese_font_name(families: list[str], font_path_text: str) -> str:
    for family in families:
        if _contains_cjk_char(family):
            return family
    path_name = ""
    path_stem = ""
    try:
        p = Path(font_path_text)
        path_name = p.name.lower()
        path_stem = p.stem.lower()
    except Exception:
        pass
    haystacks = [str(f or "").strip().lower() for f in families if str(f or "").strip()]
    joined = " | ".join(haystacks + [path_name, path_stem])
    for key, zh_name in _CHINESE_FONT_NAME_ALIASES:
        if key in joined:
            return zh_name
    return ""


def _is_unwanted_font_for_template_picker(*, label: str, font_path_text: str) -> bool:
    haystack = f"{label} {font_path_text}".lower()
    if "lastresort" in haystack:
        return True
    if "aqua kana" in haystack:
        return True
    if "fallback" in haystack and "cjk" in haystack:
        return True
    return False


@lru_cache(maxsize=4096)
def _font_metadata_from_path(font_path_text: str) -> dict[str, Any]:
    path_text = str(font_path_text or "").strip()
    if not path_text:
        return {
            "family_label": "",
            "display_label_zh": "",
            "supports_chinese": False,
        }
    try:
        font_id = QFontDatabase.addApplicationFont(path_text)
    except Exception:
        return {
            "family_label": "",
            "display_label_zh": "",
            "supports_chinese": False,
        }
    if font_id < 0:
        return {
            "family_label": "",
            "display_label_zh": "",
            "supports_chinese": False,
        }
    try:
        names: list[str] = []
        for family in QFontDatabase.applicationFontFamilies(font_id):
            text = str(family or "").strip()
            if text and text not in names:
                names.append(text)
        family_label = " / ".join(names[:2])
        supports_chinese = False
        raw_font_valid = False
        try:
            raw_font = QRawFont(path_text, 14)
            raw_font_valid = raw_font.isValid()
            if raw_font_valid:
                supports_chinese = bool(
                    raw_font.supportsCharacter("汉")
                    or raw_font.supportsCharacter("鳥")
                    or raw_font.supportsCharacter("测")
                )
        except Exception:
            raw_font_valid = False
        if not supports_chinese and not raw_font_valid:
            for family in names:
                try:
                    systems = QFontDatabase.writingSystems(family)
                except Exception:
                    systems = []
                if (
                    QFontDatabase.WritingSystem.SimplifiedChinese in systems
                    or QFontDatabase.WritingSystem.TraditionalChinese in systems
                ):
                    supports_chinese = True
                    break
        zh_name = _guess_chinese_font_name(names, path_text)
        if zh_name and family_label and zh_name not in family_label:
            display_label_zh = f"{zh_name} / {family_label}"
        elif zh_name:
            display_label_zh = zh_name
        else:
            display_label_zh = family_label
        return {
            "family_label": family_label,
            "display_label_zh": display_label_zh,
            "supports_chinese": supports_chinese,
        }
    except Exception:
        return {
            "family_label": "",
            "display_label_zh": "",
            "supports_chinese": False,
        }
    finally:
        try:
            QFontDatabase.removeApplicationFont(font_id)
        except Exception:
            pass


@lru_cache(maxsize=4096)
def font_family_label_from_path(font_path_text: str) -> str:
    metadata = _font_metadata_from_path(font_path_text)
    return str(metadata.get("family_label") or "")


@lru_cache(maxsize=4096)
def font_display_label_from_path(font_path_text: str) -> str:
    metadata = _font_metadata_from_path(font_path_text)
    return str(metadata.get("display_label_zh") or metadata.get("family_label") or "")


@lru_cache(maxsize=4096)
def font_supports_chinese_from_path(font_path_text: str) -> bool:
    metadata = _font_metadata_from_path(font_path_text)
    return bool(metadata.get("supports_chinese"))


def normalize_template_font_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_TEMPLATE_FONT_TYPE
    lowered = text.lower()
    if lowered in {"auto", "default", "system", "none"}:
        return DEFAULT_TEMPLATE_FONT_TYPE
    return text


def template_font_path_from_type(value: Any) -> Path | None:
    font_type = normalize_template_font_type(value)
    if font_type == DEFAULT_TEMPLATE_FONT_TYPE:
        return None
    try:
        candidate = Path(font_type).expanduser()
    except Exception:
        return None
    try:
        if candidate.exists() and candidate.is_file():
            return candidate
    except Exception:
        return None
    return None


@lru_cache(maxsize=4)
def template_font_choices(
    *,
    chinese_only: bool = False,
    prefer_chinese_label: bool = False,
) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = [("自动(系统默认)", DEFAULT_TEMPLATE_FONT_TYPE)]
    font_entries: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for font_path in list_available_font_paths():
        key = str(font_path).strip()
        if not key or key in seen_paths:
            continue
        seen_paths.add(key)
        if chinese_only and not font_supports_chinese_from_path(key):
            continue
        family_label = font_display_label_from_path(key) if prefer_chinese_label else font_family_label_from_path(key)
        if chinese_only and _is_unwanted_font_for_template_picker(label=family_label, font_path_text=key):
            continue
        if family_label:
            label = f"{family_label} ({font_path.name})"
        else:
            label = f"{font_path.stem} ({font_path.name})"
        font_entries.append((label, key))
    font_entries.sort(key=lambda item: item[0].lower())
    choices.extend(font_entries)
    return choices


def path_key(path: Path) -> str:
    return str(path.resolve(strict=False)).casefold()


def sanitize_template_name(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    safe = safe.replace(" ", "_").strip("._")
    return safe


def build_metadata_context(path: Path | PhotoInfo, raw_metadata: dict[str, Any]) -> dict[str, str]:
    """构建用于模板和 UI 的元数据上下文字典。

    当前实现委托给 PhotoInfo + template_context 数据源装配，便于后续继续扩展字段来源。
    """
    return build_template_context(path, raw_metadata)


_DEFAULT_FALLBACK_CONTEXT_VARS: list[tuple[str, str]] = [
    ("{bird}", "鸟种名称"),
    ("{bird_latin}", "鸟种拉丁文名称"),
    ("{bird_scientific}", "鸟种学名"),
    ("{bird_common}", "鸟种通用名"),
    ("{bird_family}", "鸟种科名"),
    ("{bird_order}", "鸟种目名"),
    ("{bird_class}", "鸟种纲名"),
    ("{bird_phylum}", "鸟种门名"),
    ("{bird_kingdom}", "鸟种界名"),
    ("{capture_date}", "拍摄日期"),
    ("{capture_text}", "拍摄日期时间"),
    ("{author}", "作者"),
    ("{location}", "拍摄地点"),
    ("{gps_text}", "GPS 坐标文字"),
    ("{camera}", "相机型号"),
    ("{lens}", "镜头型号"),
    ("{settings_text}", "拍摄参数"),
    ("{stem}", "文件名（不含扩展名）"),
    ("{filename}", "完整文件名"),
]


def get_birdstamp_cfg_path() -> Path:
    """返回内置 birdstamp.cfg 的路径。"""
    return resolve_bundled_path("config", "birdstamp.cfg")


def _load_birdstamp_cfg_raw() -> dict[str, Any]:
    path = get_birdstamp_cfg_path()
    try:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _fallback_context_vars_from_cfg(data: dict[str, Any]) -> list[tuple[str, str]]:
    items = data.get("template_fallback_context_vars")
    if not isinstance(items, list):
        return list(_DEFAULT_FALLBACK_CONTEXT_VARS)
    result: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        expr = str(item.get("expr") or "").strip()
        label = str(item.get("label") or "").strip()
        if not expr or not label:
            continue
        result.append((expr, label))
    return result or list(_DEFAULT_FALLBACK_CONTEXT_VARS)


@lru_cache(maxsize=1)
def get_fallback_context_vars() -> list[tuple[str, str]]:
    """返回用于 Fallback 下拉列表的上下文变量配置。

    优先从 config/birdstamp.cfg 读取；否则回退到内置默认列表。
    """
    data = _load_birdstamp_cfg_raw()
    return _fallback_context_vars_from_cfg(data)


def get_template_context_field_options() -> list[tuple[str, str, str]]:
    """返回统一「数据源/字段」选项列表，供模板编辑下拉使用。

    字段定义下沉到各 TemplateContextProvider 类中，这里只做薄封装。
    """
    return _provider_field_options()


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    """Convert a PIL Image to a QPixmap (RGBA round-trip)."""
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    q_image = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(q_image.copy())


def _default_placeholder_path() -> Path:
    """Locate bundled images/default.jpg in dev/frozen layouts."""
    return resolve_bundled_path("images", "default.jpg")


def build_placeholder_image(width: int = 1600, height: int = 1000) -> Image.Image:
    width = max(320, width)
    height = max(220, height)
    src = _default_placeholder_path()
    if src.exists():
        try:
            return ImageOps.fit(
                Image.open(src).convert("RGB"),
                (width, height),
                method=Image.Resampling.LANCZOS,
            )
        except Exception:
            pass
    # Fallback: simple dark gradient when image is unavailable
    image = Image.new("RGB", (width, height), color="#2C3340")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / float(max(1, height - 1))
        r = int(40 + (58 - 40) * ratio)
        g = int(49 + (70 - 49) * ratio)
        b = int(62 + (86 - 62) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b), width=1)
    return image


# -------- Screen color picker (Qt) --------
_ACTIVE_SCREEN_COLOR_PICKERS: list["_ScreenColorPickerSession"] = []


def _sample_screen_color_at(global_pos: QPoint) -> str | None:
    screen = QGuiApplication.screenAt(global_pos)
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    geo = screen.geometry()
    local_x = global_pos.x() - geo.x()
    local_y = global_pos.y() - geo.y()
    if local_x < 0 or local_y < 0:
        return None
    sample = screen.grabWindow(0, local_x, local_y, 1, 1)
    if sample.isNull():
        return None
    image = sample.toImage()
    if image.isNull():
        return None
    color = QColor.fromRgb(image.pixel(0, 0))
    return color.name(QColor.NameFormat.HexRgb).upper()


class _ScreenColorPickerOverlay(QWidget):
    colorPicked = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, geometry: QRect, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(geometry)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            color = _sample_screen_color_at(event.globalPosition().toPoint())
            if color:
                self.colorPicked.emit(color)
            else:
                self.cancelled.emit()
            return
        if event.button() in {Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton}:
            self.cancelled.emit()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 26))
        pos = self.mapFromGlobal(QCursor.pos())
        sample = _sample_screen_color_at(QCursor.pos())
        sample_color = QColor(sample) if sample else QColor("#FFFFFF")
        preview_size = 32
        preview_rect = QRectF(
            float(pos.x() + 16),
            float(pos.y() + 16),
            float(preview_size),
            float(preview_size),
        )
        if preview_rect.right() > self.width() - 8:
            preview_rect.moveLeft(float(max(8, pos.x() - preview_size - 16)))
        if preview_rect.bottom() > self.height() - 8:
            preview_rect.moveTop(float(max(8, pos.y() - preview_size - 16)))
        painter.setPen(QPen(QColor("#111111"), 1))
        painter.setBrush(sample_color)
        painter.drawRect(preview_rect)
        text = f"{sample or '-'}  左键取色 / 右键或Esc取消"
        text_rect = QRectF(
            preview_rect.left(),
            preview_rect.bottom() + 6.0,
            280.0,
            22.0,
        )
        if text_rect.right() > self.width() - 8:
            text_rect.moveLeft(float(max(8, self.width() - text_rect.width() - 8)))
        if text_rect.bottom() > self.height() - 8:
            text_rect.moveTop(float(max(8, preview_rect.top() - text_rect.height() - 6.0)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 20, 180))
        painter.drawRoundedRect(text_rect, 4.0, 4.0)
        painter.setPen(QPen(QColor("#F6F6F6"), 1))
        painter.drawText(text_rect.adjusted(8.0, 0.0, -8.0, 0.0), int(Qt.AlignmentFlag.AlignVCenter), text)
        painter.end()


class _ScreenColorPickerSession:
    def __init__(self, *, parent: QWidget | None, on_picked: Callable[[str], None]) -> None:
        self._parent = parent
        self._on_picked = on_picked
        self._overlays: list[_ScreenColorPickerOverlay] = []
        self._finished = False

    def start(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return
        for screen in screens:
            overlay = _ScreenColorPickerOverlay(screen.geometry(), parent=None)
            overlay.colorPicked.connect(self._handle_color_picked)
            overlay.cancelled.connect(self._handle_cancelled)
            self._overlays.append(overlay)
        _ACTIVE_SCREEN_COLOR_PICKERS.append(self)
        for overlay in self._overlays:
            overlay.show()
            overlay.raise_()
        if self._overlays:
            self._overlays[0].activateWindow()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        for overlay in self._overlays:
            overlay.hide()
            overlay.deleteLater()
        self._overlays.clear()
        if self in _ACTIVE_SCREEN_COLOR_PICKERS:
            _ACTIVE_SCREEN_COLOR_PICKERS.remove(self)

    def _handle_color_picked(self, color: str) -> None:
        self._finish()
        try:
            self._on_picked(color)
        except Exception:
            return

    def _handle_cancelled(self) -> None:
        self._finish()


def start_screen_color_picker(*, parent: QWidget | None, on_picked: Callable[[str], None]) -> None:
    session = _ScreenColorPickerSession(parent=parent, on_picked=on_picked)
    session.start()
