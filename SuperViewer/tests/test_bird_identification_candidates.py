"""已保存识鸟候选的兼容读取、精简字段优先级及中文采纳回读。"""
import json
import sqlite3
import xml.etree.ElementTree as ET

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer.bird_identification import BirdIDError, adopt_candidate
from SuperViewer.superviewer.bird_identification_candidates import load_saved_candidates, saved_candidates


@pytest.fixture
def saved(tmp_path):
    photo = tmp_path / '中文鸟片.jpg'
    Image.new('RGB', (32, 24), 'blue').save(photo)
    candidates = [
        {'cn_name': '红耳鹎', 'en_name': 'Red-whiskered Bulbul', 'confidence': 35},
        {'cn_name': '白头鹎', 'en_name': 'Light-vented Bulbul', 'confidence': 95},
        {'cn_name': '', 'en_name': 'Common Kingfisher', 'confidence': 0},
    ]
    response = {'success': True, 'results': [
        {**candidates[1], 'pinyin_name': 'bái tóu bēi', 'gbif_rarity_100': 0, 'iucn_category': 'LC'},
        {**candidates[0], 'description': '中文说明', 'gbif_rarity_100': 80, 'iucn_category': 'NT'},
        candidates[2]], 'warning': '实际仅返回三个候选'}
    store = PhotoMetaDataXMP()
    raw = json.dumps(response, ensure_ascii=False, indent=2)
    assert store.write(str(photo), {'XMP-superpicky:birdid_candidates': json.dumps(candidates, ensure_ascii=False),
                                  'XMP-superpicky:birdid_response': raw,
                                  'XMP-dc:Title': '红耳鹎', 'XMP-superpicky:bird_species_cn': '红耳鹎',
                                  'XMP-dc:Description': '我的拍摄笔记', 'XMP-xmp:Rating': 4})
    return str(photo), candidates, response, raw


def test_new_list_controls_order_and_matches_details_by_identity(saved):
    photo, candidates, response, raw = saved
    before = PhotoMetaDataXMP().sidecar_path_for(photo).read_bytes()
    result = load_saved_candidates(photo)
    assert result.status == 'success', result.message
    assert result.accepted_index == 0  # 当前手动选中的低分鸟种不能被读取操作换掉。
    assert result.response['results'][0]['description'] == '中文说明'
    assert result.response['results'][0]['gbif_rarity_100'] == 80
    assert result.response['results'][1]['gbif_rarity_100'] == 0
    assert result.response_record == raw and not result.updates
    assert PhotoMetaDataXMP().sidecar_path_for(photo).read_bytes() == before
    adopted = adopt_candidate(result, 1)
    assert adopted.status == 'success', adopted.message
    values = PhotoMetaDataXMP().read(photo)
    assert values['Title'] == '白头鹎'
    assert values['pinyin_name'] == 'bái tóu bēi'
    assert float(values['gbif_rarity_100']) == 0 and values['iucn_category'] == 'LC'
    assert values['birdid_response'] == raw
    assert json.loads(values['birdid_candidates']) == candidates
    assert values['Description'] == '我的拍摄笔记' and values['rating'] == 4
    assert load_saved_candidates(photo).accepted_index == 1
    again = adopt_candidate(adopted, 2)
    assert again.status == 'success', again.message
    assert PhotoMetaDataXMP().read(photo)['Title'] == 'Common Kingfisher'
    assert load_saved_candidates(photo).accepted_index == 2


def test_old_response_is_read_only_until_adoption_backfills_new_field(saved):
    photo, candidates, response, raw = saved
    store = PhotoMetaDataXMP()
    assert store.write(photo, {'XMP-superpicky:birdid_candidates': ''})
    before = store.sidecar_path_for(photo).read_bytes()
    result = load_saved_candidates(photo)
    assert result.candidates_missing and result.accepted_index == 1
    assert store.sidecar_path_for(photo).read_bytes() == before
    adopted = adopt_candidate(result, result.accepted_index)
    assert adopted.status == 'success' and not adopted.candidates_missing
    values = store.read(photo)
    assert [c['cn_name'] for c in json.loads(values['birdid_candidates'])] == ['白头鹎', '红耳鹎', '']
    assert values['birdid_response'] == raw


def test_compact_only_and_more_than_three_candidates_are_supported(saved):
    photo, candidates, response, raw = saved
    candidates.extend([{'cn_name': '家燕', 'en_name': '', 'confidence': 5},
                       {'cn_name': '麻雀', 'en_name': '', 'confidence': 3}])
    store = PhotoMetaDataXMP()
    assert store.write(photo, {'XMP-superpicky:birdid_response': '',
                              'XMP-superpicky:birdid_candidates': json.dumps(candidates, ensure_ascii=False)})
    result = load_saved_candidates(photo)
    assert len(result.response['results']) == 5
    assert adopt_candidate(result, 4).status == 'success'
    values = store.read(photo)
    assert values['Title'] == '麻雀'
    assert json.loads(values['birdid_candidates']) == candidates


