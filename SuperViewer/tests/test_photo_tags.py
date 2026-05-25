from __future__ import annotations

import os
from pathlib import Path

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.writer import read_batch_metadata
from SuperViewer.superviewer.photo_tags import (
    PhotoTagConfig,
    PhotoTagSidecarStore,
    find_superpicky_tag_config_path,
)


def test_photo_tag_config_loads_utf8_lines_and_dedupes(tmp_path: Path) -> None:
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("窝\n打架\n\n打架\n捕食\n", encoding="utf-8")

    assert PhotoTagConfig(cfg).load() == ["窝", "打架", "捕食"]


def test_photo_tag_config_path_comes_from_nearest_superpicky(tmp_path: Path) -> None:
    root = tmp_path / "root"
    leaf = root / "nested" / "leaf"
    superpicky = root / ".superpicky"
    leaf.mkdir(parents=True)
    superpicky.mkdir()
    (superpicky / "tags.cfg").write_text("alpha\nbeta\n", encoding="utf-8")

    assert find_superpicky_tag_config_path(leaf) == superpicky / "tags.cfg"
    assert PhotoTagConfig(find_superpicky_tag_config_path(leaf)).load() == ["alpha", "beta"]

    nested_superpicky = leaf / ".superpicky"
    nested_superpicky.mkdir()
    assert find_superpicky_tag_config_path(leaf) == nested_superpicky / "tags.cfg"
    assert find_superpicky_tag_config_path(nested_superpicky) == nested_superpicky / "tags.cfg"
    outside = tmp_path / "outside"
    outside.mkdir()
    assert find_superpicky_tag_config_path(outside, max_levels=0) is None


def test_xmp_subject_roundtrip_preserves_multiple_tags(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()

    assert metadata.write_subjects(str(photo_path), ["打架", "捕食", "打架"])
    assert metadata.read_subjects(str(photo_path)) == ["打架", "捕食"]
    assert (tmp_path / "img001.xmp").is_file()

    flat = metadata.read(str(photo_path))
    assert flat.get("XMP-dc:subject") == "打架; 捕食"
    assert flat.get("XMP-dc:Subject") == "打架; 捕食"


def test_xmp_write_accepts_subject_field_alias(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo_path), {"XMP-dc:Subject": "打架; 捕食"})
    assert metadata.read_subjects(str(photo_path)) == ["打架", "捕食"]


def test_xmp_rating_pick_read_as_normalized_fields(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo_path), {"XMP-xmp:Rating": 3, "XMP-xmpDM:pick": 1})
    flat = metadata.read(str(photo_path))

    assert flat.get("XMP-xmp:Rating") == "3"
    assert flat.get("rating") == 3
    assert flat.get("XMP-xmpDM:pick") == "1"
    assert flat.get("pick") == 1


def test_xmp_write_invalidates_batch_metadata_cache(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    norm_path = os.path.normpath(str(photo_path))

    metadata = PhotoMetaDataXMP()
    before = read_batch_metadata([str(photo_path)]).get(norm_path, {})
    assert before.get("XMP-xmp:Rating") in (None, "")

    assert metadata.write(str(photo_path), {"XMP-xmp:Rating": 4})
    after = read_batch_metadata([str(photo_path)]).get(norm_path, {})

    assert after.get("XMP-xmp:Rating") == "4"
    assert after.get("rating") == 4


def test_store_persists_multiple_tags_per_photo_in_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    store = PhotoTagSidecarStore()
    store.set_tag_for_paths([str(photo_path)], "打架", True)
    store.set_tag_for_paths([str(photo_path)], "捕食", True)
    assert store.get_tags(str(photo_path)) == {"打架", "捕食"}

    store.set_tag_for_paths([str(photo_path)], "打架", False)
    assert store.get_tags(str(photo_path)) == {"捕食"}


def test_store_loads_and_filters_configured_tags(tmp_path: Path) -> None:
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    p1.write_bytes(b"")
    p2.write_bytes(b"")

    store = PhotoTagSidecarStore()
    store.set_tag_for_paths([str(p1), str(p2)], "同框", True)
    store.set_tag_for_paths([str(p2)], "踩背", True)

    tags = store.load_tags_for_paths([str(p1), str(p2)], allowed_tags=["同框"])
    assert tags[str(p1)] == {"同框"}
    assert tags[str(p2)] == {"同框"}


def test_clear_configured_tags_preserves_unrelated_xmp_subjects(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()
    assert metadata.write_subjects(str(photo_path), ["Lightroom", "打架", "捕食"])

    store = PhotoTagSidecarStore(metadata)
    store.clear_tags_for_paths([str(photo_path)], allowed_tags=["打架", "捕食"])

    assert metadata.read_subjects(str(photo_path)) == ["Lightroom"]
