"""去抖动接力追踪：失败照片上新增选区，作为后续照片的接力参考图。

每张照片按列表位置归属原参考图或某张接力参考图（“定义”）；选区绘制、逐编号手动修正、
诊断框和推荐选区都只使用该照片所属的定义，不把接力选区写进原参考选区。
"""
from __future__ import annotations

from pathlib import Path

from birdstamp.image_dejitter.relay_anchors import (
    relay_candidate_bridge, relay_record, relay_regions, relay_segments, relay_settings_value,
    resolve_relay_anchors,
)
from .editor_utils import path_key


class _BirdStampDejitterRelayMixin:
    """接力参考图状态；记录以路径为键，列表顺序变化时自动重新划分接力段。"""

    def _relay_enabled(self):
        controls = getattr(self, 'dejitter_subject_controls', None)
        return (controls is None or controls.method.currentData() != 'subject_local') and bool(
            getattr(self, '_dejitter_reference_source', None) and getattr(self, '_dejitter_reference_regions', ()))

    def _relay_plan(self):
        """(列表路径, path_key→位置, 接力参考图含未框选的, 生效归属, 含未框选的草稿归属)；无接力时不扫描列表。

        生效归属只计已框选的接力，与分析核心一致；未框选的接力只让自身可框选，不改变其后照片的定义。
        """
        records = getattr(self, '_dejitter_relay_anchors', None)
        if not records or not self._relay_enabled():
            return (), {}, (), (), ()
        paths = tuple(self._list_photo_paths())
        reference = self._dejitter_reference_source
        key = (tuple(map(str, paths)), reference, getattr(self, '_dejitter_relay_version', 0))
        cached = getattr(self, '_dejitter_relay_cache', None)
        if cached is not None and cached[0] == key:
            return cached[1]
        positions = {}
        for index, path in enumerate(paths):
            positions.setdefault(path_key(path), index)
        reference_index = positions.get(path_key(Path(reference)), -1)
        anchors = resolve_relay_anchors(list(records.values()), paths, reference, allow_empty=True)
        plan = (paths, positions, anchors,
                relay_segments(len(paths), reference_index, tuple(a for a in anchors if a.regions)),
                relay_segments(len(paths), reference_index, anchors))
        self._dejitter_relay_cache = (key, plan)
        return plan

    def _relay_changed(self):
        self._dejitter_relay_version = getattr(self, '_dejitter_relay_version', 0) + 1
        self._dejitter_relay_cache = None

    def _relay_position(self, path):
        return self._relay_plan()[1].get(path_key(Path(path))) if path is not None else None

    def _relay_anchor_for_path(self, path):
        """path 本身是有效接力参考图时返回 RelayAnchor（可能尚未框选）。"""
        index = self._relay_position(path)
        return next((a for a in self._relay_plan()[2] if a.index == index), None) if index is not None else None

    def _relay_owner_for_path(self, path, *, draft=False, effective=False):
        """path 所匹配的接力参考图；接力参考图自身归属自己以便框选，effective 时未框选的不算。"""
        anchor = self._relay_anchor_for_path(path)
        if anchor is not None and (anchor.regions or not effective):
            return anchor
        index = self._relay_position(path)
        owners = self._relay_plan()[4 if draft else 3]
        return owners[index] if index is not None and index < len(owners) else None

    def _definition_for_path(self, path, *, effective=False):
        """照片所属的 (定义源图, 选区)：接力段返回接力参考图，其余返回原参考图。

        effective 与分析核心一致，忽略未框选的接力参考图；用于决定哪些手动修正进入分析。
        """
        owner = self._relay_owner_for_path(path, effective=effective)
        if owner is not None:
            return str(owner.path), owner.regions
        return getattr(self, '_dejitter_reference_source', None), tuple(getattr(self, '_dejitter_reference_regions', ()))

    def _definition_regions_for_source(self, source):
        """按记录中的定义源图找选区；工作区恢复时尚不依赖列表顺序。"""
        if not source:
            return None
        reference = getattr(self, '_dejitter_reference_source', None)
        if reference and path_key(Path(reference)) == path_key(Path(source)):
            return tuple(self._dejitter_reference_regions)
        record = getattr(self, '_dejitter_relay_anchors', {}).get(path_key(Path(source)))
        return relay_regions(record) if record else None

    def _is_definition_photo(self, path):
        return self._is_reference_photo(path) or self._relay_anchor_for_path(path) is not None

    def _definition_paths_for_path(self, path):
        """与 path 使用同一组选区的照片，供推荐选区抽样；未用接力时为整组。"""
        paths, _, _, _, owners = self._relay_plan()
        if not paths:
            return tuple(self._list_photo_paths())
        # 未框选的接力参考图也按将来的接力段抽样，推荐结果才能覆盖它之后的照片。
        owner = self._relay_owner_for_path(path, draft=True)
        return tuple(p for p, o in zip(paths, owners) if o is owner)

    def _relay_settings_value(self):
        return relay_settings_value(list(getattr(self, '_dejitter_relay_anchors', {}).values()))

    def _restore_relay_anchors(self, value):
        self._dejitter_relay_anchors = {}
        for record in value if isinstance(value, (list, tuple)) else ():
            if isinstance(record, dict) and isinstance(record.get('path'), str) and relay_regions(record):
                self._dejitter_relay_anchors[path_key(Path(record['path']))] = dict(
                    path=record['path'], signature=record.get('signature'),
                    regions=tuple(relay_regions(record)))
        self._relay_changed()

    def _clear_relay_anchors(self):
        if getattr(self, '_dejitter_relay_anchors', None):
            self._dejitter_relay_anchors = {}
            self._relay_changed()

    def _drop_manual_matches_for_source(self, source):
        """接力选区变化后，以它为定义的逐图手动修正不再对应原编号。"""
        key = path_key(Path(source))
        for path in [k for k, r in self._dejitter_manual_matches.items()
                     if isinstance(r, dict) and r.get('reference') and path_key(Path(r['reference'])) == key]:
            self._dejitter_manual_matches.pop(path, None)

    def _commit_relay_regions(self, path, regions):
        key = path_key(Path(path))
        self._dejitter_relay_anchors[key] = relay_record(path, regions)
        self._relay_changed()
        self._drop_manual_matches_for_source(path)
        self._invalidate_reference_tracking('接力选区已变化，请重新分析。')
        self._sequence_message = (f'接力参考图 {Path(path).name}：{len(regions)} 个接力选区。点击分析按钮继续，'
                                  '之前已对齐的照片直接复用，只重新对齐使用接力选区的照片。'
                                  if regions else f'接力参考图 {Path(path).name} 尚未框选，分析时不会使用。')
        self._update_dejitter_reference_clear_enabled()
        self._apply_preview_overlay_options_from_ui()
        self._on_output_settings_changed()
        self._update_dejitter_controls()
        self._schedule_workspace_autosave()

    def _relay_add_block_reason(self, path, *, ignore_worker=False):
        """不能在 path 建立接力时返回原因；可以时返回空字符串。"""
        if not self._relay_enabled():
            return '请先在参考图框选原参考区；局部主体方法不支持接力。'
        if path is None:
            return '请先选择照片。'
        if self._sequence_shutdown or (self._sequence_worker is not None and not ignore_worker):
            return '请等待当前任务结束。'
        if self._is_reference_photo(path):
            return '参考图本身不需要接力。'
        if self._relay_anchor_for_path(path) is not None:
            return '当前照片已是接力参考图。'
        paths = tuple(self._list_photo_paths())
        keys = [path_key(p) for p in paths]
        if path_key(Path(path)) not in keys:
            return '当前照片不在列表中。'
        reference = path_key(Path(self._dejitter_reference_source))
        bridge = relay_candidate_bridge(keys.index(path_key(Path(path))), len(paths),
                                        keys.index(reference) if reference in keys else -1)
        if bridge is None:
            return '没有可衔接的相邻照片。'
        return ''

    def _relay_bridge_path(self, anchor):
        paths = self._relay_plan()[0]
        return paths[anchor.bridge] if anchor is not None and anchor.bridge < len(paths) else None

    def _on_dejitter_relay_add(self):
        path = self.current_path
        reason = self._relay_add_block_reason(path)
        if reason:
            self._sequence_message = f'无法接力追踪：{reason}'
            self._update_dejitter_controls()
            return
        # 空记录先成为可框选的接力参考图；框选前分析会忽略它。
        self._dejitter_relay_anchors[path_key(Path(path))] = relay_record(path, ())
        self._relay_changed()
        anchor = self._relay_anchor_for_path(path)
        bridge = self._relay_bridge_path(anchor)
        from .edit_modes import EDIT_MODE_REFERENCE_REGION
        self._set_dejitter_view('edit')
        self._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
        ab = getattr(self, 'ab_preview', None)
        if ab is not None and ab.enabled.isChecked() and bridge is not None:
            # A 显示衔接照片，便于挑选两张都清晰可见的纹理；B 保持当前接力参考图。
            ab.activate('b', sync_selection=False)
            ab.select_a(bridge)
        position = self._relay_position(path)
        self._sequence_message = (
            f'已将第 {position + 1} 张（{Path(path).name}）设为接力参考图。请在这张照片框选新的稳定纹理'
            f'（也须在衔接照片第 {anchor.bridge + 1} 张 {bridge.name} 中可见），然后重新分析；之后的照片改为匹配接力选区。')
        self._update_dejitter_reference_clear_enabled()
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)

    def _on_dejitter_relay_remove(self):
        # 接力段内任一张照片都可移除其所属接力，不必先找到接力参考图本身。
        anchor = self._relay_owner_for_path(self.current_path)
        if anchor is None:
            return
        path = anchor.path
        self._dejitter_relay_anchors.pop(path_key(Path(path)), None)
        self._relay_changed()
        self._drop_manual_matches_for_source(path)
        self._invalidate_reference_tracking('已移除接力，请重新分析。')
        self._sequence_message = f'已移除第 {anchor.index + 1} 张（{Path(path).name}）的接力，请重新分析。'
        self._update_dejitter_reference_clear_enabled()
        self._apply_preview_overlay_options_from_ui()
        self._on_output_settings_changed()
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)
        self._schedule_workspace_autosave()

    def _relay_status_text(self):
        """当前照片与接力的关系；整条链由接力链列表显示，这里只补充当前照片需要注意的事。"""
        if not self._relay_plan()[2]:
            return ''
        owner = self._relay_owner_for_path(self.current_path)
        if owner is None:
            return '当前照片匹配原参考图的选区。'
        bridge = self._relay_bridge_path(owner)
        if not owner.regions:
            return (f'当前接力参考图尚未框选；框选前分析时忽略，其后照片仍匹配上一组选区。'
                    + (f'\n新纹理须同时出现在衔接照片第 {owner.bridge + 1} 张（{bridge.name}）中。' if bridge else ''))
        if self._relay_anchor_for_path(self.current_path) is not None:
            return (f'这张是接力参考图，经衔接照片第 {owner.bridge + 1} 张（{bridge.name}）接回原参考图。'
                    if bridge else '这张是接力参考图。')
        return f'当前照片匹配第 {owner.index + 1} 张接力参考图的 {len(owner.regions)} 个接力选区。'

    def _region_group_status(self):
        """(状态, 悬停说明)：始终描述当前照片所用的选区组，与选区列表、清除按钮一致。"""
        source = getattr(self, '_dejitter_reference_source', None)
        regions = tuple(getattr(self, '_dejitter_reference_regions', ()))
        reference = f'{Path(source).name} · {len(regions)} 个选区' if source and regions else ''
        check = getattr(self, 'dejitter_reference_check', None)
        if reference and check is not None and not check.isChecked():
            reference += ' · 已停用'
        owner = self._relay_owner_for_path(getattr(self, 'current_path', None)) if reference else None
        if owner is None:
            return (f'参考图 {reference}' if reference else '尚未选择参考区'), str(source or '')
        count = f'{len(owner.regions)} 个接力选区' if owner.regions else '尚未框选'
        return (f'接力参考图 {owner.path.name}（第 {owner.index + 1} 张）· {count}',
                f'{owner.path}\n原参考图：{reference}')

    def _relay_chain_rows(self):
        """接力链列表：[(定义源图, 文本, 是否当前照片所属)]；没有接力时为空。

        覆盖范围按生效归属计算，与分析一致；未框选的接力只列出，不计范围。
        """
        paths, _, anchors, owners, _ = self._relay_plan()
        if not anchors:
            return []
        span = lambda indices: (f'第 {indices[0] + 1} 张' if len(indices) == 1 else
                                f'第 {indices[0] + 1}–{indices[-1] + 1} 张') if indices else '无'
        current = self._relay_owner_for_path(self.current_path)
        source = Path(self._dejitter_reference_source)
        reference_index = self._relay_position(source)
        where = f'第 {reference_index + 1} 张' if reference_index is not None else f'{source.name}（不在列表中）'
        rows = [(source, f'参考图 · {where} · {len(self._dejitter_reference_regions)} 个选区 · 用于'
                         f'{span([i for i, o in enumerate(owners) if o is None])}', current is None)]
        for anchor in anchors:
            if anchor.regions:
                used = span([i for i, o in enumerate(owners) if o is not None and o.index == anchor.index])
                text = f'接力 · 第 {anchor.index + 1} 张 · {len(anchor.regions)} 个选区 · 用于{used}'
            else:
                text = f'接力 · 第 {anchor.index + 1} 张 · 未框选（分析时忽略）'
            rows.append((anchor.path, text, current is not None and current.index == anchor.index))
        return rows
