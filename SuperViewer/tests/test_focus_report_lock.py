"""A locked optional report must not crash the focus worker or Qt process."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from SuperViewer.superviewer import focus_box_loader as worker_module
from SuperViewer.superviewer import focus_preview_loader


def test_locked_report_returns_empty_focus_and_closes_connection(tmp_path, monkeypatch):
    scope = tmp_path / "炮台湾公园"
    (scope / ".superpicky").mkdir(parents=True)
    photo = scope / "DSC06453.ARW"
    photo.write_bytes(b"placeholder")
    db_path = scope / ".superpicky" / "report.db"
    writer = sqlite3.connect(db_path)
    writer.execute("CREATE TABLE photos (filename TEXT, focus_x REAL, focus_y REAL)")
    writer.commit()
    writer.execute("BEGIN EXCLUSIVE")
    connect = sqlite3.connect
    readers = []

    def fast_connect(*args, **kwargs):
        kwargs["timeout"] = 0.01
        connection = connect(*args, **kwargs)
        readers.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", fast_connect)
    monkeypatch.setattr(worker_module, "_load_focus_box_for_preview", lambda *a, **kw: None)
    monkeypatch.setattr(focus_preview_loader, "_load_focus_metadata_for_path", lambda *a: {})
    worker = worker_module.FocusBoxLoader(151, str(photo), str(photo), str(photo), 128, 85)
    results = []
    worker.focus_loaded.connect(lambda *args: results.append(args))
    try:
        worker.run()
        assert results == [(151, None, str(photo))]
        assert len(readers) == 1
        try:
            readers[0].execute("SELECT 1")
        except sqlite3.ProgrammingError as error:
            assert "closed" in str(error)
        else:
            raise AssertionError("report connection leaked")
    finally:
        writer.rollback()
        writer.close()


def test_locked_report_in_real_qthread_survives_without_stderr(tmp_path):
    # Isolate the actual Qt callback: an escaped exception aborts the interpreter.
    script = r'''
import sqlite3
import sys
from pathlib import Path
from PyQt6.QtCore import QCoreApplication, QTimer
from SuperViewer.superviewer import focus_box_loader as workers
from SuperViewer.superviewer import focus_preview_loader as focus

scope = Path(sys.argv[1])
(scope / '.superpicky').mkdir()
photo = scope / 'sample.ARW'
photo.write_bytes(b'placeholder')
writer = sqlite3.connect(scope / '.superpicky' / 'report.db')
writer.execute('CREATE TABLE photos (filename TEXT)')
writer.commit()
writer.execute('BEGIN EXCLUSIVE')
original_connect = sqlite3.connect
def fast_connect(*args, **kwargs):
    kwargs['timeout'] = 0.01
    return original_connect(*args, **kwargs)
sqlite3.connect = fast_connect
workers._load_focus_box_for_preview = lambda *a, **kw: None
focus._load_focus_metadata_for_path = lambda *a: {}
sys.stderr = None
app = QCoreApplication([])
worker = workers.FocusBoxLoader(151, str(photo), str(photo), str(photo), 128, 85)
results = []
worker.focus_loaded.connect(lambda *args: results.append(args))
worker.finished.connect(app.quit)
QTimer.singleShot(5000, lambda: app.exit(2))
worker.start()
exit_code = app.exec()
assert worker.wait(1000)
writer.rollback()
writer.close()
assert exit_code == 0
assert results == [(151, None, str(photo))], results
print('focus worker survived locked report')
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", APP_COMMON_LOG_FILE=str(tmp_path / "focus.log"))
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "focus worker survived locked report" in result.stdout
    assert "sqlite3.OperationalError: database is locked" in (tmp_path / "focus.log").read_text(encoding="utf-8")
