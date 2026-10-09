"""主要鸟名：真实中文 XMP 回读、过期结果和失败保护。"""
import json

import pytest
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer.per_bird_identification import FIELD, INFO_FIELD
from SuperViewer.superviewer.per_bird_primary import individual_record_snapshot, set_primary_individual
from SuperViewer.tests.test_per_bird_identification import photo
from SuperViewer.tests.test_bird_identification_controller import env, wait_for


@pytest.fixture
def saved(photo):
    store = PhotoMetaDataXMP()
    best = {'cn_name': '白鹭', 'en_name': 'Little Egret', 'confidence': 30,
            'scientific_name': 'Egretta garzetta', 'description': '白色的鹭鸟',
            'gbif_rarity_100': 0, 'iucn_category': 'LC'}
    birds = [{'index': 3, 'box': [.1, .2, .8, .9], 'box_px': [32, 48, 256, 216],
              'detection_confidence': .8, 'status': 'candidate', **best,
              'response': {'success': True, 'results': [best]}}]
    assert store.write(str(photo), {
        'XMP-dc:Title': '旧主鸟名', 'XMP-dc:Description': '保留中文备注', 'XMP-xmp:Rating': 4,
        'XMP-dc:Subject': ['湿地', '中文标签'],
        'XMP-superpicky:pinyin_name': '旧拼音',
        'XMP-superpicky:china_protection_level': '旧等级',
        f'XMP-superpicky:{FIELD}': json.dumps(birds, ensure_ascii=False),
        f'XMP-superpicky:{INFO_FIELD}': json.dumps({'schema': 1, 'coordinate_space': 'oriented_camera_normalized_xyxy'})})
    return photo, store, individual_record_snapshot(store.read(str(photo)))


def test_set_primary_chinese_metadata_and_originals_preserved(saved):
    photo, store, expected = saved
    before = photo.read_bytes()
    old = store.read(str(photo))
    result = set_primary_individual(photo, 3, expected)
    assert result.status == 'success', result.message
    meta = store.read(str(photo))
    assert meta['Title'] == meta['bird_species_cn'] == '白鹭'
    assert meta['bird_species_en'] == 'Little Egret'
    assert float(meta['birdid_confidence']) == 30
    assert meta['scientific_name'] == 'Egretta garzetta'
    assert meta['bird_species_description'] == '白色的鹭鸟'
    assert float(meta['gbif_rarity_100']) == 0 and meta['iucn_category'] == 'LC'
    assert not meta.get('china_protection_level') and not meta.get('pinyin_name')
    assert meta['Description'] == '保留中文备注' and meta['rating'] == 4
    assert meta['XMP-dc:Subject'] == old['XMP-dc:Subject']
    assert individual_record_snapshot(meta) == expected
    assert json.loads(meta['birdid_response']) == json.loads(expected[0])[0]['response']
    assert meta['bird_species_source'] == 'individual'
    assert photo.read_bytes() == before
    assert result.updates['Title'] == '白鹭'


def test_primary_cli_uses_displayed_number(saved, capsys):
    from SuperViewer.superviewer.per_bird_primary import main
    photo, store, _ = saved
    assert main([str(photo), '--bird', '4']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'success'
    assert store.read(str(photo))['Title'] == '白鹭'


@pytest.mark.parametrize('change', ['stale', 'missing', 'failed', 'cancel', 'corrupt', 'write_failure', 'edited_during_read'])
def test_primary_rejects_invalid_or_stale_data_without_writing(saved, monkeypatch, change):
    photo, store, expected = saved
    if change in {'stale', 'missing', 'failed'}:
        birds = json.loads(expected[0])
        if change == 'stale': birds[0]['cn_name'] = '另一只鸟'
        if change == 'missing': birds.clear()
        if change == 'failed': birds[0]['status'] = 'failed'
        assert store.write_superpicky_fields(str(photo), {FIELD: json.dumps(birds, ensure_ascii=False)})
        if change != 'stale': expected = individual_record_snapshot(store.read(str(photo)))
    if change == 'corrupt': photo.with_suffix('.xmp').write_text('<broken', encoding='utf-8')
    if change == 'write_failure':
        monkeypatch.setattr(PhotoMetaDataXMP, '_write_tree_atomic', staticmethod(lambda *_: False))
    if change == 'edited_during_read':
        original = PhotoMetaDataXMP.read
        def read(self, path):
            metadata = original(self, path)
            assert store.write_title(path, '用户刚改的标题')
            return metadata
        monkeypatch.setattr(PhotoMetaDataXMP, 'read', read)
    before = photo.with_suffix('.xmp').read_bytes()
    result = set_primary_individual(photo, 3, expected, cancelled=lambda: change == 'cancel')
    assert result.status in {'skipped', 'failed', 'cancelled'}
    if change != 'edited_during_read':
        assert photo.with_suffix('.xmp').read_bytes() == before
    else:
        assert '用户刚改的标题' in photo.with_suffix('.xmp').read_text(encoding='utf-8')


def test_primary_worker_resolves_alias_and_refreshes_without_reselection(saved, env):
    photo, store, expected = saved
    controller, files, _, _ = env
    files.sources['缓存预览.jpg'] = str(photo)
    files._all_files = ['缓存预览.jpg', str(photo)]
    selected = []
    files.file_selected.connect(selected.append)
    assert controller.primary_bird.apply('缓存预览.jpg', 3, expected)
    assert wait_for(controller.primary_bird.is_shutdown_done)
    assert store.read(str(photo))['Title'] == '白鹭'
    assert files.updates[-1]['缓存预览.jpg']['bird_species_cn'] == '白鹭'
    assert not selected


def test_primary_shutdown_owns_worker_and_prevents_late_write(saved, env, monkeypatch):
    import threading
    from SuperViewer.superviewer import per_bird_primary_controller as ui
    photo, _, expected = saved
    controller, files, _, state = env
    state['gate'] = threading.Event()
    original = ui.set_primary_individual
    def delayed(*args, **kwargs):
        state['started'].set()
        assert state['gate'].wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(ui, 'set_primary_individual', delayed)
    before = photo.with_suffix('.xmp').read_bytes()
    assert controller.primary_bird.apply(str(photo), 3, expected)
    assert state['started'].wait(3)
    controller.request_shutdown()
    assert not controller.is_shutdown_done()
    state['gate'].set()
    assert wait_for(controller.is_shutdown_done)
    assert photo.with_suffix('.xmp').read_bytes() == before
    assert not files.updates
