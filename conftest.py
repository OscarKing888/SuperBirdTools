"""Repository-wide pytest setup.

GUI tests construct real SuperViewer / SuperBirdStamp windows. Without a headless
Qt platform those windows pop up on the desktop (macOS Cocoa / Windows) while
someone is using the apps, so every test run defaults to ``offscreen``. This file
is imported before any test module, i.e. before PyQt creates a QApplication.

- An explicit ``QT_QPA_PLATFORM`` in the environment is respected.
- ``SUPERBIRD_TEST_SHOW_WINDOWS=1`` (or ``run_tests.sh --show-windows``) keeps
  the native platform for debugging a GUI test visually.
"""

import os

SHOW_WINDOWS_ENV = "SUPERBIRD_TEST_SHOW_WINDOWS"


def _show_windows_requested() -> bool:
    return os.environ.get(SHOW_WINDOWS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


if not _show_windows_requested():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
