"""整份 XMP 复制：真实中文回读、字节保留、失败与并发修改保护。"""
from pathlib import Path

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import raw_xmp_update as core


@pytest.fixture
def env(tmp_path):
    targets, raws = tmp_path / '导出图片', tmp_path / '原片'
    targets.mkdir()
    (raws / '.归档' / '十月').mkdir(parents=True)
    photo = targets / '白头鹎.JPG'
    Image.new('RGB', (24, 16), 'green').save(photo)
    raw = raws / '.归档' / '十月' / '白头鹎.ARW'
    raw.write_bytes(b'original raw')
    store = PhotoMetaDataXMP()
    assert store.write(str(raw), {'XMP-dc:Title': '白头鹎', 'XMP-dc:Description': '中文备注 🐦',
                                 'XMP-dc:Subject': ['观鸟', '未知标签'], 'XMP-xmp:Rating': 4,
                                 'XMP-superpicky:camera_model': '中文相机',
                                 'XMP-superpicky:birdid_individuals': '[{"name":"白头鹎"}]'})
    assert store.write(str(photo), {'XMP-dc:Title': '旧标题', 'XMP-dc:Description': '旧备注',
                                   'XMP-superpicky:obsolete': '目标独有字段'})
    # 额外保留未知第三方命名空间、处理指令与注释，不能重新序列化复制文件。
    sidecar = raw.with_suffix('.xmp')
    data = sidecar.read_bytes().replace(b'</rdf:Description>',
        '<other:custom xmlns:other="urn:third-party:test">任意信息</other:custom><!-- 原样保留 -->\n</rdf:Description>'.encode())
    sidecar.write_bytes(data)
    return photo, raw, raws


@pytest.mark.parametrize('suffix', ['.JPG', '.png', '.psd', '.hif', '.tif', '.arw'])
def test_exact_copy_overwrites_all_fields_and_keeps_photo_and_raw(env, suffix):
    photo, raw, raws = env
    target = photo.with_suffix(suffix)
    if target != photo:
        target.write_bytes(photo.read_bytes())
    source_bytes = raw.with_suffix('.xmp').read_bytes()
    before, raw_before = target.read_bytes(), raw.read_bytes()
    result, = core.update_all_information([str(target)], raws)
    assert result.updates, result.message
    assert result.raw_source == str(raw)
    assert target.with_suffix('.xmp').read_bytes() == source_bytes
    meta = PhotoMetaDataXMP().read(str(target))
    assert meta['Title'] == '白头鹎' and meta['Description'] == '中文备注 🐦'
    assert meta['rating'] == 4 and meta['camera_model'] == '中文相机'
    assert 'obsolete' not in meta
    assert PhotoMetaDataXMP().read_subjects(str(target)) == ['观鸟', '未知标签']
    assert target.read_bytes() == before and raw.read_bytes() == raw_before
    assert raw.with_suffix('.xmp').read_bytes() == source_bytes
    assert not list(target.parent.glob('.*.tmp'))


def test_shared_sidecar_copied_once_case_insensitive_source_and_uppercase_xmp(env, monkeypatch):
    photo, raw, raws = env
    png = photo.with_suffix('.png')
    png.write_bytes(photo.read_bytes())
    raw.with_suffix('.xmp').rename(raw.with_suffix('.XMP'))
    original = core.shutil.copyfile
    copies = []
    def copy(*args):
        copies.append(args)
        return original(*args)
    monkeypatch.setattr(core.shutil, 'copyfile', copy)
    results = list(core.update_all_information([str(photo), str(png), str(photo)], raws))
    assert len(results) == 2 and all(r.updates for r in results)
    assert len(copies) == 1
    assert photo.with_suffix('.xmp').read_bytes() == raw.with_suffix('.XMP').read_bytes()


@pytest.mark.parametrize('failure', ['missing_raw', 'ambiguous', 'missing_xmp', 'invalid_xmp',
                                    'copy', 'replace', 'photo_changed', 'target_changed',
                                    'raw_changed', 'source_changed', 'cancel'])