@pytest.mark.parametrize('bad', ['broken', '{}', '[]', '[null]',
    '[{"cn_name":"鸟","confidence":101}]', '[{"cn_name":"鸟","confidence":true}]',
    '[{"cn_name":"鸟","confidence":"20"}]', '[{"cn_name":"鸟","confidence":NaN}]'])
def test_invalid_new_field_never_silently_uses_old_response(saved, bad):
    photo, *_ = saved
    store = PhotoMetaDataXMP()
    assert store.write(photo, {'XMP-superpicky:birdid_candidates': bad})
    before = store.sidecar_path_for(photo).read_bytes()
    result = load_saved_candidates(photo)
    assert result.status == 'failed' and '格式错误' in result.message
    assert store.sidecar_path_for(photo).read_bytes() == before


def test_mismatched_full_response_does_not_supply_another_species_details(saved):
    photo, candidates, response, raw = saved
    candidates[0]['cn_name'] = '家燕'
    assert PhotoMetaDataXMP().write(photo, {
        'XMP-superpicky:birdid_candidates': json.dumps(candidates, ensure_ascii=False)})
    result = load_saved_candidates(photo)
    assert result.accepted_index is None
    assert 'gbif_rarity_100' not in result.response['results'][0]
    assert adopt_candidate(result, 0).status == 'success'
    assert PhotoMetaDataXMP().read(photo)['birdid_response'] == raw


def test_newer_user_edit_after_opening_candidates_is_preserved(saved):
    photo, *_ = saved
    result = load_saved_candidates(photo)
    assert PhotoMetaDataXMP().write_title(photo, '用户新鸟名')
    adopted = adopt_candidate(result, 1)
    assert adopted.status == 'skipped'
    assert PhotoMetaDataXMP().read(photo)['Title'] == '用户新鸟名'


def test_missing_cancelled_or_corrupt_sidecars_never_create_or_modify_data(saved):
    photo, *_ = saved
    store = PhotoMetaDataXMP()
    sidecar = store.sidecar_path_for(photo)
    assert load_saved_candidates(photo, cancelled=lambda: True).status == 'cancelled'
    sidecar.write_bytes(b'<broken')
    assert load_saved_candidates(photo).status == 'failed'
    assert sidecar.read_bytes() == b'<broken'
    sidecar.unlink()
    assert load_saved_candidates(photo).status == 'skipped'
    assert not sidecar.exists()


@pytest.mark.parametrize('title', ['反嘴鹬（待确定 33%）', '反嘴鹬 (待确定 33%)'])
def test_legacy_pending_title_is_read_only_and_can_be_adopted(tmp_path, title):
    # 复现旧版侧车和复制鸟名后只有待确定标题、没有新版 JSON 候选的情况。
    photo = tmp_path / 'DSC05874.HIF'
    photo.write_bytes(b'photo must not be decoded or changed')
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {'XMP-dc:Title': title, 'XMP-superpicky:bird_species_cn': title,
                                   'XMP-dc:Description': '保留中文拍摄笔记', 'XMP-xmp:Rating': 3})
    # 真实旧侧车还重复追加了评分/标记，采纳的通用字段写入同样需要成功。
    tree = ET.parse(photo.with_suffix('.xmp'))
    rdf = tree.getroot().find('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF')
    desc = ET.SubElement(rdf, '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description',
                         {'{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about': ''})
    ET.SubElement(desc, '{http://ns.adobe.com/xap/1.0/}Rating').text = '3'
    tree.write(photo.with_suffix('.xmp'), encoding='utf-8', xml_declaration=True)
    original = photo.read_bytes(), photo.with_suffix('.xmp').read_bytes()
    result = load_saved_candidates(str(photo))
    assert result.status == 'candidate', result.message
    assert result.accepted_index is None and result.candidates_missing
    assert result.response['results'] == [{'cn_name': '反嘴鹬', 'en_name': '', 'confidence': 33.0}]
    assert '旧版待确定记录' in result.message
    assert (photo.read_bytes(), photo.with_suffix('.xmp').read_bytes()) == original
    adopted = adopt_candidate(result, 0)
    assert adopted.status == 'success', adopted.message
    metadata = store.read(str(photo))
    assert metadata['Title'] == metadata['bird_species_cn'] == '反嘴鹬'
    assert float(metadata['birdid_confidence']) == 33
    assert metadata['Description'] == '保留中文拍摄笔记' and metadata['rating'] == 3
    assert not metadata.get('birdid_response')
    assert json.loads(metadata['birdid_candidates']) == result.response['results']
    assert photo.read_bytes() == original[0]
    assert load_saved_candidates(str(photo)).accepted_index == 0


@pytest.mark.parametrize('confidence', [0, 0.63, 33.36047828197479, 100])
def test_legacy_structured_candidate_preserves_percentage_units(confidence):
    response, record, missing = saved_candidates({
        'XMP-superpicky:alt_species_cn': '反嘴鹬', 'XMP-superpicky:alt_species_en': 'Pied Avocet',
        'XMP-superpicky:alt_confidence': str(confidence)})
    assert response['results'] == [{'cn_name': '反嘴鹬', 'en_name': 'Pied Avocet', 'confidence': confidence}]
    assert record == '' and missing


