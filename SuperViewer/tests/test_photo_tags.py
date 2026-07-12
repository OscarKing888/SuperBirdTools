from __future__ import annotations

import os
from pathlib import Path

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.writer import read_batch_metadata, write_exif_with_exiftool_by_key
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


def test_legacy_exif_writer_routes_title_to_xmp_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    write_exif_with_exiftool_by_key(str(photo_path), "IFD0:XPTitle", "sidecar title")

    metadata = PhotoMetaDataXMP()
    assert (tmp_path / "img001.xmp").is_file()
    assert metadata.read(str(photo_path)).get("XMP-dc:Title") == "sidecar title"


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


def test_store_batch_indexes_sidecars_once(monkeypatch) -> None:
    from SuperViewer.superviewer import photo_tags as photo_tags_module

    paths = [os.path.normpath(f"C:/photos/{name}.jpg") for name in ("a", "b", "c")]
    index_calls: list[list[str]] = []
    parsed_paths: list[str] = []

    def fake_find(all_paths):
        index_calls.append(list(all_paths))
        return {paths[0]: "a.xmp", paths[2]: "c.xmp"}

    def fake_read(_self, xmp_path: str):
        parsed_paths.append(xmp_path)
        if xmp_path == "a.xmp":
            return ["fight", "feeding"]
        return ["flight"]

    monkeypatch.setattr(photo_tags_module, "find_xmp_sidecars", fake_find)
    monkeypatch.setattr(photo_tags_module.PhotoMetaDataXMP, "read_subjects", fake_read)

    tags = PhotoTagSidecarStore().load_tags_for_paths(
        paths,
        allowed_tags=["fight", "feeding", "flight"],
    )

    assert index_calls == [paths]
    assert parsed_paths == ["a.xmp", "c.xmp"]
    assert tags == {
        paths[0]: {"fight", "feeding"},
        paths[1]: set(),
        paths[2]: {"flight"},
    }


def test_store_batch_preserves_semicolons_and_rdf_resource_subjects(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"")
    (tmp_path / "img001.xmp").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:subject><rdf:Bag>
        <rdf:li>行为;特殊</rdf:li>
        <rdf:li rdf:resource="flight" />
      </rdf:Bag></dc:subject>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
""",
        encoding="utf-8",
    )

    tags = PhotoTagSidecarStore().load_tags_for_paths(
        [str(photo_path)],
        allowed_tags=["行为;特殊", "flight"],
    )

    assert tags[str(photo_path)] == {"行为;特殊", "flight"}


def test_clear_configured_tags_preserves_unrelated_xmp_subjects(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    metadata = PhotoMetaDataXMP()
    assert metadata.write_subjects(str(photo_path), ["Lightroom", "fight", "feeding"])

    store = PhotoTagSidecarStore(metadata)
    store.clear_tags_for_paths([str(photo_path)], allowed_tags=["fight", "feeding"])

    assert metadata.read_subjects(str(photo_path)) == ["Lightroom"]
