"""同名 RAW 匹配、真实 XMP 回读、亚秒/时区及失败保留。"""
from pathlib import Path

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.exiftool_runner import exiftool_worker_session, exiftool_read_request
from SuperViewer.superviewer import capture_time_update as core

STAMP = '2025-08-19T09:10:11.045+08:00'


@pytest.fixture(autouse=True)
def owned_exiftool():
    with exiftool_worker_session(), exiftool_read_request(lambda: False):
        yield


@pytest.fixture
def env(tmp_path, monkeypatch):
    targets = tmp_path / '导出'
    raws = tmp_path / '原片'
    targets.mkdir()
    raws.mkdir()
    photo = targets / '白头鹎.JPG'
    Image.new('RGB', (24, 16), 'green').save(photo)
    raw = raws / '白头鹎.ARW'
    raw.write_bytes(b'raw')
    monkeypatch.setattr(core, 'read_raw_capture_time', lambda *a, **k: STAMP)
    return photo, raw, raws


@pytest.mark.parametrize('suffix', ['.png', '.jpg', '.JPEG'])
def test_chinese_xmp_roundtrip_preserves_original_and_other_fields(env, suffix):
    photo, raw, raws = env
    target = photo.with_suffix(suffix)
    Image.new('RGB', (24, 16)).save(target)
    store = PhotoMetaDataXMP()
    assert store.write(str(target), {'XMP-dc:Title': '白头鹎', 'XMP-dc:Description': '中文备注',
                                    'XMP-dc:Subject': ['观鸟'], 'XMP-xmp:Rating': 4})
    before, raw_before = target.read_bytes(), raw.read_bytes()
    result, = core.update_capture_times([str(target)], raws)
    assert result.updates, result.message
    meta = store.read(str(target))
    assert meta['XMP-exif:DateTimeOriginal'] == meta['XMP-xmp:CreateDate'] == STAMP
    assert meta['date_time_original'] == STAMP
    assert meta['Title'] == '白头鹎' and meta['Description'] == '中文备注' and meta['rating'] == 4
    assert store.read_subjects(str(target)) == ['观鸟']
    assert target.read_bytes() == before and raw.read_bytes() == raw_before


def test_recursive_case_insensitive_exact_names_and_ambiguity(tmp_path):
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / 'DSC001.ArW').write_bytes(b'raw')
    (nested / 'DSC001-edited.ARW').write_bytes(b'raw')
    (nested / 'DSC001.jpg').write_bytes(b'jpeg')
    index = core.index_raw_files(tmp_path)
    assert core.matching_raw('dsc001.png', index) == str(nested / 'DSC001.ArW')
    with pytest.raises(ValueError, match='未找到'):
        core.matching_raw('dsc001-copy.jpg', index)
    (tmp_path / 'DSC001.CR3').write_bytes(b'raw')
    with pytest.raises(ValueError, match='多个'):
        core.matching_raw('dsc001.png', core.index_raw_files(tmp_path))


def test_shared_png_jpeg_sidecar_written_once(env, monkeypatch):
    photo, raw, raws = env
    png = photo.with_suffix('.png')
    Image.new('RGB', (24, 16)).save(png)
    calls = []
    write = PhotoMetaDataXMP.write
    def tracked(self, path, updates):
        calls.append(path)
        return write(self, path, updates)
    monkeypatch.setattr(PhotoMetaDataXMP, 'write', tracked)
    results = list(core.update_capture_times([str(photo), str(png)], raws))
    assert len(calls) == 1 and len(results) == 2 and all(r.updates for r in results)


@pytest.mark.parametrize('failure', ['missing_raw', 'duplicate_raw', 'invalid_time', 'broken_xmp', 'write',
                                    'target_changed', 'sidecar_changed', 'raw_changed', 'cancel'])
def test_failure_never_overwrites_latest_data(env, monkeypatch, failure):
    photo, raw, raws = env
    store = PhotoMetaDataXMP()
    assert store.write_title(str(photo), '原有标题')
    sidecar = photo.with_suffix('.xmp')
    cancelled = False
    def read(*args, **kwargs):
        nonlocal cancelled
        if failure == 'invalid_time':
            raise ValueError('RAW 缺少有效拍摄时间')
        if failure == 'target_changed':
            photo.write_bytes(b'changed photo')
        if failure == 'sidecar_changed':
            assert store.write_title(str(photo), '用户刚刚编辑')
        if failure == 'raw_changed':
            raw.write_bytes(b'changed raw')
        if failure == 'cancel':
            cancelled = True
        return STAMP
    monkeypatch.setattr(core, 'read_raw_capture_time', read)
    if failure == 'missing_raw':
        raw.unlink()
    elif failure == 'duplicate_raw':
        raw.with_suffix('.CR3').write_bytes(b'raw')
    elif failure == 'broken_xmp':
        sidecar.write_bytes(b'<broken')
    elif failure == 'write':
        monkeypatch.setattr(PhotoMetaDataXMP, '_write_tree_atomic', staticmethod(lambda *_: False))
    before = sidecar.read_bytes()
    results = list(core.update_capture_times([str(photo)], raws, cancelled=lambda: cancelled))
    assert not any(r.updates for r in results)
    assert all(r.missing_raw == (failure == 'missing_raw') for r in results)
    if failure == 'sidecar_changed':
        assert store.read(str(photo))['Title'] == '用户刚刚编辑'
    else:
        assert sidecar.read_bytes() == before


