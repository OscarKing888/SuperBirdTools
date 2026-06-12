from SuperViewer.superviewer import focus_preview_loader as loader


def test_raw_focus_metadata_prefers_embedded_reader_without_exiftool(monkeypatch, tmp_path) -> None:
    path = tmp_path / "sample.ARW"
    path.write_bytes(b"raw")

    monkeypatch.setattr(
        loader,
        "read_raw_embedded_focus_metadata",
        lambda _path: {"SourceFile": str(path), "Make": "SONY", "MakerNote Tag 0x2027": "1 1 0.5 0.5"},
    )
    monkeypatch.setattr(
        loader,
        "_run_exiftool_json_for_focus",
        lambda _path: (_ for _ in ()).throw(AssertionError("RAW focus should not call exiftool")),
    )
    monkeypatch.setattr(
        loader,
        "extract_metadata_with_xmp_priority",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("RAW focus should not call primary exiftool path")),
    )

    metadata = loader._load_focus_metadata_for_path(str(path))

    assert metadata["Make"] == "SONY"
    assert metadata["MakerNote Tag 0x2027"] == "1 1 0.5 0.5"


def test_raw_focus_metadata_falls_back_to_exifread_without_exiftool(monkeypatch, tmp_path) -> None:
    path = tmp_path / "sample.ARW"
    path.write_bytes(b"raw")

    monkeypatch.setattr(loader, "read_raw_embedded_focus_metadata", lambda _path: {})
    monkeypatch.setattr(loader, "_load_exifread_metadata_for_focus", lambda _path: {"Model": "ILCE-1M2"})
    monkeypatch.setattr(
        loader,
        "_run_exiftool_json_for_focus",
        lambda _path: (_ for _ in ()).throw(AssertionError("RAW focus fallback should not call exiftool")),
    )
    monkeypatch.setattr(
        loader,
        "extract_metadata_with_xmp_priority",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("RAW focus fallback should not call primary exiftool path")),
    )

    metadata = loader._load_focus_metadata_for_path(str(path))

    assert metadata["Model"] == "ILCE-1M2"
