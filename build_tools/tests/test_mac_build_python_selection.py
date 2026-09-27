from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="macOS bash build scripts")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_python(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '#!/bin/bash\n'
        'printf "%s\\n" "$0" >> "$SBT_PYTHON_PROBE"\n'
        '# Stop at the actual build, before any outputs or GUI smoke tests.\n'
        'if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then exit 73; fi\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _env(probe: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("PYTHON_BIN", "VIRTUAL_ENV", "SUPERBIRDTOOLS_DIST_ROOT", "SUPERBIRDTOOLS_BUILD_ROOT"):
        env.pop(key, None)
    env["SBT_PYTHON_PROBE"] = str(probe)
    return env


@pytest.mark.parametrize("selection", ["repo", "override", "standalone", "activated"])
def test_birdstamp_selects_one_environment_for_checks_and_build(tmp_path: Path, selection: str) -> None:
    repo = tmp_path / "repo with spaces"
    script = repo / "SuperBirdStamp" / "scripts_dev" / "build_mac.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "SuperBirdStamp" / "scripts_dev" / "build_mac.sh", script)
    probe = tmp_path / "python-calls.txt"
    env = _env(probe)
    activated = tmp_path / "activated"
    expected = _fake_python(activated / "bin" / "python3")
    env["VIRTUAL_ENV"] = str(activated)
    if selection != "activated":
        expected = _fake_python(repo / "SuperBirdStamp" / ".venv" / "bin" / "python3")
    if selection in {"repo", "override"}:
        expected = _fake_python(repo / ".venv" / "bin" / "python3")
    if selection == "override":
        expected = _fake_python(tmp_path / "explicit python")
        env["PYTHON_BIN"] = str(expected)

    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=10)

    assert result.returncode == 73, result.stdout + result.stderr
    assert probe.read_text(encoding="utf-8").splitlines() == [str(expected), str(expected)]


@pytest.mark.parametrize("override", [False, True])
def test_aggregate_passes_selected_python_to_both_builders(tmp_path: Path, override: bool) -> None:
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    shutil.copy2(REPO_ROOT / "build_all.sh", repo / "build_all.sh")
    expected = _fake_python(repo / ".venv" / "bin" / "python3")
    probe = tmp_path / "python-calls.txt"
    env = _env(probe)
    if override:
        expected = _fake_python(tmp_path / "explicit python")
        env["PYTHON_BIN"] = str(expected)
    for app in ("SuperViewer", "SuperBirdStamp"):
        script = repo / app / "scripts_dev" / "build_mac.sh"
        script.parent.mkdir(parents=True)
        script.write_text(
            '#!/bin/bash\nset -eu\nprintf "%s\\n" "$PYTHON_BIN" >> "$SBT_PYTHON_PROBE"\n',
            encoding="utf-8",
        )

    result = subprocess.run(
        ["bash", str(repo / "build_all.sh"), "--skip-dedupe"],
        env=env, capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.read_text(encoding="utf-8").splitlines() == [str(expected), str(expected)]
