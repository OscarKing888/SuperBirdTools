"""真实 EXIF 写入/读回、中文保留、统一出口与 sidecar 配对。"""
from pathlib import Path
from io import BytesIO
import json
import struct
import threading
from xml.etree import ElementTree as ET
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
                'ExifIFD:UserComment', 'GPS:GPSLatitude', 'GPS:GPSLongitude'):
        assert b[tag] == a[tag], tag
    assert b['ExifIFD:UserComment'] == '小勺子，原始拍摄信息。'
    assert b['IFD0:Orientation'] == b['IFD1:Orientation'] == 1
    sidecar_tags = metadata(output.with_suffix('.xmp'))
    assert sidecar_tags['XMP-dc:Description'] == a['XMP-dc:Description']
    assert sidecar_tags['XMP-tiff:Orientation'] == 1
    exported_xmp(output)
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
    original.with_suffix('.xmp').write_bytes(xmp_packet('<sp:bird_species_cn>普通翠鸟</sp:bird_species_cn>'))
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
    assert exported_xmp(output).find('.//sp:bird_species_cn', NS).text == '普通翠鸟'


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


def test_dejitter_export_renames_uppercase_xmp_and_corrects_geometry(sequence, tmp_path):
    from birdstamp.export_stage.sequence_preview import prepare_sequence_preview
    from birdstamp.export_stage.sequence_export import export_aligned_sequence
    seeds, _ = sequence
    sidecar = seeds[0].path.with_suffix('.XMP')
    payload = xmp_packet('<dc:title><rdf:Alt><rdf:li xml:lang="x-default">中文小勺子</rdf:li></rdf:Alt></dc:title>')
    sidecar.write_bytes(payload)
    exiftool('-overwrite_original', '-ISO=1250', str(seeds[0].path))
    result = prepare_sequence_preview(seeds, cancel_event=threading.Event())
    folder = export_aligned_sequence(result, tmp_path, cancel_event=threading.Event())
    images = sorted(folder.glob('*.png'))
    assert '中文小勺子' in images[0].with_suffix('.xmp').read_text(encoding='utf-8')
    assert metadata(images[0].with_suffix('.xmp'))['XMP-tiff:Orientation'] == 1
    assert metadata(images[0])['ExifIFD:ISO'] == 1250
    assert sidecar.read_bytes() == payload
    assert images[1].with_suffix('.xmp').is_file()


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
    from app_common.exif_io import export_xmp
    monkeypatch.setattr(export_xmp, 'read_export_xmp', lambda *a: (_ for _ in ()).throw(OSError('XMP 失败')))
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


NS = {
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'sp': 'https://superbirdtools.local/xmp/superpicky/1.0/',
    'custom': 'https://example.test/private/',
    'xmp': 'http://ns.adobe.com/xap/1.0/',
    'tiff': 'http://ns.adobe.com/tiff/1.0/',
    'exif': 'http://ns.adobe.com/exif/1.0/',
}


def xmp_packet(properties='', attributes='', other=''):
    namespaces = ' '.join(f'xmlns:{prefix}="{uri}"' for prefix, uri in NS.items())
    return (f'<x:xmpmeta xmlns:x="adobe:ns:meta/" {namespaces}><rdf:RDF>'
            f'<rdf:Description rdf:about="" {attributes}>{properties}</rdf:Description>'
            f'{other}</rdf:RDF></x:xmpmeta>').encode('utf-8')


def embed_xmp(source, payload, tmp_path):
    packet = tmp_path / 'packet.xmp'
    packet.write_bytes(payload)
    exiftool('-overwrite_original', f'-XMP<={packet}', str(source))


