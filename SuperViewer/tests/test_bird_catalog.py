"""目录协议、中文 XMP 原子回读、旧值清理与并发编辑保护。"""
import copy
import json

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.report_db import ReportDB
from SuperViewer.superviewer import bird_catalog as catalog


@pytest.fixture
def bird():
    return dict(bird_id=2, version_id=10, version_name='IOC 14.2', cn_name='白头鹎',
                en_name='Light-vented Bulbul', scientific_name='Pycnonotus sinensis',
                pinyin_name='bái tóu bēi', pinyin_plain='bai tou bei', abbreviation='BTB',
                gbif_rarity_100=0, iucn_category='LC', china_protection_level=None,
                description='常见留鸟，叫声清脆。', rarity_scope='global')


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / '中文照片.jpg'
    Image.new('RGB', (24, 16)).save(path)
    return path


def test_manual_chinese_roundtrip_original_report_and_notes_preserved(photo, bird):
    store = PhotoMetaDataXMP()
    db = ReportDB(str(photo.parent))
    db.insert_photo({'filename': photo.stem, 'original_path': str(photo), 'current_path': str(photo),
                     'bird_species_cn': '旧鸟种', 'rating': 0, 'pick': 0})
    db.close()
    report = photo.parent / '.superpicky/report.db'
    report_before, original = report.read_bytes(), photo.read_bytes()
    assert store.write(str(photo), {'XMP-dc:Description': '我的备注', 'XMP-dc:Subject': ['观鸟'],
                                   'XMP-xmp:Rating': 4, 'XMP-superpicky:birdid_confidence': 99,
                                   'XMP-superpicky:alt_species_cn': '旧候选',
                                   'XMP-superpicky:birdid_candidates': '[{"cn_name":"旧候选","confidence":99}]'})
    result = catalog.apply_bird(str(photo), bird, catalog.snapshot(str(photo)))
    assert result.status == 'success', result.message
    meta = store.read(str(photo))
    assert meta['Title'] == meta['bird_species_cn'] == bird['cn_name']
    assert meta['bird_species_en'] == bird['en_name']
    assert meta['pinyin_name'] == bird['pinyin_name']
    assert meta['pinyin_plain'] == bird['pinyin_plain']
    assert meta['scientific_name'] == bird['scientific_name']
    assert float(meta['gbif_rarity_100']) == 0 and meta['iucn_category'] == 'LC'
    assert meta['bird_species_source'] == 'manual'
    assert meta['Description'] == '我的备注' and meta['rating'] == 4
    assert store.read_subjects(str(photo)) == ['观鸟']
    assert not meta.get('birdid_confidence') and not meta.get('alt_species_cn')
    assert json.loads(meta['bird_catalog_response']) == bird
    assert meta['bird_species_description'] == bird['description']
    assert meta['birdid_candidates']
    assert report.read_bytes() == report_before and photo.read_bytes() == original


def test_absent_fields_clear_previous_species_and_compatibility_tags(photo, bird):
    store = PhotoMetaDataXMP()
    assert catalog.apply_bird(str(photo), bird, catalog.snapshot(str(photo))).status == 'success'
    new = {**bird, 'cn_name': '未知鸟', 'pinyin_name': '', 'pinyin_plain': '',
           'gbif_rarity_100': None, 'iucn_category': None}
    assert catalog.apply_bird(str(photo), new, catalog.snapshot(str(photo))).status == 'success'
    meta = store.read(str(photo))
    assert not meta.get('pinyin_name') and meta['pinyin_name_source'] == '未知鸟'
    assert not meta.get('gbif_rarity_100') and not meta.get('iucn_category')
    assert meta['birdid_rarity_source'] == '未知鸟'
    assert meta['birdid_rarity_missing'] == 'gbif_rarity_100,iucn_category'
    assert not meta.get('XMP-iptcExt:Event') and not meta.get('XMP-iptcCore:IntellectualGenre')


@pytest.mark.parametrize('action', ['edit', 'replace', 'move', 'corrupt', 'cancel', 'write_failure'])
def test_failure_retains_last_complete_data(photo, bird, monkeypatch, action):
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), '原有鸟名')
    before = catalog.snapshot(str(photo))
    if action == 'edit':
        assert store.write_title(str(photo), '刚刚编辑')
    elif action == 'replace':
        photo.write_bytes(b'new source')
    elif action == 'move':
        photo.rename(photo.with_name('moved.jpg'))
    elif action == 'corrupt':
        photo.with_suffix('.xmp').write_bytes(b'<broken')
        before = catalog.snapshot(str(photo))
    elif action == 'write_failure':
        monkeypatch.setattr(PhotoMetaDataXMP, '_write_tree_atomic', staticmethod(lambda *_: False))
    previous = photo.with_suffix('.xmp').read_bytes()
    result = catalog.apply_bird(str(photo), bird, before, cancelled=lambda: action == 'cancel')
    assert result.status != 'success'
    assert photo.with_suffix('.xmp').read_bytes() == previous


@pytest.mark.parametrize('change', [dict(bird_id=True), dict(en_name=None), dict(gbif_rarity_100=True),
    dict(gbif_rarity_100=float('nan')), dict(iucn_category=3), dict(pinyin_name=None)])
def test_malformed_details_never_write(photo, bird, change):
    result = catalog.apply_bird(str(photo), {**bird, **change}, catalog.snapshot(str(photo)))
    assert result.status == 'failed'
    assert not photo.with_suffix('.xmp').exists()


def test_client_url_encoding_identity_and_incomplete_responses(bird, monkeypatch):
    client = catalog.BirdCatalogClient(catalog.BirdIDOptions())
    calls = []
    def request(endpoint):
        calls.append(endpoint)
        if endpoint.startswith('/birds/search'):
            return dict(success=True, results=[bird], offset=0, limit=100, total=1, version_id=10)
        return dict(success=True, bird=copy.deepcopy(bird))
    monkeypatch.setattr(client, '_request', request)
    assert client.search('白头鹎')['results'][0] == bird
    assert '%E7%99%BD' in calls[0]
    assert client.detail(2, 10) == bird
    with pytest.raises(catalog.BirdIDError, match='不一致'):
        client.detail(3, 10)
    monkeypatch.setattr(client, '_request', lambda _: {'success': True, 'bird': {**bird, 'description': None}})
    with pytest.raises(catalog.BirdIDError):
        client.detail(2, 10)


def test_recognition_after_manual_assignment_clears_other_species_details(photo, bird, monkeypatch):
    from SuperViewer.superviewer.bird_identification import BirdIDClient, identify_file
    assert catalog.apply_bird(str(photo), bird, catalog.snapshot(str(photo))).status == 'success'
    client = BirdIDClient(catalog.BirdIDOptions())
    monkeypatch.setattr(client, 'recognize', lambda _: dict(success=True, results=[dict(
        cn_name='红耳鹎', en_name='Red-whiskered Bulbul', confidence=90,
        scientific_name='Pycnonotus jocosus', description='另一种鸟')]))
    assert identify_file(str(photo), client).status == 'success'
    meta = PhotoMetaDataXMP().read(str(photo))
    assert meta['scientific_name'] == 'Pycnonotus jocosus'
    assert meta['bird_species_description'] == '另一种鸟'
    assert meta['bird_species_source'] == 'recognition'
    assert not meta.get('pinyin_plain') and not meta.get('bird_species_abbreviation')
    assert not meta.get('bird_catalog_id') and not meta.get('bird_catalog_response')
