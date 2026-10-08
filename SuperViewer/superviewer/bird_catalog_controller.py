"""手动指定鸟名：分页搜索、后台详情及有界批量 XMP 保存。"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import queue
import time

from app_common.bird_rarity import IUCN_LABELS
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_catalog import BirdCatalogClient, apply_bird, snapshot
from .bird_identification import BirdIDOptions, collect_paths
from .metadata_result_sync import MetadataResultSync
from .qt_compat import QThread
try:
    from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                                QListWidget, QListWidgetItem, QTextEdit, QPushButton, QMessageBox)
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
    from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                                QListWidget, QListWidgetItem, QTextEdit, QPushButton, QMessageBox)


class CatalogQueryWorker(QThread):
    def __init__(self, options, generation, kind, arguments):
        super().__init__()
        self.client = BirdCatalogClient(options)
        self.generation, self.kind, self.arguments = generation, kind, arguments
        self.value, self.error = None, ''

    def stop(self):
        self.client.cancel()

    def run(self):
        try:
            self.value = getattr(self.client, self.kind)(**self.arguments)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.client.cancel()


class CatalogApplyWorker(QThread):
    def __init__(self, paths, bird, options):
        super().__init__()
        self.paths, self.bird = paths, bird
        self.client = BirdCatalogClient(options)
        self.results = queue.Queue(maxsize=128)
        self.total, self.failure = 0, ''

    def stop(self):
        self.client.cancel()

    def run(self):
        try:
            paths = collect_paths(self.paths, cancelled=self.client.cancelled.is_set)
            self.total = len(paths)
            if not paths:
                self.client.check_cancelled()
                raise ValueError('没有找到受支持的照片')
            before = {}
            for path in paths:
                self.client.check_cancelled()
                before[path] = snapshot(path)
            bird = self.client.detail(self.bird['bird_id'], self.bird['version_id'])
            if bird != self.bird:
                raise ValueError('鸟种资料已更新，请重新选择并查看详情后应用')
            for path in paths:
                if self.client.cancelled.is_set():
                    break
                result = apply_bird(path, bird, before[path], cancelled=self.client.cancelled.is_set)
                # 已写结果必须交付；关闭期间控制器仍排空队列。
                self.results.put(result)
        except Exception as exc:
            if not self.client.cancelled.is_set():
                self.failure = str(exc)


class CatalogDialog(QDialog):
    query_changed = pyqtSignal()
    bird_selected = pyqtSignal(object)
    bird_activated = pyqtSignal(object)
    apply_requested = pyqtSignal()

    def __init__(self, parent, options, count):
        super().__init__(parent)
        self.setWindowTitle('手动指定鸟名（SuperPicky）')
        self.resize(740, 640)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'选择鸟种后点击应用，或双击鸟名，指定到所选 {count} 张照片。'))
        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('中文 / 英文 / 学名 / 拼音 / 缩写；留空浏览鸟名列表')
        self.query.setToolTip(self.query.placeholderText())
        search_row.addWidget(self.query, 3)
        self.url = QLineEdit(options.url)
        self.url.setPlaceholderText('本机 SuperPicky 服务地址')
        self.url.setToolTip('本机 SuperPicky 服务地址')
        self.url.setMaximumWidth(260)
        search_row.addWidget(self.url, 2)
        layout.addLayout(search_row)
        self.status = QLabel('正在读取鸟名列表…')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.list = QListWidget()
        layout.addWidget(self.list, 3)
        pages = QHBoxLayout()
        self.previous, self.next = QPushButton('上一页'), QPushButton('下一页')
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        pages.addWidget(self.previous)
        pages.addWidget(self.next)
        layout.addLayout(pages)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.document().setMaximumBlockCount(1000)
        layout.addWidget(self.detail, 2)
        layout.addWidget(QLabel('等级来自 IUCN；国家保护等级无资料时显示未知。简介独立保存，保留照片备注。'))
        self.apply = QPushButton(f'应用到所选 {count} 张照片的 XMP')
        self.apply.setEnabled(False)
        layout.addWidget(self.apply)
        for button in (self.previous, self.next, self.apply):
            button.setAutoDefault(False)
        self.query.textChanged.connect(self.query_changed.emit)
        self.url.textChanged.connect(self.query_changed.emit)
        self.list.currentItemChanged.connect(lambda current, _old: self.bird_selected.emit(
            current.data(Qt.ItemDataRole.UserRole) if current else None))
        self.list.itemDoubleClicked.connect(lambda item: self.bird_activated.emit(
            item.data(Qt.ItemDataRole.UserRole)))
        self.apply.clicked.connect(self.apply_requested.emit)

    def show_birds(self, data):
        self.list.clear()
        for bird in data['results']:
            text = f"{bird['cn_name']}  {bird['pinyin_name']}  |  {bird['en_name']}  |  {bird['scientific_name']}"
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setData(Qt.ItemDataRole.UserRole, bird)
            self.list.addItem(item)
        start = data['offset'] + 1 if data['results'] else 0
        end = data['offset'] + len(data['results'])
        self.status.setText(f"{data.get('version_name', '')} · 共 {data['total']} 种，显示 {start}–{end}")
        self.previous.setEnabled(data['offset'] > 0)
        self.next.setEnabled(end < data['total'])

    def show_detail(self, bird):
        category = bird['iucn_category']
        score = bird['gbif_rarity_100']
        self.detail.setPlainText('\n'.join([
            f"中文：{bird['cn_name']}　英文：{bird['en_name']}",
            f"学名：{bird['scientific_name']}",
            f"拼音：{bird['pinyin_name'] or '未知'}　无声调：{bird['pinyin_plain']}　缩写：{bird['abbreviation']}",
            f"全球稀有度：{str(score) + '/100' if score is not None else '未知'}",
            f"IUCN：{category or '未知'} {IUCN_LABELS.get(category, '')}",
            f"中国国家保护等级：{bird['china_protection_level'] or '未知（资料库未提供）'}",
            f"简介：{bird['description'] or '暂无'}",
        ]))
        self.apply.setEnabled(True)


class BirdCatalogController(QObject):
    def __init__(self, window, files, options_provider=BirdIDOptions):
        super().__init__(window)
        self._main, self._files = window, files
        self._options_provider = options_provider
        self._sync = MetadataResultSync(files)
        self._dialog = self._query_worker = self._apply_worker = None
        self._pending = self._selected = None
        self._apply_after_detail = None
        self._generation = 0
        self._shutdown_requested = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.search)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._drain)
        files.add_file_context_menu_extender(self.extend_file_menu)

    @property
    def busy(self):
        return self._query_worker is not None or self._apply_worker is not None or self._debounce.isActive()

    def _allowed(self):
        check = getattr(self._files, '_file_writes_allowed', None)
        return not callable(check) or check('手动指定鸟名')

    def extend_file_menu(self, menu, paths):
        photos = [p for p in paths if Path(p).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS]
        if photos:
            action = menu.addAction('手动指定鸟名…', lambda: self.open(photos))
            action.setEnabled(self._allowed() and not self._shutdown_requested and self._apply_worker is None)

    def open(self, paths):
        if self._shutdown_requested or self._apply_worker is not None or not self._allowed():
            return False
        try:
            resolve = self._files._resolve_source_path_for_action
            aliases = [(p, resolve(p)) for p in paths]
            if not aliases or any(not source for _, source in aliases):
                raise ValueError('找不到照片原文件')
        except Exception as exc:
            QMessageBox.warning(self._main, '手动指定鸟名', str(exc))
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._aliases = aliases
        self._dialog = dialog = CatalogDialog(self._main, self._options_provider(), len(paths))
        dialog.query_changed.connect(self._changed)
        dialog.bird_selected.connect(self.select)
        dialog.bird_activated.connect(self.activate)
        dialog.apply_requested.connect(self.apply)
        dialog.previous.clicked.connect(lambda: self.search(max(0, self._offset - 100)))
        dialog.next.clicked.connect(lambda: self.search(self._offset + 100))
        dialog.finished.connect(self._dismiss)
        dialog.show()
        dialog.query.setFocus()
        self._offset, self._version_id = 0, None
        self.search()
        return True

    def _invalidate(self):
        self._generation += 1
        self._pending = self._selected = None
        self._apply_after_detail = None
        if self._query_worker is not None:
            self._query_worker.stop()
        if self._dialog is not None:
            self._dialog.apply.setEnabled(False)
            self._dialog.detail.clear()

    def _changed(self):
        self._invalidate()
        self._version_id = None
        self._dialog.list.clear()
        self._dialog.previous.setEnabled(False)
        self._dialog.next.setEnabled(False)
        self._debounce.start()

    def _request(self, kind, arguments):
        self._invalidate()
        if self._shutdown_requested or self._apply_worker is not None:
            return
        try:
            options = BirdIDOptions(url=self._dialog.url.text().strip())
            options.validate()
        except ValueError as exc:
            self._dialog.status.setText(str(exc))
            return
        self._pending = (options, self._generation, kind, arguments)
        self._start_pending()

    def _start_pending(self):
        if self._pending is None or self._query_worker is not None or self._shutdown_requested:
            return
        self._query_worker = worker = CatalogQueryWorker(*self._pending)
        self._pending = None
        worker.finished.connect(lambda w=worker: self._query_finished(w))
        worker.start()

    def search(self, offset=0):
        self._debounce.stop()
        self._offset = offset
        self._dialog.list.clear()
        self._dialog.status.setText('正在查询鸟名…')
        self._dialog.previous.setEnabled(False)
        self._dialog.next.setEnabled(False)
        self._request('search', dict(query=self._dialog.query.text().strip(), offset=offset,
                                     version_id=self._version_id))

    def select(self, bird):
        if bird is None:
            self._invalidate()
            return
        self._dialog.status.setText('正在查询所选鸟种详情…')
        self._request('detail', dict(bird_id=bird['bird_id'], version_id=bird['version_id']))

    def activate(self, bird):
        """双击即应用；详情未到时绑定本次请求，切换/关闭后不得自动保存。"""
        if not bird or self._shutdown_requested or self._apply_worker is not None or not self._allowed():
            return
        identity = (bird['bird_id'], bird['version_id'])
        if self._selected is not None and identity == (self._selected['bird_id'], self._selected['version_id']):
            self.apply()
        else:
            self.select(bird)
            self._apply_after_detail = (self._generation, *identity)

    def _query_finished(self, worker):
        if worker is not self._query_worker:
            return
        self._query_worker = None
        if not self._shutdown_requested and worker.generation == self._generation:
            if worker.error:
                self._apply_after_detail = None
                self._dialog.status.setText(worker.error)
            elif worker.kind == 'search':
                self._version_id = worker.value['version_id']
                self._dialog.show_birds(worker.value)
            else:
                self._selected = worker.value
                self._dialog.show_detail(worker.value)
                self._dialog.status.setText('已取得详情，可应用到所选照片。')
                if self._apply_after_detail == (worker.generation, worker.value['bird_id'], worker.value['version_id']):
                    self.apply()
        worker.deleteLater()
        self._start_pending()

    def apply(self):
        if self._selected is None or self._apply_worker is not None or self._shutdown_requested or not self._allowed():
            return False
        options = BirdIDOptions(url=self._dialog.url.text().strip())
        self._apply_worker = worker = CatalogApplyWorker([s for _, s in self._aliases], self._selected, options)
        self._counts, self._apply_finished_received = Counter(), False
        self._apply_after_detail = None
        self._dialog.setEnabled(False)
        self._dialog.status.setText('正在核对鸟种资料并保存…')
        worker.finished.connect(lambda w=worker: self._apply_finished(w))
        self._timer.start()
        worker.start()
        return True

    def _apply_finished(self, worker):
        if worker is self._apply_worker:
            self._apply_finished_received = True
            self._drain()

    def _drain(self):
        worker = self._apply_worker
        if worker is None:
            return
        batch, start = [], time.monotonic()
        while len(batch) < 16 and time.monotonic() - start < .008:
            try:
                batch.append(worker.results.get_nowait())
            except queue.Empty:
                break
        self._counts.update(r.status for r in batch)
        if not self._shutdown_requested:
            self._sync.sync(batch, self._aliases)
            for result in batch:
                if result.status != 'success':
                    self._dialog.detail.insertPlainText(f'\n{Path(result.source).name}：{result.message}')
            if batch:
                self._dialog.status.setText(f"已处理 {sum(self._counts.values())}/{worker.total}，"
                                           f"已保存 {self._counts['success']}，跳过 {self._counts['skipped']}，失败 {self._counts['failed']}")
        if self._apply_finished_received and worker.results.empty():
            self._timer.stop()
            self._apply_worker = None
            self._dialog.setEnabled(True)
            if not self._shutdown_requested:
                if not worker.failure and not worker.client.cancelled.is_set() and self._counts['success'] == worker.total:
                    self._dialog.accept()
                else:
                    text = (f'保存未完成：{worker.failure}' if worker.failure else
                            '已停止，已保存的鸟名保留。' if worker.client.cancelled.is_set() else
                            f"已保存 {self._counts['success']}，跳过 {self._counts['skipped']}，失败 {self._counts['failed']}，请查看详情。")
                    self._dialog.status.setText(text)
            worker.deleteLater()

    def _dismiss(self):
        self._debounce.stop()
        self._invalidate()
        if self._apply_worker is not None:
            self._apply_worker.stop()

    def request_shutdown(self):
        self._shutdown_requested = True
        self._dismiss()
        if self._apply_worker is not None:
            self._apply_worker.stop()
        if self._dialog is not None:
            self._dialog.close()

    def is_shutdown_done(self):
        return self._query_worker is None and self._apply_worker is None
