"""editor_crop_calculator.py – _BirdStampCropMixin

Crop-box calculation, bird-box detection, and UI-value accessor helpers.
Mixed into BirdStampEditorWindow via multiple inheritance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from birdstamp import perf as birdstamp_perf
from birdstamp.gui import editor_core, editor_options
from birdstamp.gui.edit_modes import EDIT_MODE_CROP_ADJUST

_parse_ratio_value                  = editor_core.parse_ratio_value
_detect_primary_bird_box            = editor_core.detect_primary_bird_box
_get_bird_detector_error_message    = editor_core.get_bird_detector_error_message
_extract_focus_point_for_display    = editor_core.get_focus_point_for_display
_resolve_focus_camera_type_from_metadata = editor_core.resolve_focus_camera_type_from_metadata
_normalize_center_mode              = editor_core.normalize_center_mode
_compute_crop_plan_for_image        = editor_core.compute_crop_plan_for_image
_CENTER_MODE_BIRD                   = editor_core.CENTER_MODE_BIRD
_CENTER_MODE_FOCUS                  = editor_core.CENTER_MODE_FOCUS
_CENTER_MODE_IMAGE                  = editor_core.CENTER_MODE_IMAGE
OUTPUT_FORMAT_OPTIONS               = editor_options.OUTPUT_FORMAT_OPTIONS


class _BirdStampCropMixin:
    """Mixin: crop-box calculation, bird-box detection, UI-value helpers."""

    def _bird_box_for_path(self, path: Path, *, source_image: Image.Image | None = None) -> tuple[float, float, float, float] | None:
        if callable(getattr(self, "_is_placeholder_active", None)) and self._is_placeholder_active():
            return None

        signature = self._source_signature(path)
        if signature in self._bird_box_cache:
            birdstamp_perf.plog("bird_box cache_hit path=%s", path)
            return self._bird_box_cache[signature]

        with birdstamp_perf.span("bird_box", path=str(path), from_cache=False):
            image = source_image
            if image is None:
                try:
                    image = self._decode_image_for_path(path)
                    birdstamp_perf.plog("bird_box extra_decode path=%s", path)
                except Exception:
                    self._bird_box_cache[signature] = None
                    return None

            bird_box = _detect_primary_bird_box(image)
        self._bird_box_cache[signature] = bird_box
        if bird_box is None and not self._bird_detect_error_reported and _get_bird_detector_error_message():
            self._set_status(f"鸟体识别不可用: {_get_bird_detector_error_message()}")
            self._bird_detect_error_reported = True
        return bird_box

    def _resolve_bird_box_for_crop_plan(
        self,
        *,
        path: Path | None,
        image: Image.Image,
        raw_metadata: dict[str, Any],
        center_mode: str,
    ) -> tuple[float, float, float, float] | None:
        mode = _normalize_center_mode(center_mode)
        focus_point = _extract_focus_point_for_display(
            raw_metadata,
            image.width,
            image.height,
            camera_type=_resolve_focus_camera_type_from_metadata(raw_metadata),
        )
        needs_bird_box = mode == _CENTER_MODE_BIRD or (
            mode == _CENTER_MODE_FOCUS and focus_point is None
        )
        if not needs_bird_box or path is None:
            return None
        return self._bird_box_for_path(path, source_image=image)

    def _crop_edit_mode_active(self) -> bool:
        getter = getattr(self, "_current_edit_mode_id", None)
        if not callable(getter):
            return False
        try:
            return getter() == EDIT_MODE_CROP_ADJUST
        except Exception:
            return False

    def _crop_plan_source_size(self, path: Path | None) -> tuple[int, int] | None:
        if path is None:
            return None
        current_path = getattr(self, "current_path", None)
        if current_path is None or Path(path) != Path(current_path):
            return None
        full_size = getattr(self, "current_source_full_size", None)
        if not full_size:
            return None
        return (int(full_size[0]), int(full_size[1]))

    def _compute_crop_plan_for_image(
        self,
        *,
        path: Path | None,
        image: Image.Image,
        raw_metadata: dict[str, Any],
        settings: dict[str, Any],
    ) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]:
        bird_box = self._resolve_bird_box_for_crop_plan(
            path=path,
            image=image,
            raw_metadata=raw_metadata,
            center_mode=str(settings.get("center_mode") or _CENTER_MODE_IMAGE),
        )
        return _compute_crop_plan_for_image(
            image=image,
            raw_metadata=raw_metadata,
            settings=settings,
            bird_box=bird_box,
            crop_edit_active=self._crop_edit_mode_active(),
            source_size=self._crop_plan_source_size(path),
        )

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
        return _parse_ratio_value(self.ratio_combo.currentData())

    def _selected_max_long_edge(self) -> int:
        value = self.max_edge_combo.currentData()
        try:
            return int(value)
        except Exception:
            return 0

    def _selected_output_suffix(self) -> str:
        buttons = getattr(self, "output_format_buttons", None)
        if isinstance(buttons, dict):
            for suffix, button in buttons.items():
                try:
                    if button.isChecked():
                        value = str(suffix or "").strip().lower()
                        return "jpg" if value == "jpeg" else value
                except Exception:
                    continue
        combo = getattr(self, "output_format_combo", None)
        value = str(combo.currentData() if combo is not None else "jpg").strip().lower()
        if value == "jpeg":
            value = "jpg"
        supported = [suffix for suffix, _label in OUTPUT_FORMAT_OPTIONS if suffix in {"jpg", "jpeg", "png"}]
        if not supported:
            supported = ["jpg", "png"]
        if value in supported:
            return value
        return supported[0]