@pytest.mark.parametrize('metadata, expected', [
    ({'ExifIFD:DateTimeOriginal': '2025:08:19 09:10:11', 'ExifIFD:SubSecTimeOriginal': '045',
      'ExifIFD:OffsetTimeOriginal': '+08:00'}, STAMP),
    ({'EXIF:DateTimeOriginal': '2025:08:19 09:10:11'}, '2025-08-19T09:10:11'),
    ({'ExifIFD:CreateDate': '2025:08:19 09:10:11', 'ExifIFD:SubSecTimeDigitized': '0001'}, '2025-08-19T09:10:11.0001'),
    ({'DateTimeOriginal': '2025-08-19T09:10:11.045Z'}, '2025-08-19T09:10:11.045Z'),
])
def test_timestamp_preserves_precision_timezone_and_fallback(metadata, expected):
    assert core.capture_timestamp(metadata) == expected


@pytest.mark.parametrize('metadata', [{}, {'File:FileModifyDate': '2025:08:19 09:10:11'},
    {'DateTimeOriginal': '0000:00:00 00:00:00'}, {'DateTimeOriginal': '2025:02:30 00:00:00'},
    {'DateTimeOriginal': '2025:08:19 09:10:11', 'SubSecTimeOriginal': 'invalid'},
    {'DateTimeOriginal': '2025:08:19 09:10:11', 'OffsetTimeOriginal': 'bad'},
    {'DateTimeOriginal': '2025:08:19 09:10:11', 'OffsetTimeOriginal': '+01:60'},
    {'DateTimeOriginal': '2025:08:19 09:10:11', 'OffsetTimeOriginal': '+24:00'}])
def test_missing_invalid_dates_never_use_filesystem_time(metadata):
    with pytest.raises(ValueError):
        core.capture_timestamp(metadata)


def test_unsupported_missing_and_inaccessible_directory_are_reported(env, monkeypatch):
    photo, raw, raws = env
    result, = core.update_capture_times([str(photo.with_suffix('.webp'))], raws)
    assert '仅支持' in result.message
    result, = core.update_capture_times([str(photo.with_name('missing.png'))], raws)
    assert '不存在' in result.message
    def fail(*a, **k):
        raise PermissionError('目录拒绝访问')
    monkeypatch.setattr(core, 'index_raw_files', fail)
    result, = core.update_capture_times([str(photo)], raws)
    assert '目录拒绝访问' in result.message
    assert not result.missing_raw


def test_exiftool_reads_embedded_raw_time_not_sidecar(tmp_path):
    # 小型 TIFF/DNG 元数据样本；无需相机像素或网络下载。
    raw = tmp_path / '中文原片.dng'
    exif = Image.Exif()
    exif[34665] = {36867: '2025:08:19 09:10:11', 37521: '045', 36881: '+08:00'}
    decoded = Image.Exif()
    decoded.load(exif.tobytes())
    Image.new('RGB', (24, 16)).save(raw, format='TIFF', exif=decoded)
    assert PhotoMetaDataXMP().write(str(raw), {'XMP-exif:DateTimeOriginal': '2030-01-01T00:00:00'})
    before = raw.read_bytes()
    assert core.read_raw_capture_time(str(raw)) == STAMP
    assert raw.read_bytes() == before


def test_inaccessible_target_does_not_abort_other_photos(env, monkeypatch):
    photo, raw, raws = env
    denied = photo.with_name('denied.jpg')
    fingerprint = core._fingerprint
    def stat(path):
        if Path(path) == denied:
            raise PermissionError('拒绝访问')
        return fingerprint(path)
    monkeypatch.setattr(core, '_fingerprint', stat)
    failed, succeeded = core.update_capture_times([str(denied), str(photo)], raws)
    assert '拒绝访问' in failed.message
    assert succeeded.updates and succeeded.source == str(photo)
