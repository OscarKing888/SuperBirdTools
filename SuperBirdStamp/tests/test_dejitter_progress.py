"""真实 Qt 信号驱动分析/导出进度，迟到更新不能覆盖取消或失败状态。"""
import threading

import pytest

from test_dejitter_tab import setup_tab, analyze, wait_until
from test_editor_dejitter import window
from birdstamp.export_stage import sequence_analysis, sequence_export
from birdstamp.gui import editor_dejitter
from birdstamp.gui.editor_sequence_preview_worker import EditorSequencePreviewWorker, EditorSequenceExportWorker


def test_analysis_and_export_show_live_counts_then_completion(window, monkeypatch, tmp_path):
    paths, _, _ = setup_tab(window, monkeypatch)
    release = threading.Event()
    original_execute = sequence_analysis.SequenceAnalysisAction.execute

    def execute(action):
        assert release.wait(10)
        return original_execute(action)

    monkeypatch.setattr(sequence_analysis.SequenceAnalysisAction, 'execute', execute)
    window.dejitter_preprocess_btn.click()
    bar = window.dejitter_analysis_progress
    try:
        wait_until(lambda: bar.maximum() == 2 and bar.value() == 1)
        assert not bar.isHidden()
        assert '对齐照片' in bar.format()
        assert window._sequence_worker is not None
    finally:
        release.set()
        wait_until(lambda: window._sequence_worker is None)
    assert bar.value() == bar.maximum() == 2
    assert '分析完成' in bar.format()
    assert window._sequence_preview is not None

    release.clear()
    original_save = sequence_export.save_export_image

    def save(image, target, *, source_path, **kwargs):
        if source_path == paths[1]:
            assert release.wait(10)
        return original_save(image, target, source_path=source_path, **kwargs)

    monkeypatch.setattr(sequence_export, 'save_export_image', save)
    monkeypatch.setattr(editor_dejitter.QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path))
    window.dejitter_export_btn.click()
    export_bar = window.dejitter_export_progress
    try:
        wait_until(lambda: export_bar.maximum() == 2 and export_bar.value() == 1)
        assert not export_bar.isHidden()
        assert '导出图片' in export_bar.format()
        assert '分析完成' in bar.format()
    finally:
        release.set()
        wait_until(lambda: window._sequence_worker is None)
    assert export_bar.value() == export_bar.maximum() == 2
    assert '导出完成' in export_bar.format()
    assert len(list(tmp_path.glob('去抖动_*/*.png'))) == 2


@pytest.mark.parametrize('route', ['analysis', 'export'])
@pytest.mark.parametrize('cancel', [False, True])
def test_cancel_and_failure_stop_progress_and_reject_late_signals(window, monkeypatch, tmp_path, route, cancel):
    setup_tab(window, monkeypatch)
    if route == 'export':
        analyze(window)
    release = threading.Event()
    worker_class = EditorSequenceExportWorker if route == 'export' else EditorSequencePreviewWorker

    class DelayedWorker(worker_class):
        def run(self):
            self.progress_counts.emit(self.token, 1, 2, '测试阶段')
            release.wait(10)
            self.progress_counts.emit(self.token, 0, 0, '迟到阶段')
            self.failed.emit(self.token, '模拟任务失败')

    name = 'EditorSequenceExportWorker' if route == 'export' else 'EditorSequencePreviewWorker'
    monkeypatch.setattr(editor_dejitter, name, DelayedWorker)
    monkeypatch.setattr(editor_dejitter.QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path))
    button = window.dejitter_export_btn if route == 'export' else window.dejitter_preprocess_btn
    bar = window.dejitter_export_progress if route == 'export' else window.dejitter_analysis_progress
    button.click()
    worker = window._sequence_worker
    try:
        wait_until(lambda: bar.value() == 1 and bar.maximum() == 2)
        if cancel:
            window.dejitter_preprocess_btn.click()
            assert '已取消' in bar.format()
            assert window._sequence_worker is worker
    finally:
        release.set()
        wait_until(lambda: window._sequence_worker is None)
    assert bar.maximum() > 0  # 不留下永远运行的忙碌动画。
    assert ('已取消' if cancel else '失败') in bar.format()
    assert '迟到' not in bar.format()
    assert window._sequence_progress_kind is None


def test_sharp_upgrade_does_not_reset_completed_analysis_progress(window, monkeypatch):
    setup_tab(window, monkeypatch)
    analyze(window)
    bar = window.dejitter_analysis_progress
    before = (bar.minimum(), bar.maximum(), bar.value(), bar.format())
    window._sequence_frames.clear()
    window._sequence_frame_bytes = 0
    window._launch_sequence_worker()
    wait_until(lambda: window._sequence_worker is None)
    assert (bar.minimum(), bar.maximum(), bar.value(), bar.format()) == before
    window.dejitter_reference_strength_slider.setValue(50)
    assert bar.isHidden()
