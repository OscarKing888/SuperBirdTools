"""真实 EXIF 写入/读回、中文保留、统一出口与 sidecar 配对。"""
from pathlib import Path
from io import BytesIO
import json
import struct
import threading
from types import SimpleNamespace

import pytest
from PIL import Image, ImageOps

from app_common.exif_io import get_exiftool_executable_path
from app_common.exif_io.exiftool_runner import run_exiftool, run_exiftool_once
from birdstamp import export_metadata
from birdstamp.export_metadata import save_export_image
from test_dejitter_tab import sequence


def exiftool(*args):
    executable = get_exiftool_executable_path()
    if not executable:
        pytest.skip('ExifTool unavailable')
    result = run_exiftool(executable, list(args), timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def metadata(path):
    return json.loads(exiftool('-j', '-G1', '-s', '-n', str(path)))[0]


@pytest.fixture
def original(tmp_path):
    path = tmp_path / '中文 原图.jpg'
    with Image.new('RGB', (120, 80), '#6395bd') as image:
        image.save(path)
    chinese = tmp_path / '文字.txt'
    chinese.write_text('小勺子，原始拍摄信息。', encoding='utf-8')
    exiftool('-overwrite_original', '-charset', 'filename=UTF8', '-Make=SONY', '-Model=ILCE-1',
             '-ISO=1600', '-FNumber=2.8', '-ExposureTime=1/2000', '-FocalLength=600',
             '-LensModel=FE 600mm F4 GM OSS', '-DateTimeOriginal=2026:09:19 08:12:34',
             '-SubSecTimeOriginal=456', '-OffsetTimeOriginal=+08:00', '-ExifIFD:SerialNumber=123456',
             '-GPSLatitude=31.2', '-GPSLatitudeRef=N', '-GPSLongitude=121.5', '-GPSLongitudeRef=E',
             '-Orientation#=6', f'-UserComment<={chinese}', f'-XMP-dc:Description<={chinese}', str(path))
    return path


@pytest.mark.parametrize('suffix,format', [('png', 'PNG'), ('jpg', 'JPEG')])
def test_full_exif_chinese_and_geometry_survive_export(original, tmp_path, suffix, format):
    before = original.read_bytes()
    output = tmp_path / f'输出 图片.{suffix}'
    with Image.open(original) as source, ImageOps.exif_transpose(source) as upright:
        rendered = upright.resize((40, 60))
        save_export_image(rendered, output, source_path=original, format=format)
        with Image.open(output) as exported, ImageOps.exif_transpose(exported) as viewed:
            assert exported.size == viewed.size == (40, 60)
    a, b = metadata(original), metadata(output)
    for tag in ('IFD0:Make', 'IFD0:Model', 'ExifIFD:ISO', 'ExifIFD:FNumber', 'ExifIFD:ExposureTime',
                'ExifIFD:FocalLength', 'ExifIFD:LensModel', 'ExifIFD:DateTimeOriginal',
                'ExifIFD:SubSecTimeOriginal', 'ExifIFD:OffsetTimeOriginal', 'ExifIFD:SerialNumber',
                'ExifIFD:UserComment', 'GPS:GPSLatitude', 'GPS:GPSLongitude', 'XMP-dc:Description'):
        assert b[tag] == a[tag], tag
    assert b['ExifIFD:UserComment'] == '小勺子，原始拍摄信息。'
    assert b['IFD0:Orientation'] == b['IFD1:Orientation'] == b['XMP-tiff:Orientation'] == 1
    assert b['ExifIFD:ExifImageWidth'] == b['IFD0:ImageWidth'] == 40
    assert b['ExifIFD:ExifImageHeight'] == b['IFD0:ImageHeight'] == 60
    assert b['IFD1:ImageWidth'] == 40 and b['IFD1:ImageHeight'] == 60
    assert original.read_bytes() == before


def test_tiff_exif_is_copied_to_png(tmp_path):
    source = tmp_path / 'source.tiff'
    with Image.new('RGB', (100, 60)) as image:
        image.save(source)
        exiftool('-overwrite_original', '-ISO=320', '-Make=NIKON', '-Model=Z9', str(source))
        save_export_image(image, tmp_path / 'out.png', source_path=source, format='PNG')
    assert metadata(tmp_path / 'out.png')['ExifIFD:ISO'] == 320


@pytest.mark.parametrize('suffix,format', [('png', 'PNG'), ('jpg', 'JPEG')])
@pytest.mark.parametrize('endian', ['<', '>'])
def test_truncated_ifd1_is_rebuilt_without_losing_original_metadata(
        original, tmp_path, suffix, format, endian):
    source = tmp_path / '缩略图目录损坏.jpg'
    with Image.open(original) as photo:
        exif = photo.getexif()
        exif.endian = endian
        exif[271] = 'Unknown camera'
        exif[65001] = b'private EXIF payload'
        exif.get_ifd(34665)[37500] = b'opaque maker note payload\0\x01\x02'
        # 切换 TIFF 字节序时同步编码 Unicode 注释，避免样本本身产生乱码。
        exif.get_ifd(34665)[37510] = b'UNICODE\0' + '小勺子，原始拍摄信息。'.encode(
            'utf-16-le' if endian == '<' else 'utf-16-be')
        payload = bytearray(exif.tobytes())
        # 只破坏缩略图 IFD1：声明两条记录，实际只剩六字节，复现 Truncated IFD1。
        base = 6  # Exif\0\0 前缀之后才是 TIFF 偏移原点。
        ifd0 = base + struct.unpack_from(endian + 'I', payload, base + 4)[0]
        count = struct.unpack_from(endian + 'H', payload, ifd0)[0]
        struct.pack_into(endian + 'I', payload, ifd0 + 2 + count * 12, len(payload) - base)
        payload.extend(struct.pack(endian + 'H', 2) + b'\0' * 6)
        photo.save(source, exif=bytes(payload))
    before = source.read_bytes()
    target = tmp_path / f'修复导出.{suffix}'
    with Image.new('RGB', (320, 240), '#6395bd') as rendered:
        save_export_image(rendered, target, source_path=source, format=format)
    expected, actual = metadata(source), metadata(target)
    for tag in ('IFD0:Make', 'IFD0:Model', 'ExifIFD:ISO', 'ExifIFD:ExposureTime',
                'ExifIFD:DateTimeOriginal', 'ExifIFD:UserComment',
                'GPS:GPSLatitude', 'GPS:GPSLongitude'):
        assert actual[tag] == expected[tag], tag
    assert actual['ExifIFD:UserComment'] == '小勺子，原始拍摄信息。'
    assert actual['IFD0:Orientation'] == actual['IFD1:Orientation'] == 1
    assert actual['ExifIFD:ExifImageWidth'] == 320
    assert actual['ExifIFD:ExifImageHeight'] == 240
    assert actual['IFD1:ImageWidth'] == 160 and actual['IFD1:ImageHeight'] == 120
    assert 'IFD1' not in actual.get('ExifTool:Warning', '')
    with Image.open(target) as exported:
        assert exported.size == (320, 240)
        assert exported.getexif()[65001] == b'private EXIF payload'
        assert exported.getexif().get_ifd(34665)[37500] == b'opaque maker note payload\0\x01\x02'
    result = run_exiftool_once(
        [get_exiftool_executable_path(), '-b', '-ThumbnailImage', str(target)],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with Image.open(BytesIO(result.stdout)) as thumbnail:
        assert thumbnail.size == (160, 120)
        assert thumbnail.getpixel((80, 60)) == pytest.approx((99, 149, 189), abs=3)
    assert source.read_bytes() == before
    assert not list(tmp_path.glob('.birdstamp-export-*'))


def test_metadata_failure_keeps_existing_output_and_cleans_temporary(original, tmp_path, monkeypatch):
    target = tmp_path / 'existing.png'
    target.write_bytes(b'previous-complete-output')
    before = set(tmp_path.iterdir())
    monkeypatch.setattr(export_metadata, 'copy_export_metadata', lambda *a: (_ for _ in ()).throw(RuntimeError('EXIF 失败')))
    with Image.new('RGB', (10, 10)) as image, pytest.raises(RuntimeError, match='EXIF'):
        save_export_image(image, target, source_path=original, format='PNG')
    assert target.read_bytes() == b'previous-complete-output'
    assert set(tmp_path.iterdir()) == before


def test_export_cannot_overwrite_original(original):
    before = original.read_bytes()
    with Image.new('RGB', (10, 10)) as image, pytest.raises(ValueError, match='原图'):
        save_export_image(image, original, source_path=original, format='JPEG')
    assert original.read_bytes() == before


def test_missing_exiftool_fails_explicitly(original, tmp_path, monkeypatch):
    monkeypatch.setattr(export_metadata, 'get_exiftool_executable_path', lambda: None)
    target = tmp_path / 'failed.png'
    with Image.new('RGB', (10, 10)) as image, pytest.raises(RuntimeError, match='ExifTool'):
        save_export_image(image, target, source_path=original, format='PNG')
    assert not target.exists()
    assert not list(tmp_path.glob('.birdstamp-export-*'))


@pytest.mark.parametrize('route', ['gui', 'gif_frame', 'cli', 'video_source', 'video_frame'])
def test_all_image_write_routes_keep_original_exif(original, tmp_path, route):
    from birdstamp.gui.editor_exporter import _BirdStampExporterMixin
    from birdstamp.cli import _save_image
    from birdstamp.export_stage.core import _save_rendered_source_frame, _save_normalized_temp_frame
    output = tmp_path / f'{route}.png'
    with Image.new('RGB', (40, 30)) as image:
        if route in ('gui', 'gif_frame'):
            _BirdStampExporterMixin()._save_image(image, output, source_path=original, fast_png=route == 'gif_frame')
        elif route == 'cli':
            _save_image(image, output, pil_format='PNG', quality=92, source_path=original)
        elif route == 'video_source':
            _save_rendered_source_frame(image, output, source_path=original)
        else:
            _save_normalized_temp_frame(image, output, (60, 50), background_color='#000000', source_path=original)
    result = metadata(output)
    assert result['ExifIFD:ISO'] == 1600
    assert result['ExifIFD:UserComment'] == '小勺子，原始拍摄信息。'
    assert result['ExifIFD:ExifImageWidth'] == (60 if route == 'video_frame' else 40)


def test_matching_video_frame_copies_complete_png_and_chinese_exif(original, tmp_path):
    from birdstamp.export_stage.core import _normalize_and_cache_video_frame, _save_rendered_source_frame

    source_frame = tmp_path / 'source_frame.png'
    with Image.new('RGB', (40, 30)) as image:
        _save_rendered_source_frame(image, source_frame, source_path=original)
    plan = SimpleNamespace(frames_dir=tmp_path / 'video_frames')
    _normalize_and_cache_video_frame(
        index=1, source_frame_path=source_frame, label='中文', video_plan=plan,
        target_size=(40, 30), background_color='#000000', cancel_event=None,
    )
    video_frame = plan.frames_dir / 'frame_000001.png'
    assert video_frame.read_bytes() == source_frame.read_bytes()
    result = metadata(video_frame)
    assert result['ExifIFD:ISO'] == 1600
    assert result['ExifIFD:UserComment'] == '小勺子，原始拍摄信息。'


def test_dejitter_export_renames_uppercase_xmp_and_keeps_exact_bytes(sequence, tmp_path):
    from birdstamp.export_stage.sequence_preview import prepare_sequence_preview
    from birdstamp.export_stage.sequence_export import export_aligned_sequence
    seeds, _ = sequence
    sidecar = seeds[0].path.with_suffix('.XMP')
    payload = '<?xml version="1.0" encoding="UTF-8"?><x:xmpmeta xmlns:x="adobe:ns:meta/">中文小勺子</x:xmpmeta>'.encode()
    sidecar.write_bytes(payload)
    exiftool('-overwrite_original', '-ISO=1250', str(seeds[0].path))
    result = prepare_sequence_preview(seeds, cancel_event=threading.Event())
    folder = export_aligned_sequence(result, tmp_path, cancel_event=threading.Event())
    images = sorted(folder.glob('*.png'))
    assert images[0].with_suffix('.xmp').read_bytes() == payload
    assert metadata(images[0])['ExifIFD:ISO'] == 1250
    assert sidecar.read_bytes() == payload
    assert not images[1].with_suffix('.xmp').exists()


def test_sidecar_added_or_changed_after_analysis_invalidates_sequence(sequence):
    from birdstamp.export_stage.sequence_preview import prepare_sequence_preview
    seeds, result = sequence
    sidecar = seeds[0].path.with_suffix('.XMP')
    sidecar.write_bytes(b'first')
    assert not result.files_current()
    result = prepare_sequence_preview(seeds, cancel_event=threading.Event())
    assert result.files_current()
    sidecar.write_bytes(b'changed')
    assert not result.files_current()


def test_sidecar_copy_failure_rolls_back_whole_dejitter_export(sequence, tmp_path, monkeypatch):
    from birdstamp.export_stage import sequence_export
    _, result = sequence
    before = set(tmp_path.iterdir())
    monkeypatch.setattr(sequence_export, 'copy_export_sidecar', lambda *a: (_ for _ in ()).throw(OSError('XMP 失败')))
    with pytest.raises(OSError, match='XMP'):
        sequence_export.export_aligned_sequence(result, tmp_path, cancel_event=threading.Event())
    assert set(tmp_path.iterdir()) == before


def test_unknown_exif_and_opaque_maker_notes_are_not_dropped(tmp_path):
    source, target = tmp_path / 'private.jpg', tmp_path / 'out.png'
    exif = Image.Exif()
    exif[271] = 'Unknown camera'
    exif[65001] = b'private EXIF payload'
    exif[34665] = {37500: b'opaque maker note payload\0\x01\x02'}
    with Image.new('RGB', (40, 30)) as image:
        image.save(source, exif=exif)
        save_export_image(image, target, source_path=source, format='PNG')
    with Image.open(source) as a, Image.open(target) as b:
        assert a.getexif()[65001] == b.getexif()[65001]
        assert a.getexif().get_ifd(34665)[37500] == b.getexif().get_ifd(34665)[37500]
