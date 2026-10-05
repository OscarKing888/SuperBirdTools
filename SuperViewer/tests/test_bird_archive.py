"""照片/XMP 的真实文件归档与失败恢复；所有素材均在临时目录。"""
from pathlib import Path
import unicodedata

from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.report_db import ReportDB
from SuperViewer.superviewer import bird_archive as archive


@pytest.fixture(autouse=True)
def embedded_date(monkeypatch):
    monkeypatch.setattr(archive.PhotoMetaDataEXIFEmbeded, "read",
                        lambda self, path: {"ExifIFD:DateTimeOriginal": "2026:10:05 08:30:15"})


def photo(directory, name="DSC01234.jpg", bird="白鹭"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        Image.new("RGB", (20, 12), "white").save(path)
    else:
        path.write_bytes(b"raw-photo")
    if bird:
        assert PhotoMetaDataXMP().write_title(str(path), bird)
        assert PhotoMetaDataXMP().write_subjects(str(path), ["中国鸟类", "珍藏"])
    return path


def run(paths, target, mode="move", date=True, **kwargs):
    return archive.archive_photos(paths, archive.ArchiveOptions(str(target), mode, date), **kwargs)


def test_move_chinese_photo_xmp_with_capture_time_and_read_back(tmp_path):
    source = photo(tmp_path / "相机")
    original = source.read_bytes()
    sidecar = source.with_suffix(".xmp").read_bytes()
    result, = run([source], tmp_path / "名册")
    assert result.status == "success"
    destination = Path(result.destinations[0])
    assert destination == tmp_path / "名册/白鹭/Export/20261005_083015_DSC01234.jpg"
    assert destination.read_bytes() == original
    assert destination.with_suffix(".xmp").read_bytes() == sidecar
    assert PhotoMetaDataXMP().read(str(destination))["Title"] == "白鹭"
    assert PhotoMetaDataXMP().read_subjects(str(destination)) == ["中国鸟类", "珍藏"]
    assert not source.exists() and not source.with_suffix(".xmp").exists()


@pytest.mark.parametrize("conflict", ["photo", "sidecar", "other_extension", "directory"])
def test_copy_case_insensitive_conflicts_never_overwrite(tmp_path, conflict):
    source = photo(tmp_path / "source")
    folder = tmp_path / "archive/白鹭/Export"
    folder.mkdir(parents=True)
    name = {"photo": "dsc01234.JPG", "sidecar": "DSC01234.XMP", "other_extension": "DSC01234.ARW", "directory": "DSC01234"}[conflict]
    existing = folder / name
    if conflict == "directory":
        existing.mkdir()
    else:
        existing.write_bytes(b"keep")
    first, = run([source], folder.parent.parent, "copy", False)
    second, = run([source], folder.parent.parent, "copy", False)
    assert Path(first.destinations[0]).name == "DSC01234_002.jpg"
    assert Path(second.destinations[0]).name == "DSC01234_003.jpg"
    assert source.exists() and source.with_suffix(".xmp").exists()
    assert existing.is_dir() if conflict == "directory" else existing.read_bytes() == b"keep"


def test_raw_jpeg_siblings_share_name_and_each_directory_has_xmp(tmp_path):
    raw = photo(tmp_path / "source", "DSC01234.ARW")
    jpeg = photo(raw.parent)
    result, = run([raw, jpeg, raw], tmp_path / "archive")
    assert len(result.sources) == 2
    assert result.status == "success"
    assert {Path(p).stem for p in result.destinations} == {"20261005_083015_DSC01234"}
    assert {Path(p).parent.name for p in result.destinations} == {"RAW", "Export"}
    assert len(list((tmp_path / "archive/白鹭").rglob("*.xmp"))) == 2
    for destination in result.destinations:
        assert PhotoMetaDataXMP().read(destination)["Title"] == "白鹭"
        assert PhotoMetaDataXMP().read_subjects(destination) == ["中国鸟类", "珍藏"]
    assert not raw.with_suffix(".xmp").exists()


def test_unselected_sibling_keeps_its_xmp(tmp_path):
    raw = photo(tmp_path / "source", "DSC01234.ARW")
    jpeg = photo(raw.parent)
    before = raw.with_suffix(".xmp").read_bytes()
    result, = run([raw], tmp_path / "archive")
    assert result.status == "success" and jpeg.exists() and not raw.exists()
    assert jpeg.with_suffix(".xmp").read_bytes() == before
    assert Path(result.destinations[0]).with_suffix(".xmp").read_bytes() == before


def test_unknown_name_corrupt_sidecar_missing_and_nonphoto_are_reported(tmp_path):
    unnamed = photo(tmp_path / "source", "unnamed.jpg", None)
    corrupt = photo(unnamed.parent, "corrupt.jpg")
    corrupt.with_suffix(".xmp").write_text("<broken", encoding="utf-8")
    video = unnamed.parent / "clip.mp4"
    video.write_bytes(b"video")
    results = run([unnamed, corrupt, video, unnamed.parent / "missing.jpg"], tmp_path / "archive")
    assert [r.status for r in results] == ["skipped", "failed", "skipped", "failed"]
    assert unnamed.exists() and corrupt.exists() and video.exists()
    assert not (tmp_path / "archive").exists()


def test_no_capture_date_never_uses_file_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(archive.PhotoMetaDataEXIFEmbeded, "read", lambda *_: {})
    source = photo(tmp_path / "source")
    result, = run([source], tmp_path / "archive")
    assert Path(result.destinations[0]).name == "日期未知_DSC01234.jpg"


def test_report_only_metadata_follows_photo_and_database_is_unchanged(tmp_path):
    source = photo(tmp_path / "source", bird=None)
    db = ReportDB(str(source.parent))
    try:
        db.insert_photo({"filename": source.stem, "original_path": str(source), "current_path": str(source),
                         "bird_species_cn": "红嘴蓝鹊", "rating": 0, "pick": 0,
                         "date_time_original": "2025:01:02 03:04:05"})
    finally:
        db.close()
    db_file = source.parent / ".superpicky/report.db"
    before = db_file.read_bytes()
    result, = run([source], tmp_path / "archive")
    assert result.status == "success", result.message
    destination = Path(result.destinations[0])
    assert destination.parent.parent.name == "红嘴蓝鹊"
    assert destination.name.startswith("20250102_030405_")
    meta = PhotoMetaDataXMP().read(str(destination))
    assert meta["bird_species_cn"] == "红嘴蓝鹊" and str(meta["rating"]) == "0"
    assert db_file.read_bytes() == before


def test_sidecar_bird_name_wins_over_stale_report(tmp_path):
    source = photo(tmp_path / "source", bird="白鹭")
    result, = run([source], tmp_path / "archive", report_rows={str(source): {"bird_species_cn": "旧鸟名", "rating": 0}})
    assert Path(result.destinations[0]).parent.parent.name == "白鹭"
    assert PhotoMetaDataXMP().read(result.destinations[0])["Title"] == "白鹭"


def test_failure_publishing_xmp_restores_photo_and_sidecar(tmp_path, monkeypatch):
    from app_common import file_transactions as tx
    source = photo(tmp_path / "source")
    before = (source.read_bytes(), source.with_suffix(".xmp").read_bytes())
    original = tx.publish_without_overwrite

    def fail_sidecar(src, dest):
        if Path(dest).suffix == ".xmp":
            raise OSError("归档盘已满")
        original(src, dest)

    monkeypatch.setattr(tx, "publish_without_overwrite", fail_sidecar)
    result, = run([source], tmp_path / "archive")
    assert result.status == "failed" and "归档盘已满" in result.message
    assert (source.read_bytes(), source.with_suffix(".xmp").read_bytes()) == before
    assert not list((tmp_path / "archive").rglob("*.jpg"))
    assert not list((tmp_path / "archive").rglob("*.tmp"))


def test_cancel_between_groups_keeps_completed_files_and_remaining_sources(tmp_path):
    first = photo(tmp_path / "source", "first.jpg")
    second = photo(first.parent, "second.jpg")
    seen = []
    results = run([first, second], tmp_path / "archive", cancelled=lambda: bool(seen), on_result=seen.append)
    assert len(results) == 1 and not first.exists() and second.exists()


def test_windows_names_unicode_aliases_and_already_archived(tmp_path):
    assert archive.safe_component("CON") == "_CON"
    assert archive.safe_component(" 白/鹭:*? ") == "白_鹭___"
    source = photo(tmp_path / "source", bird="café")
    target = tmp_path / "archive"
    existing = target / unicodedata.normalize("NFD", "CAFÉ")
    existing.mkdir(parents=True)
    result, = run([source], target)
    assert Path(result.destinations[0]).parent == existing / "Export"
    again, = run(result.destinations, target)
    assert again.status == "failed" and "已经位于" in again.message


def test_species_symlink_cannot_escape_archive(tmp_path):
    source = photo(tmp_path / "source")
    target = tmp_path / "archive"
    target.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "白鹭").symlink_to(outside, target_is_directory=True)
    result, = run([source], target)
    assert result.status == "failed" and source.exists()
    assert not list(outside.iterdir())


