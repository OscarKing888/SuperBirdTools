"""Repository conftest keeps GUI tests headless unless windows are explicitly requested."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_PROBE = (
    "import os, runpy; runpy.run_path('conftest.py'); "
    "print(os.environ.get('QT_QPA_PLATFORM', '<unset>'))"
)


def _platform_after_conftest(**env_overrides: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "SUPERBIRD_TEST_SHOW_WINDOWS")}
    env.update(env_overrides)
    cp = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    )
    return cp.stdout.strip()


def test_default_is_offscreen() -> None:
    assert _platform_after_conftest() == "offscreen"


def test_explicit_platform_is_respected() -> None:
    assert _platform_after_conftest(QT_QPA_PLATFORM="minimal") == "minimal"


@pytest.mark.parametrize("flag", ["1", "true", "YES"])
def test_show_windows_flag_keeps_native_platform(flag) -> None:
    assert _platform_after_conftest(SUPERBIRD_TEST_SHOW_WINDOWS=flag) == "<unset>"


def test_run_tests_scripts_exist_and_default_to_offscreen() -> None:
    sh = (REPO_ROOT / "run_tests.sh").read_text(encoding="utf-8")
    bat = (REPO_ROOT / "run_tests.bat").read_text(encoding="utf-8")
    assert "export QT_QPA_PLATFORM=offscreen" in sh and "--show-windows" in sh
    assert 'set "QT_QPA_PLATFORM=offscreen"' in bat and "--show-windows" in bat
    bat.encode("ascii")  # cmd.exe code pages: keep the batch file ASCII-only
    if os.name != "nt":
        assert os.access(REPO_ROOT / "run_tests.sh", os.X_OK)
