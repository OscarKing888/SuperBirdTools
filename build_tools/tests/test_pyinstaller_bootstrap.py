from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _bootstrap_env() -> dict[str, str]:
    repo_root = Path(__file__).resolve().parents[2]
    bootstrap_dir = repo_root / "build_tools" / "pyinstaller_bootstrap"
    env = os.environ.copy()
    existing_pythonpath = str(env.get("PYTHONPATH", "") or "").strip()
    env["PYTHONPATH"] = (
        str(bootstrap_dir)
        if not existing_pythonpath
        else os.pathsep.join((str(bootstrap_dir), existing_pythonpath))
    )
    return env


def _run_probe(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source],
        env=_bootstrap_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL-order regression")
def test_bootstrap_allows_pyqt_to_be_imported_before_torch() -> None:
    result = _run_probe(
        "import PyQt6.QtCore; "
        "import torch; "
        "print(PyQt6.QtCore.PYQT_VERSION_STR, torch.__version__)"
    )

    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL-order regression")
def test_bootstrap_reaches_pyinstaller_isolated_worker() -> None:
    result = _run_probe(
        "from PyInstaller import isolated\n"
        "def probe():\n"
        "    import PyQt6.QtCore\n"
        "    import torch\n"
        "    return str(PyQt6.QtCore.PYQT_VERSION_STR), str(torch.__version__)\n"
        "print(isolated.call(probe))\n"
    )

    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
