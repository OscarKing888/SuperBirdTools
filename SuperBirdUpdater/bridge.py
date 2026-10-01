"""应用中的轻量 Qt 适配器；不接管原有照片发送接口。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QMessageBox

from .common import CONFIG_NAME, UpdateError, read_json, suite_root
from .runtime import get_registration, start_updater

log = logging.getLogger("SuperBirdUpdater.bridge")


class AppEndpoint(QObject):
    def __init__(self, window, registration: dict, ready):
        super().__init__(window)
        self.window, self.registration, self.ready = window, registration, ready
        self.prepared = False
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        if not self.server.listen(registration["server"]):
            raise UpdateError("更新器本地通信监听失败")

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket._update_buffer = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(socket.abort)
            timer.start(5000)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket):
        socket._update_buffer.extend(bytes(socket.readAll()))
        if len(socket._update_buffer) > 4096:
            socket.abort()
            return
        if b"\n" not in socket._update_buffer:
            return
        try:
            request = json.loads(bytes(socket._update_buffer).split(b"\n", 1)[0])
            if request.get("token") != self.registration["token"]:
                raise ValueError("invalid token")
            action = request.get("action")
            if action not in {"status", "prepare", "close", "resume"}:
                raise ValueError("invalid action")
            reason = str(self.ready() or "")
            if action == "resume":
                self.prepared = False
                self.window.setEnabled(True)
            elif action == "prepare" and not reason:
                self.prepared = True
                # 防止准备退出成功后用户又开始导出。
                self.window.setEnabled(False)
            accepted = not reason and (action != "close" or self.prepared)
            response = {"ready": accepted, "reason": reason, "pid": self.registration["pid"]}
            socket.write(json.dumps(response).encode() + b"\n")
            socket.flush()
            socket.disconnectFromServer()
            if action == "close" and accepted:
                QTimer.singleShot(0, self.window.close)
        except (ValueError, TypeError) as exc:
            log.warning("update IPC rejected: %s", exc)
            socket.abort()


def request_instance(instance: dict, action: str, timeout_ms: int = 3000) -> dict:
    socket = QLocalSocket()
    socket.connectToServer(instance["server"])
    if not socket.waitForConnected(timeout_ms):
        raise UpdateError(f"无法联系 {instance['app']}，请等待启动完成或手动关闭")
    try:
        socket.write(json.dumps({"action": action, "token": instance["token"]}).encode() + b"\n")
        socket.waitForBytesWritten(timeout_ms)
        buffer = bytearray()
        while b"\n" not in buffer:
            if not socket.bytesAvailable() and not socket.waitForReadyRead(timeout_ms):
                raise UpdateError(f"{instance['app']} 未响应更新请求")
            buffer.extend(bytes(socket.readAll()))
            if len(buffer) > 4096:
                raise UpdateError("应用响应过大")
        response = json.loads(bytes(buffer).split(b"\n", 1)[0])
        if response.get("pid") != instance["pid"]:
            raise UpdateError("应用进程身份不匹配")
        return response
    finally:
        socket.abort()


def attach(window, app_id: str, ready=lambda: "") -> None:
    registration = get_registration()
    root = suite_root(Path(sys.executable)) if getattr(sys, "frozen", False) else None

    def manual():
        try:
            if root is None:
                raise UpdateError("源码运行不自动替换代码；请从完整打包套件运行更新器")
            start_updater(root)
        except Exception as exc:
            QMessageBox.information(window, "检查更新", str(exc))

    action = QAction("检查更新…", window)
    action.triggered.connect(manual)
    # 独立菜单不会与原有菜单重建或应用业务菜单发生耦合。
    menu = window.menuBar().addMenu("更新")
    menu.addAction(action)
    window._updater_menu = menu
    if registration is None or root is None:
        return
    try:
        endpoint = AppEndpoint(window, registration, ready)
        window._updater_endpoint = endpoint
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().aboutToQuit.connect(endpoint.server.close)
        config = read_json(root / CONFIG_NAME)
        if config.get("automatic_check", True):
            timer = QTimer(window)
            timer.setSingleShot(True)
            def check():
                try:
                    start_updater(root, automatic=True)
                except Exception:
                    log.exception("background updater launch failed")
            timer.timeout.connect(check)
            timer.start(max(1000, int(config.get("startup_delay_ms", 5000))))
            window._updater_timer = timer
    except Exception:
        log.exception("updater integration unavailable")
