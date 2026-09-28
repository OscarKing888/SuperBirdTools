"""独立导出充分使用逻辑核心，同时保留大画幅的内存约束。"""
from types import SimpleNamespace
import sys

import pytest

from birdstamp.export_stage import sequence_export_workers as policy


def machine(monkeypatch, cpus=32, available_gib=128):
    monkeypatch.setattr(policy.os, 'process_cpu_count', lambda: cpus, raising=False)
    monkeypatch.setattr(policy.os, 'cpu_count', lambda: 64)
    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(
        virtual_memory=lambda: SimpleNamespace(available=int(available_gib * 1024**3))))


def test_auto_uses_all_logical_cores_with_no_eight_worker_or_four_gib_cap(monkeypatch):
    machine(monkeypatch)
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=45_000_000) == 32
    assert policy.resolve_sequence_export_workers(0, 7, max_frame_pixels=45_000_000) == 7


def test_auto_respects_process_cpu_quota_and_older_python_fallback(monkeypatch):
    machine(monkeypatch, cpus=6)
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=1_000_000) == 6
    monkeypatch.delattr(policy.os, 'process_cpu_count')
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=1_000_000) == 64
    monkeypatch.setattr(policy.os, 'cpu_count', lambda: None)
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=1_000_000) == 1


def test_memory_budget_is_half_current_available_ram_and_includes_large_frames(monkeypatch):
    machine(monkeypatch, available_gib=8)
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=80_000_000) == 2
    machine(monkeypatch, available_gib=1)
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=80_000_000) == 1
    machine(monkeypatch, available_gib=0)
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=80_000_000) == 1


def test_explicit_setting_remains_available_for_api_callers(monkeypatch):
    machine(monkeypatch, cpus=8, available_gib=1)
    assert policy.resolve_sequence_export_workers(12, 20, max_frame_pixels=45_000_000) == 12
    assert policy.resolve_sequence_export_workers(12, 4, max_frame_pixels=45_000_000) == 4
    assert policy.resolve_sequence_export_workers(0, 0) == 1


@pytest.mark.parametrize('missing', [True, False])
def test_unavailable_memory_probe_uses_bounded_bytes_not_fixed_thread_count(monkeypatch, missing):
    machine(monkeypatch)
    def failed_probe():
        raise OSError('memory unavailable')
    monkeypatch.setitem(sys.modules, 'psutil', None if missing else SimpleNamespace(virtual_memory=failed_probe))
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=1_000_000) == 32
    assert policy.resolve_sequence_export_workers(0, 100, max_frame_pixels=80_000_000) == 1
    assert policy.resolve_sequence_export_workers(0, 100) == 3
