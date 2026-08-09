import struct

from SuperViewer.superviewer import focus_preview_loader as loader
from SuperViewer.superviewer.qt_compat import QApplication


def _write_raw_16_bit_psd(path, width: int = 4, height: int = 2) -> None:
    header = struct.pack(
        ">4sH6sHIIHH",
        b"8BPS",
        1,
        b"\x00" * 6,
        3,
        height,
        width,
        16,
        3,
    )
    sections = struct.pack(">III", 0, 0, 0)
    pixels = width * height
    planes = b"".join(
        struct.pack(f">{pixels}H", *([value] * pixels))
        for value in (65535, 32768, 0)
    )
    path.write_bytes(header + sections + b"\x00\x00" + planes)


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


def test_canvas_loader_falls_back_to_full_resolution_psd_composite(
    monkeypatch,
    tmp_path,
) -> None:
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "16-bit.psd"
    _write_raw_16_bit_psd(path)
    decode_calls = []
    original_decoder = loader.load_psd_composite_rgb
    monkeypatch.setattr(loader, "_load_standard_pixmap_qt", lambda _path: None)
    monkeypatch.setattr(loader, "_load_preview_pixmap_with_orientation", lambda _path: None)

    def decode_psd(requested_path, max_size):
        decode_calls.append((requested_path, max_size))
        return original_decoder(requested_path, max_size)

    monkeypatch.setattr(loader, "load_psd_composite_rgb", decode_psd)

    pixmap = loader._load_preview_pixmap_for_canvas(str(path))

    assert pixmap is not None and not pixmap.isNull()
    assert (pixmap.width(), pixmap.height()) == (4, 2)
    assert decode_calls == [(str(path), None)]
    assert app is QApplication.instance()


def test_canvas_loader_never_calls_psd_decoder_for_raw(monkeypatch, tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "sample.arw"
    path.write_bytes(b"raw-placeholder")
    monkeypatch.setattr(loader, "get_raw_preview_jpeg", lambda _path: None)
    monkeypatch.setattr(loader, "_load_preview_pixmap_with_orientation", lambda _path: None)
    monkeypatch.setattr(loader, "_load_raw_full_as_pixmap", lambda _path: None)
    monkeypatch.setattr(loader, "get_raw_thumbnail", lambda _path: None)
    monkeypatch.setattr(
        loader,
        "load_psd_composite_rgb",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("RAW 预览不应调用 PSD 解码器")
        ),
    )

    pixmap = loader._load_preview_pixmap_for_canvas(str(path))

    assert pixmap is None or pixmap.isNull()
    assert app is QApplication.instance()
