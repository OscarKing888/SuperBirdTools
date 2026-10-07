"""手动稀有度的真实中文侧车回读、报告只读与失败保护。"""
import sqlite3

from PIL import Image
import pytest
from app_common.bird_rarity import rarity_metadata
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, PhotoMetaDataEXIFEmbeded
from SuperViewer.superviewer import rarity_edit as edit


@pytest.fixture
def photo(tmp_path, monkeypatch):
    path = tmp_path / '中文鸟片.jpg'
    Image.new('RGB', (24, 18)).save(path)
    monkeypatch.setattr(PhotoMetaDataEXIFEmbeded, 'read', lambda *_: {})
    return path


@pytest.mark.parametrize('score', [0, 8, 25, 50, 75, 100, 72.25])
def test_roundtrip_preserves_original_and_other_fields(photo, score):
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': '白头鹎', 'XMP-dc:Description': '保留中文备注',
        'XMP-dc:Subject': ['鸟类'], 'XMP-xmp:Rating': 4,
        'XMP-superpicky:birdid_rarity_source': '白头鹎',
        'XMP-superpicky:birdid_rarity_missing': 'gbif_rarity_100',
        'XMP-superpicky:iucn_category': 'NT'})
    original = photo.read_bytes()
    result = edit.save_rarity(str(photo), score)
    metadata = store.read(str(photo))
    assert result.updates and rarity_metadata(metadata) == (score, 'NT')
    assert metadata['Description'] == '保留中文备注' and metadata['rating'] == 4
    assert store.read_subjects(str(photo)) == ['鸟类']
    assert photo.read_bytes() == original
    xml = photo.with_suffix('.xmp').read_text(encoding='utf-8')
    assert '白头鹎' in xml and f'{score:.2f}' in xml
    from app_common.file_browser._workers import MetadataLoader
    loader = MetadataLoader([str(photo)], meta_proxy=object())
    assert rarity_metadata(loader._parse_rec(metadata)) == (score, 'NT')


def test_changed_bird_name_does_not_hide_manual_score_or_revive_old_iucn(photo):
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': '新鸟名', 'XMP-superpicky:birdid_rarity_source': '旧鸟名',
        'XMP-superpicky:gbif_rarity_100': 99, 'XMP-superpicky:iucn_category': 'CR'})
    edit.save_rarity(str(photo), 8)
    assert rarity_metadata(store.read(str(photo))) == (8, '')


def test_report_hydration_keeps_iucn_and_does_not_write_database(photo):
    report = photo.parent / '.superpicky/report.db'
    report.parent.mkdir()
    with sqlite3.connect(report) as db:
        db.execute('CREATE TABLE photos (filename TEXT, current_path TEXT, bird_species_cn TEXT, gbif_rarity_100 REAL, iucn_category TEXT)')
        db.execute('INSERT INTO photos VALUES (?,?,?,?,?)', (photo.stem, photo.name, '白头鹎', 99, 'NT'))
    before = report.read_bytes()
    edit.save_rarity(str(photo), 0)
    assert rarity_metadata(PhotoMetaDataXMP().read(str(photo))) == (0, 'NT')
    assert report.read_bytes() == before


def test_no_bird_name_and_cli(photo, capsys):
    assert edit.main([str(photo), '--score', '50']) == 0
    assert rarity_metadata(PhotoMetaDataXMP().read(str(photo))) == (50, '')


@pytest.mark.parametrize('score', [-1, 101, True, float('nan'), float('inf'), None])
def test_invalid_score_leaves_no_sidecar(photo, score):
    with pytest.raises(ValueError): edit.save_rarity(str(photo), score)
    assert not photo.with_suffix('.xmp').exists()


@pytest.mark.parametrize('failure', ['corrupt', 'write', 'changed', 'removed', 'cancelled'])
def test_failed_write_and_concurrent_change_preserve_sidecar(photo, monkeypatch, failure):
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), '白头鹎')
    sidecar = photo.with_suffix('.xmp')
    if failure == 'corrupt': sidecar.write_bytes(b'<broken')
    before = sidecar.read_bytes()
    original = edit.PhotoMetaDataProxy.read
    def read(proxy, path):
        metadata = original(proxy, path)
        if failure == 'changed': store.write_title(path, '用户新鸟名')
        if failure == 'removed': photo.unlink()
        return metadata
    monkeypatch.setattr(edit.PhotoMetaDataProxy, 'read', read)
    if failure == 'write': monkeypatch.setattr(PhotoMetaDataXMP, 'write', lambda *_: False)
    if failure == 'cancelled':
        assert not edit.save_rarity(str(photo), 50, cancelled=lambda: True).updates
    else:
        with pytest.raises((ValueError, OSError, RuntimeError)): edit.save_rarity(str(photo), 50)
    if failure == 'changed': assert store.read(str(photo))['Title'] == '用户新鸟名'
    else: assert sidecar.read_bytes() == before
