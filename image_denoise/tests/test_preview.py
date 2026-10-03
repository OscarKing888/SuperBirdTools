"""降噪预览来源校验、旧成片兼容及实际 RAW 几何回归。"""
import os
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import pytest

from image_denoise import DenoiseOptions, denoise_file
from image_denoise import preview
from image_denoise.export import _prepare_sidecar


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    from collections import OrderedDict
    monkeypatch.setattr(preview, "_recent", OrderedDict())


def _output(source, destination, *, crop=None, legacy=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 16), "blue").save(destination)
    _prepare_sidecar(source, destination.with_suffix(".xmp"), 24, 16, camera_crop=crop)
    if legacy:
        tree = ET.parse(destination.with_suffix(".xmp"))
        for node in tree.getroot().iter():
            for child in list(node):
                if child.tag.startswith("{" + preview.NAMESPACE + "}denoise_"):
                    node.remove(child)
        tree.write(destination.with_suffix(".xmp"), encoding="utf-8")
    return destination


def test_source_provenance_chinese_readback_crop_and_stale_rejection(tmp_path):
    source = tmp_path / "中文 鸟.ARW"
    source.write_bytes(b"raw source")
    sidecar = source.with_suffix(".xmp")
    sidecar.write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
                       'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
                       'xmlns:s="https://superbirdtools.local/xmp/superpicky/1.0/" '
                       's:bird_species_cn="白鹭"/></rdf:RDF></x:xmpmeta>', encoding="utf-8")
    before = sidecar.read_bytes()
    target = _output(source, tmp_path / "denoised" / "中文 鸟_denoised.jpg", crop=(.1, .2, .9, .8))
    found = preview.find_denoised_preview(source)
    assert found.path == str(target) and found.camera_crop == (.1, .2, .9, .8)
    assert not found.legacy and sidecar.read_bytes() == before
    values = preview._output_metadata(target)
    assert values["bird_species_cn"] == "白鹭"
    assert values["denoise_source_path"] == str(source.resolve())
    source.write_bytes(b"a changed source")
    assert preview.find_denoised_preview(source) is None


def test_fixed_directory_disambiguates_same_stem_and_prefers_latest(tmp_path):
    sources = [tmp_path / name / "same.jpg" for name in ("one", "two")]
    for source in sources:
        source.parent.mkdir()
        source.write_bytes(b"input")
    fixed = tmp_path / "fixed"
    wrong = _output(sources[1], fixed / "same_denoised.jpg")
    older = _output(sources[0], fixed / "same_denoised_2.jpg")
    newest = _output(sources[0], fixed / "same_denoised_3.jpg")
    os.utime(older, ns=(1, 1))
    os.utime(newest, ns=(2, 2))
    options = DenoiseOptions(output_mode="fixed", output_directory=str(fixed))
    assert preview.find_denoised_preview(sources[0], options).path == str(newest)
    assert preview.find_denoised_preview(sources[1], options).path == str(wrong)


def test_legacy_requires_creator_local_subdir_and_unambiguous_stem(tmp_path):
    source = tmp_path / "a.jpg"
    source.write_bytes(b"input")
    output = _output(source, tmp_path / "denoised" / "a_denoised.jpg", legacy=True)
    assert preview.find_denoised_preview(source).legacy
    (tmp_path / "a.ARW").write_bytes(b"ambiguous same shot")
    assert preview.find_denoised_preview(source) is None
    (tmp_path / "a.ARW").unlink()
    fixed = tmp_path / "other" / "fixed"
    _output(source, fixed / "a_denoised.jpg", legacy=True)
    output.unlink()
    assert preview.find_denoised_preview(source, DenoiseOptions(output_directory=str(fixed))) is None


def test_session_registry_finds_ask_directory_and_rejects_wrong_source(tmp_path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"input")
    output = _output(source, tmp_path / "任意目录" / "custom.jpg")
    assert preview.find_denoised_preview(source) is None
    preview.register_denoised_output(source, output)
    assert preview.find_denoised_preview(source).path == str(output)
    other = tmp_path / "other.jpg"
    other.write_bytes(b"input")
    preview.register_denoised_output(other, output)
    assert preview.find_denoised_preview(other) is None


def test_source_change_during_denoise_does_not_publish_old_pixels(tmp_path):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (24, 16)).save(source)

    class Engine:
        def denoise(self, rgb, **kwargs):
            source.write_bytes(b"replacement")
            return np.array(rgb), "cpu", 16

    target = tmp_path / "output.tif"
    result = denoise_file(source, target, DenoiseOptions(), engine=Engine())
    assert result.status == "failed" and "原图已变化" in result.error
    assert not target.exists() and not target.with_suffix(".xmp").exists()


def test_registry_has_bounded_capacity_and_registration_does_no_stat(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "_RECENT_LIMIT", 2)
    monkeypatch.setattr(type(tmp_path), "stat", lambda *_args: pytest.fail("GUI registration must not stat"))
    for i in range(3):
        preview.register_denoised_output(tmp_path / str(i), tmp_path / f"out{i}")
    assert len(preview._recent) == 2
    assert preview._key(tmp_path / "0") not in preview._recent