def test_xmp_explicit_source_reference_survives_rename(tmp_path):
    import xml.etree.ElementTree as ET
    source = photo(tmp_path / "source")
    sidecar = source.with_suffix(".xmp")
    tree = ET.parse(sidecar)
    desc = tree.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
    desc.set("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", source.as_uri())
    tree.write(sidecar, encoding="utf-8", xml_declaration=True)
    result, = run([source], tmp_path / "archive")
    assert result.status == "success"
    assert PhotoMetaDataXMP().read(result.destinations[0])["Title"] == "白鹭"


def test_report_hydration_does_not_resurrect_stale_standard_xmp_edits(tmp_path):
    import xml.etree.ElementTree as ET
    source = photo(tmp_path / 'source')
    sidecar = source.with_suffix('.xmp')
    tree = ET.parse(sidecar)
    desc = tree.find('.//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description')
    ET.SubElement(desc, '{http://ns.adobe.com/tiff/1.0/}Model').text = '新相机'
    ET.SubElement(desc, '{http://ns.adobe.com/exif/1.0/}DateTimeOriginal').text = '2026-10-04T09:00:00'
    tree.write(sidecar, encoding='utf-8', xml_declaration=True)
    result, = run([source], tmp_path / 'archive', report_rows={str(source): {
        'bird_species_cn': '旧鸟名', 'camera_model': '旧相机', 'date_time_original': '2000:01:01 01:01:01', 'rating': 0}})
    assert result.status == 'success'
    assert Path(result.destinations[0]).name.startswith('20261004_090000_')
    data = PhotoMetaDataXMP().read(result.destinations[0])
    assert data['XMP-tiff:Model'] == '新相机'
    assert data.get('camera_model') != '旧相机'
    assert data.get('date_time_original') != '2000:01:01 01:01:01'