def test_failures_and_cancel_preserve_last_complete_target(env, monkeypatch, failure):
    photo, raw, raws = env
    destination, source = photo.with_suffix('.xmp'), raw.with_suffix('.xmp')
    old = destination.read_bytes()
    cancelled = False
    copy = core.shutil.copyfile
    def copy_with_change(src, dst):
        nonlocal cancelled
        if failure == 'copy':
            Path(dst).write_bytes(b'partial')
            raise OSError('复制失败')
        copy(src, dst)
        if failure == 'target_changed':
            assert PhotoMetaDataXMP().write_title(str(photo), '用户刚编辑')
        if failure == 'photo_changed':
            photo.write_bytes(b'changed photo')
        if failure == 'raw_changed':
            raw.write_bytes(b'changed raw')
        if failure == 'source_changed':
            source.write_bytes(source.read_bytes() + b'\n')
        if failure == 'cancel':
            cancelled = True
    monkeypatch.setattr(core.shutil, 'copyfile', copy_with_change)
    if failure == 'missing_raw':
        raw.unlink()
    elif failure == 'ambiguous':
        (raws / raw.name).write_bytes(b'duplicate')
    elif failure == 'missing_xmp':
        source.unlink()
    elif failure == 'invalid_xmp':
        source.write_bytes(b'<broken')
    elif failure == 'replace':
        def fail(*args):
            raise PermissionError('替换失败')
        monkeypatch.setattr(core.os, 'replace', fail)
    results = list(core.update_all_information([str(photo)], raws, cancelled=lambda: cancelled))
    assert not any(r.updates for r in results)
    assert all(r.missing_raw == (failure == 'missing_raw') for r in results)
    if failure == 'target_changed':
        assert PhotoMetaDataXMP().read(str(photo))['Title'] == '用户刚编辑'
    else:
        assert destination.read_bytes() == old
    assert not list(photo.parent.glob('.*.tmp'))


def test_copy_to_missing_or_broken_destination_and_empty_rdf(env):
    photo, raw, raws = env
    destination = photo.with_suffix('.xmp')
    destination.unlink()
    result, = core.update_all_information([str(photo)], raws)
    assert result.updates and destination.read_bytes() == raw.with_suffix('.xmp').read_bytes()
    destination.write_bytes(b'<broken old target')
    empty = b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" />'
    raw.with_suffix('.xmp').write_bytes(empty)
    result, = core.update_all_information([str(photo)], raws)
    assert result.updates and destination.read_bytes() == empty


def test_same_source_sidecar_noop_and_source_symlink_rejected(env):
    photo, raw, raws = env
    before = raw.with_suffix('.xmp').stat().st_mtime_ns
    result, = core.update_all_information([str(raw)], raws)
    assert result.updates and raw.with_suffix('.xmp').stat().st_mtime_ns == before
    sidecar = raw.with_suffix('.xmp')
    sidecar.unlink()
    sidecar.symlink_to(photo.with_suffix('.xmp'))
    result, = core.update_all_information([str(photo)], raws)
    assert not result.updates and '不是普通文件' in result.message


def test_missing_photo_unsupported_and_scan_failure_are_reported(env, monkeypatch):
    photo, raw, raws = env
    unsupported, missing = core.update_all_information([str(photo.with_suffix('.mp4')),
                                                        str(photo.with_name('missing.jpg'))], raws)
    assert '仅支持' in unsupported.message and '不存在' in missing.message
    result, = core.update_all_information([str(photo)], raws / 'missing')
    assert not result.updates and '目录不存在' in result.message


def test_cli_returns_json_and_failure_status(env, capsys):
    photo, raw, raws = env
    assert core.main(['--raw-directory', str(raws), str(photo)]) == 0
    assert '"success": true' in capsys.readouterr().out
    raw.unlink()
    assert core.main(['--raw-directory', str(raws), str(photo)]) == 1
    assert '未找到同名 RAW' in capsys.readouterr().out


def test_replacement_flushes_pending_source_and_notifies_cached_target(env):
    from app_common.exif_io.metadata_store import get_store
    photo, raw, raws = env
    store = get_store()
    store.remember_many({str(photo): {'Title': '旧快照'}}).result(5)
    old_revision = store.revisions([str(photo)])[str(photo)]
    store.submit_edit(str(raw), {'title': '已提交的 RAW 标题'}).result(5)
    store.submit_edit(str(photo), {'description': '替换前已接受'}).result(5)
    result, = core.update_all_information([str(photo)], raws)
    assert result.updates, result.message
    assert not store.pending_status([str(photo), str(raw)])
    assert PhotoMetaDataXMP().read(str(photo))['Title'] == '已提交的 RAW 标题'
    assert store.revisions([str(photo)])[str(photo)] > old_revision
    assert store.peek(str(photo)) is None


def test_replacement_stops_when_pending_target_cannot_sync(env, monkeypatch):
    from app_common.exif_io.metadata_store import get_store
    photo, raw, raws = env
    before = photo.with_suffix('.xmp').read_bytes()
    store = get_store()
    store.submit_edit(str(photo), {'title': '不可丢失的输入'}).result(5)
    monkeypatch.setattr(PhotoMetaDataXMP, 'write', lambda *_: False)
    result, = core.update_all_information([str(photo)], raws)
    assert not result.updates and result.message
    assert photo.with_suffix('.xmp').read_bytes() == before
    assert store.pending_edits(str(photo))['fields']['XMP-dc:Title'] == '不可丢失的输入'
