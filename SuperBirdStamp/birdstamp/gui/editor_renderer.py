"""editor_renderer.py – _BirdStampRendererMixin

Preview caching, render-settings building, image processing pipeline,
and the render_preview entry point.
Mixed into BirdStampEditorWindow via multiple inheritance.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image
from PyQt6.QtGui import QPixmap

from app_common.preview_canvas import (
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
)
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui import editor_core, editor_options, editor_template, editor_utils, template_context as _template_context
from birdstamp.gui.editor_preview_canvas import EditorPreviewOverlayOptions, EditorPreviewOverlayState
from birdstamp.video_export import (
    DEFAULT_EXPORT_STAGE_ID,
    EXPORT_STAGE_ID_KEY,
    PIPELINE_STAGE_ORDER_KEY,
    STAGE_FOCUS_OVERLAY_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ID,
    STAGE_RESIZE_LIMIT_ENABLED_KEY,
    STAGE_RESIZE_LIMIT_ID,
    STAGE_TEMPLATE_CROP_ENABLED_KEY,
    STAGE_TEMPLATE_CROP_ID,
    STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
    STAGE_TEMPLATE_OVERLAY_ID,
    normalize_export_stage_id,
    normalize_pipeline_stage_order,
)

_pil_to_qpixmap                     = editor_utils.pil_to_qpixmap
_path_key                           = editor_utils.path_key
_safe_color                         = editor_utils.safe_color
_build_metadata_context             = editor_utils.build_metadata_context
_default_placeholder_path           = editor_utils._default_placeholder_path
_extract_focus_box                  = editor_core.extract_focus_box
_extract_focus_box_for_display      = editor_core.extract_focus_box_for_display
_draw_focus_box_overlay            = editor_core.draw_focus_box_overlay
_resolve_focus_box_after_processing = editor_core.resolve_focus_box_after_processing
_resolve_focus_camera_type_from_metadata = editor_core.resolve_focus_camera_type_from_metadata
_transform_source_box_after_crop_padding = editor_core.transform_source_box_after_crop_padding
_normalize_center_mode              = editor_core.normalize_center_mode
_parse_bool_value                   = editor_core.parse_bool_value
_parse_ratio_value                  = editor_core.parse_ratio_value
_is_ratio_free                      = editor_core.is_ratio_free
_is_ratio_no_crop                   = editor_core.is_ratio_no_crop
_parse_padding_value                = editor_core.parse_padding_value
_pad_image                          = editor_core.pad_image
_resize_fit                         = editor_core.resize_fit
_crop_image_by_normalized_box       = editor_core.crop_image_by_normalized_box
_crop_box_has_effect                = editor_core.crop_box_has_effect
_compute_crop_output_size           = editor_core.compute_crop_output_size
_normalized_box_to_pixel_box        = editor_core.normalized_box_to_pixel_box
_normalize_template_payload         = editor_template.normalize_template_payload
_deep_copy_payload                  = editor_template.deep_copy_payload
_default_template_payload           = editor_template.default_template_payload
_load_template_payload              = editor_template.load_template_payload
render_template_overlay             = editor_template.render_template_overlay
_render_template_overlay_in_crop_region = editor_template.render_template_overlay_in_crop_region
_DEFAULT_TEMPLATE_CENTER_MODE       = editor_template.DEFAULT_TEMPLATE_CENTER_MODE
_DEFAULT_TEMPLATE_MAX_LONG_EDGE     = editor_template.DEFAULT_TEMPLATE_MAX_LONG_EDGE
_DEFAULT_CROP_PADDING_PX            = editor_core.DEFAULT_CROP_PADDING_PX
_CENTER_MODE_CUSTOM                 = editor_core.CENTER_MODE_CUSTOM
RATIO_FREE                          = editor_options.RATIO_FREE
RATIO_NO_CROP                       = editor_options.RATIO_NO_CROP
OUTPUT_FORMAT_OPTIONS               = editor_options.OUTPUT_FORMAT_OPTIONS


class _BirdStampRendererMixin:
    """Mixin: preview-cache, render-settings, image processing pipeline, render_preview."""

    def _crop_padding_state_for_render(self) -> dict[str, Any]:
        getter = getattr(self, "_get_crop_padding_state", None)
        if callable(getter):
            try:
                state = getter()
            except Exception:
                state = {}
        else:
            state = {}
        if not isinstance(state, dict):
            state = {}
        return {
            "top": _parse_padding_value(state.get("top", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "bottom": _parse_padding_value(state.get("bottom", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "left": _parse_padding_value(state.get("left", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "right": _parse_padding_value(state.get("right", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            "fill": _safe_color(str(state.get("fill", "#FFFFFF")), "#FFFFFF"),
        }

    def _apply_crop_padding_state_from_settings(self, settings: dict[str, Any]) -> None:
        setter = getattr(self, "_set_crop_padding_state", None)
        if not callable(setter):
            return
        setter(
            top=_parse_padding_value(settings.get("crop_padding_top", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            bottom=_parse_padding_value(settings.get("crop_padding_bottom", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            left=_parse_padding_value(settings.get("crop_padding_left", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            right=_parse_padding_value(settings.get("crop_padding_right", _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX),
            fill=_safe_color(str(settings.get("crop_padding_fill", "#FFFFFF")), "#FFFFFF"),
        )

    def _build_preview_overlay_options(self) -> EditorPreviewOverlayOptions:
        """Build editor preview overlay options from the current toolbar UI state."""
        preview_grid_combo = getattr(self, "preview_grid_combo", None)
        preview_grid_line_width_combo = getattr(self, "preview_grid_line_width_combo", None)
        return EditorPreviewOverlayOptions(
            show_focus_box=bool(self.show_focus_box_check.isChecked()),
            show_bird_box=bool(self.show_bird_box_check.isChecked()),
            show_crop_effect=bool(self.show_crop_effect_check.isChecked()),
            crop_effect_alpha=int(self.crop_effect_alpha_slider.value()),
            composition_grid_mode=normalize_preview_composition_grid_mode(
                preview_grid_combo.currentData() if preview_grid_combo is not None else "none"
            ),
            composition_grid_line_width=normalize_preview_composition_grid_line_width(
                preview_grid_line_width_combo.currentData() if preview_grid_line_width_combo is not None else 1
            ),
        )

    def _apply_preview_overlay_options_from_ui(self) -> None:
        """Apply preview overlay options to the preview canvas/composite."""
        self.preview_label.apply_overlay_options(self._build_preview_overlay_options())
        canvas = self.preview_label.canvas
        if hasattr(canvas, "set_crop_edit_mode"):
            crop_edit = getattr(self, "crop_edit_mode_check", None)
            canvas.set_crop_edit_mode(crop_edit.isChecked() if crop_edit else False)
        if hasattr(canvas, "set_crop_ratio_constraint"):
            r = self._selected_ratio()
            ratio_constraint = float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None
            canvas.set_crop_ratio_constraint(
                ratio_constraint,
                _is_ratio_free(r),
            )

    def _source_signature(self, path: Path) -> str:
        try:
            stat = path.stat()
            return f"{_path_key(path)}:{stat.st_size}:{stat.st_mtime_ns}"
        except Exception:
            return _path_key(path)

    def _preview_cache_file_for_source(self, path: Path, signature: str) -> Path:
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
        preview_dir = path.parent / ".preview"
        return preview_dir / f"{path.stem}.{digest}.png"

    def _invalidate_original_mode_cache(self) -> None:
        self._original_mode_signature = None
        self._original_mode_pixmap = None

    def _original_mode_cache_key(self) -> str:
        """原尺寸图缓存键：含源图与裁切/填充设置，任一变化即失效。"""
        if self.current_path is None:
            return ""
        base = self._source_signature(self.current_path)
        template_name = str(self.template_combo.currentText() or "default").strip() or "default"
        draw_overlay = f"{self.draw_banner_check.isChecked()}|{self.draw_text_check.isChecked()}|{self.draw_focus_check.isChecked()}"
        r = self._selected_ratio()
        cm = self._selected_center_mode()
        max_edge = self._selected_max_long_edge()
        stage_order = list(normalize_pipeline_stage_order(getattr(self, "_pipeline_stage_order", None)))
        stage_enabled_getter = getattr(self, "_current_pipeline_stage_enabled_map", None)
        stage_enabled = stage_enabled_getter() if callable(stage_enabled_getter) else {}
        padding = self._crop_padding_state_for_render()
        return (
            f"{base}|{template_name}|{draw_overlay}|{r}|{cm}|"
            f"{max_edge}|{stage_order}|{stage_enabled}|"
            f"{padding['top']}_{padding['bottom']}_{padding['left']}_{padding['right']}|{padding['fill']}"
        )

    def _preview_render_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """预览使用当前管线设置，但保留完整画布用于裁切框编辑。"""
        return self._clone_render_settings(settings)

    def _is_preview_stage_enabled(self, settings: dict[str, Any], stage_id: str) -> bool:
        if stage_id == STAGE_TEMPLATE_CROP_ID:
            return True
        enabled_keys = {
            STAGE_RESIZE_LIMIT_ID: STAGE_RESIZE_LIMIT_ENABLED_KEY,
            STAGE_TEMPLATE_OVERLAY_ID: STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
            STAGE_FOCUS_OVERLAY_ID: STAGE_FOCUS_OVERLAY_ENABLED_KEY,
        }
        key = enabled_keys.get(stage_id)
        if key is None:
            return True
        return _parse_bool_value(settings.get(key), True)

    def _resize_fit_size(self, size: tuple[int, int], max_long_edge: int) -> tuple[int, int]:
        width, height = max(1, int(size[0])), max(1, int(size[1]))
        edge = max(0, int(max_long_edge))
        if edge <= 0:
            return (width, height)
        long_edge = max(width, height)
        if long_edge <= edge:
            return (width, height)
        scale = edge / float(long_edge)
        return (max(1, int(round(width * scale))), max(1, int(round(height * scale))))

    def _preview_pipeline_output_size(
        self,
        *,
        source_size: tuple[int, int],
        crop_box: tuple[float, float, float, float] | None,
        outer_pad: tuple[int, int, int, int],
        settings: dict[str, Any],
    ) -> tuple[int, int] | None:
        crop_size = _compute_crop_output_size(
            int(source_size[0]),
            int(source_size[1]),
            crop_box,
            outer_pad,
        )
        if crop_size is None:
            return None
        if not self._is_preview_stage_enabled(settings, STAGE_RESIZE_LIMIT_ID):
            return crop_size
        return self._resize_fit_size(crop_size, max(0, int(settings.get("max_long_edge") or 0)))

    def _load_original_mode_pixmap(self) -> QPixmap | None:
        if self.current_path is None or self.current_source_image is None:
            return None

        signature = self._original_mode_cache_key()
        if not signature:
            return None
        if (
            self._original_mode_signature == signature
            and self._original_mode_pixmap is not None
            and not self._original_mode_pixmap.isNull()
        ):
            return self._original_mode_pixmap

        settings = self._render_settings_for_path(self.current_path, prefer_current_ui=True)
        original_settings = self._preview_render_settings(settings)
        try:
            # 原尺寸模式显示未裁切预览源，仅保持原始分辨率。
            raw_metadata = dict(self.current_raw_metadata)
            crop_box, outer_pad = self._compute_crop_plan_for_image(
                path=self.current_path,
                image=self.current_source_image,
                raw_metadata=raw_metadata,
                settings=original_settings,
            )
            img = self._build_processed_image(
                self.current_source_image.copy(),
                raw_metadata,
                settings=original_settings,
                source_path=self.current_path,
                apply_ratio_crop=False,
                crop_plan=(crop_box, outer_pad),
            )
            img = self._render_overlay_for_preview_frame(
                preview_base=img,
                raw_metadata=raw_metadata,
                metadata_context=dict(self.current_metadata_context),
                photo_info=self.current_photo_info,
                settings=original_settings,
                crop_box=crop_box,
            )
            img = self._render_focus_box_for_image(
                img,
                raw_metadata=raw_metadata,
                source_image=self.current_source_image,
                settings=original_settings,
                crop_box=crop_box,
                outer_pad=outer_pad,
                apply_ratio_crop=False,
            )
            direct_pixmap = _pil_to_qpixmap(img)
            if not direct_pixmap.isNull():
                self._original_mode_signature = signature
                self._original_mode_pixmap = direct_pixmap
                return direct_pixmap
        except Exception:
            pass

        # 处理失败时退回原图，避免界面无预览。
        try:
            direct_pixmap = _pil_to_qpixmap(self.current_source_image)
            if not direct_pixmap.isNull():
                self._original_mode_signature = signature
                self._original_mode_pixmap = direct_pixmap
                return direct_pixmap
        except Exception:
            pass
        return None

    def _current_focus_box_after_processing(self, *, apply_ratio_crop: bool = True) -> tuple[float, float, float, float] | None:
        if self.current_path is None or self.current_source_image is None:
            return None

        source_width, source_height = self.current_source_image.size
        focus_camera_type = _resolve_focus_camera_type_from_metadata(self.current_raw_metadata)
        focus_box_source = _extract_focus_box_for_display(
            self.current_raw_metadata,
            source_width,
            source_height,
            camera_type=focus_camera_type,
        )
        if focus_box_source is None:
            return None

        settings = self._render_settings_for_path(self.current_path, prefer_current_ui=True)
        crop_box, outer_pad = self._compute_crop_plan_for_image(
            path=self.current_path,
            image=self.current_source_image,
            raw_metadata=self.current_raw_metadata,
            settings=settings,
        )
        pad_top, pad_bottom, pad_left, pad_right = outer_pad
        focus_box = _transform_source_box_after_crop_padding(
            focus_box_source,
            crop_box=None,
            source_width=source_width,
            source_height=source_height,
            pt=pad_top,
            pb=pad_bottom,
            pl=pad_left,
            pr=pad_right,
        )
        if not apply_ratio_crop or focus_box is None or crop_box is None:
            return focus_box
        return _transform_source_box_after_crop_padding(
            focus_box,
            crop_box=crop_box,
            source_width=source_width + pad_left + pad_right,
            source_height=source_height + pad_top + pad_bottom,
            pt=0,
            pb=0,
            pl=0,
            pr=0,
        )

    def _current_bird_box(self) -> tuple[float, float, float, float] | None:
        if self.current_path is None or self.current_source_image is None:
            return None
        return self._bird_box_for_path(self.current_path, source_image=self.current_source_image)

    def _show_placeholder_preview(self) -> None:
        """激活 images/default.jpg 作为当前图像，走与真实照片完全相同的渲染流程。
        不将其加入照片列表，self.placeholder_path 标记当前处于占位状态。
        若 default.jpg 不存在则回退到裸 PIL 占位图。
        """
        src = _default_placeholder_path()
        if src.exists():
            try:
                image = decode_image(src, decoder="auto")
                self.placeholder_path: "Path | None" = src
                self.current_path = src
                self.current_source_image = image
                self._invalidate_original_mode_cache()
                self.current_raw_metadata = self._load_raw_metadata(src)
                self.current_photo_info = _template_context.ensure_photo_info(src, raw_metadata=self.current_raw_metadata)
                self.current_metadata_context = _build_metadata_context(self.current_photo_info, self.current_raw_metadata)
                # 走正常渲染流程（current_path 已设置，不会再次调用本函数）
                self.render_preview()
                return
            except Exception:
                pass
        # 回退：default.jpg 不可用，显示裸 PIL 占位图
        self.placeholder_path = None
        self.current_path = None
        self.current_photo_info = None
        self.current_source_image = None
        self.current_raw_metadata = {}
        self.current_metadata_context = {}
        self._preview_crop_size: tuple[int, int] | None = None
        self.preview_pixmap = _pil_to_qpixmap(self.placeholder)
        self.preview_overlay_state = EditorPreviewOverlayState()
        self._invalidate_original_mode_cache()
        self._refresh_preview_label(reset_view=True)

    def _refresh_preview_label(
        self,
        *,
        reset_view: bool = False,
        preserve_view: bool = False,
        force_fit: bool = False,
    ) -> None:
        self._apply_preview_overlay_options_from_ui()

        display_pixmap: QPixmap | None = self.preview_pixmap
        source_mode = "原图"

        self.preview_label.apply_overlay_state(
            self.preview_overlay_state if self.preview_pixmap else EditorPreviewOverlayState()
        )
        if self.current_source_image is not None:
            self.preview_label.set_original_size(self.current_source_image.size[0], self.current_source_image.size[1])
        else:
            self.preview_label.set_original_size(None, None)
        crop_size = getattr(self, "_preview_crop_size", None)
        if crop_size is not None:
            self.preview_label.set_cropped_size(crop_size[0], crop_size[1])
        else:
            self.preview_label.set_cropped_size(None, None)
        self.preview_label.set_source_mode(source_mode)
        self.preview_label.set_source_pixmap(
            display_pixmap,
            reset_view=reset_view,
            preserve_view=preserve_view,
            preserve_scale=preserve_view,
        )

    def _selected_center_mode(self) -> str:
        getter = getattr(self, "_center_mode_button_value", None)
        if callable(getter):
            try:
                return _normalize_center_mode(getter())
            except Exception:
                pass
        combo = getattr(self, "center_mode_combo", None)
        return _normalize_center_mode(combo.currentData() if combo is not None else _DEFAULT_TEMPLATE_CENTER_MODE)

    def _should_draw_template_overlay(self, settings: dict[str, Any]) -> bool:
        if not self._is_preview_stage_enabled(settings, STAGE_TEMPLATE_OVERLAY_ID):
            return False
        draw_banner = _parse_bool_value(settings.get("draw_banner"), True)
        draw_text = _parse_bool_value(settings.get("draw_text"), True)
        return draw_banner or draw_text

    def _build_current_render_settings(self) -> dict[str, Any]:
        template_name = str(self.template_combo.currentText() or "default").strip() or "default"
        template_payload = _normalize_template_payload(self.current_template_payload, fallback_name=template_name)
        center_mode = self._selected_center_mode()
        custom_center = getattr(self, "_custom_center", None)
        padding = self._crop_padding_state_for_render()
        uniform_auto_crop_check = getattr(self, "uniform_auto_crop_check", None)
        auto_crop_stabilization_slider = getattr(self, "auto_crop_stabilization_slider", None)
        pipeline_stage_order_getter = getattr(self, "_current_pipeline_stage_order", None)
        pipeline_stage_order = (
            pipeline_stage_order_getter()
            if callable(pipeline_stage_order_getter) else normalize_pipeline_stage_order(None)
        )
        export_stage_getter = getattr(self, "_selected_export_stage_id", None)
        export_stage_id = export_stage_getter() if callable(export_stage_getter) else DEFAULT_EXPORT_STAGE_ID
        stage_enabled_getter = getattr(self, "_is_pipeline_stage_enabled", None)

        def _stage_enabled(stage_id: str) -> bool:
            if callable(stage_enabled_getter):
                try:
                    return bool(stage_enabled_getter(stage_id))
                except Exception:
                    return True
            return True

        return {
            "template_name": template_name,
            "template_payload": _deep_copy_payload(template_payload),
            "draw_banner": bool(self.draw_banner_check.isChecked()),
            "draw_text": bool(self.draw_text_check.isChecked()),
            "draw_focus": bool(self.draw_focus_check.isChecked()),
            STAGE_TEMPLATE_CROP_ENABLED_KEY: _stage_enabled(STAGE_TEMPLATE_CROP_ID),
            STAGE_RESIZE_LIMIT_ENABLED_KEY: _stage_enabled(STAGE_RESIZE_LIMIT_ID),
            STAGE_TEMPLATE_OVERLAY_ENABLED_KEY: _stage_enabled(STAGE_TEMPLATE_OVERLAY_ID),
            STAGE_FOCUS_OVERLAY_ENABLED_KEY: _stage_enabled(STAGE_FOCUS_OVERLAY_ID),
            PIPELINE_STAGE_ORDER_KEY: list(normalize_pipeline_stage_order(pipeline_stage_order)),
            EXPORT_STAGE_ID_KEY: normalize_export_stage_id(export_stage_id),
            "uniform_auto_crop": bool(uniform_auto_crop_check.isChecked())
            if uniform_auto_crop_check is not None else False,
            "auto_crop_stabilization": int(auto_crop_stabilization_slider.value())
            if auto_crop_stabilization_slider is not None else 0,
            "ratio": self._selected_ratio(),
            "center_mode": center_mode,
            "max_long_edge": self._selected_max_long_edge(),
            "crop_padding_top": padding["top"],
            "crop_padding_bottom": padding["bottom"],
            "crop_padding_left": padding["left"],
            "crop_padding_right": padding["right"],
            "crop_padding_fill": padding["fill"],
            "crop_box": _template_context.normalize_crop_box(getattr(self, "_crop_box_override", None)),
            "custom_center_x": float(custom_center[0]) if center_mode == _CENTER_MODE_CUSTOM and custom_center else None,
            "custom_center_y": float(custom_center[1]) if center_mode == _CENTER_MODE_CUSTOM and custom_center else None,
        }

    def _photo_override_settings_from_snapshot(self, settings: dict[str, Any]) -> dict[str, Any]:
        """照片级 override 只保留逐图渲染参数，不包含全局导出开关。"""
        normalized = self._clone_render_settings(settings)
        normalized.pop("draw_banner", None)
        normalized.pop("draw_text", None)
        normalized.pop("draw_focus", None)
        normalized.pop(STAGE_TEMPLATE_CROP_ENABLED_KEY, None)
        normalized.pop(STAGE_RESIZE_LIMIT_ENABLED_KEY, None)
        normalized.pop(STAGE_TEMPLATE_OVERLAY_ENABLED_KEY, None)
        normalized.pop(STAGE_FOCUS_OVERLAY_ENABLED_KEY, None)
        normalized.pop(PIPELINE_STAGE_ORDER_KEY, None)
        normalized.pop(EXPORT_STAGE_ID_KEY, None)
        normalized.pop("max_long_edge", None)
        normalized.pop("uniform_auto_crop", None)
        normalized.pop("auto_crop_stabilization", None)
        return normalized

    def _clone_render_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        template_name = str(settings.get("template_name") or "default").strip() or "default"
        template_payload_raw = settings.get("template_payload")
        if isinstance(template_payload_raw, dict):
            template_payload = _normalize_template_payload(template_payload_raw, fallback_name=template_name)
        else:
            template_payload = _default_template_payload(name=template_name)

        ratio = _parse_ratio_value(settings.get("ratio"))

        max_long_edge = 0
        try:
            max_long_edge = int(settings.get("max_long_edge", 0))
        except Exception:
            max_long_edge = 0
        max_long_edge = max(0, max_long_edge)

        def _pad_px(key: str) -> int:
            return _parse_padding_value(settings.get(key, _DEFAULT_CROP_PADDING_PX), _DEFAULT_CROP_PADDING_PX)

        fill = _safe_color(str(settings.get("crop_padding_fill", "#FFFFFF")), "#FFFFFF")
        try:
            auto_crop_stabilization = int(round(float(settings.get("auto_crop_stabilization", 0))))
        except Exception:
            auto_crop_stabilization = 0
        auto_crop_stabilization = max(0, min(100, auto_crop_stabilization))
        uniform_auto_crop = _parse_bool_value(settings.get("uniform_auto_crop"), False)
        if not uniform_auto_crop:
            auto_crop_stabilization = 0

        custom_center_x = settings.get("custom_center_x")
        custom_center_y = settings.get("custom_center_y")
        return {
            "template_name": template_name,
            "template_payload": _deep_copy_payload(template_payload),
            "draw_banner": _parse_bool_value(settings.get("draw_banner"), True),
            "draw_text": _parse_bool_value(settings.get("draw_text"), True),
            "draw_focus": _parse_bool_value(settings.get("draw_focus"), False),
            STAGE_TEMPLATE_CROP_ENABLED_KEY: _parse_bool_value(settings.get(STAGE_TEMPLATE_CROP_ENABLED_KEY), True),
            STAGE_RESIZE_LIMIT_ENABLED_KEY: _parse_bool_value(settings.get(STAGE_RESIZE_LIMIT_ENABLED_KEY), True),
            STAGE_TEMPLATE_OVERLAY_ENABLED_KEY: _parse_bool_value(settings.get(STAGE_TEMPLATE_OVERLAY_ENABLED_KEY), True),
            STAGE_FOCUS_OVERLAY_ENABLED_KEY: _parse_bool_value(settings.get(STAGE_FOCUS_OVERLAY_ENABLED_KEY), True),
            PIPELINE_STAGE_ORDER_KEY: list(normalize_pipeline_stage_order(settings.get(PIPELINE_STAGE_ORDER_KEY))),
            EXPORT_STAGE_ID_KEY: normalize_export_stage_id(settings.get(EXPORT_STAGE_ID_KEY)),
            "uniform_auto_crop": uniform_auto_crop,
            "auto_crop_stabilization": auto_crop_stabilization,
            "ratio": ratio,
            "center_mode": _normalize_center_mode(settings.get("center_mode")),
            "max_long_edge": max_long_edge,
            "crop_padding_top": _pad_px("crop_padding_top"),
            "crop_padding_bottom": _pad_px("crop_padding_bottom"),
            "crop_padding_left": _pad_px("crop_padding_left"),
            "crop_padding_right": _pad_px("crop_padding_right"),
            "crop_padding_fill": fill,
            "crop_box": _template_context.normalize_crop_box(settings.get("crop_box")),
            "custom_center_x": float(custom_center_x) if custom_center_x is not None else None,
            "custom_center_y": float(custom_center_y) if custom_center_y is not None else None,
        }

    def _normalize_render_settings(self, raw: Any, fallback: dict[str, Any]) -> dict[str, Any]:
        settings = self._clone_render_settings(fallback)
        if not isinstance(raw, dict):
            return settings

        template_name = str(raw.get("template_name") or settings["template_name"]).strip() or settings["template_name"]
        settings["template_name"] = template_name
        payload_raw = raw.get("template_payload")
        if isinstance(payload_raw, dict):
            settings["template_payload"] = _normalize_template_payload(payload_raw, fallback_name=template_name)

        ratio_raw = raw.get("ratio")
        if ratio_raw is None or ratio_raw == "":
            settings["ratio"] = None
        else:
            parsed = _parse_ratio_value(ratio_raw)
            settings["ratio"] = parsed

        if "crop_box" in raw:
            cb = raw.get("crop_box")
            if cb is not None and isinstance(cb, (list, tuple)) and len(cb) == 4:
                try:
                    settings["crop_box"] = (float(cb[0]), float(cb[1]), float(cb[2]), float(cb[3]))
                except (TypeError, ValueError):
                    settings["crop_box"] = settings.get("crop_box")
            else:
                settings["crop_box"] = None
        if "center_mode" in raw:
            settings["center_mode"] = _normalize_center_mode(raw.get("center_mode"))
        if "uniform_auto_crop" in raw:
            settings["uniform_auto_crop"] = _parse_bool_value(raw.get("uniform_auto_crop"), False)
        if "auto_crop_stabilization" in raw:
            try:
                stabilization = int(round(float(raw.get("auto_crop_stabilization"))))
            except Exception:
                stabilization = int(settings.get("auto_crop_stabilization", 0))
            settings["auto_crop_stabilization"] = max(0, min(100, stabilization))

        for key in (
            STAGE_TEMPLATE_CROP_ENABLED_KEY,
            STAGE_RESIZE_LIMIT_ENABLED_KEY,
            STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
            STAGE_FOCUS_OVERLAY_ENABLED_KEY,
        ):
            if key in raw:
                settings[key] = _parse_bool_value(raw.get(key), True)
        if PIPELINE_STAGE_ORDER_KEY in raw:
            settings[PIPELINE_STAGE_ORDER_KEY] = list(normalize_pipeline_stage_order(raw.get(PIPELINE_STAGE_ORDER_KEY)))
        if EXPORT_STAGE_ID_KEY in raw:
            settings[EXPORT_STAGE_ID_KEY] = normalize_export_stage_id(raw.get(EXPORT_STAGE_ID_KEY))

        if "custom_center_x" in raw:
            try:
                settings["custom_center_x"] = float(raw.get("custom_center_x"))
            except Exception:
                settings["custom_center_x"] = settings.get("custom_center_x")
        if "custom_center_y" in raw:
            try:
                settings["custom_center_y"] = float(raw.get("custom_center_y"))
            except Exception:
                settings["custom_center_y"] = settings.get("custom_center_y")

        if "max_long_edge" in raw:
            try:
                parsed_max_edge = int(raw.get("max_long_edge"))
            except Exception:
                parsed_max_edge = int(settings["max_long_edge"])
            settings["max_long_edge"] = max(0, parsed_max_edge)

        def _parse_pad(key: str) -> int:
            return _parse_padding_value(raw.get(key, settings[key]), settings[key])

        for key in ("crop_padding_top", "crop_padding_bottom", "crop_padding_left", "crop_padding_right"):
            if key in raw:
                settings[key] = _parse_pad(key)
        if "crop_padding_fill" in raw:
            settings["crop_padding_fill"] = _safe_color(str(raw.get("crop_padding_fill", "#FFFFFF")), "#FFFFFF")
        if not _parse_bool_value(settings.get("uniform_auto_crop"), False):
            settings["auto_crop_stabilization"] = 0
        return settings

    def _render_settings_for_path(self, path: Path | None, *, prefer_current_ui: bool) -> dict[str, Any]:
        fallback = self._build_current_render_settings()
        if path is None:
            return fallback
        key = _path_key(path)
        if prefer_current_ui and self.current_path is not None and key == _path_key(self.current_path):
            return fallback
        settings = self._normalize_render_settings(self.photo_render_overrides.get(key), fallback=fallback)
        getter = getattr(self, "_photo_crop_box_for_path", None)
        if callable(getter):
            try:
                photo_crop_box = getter(path)
            except Exception:
                photo_crop_box = None
            if photo_crop_box is not None:
                settings["crop_box"] = photo_crop_box
        return settings

    def _ratio_combo_index_for_value(self, ratio: Any) -> int:
        for idx in range(self.ratio_combo.count()):
            data = self.ratio_combo.itemData(idx)
            if data is None and ratio is None:
                return idx
            if data is RATIO_NO_CROP or data == RATIO_NO_CROP:
                if _is_ratio_no_crop(ratio):
                    return idx
                continue
            if data is RATIO_FREE or data == RATIO_FREE:
                if ratio is RATIO_FREE or ratio == RATIO_FREE:
                    return idx
                continue
            if data is None or ratio is None:
                continue
            if _is_ratio_no_crop(ratio) or ratio is RATIO_FREE or ratio == RATIO_FREE:
                continue
            try:
                if abs(float(data) - float(ratio)) <= 0.0001:
                    return idx
            except Exception:
                continue
        return -1

    def _ensure_max_edge_option(self, max_edge: int) -> int:
        edge = max(0, int(max_edge))
        idx = self.max_edge_combo.findData(edge)
        if idx >= 0:
            return idx
        label = "不限制" if edge == 0 else str(edge)
        self.max_edge_combo.addItem(label, edge)
        return self.max_edge_combo.findData(edge)

    def _apply_render_settings_to_ui(self, settings: dict[str, Any]) -> None:
        normalized = self._clone_render_settings(settings)
        template_name = str(normalized["template_name"])

        widgets_to_block = [
            self.template_combo,
            self.ratio_combo,
            self.max_edge_combo,
        ]
        center_buttons = getattr(self, "center_mode_buttons", {}) or {}
        if isinstance(center_buttons, dict):
            widgets_to_block.extend(center_buttons.values())
        else:
            combo = getattr(self, "center_mode_combo", None)
            if combo is not None:
                widgets_to_block.append(combo)
        for w in widgets_to_block:
            w.blockSignals(True)
        try:
            template_idx = self.template_combo.findText(template_name)
            if template_idx >= 0:
                self.template_combo.setCurrentIndex(template_idx)

            ratio_idx = self._ratio_combo_index_for_value(normalized["ratio"])
            if ratio_idx >= 0:
                self.ratio_combo.setCurrentIndex(ratio_idx)

            center_setter = getattr(self, "_set_center_mode_value", None)
            if callable(center_setter):
                center_setter(normalized["center_mode"], emit_changed=False)
            else:
                combo = getattr(self, "center_mode_combo", None)
                center_idx = combo.findData(normalized["center_mode"]) if combo is not None else -1
                if center_idx >= 0:
                    combo.setCurrentIndex(center_idx)

            max_edge_idx = self._ensure_max_edge_option(int(normalized["max_long_edge"]))
            if max_edge_idx >= 0:
                self.max_edge_combo.setCurrentIndex(max_edge_idx)
            self._apply_crop_padding_state_from_settings(normalized)
        finally:
            for w in reversed(widgets_to_block):
                w.blockSignals(False)

        self.current_template_payload = _normalize_template_payload(
            normalized["template_payload"],
            fallback_name=template_name,
        )
        self._crop_box_override = _template_context.normalize_crop_box(normalized.get("crop_box"))
        if normalized["center_mode"] == _CENTER_MODE_CUSTOM:
            cx = normalized.get("custom_center_x")
            cy = normalized.get("custom_center_y")
            if cx is not None and cy is not None:
                self._custom_center = (float(cx), float(cy))
            else:
                self._custom_center = None
        else:
            self._custom_center = None

    def _compose_preview_with_crop_aligned_overlay(
        self,
        *,
        preview_base: Image.Image,
        rendered_crop: Image.Image,
        crop_box: tuple[float, float, float, float] | None,
    ) -> Image.Image:
        if not _crop_box_has_effect(crop_box):
            return rendered_crop

        crop_px = _normalized_box_to_pixel_box(crop_box, preview_base.width, preview_base.height)
        if crop_px is None:
            return preview_base
        left, top, right, bottom = crop_px
        target_w = max(1, right - left)
        target_h = max(1, bottom - top)

        patch = rendered_crop.convert("RGB")
        if patch.width != target_w or patch.height != target_h:
            patch = patch.resize((target_w, target_h), Image.Resampling.LANCZOS)

        merged = preview_base.copy()
        merged.paste(patch, (left, top))
        return merged

    def _resolve_template_payload_for_render(self, settings: dict[str, Any]) -> dict[str, Any]:
        template_name = str(settings.get("template_name") or "default").strip() or "default"
        payload_raw = settings.get("template_payload")
        if isinstance(payload_raw, dict):
            payload = _normalize_template_payload(payload_raw, fallback_name=template_name)
        else:
            payload = _default_template_payload(name=template_name)

        # 主预览和导出的模板内容始终跟随模板文件配置。
        template_path = self.template_paths.get(template_name)
        if template_path and template_path.is_file():
            try:
                payload = _load_template_payload(template_path)
            except Exception:
                pass
        return payload

    def _render_overlay_for_preview_frame(
        self,
        *,
        preview_base: Image.Image,
        raw_metadata: dict[str, Any],
        metadata_context: dict[str, str],
        photo_info: _template_context.PhotoInfo | None,
        settings: dict[str, Any],
        crop_box: tuple[float, float, float, float] | None,
    ) -> Image.Image:
        if not self._should_draw_template_overlay(settings):
            return preview_base

        template_payload = self._resolve_template_payload_for_render(settings)
        # 直接在当前预览帧的裁切区域绘制模板，避免先按输出尺寸渲染再缩放导致 Banner 预览偏差。
        return _render_template_overlay_in_crop_region(
            preview_base,
            raw_metadata=raw_metadata,
            metadata_context=metadata_context,
            photo_info=photo_info,
            template_payload=template_payload,
            crop_box=crop_box,
            draw_banner=_parse_bool_value(settings.get("draw_banner"), True),
            draw_text=_parse_bool_value(settings.get("draw_text"), True),
        )

    def _build_processed_image(
        self,
        image: Image.Image,
        raw_metadata: dict[str, Any],
        *,
        settings: dict[str, Any],
        source_path: Path | None,
        apply_ratio_crop: bool = True,
        crop_plan: tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]] | None = None,
    ) -> Image.Image:
        if crop_plan is None:
            crop_box, outer_pad = self._compute_crop_plan_for_image(
                path=source_path,
                image=image,
                raw_metadata=raw_metadata,
                settings=settings,
            )
        else:
            crop_box, outer_pad = crop_plan
        top, bottom, left, right = outer_pad
        if top or bottom or left or right:
            fill = str(settings.get("crop_padding_fill") or "#FFFFFF").strip() or "#FFFFFF"
            image = _pad_image(image, top=top, bottom=bottom, left=left, right=right, fill=fill)

        if apply_ratio_crop:
            image = _crop_image_by_normalized_box(image, crop_box)

        if self._is_preview_stage_enabled(settings, STAGE_RESIZE_LIMIT_ID):
            max_long_edge = max(0, int(settings.get("max_long_edge") or 0))
            image = _resize_fit(image, max_long_edge)
        return image

    def _render_focus_box_for_image(
        self,
        image: Image.Image,
        *,
        raw_metadata: dict[str, Any],
        source_image: Image.Image,
        settings: dict[str, Any],
        crop_box: tuple[float, float, float, float] | None,
        outer_pad: tuple[int, int, int, int],
        apply_ratio_crop: bool,
    ) -> Image.Image:
        if not self._is_preview_stage_enabled(settings, STAGE_FOCUS_OVERLAY_ID):
            return image
        if not _parse_bool_value(settings.get("draw_focus"), False):
            return image
        focus_box = _resolve_focus_box_after_processing(
            raw_metadata,
            source_width=source_image.width,
            source_height=source_image.height,
            crop_box=crop_box,
            outer_pad=outer_pad,
            apply_ratio_crop=apply_ratio_crop,
            camera_type=_resolve_focus_camera_type_from_metadata(raw_metadata),
        )
        if focus_box is None:
            return image
        return _draw_focus_box_overlay(image, focus_box)

    def _render_preview_pipeline_image(
        self,
        image: Image.Image,
        raw_metadata: dict[str, Any],
        *,
        settings: dict[str, Any],
        source_image: Image.Image,
        crop_box: tuple[float, float, float, float] | None,
        outer_pad: tuple[int, int, int, int],
    ) -> Image.Image:
        """按当前管线渲染预览，同时保留未裁切画布上的裁切框语义。"""
        padded = False
        for stage_id in normalize_pipeline_stage_order(settings.get(PIPELINE_STAGE_ORDER_KEY)):
            if stage_id == STAGE_TEMPLATE_CROP_ID:
                if padded:
                    continue
                top, bottom, left, right = outer_pad
                if top or bottom or left or right:
                    fill = str(settings.get("crop_padding_fill") or "#FFFFFF").strip() or "#FFFFFF"
                    image = _pad_image(image, top=top, bottom=bottom, left=left, right=right, fill=fill)
                padded = True
                continue

            if not self._is_preview_stage_enabled(settings, stage_id):
                continue

            if stage_id == STAGE_RESIZE_LIMIT_ID:
                image = _resize_fit(image, max(0, int(settings.get("max_long_edge") or 0)))
                continue

            if stage_id == STAGE_TEMPLATE_OVERLAY_ID:
                image = self._render_overlay_for_preview_frame(
                    preview_base=image,
                    raw_metadata=raw_metadata,
                    metadata_context=dict(self.current_metadata_context),
                    photo_info=self.current_photo_info,
                    settings=settings,
                    crop_box=crop_box,
                )
                continue

            if stage_id == STAGE_FOCUS_OVERLAY_ID:
                image = self._render_focus_box_for_image(
                    image,
                    raw_metadata=raw_metadata,
                    source_image=source_image,
                    settings=settings,
                    crop_box=crop_box,
                    outer_pad=outer_pad,
                    apply_ratio_crop=False,
                )

        if not padded:
            top, bottom, left, right = outer_pad
            if top or bottom or left or right:
                fill = str(settings.get("crop_padding_fill") or "#FFFFFF").strip() or "#FFFFFF"
                image = _pad_image(image, top=top, bottom=bottom, left=left, right=right, fill=fill)
        return image

    def _render_for_path(self, path: Path, *, prefer_current_ui: bool) -> Image.Image:
        settings = self._render_settings_for_path(path, prefer_current_ui=prefer_current_ui)
        if self.current_path and path == self.current_path and self.current_source_image is not None:
            source_image = self.current_source_image.copy()
            raw_metadata = dict(self.current_raw_metadata)
        else:
            source_image = decode_image(path, decoder="auto")
            raw_metadata = self._load_raw_metadata(path)

        crop_box, outer_pad = self._compute_crop_plan_for_image(
            path=path,
            image=source_image,
            raw_metadata=raw_metadata,
            settings=settings,
        )
        processed = self._build_processed_image(
            source_image,
            raw_metadata,
            settings=settings,
            source_path=path,
            apply_ratio_crop=True,
            crop_plan=(crop_box, outer_pad),
        )
        rendered = processed
        if self._should_draw_template_overlay(settings):
            template_payload = self._resolve_template_payload_for_render(settings)
            if self.current_path and path == self.current_path and self.current_source_image is not None:
                context = dict(self.current_metadata_context)
                photo_info = self.current_photo_info
            else:
                photo_info = _template_context.ensure_photo_info(path, raw_metadata=raw_metadata)
                context = _build_metadata_context(photo_info, raw_metadata)
            rendered = render_template_overlay(
                processed,
                raw_metadata=raw_metadata,
                metadata_context=context,
                photo_info=photo_info,
                template_payload=template_payload,
                draw_banner=_parse_bool_value(settings.get("draw_banner"), True),
                draw_text=_parse_bool_value(settings.get("draw_text"), True),
            )

        return self._render_focus_box_for_image(
            rendered,
            raw_metadata=raw_metadata,
            source_image=source_image,
            settings=settings,
            crop_box=crop_box,
            outer_pad=outer_pad,
            apply_ratio_crop=True,
        )

    def render_preview(self, *_args: Any) -> None:
        if not self.current_path:
            # default.jpg 不可用时的降级路径；正常情况由 _show_placeholder_preview 负责激活占位图
            self._set_status("请选择照片后再预览。")
            return

        crop_box: tuple[float, float, float, float] | None = None
        outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0)
        try:
            if self.current_source_image is None:
                raise RuntimeError("缺少当前原图数据")
            settings = self._render_settings_for_path(self.current_path, prefer_current_ui=True)
            preview_settings = self._preview_render_settings(settings)
            source_image = self.current_source_image.copy()
            raw_metadata = dict(self.current_raw_metadata)
            crop_box, outer_pad = self._compute_crop_plan_for_image(
                path=self.current_path,
                image=self.current_source_image,
                raw_metadata=raw_metadata,
                settings=preview_settings,
            )
            # 预览保留完整画布用于裁切框编辑，但其它 stage 按管线顺序渲染。
            rendered = self._render_preview_pipeline_image(
                source_image,
                raw_metadata,
                settings=preview_settings,
                source_image=self.current_source_image,
                crop_box=crop_box,
                outer_pad=outer_pad,
            )
        except Exception as exc:
            self._preview_crop_size = None
            self.preview_overlay_state = EditorPreviewOverlayState()
            self._show_error("预览失败", str(exc))
            self._set_status(f"预览失败: {exc}")
            return

        self.last_rendered = rendered
        self._preview_crop_size = self._preview_pipeline_output_size(
            source_size=(self.current_source_image.width, self.current_source_image.height),
            crop_box=crop_box,
            outer_pad=outer_pad,
            settings=preview_settings,
        )
        pad_top, pad_bottom, pad_left, pad_right = outer_pad
        focus_camera_type = _resolve_focus_camera_type_from_metadata(raw_metadata)
        # 注意：预览图是经过 EXIF Orientation 纠正后的显示坐标，不能直接用当前图像尺寸调用
        # extract_focus_box()。这里必须走 extract_focus_box_for_display()，否则“显示对焦点”会再次错位。
        preview_focus_box = _transform_source_box_after_crop_padding(
            _extract_focus_box_for_display(
                raw_metadata,
                self.current_source_image.width,
                self.current_source_image.height,
                camera_type=focus_camera_type,
            ),
            crop_box=None,
            source_width=self.current_source_image.width,
            source_height=self.current_source_image.height,
            pt=pad_top,
            pb=pad_bottom,
            pl=pad_left,
            pr=pad_right,
        )
        preview_bird_box = _transform_source_box_after_crop_padding(
            self._bird_box_for_path(self.current_path, source_image=self.current_source_image),
            crop_box=None,
            source_width=self.current_source_image.width,
            source_height=self.current_source_image.height,
            pt=pad_top,
            pb=pad_bottom,
            pl=pad_left,
            pr=pad_right,
        )
        focus_stage_draws = (
            self._is_preview_stage_enabled(preview_settings, STAGE_FOCUS_OVERLAY_ID)
            and _parse_bool_value(preview_settings.get("draw_focus"), False)
        )
        self.preview_overlay_state = EditorPreviewOverlayState(
            focus_box=None if focus_stage_draws else preview_focus_box,
            bird_box=preview_bird_box,
            crop_effect_box=crop_box,
        )

        self.preview_pixmap = _pil_to_qpixmap(rendered)
        fit_reset = self._pending_preview_fit_reset
        self._pending_preview_fit_reset = False
        self._refresh_preview_label(reset_view=True, force_fit=fit_reset)
        output_size = self._preview_crop_size
        output_text = f" | 输出 {output_size[0]}x{output_size[1]}" if output_size is not None else ""
        if pad_top or pad_bottom or pad_left or pad_right:
            self._set_status(
                f"预览完成: {rendered.width}x{rendered.height}{output_text} | 外填充 上{pad_top}px 下{pad_bottom}px 左{pad_left}px 右{pad_right}px"
            )
        else:
            self._set_status(f"预览完成: {rendered.width}x{rendered.height}{output_text}")