@pytest.mark.parametrize(('extension', 'category'), [
    ('.ARW', 'RAW'), ('.nEf', 'RAW'), ('.HIF', 'RAW'), ('.heif', 'RAW'), ('.HEIC', 'RAW'),
    ('.PSD', 'PSD'), ('.png', 'Export'), ('.jpg', 'Export'), ('.JPEG', 'Export'),
    ('.tif', ''), ('.webp', ''),
])
def test_format_routing_preserves_photo_and_chinese_sidecar(tmp_path, extension, category):
    source = photo(tmp_path / 'source', '鸟片' + extension)
    before = source.read_bytes()
    result, = run([source], tmp_path / 'archive', date=False)
    assert result.status == 'success', result.message
    destination = Path(result.destinations[0])
    assert destination == tmp_path / 'archive/白鹭' / category / source.name
    assert destination.read_bytes() == before and not source.exists()
    assert PhotoMetaDataXMP().read(str(destination))['Title'] == '白鹭'
    assert PhotoMetaDataXMP().read_subjects(str(destination)) == ['中国鸟类', '珍藏']


@pytest.mark.parametrize('mode', ['move', 'copy'])
def test_raw_hif_psd_and_exports_split_with_one_xmp_per_directory(tmp_path, mode):
    sources = [photo(tmp_path / 'source', '鸟片' + ext) for ext in ('.ARW', '.HIF', '.PSD', '.PNG', '.JPG')]
    before = {p: p.read_bytes() for p in sources}
    sidecar = sources[0].with_suffix('.xmp')
    xmp_before = sidecar.read_bytes()
    result, = run(sources, tmp_path / 'archive', mode, False)
    assert result.status == 'success', result.message
    bird = tmp_path / 'archive/白鹭'
    assert set(p.name for p in bird.iterdir()) == {'RAW', 'PSD', 'Export'}
    assert len(list(bird.rglob('*.xmp'))) == 3
    for source, destination in zip(sources, map(Path, result.destinations)):
        assert destination.read_bytes() == before[source]
        assert destination.with_suffix('.xmp').read_bytes() == xmp_before
        assert PhotoMetaDataXMP().read(str(destination))['Title'] == '白鹭'
        assert source.exists() == (mode == 'copy')
    assert sidecar.exists() == (mode == 'copy')


