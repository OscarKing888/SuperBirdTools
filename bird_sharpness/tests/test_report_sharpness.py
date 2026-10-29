"""Clarity persistence must keep the report's independent sharpness and its cache."""
from pathlib import Path

import pytest
from PIL import Image

from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
from bird_sharpness.actions import BirdSharpnessAction
from bird_sharpness.analyzer import BirdSharpnessResult
from bird_sharpness.scoring import ALGORITHM_VERSION
from bird_sharpness.xmp_store import browser_meta_updates, persisted_sharpness_updates, write_result


def _legacy(photo: Path, score=203):
    photo.with_suffix('.xmp').write_text(f'''<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
xmlns:superpicky="https://superbirdtools.local/xmp/superpicky/1.0/">
<photoshop:City>{score:.2f}</photoshop:City>
<superpicky:adj_sharpness>{score}</superpicky:adj_sharpness>
<superpicky:bird_species_cn>鹰鹃</superpicky:bird_species_cn>
<superpicky:bird_sharpness_score>{score}</superpicky:bird_sharpness_score>
<superpicky:bird_sharpness_verdict>usable</superpicky:bird_sharpness_verdict>
<superpicky:bird_sharpness_version>{ALGORITHM_VERSION}</superpicky:bird_sharpness_version>
</rdf:Description></rdf:RDF></x:xmpmeta>''', encoding='utf-8')


@pytest.mark.parametrize('score', [None, 0, 1000])
def test_analysis_updates_never_contain_report_sharpness(score):
    result = BirdSharpnessResult('bird.jpg', 'sharp', score=score)
    for updates in (result.to_xmp_fields(), browser_meta_updates(result)):
        assert all('City' not in key and key not in ('city', 'sharpness', 'adj_sharpness') for key in updates)


@pytest.mark.parametrize('original', [0.0, 712.375, None])
def test_recalculation_restores_original_sharpness_before_replacing_old_score(tmp_path, monkeypatch, original):
    photo = tmp_path / '中文鹰鹃.jpg'
    Image.new('RGB', (16, 12), 'gray').save(photo)
    before = photo.read_bytes()
    _legacy(photo)
    monkeypatch.setattr(PhotoMetaDataReportDB, '_row_for', lambda *_: {'adj_sharpness': original} if original is not None else None)
    result = BirdSharpnessResult(str(photo), 'sharp', score=577, head_score=600, body_score=500, mean_score=550.)
    assert write_result(str(photo), result)
    rec = PhotoMetaDataXMP().read(str(photo))
    assert rec['bird_sharpness_score'] == '577' and rec['bird_sharpness_mean_score'] == '550.0'
    patch = persisted_sharpness_updates(str(photo))
    if original is None:
        assert not rec.get('XMP-photoshop:City') and not rec.get('adj_sharpness')
        assert patch['XMP:City'] == patch['sharpness'] == patch['adj_sharpness'] == ''
    else:
        assert float(rec['XMP-photoshop:City']) == original
        assert float(rec['adj_sharpness']) == original
        assert float(patch['sharpness']) == original
    assert photo.read_bytes() == before


@pytest.mark.parametrize('write_xmp', [False, True])
def test_skip_existing_repairs_metadata_without_analysis_only_when_writing(tmp_path, monkeypatch, write_xmp):
    photo = tmp_path / '已检测.jpg'
    Image.new('RGB', (16, 12), 'gray').save(photo)
    _legacy(photo)
    original_sidecar = photo.with_suffix('.xmp').read_bytes()
    monkeypatch.setattr(PhotoMetaDataReportDB, '_row_for', lambda *_: {'adj_sharpness': 756.125})

    class NoAnalysis:
        version = ALGORITHM_VERSION

        def analyze(self, *_args, **_kwargs):
            pytest.fail('same-version metadata migration must not run the analyzer')

    outcome = BirdSharpnessAction(NoAnalysis(), str(photo), str(photo), skip_existing=True, write_xmp=write_xmp).execute()
    assert outcome.skipped and outcome.result is None and not outcome.metadata_error
    if write_xmp:
        assert float(outcome.metadata_updates['sharpness']) == 756.125
        assert PhotoMetaDataXMP().read(str(photo))['bird_sharpness_score'] == '203'
        assert 'write' in outcome.timing.stages
    else:
        assert outcome.metadata_updates == {}
        assert photo.with_suffix('.xmp').read_bytes() == original_sidecar


def test_skip_repair_failure_keeps_cache_and_sidecar(tmp_path, monkeypatch):
    photo = tmp_path / '修复失败.jpg'
    Image.new('RGB', (16, 12), 'gray').save(photo)
    _legacy(photo)
    before = photo.with_suffix('.xmp').read_bytes()
    monkeypatch.setattr(PhotoMetaDataXMP, 'ensure_report_sharpness', lambda *_: False)
    outcome = BirdSharpnessAction(object(), str(photo), str(photo), skip_existing=True).execute()
    assert outcome.skipped and outcome.metadata_error and not outcome.metadata_updates
    assert photo.with_suffix('.xmp').read_bytes() == before


def test_unreadable_persisted_metadata_is_not_reported_as_a_clear(tmp_path):
    photo = tmp_path / 'damaged.jpg'
    photo.with_suffix('.xmp').write_text('<broken', encoding='utf-8')
    with pytest.raises(OSError):
        persisted_sharpness_updates(str(photo))


def test_explicit_sharpness_clear_also_clears_cached_report_fallbacks(tmp_path, monkeypatch):
    from app_common.file_browser._browser_core import _metadata_sharpness_text

    photo = tmp_path / '清空锐度.jpg'
    Image.new('RGB', (16, 12), 'gray').save(photo)
    monkeypatch.setattr(PhotoMetaDataReportDB, '_row_for', lambda *_: {'adj_sharpness': 700, 'head_sharp': 600})
    writer = PhotoMetaDataXMP()
    assert writer.write(str(photo), {'XMP-dc:Title': '白鹭'})
    assert writer.write(str(photo), {'XMP-photoshop:City': ''})
    cache = {'XMP:City': 700, 'sharpness': 700, 'adj_sharpness': 700, 'head_sharp': 600, 'IPTC:City': 500}
    cache.update(persisted_sharpness_updates(str(photo)))
    assert _metadata_sharpness_text(cache) == ''
