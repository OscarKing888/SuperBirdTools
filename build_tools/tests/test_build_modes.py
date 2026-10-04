"""执行真实构建入口，以轻量外部命令替身验证本地/发布流程及失败退出。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
STUB = '''\
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
stage = Path(__file__).stem
if stage == "__main__":
    spec = next(Path(arg).stem for arg in args if arg.endswith(".spec"))
    stage = "merged" if spec == "build_all_win_merged" else "SuperBirdUpdater"
elif stage == "build_stub":
    stage, *args = args
with open(os.environ["SBT_BUILD_PROBE"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"stage": stage, "args": args}) + "\\n")
if os.environ.get("SBT_FAIL_STAGE") == stage:
    sys.exit(73)
dist = Path(os.environ["SUPERBIRDTOOLS_DIST_ROOT"])
apps = ("SuperViewer", "SuperBirdStamp") if stage == "merged" else (stage,)
for app in apps:
    if not app.startswith("Super"):
        continue
    executable = (dist / app / (app + ".exe") if os.name == "nt" else
                  dist / (app + ".app") / "Contents" / "MacOS" / app)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("built", encoding="utf-8")
if stage == "SuperBirdStamp" and os.environ.get("BIRDSTAMP_CREATE_ZIP") == "1":
    (dist / "SuperBirdStamp-mac.zip").write_text("zip", encoding="utf-8")
if stage == "generate_update_manifest":
    (dist / "updates").mkdir(exist_ok=True)
    (dist / "updates" / "release.zip").write_text("new release", encoding="utf-8")
    (dist / "installed-update.json").write_text("new manifest", encoding="utf-8")
'''


@pytest.fixture
def build_entry(tmp_path):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    script_name = "build_all.bat" if os.name == "nt" else "build_all.sh"
    script = repo / script_name
    shutil.copy2(REPO_ROOT / script_name, script)
    for name in ("set_build_version", "download_denoise_model", "hardlink_dedupe",
                 "generate_update_manifest", "build_stub"):
        stub = repo / "build_tools" / f"{name}.py"
        stub.parent.mkdir(exist_ok=True)
        stub.write_text(STUB, encoding="utf-8")
    module = repo / "stubs" / "PyInstaller"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "__main__.py").write_text(STUB, encoding="utf-8")
    for app in ("SuperViewer", "SuperBirdStamp"):
        child = repo / app / "scripts_dev" / "build_mac.sh"
        child.parent.mkdir(parents=True)
        child.write_text(
            f'#!/bin/bash\nexec "$PYTHON_BIN" "$SBT_BUILD_STUB" {app} "$@"\n',
            encoding="utf-8",
        )
    env = os.environ.copy()
    env.update(
        PYTHON_BIN=sys.executable, PYTHON_EXE=sys.executable,
        PYTHONPATH=str(module.parent),
        SUPERBIRDTOOLS_DIST_ROOT=str(repo / "dist"),
        SUPERBIRDTOOLS_BUILD_ROOT=str(repo / "build"),
        SBT_BUILD_PROBE=str(repo / "calls.jsonl"),
        SBT_BUILD_STUB=str(repo / "build_tools" / "build_stub.py"),
        BIRDSTAMP_CREATE_ZIP="1",
    )
    env.pop("SBT_FAIL_STAGE", None)

    def run(*args, fail=""):
        env["SBT_FAIL_STAGE"] = fail
        command = (["cmd", "/d", "/c", script.name] if os.name == "nt" else ["bash", str(script)])
        result = subprocess.run(command + list(args), cwd=repo, env=env,
                                capture_output=True, text=True, timeout=30)
        probe = repo / "calls.jsonl"
        calls = [json.loads(line) for line in probe.read_text(encoding="utf-8").splitlines()] if probe.exists() else []
        return result, calls

    return repo, run


@pytest.mark.parametrize("apps_only", [False, True])
@pytest.mark.parametrize("clean", [False, True])
def test_build_modes_keep_apps_and_control_release_artifacts(build_entry, apps_only, clean):
    repo, run = build_entry
    dist = repo / "dist"
    (dist / "updates").mkdir(parents=True)
    (dist / "updates" / "release.zip").write_text("old release", encoding="utf-8")
    (dist / "installed-update.json").write_text("old manifest", encoding="utf-8")
    args = (["--apps-only"] if apps_only else []) + (["--clean"] if clean else [])
    result, calls = run(*args)
    assert result.returncode == 0, result.stdout + result.stderr
    for app in ("SuperViewer", "SuperBirdStamp", "SuperBirdUpdater"):
        executable = (dist / app / f"{app}.exe" if os.name == "nt" else
                      dist / f"{app}.app" / "Contents" / "MacOS" / app)
        assert executable.read_text(encoding="utf-8") == "built"
    stages = [call["stage"] for call in calls]
    assert ("generate_update_manifest" in stages) == (not apps_only)
    if os.name != "nt":
        assert "hardlink_dedupe" in stages
        assert (dist / "SuperBirdStamp-mac.zip").exists() == (not apps_only)
    if apps_only and clean:
        assert not (dist / "updates").exists()
        assert not (dist / "installed-update.json").exists()
    else:
        prefix = "old" if apps_only else "new"
        assert (dist / "updates" / "release.zip").read_text() == f"{prefix} release"
        assert (dist / "installed-update.json").read_text() == f"{prefix} manifest"
    summary = result.stdout.split("[OK] outputs:")[1]
    assert (str(dist / "updates") in summary) == (not apps_only)


@pytest.mark.parametrize("apps_only", [False, True])
@pytest.mark.parametrize("stage", ["primary", "SuperBirdUpdater"])
def test_build_failure_stops_before_release_and_success_message(build_entry, apps_only, stage):
    _, run = build_entry
    if stage == "primary":
        stage = "merged" if os.name == "nt" else "SuperViewer"
    result, calls = run(*(["--apps-only"] if apps_only else []), fail=stage)
    assert result.returncode != 0
    assert calls[-1]["stage"] == stage
    assert "[OK] outputs:" not in result.stdout


def test_release_generation_failure_is_not_reported_as_success(build_entry):
    _, run = build_entry
    result, calls = run(fail="generate_update_manifest")
    assert result.returncode != 0
    assert calls[-1]["stage"] == "generate_update_manifest"
    assert "[OK] outputs:" not in result.stdout
