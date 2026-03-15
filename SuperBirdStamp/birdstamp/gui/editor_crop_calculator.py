"""editor_crop_calculator.py – _BirdStampCropMixin

Crop-box calculation, bird-box detection, and UI-value accessor helpers.
Mixed into BirdStampEditorWindow via multiple inheritance.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image

from birdstamp.gui import editor_core, editor_options

_parse_ratio_value                  = editor_core.parse_ratio_value
_parse_bool_value                   = editor_core.parse_bool_value
_normalize_center_mode              = editor_core.normalize_center_mode
_parse_padding_value                = editor_core.parse_padding_value
_pad_image                          = editor_core.pad_image
_solve_axis_crop_start              = editor_core.solve_axis_crop_start
_compute_ratio_crop_box             = editor_core.compute_ratio_crop_box
_crop_box_has_effect                = editor_core.crop_box_has_effect
_crop_plan_from_override            = editor_core._crop_plan_from_override
_is_ratio_free                      = editor_core.is_ratio_free
_normalize_unit_box                 = editor_core.normalize_unit_box
_box_center                         = editor_core.box_center
_expand_unit_box_to_unclamped_pixels = editor_core.expand_unit_box_to_unclamped_pixels
_transform_source_box_after_crop_padding = editor_core.transform_source_box_after_crop_padding
_detect_primary_bird_box            = editor_core.detect_primary_bird_box
_get_bird_detector_error_message    = editor_core.get_bird_detector_error_message
_extract_focus_point_for_display    = editor_core.get_focus_point_for_display
_resolve_focus_camera_type_from_metadata = editor_core.resolve_focus_camera_type_from_metadata
_CENTER_MODE_BIRD                   = editor_core.CENTER_MODE_BIRD
_CENTER_MODE_FOCUS                  = editor_core.CENTER_MODE_FOCUS
_CENTER_MODE_IMAGE                  = editor_core.CENTER_MODE_IMAGE
_CENTER_MODE_CUSTOM                 = editor_core.CENTER_MODE_CUSTOM
RATIO_FREE                          = editor_options.RATIO_FREE
OUTPUT_FORMAT_OPTIONS               = editor_options.OUTPUT_FORMAT_OPTIONS


class _BirdStampCropMixin:
    """Mixin: crop-box calculation, bird-box detection, UI-value helpers."""

    def _bird_box_for_path(self, path: Path, *, source_image: Image.Image | None = None) -> tuple[float, float, float, float] | None:
        signature = self._source_signature(path)
        if signature in self._bird_box_cache:
            return self._bird_box_cache[signature]

        image = source_image
        if image is None:
            try:
                image = decode_image(path, decoder="auto")
            except Exception:
                self._bird_box_cache[signature] = None
                return None

        bird_box = _detect_primary_bird_box(image)
        self._bird_box_cache[signature] = bird_box
        if bird_box is None and not self._bird_detect_error_reported and _get_bird_detector_error_message():
            self._set_status(f"鸟体识别不可用: {_get_bird_detector_error_message()}")
            self._bird_detect_error_reported = True
        return bird_box

    def _resolve_crop_targets_for_image_center(
        self,
        *,
        focus_point: tuple[float, float] | None,
        bird_box: tuple[float, float, float, float] | None,
    ) -> tuple[tuple[float, float], tuple[float, float, float, float] | None]:
        _ = focus_point, bird_box
        return ((0.5, 0.5), None)

    def _resolve_crop_targets_for_focus_center(
        self,
        *,
        focus_point: tuple[float, float] | None,
        bird_box: tuple[float, float, float, float] | None,
    ) -> tuple[tuple[float, float], tuple[float, float, float, float] | None]:
        if focus_point is not None:
            return (focus_point, None)
        if bird_box is not None:
            return (_box_center(bird_box), None)
        return ((0.5, 0.5), None)

    def _resolve_crop_targets_for_bird_center(
        self,
        *,
        focus_point: tuple[float, float] | None,
        bird_box: tuple[float, float, float, float] | None,
    ) -> tuple[tuple[float, float], tuple[float, float, float, float] | None]:
        if bird_box is not None:
            return (_box_center(bird_box), bird_box)
        if focus_point is not None:
            return (focus_point, None)
        return ((0.5, 0.5), None)

    def _resolve_crop_anchor_and_keep_box(
        self,
        *,
        path: Path | None,
        image: Image.Image,
        raw_metadata: dict[str, Any],
        center_mode: str,
        settings: dict[str, Any] | None = None,
    ) -> tuple[tuple[float, float], tuple[float, float, float, float] | None]:
        focus_camera_type = _resolve_focus_camera_type_from_metadata(raw_metadata)
        focus_point = _extract_focus_point_for_display(
            raw_metadata,
            image.width,
            image.height,
            camera_type=focus_camera_type,
        )
        mode = _normalize_center_mode(center_mode)
        if mode == _CENTER_MODE_CUSTOM and settings is not None:
            try:
                cx = float(settings.get("custom_center_x", 0.5))
                cy = float(settings.get("custom_center_y", 0.5))
            except Exception:
                cx, cy = 0.5, 0.5
            return ((cx, cy), None)

        bird_box: tuple[float, float, float, float] | None = None
        if path is not None:
            bird_box = self._bird_box_for_path(path, source_image=image)

        resolver_map = {
            _CENTER_MODE_IMAGE: self._resolve_crop_targets_for_image_center,
            _CENTER_MODE_FOCUS: self._resolve_crop_targets_for_focus_center,
            _CENTER_MODE_BIRD: self._resolve_crop_targets_for_bird_center,
        }
        resolver = resolver_map.get(mode, self._resolve_crop_targets_for_image_center)
        return resolver(focus_point=focus_point, bird_box=bird_box)

    def _compute_auto_bird_crop_plan(
        self,
        *,
        image: Image.Image,
        bird_box: tuple[float, float, float, float],
        ratio: float,
        inner_top: int,
        inner_bottom: int,
        inner_left: int,
        inner_right: int,
    ) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]:
        width, height = image.size
        if width <= 0 or height <= 0 or ratio <= 0:
            return (None, (0, 0, 0, 0))

        expanded_px = _expand_unit_box_to_unclamped_pixels(
            bird_box,
            width=width,
            height=height,
            top=inner_top,
            bottom=inner_bottom,
            left=inner_left,
            right=inner_right,
        )
        if expanded_px is None:
            return (None, (0, 0, 0, 0))

        keep_left, keep_top, keep_right, keep_bottom = expanded_px
        keep_w = max(1.0, keep_right - keep_left)
        keep_h = max(1.0, keep_bottom - keep_top)
        center_x = (keep_left + keep_right) * 0.5
        center_y = (keep_top + keep_bottom) * 0.5

        crop_w = keep_w
        crop_h = crop_w / ratio
        if crop_h < keep_h:
            crop_h = keep_h
            crop_w = crop_h * ratio

        crop_left = center_x - (crop_w * 0.5)
        crop_top = center_y - (crop_h * 0.5)
        crop_right = crop_left + crop_w
        crop_bottom = crop_top + crop_h

        outer_left = max(0, int(math.ceil(-crop_left)))
        outer_top = max(0, int(math.ceil(-crop_top)))
        outer_right = max(0, int(math.ceil(crop_right - width)))
        outer_bottom = max(0, int(math.ceil(crop_bottom - height)))

        padded_width = width + outer_left + outer_right
        padded_height = height + outer_top + outer_bottom
        if padded_width <= 0 or padded_height <= 0:
            return (None, (0, 0, 0, 0))

        crop_box = _normalize_unit_box(
            (
                (crop_left + outer_left) / float(padded_width),
                (crop_top + outer_top) / float(padded_height),
                (crop_right + outer_left) / float(padded_width),
                (crop_bottom + outer_top) / float(padded_height),
            )
        )
        return (crop_box, (outer_top, outer_bottom, outer_left, outer_right))

    def _compute_crop_plan_for_image(
        self,
        *,
        path: Path | None,
        image: Image.Image,
        raw_metadata: dict[str, Any],
        settings: dict[str, Any],
    ) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]:
        ratio = _parse_ratio_value(settings.get("ratio"))
        crop_box_raw = settings.get("crop_box")
        if crop_box_raw is not None and isinstance(crop_box_raw, (list, tuple)) and len(crop_box_raw) == 4:
            try:
                cb = (float(crop_box_raw[0]), float(crop_box_raw[1]), float(crop_box_raw[2]), float(crop_box_raw[3]))
                if _crop_box_has_effect(cb):
                    return _crop_plan_from_override(image.width, image.height, cb)
            except (TypeError, ValueError):
                pass
        if ratio is None or _is_ratio_free(ratio):
            return (None, (0, 0, 0, 0))

        anchor, keep_box = self._resolve_crop_anchor_and_keep_box(
            path=path,
            image=image,
            raw_metadata=raw_metadata,
            center_mode=str(settings.get("center_mode") or _CENTER_MODE_IMAGE),
            settings=settings,
        )

        if keep_box is not None:
            crop_box, outer_pad = self._compute_auto_bird_crop_plan(
                image=image,
                bird_box=keep_box,
                ratio=ratio,
                inner_top=_parse_padding_value(settings.get("crop_padding_top"), 0),
                inner_bottom=_parse_padding_value(settings.get("crop_padding_bottom"), 0),
                inner_left=_parse_padding_value(settings.get("crop_padding_left"), 0),
                inner_right=_parse_padding_value(settings.get("crop_padding_right"), 0),
            )
            if crop_box is not None:
                return (crop_box, outer_pad)
            # 自动模式失败时回退为普通裁切。

        crop_box = _compute_ratio_crop_box(
            width=image.width,
            height=image.height,
            ratio=ratio,
            anchor=anchor,
            keep_box=None,
        )
        if not _crop_box_has_effect(crop_box):
            return (None, (0, 0, 0, 0))
        return (crop_box, (0, 0, 0, 0))

    def _compute_crop_box_for_image(
        self,
        *,
        path: Path | None,
        image: Image.Image,
        raw_metadata: dict[str, Any],
        settings: dict[str, Any],
    ) -> tuple[float, float, float, float] | None:
        crop_box, _outer_pad = self._compute_crop_plan_for_image(
            path=path,
            image=image,
            raw_metadata=raw_metadata,
            settings=settings,
        )
        return crop_box

    def _current_crop_effect_box(self) -> tuple[float, float, float, float] | None:
        if self.current_path is None or self.current_source_image is None:
            return None
        settings = self._render_settings_for_path(self.current_path, prefer_current_ui=True)
        return self._compute_crop_box_for_image(
            path=self.current_path,
            image=self.current_source_image,
            raw_metadata=self.current_raw_metadata,
            settings=settings,
        )

    def _selected_ratio(self) -> float | None | str:
        value = self.ratio_combo.currentData()
        if value is None:
            return None
        if value is RATIO_FREE or value == RATIO_FREE:
            return RATIO_FREE
        try:
            return float(value)
        except Exception:
            return None

    def _selected_max_long_edge(self) -> int:
        value = self.max_edge_combo.currentData()
        try:
            return int(value)
        except Exception:
            return 0

    def _selected_output_suffix(self) -> str:
        value = str(self.output_format_combo.currentData() or "jpg").strip().lower()
        supported = [suffix for suffix, _label in OUTPUT_FORMAT_OPTIONS if suffix in {"jpg", "jpeg", "png"}]
        if not supported:
            supported = ["jpg", "png"]
        if value in supported:
            return value
        return supported[0]
