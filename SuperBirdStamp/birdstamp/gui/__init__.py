from __future__ import annotations

from pathlib import Path


def launch_gui(
    startup_file: Path | None = None,
    startup_files: list[Path] | None = None,
) -> None:
    from birdstamp.gui.editor import launch_gui as _launch_gui

    _launch_gui(startup_file=startup_file, startup_files=startup_files)


__all__ = ["launch_gui"]
