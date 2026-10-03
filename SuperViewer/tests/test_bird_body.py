from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import threading

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
from app_common.file_browser._work_pool import BrowserWorkPool, WorkKind
from SuperViewer.superviewer import bird_body as core
from SuperViewer.superviewer import bird_body_worker as worker


@pytest.fixture
def photo(tmp_path, monkeypatch):
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    path = tmp_path / "翠鸟照片.jpg"
    Image.new("RGB", (60, 40), "gray").save(path)
    return path


class Detector:
    def __init__(self, box=(0.1, 0.2, 0.6, 0.8), callback=lambda: None):
        self.box, self.callback, self.calls = box, callback, 0

    def detect(self, _image):
        self.calls += 1
        self.callback()
        return self.box


@pytest.mark.parametrize("box", [(0.1, 0.2, 0.6, 0.8), None])
def test_xmp_cache_roundtrip_keeps_chinese_metadata_and_negative_detection(photo, box):
    meta = PhotoMetaDataXMP()
    assert meta.write_title(str(photo), "翠鸟捕食")
    assert meta.write_subjects(str(photo), ["野生鸟类", "池塘"])
    original = photo.read_bytes()
    detector = Detector(box)

    first = worker.BirdBodyAction(str(photo), detector=detector).execute()
    second = worker.BirdBodyAction(str(photo), detector=detector).execute()

    assert first.written and not first.cache_hit and not first.error
    assert first.result.box == box
    assert second.cache_hit and second.result == first.result
    assert detector.calls == 1
    rec = meta.read(str(photo))
    assert rec["Title"] == "翠鸟捕食"
    assert meta.read_subjects(str(photo)) == ["野生鸟类", "池塘"]
    assert core.result_from_metadata(str(photo), rec) == first.result
    assert photo.read_bytes() == original


def test_cache_invalidates_changed_source_and_distinguishes_same_stem_formats(photo):
    detector = Detector()
    first = worker.BirdBodyAction(str(photo), detector=detector).execute()
    stamp = photo.stat().st_mtime_ns
    os.utime(photo, ns=(stamp + 1_000_000, stamp + 1_000_000))
    changed = worker.BirdBodyAction(str(photo), detector=detector).execute()
    assert changed.written and not changed.cache_hit and detector.calls == 2
    assert first.result.source_fingerprint != changed.result.source_fingerprint
    assert core.cache_field(str(photo)) != core.cache_field(str(photo.with_suffix(".arw")))


@pytest.mark.parametrize("changes", [
    {"version": "old"}, {"source": "wrong"}, {"geometry": "sensor"},
    {"box": [0.5, 0.5, 0.2, 0.2]}, {"box": [0, 0, float("nan"), 1]},
    {"box": [-1, 0, 1, 1]}, {"box": [0, 1]}, {"box": "none"},
])
def test_bad_cache_is_miss(photo, changes):
    fingerprint = core.source_fingerprint(str(photo))
    data = json.loads(core.BirdBodyResult(None, fingerprint).to_json())
    data.update(changes)
    rec = {core.cache_field(str(photo)): json.dumps(data)}
    assert core.result_from_metadata(str(photo), rec, fingerprint) is None


def test_cancel_and_source_changed_during_detection_never_publish(photo):
    stopped = threading.Event()
    before = worker.BirdBodyAction(str(photo), cancelled=lambda: True, detector=Detector()).execute()
    assert before.cancelled
    detector = Detector(callback=stopped.set)
    late = worker.BirdBodyAction(str(photo), cancelled=stopped.is_set, detector=detector).execute()
    assert late.cancelled and late.result is None
    assert not photo.with_suffix(".xmp").exists()

    mutate = Detector(callback=lambda: photo.write_bytes(b"different source"))
    changed = worker.BirdBodyAction(str(photo), detector=mutate).execute()
    assert changed.cancelled and not photo.with_suffix(".xmp").exists()


def test_missing_model_or_failure_does_not_cache_no_bird(photo):
    def fail():
        raise RuntimeError("YOLO 模型不可用")
    outcome = worker.BirdBodyAction(str(photo), detector=Detector(callback=fail)).execute()
    assert outcome.result is None and "YOLO 模型不可用" in outcome.error
    assert not outcome.written and not photo.with_suffix(".xmp").exists()


