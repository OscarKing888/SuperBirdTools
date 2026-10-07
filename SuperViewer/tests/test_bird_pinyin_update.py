"""真实中文 XMP 的拼音补全与原图、报告和并发编辑保护。"""
import json
from pathlib import Path
import sqlite3

from PIL import Image
import pytest
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import bird_pinyin_update as update


@pytest.fixture
def photo(tmp_path, monkeypatch):
    path = tmp_path / '中文照片.jpg'
    Image.new('RGB', (24, 18)).save(path)
    monkeypatch.setattr(update.PhotoMetaDataEXIFEmbeded, 'read', lambda *_: {})
    return path


def test_updates_pinyin_name_utf8_and_preserves_other_metadata(photo):
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': '白头鹎', 'XMP-dc:Description': '保留中文注释',
                                   'XMP-dc:Subject': ['鸟类'], 'XMP-xmp:Rating': 4})
    original = photo.read_bytes()
    result = update.PinyinUpdater().update(str(photo))
    assert result.status == 'success', result.message
    meta = store.read(str(photo))
    assert meta['pinyin_name'] == 'bái tóu bēi'
    assert meta['pinyin_name_source'] == '白头鹎'
    assert meta['Description'] == '保留中文注释' and meta['rating'] == 4
    assert store.read_subjects(str(photo)) == ['鸟类']
    assert photo.read_bytes() == original
    assert 'bái tóu bēi' in photo.with_suffix('.xmp').read_text(encoding='utf-8')
    assert 'bird_species_pinyin' not in meta
    before = photo.with_suffix('.xmp').read_bytes()
    assert update.PinyinUpdater().update(str(photo)).status == 'skipped'
    assert photo.with_suffix('.xmp').read_bytes() == before


@pytest.mark.parametrize('alias', ['pinyin_name', 'bird_species_pinyin', 'bird_pinyin', 'pinyin'])
def test_existing_alias_pinyin_is_not_overwritten(photo, alias):
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': '家燕', f'XMP-superpicky:{alias}': '自定义拼音'})
    before = photo.with_suffix('.xmp').read_bytes()
    assert update.PinyinUpdater().update(str(photo)).status == 'skipped'
    assert photo.with_suffix('.xmp').read_bytes() == before


def test_name_change_makes_pinyin_updatable(photo):
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), '家燕')
    assert update.PinyinUpdater().update(str(photo)).status == 'success'
    assert store.write_title(str(photo), '白头鹎')
    assert update.PinyinUpdater().update(str(photo)).status == 'success'
    assert store.read(str(photo))['pinyin_name'] == 'bái tóu bēi'


@pytest.mark.parametrize('name', ['', '词表未知鸟名'])
def test_no_name_or_unknown_name_does_not_create_sidecar(photo, monkeypatch, name):
    monkeypatch.setattr(update.PhotoMetaDataEXIFEmbeded, 'read', lambda *_: {'Title': name})
    result = update.PinyinUpdater().update(str(photo))
    assert result.status == 'skipped'
    assert not photo.with_suffix('.xmp').exists()


def test_read_only_report_fallback_and_xmp_title_priority(photo):
    report = photo.parent / '.superpicky/report.db'
    report.parent.mkdir()
    with sqlite3.connect(report) as db:
        db.execute('CREATE TABLE photos (filename TEXT, current_path TEXT, bird_species_cn TEXT, pinyin_name TEXT)')
        db.execute('INSERT INTO photos VALUES (?,?,?,?)', (photo.stem, photo.name, '家燕', '旧拼音'))
    before = report.read_bytes()
    assert update.PinyinUpdater().update(str(photo)).status == 'skipped'
    assert PhotoMetaDataXMP().write_title(str(photo), '白头鹎')
    result = update.PinyinUpdater().update(str(photo))
    assert result.status == 'success', result.message
    assert PhotoMetaDataXMP().read(str(photo))['pinyin_name'] == 'bái tóu bēi'
    assert report.read_bytes() == before


@pytest.mark.parametrize('failure', ['corrupt', 'write', 'changed', 'removed', 'cancelled'])
def test_failure_cancel_and_inflight_change_preserve_files(photo, monkeypatch, failure):
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), '白头鹎')
    sidecar = photo.with_suffix('.xmp')
    if failure == 'corrupt': sidecar.write_bytes(b'<broken')
    before = sidecar.read_bytes()
    cancelled = [False]
    original_lookup = update.pinyin_for
    def lookup(name):
        if failure == 'changed': store.write_title(str(photo), '用户更改鸟名')
        if failure == 'removed': photo.unlink()
        if failure == 'cancelled': cancelled[0] = True
        return original_lookup(name)
    monkeypatch.setattr(update, 'pinyin_for', lookup)
    if failure == 'write': monkeypatch.setattr(PhotoMetaDataXMP, 'write', lambda *_: False)
    result = update.PinyinUpdater().update(str(photo), cancelled=lambda: cancelled[0])
    assert result.status == ('failed' if failure in ('write', 'corrupt') else 'cancelled' if failure == 'cancelled' else 'skipped')
    if failure == 'changed': assert store.read(str(photo))['Title'] == '用户更改鸟名'
    else: assert sidecar.read_bytes() == before


def test_cli_and_raw_jpeg_same_sidecar(photo, capsys):
    raw = photo.with_suffix('.ARW')
    raw.write_bytes(b'raw')
    assert PhotoMetaDataXMP().write_title(str(photo), '白头鹎')
    assert update.main([str(photo.parent)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['source'] == str(raw) and result['status'] == 'success'
