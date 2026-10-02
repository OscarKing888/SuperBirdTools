"""接力段的跟踪器、衔接匹配和整段变换；所有照片仍输出到原参考图的同一画布。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.reference_region_tracker import ReferenceRegionTracker
from birdstamp.image_dejitter.relay_anchors import (
    RELAY_ANCHORS_KEY, blended_alignment, compose, relay_segments, resolve_relay_anchors,
)
from birdstamp.image_dejitter.rigid_alignment import estimate_alignment, inverse_matrix
from .sequence_photo_error import SequencePhotoError, sequence_photo_errors
from .video_export_cancelled_error import VideoExportCancelledError


@dataclass
class RelayPlan:
    """keys/paths 为完整输入列表顺序；owners 只含接力段内的照片（含接力参考图本身）。"""

    keys: tuple
    paths: dict
    anchors: tuple
    owners: dict
    trackers: dict = field(default_factory=dict)
    sizes: dict = field(default_factory=dict)
    bridges: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)

    @classmethod
    def resolve(cls, jobs, reference, settings):
        paths = tuple(job.path for job in jobs)
        anchors = resolve_relay_anchors(settings.get(RELAY_ANCHORS_KEY), paths, reference)
        if not anchors:
            return None
        keys = tuple(path_key(path) for path in paths)
        reference_index = keys.index(path_key(Path(reference))) if path_key(Path(reference)) in keys else -1
        owners = {keys[i]: anchor for i, anchor in enumerate(relay_segments(len(keys), reference_index, anchors))
                  if anchor is not None}
        return cls(keys, dict(zip(keys, paths)), anchors, owners)

    def anchor_key(self, anchor):
        return self.keys[anchor.index]

    def segment_regions(self):
        """成片诊断用：接力段照片 → (接力参考图路径, 接力选区)。"""
        return {key: (str(anchor.path), anchor.regions) for key, anchor in self.owners.items()}

    def prepare_trackers(self, options, *, cancel_event, preview_source=None, photo_errors=None):
        """接力参考图读取失败时，交互预览把整段标为失败；否则按原契约抛出。"""
        for anchor in self.anchors:
            if cancel_event.is_set():
                raise VideoExportCancelledError('已取消去抖动分析。')
            key = self.anchor_key(anchor)
            try:
                with sequence_photo_errors(anchor.path), decode_image(anchor.path, decoder='auto') as image:
                    self.trackers[key] = ReferenceRegionTracker(image, anchor.regions, options=options)
                    self.sizes[key] = image.size
                    if preview_source is not None and not cancel_event.is_set():
                        preview_source(anchor.path, image)
            except SequencePhotoError as exc:
                if photo_errors is None:
                    raise
                for member, owner in self.owners.items():
                    if owner is anchor:
                        self.errors[member] = SequencePhotoError(
                            self.paths[member], f'接力参考图 {anchor.path.name} 无法读取：{exc.message}')
                photo_errors.update(self.errors)

    def segment_trackers(self):
        return {key: (self.trackers[self.anchor_key(anchor)], anchor.path)
                for key, anchor in self.owners.items() if self.anchor_key(anchor) in self.trackers}

    def track_bridges(self, tracking, *, cancel_event, progress_counts=lambda c, t, s: None, allow_partial=False):
        """衔接照片另匹配一次接力选区；不能用预测或插值位置把两段接起来。"""
        anchors = [a for a in self.anchors if self.anchor_key(a) in self.trackers
                   and self.keys[a.bridge] in tracking and self.anchor_key(a) in tracking]
        for done, anchor in enumerate(anchors):
            progress_counts(done, len(anchors), '衔接接力选区')
            if cancel_event.is_set():
                raise VideoExportCancelledError('已取消去抖动分析。')
            bridge = self.paths[self.keys[anchor.bridge]]
            try:
                with sequence_photo_errors(bridge), decode_image(bridge, decoder='auto') as image:
                    try:
                        result = self.trackers[self.anchor_key(anchor)].track(image, cancelled=cancel_event.is_set)
                    except InterruptedError as exc:
                        raise VideoExportCancelledError('已取消去抖动分析。') from exc
                    self.bridges[self.anchor_key(anchor)] = result
            except SequencePhotoError as exc:
                if not allow_partial:
                    raise
                self.bridges[self.anchor_key(anchor)] = exc
        if anchors:
            progress_counts(len(anchors), len(anchors), '衔接接力选区')

    def alignments(self, base_regions, tracking, sizes, reference_key, reference_size, settings, *, rigid, options):
        """接力段照片 → (按强度插值后的 FrameAlignment, 100% 平移 (dx, dy)) 或 SequencePhotoError。"""
        mode = 'rigid' if rigid else 'translation'
        memo = {}

        def fail(key, message):
            return SequencePhotoError(self.paths[key], message)

        def full(key):
            if key in memo:
                return memo[key]
            try:
                if key in self.errors:
                    raise self.errors[key]
                if key not in tracking:
                    raise fail(key, '该照片尚未完成分析。')
                anchor = self.owners.get(key)
                if anchor is None:
                    try:
                        part = estimate_alignment(base_regions, tracking[key], sizes[key], reference_size, mode=mode,
                                                  options=options, is_reference=key == reference_key)
                    except ValueError as exc:
                        raise fail(key, f'参考区失配：{tracking[key].error or exc}') from None
                    value = (part.source_to_reference, (part,))
                else:
                    anchor_key = self.anchor_key(anchor)
                    try:
                        part = estimate_alignment(anchor.regions, tracking[key], sizes[key], self.sizes[anchor_key],
                                                  mode=mode, options=options, is_reference=key == anchor_key)
                    except ValueError as exc:
                        raise fail(key, f'接力选区失配：{tracking[key].error or exc}。'
                                        '可在这张照片再次“接力追踪”。') from None
                    matrix, parts = anchor_full(anchor)
                    value = (compose(matrix, part.source_to_reference), (*parts, part))
            except SequencePhotoError as exc:
                value = exc
            memo[key] = value
            return value

        def anchor_full(anchor):
            anchor_key, bridge_key = self.anchor_key(anchor), self.keys[anchor.bridge]
            bridge = full(bridge_key)
            if isinstance(bridge, SequencePhotoError):
                raise bridge
            name = self.paths[bridge_key].name
            result = self.bridges.get(anchor_key)
            if isinstance(result, SequencePhotoError):
                raise fail(anchor_key, f'衔接照片 {name} 读取失败：{result.message}')
            if result is None:
                raise fail(anchor_key, f'衔接照片 {name} 尚未匹配接力选区。')
            try:
                part = estimate_alignment(anchor.regions, result, sizes[bridge_key], self.sizes[anchor_key],
                                          mode=mode, options=options)
            except ValueError as exc:
                raise fail(anchor_key, f'接力选区无法在衔接照片 {name} 中匹配：{result.error or exc}。'
                                       '请框选两张照片中都清晰可见、且与原选区同一运动的稳定纹理。') from None
            return compose(bridge[0], inverse_matrix(part.source_to_reference)), (*bridge[1], part)

        strength = settings.get('dejitter_reference_strength', 100)
        output = {}
        for key in self.owners:
            if key not in tracking and key not in self.errors:
                continue
            value = full(key)
            if isinstance(value, SequencePhotoError):
                output[key] = value
                continue
            matrix, parts = value
            fallback = next((p for p in parts if p.status == 'fallback'), None)
            status = 'fallback' if fallback else 'rigid' if rigid else 'translation'
            alignment = blended_alignment(matrix, sizes[key], strength, rigid=rigid, status=status,
                                          reason=f'接力链：{fallback.reason}' if fallback else '',
                                          indices=parts[-1].region_indices)
            output[key] = (alignment, (-matrix[2], -matrix[5]))
        return output
