from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import threading
import time

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox,
                             QProgressBar, QPushButton, QVBoxLayout)

from .bridge import request_instance
from .common import (CONFIG_NAME, INSTALLED_MANIFEST, Cancelled, UpdateError, architecture,
                     atomic_json, platform_id, read_json)
from .coordinator import apply_update
from .download import prepare
from .install import preflight, state_dir
from .manifest import load, newer
from .runtime import isolated_updater, launch_process, user_state
from .sources import RangeUnsupported, source_from_config

log = logging.getLogger("SuperBirdUpdater")


class Task(QThread):
    progress = pyqtSignal(int, int, str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation(self.progress.emit)
        except Exception as exc:
            self.error = exc
            if not isinstance(exc, (Cancelled, RangeUnsupported)):
                log.exception("update operation failed")


class UpdateWindow(QDialog):
    def __init__(self, root: Path, *, automatic: bool = False, job: Path | None = None):
        super().__init__()
        self.root, self.automatic, self.job = root, automatic, job
        self.state = user_state(root)
        self.cancel = threading.Event()
        self.task = None
        self.allow_full = False
        self.source = self.candidate = None
        self._closing = False
        self.setWindowTitle("SuperBirdTools 更新")
        self.resize(480, 150)
        layout = QVBoxLayout(self)
        self.label = QLabel("正在检查更新…")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        buttons = QHBoxLayout()
        self.retry = QPushButton("重试")
        self.retry.hide()
        self.retry.clicked.connect(self.begin)
        buttons.addWidget(self.retry)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.close)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        if not automatic or job:
            self.show()
        QTimer.singleShot(0, self.begin)

    def _run(self, operation, done):
        self.retry.hide()
        task = Task(operation, self)
        self.task = task
        task.progress.connect(self._progress)
        def finished():
            if self.task is not task:
                return
            self.task = None
            result, error = task.result, task.error
            task.deleteLater()
            if self._closing:
                self._quit()
            else:
                done(result, error)
        task.finished.connect(finished)
        task.start()

    def _progress(self, index, total, text):
        self.label.setText(text)
        self.bar.setRange(0, total)
        self.bar.setValue(index)

    def begin(self):
        self.cancel.clear()
        if self.job:
            self.cancel_button.setEnabled(False)
            def apply(progress):
                job = read_json(self.job)
                candidate = load(Path(job["manifest"]))
                return apply_update(self.root, candidate, Path(job["cache"]), request=request_instance,
                                    cancel=self.cancel, progress=progress)
            self._run(apply, self._installed)
        else:
            self._run(self._check, self._checked)

    def _check(self, progress):
        current = load(self.root / INSTALLED_MANIFEST)
        if current["platform"] != platform_id() or current["arch"] not in {architecture(), "universal2"}:
            raise UpdateError("安装套件与当前平台或 CPU 架构不匹配")
        config = read_json(self.root / CONFIG_NAME)
        settings_path = self.state / "preferences.json"
        settings = read_json(settings_path) if settings_path.exists() else {}
        if self.automatic and (not config.get("automatic_check", True) or
                time.time() - settings.get("last_check", 0) < max(60, int(config.get("check_interval_seconds", 3600)))):
            return None
        settings["last_check"] = time.time()
        atomic_json(settings_path, settings)
        self.source = source_from_config(config, current["platform"], current["arch"])
        candidate = self.source.latest()
        if not candidate or not newer(current, candidate):
            return None
        if self.automatic and settings.get("skipped_commit") == candidate["commit"]:
            return None
        self.candidate = candidate
        return current["version"], candidate["version"], self.source.notes

    def _checked(self, result, error):
        if error:
            if self.automatic:
                self._quit()
            else:
                self._error(error)
            return
        if result is None:
            if not self.automatic:
                QMessageBox.information(self, "检查更新", "当前没有可用的新版本。")
            self._quit()
            return
        old, new, notes = result
        self.show()
        prompt = QMessageBox(self)
        prompt.setWindowTitle("发现新版本")
        prompt.setText(f"当前版本：{old}\n新版本：{new}\n\n下载完成后将安全关闭两款应用，安装后重新启动。")
        prompt.setDetailedText(notes or "此版本未提供发布说明。")
        update = prompt.addButton("更新", QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        skip = prompt.addButton("跳过此版本", QMessageBox.ButtonRole.DestructiveRole)
        prompt.exec()
        if prompt.clickedButton() is skip:
            path = self.state / "preferences.json"
            settings = read_json(path) if path.exists() else {}
            settings["skipped_commit"] = self.candidate["commit"]
            atomic_json(path, settings)
        if prompt.clickedButton() is not update:
            self._quit()
            return
        self._download()

    def _download(self):
        self.automatic = False
        self.cache = self.state / "downloads" / self.candidate["commit"]
        def download(progress):
            preflight(self.root)
            return prepare(self.root, self.candidate, self.source, self.cache, cancel=self.cancel,
                           progress=progress, allow_full=self.allow_full)
        self._run(download, self._downloaded)

    def _downloaded(self, result, error):
        if isinstance(error, RangeUnsupported):
            answer = QMessageBox.question(self, "需要整包下载", str(error),
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.allow_full = True
                self._download()
            else:
                self._quit()
            return
        if error:
            self._error(error)
            return
        # 安装子进程使用完整独立副本；此进程退出后才开始替换自身文件。
        def handoff(progress):
            command, temporary = isolated_updater(self.root)
            try:
                manifest = self.cache / "candidate.json"
                atomic_json(manifest, self.candidate)
                job = self.cache / "job.json"
                atomic_json(job, {"manifest": str(manifest), "cache": str(self.cache), "parent_pid": os.getpid()})
                atomic_json(state_dir(self.root) / "helper.json", {"command": command, "directory": str(temporary)})
                process = launch_process([*command, "--apply-job", str(job)], cwd=temporary)
                return process.pid
            except Exception:
                shutil.rmtree(temporary)
                raise
        self.cancel_button.setEnabled(False)
        self._run(handoff, lambda result, error: self._error(error) if error else self._quit())

    def _installed(self, result, error):
        self.cancel_button.setEnabled(True)
        if error:
            self._error(error)
            return
        QMessageBox.information(self, "更新完成", "新版本已安装，原来运行的应用已重新启动。")
        # 内容缓存保留到实际安装成功；失败时供重试继续使用。
        if self.job:
            shutil.rmtree(self.job.parent, ignore_errors=True)
        self._quit()

    def _error(self, error):
        self.show()
        self.label.setText(str(error))
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.retry.show()
        self.cancel_button.setEnabled(True)
        log.error("update paused: %s", error)

    def _quit(self):
        self.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if self.task is not None:
            if self.job or not self.cancel_button.isEnabled():
                event.ignore()
                return
            self._closing = True
            self.cancel.set()
            self.label.setText("正在取消下载…")
            event.ignore()
            return
        event.accept()
        self._quit()
