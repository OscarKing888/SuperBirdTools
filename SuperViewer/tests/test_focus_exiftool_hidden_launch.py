import subprocess
import sys

from SuperViewer.superviewer import focus_preview_loader


def test_focus_metadata_uses_hidden_exiftool_runner(monkeypatch, tmp_path):
    photo = tmp_path / "测试.hif"
    photo.touch()
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '[{"EXIF:Make":"Camera"}]', "")

    monkeypatch.setattr(focus_preview_loader, "get_exiftool_executable_path", lambda: "exiftool.exe")
    monkeypatch.setattr(focus_preview_loader, "run_exiftool_once", fake_run)

    assert focus_preview_loader._run_exiftool_json_for_focus(str(photo)) == {"EXIF:Make": "Camera"}
    assert len(commands) == 1
    command, kwargs = commands[0]
    assert command[0] == "exiftool.exe"
    if sys.platform.startswith("win"):
        assert "-@" in command
    else:
        assert str(photo) in command
    assert kwargs["capture_output"] is True
