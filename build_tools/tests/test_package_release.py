from pathlib import Path
import stat
import zipfile

import pytest

from build_tools.package_release import RELEASE_TARGETS, validate_packages
from SuperBirdUpdater.build import package_full_suite
from SuperBirdUpdater.common import APPS, UpdateError, executable

VERSION = "1.0.12345678"


def make_suite(root, target):
    for app in APPS:
        path = executable(root, app, target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"application bytes")
        path.chmod(0o755)


@pytest.mark.parametrize("target,arch", RELEASE_TARGETS)
def test_full_package_without_update_metadata(tmp_path, target, arch):
    root, output = tmp_path / "dist", tmp_path / "release"
    make_suite(root, target)
    private = executable(root, "SuperBirdStamp", target).parent / "editor_export_state.json"
    private.write_text("private paths")
    (root / "installed-update.json").write_text("stale metadata")
    (root / "update_config.json").write_text("stale config")
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("license notices")
    package = package_full_suite(root, output, VERSION, target, arch, tmp_path)
    assert list(output.iterdir()) == [package]
    assert not (root / "updates").exists()
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert not any("update" in name or "editor_export_state" in name for name in names)
        for app in APPS:
            name = executable(Path(package.stem), app, target).as_posix()
            assert archive.read(name) == b"application bytes"
        assert archive.read(f"{package.stem}/THIRD_PARTY_NOTICES.md") == b"license notices"


def test_full_package_preserves_macos_links_and_permissions(tmp_path):
    root = tmp_path / "dist"
    make_suite(root, "macos")
    binary = executable(root, "SuperViewer", "macos")
    link = binary.parent / "current"
    try:
        link.symlink_to(binary.name)
    except OSError:
        pytest.skip("Symlinks unavailable")
    package = package_full_suite(root, tmp_path / "release", VERSION, "macos", "arm64", tmp_path)
    with zipfile.ZipFile(package) as archive:
        name = f"{package.stem}/{link.relative_to(root).as_posix()}"
        assert stat.S_ISLNK(archive.getinfo(name).external_attr >> 16)
        assert archive.read(name) == b"SuperViewer"
        name = f"{package.stem}/{binary.relative_to(root).as_posix()}"
        assert archive.getinfo(name).external_attr >> 16 & 0o777 == stat.S_IMODE(binary.stat().st_mode)


def test_missing_app_fails_before_packaging(tmp_path):
    with pytest.raises(UpdateError, match="Missing packaged executable"):
        package_full_suite(tmp_path, tmp_path / "release", VERSION, "windows", "x86_64", tmp_path)
    assert not (tmp_path / "release").exists()


def test_release_requires_both_platforms_and_rejects_update_assets(tmp_path):
    output = tmp_path / "release"
    for target, arch in RELEASE_TARGETS:
        root = tmp_path / target
        make_suite(root, target)
        package_full_suite(root, output, VERSION, target, arch, tmp_path)
    validate_packages(output, VERSION)
    with pytest.raises(UpdateError, match="mismatch"):
        validate_packages(output, "1.0.87654321")
    extra = output / "update-windows-x86_64.json"
    extra.write_text("{}")
    with pytest.raises(UpdateError, match="extra"):
        validate_packages(output, VERSION)
    extra.unlink()
    next(output.glob("*windows*.zip")).unlink()
    with pytest.raises(UpdateError, match="missing"):
        validate_packages(output, VERSION)


def test_release_rejects_archive_without_executables(tmp_path):
    for target, arch in RELEASE_TARGETS:
        with zipfile.ZipFile(tmp_path / f"SuperBirdTools-{VERSION}-{target}-{arch}.zip", "w") as archive:
            archive.writestr("wrong-package.txt", "invalid")
    with pytest.raises(UpdateError, match="Missing executable"):
        validate_packages(tmp_path, VERSION)
