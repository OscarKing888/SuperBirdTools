from __future__ import annotations

from pathlib import Path

from app_identity import load_app_identity

import pytest

from build_tools.set_build_version import apply_build_version, normalize_version


def _write_version_fixture(repo_root: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "app_metadata.json"
    (repo_root / "app_metadata.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


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

    assert changed == (tmp_path / "app_metadata.json",)
    for app_id in ("SuperViewer", "SuperBirdStamp"):
        identity = load_app_identity(app_id, changed[0])
        assert identity.version == "2.4.0-rc.2"
        assert identity.bundle_version == "2.4.0"
        assert identity.build_number == "37"
        assert "2.4.0-rc.2" in identity.window_title()


def test_invalid_build_number_leaves_config_untouched(tmp_path):
    _write_version_fixture(tmp_path)
    path = tmp_path / "app_metadata.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        apply_build_version(tmp_path, "1.2.3", build_number="bad")
    assert path.read_bytes() == before