@pytest.mark.parametrize('category', ['RAW', 'PSD', 'Export'])
def test_collision_in_any_destination_renames_whole_group(tmp_path, category):
    sources = [photo(tmp_path / 'source', 'DSC01234' + ext) for ext in ('.ARW', '.PSD', '.JPG')]
    folder = tmp_path / 'archive/白鹭' / category
    folder.mkdir(parents=True)
    conflict = folder / 'dsc01234.XMP'
    conflict.write_bytes(b'keep existing metadata')
    result, = run(sources, tmp_path / 'archive', date=False)
    assert result.status == 'success', result.message
    assert {Path(p).stem for p in result.destinations} == {'DSC01234_002'}
    assert conflict.read_bytes() == b'keep existing metadata'
    assert all(Path(p).with_suffix('.xmp').is_file() for p in result.destinations)


@pytest.mark.parametrize('mode', ['move', 'copy'])
def test_split_sidecar_failure_rolls_back_all_format_directories(tmp_path, monkeypatch, mode):
    from app_common import file_transactions as tx
    sources = [photo(tmp_path / 'source', '鸟片' + ext) for ext in ('.ARW', '.PSD', '.JPG')]
    sidecar = sources[0].with_suffix('.xmp')
    originals = {p: p.read_bytes() for p in [*sources, sidecar]}
    publish = tx.publish_without_overwrite
    def fail_last_xmp(src, dest):
        if Path(dest).parent.name == 'Export' and Path(dest).suffix == '.xmp':
            raise OSError('Export 侧车发布失败')
        publish(src, dest)
    monkeypatch.setattr(tx, 'publish_without_overwrite', fail_last_xmp)
    result, = run(sources, tmp_path / 'archive', mode)
    assert result.status == 'failed' and 'Export 侧车发布失败' in result.message
    assert all(p.read_bytes() == content for p, content in originals.items())
    assert not [p for p in (tmp_path / 'archive').rglob('*') if p.is_file()]


def test_split_selected_formats_keeps_sidecar_for_unselected_source(tmp_path):
    raw = photo(tmp_path / 'source', 'bird.ARW')
    psd = photo(raw.parent, 'bird.PSD')
    jpeg = photo(raw.parent, 'bird.JPG')
    result, = run([raw, psd], tmp_path / 'archive')
    assert result.status == 'success'
    assert jpeg.exists() and not raw.exists() and not psd.exists()
    assert PhotoMetaDataXMP().read(str(jpeg))['Title'] == '白鹭'
    assert all(PhotoMetaDataXMP().read(p)['Title'] == '白鹭' for p in result.destinations)


def test_legacy_flat_archive_can_be_sorted_without_duplicating_date_prefix(tmp_path):
    target = tmp_path / 'archive'
    source = photo(target / '白鹭', '20261005_083015_DSC01234.jpg')
    result, = run([source], target)
    assert result.status == 'success', result.message
    assert Path(result.destinations[0]) == source.parent / 'Export' / source.name
    assert not source.exists() and not source.with_suffix('.xmp').exists()


def test_existing_lowercase_format_directory_is_reused(tmp_path):
    source = photo(tmp_path / 'source', 'bird.ARW')
    existing = tmp_path / 'archive/白鹭/raw'
    existing.mkdir(parents=True)
    result, = run([source], tmp_path / 'archive')
    assert result.status == 'success'
    assert Path(result.destinations[0]).parent == existing
    assert [p.name for p in existing.parent.iterdir()] == ['raw']


@pytest.mark.parametrize('kind', ['file', 'symlink'])
def test_invalid_format_directory_keeps_source_bundle(tmp_path, kind):
    source = photo(tmp_path / 'source')
    bird = tmp_path / 'archive/白鹭'
    bird.mkdir(parents=True)
    if kind == 'file':
        (bird / 'Export').write_bytes(b'keep')
    else:
        outside = tmp_path / 'outside'
        outside.mkdir()
        (bird / 'Export').symlink_to(outside, target_is_directory=True)
    result, = run([source], bird.parent)
    assert result.status == 'failed'
    assert source.exists() and source.with_suffix('.xmp').exists()