def test_write_failure_still_returns_detected_box_without_fabricating_cache(photo, monkeypatch):
    monkeypatch.setattr(PhotoMetaDataXMP, "write", lambda *_args: False)
    outcome = worker.BirdBodyAction(str(photo), detector=Detector()).execute()
    assert outcome.result.box == Detector().box
    assert not outcome.written and outcome.error
    assert not photo.with_suffix(".xmp").exists()


def test_read_only_detection_does_not_create_sidecar(photo):
    outcome = worker.BirdBodyAction(str(photo), detector=Detector(), write_xmp=False).execute()
    assert outcome.result and not outcome.written and not outcome.error
    assert not photo.with_suffix(".xmp").exists()


def test_image_orientation_and_raw_embedded_preview_are_bounded(photo, monkeypatch):
    from app_common import thumb_stream
    image = Image.new("RGB", (2000, 1000), "gray")
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    monkeypatch.setattr(thumb_stream, "get_raw_preview_jpeg", lambda _path: buffer.getvalue())
    small, crop = core.load_detection_image(str(photo.with_suffix(".arw")))
    try:
        assert small.size == (640, 1280) and crop is None
    finally:
        small.close()


def test_raw_fallback_box_roundtrip_uses_pixel_crop_geometry():
    from app_common.raw_preview_geometry import map_camera_focus_box
    crop = (0.1, 0.05, 0.9, 0.95)
    camera_box = (0.25, 0.25, 0.75, 0.75)
    sensor_box = map_camera_focus_box(camera_box, crop)
    assert core.camera_box_from_raw(sensor_box, crop) == pytest.approx(camera_box)
    assert core.camera_box_from_raw((0.0, 0.0, 0.05, 0.02), crop) is None


def test_model_device_failure_retries_on_cpu_and_does_not_download():
    calls = []

    class Model:
        def predict(self, *, device, **kwargs):
            calls.append((device, kwargs["classes"]))
            if device != "cpu":
                raise RuntimeError("CUDA unavailable")
            return []

    detector = core.BirdBodyDetector()
    detector._model, detector._device, detector._classes = Model(), "cuda:0", (14,)
    with Image.new("RGB", (30, 20)) as image:
        assert detector.detect(image) is None
        assert detector.detect(image) is None
    assert calls == [("cuda:0", [14]), ("cpu", [14]), ("cpu", [14])]


def test_cancelled_model_waiter_skips_loading_and_inference(monkeypatch):
    detector = core.BirdBodyDetector()
    stopped = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(detector, "_load", lambda: pytest.fail("cancelled waiter loaded a model"))

    def wait_for_detector():
        entered.set()
        with Image.new("RGB", (30, 20)) as image:
            return detector.detect(image, cancelled=stopped.is_set)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with detector._lock:
            future = executor.submit(wait_for_detector)
            assert entered.wait(5)
            stopped.set()
        with pytest.raises(InterruptedError):
            future.result(timeout=5)


def test_cancelled_after_model_load_does_not_start_inference(monkeypatch):
    detector = core.BirdBodyDetector()
    stopped = threading.Event()
    monkeypatch.setattr(detector, "_load", stopped.set)
    with Image.new("RGB", (30, 20)) as image, pytest.raises(InterruptedError):
        detector.detect(image, cancelled=stopped.is_set)


def test_action_executes_in_browser_analysis_pool(photo):
    gui_thread = threading.get_ident()
    executed = []
    pool = BrowserWorkPool(4, metadata_workers=2, analysis_workers=1)
    try:
        detector = Detector(callback=lambda: executed.append(threading.get_ident()))
        result = pool.submit_action(worker.BirdBodyAction(str(photo), detector=detector),
                                    kind=WorkKind.ANALYSIS).result(timeout=5)
        assert result.written and executed and executed[0] != gui_thread
    finally:
        assert pool.shutdown(timeout=5)


def test_source_recheck_after_waiting_for_sidecar_lock(photo, monkeypatch):
    original_fingerprint = worker.source_fingerprint
    checked = threading.Event()
    checks = []

    def fingerprint(path):
        checks.append(path)
        result = original_fingerprint(path)
        if len(checks) == 2:
            checked.set()
        return result

    monkeypatch.setattr(worker, "source_fingerprint", fingerprint)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with worker.xmp_sidecar_write_lock(str(photo)):
            future = executor.submit(worker.BirdBodyAction(str(photo), detector=Detector()).execute)
            assert checked.wait(5)
            photo.write_bytes(b"replaced while waiting to publish")
        outcome = future.result(timeout=5)
    assert outcome.cancelled and not photo.with_suffix(".xmp").exists()
