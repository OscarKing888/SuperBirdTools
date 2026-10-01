from __future__ import annotations

from pathlib import Path
import threading
import time

from .common import INSTALLED_MANIFEST, Cancelled, UpdateError, executable
from .install import install, journal_path, preflight, recover, state_dir
from .locking import FileLock
from .manifest import load, newer
from .runtime import active_instances, launch_process, process_alive


def apply_update(root: Path, candidate: dict, cache: Path, *, request, cancel=None,
                 progress=None, shutdown_timeout: float = 120, restart=True) -> list[str]:
    cancel = cancel or threading.Event()
    instances, prepared = [], []
    gate = FileLock(state_dir(root) / "launch.lock")
    if not gate.acquire(timeout=5):
        gate.close()
        raise UpdateError("其他启动或更新操作正在进行，请重试")
    try:
        instances = active_instances(root)
        if journal_path(root).exists():
            if instances:
                raise UpdateError("存在待恢复事务，请先关闭应用")
            recover(root)
        current = load(root / INSTALLED_MANIFEST)
        if not newer(current, candidate):
            raise UpdateError("安装版本已变化，无需应用此更新")
        preflight(root, sum(e.get("size", 0) for e in candidate["files"]) * 2)
        # 所有实例先通过准备检查，再发任何关闭请求。
        for instance in instances:
            if cancel.is_set():
                raise Cancelled("更新已取消")
            response = request(instance, "prepare")
            if not response.get("ready"):
                raise UpdateError(f"{instance['app']} 暂不能退出: {response.get('reason', '')}")
            prepared.append(instance)
        for instance in prepared:
            response = request(instance, "close")
            if not response.get("ready"):
                raise UpdateError(f"{instance['app']} 拒绝退出")
        deadline = time.monotonic() + shutdown_timeout
        while any(process_alive(i["pid"]) for i in instances):
            if cancel.is_set():
                raise Cancelled("已暂停安装；等待应用结束后可重试")
            if time.monotonic() >= deadline:
                raise UpdateError("应用尚未完全退出，安装已暂停；请稍后重试")
            if progress:
                progress(0, 0, "正在等待两款应用安全退出…")
            cancel.wait(0.1)
        if progress:
            progress(0, 0, "正在安装；请勿关闭更新器…")
        install(root, current, candidate, cache)
        return sorted({i["app"] for i in instances})
    finally:
        for instance in prepared:
            if process_alive(instance["pid"]):
                try:
                    request(instance, "resume")
                except Exception:
                    pass
        gate.close()
        # 失败恢复完成后同样恢复原本已关闭的应用；未完成恢复时禁止启动。
        if restart and not journal_path(root).exists():
            for app in sorted({i["app"] for i in prepared}):
                running = [i for i in instances if i["app"] == app and process_alive(i["pid"])]
                if not running:
                    launch_process([str(executable(root, app))], cwd=root)
