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
        "    import platform\n"
        "    assert platform.win32_ver()[1]\n"
        "    assert platform.machine()\n"
        "    import PyQt6.QtCore\n"
        "    import torch\n"
        "    return str(PyQt6.QtCore.PYQT_VERSION_STR), str(torch.__version__)\n"
        "print(isolated.call(probe))\n"
    )

    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows platform fallback")
def test_platform_queries_use_local_fallback_even_when_wmi_cannot_respond() -> None:
    result = _run_probe(
        "import os, platform, sitecustomize, sys\n"
        "class UnresponsiveWMI:\n"
        "    def exec_query(self, *args):\n"
        "        raise AssertionError('build must not query WMI')\n"
        "platform._wmi = UnresponsiveWMI()\n"
        "sitecustomize._use_local_windows_platform_info()\n"
        "if hasattr(platform, 'invalidate_caches'): platform.invalidate_caches()\n"
        "release, version, csd, ptype = platform.win32_ver()\n"
        "assert release and version\n"
        "assert tuple(map(int, version.split('.')))[:2] == sys.getwindowsversion()[:2]\n"
        "expected = os.environ.get('PROCESSOR_ARCHITEW6432') or os.environ['PROCESSOR_ARCHITECTURE']\n"
        "assert platform.machine() == expected\n"
        "assert platform.processor()\n"
        "print(release, version, platform.machine())\n"
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PyInstaller startup")
def test_pyinstaller_cli_starts_with_build_bootstrap() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        env=_bootstrap_env(), capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    assert result.stdout.strip()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows frozen runtime hook")
def test_bootstrap_runs_as_runtime_hook_without_pythonpath() -> None:
    hook = Path(__file__).resolve().parents[1] / "pyinstaller_bootstrap" / "sitecustomize.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c",
         f"import runpy; runpy.run_path({str(hook)!r}); "
         "import platform; assert platform.win32_ver()[1]; "
         "import numpy; import PyQt6.QtCore; import torch; "
         "print(torch.__version__)"],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