def test_xmp_legacy_candidate_precedes_report_and_keeps_current_confirmed_name():
    response, _, _ = saved_candidates({'Title': '当前已确认鸟名', 'alt_species_cn': '反嘴鹬',
                                      'alt_species_en': 'Pied Avocet', 'alt_confidence': 33},
                                     report_metadata={'alt_species_cn': '旧报告鸟名', 'alt_confidence': 80})
    assert response['results'] == [{'cn_name': '反嘴鹬', 'en_name': 'Pied Avocet', 'confidence': 33}]


def test_legacy_report_candidate_is_available_when_xmp_only_contains_notes():
    response, _, _ = saved_candidates({'Description': '保留拍摄笔记'},
                                     report_metadata={'alt_species_en': 'Pied Avocet', 'alt_confidence': 33})
    assert response['results'] == [{'cn_name': '', 'en_name': 'Pied Avocet', 'confidence': 33}]


@pytest.mark.parametrize('title,score,expected', [('反嘴鹬（待确定 33%）', 33.5, 33),
                                              ('反嘴鹬（待确定 33.4%）', 33.43, 33.43),
                                              ('反嘴鹬（待确定 33.4%）', 33.89, 33.4)])
def test_report_enrichment_respects_title_precision(title, score, expected):
    response, _, _ = saved_candidates({'Title': title},
                                     report_metadata={'alt_species_cn': '反嘴鹬', 'alt_confidence': score})
    assert response['results'][0]['confidence'] == expected


@pytest.mark.parametrize('confidence', [None, '', 'NaN', 'inf', -1, 101, True])
def test_invalid_legacy_confidence_does_not_create_a_candidate(confidence):
    with pytest.raises(BirdIDError, match='格式错误'):
        saved_candidates({'alt_species_cn': '反嘴鹬', 'alt_confidence': confidence})


@pytest.mark.parametrize('title', ['反嘴鹬', '反嘴鹬（待确定）', '随手记：33%', '反嘴鹬（待确定 101%）'])
def test_ordinary_or_invalid_titles_are_not_inferred_as_candidates(title):
    with pytest.raises(BirdIDError):
        saved_candidates({'Title': title})


@pytest.mark.parametrize('report_name,score,enriched', [('反嘴鹬', 33.36047828197479, True),
                                                      ('反嘴鹬', 98, False), ('黑尾塍鹬', 9, False)])
def test_report_legacy_candidate_only_enriches_matching_pending_title(tmp_path, report_name, score, enriched):
    photo = tmp_path / '中文照片.HIF'
    photo.write_bytes(b'original HIF')
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), '反嘴鹬（待确定 33%）')
    db_path = tmp_path / '.superpicky' / 'report.db'
    db_path.parent.mkdir()
    # 单独构造生产者字段，current_path 为预览图，按原图路径解析旧报告。
    connection = sqlite3.connect(db_path)
    try:
        with connection:
            connection.execute('CREATE TABLE photos (filename TEXT, original_path TEXT, current_path TEXT, '
                               'alt_species_cn TEXT, alt_species_en TEXT, alt_confidence REAL)')
            connection.execute('INSERT INTO photos VALUES (?, ?, ?, ?, ?, ?)',
                               (photo.stem, photo.name, '.superpicky/cache/temp_preview/中文照片.jpg',
                                report_name, 'Pied Avocet' if enriched else 'Other Bird', score))
    finally:
        connection.close()
    before = db_path.read_bytes(), db_path.stat().st_mtime_ns, photo.with_suffix('.xmp').read_bytes()
    result = load_saved_candidates(str(photo))
    assert result.status == 'candidate', result.message
    candidate = result.response['results'][0]
    assert candidate == {'cn_name': '反嘴鹬', 'en_name': 'Pied Avocet' if enriched else '',
                         'confidence': score if enriched else 33.0}
    assert (db_path.read_bytes(), db_path.stat().st_mtime_ns, photo.with_suffix('.xmp').read_bytes()) == before
    assert adopt_candidate(result, 0).status == 'success'
    assert store.read(str(photo))['Title'] == '反嘴鹬'
    assert (db_path.read_bytes(), db_path.stat().st_mtime_ns) == before[:2]


def test_modern_records_keep_priority_over_legacy_pending_title(saved):
    photo, candidates, response, raw = saved
    store = PhotoMetaDataXMP()
    assert store.write_title(photo, '反嘴鹬（待确定 33%）')
    assert len(load_saved_candidates(photo).response['results']) == 3
    assert store.write(photo, {'XMP-superpicky:birdid_candidates': ''})
    assert len(load_saved_candidates(photo).response['results']) == 3
    assert store.write(photo, {'XMP-superpicky:birdid_response': '{broken'})
    result = load_saved_candidates(photo)
    assert result.status == 'failed' and '格式错误' in result.message
