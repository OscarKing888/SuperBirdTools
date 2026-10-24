"""实际大 XMP、批量导出、缓存配对及提交失败回归。"""
from pathlib import Path

import pytest
from PIL import Image

from birdstamp.export_metadata import save_export_image
from birdstamp import export_image_pair
from test_export_metadata import exiftool, embed_xmp, exported_xmp, metadata, xmp_packet, NS


@pytest.mark.parametrize('kind', ['padded', 'large', 'extended'])
@pytest.mark.parametrize('suffix,format', [('jpg', 'JPEG'), ('png', 'PNG')])
def test_large_xmp_stays_in_sidecar_with_originals_unchanged(tmp_path, kind, suffix, format):
    source = tmp_path / ('原片.jpg' if kind == 'extended' else '原片.tif')
    with Image.new('RGB', (120, 80), '#6395bd') as image:
        image.save(source)
    description = '普通翠鸟，中文备注。' * (6000 if kind != 'padded' else 1)
    if kind == 'extended':
        text = tmp_path / '文字.txt'
        text.write_text(description, encoding='utf-8')
        exiftool('-overwrite_original', f'-XMP-dc:Description<={text}', str(source))
        assert 'HasExtendedXMP' in exiftool('-s', '-XMP:all', str(source))
    else:
        body = xmp_packet(f'<custom:private>{description}</custom:private>')
        if kind == 'padded':
            begin = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            end = b'<?xpacket end="w"?>'
            body = begin + body + b' ' * (65536 - len(begin + body + end)) + end
            assert len(body) == 65536
        embed_xmp(source, body, tmp_path)
    sidecar = source.with_suffix('.XMP')
    sidecar.write_bytes(xmp_packet('<sp:bird_species_cn>白鹭</sp:bird_species_cn>', 'xmp:Rating="0"'))
    before = {path: path.read_bytes() for path in (source, sidecar)}
    target = tmp_path / f'导出.{suffix}'
    with Image.new('RGB', (40, 30)) as image:
        save_export_image(image, target, source_path=source, format=format)
    root = exported_xmp(target)
    assert root.find('.//sp:bird_species_cn', NS).text == '白鹭'
    if kind == 'extended':
        assert metadata(target.with_suffix('.xmp'))['XMP-dc:Description'] == description
    else:
        assert root.find('.//custom:private', NS).text == description
    assert metadata(target.with_suffix('.xmp'))['XMP-xmp:Rating'] == 0
    for path, contents in before.items():
        assert path.read_bytes() == contents
    assert not list(tmp_path.glob('.birdstamp-*'))


@pytest.mark.parametrize('existing', [False, True])
def test_second_publication_failure_restores_image_and_uppercase_sidecar(tmp_path, monkeypatch, existing):
    source, target = tmp_path / '原图.png', tmp_path / '成片.jpg'
    with Image.new('RGB', (20, 10)) as image:
        image.save(source)
    sidecar = target.with_suffix('.XMP' if existing else '.xmp')
    if existing:
        target.write_bytes(b'previous image')
        sidecar.write_bytes(b'previous XMP')
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    replace = export_image_pair.os.replace
    def fail_second(src, dst):
        if Path(dst) == target:
            raise OSError('模拟图片发布失败')
        return replace(src, dst)
    monkeypatch.setattr(export_image_pair.os, 'replace', fail_second)
    with Image.new('RGB', (10, 5)) as image, pytest.raises(OSError, match='发布失败'):
        save_export_image(image, target, source_path=source, format='JPEG')
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_export_cannot_replace_source_sidecar_through_another_image_extension(tmp_path):
    source = tmp_path / '原图.tif'
    with Image.new('RGB', (20, 10)) as image:
        image.save(source)
        with pytest.raises(ValueError, match='原图 XMP'):
            save_export_image(image, source.with_suffix('.jpg'), source_path=source, format='JPEG')
    assert not source.with_suffix('.xmp').exists()


