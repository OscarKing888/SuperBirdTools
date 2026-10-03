"""预览来源异步动作：独立像素身份、降噪查找与普通快切缓存。"""
from PIL import Image
import pytest

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY
from birdstamp.decoders import preview_source
from birdstamp.gui import editor_preview_decode_worker as worker_module
from image_denoise.preview import DenoisedPreview
from image_denoise.types import DenoiseCancelled


@pytest.fixture(autouse=True)
def isolate_previews(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_module, "cached_preview_image", lambda *_args: None)
    monkeypatch.setattr(worker_module, "write_thumbnail", lambda *_args: True)
    monkeypatch.setattr(preview_source, "denoise_preview_history_path", lambda: tmp_path / "history.json")


def _execute(path, *, mode=None, show_raw=False, quick_only=False, edge=120):
    quick, full = [], []
    action = worker_module.EditorPreviewAction(
        path, edge, quick_only, lambda image, size: quick.append((image, size)),
        lambda image, size: full.append((image, size)), cancelled=lambda: False,
        show_raw=show_raw, source_mode=mode,
    )
    action.execute()
    return quick, full


def _close(*results):
    for images in results:
        for image, _size in images:
            image.close()


@pytest.mark.parametrize("mode", ["default", "raw", "denoised"])
def test_quick_preview_always_uses_default_source_and_never_resolves_denoise(tmp_path, monkeypatch, mode):
    path = tmp_path / "bird.ARW"
    path.write_bytes(b"raw")
    calls, written = [], []

    def decode(source, *, max_long_edge, decoder, **kwargs):
        calls.append((source, max_long_edge, kwargs))
        image = Image.new("RGB", (256, 128))
        image.info["birdstamp_source_properties"] = {"size": (1600, 800)}
        return image

    monkeypatch.setattr(worker_module, "decode_image_for_preview", decode)
    monkeypatch.setattr(worker_module, "load_denoised_preview", lambda *_a, **_k: pytest.fail("quick path resolved denoise"))
    monkeypatch.setattr(worker_module, "write_thumbnail", lambda source, image: written.append(source))
    quick, full = _execute(path, mode=mode, quick_only=True, edge=1600)
    try:
        assert calls == [(path, 256, {})]
        assert written == [path] and not full
        image, size = quick[0]
        assert size == (1600, 800)
        assert image.info[preview_source.PREVIEW_SOURCE_MODE_KEY] == "default"
        assert image.info[preview_source.PREVIEW_SOURCE_PATH_KEY] == str(path)
    finally:
        _close(quick, full)


def test_denoised_action_keeps_source_signal_identity_output_size_and_crop(tmp_path, monkeypatch):
    source, output = tmp_path / "鸟.ARW", tmp_path / "鸟_denoised.jpg"
    source.write_bytes(b"raw")
    Image.new("RGB", (720, 480), "blue").save(output)
    crop = (.1, .2, .9, .8)
    monkeypatch.setattr(preview_source, "find_denoised_preview", lambda *_a, **_k: DenoisedPreview(str(output), crop))
    monkeypatch.setattr(worker_module, "read_decoded_image_size", lambda *_a: pytest.fail("output size used original file"))
    worker = worker_module.EditorPreviewDecodeWorker(13, source, max_long_edge=120, source_mode="denoised")
    received = []
    worker.decoded.connect(lambda *args: received.append(args))
    worker.run()
    assert len(received) == 1
    token, path, image, size = received[0]
    try:
        assert (token, path) == (13, str(source))
        assert size == (720, 480) and image.size == (120, 80)
        assert image.info[RAW_FOCUS_CROP_KEY] == crop
        assert image.info[preview_source.PREVIEW_SOURCE_MODE_KEY] == "denoised"
        assert image.info[preview_source.PREVIEW_SOURCE_PATH_KEY] == str(output)
        assert image.info[preview_source.PREVIEW_SOURCE_MESSAGE_KEY] == ""
    finally:
        image.close()


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_corrupt_denoised_output_falls_back_with_visible_note(tmp_path, monkeypatch, missing):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (400, 200), "red").save(source)
    result = None if missing else DenoisedPreview(str(tmp_path / "missing-output.jpg"))
    monkeypatch.setattr(preview_source, "find_denoised_preview", lambda *_a, **_k: result)
    quick, full = _execute(source, mode="denoised")
    try:
        image, size = full[0]
        assert size == (400, 200)
        assert image.info[preview_source.PREVIEW_SOURCE_MODE_KEY] == "default"
        assert image.info[preview_source.PREVIEW_SOURCE_PATH_KEY] == str(source)
        assert "当前显示原图" in image.info[preview_source.PREVIEW_SOURCE_MESSAGE_KEY]
    finally:
        _close(quick, full)


