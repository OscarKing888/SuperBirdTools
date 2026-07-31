from __future__ import annotations

from pathlib import Path

import pytest

from build_tools.set_build_version import apply_build_version, normalize_version


def _write_version_fixture(repo_root: Path) -> None:
    viewer_root = repo_root / "SuperViewer"
    bird_root = repo_root / "SuperBirdStamp"
    (bird_root / "birdstamp").mkdir(parents=True)
    (viewer_root / "superviewer").mkdir(parents=True)

    (viewer_root / "superviewer" / "__init__.py").write_text(
        '__version__ = "0.1.0"\n',
        encoding="utf-8",
    )
    (bird_root / "birdstamp" / "__init__.py").write_text(
        '__version__ = "0.1.0"\n',
        encoding="utf-8",
    )
    spec_text = (
        'info_plist={\n'
        '    "CFBundleShortVersionString": "0.1.0",\n'
        '    "CFBundleVersion": "1",\n'
        "}\n"
    )
    (bird_root / "BirdStamp_mac.spec").write_text(spec_text, encoding="utf-8")
    (bird_root / "BirdStamp_mac_console.spec").write_text(
        spec_text,
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.3", "1.2.3"),
        ("v1.2.3", "1.2.3"),
        ("2.0.0-rc.1+build.5", "2.0.0-rc.1+build.5"),
    ],
)
def test_normalize_version_accepts_supported_semver(raw: str, expected: str) -> None:
    assert normalize_version(raw) == expected


@pytest.mark.parametrize(
    "value",
    ["", "1", "1.2", "v1.02.3", "1.2.3-01", "1.2.3/unsafe"],
)
def test_normalize_version_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_version(value)


def test_apply_build_version_updates_all_packaged_version_sources(
    tmp_path: Path,
) -> None:
    _write_version_fixture(tmp_path)

    changed = apply_build_version(
        tmp_path,
        "v2.4.0-rc.2",
        build_number=37,
    )

    assert len(changed) == 4
    assert '__version__ = "2.4.0-rc.2"' in (
        tmp_path / "SuperViewer" / "superviewer" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert '__version__ = "2.4.0-rc.2"' in (
        tmp_path / "SuperBirdStamp" / "birdstamp" / "__init__.py"
    ).read_text(encoding="utf-8")
    for spec_name in ("BirdStamp_mac.spec", "BirdStamp_mac_console.spec"):
        spec_text = (tmp_path / "SuperBirdStamp" / spec_name).read_text(
            encoding="utf-8"
        )
        assert '"CFBundleShortVersionString": "2.4.0"' in spec_text
        assert '"CFBundleVersion": "37"' in spec_text
