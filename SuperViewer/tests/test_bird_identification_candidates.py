"""已保存识鸟候选的兼容读取、精简字段优先级及中文采纳回读。"""
import json

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer.bird_identification import adopt_candidate
from SuperViewer.superviewer.bird_identification_candidates import load_saved_candidates


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
