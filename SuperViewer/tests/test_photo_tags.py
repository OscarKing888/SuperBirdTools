from __future__ import annotations

import os
from pathlib import Path

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.writer import read_batch_metadata
from SuperViewer.superviewer.photo_tags import (
    PhotoTagConfig,
    PhotoTagSidecarStore,
    find_superpicky_tag_config_path,
    photo_tag_filter_matches,
)


def test_photo_tag_config_loads_utf8_lines_and_dedupes(tmp_path: Path) -> None:
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("nest\nfight\n\nfight\nfeeding\n", encoding="utf-8")

    assert PhotoTagConfig(cfg).load() == ["nest", "fight", "feeding"]


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


def test_photo_tag_filter_exact_and_partial_matches() -> None:
    assert photo_tag_filter_matches(["bird"], ["bird"])
    assert not photo_tag_filter_matches(["bird"], ["waterbird"])
    assert photo_tag_filter_matches(["bird"], ["waterbird"], partial_match=True)
    assert photo_tag_filter_matches(["tag1", "tag2"], ["tag1", "tag2", "tag3"])
    assert not photo_tag_filter_matches(["tag1", "tag2"], ["tag1", "tag3"])


def test_xmp_subject_roundtrip_preserves_multiple_tags(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    metadata = PhotoMetaDataXMP()

    assert metadata.write_subjects(str(photo_path), ["fight", "feeding", "fight"])
    assert metadata.read_subjects(str(photo_path)) == ["fight", "feeding"]
    assert (tmp_path / "img001.xmp").is_file()

    flat = metadata.read(str(photo_path))
    assert flat.get("XMP-dc:subject") == "fight; feeding"
    assert flat.get("XMP-dc:Subject") == "fight; feeding"


def test_xmp_write_accepts_subject_field_alias(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo_path), {"XMP-dc:Subject": "fight; feeding"})
    assert metadata.read_subjects(str(photo_path)) == ["fight", "feeding"]


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


def test_store_persists_multiple_tags_per_photo_in_xmp_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    store = PhotoTagSidecarStore()

    store.set_tag_for_paths([str(photo_path)], "fight", True)
    store.set_tag_for_paths([str(photo_path)], "feeding", True)
    assert store.get_tags(str(photo_path)) == {"fight", "feeding"}

    store.set_tag_for_paths([str(photo_path)], "fight", False)
    assert store.get_tags(str(photo_path)) == {"feeding"}
    assert (tmp_path / "img001.xmp").is_file()


def test_store_uses_sibling_xmp_even_under_superpicky_root(tmp_path: Path) -> None:
    root = tmp_path / "library"
    photo_dir = root / "day1"
    photo_dir.mkdir(parents=True)
    (root / ".superpicky").mkdir()
    photo_path = photo_dir / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    store = PhotoTagSidecarStore()
    store.set_tag_for_paths([str(photo_path)], "alpha", True)

    assert (photo_dir / "img001.xmp").is_file()
    assert not (root / ".superpicky" / "metadata").exists()
    assert store.get_tags(str(photo_path)) == {"alpha"}


def test_store_loads_and_filters_configured_tags(tmp_path: Path) -> None:
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    p1.write_bytes(b"")
    p2.write_bytes(b"")
    store = PhotoTagSidecarStore()

    store.set_tag_for_paths([str(p1), str(p2)], "same-frame", True)
    store.set_tag_for_paths([str(p2)], "mating", True)

    tags = store.load_tags_for_paths([str(p1), str(p2)], allowed_tags=["same-frame"])
    assert tags[str(p1)] == {"same-frame"}
    assert tags[str(p2)] == {"same-frame"}


def test_clear_configured_tags_preserves_unrelated_xmp_subjects(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    metadata = PhotoMetaDataXMP()
    assert metadata.write_subjects(str(photo_path), ["Lightroom", "fight", "feeding"])

    store = PhotoTagSidecarStore(metadata)
    store.clear_tags_for_paths([str(photo_path)], allowed_tags=["fight", "feeding"])

    assert metadata.read_subjects(str(photo_path)) == ["Lightroom"]
