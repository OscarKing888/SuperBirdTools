from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer.photo_tags import PhotoTagConfig
from SuperViewer.superviewer.tag_library_model import TagLibraryDraft
from SuperViewer.superviewer import tag_library_transaction as tx


@pytest.fixture
def library(tmp_path):
    state = tmp_path / '.superpicky'
    state.mkdir()
    cfg = state / 'tags.cfg'
    cfg.write_text('行为\n  飞行\n  捕食\n', encoding='utf-8')
    meta = PhotoMetaDataXMP()
    photos = []
    for name in ['甲.jpg', 'child/乙.jpg']:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        Image.new('RGB', (4, 4), 'blue').save(path)
        assert meta.write(str(path), {'XMP-xmp:Rating': 4, 'XMP-dc:Description': '清晨湿地',
                                      'XMP-dc:Subject': ['飞行', '外部关键词']})
        photos.append(path)
    config = PhotoTagConfig(cfg)
    draft = TagLibraryDraft(config.read_bytes())
    draft.rename(draft.roots[0].children[0], '盘旋')
    plan = tx.prepare_tag_edit(config, draft.original, draft.serialize(), str(tmp_path), draft.migration())
    return config, photos, meta, plan


def snapshots(config, photos):
    return {config.path: config.read_bytes(), **{p.with_suffix('.xmp'): p.with_suffix('.xmp').read_bytes() for p in photos}}


def test_recursive_chinese_roundtrip_undo_preserves_other_fields(library):
    cfg, photos, meta, plan = library
    originals = {p: p.read_bytes() for p in photos}
    assert plan.affected_count == 2
    result = tx.apply_tag_edit(plan)
    assert len(result) == 2
    assert cfg.load() == ['盘旋', '捕食']
    for photo in photos:
        assert meta.read_subjects(str(photo), strict=True) == ['外部关键词', '盘旋']
        assert meta.read(str(photo))['XMP-dc:Description'] == '清晨湿地'
        assert photo.read_bytes() == originals[photo]
    # Unrelated user edits made later must survive undo and redo.
    assert meta.write(str(photos[0]), {'XMP-xmp:Rating': 5})
    assert meta.add_subjects(str(photos[0]), ['后来添加'])
    tx.apply_tag_edit(plan.inverse())
    assert cfg.load() == ['飞行', '捕食']
    assert set(meta.read_subjects(str(photos[0]))) == {'外部关键词', '飞行', '后来添加'}
    assert meta.read(str(photos[0]))['XMP-xmp:Rating'] == '5'
    tx.apply_tag_edit(plan)
    assert '后来添加' in meta.read_subjects(str(photos[0]))
    assert not tx.recovery_directories(cfg)


def test_shared_sidecar_once_and_skip_cache(library, monkeypatch):
    cfg, photos, meta, _ = library
    photos[0].with_suffix('.NEF').write_bytes(b'raw')
    cache_photo = cfg.path.parent / 'cache.jpg'
    cache_photo.write_bytes(b'cache')
    assert meta.write_subjects(str(cache_photo), ['飞行'])
    plan = tx.prepare_tag_edit(cfg, cfg.read_bytes(), '盘旋\n捕食\n'.encode(), str(photos[0].parent), {'飞行': '盘旋'})
    assert len(plan.patches) == 2 and plan.affected_count == 3
    calls = []
    write = meta.write_subjects
    def record(path, subjects):
        calls.append(path)
        return write(path, subjects)
    monkeypatch.setattr(meta, 'write_subjects', record)
    result = tx.apply_tag_edit(plan, metadata=meta)
    assert len(calls) == 2 and len(result) == 3
    assert meta.read_subjects(str(cache_photo)) == ['飞行']


@pytest.mark.parametrize('failure', ['write', 'config', 'cancel'])
def test_failures_and_cancel_restore_exact_files(library, monkeypatch, failure):
    cfg, photos, meta, plan = library
    before = snapshots(cfg, photos)
    stopped = False
    original = meta.write_subjects
    count = 0
    def write(path, subjects):
        nonlocal count
        count += 1
        if failure == 'write' and count == 2:
            return False
        return original(path, subjects)
    monkeypatch.setattr(meta, 'write_subjects', write)
    def fail_config(*args, **kwargs):
        raise OSError('config denied')
    if failure == 'config':
        monkeypatch.setattr(PhotoTagConfig, 'save_bytes', fail_config)
    def progress(text, done, total):
        nonlocal stopped
        if failure == 'cancel' and text == '保存照片标签':
            stopped = True
    with pytest.raises((OSError, tx.TagEditCancelled)):
        tx.apply_tag_edit(plan, metadata=meta, cancel=lambda: stopped, progress=progress)
    assert snapshots(cfg, photos) == before
    assert not tx.recovery_directories(cfg)