def test_batch_and_cached_frames_keep_the_same_sidecar(tmp_path, monkeypatch):
    from test_exported_image_reuse import _Exporter, _jobs
    from birdstamp.gui import editor_exporter
    from birdstamp.exported_image_index import materialize_exported_image
    from birdstamp.export_stage.core import _copy_complete_frame

    jobs = _jobs(tmp_path)
    for job in jobs:
        job.path.with_suffix('.xmp').write_bytes(xmp_packet('<sp:bird_species_cn>普通翠鸟</sp:bird_species_cn>'))
    monkeypatch.setattr(editor_exporter, 'render_video_frame', lambda *a, **kw: Image.new('RGB', (40, 30)))
    outputs = [tmp_path / '批量' / f'{i}.png' for i in range(len(jobs))]
    completed, failed = _Exporter()._export_render_jobs_to_images(jobs, outputs, label='sidecar regression')
    assert set(completed) == set(outputs) and not failed
    for index, output in enumerate(outputs):
        exported_xmp(output)
        cached = tmp_path / '缓存' / f'{index}.png'
        assert materialize_exported_image(output, cached, original=jobs[index].path)
        video = tmp_path / '视频帧' / f'{index}.png'
        _copy_complete_frame(cached, video)
        for path in (cached, video):
            assert path.read_bytes() == output.read_bytes()
            assert path.with_suffix('.xmp').read_bytes() == output.with_suffix('.xmp').read_bytes()


def test_cache_and_export_index_invalidate_missing_or_changed_sidecar(tmp_path):
    from birdstamp.export_frame_cache import (path_signature, create_frame_cache_plan,
        empty_frame_manifest, update_frame_manifest_record, reusable_frame_path)
    from birdstamp.exported_image_index import ExportedImageIndex
    # 使用真实导出配对，缓存指纹必须把 sidecar 算进去。
    source, output = tmp_path / 'original.png', tmp_path / 'output.png'
    with Image.new('RGB', (20, 10)) as image:
        image.save(source)
        save_export_image(image, output, source_path=source, format='PNG')
    index = ExportedImageIndex(tmp_path / 'index.json')
    index.add_many([(source, path_signature(source), 'frame', output)])
    records = index.load()
    assert index.find(records, source=source, frame_signature='frame') == output
    sidecar = output.with_suffix('.xmp')
    sidecar.write_bytes(sidecar.read_bytes() + b'\n')
    assert index.find(records, source=source, frame_signature='frame') is None
    index.add_many([(source, path_signature(source), 'frame', output)])
    sidecar.unlink()
    assert index.find(index.load(), source=source, frame_signature='frame') is None
    plan = create_frame_cache_plan(tmp_path / 'video.mp4', bucket_kind='source', bucket_key='test', persistent=True)
    cached = plan.frames_dir / 'frame_000001.png'
    with Image.new('RGB', (20, 10)) as image:
        save_export_image(image, cached, source_path=source, format='PNG')
    manifest = empty_frame_manifest(plan)
    args = dict(index=1, source_path=source, source_signature=path_signature(source), frame_signature='frame')
    update_frame_manifest_record(plan, manifest, frame_path=cached, **args)
    assert reusable_frame_path(plan, manifest, **args) == cached
    cached.with_suffix('.xmp').unlink()
    assert reusable_frame_path(plan, manifest, **args) is None


def test_failed_rollback_keeps_recovery_sidecar_and_reports_it(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.png', tmp_path / 'output.jpg'
    with Image.new('RGB', (20, 10)) as image:
        image.save(source)
    target.write_bytes(b'old image')
    target.with_suffix('.xmp').write_bytes(b'old sidecar')
    replace = export_image_pair.os.replace
    def fail_publication_and_restore(src, dst):
        if Path(dst) == target or Path(src).parent.name.startswith('.birdstamp-backup-'):
            raise OSError('模拟磁盘故障')
        return replace(src, dst)
    monkeypatch.setattr(export_image_pair.os, 'replace', fail_publication_and_restore)
    with Image.new('RGB', (20, 10)) as image, pytest.raises(RuntimeError, match='恢复副本') as failure:
        save_export_image(image, target, source_path=source, format='JPEG')
    folder, = tmp_path.glob('.birdstamp-backup-*')
    assert str(folder) in str(failure.value)
    assert (folder / 'output.xmp').read_bytes() == b'old sidecar'
    assert target.read_bytes() == b'old image'
