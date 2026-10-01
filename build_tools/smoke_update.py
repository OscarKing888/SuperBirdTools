"""对 macOS 真实打包套件执行文件级更新与更新器自替换，完全使用临时安装目录。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from SuperBirdUpdater.build import generate
from SuperBirdUpdater.common import APPS, CONFIG_NAME, INSTALLED_MANIFEST, atomic_json, executable
from SuperBirdUpdater.manifest import load
from SuperBirdUpdater.runtime import process_alive, user_state


def smoke(dist: Path) -> None:
    if sys.platform != "darwin":
        raise SystemExit("此烟雾脚本验证 macOS 签名；Windows 实机使用同一 prepare/apply 流程单独验证")
    current = load(dist / INSTALLED_MANIFEST)
    with tempfile.TemporaryDirectory(prefix="sbt-update-smoke-") as temp:
        base = Path(temp)
        installed, candidate = base / "installed", base / "candidate"
        installed.mkdir()
        candidate.mkdir()
        for app in APPS:
            for target in (installed, candidate):
                shutil.copytree(dist / f"{app}.app", target / f"{app}.app", symlinks=True)
            (candidate / f"{app}.app" / "Contents/Resources/update-smoke.txt").write_text("更新烟雾测试", encoding="utf-8")
            subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(candidate / f"{app}.app")], check=True)
        atomic_json(installed / INSTALLED_MANIFEST, current)
        identity = {key: current[key] for key in ("commit", "version", "revision", "app_common_commit")}
        identity["commit"] = hashlib.sha1((current["commit"] + "smoke").encode()).hexdigest()
        identity["version"] = identity["commit"][:8]
        identity["revision"] += 1
        assets = base / "assets"
        target = generate(candidate, assets, identity, "macos", current["arch"])
        atomic_json(installed / CONFIG_NAME, {"source": "local", "directory": str(assets)})
        program = executable(installed, "SuperBirdUpdater")
        command = [str(program), "--root", str(installed)]
        def run(*args):
            result = subprocess.run([*command, *args], check=True, capture_output=True, text=True, timeout=180)
            print(result.stdout.strip(), flush=True)
            return json.loads(result.stdout)
        cache = base / "cache"
        try:
            assert run("--check")["update"]
            run("--prepare", str(cache))
            process = run("--apply", str(cache))
            deadline = time.monotonic() + 180
            while process_alive(process["installer_pid"]) and time.monotonic() < deadline:
                time.sleep(0.1)
            if process_alive(process["installer_pid"]):
                raise RuntimeError(f"安装超时，日志: {process['log']}")
            assert load(installed / INSTALLED_MANIFEST)["commit"] == target["commit"], Path(process["log"]).read_text()
            for app in APPS:
                subprocess.run(["/usr/bin/codesign", "--verify", "--deep", str(installed / f"{app}.app")], check=True)
                assert (installed / f"{app}.app/Contents/Resources/update-smoke.txt").read_text() == "更新烟雾测试"
            assert not run("--check")["update"]
            print("PACKAGED_UPDATE_SMOKE_OK: three bundles, self-update, signatures, Chinese resource", flush=True)
        finally:
            from SuperBirdUpdater.runtime import cleanup_helpers
            cleanup_helpers(installed)
            shutil.rmtree(user_state(installed), ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path)
    smoke(parser.parse_args().dist.resolve())