def test_raw_legacy_api_and_explicit_default_override(monkeypatch, tmp_path):
    source = tmp_path / "bird.ARW"
    source.write_bytes(b"raw")
    calls = []

    def decode(path, **kwargs):
        calls.append(kwargs.get("show_raw", False))
        image = Image.new("RGB", (120, 80))
        image.info["birdstamp_source_properties"] = {"size": (6000, 4000)}
        if kwargs.get("show_raw"):
            image.info[RAW_FOCUS_CROP_KEY] = (.05, .05, .95, .95)
        return image

    monkeypatch.setattr(worker_module, "decode_image_for_preview", decode)
    first = _execute(source, show_raw=True)
    second = _execute(source, show_raw=True, mode="default")
    try:
        assert calls == [True, False]
        assert first[1][0][0].info[preview_source.PREVIEW_SOURCE_MODE_KEY] == "raw"
        assert RAW_FOCUS_CROP_KEY in first[1][0][0].info
        assert second[1][0][0].info[preview_source.PREVIEW_SOURCE_MODE_KEY] == "default"
    finally:
        _close(*first, *second)


def test_cancelled_denoised_action_does_not_decode_original(tmp_path, monkeypatch):
    path = tmp_path / "bird.ARW"
    path.write_bytes(b"raw")
    cancelled = [False]

    def resolve(*_args, **_kwargs):
        cancelled[0] = True
        return None

    monkeypatch.setattr(preview_source, "find_denoised_preview", resolve)
    monkeypatch.setattr(worker_module, "decode_image", lambda *_a, **_k: pytest.fail("cancelled action decoded original"))
    action = worker_module.EditorPreviewAction(
        path, 0, False, lambda *_a: pytest.fail("emitted quick"), lambda *_a: pytest.fail("emitted full"),
        cancelled=lambda: cancelled[0], source_mode="denoised",
    )
    with pytest.raises(DenoiseCancelled):
        action.execute()


def test_cached_quick_frame_is_not_decoded_again_and_cannot_override_denoised_size(tmp_path, monkeypatch):
    source = tmp_path / "bird.jpg"
    source.write_bytes(b"original")
    monkeypatch.setattr(worker_module, "cached_preview_image", lambda *_a: Image.new("RGB", (256, 128)))
    monkeypatch.setattr(worker_module, "read_decoded_image_size", lambda *_a: (1600, 800))
    monkeypatch.setattr(worker_module, "decode_image_for_preview", lambda *_a, **_k: pytest.fail("duplicate quick decode"))
    output = Image.new("RGB", (2400, 1600))
    preview_source.set_preview_source_info(output, tmp_path / "output.jpg", "denoised")
    monkeypatch.setattr(worker_module, "load_denoised_preview", lambda *_a, **_k: (output, ""))
    quick, full = _execute(source, mode="denoised", edge=0)
    try:
        assert len(quick) == 1 and quick[0][1] == (1600, 800)
        assert full[0][1] == (2400, 1600)
    finally:
        _close(quick, full)


def test_denoised_preview_reads_existing_viewer_history(tmp_path):
    import json
    from image_denoise.export import _prepare_sidecar

    source = tmp_path / "中文鸟.jpg"
    Image.new("RGB", (640, 480)).save(source)
    directory = tmp_path / "自选输出"
    directory.mkdir()
    output = directory / "不同名称.jpg"
    Image.new("RGB", (640, 480), "blue").save(output)
    _prepare_sidecar(source, output.with_suffix(".xmp"), 640, 480)
    (tmp_path / "history.json").write_text(json.dumps([[str(source), str(output)]], ensure_ascii=False), encoding="utf-8")
    image, note = preview_source.load_denoised_preview(source, max_long_edge=160)
    assert image is not None and not note
    try:
        assert image.info[preview_source.PREVIEW_SOURCE_PATH_KEY] == str(output)
    finally:
        image.close()
