from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app_identity import load_app_identity

import pytest

from build_tools.set_build_version import (apply_build_version, normalize_version,
                                           prepare_build_metadata, resolve_build_version)


def _write_version_fixture(repo_root: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "app_metadata.json"
    (repo_root / "app_metadata.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.3", "1.2.3"),
        ("v1.2.3", "1.2.3"),
        ("v1.2.0abc1234", "1.2.0abc1234"),
        ("1.2.01234567", "1.2.01234567"),
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


@pytest.fixture
def version_repo(tmp_path):
    root = tmp_path / "版本 repo"
    root.mkdir()
    _write_version_fixture(root)
    data = json.loads((root / "app_metadata.json").read_text(encoding="utf-8"))
    data.update(version="1.2", build_number="19")
    (root / "app_metadata.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for args in (("init", "-q"), ("add", "app_metadata.json"),
                 ("-c", "user.name=Version Test", "-c", "user.email=test@example.invalid",
                  "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={root / 'no-hooks'}",
                  "commit", "-qm", "Fixture")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _head(root):
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def test_build_uses_current_commit_without_touching_source(version_repo, monkeypatch):
    root = version_repo
    monkeypatch.delenv("SUPERBIRDTOOLS_BUILD_VERSION", raising=False)
    monkeypatch.delenv("SUPERBIRDTOOLS_BUILD_NUMBER", raising=False)
    monkeypatch.delenv("SUPERBIRDTOOLS_BUILD_ROOT", raising=False)
    source = root / "app_metadata.json"
    before = source.read_bytes()
    subprocess.run(["git", "config", "core.abbrev", "12"], cwd=root, check=True)
    expected = f"1.2.{_head(root)[:8]}"
    assert load_app_identity("SuperViewer", source).version == expected
    output = prepare_build_metadata(root)
    assert output == root / "build/version/app_metadata.json"
    identity = load_app_identity("SuperViewer", output)
    assert identity.version == expected
    assert identity.build_number == "19"
    assert identity.bundle_version == "1.2.0"
    assert source.read_bytes() == before
    timestamp = output.stat().st_mtime_ns
    prepare_build_metadata(root)
    assert output.stat().st_mtime_ns == timestamp
    subprocess.run(["git", "-c", "user.name=Version Test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={root / 'no-hooks'}",
                    "commit", "--allow-empty", "-qm", "Next commit"], cwd=root, check=True)
    assert load_app_identity("SuperViewer", prepare_build_metadata(root)).version == f"1.2.{_head(root)[:8]}"
    assert source.read_bytes() == before


def test_ci_override_and_tag_require_the_actual_commit(version_repo, monkeypatch):
    root = version_repo
    expected = f"2.3.{_head(root)[:8]}"
    monkeypatch.setenv("SUPERBIRDTOOLS_BUILD_VERSION", expected)
    monkeypatch.setenv("SUPERBIRDTOOLS_BUILD_NUMBER", "42")
    monkeypatch.setenv("SUPERBIRDTOOLS_BUILD_ROOT", "custom build")
    output = prepare_build_metadata(root)
    assert output == root / "custom build/version/app_metadata.json"
    info = load_app_identity("SuperBirdStamp", output)
    assert (info.version, info.build_number) == (expected, "42")
    with pytest.raises(ValueError, match="prefix"):
        resolve_build_version(root, "v" + expected, release_tag=True)
    correct_tag = f"v1.2.{_head(root)[:8]}"
    assert resolve_build_version(root, correct_tag, release_tag=True) == correct_tag[1:]
    before = output.read_bytes()
    for bad in ("1.2", "v1.2", "v1.2.3", "v1.2.deadbeef", "v1.2.3-rc.1", None):
        with pytest.raises(ValueError):
            resolve_build_version(root, bad, release_tag=True)
    monkeypatch.setenv("SUPERBIRDTOOLS_BUILD_VERSION", "1.2.deadbeef")
    with pytest.raises(ValueError, match="current Git commit"):
        prepare_build_metadata(root)
    assert output.read_bytes() == before


def test_cli_check_only_and_build_do_not_write_source(version_repo):
    root = version_repo
    source = root / "app_metadata.json"
    before = source.read_bytes()
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "set_build_version.py"),
               "--repo-root", str(root)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUPERBIRDTOOLS_BUILD_")}
    result = subprocess.run(command + ["3.4", "--check-only"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"3.4.{_head(root)[:8]}"
    assert not (root / "build").exists()
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((root / "build/version/app_metadata.json").read_text())["version"] == f"1.2.{_head(root)[:8]}"
    assert source.read_bytes() == before


def test_missing_git_fails_without_writing_metadata(tmp_path):
    _write_version_fixture(tmp_path)
    before = (tmp_path / "app_metadata.json").read_bytes()
    with pytest.raises(ValueError, match="Git HEAD"):
        prepare_build_metadata(tmp_path, version="1.2")
    assert (tmp_path / "app_metadata.json").read_bytes() == before