def test_rollback_failure_keeps_backups_and_can_retry(library, monkeypatch):
    cfg, photos, meta, plan = library
    before = snapshots(cfg, photos)
    atomic = tx.atomic_write_bytes
    def fail_restore(path, data):
        if path.suffix == '.xmp':
            raise PermissionError('restore denied')
        atomic(path, data)
    def fail_config(*args, **kwargs):
        raise OSError('config denied')
    monkeypatch.setattr(tx, 'atomic_write_bytes', fail_restore)
    monkeypatch.setattr(PhotoTagConfig, 'save_bytes', fail_config)
    with pytest.raises(tx.TagRecoveryRequired, match='恢复资料'):
        tx.apply_tag_edit(plan)
    journals = tx.recovery_directories(cfg)
    assert len(journals) == 1
    assert (journals[0] / '0.bak').is_file()
    with pytest.raises(tx.TagRecoveryRequired):
        tx.apply_tag_edit(plan)
    monkeypatch.setattr(tx, 'atomic_write_bytes', atomic)
    tx.recover_tag_edits(cfg)
    assert snapshots(cfg, photos) == before
    assert not tx.recovery_directories(cfg)


def test_external_config_and_subject_conflicts_do_not_write(library):
    cfg, photos, meta, plan = library
    cfg.path.write_text('外部修改\n', encoding='utf-8')
    before = snapshots(cfg, photos)
    with pytest.raises(ValueError, match='配置'):
        tx.apply_tag_edit(plan)
    assert snapshots(cfg, photos) == before
    cfg.path.write_bytes(plan.before)
    tx.apply_tag_edit(plan)
    assert meta.remove_subjects(str(photos[0]), ['盘旋'])
    before = snapshots(cfg, photos)
    with pytest.raises(ValueError, match='照片标签'):
        tx.apply_tag_edit(plan.inverse())
    assert snapshots(cfg, photos) == before


def test_bad_xmp_missing_photo_and_unreadable_scan(library, monkeypatch):
    cfg, photos, meta, plan = library
    photos[0].with_suffix('.xmp').write_text('<broken', encoding='utf-8')
    before = snapshots(cfg, photos)
    with pytest.raises(Exception):
        tx.prepare_tag_edit(cfg, plan.before, plan.after, str(photos[0].parent), {'飞行': '盘旋'})
    assert snapshots(cfg, photos) == before
    photos[0].unlink()
    with pytest.raises(ValueError, match='位置'):
        tx.apply_tag_edit(plan)


def test_config_only_edits_and_missing_config_undo(tmp_path):
    cfg = PhotoTagConfig(tmp_path / 'tags.cfg')
    plan = tx.prepare_tag_edit(cfg, None, '飞行\n'.encode(), '', {})
    assert plan.affected_count == 0
    tx.apply_tag_edit(plan)
    assert cfg.load() == ['飞行']
    tx.apply_tag_edit(plan.inverse())
    assert not cfg.path.exists()


def test_target_keyword_already_present_restored_on_undo(library):
    cfg, photos, meta, _ = library
    assert meta.add_subjects(str(photos[0]), ['盘旋'])
    plan = tx.prepare_tag_edit(cfg, cfg.read_bytes(), '盘旋\n捕食\n'.encode(), str(photos[0].parent), {'飞行': '盘旋'})
    tx.apply_tag_edit(plan)
    tx.apply_tag_edit(plan.inverse())
    assert set(meta.read_subjects(str(photos[0]))) == {'飞行', '盘旋', '外部关键词'}
    assert set(meta.read_subjects(str(photos[1]))) == {'飞行', '外部关键词'}


def test_cancel_during_snapshot_staging_never_changes_files(library):
    cfg, photos, meta, plan = library
    before = snapshots(cfg, photos)
    cancelled = False
    def progress(phase, done, total):
        nonlocal cancelled
        if phase == '准备恢复快照':
            cancelled = True
    with pytest.raises(tx.TagEditCancelled):
        tx.apply_tag_edit(plan, cancel=lambda: cancelled, progress=progress)
    assert snapshots(cfg, photos) == before
    assert list(cfg.path.parent.glob('.tag-edit-recovery-*')) == []


def test_unreadable_directory_aborts_scan(library, monkeypatch):
    cfg, photos, meta, plan = library
    def failing_walk(root, *, followlinks, onerror):
        onerror(PermissionError('unreadable subdirectory'))
        yield
    monkeypatch.setattr(tx.os, 'walk', failing_walk)
    with pytest.raises(PermissionError, match='unreadable'):
        tx.prepare_tag_edit(cfg, plan.before, plan.after, str(photos[0].parent), {'飞行': '盘旋'})


def test_report_database_bytes_untouched(library):
    cfg, photos, meta, plan = library
    import sqlite3
    db = cfg.path.parent / 'report.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE photos (filename TEXT, bird_species_cn TEXT)')
        conn.execute('INSERT INTO photos VALUES (?, ?)', ('甲.jpg', '白鹭'))
    before = db.read_bytes()
    tx.apply_tag_edit(plan)
    tx.apply_tag_edit(plan.inverse())
    assert db.read_bytes() == before


def test_directory_links_are_not_followed(library):
    cfg, photos, meta, plan = library
    link = photos[0].parent / 'linked'
    try:
        link.symlink_to(photos[1].parent, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlink unavailable')
    scanned = tx.prepare_tag_edit(cfg, plan.before, plan.after, str(photos[0].parent), {'飞行': '盘旋'})
    assert scanned.affected_count == 2
