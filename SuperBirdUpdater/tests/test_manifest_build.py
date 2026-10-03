from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import zipfile

import pytest

from SuperBirdUpdater.build import generate, package_suite
from SuperBirdUpdater.common import CONFIG_NAME, UpdateError, hash_file
from SuperBirdUpdater.manifest import load, newer, validate
from SuperBirdUpdater.tests.conftest import identity


def test_manifest_is_deterministic_and_hashes_real_bytes(versions, tmp_path):
    _, root, _, _, candidate = versions
    again = generate(root, tmp_path / "again", identity(2), "linux", "x86_64", workers=4)
    assert candidate == again
    for entry in candidate["files"]:
        if entry["kind"] == "file":
            actual = hash_file(root / entry["path"])
            assert all(actual[k] == entry[k] for k in actual)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "SuperViewer/a/../../escape", "SuperViewer/a\\b",
                                 "SuperViewer/A:stream", "SuperViewer/CON.txt", "SuperViewer/trailing."])
def test_rejects_unsafe_paths(versions, path):
    candidate = copy.deepcopy(versions[4])
    candidate["files"][-1]["path"] = path
    with pytest.raises(UpdateError):
        validate(candidate)


def test_rejects_case_collisions_and_invalid_ranges(versions):
    candidate = copy.deepcopy(versions[4])
    entry = copy.deepcopy(next(e for e in candidate["files"] if e["kind"] == "file"))
    entry["path"] = entry["path"].upper()
    candidate["files"].append(entry)
    with pytest.raises(UpdateError):
        validate(candidate)
    candidate = copy.deepcopy(versions[4])
    next(e for e in candidate["files"] if e["kind"] == "file")["offset"] = 2**62
    with pytest.raises(UpdateError):
        validate(candidate)


def test_order_is_numeric_not_hash_sorting(versions):
    current, candidate = versions[3:]
    assert newer(current, candidate)
    assert not newer(candidate, current)
    assert not newer(current, current)
    same_order = {**candidate, "revision": current["revision"]}
    with pytest.raises(UpdateError, match="顺序相同"):
        newer(current, same_order)
    with pytest.raises(UpdateError, match="架构"):
        newer(current, {**candidate, "arch": "arm64"})


@pytest.mark.skipif(os.name == "nt", reason="requires unprivileged symlinks")
def test_links_modes_and_package(versions, tmp_path):
    _, root, _, _, _ = versions
    file = root / "SuperViewer/program.bin"
    file.chmod(0o755)
    (root / "SuperViewer/current").symlink_to("program.bin")
    config = root / CONFIG_NAME
    config.write_text("{}", encoding="utf-8")
    (root / "SuperBirdStamp/editor_autosave.birdstamp-workspace.json").write_text("private")
    manifest = generate(root, tmp_path / "links", identity(2), "linux", "x86_64")
    assert not any("autosave" in e["path"] for e in manifest["files"])
    package = package_suite(root, tmp_path / "packages", manifest, tmp_path)
    with zipfile.ZipFile(package) as archive:
        prefix = "SuperBirdTools-22222222-linux-x86_64/"
        info = archive.getinfo(prefix + "SuperViewer/current")
        assert info.external_attr >> 16 & 0o170000 == 0o120000
        assert archive.read(info) == b"program.bin"
        assert archive.getinfo(prefix + "SuperViewer/program.bin").external_attr >> 16 & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="requires symlinks")
def test_link_escape_and_cycle_fail_build(versions, tmp_path):
    root = versions[1]
    (root / "SuperViewer/escape").symlink_to("../../outside")
    with pytest.raises(UpdateError):
        generate(root, tmp_path / "bad", identity(2), "linux", "x86_64")
    (root / "SuperViewer/escape").unlink()
    (root / "SuperViewer/a").symlink_to("b")
    (root / "SuperViewer/b").symlink_to("a")
    with pytest.raises(UpdateError, match="环"):
        generate(root, tmp_path / "cycle", identity(2), "linux", "x86_64")


def test_volume_split_still_reads_each_file(versions, tmp_path):
    root = versions[1]
    manifest = generate(root, tmp_path / "volumes", identity(2), "linux", "x86_64", volume_size=4096)
    assert len(manifest["assets"]) > 1
    assert load(tmp_path / "volumes/update-linux-x86_64.json") == manifest


@pytest.mark.parametrize("version", ["1.2.deadbeef", "1.2.2222222", "1.2.22222222/unsafe", "1.2.3", None])
def test_release_version_must_match_the_manifest_commit(versions, version):
    candidate = {**versions[4], "release_version": version}
    with pytest.raises(UpdateError, match="发行版本号"):
        validate(candidate)


def test_hash_release_keeps_old_manifest_compatibility_and_names_package(versions, tmp_path):
    root = versions[1]
    info = {**identity(2), "release_version": "1.2.22222222"}
    manifest = generate(root, tmp_path / "release", info, "linux", "x86_64", workers=2)
    assert manifest["version"] == "22222222"
    assert manifest["commit"].startswith(manifest["version"])
    assert newer(versions[3], manifest)
    (root / CONFIG_NAME).write_text("{}", encoding="utf-8")
    package = package_suite(root, tmp_path / "package", manifest, tmp_path)
    assert package.name == "SuperBirdTools-1.2.22222222-linux-x86_64.zip"
    with zipfile.ZipFile(package) as archive:
        assert all(name.startswith("SuperBirdTools-1.2.22222222-linux-x86_64/") for name in archive.namelist())


def test_release_validation_checks_full_package_names_and_matching_prefixes(tmp_path):
    from SuperBirdUpdater.common import APPS, atomic_json, executable
    from build_tools.validate_update_release import validate_release

    release = tmp_path / "publish"
    release.mkdir()
    for platform, arch in (("windows", "x86_64"), ("macos", "arm64")):
        root = tmp_path / platform
        for app in APPS:
            entry = executable(root, app, platform)
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_bytes(b"test executable")
        (root / CONFIG_NAME).write_text("{}", encoding="utf-8")
        info = {**identity(2), "release_version": "1.2.22222222"}
        manifest = generate(root, release, info, platform, arch, workers=1)
        package_suite(root, release, manifest, tmp_path)
    validate_release(release)
    path = release / "update-macos-arm64.json"
    wrong = {**load(path), "release_version": "1.3.22222222"}
    atomic_json(path, wrong)
    with pytest.raises(UpdateError, match="不是同一源码版本"):
        validate_release(release)