def exported_xmp(output):
    result = run_exiftool_once([get_exiftool_executable_path(), '-b', '-XMP', str(output)],
                              capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'', '成片必须只用 sidecar XMP'
    return ET.parse(output.with_suffix('.xmp')).getroot()


@pytest.mark.parametrize('suffix,format', [('png', 'PNG'), ('jpg', 'JPEG')])
def test_embedded_custom_xmp_is_preserved_without_sidecar(original, tmp_path, suffix, format):
    embed_xmp(original, xmp_packet('<custom:private>未知字段：翠鸟</custom:private>',
                                  'sp:bird_species_cn="普通翠鸟"'), tmp_path)
    output = tmp_path / f'成片.{suffix}'
    with Image.new('RGB', (40, 30)) as image:
        save_export_image(image, output, source_path=original, format=format)
    root = exported_xmp(output)
    assert root.find('.//custom:private', NS).text == '未知字段：翠鸟'
    assert metadata(output)['ExifIFD:ISO'] == 1600
    assert '普通翠鸟' in ET.tostring(root, encoding='unicode')


@pytest.mark.parametrize('suffix,format', [('png', 'PNG'), ('jpg', 'JPEG')])
def test_sidecar_merges_full_xmp_and_overrides_embedded_properties(original, tmp_path, suffix, format):
    embed_xmp(original, xmp_packet(
        '<custom:private>保留内嵌字段</custom:private>'
        '<dc:subject><rdf:Bag><rdf:li>旧标签</rdf:li></rdf:Bag></dc:subject>'
        '<dc:description><rdf:Alt><rdf:li xml:lang="x-default">旧描述</rdf:li></rdf:Alt></dc:description>',
        'sp:bird_species_cn="旧鸟名" xmp:Rating="5"'), tmp_path)
    sidecar = original.with_suffix('.XMP')
    payload = xmp_packet(
        '<sp:bird_species_cn>普通翠鸟</sp:bird_species_cn>'
        '<sp:bird_sharpness_score>812.50</sp:bird_sharpness_score>'
        '<dc:subject><rdf:Bag><rdf:li>中国鸟类</rdf:li><rdf:li>湿地</rdf:li></rdf:Bag></dc:subject>'
        '<dc:description><rdf:Alt><rdf:li xml:lang="x-default"></rdf:li></rdf:Alt></dc:description>'
        '<custom:structure rdf:parseType="Resource"><custom:child>结构字段</custom:child></custom:structure>',
        'xmp:Rating="0" tiff:Orientation="6" tiff:ImageWidth="120" exif:PixelXDimension="120"',
        '<rdf:Description rdf:about="https://example.test/resource"><custom:asset>资源信息</custom:asset></rdf:Description>')
    sidecar.write_bytes(payload)
    before = original.read_bytes()
    output = tmp_path / f'新成片.{suffix}'
    with Image.new('RGB', (40, 30)) as image:
        save_export_image(image, output, source_path=original, format=format)
    root = exported_xmp(output)
    assert root.find('.//custom:private', NS).text == '保留内嵌字段'
    assert root.find('.//sp:bird_species_cn', NS).text == '普通翠鸟'
    assert root.find('.//sp:bird_sharpness_score', NS).text == '812.50'
    assert root.find('.//custom:child', NS).text == '结构字段'
    assert root.find('.//custom:asset', NS).text == '资源信息'
    assert [node.text for node in root.findall('.//dc:subject/rdf:Bag/rdf:li', NS)] == ['中国鸟类', '湿地']
    text = ET.tostring(root, encoding='unicode')
    assert '旧鸟名' not in text and '旧描述' not in text
    result = metadata(output.with_suffix('.xmp'))
    assert result['XMP-xmp:Rating'] == 0
    assert result['XMP-tiff:Orientation'] == 1
    assert result['XMP-tiff:ImageWidth'] == result['XMP-exif:ExifImageWidth'] == 40
    assert metadata(output)['ExifIFD:ISO'] == 1600
    assert original.read_bytes() == before and sidecar.read_bytes() == payload
    assert not list(tmp_path.glob('.birdstamp-export-*'))


def test_invalid_sidecar_fails_without_replacing_existing_output(original, tmp_path):
    sidecar = original.with_suffix('.xmp')
    sidecar.write_bytes(b'<broken>')
    output = tmp_path / 'existing.jpg'
    output.write_bytes(b'previous complete export')
    with Image.new('RGB', (40, 30)) as image, pytest.raises(ValueError, match='XMP'):
        save_export_image(image, output, source_path=original, format='JPEG')
    assert output.read_bytes() == b'previous complete export'
    assert sidecar.read_bytes() == b'<broken>'
    assert not list(tmp_path.glob('.birdstamp-export-*'))


def test_sidecar_changes_invalidate_export_cache_even_with_stale_metadata(original):
    from birdstamp.export_stage import VideoFrameJob, source_frame_signature_for_job
    job = VideoFrameJob(path=original, settings={}, raw_metadata={}, metadata_context={})
    without = source_frame_signature_for_job(job)
    sidecar = original.with_suffix('.XMP')
    sidecar.write_bytes(xmp_packet('<custom:private>一</custom:private>'))
    first = source_frame_signature_for_job(job)
    assert first != without
    sidecar.write_bytes(xmp_packet('<custom:private>第二次</custom:private>'))
    assert source_frame_signature_for_job(job) not in {first, without}
    sidecar.unlink()
    assert source_frame_signature_for_job(job) == without


def test_pending_sql_edit_invalidates_export_before_sidecar_write(original):
    from app_common.exif_io.metadata_store import get_store
    from birdstamp.export_stage import VideoFrameJob, source_frame_signature_for_job
    job = VideoFrameJob(path=original, settings={}, raw_metadata={}, metadata_context={})
    first = source_frame_signature_for_job(job)
    store = get_store()
    store.submit_edit(str(original), {"title": "新导出标题"}).result(3)
    assert not original.with_suffix('.xmp').exists()
    assert source_frame_signature_for_job(job) != first


def test_export_sidecar_digest_detects_preserved_mtime_edit(original):
    import os
    from birdstamp.export_stage import VideoFrameJob, source_frame_signature_for_job
    from birdstamp.export_image_pair import sidecar_signature
    job = VideoFrameJob(path=original, settings={}, raw_metadata={}, metadata_context={})
    sidecar = original.with_suffix('.xmp')
    sidecar.write_bytes(xmp_packet('<custom:private>一</custom:private>'))
    stamp = sidecar.stat()
    first = source_frame_signature_for_job(job)
    pair_first = sidecar_signature(original)
    sidecar.write_bytes(xmp_packet('<custom:private>二</custom:private>'))
    os.utime(sidecar, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert source_frame_signature_for_job(job) != first
    assert sidecar_signature(original) != pair_first


def test_exiftool_rejected_xmp_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(export_metadata, 'run_exiftool', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout='0 image files updated', stderr='Warning: Invalid XMP data for XMP:XMP'))
    with pytest.raises(RuntimeError, match='Invalid XMP'):
        export_metadata._run('exiftool', ['-XMP<=packet.xmp', 'output.jpg'])


@pytest.mark.parametrize('identity', ['', 'filename', 'uuid'])
def test_xmp_merge_handles_split_descriptions_and_namespace_prefixes(original, identity):
    from birdstamp.export_xmp import merge_export_xmp
    from app_common.exif_io.xmp_sidecar import _photo_descriptions

    about = {'': '', 'filename': original.as_uri(), 'uuid': 'urn:uuid:550e8400-e29b-41d4-a716-446655440000'}[identity]
    embedded = xmp_packet('<sp:bird_species_cn>旧鸟名</sp:bird_species_cn>', 'xmp:Rating="5"')
    sidecar = (
        f'<m:xmpmeta xmlns:m="adobe:ns:meta/" xmlns:r="{NS["rdf"]}" '
        f'xmlns:p="{NS["sp"]}" xmlns:private="{NS["custom"]}" '
        f'xmlns:xx="{NS["xmp"]}"><r:RDF>'
        f'<r:Description r:about="{about}" p:bird_species_cn="普通翠鸟"/>'
        f'<r:Description r:about="{about}"><xx:Rating>0</xx:Rating>'
        '<private:typed r:datatype="private:custom">未知类型</private:typed></r:Description>'
        '<r:Description r:about="https://example.test/other" xx:Rating="3"/>'
        '</r:RDF></m:xmpmeta>'
    ).encode('utf-8')
    merged = merge_export_xmp(embedded, sidecar, source=original)
    root = ET.fromstring(merged)
    descriptions = _photo_descriptions(root, str(original.parent / 'export.jpg'))
    bird_key = f'{{{NS["sp"]}}}bird_species_cn'
    rating_key = f'{{{NS["xmp"]}}}Rating'
    assert [node.attrib[bird_key] for node in descriptions if bird_key in node.attrib] == ['普通翠鸟']
    assert not any(rating_key in node.attrib for node in descriptions)
    assert [node.find('xmp:Rating', NS).text for node in descriptions
            if node.find('xmp:Rating', NS) is not None] == ['0']
    assert b'xmlns:private="https://example.test/private/"' in merged
    assert '旧鸟名' not in merged.decode('utf-8')


def test_sidecar_only_metadata_survives_without_source_exif(tmp_path):
    source, target = tmp_path / 'plain.png', tmp_path / 'export.jpg'
    with Image.new('RGB', (30, 20)) as image:
        image.save(source)
        source.with_suffix('.xmp').write_bytes(xmp_packet('<sp:bird_species_cn>白鹭</sp:bird_species_cn>'))
        save_export_image(image, target, source_path=source, format='JPEG')
    assert exported_xmp(target).find('.//sp:bird_species_cn', NS).text == '白鹭'


@pytest.mark.parametrize("pipeline", ["image", "retained_frame"])
def test_export_keeps_render_signature_and_xmp_at_one_committed_version(
    original, tmp_path, monkeypatch, pipeline,
):
    from app_common.exif_io.metadata_store import get_store
    from birdstamp import export_stage
    from birdstamp.export_stage import core
    from birdstamp.export_snapshot import prepare_export_job
    from birdstamp.gui import editor_exporter, template_context
    from birdstamp.export_frame_cache import create_frame_cache_plan

    store = get_store()
    store.submit_edit(str(original), {"title": "旧标题·苍鹭"}).result(3)
    job = export_stage.VideoFrameJob(original, {}, {}, {})
    prepare_export_job(job)
    signature = export_stage.source_frame_signature_for_job(job)
    assert job.raw_metadata["XMP-dc:Title"] == "旧标题·苍鹭"

    def render(frozen_job, **kwargs):
        assert frozen_job.photo_info.metadata_is_snapshot
        store.submit_edit(str(original), {"title": "新标题·白鹭"}).result(3)
        assert store.flush_paths([str(original)], timeout=5)
        provider = template_context.ExifTemplateContextProvider("Title")
        assert provider.get_text_content(frozen_job.photo_info) == "旧标题·苍鹭"
        return Image.new("RGB", (40, 30), "red")

    # Saving must consume the prepared packet even after the source XMP changes.
    monkeypatch.setattr(export_metadata, "read_export_xmp",
                        lambda *_: pytest.fail("save re-read live XMP"))
    if pipeline == "image":
        monkeypatch.setattr(editor_exporter, "render_video_frame", render)
        output = tmp_path / "固定快照.png"
        editor_exporter._BirdStampExporterMixin()._render_and_save_image_task(
            editor_exporter._ImageExportTask(job, output), template_paths={},
            bird_box_cache={}, bird_box_lock=threading.Lock())
    else:
        monkeypatch.setattr(export_stage, "render_video_frame", render)
        plan = create_frame_cache_plan(tmp_path / "movie.mp4", bucket_kind="source",
                                       bucket_key="snapshot", persistent=True)
        result = core._render_and_cache_source_frame(
            job=job, index=1, source_plan=plan, template_paths={}, frame_signature=signature,
            bird_box_cache={}, bird_box_lock=threading.Lock(), cancel_event=None)
        output = result[2]
        assert result[4] == signature
    assert export_stage.source_frame_signature_for_job(job) == signature
    assert metadata(output.with_suffix('.xmp'))['XMP-dc:Title'] == "旧标题·苍鹭"
    assert metadata(original.with_suffix('.xmp'))['XMP-dc:Title'] == "新标题·白鹭"


def test_export_provider_report_row_is_frozen(original, monkeypatch):
    from birdstamp.export_snapshot import export_photo_info
    from birdstamp.gui import template_context as tc
    row = {"bird_species_cn": "苍鹭"}
    monkeypatch.setattr(tc, "get_report_db_row_for_path", lambda _: row)
    info = export_photo_info(original, {"SourceFile": str(original)})
    row["bird_species_cn"] = "白鹭"
    assert tc.ReportDBTemplateContextProvider._resolve_row(info)["bird_species_cn"] == "苍鹭"
    editor_info = tc.ensure_editor_photo_info(info)
    assert tc.ReportDBTemplateContextProvider._resolve_row(editor_info)["bird_species_cn"] == "苍鹭"


def test_cli_render_saves_the_version_used_for_pixels(original, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from app_common.exif_io.metadata_store import get_store
    from birdstamp import cli
    from birdstamp.gui import editor_template, editor_core

    store = get_store()
    store.submit_edit(str(original), {"title": "命令行旧标题"}).result(3)
    monkeypatch.setattr(cli, "load_config", lambda: {})
    monkeypatch.setattr(cli, "_find_template_path", lambda *_: None)
    monkeypatch.setattr(editor_core, "apply_full_crop", lambda image, **kwargs: image)

    def render(image, *, raw_metadata, photo_info, **kwargs):
        assert raw_metadata["XMP-dc:Title"] == "命令行旧标题"
        assert photo_info.metadata_is_snapshot
        store.submit_edit(str(original), {"title": "命令行新标题"}).result(3)
        return image

    monkeypatch.setattr(editor_template, "render_template_overlay", render)
    output = tmp_path / "cli-output"
    result = CliRunner().invoke(cli.app, ["render", str(original), "--out", str(output),
                                         "--format", "png", "--name", "result.{ext}"])
    assert result.exit_code == 0, result.output
    assert metadata(output / "result.xmp")["XMP-dc:Title"] == "命令行旧标题"
    assert store.pending_edits(str(original))["fields"]["XMP-dc:Title"] == "命令行新标题"
