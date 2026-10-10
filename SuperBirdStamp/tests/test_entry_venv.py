"""The GUI launcher must stay windowless when selecting the repo interpreter."""
from types import SimpleNamespace

import pytest

from SuperBirdStamp import entry


@pytest.fixture
def windows_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(entry.sys, "platform", "win32")
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(entry.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    for name in ("python.exe", "pythonw.exe"):
        (scripts / name).touch()
    return tmp_path


@pytest.mark.parametrize("name", ["python.exe", "pythonw.exe"])
def test_current_repo_interpreter_is_not_restarted(windows_repo, monkeypatch, name):
    executable = windows_repo / ".venv" / "Scripts" / name
    monkeypatch.setattr(entry.sys, "executable", str(executable))
    monkeypatch.setattr(entry.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected restart"))

    entry._reexec_into_repo_venv_if_needed(windows_repo)


@pytest.mark.parametrize("name", ["python.exe", "pythonw.exe"])
@pytest.mark.parametrize("windowless_available", [True, False])
def test_switch_environment_preserves_launch_mode_and_arguments(
    windows_repo, monkeypatch, name, windowless_available,
):
    if not windowless_available:
        (windows_repo / ".venv" / "Scripts" / "pythonw.exe").unlink()
    monkeypatch.setattr(entry.sys, "executable", str(windows_repo / "other-env" / name))
    monkeypatch.setattr(entry.sys, "argv", ["entry.py", "--file", "照片/鸟 图.jpg"])
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(entry.subprocess, "run", run)
    with pytest.raises(SystemExit) as result:
        entry._reexec_into_repo_venv_if_needed(windows_repo)

    assert result.value.code == 7
    expected_name = name if windowless_available else "python.exe"
    assert calls[0][0] == [
        str(windows_repo / ".venv" / "Scripts" / expected_name),
        "-m", "SuperBirdStamp.entry", "--file", "照片/鸟 图.jpg",
    ]
    expected_kwargs = {"check": False}
    if name == "pythonw.exe":
        expected_kwargs["creationflags"] = 0x08000000
    assert calls[0][1] == expected_kwargs


def test_packaged_app_never_restarts_into_source_venv(windows_repo, monkeypatch):
    monkeypatch.setattr(entry.sys, "frozen", True)
    monkeypatch.setattr(entry, "_repo_venv_python", lambda _: pytest.fail("looked up source venv"))
    entry._reexec_into_repo_venv_if_needed(windows_repo)


def test_missing_venv_keeps_current_interpreter(tmp_path, monkeypatch):
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(entry.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected restart"))
    entry._reexec_into_repo_venv_if_needed(tmp_path)


def test_macos_restart_uses_python3_without_windows_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(entry.sys, "platform", "darwin")
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(entry.sys, "executable", str(tmp_path / "other-env" / "python3"))
    monkeypatch.setattr(entry.sys, "argv", ["entry.py"])
    target = tmp_path / ".venv" / "bin" / "python3"
    target.parent.mkdir(parents=True)
    target.touch()

    def run(command, **kwargs):
        assert command == [str(target), "-m", "SuperBirdStamp.entry"]
        assert kwargs == {"check": False}
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(entry.subprocess, "run", run)
    with pytest.raises(SystemExit) as result:
        entry._reexec_into_repo_venv_if_needed(tmp_path)
    assert result.value.code == 0
