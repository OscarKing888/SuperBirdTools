from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import time

from .common import (APPS, CONFIG_NAME, INSTALLED_MANIFEST, UpdateError, atomic_json, read_json, suite_root)
from .install import journal_path, recover, state_dir
from .locking import FileLock
from .manifest import load, newer
from .runtime import (active_instances, cleanup_helpers, isolated_updater, launch_process,
                      process_alive, retire_helper, user_state, wait_and_relaunch)
from .sources import source_from_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SuperBirdTools 独立增量更新器")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--automatic", action="store_true")
    parser.add_argument("--check", action="store_true", help="只检查并输出 JSON，不安装")
    parser.add_argument("--diagnose", action="store_true", help="打包启动诊断，不读取用户配置")
    parser.add_argument("--recover", action="store_true", help="恢复未完成安装")
    parser.add_argument("--prepare", type=Path, metavar="CACHE", help="下载并校验新版本到指定目录，不关闭应用")
    parser.add_argument("--apply", type=Path, metavar="CACHE", help="独立启动安装已准备好的更新，并安全关闭应用")
    parser.add_argument("--allow-full", action="store_true", help="明确允许不支持 Range 时下载完整分卷")
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--wait-app", choices=APPS[:2])
    parser.add_argument("--resume-args", nargs=argparse.REMAINDER, default=[], help=argparse.SUPPRESS)
    parser.add_argument("--apply-job", type=Path)
    args = parser.parse_args(argv)
    if args.diagnose:
        from PyQt6.QtCore import QT_VERSION_STR
        print(json.dumps({"updater": 1, "qt": QT_VERSION_STR, "frozen": bool(getattr(sys, "frozen", False))}))
        return 0
    root = (args.root or suite_root(Path(sys.executable))).resolve()
    log_dir = user_state(root)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(log_dir / "updater.log", maxBytes=2*1024**2,
                                                                         backupCount=2, encoding="utf-8")])
    try:
        if args.wait_app:
            wait_and_relaunch(root, args.wait_app, args.resume_args)
            return 0
        if args.recover:
            with FileLock(state_dir(root) / "launch.lock"):
                if active_instances(root):
                    raise UpdateError("请先关闭正在运行的应用")
                recover(root)
            return 0
        if args.check or args.prepare:
            current = load(root / INSTALLED_MANIFEST)
            source = source_from_config(read_json(root / CONFIG_NAME), current["platform"], current["arch"])
            candidate = source.latest()
            if args.prepare:
                from .download import prepare
                if not candidate or not newer(current, candidate):
                    raise UpdateError("没有可用的新版本")
                prepare(root, candidate, source, args.prepare, allow_full=args.allow_full)
                atomic_json(args.prepare / "candidate.json", candidate)
            print(json.dumps({"current": current["version"], "available": candidate["version"] if candidate else None,
                              "update": bool(candidate and newer(current, candidate))}, ensure_ascii=False))
            return 0
        if args.apply:
            import os
            cache = args.apply.resolve()
            load(cache / "candidate.json")
            command, directory = isolated_updater(root)
            atomic_json(state_dir(root) / "helper.json", {"command": command, "directory": str(directory)})
            job = cache / "job.json"
            atomic_json(job, {"manifest": str(cache / "candidate.json"), "cache": str(cache), "parent_pid": os.getpid()})
            process = launch_process([*command, "--headless", "--apply-job", str(job)], cwd=directory)
            print(json.dumps({"installer_pid": process.pid, "log": str(log_dir / "updater.log")}))
            return 0
        if args.apply_job:
            parent_pid = read_json(args.apply_job).get("parent_pid", 0)
            deadline = time.monotonic() + 30
            while process_alive(parent_pid):
                if time.monotonic() >= deadline:
                    raise UpdateError("原更新器仍在运行，安装已暂停")
                time.sleep(0.1)
        lock = FileLock(state_dir(root) / "updater.lock")
        # handoff 等原进程真正退出释放运行库；同时保持单安装器实例。
        if not lock.acquire(timeout=30 if args.apply_job else 0):
            lock.close()
            return 0
        try:
            cleanup_helpers(root)
            if args.headless and args.apply_job:
                from PyQt6.QtCore import QCoreApplication
                from .bridge import request_instance
                from .coordinator import apply_update
                app = QCoreApplication([sys.argv[0]])
                job = read_json(args.apply_job)
                apply_update(root, load(Path(job["manifest"])), Path(job["cache"]), request=request_instance)
                logging.info("headless update completed")
                return 0
            from PyQt6.QtWidgets import QApplication
            from .gui import UpdateWindow
            app = QApplication([sys.argv[0]])
            app.setQuitOnLastWindowClosed(False)
            window = UpdateWindow(root, automatic=args.automatic, job=args.apply_job)
            app._update_window = window
            return app.exec()
        finally:
            if args.apply_job:
                retire_helper(root)
            lock.close()
    except Exception as exc:
        logging.exception("updater failed")
        if args.check or args.recover or args.prepare or args.apply or args.headless:
            print(str(exc), file=sys.stderr)
        elif not args.automatic:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication([sys.argv[0]])
            QMessageBox.warning(None, "更新未完成", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
