"""进程注册、启动闸门和独立更新器启动；只使用标准库。"""
from __future__ import annotations

import atexit
import ctypes
import getpass
import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .common import (APPS, CONFIG_NAME, INSTALLED_MANIFEST, UpdateError, atomic_json,
                     executable, platform_id, read_json, suite_root)
from .install import journal_path, recover, state_dir
from .locking import FileLock

_registration: dict | None = None


def process_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # 无权检查的进程视为仍在运行。
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def installation_id(root: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()[:24]


def user_state(root: Path) -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    path = base / "SuperBirdUpdater" / installation_id(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def active_instances(root: Path) -> list[dict]:
    directory = state_dir(root) / "instances"
    if not directory.exists():
        return []
    instances = []
    for file in directory.glob("*.json"):
        data = read_json(file)
        if data.get("app") not in APPS[:2] or data.get("root") != str(root.resolve()):
            raise UpdateError(f"应用注册信息无效: {file}")
        if process_alive(data.get("pid")):
            instances.append(data)
        else:
            file.unlink(missing_ok=True)
    return instances


def launch_process(command: list[str], *, cwd: Path, detached: bool = True):
    env = os.environ.copy()
    # PyInstaller 子进程必须作为新应用初始化，不能继承主应用的解包/动态库目录。
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("_MEIPASS2", None)
    kwargs = {"cwd": str(cwd), "env": env, "stdin": subprocess.DEVNULL,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        if detached:
            kwargs["creationflags"] |= subprocess.DETACHED_PROCESS
        # 清除 PyInstaller 设置的 DLL 搜索目录，随后恢复当前进程状态。
        buffer = ctypes.create_unicode_buffer(32768)
        kernel = ctypes.windll.kernel32
        kernel.GetDllDirectoryW(len(buffer), buffer)
        kernel.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(command, **kwargs)
        finally:
            kernel.SetDllDirectoryW(buffer.value or None)
    kwargs["start_new_session"] = detached
    return subprocess.Popen(command, **kwargs)


def updater_command(root: Path, *args: str) -> list[str]:
    helper = state_dir(root) / "helper.json"
    if "--wait-app" in args and helper.is_file():
        command = read_json(helper).get("command")
        if isinstance(command, list) and command and Path(command[0]).is_file():
            return [*command, *args]
    if getattr(sys, "frozen", False):
        program = executable(root, "SuperBirdUpdater")
        if not program.is_file():
            raise UpdateError("缺少独立更新器，请重新下载并解压完整套件")
        return [str(program), "--root", str(root), *args]
    return [sys.executable, "-m", "SuperBirdUpdater", "--root", str(root), *args]


def start_updater(root: Path, *, automatic: bool = False) -> None:
    command = updater_command(root, *( ["--automatic"] if automatic else []))
    launch_process(command, cwd=root if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1])


def admit_startup(app: str) -> dict | None:
    """入口在业务模块导入前注册；安装/恢复时将启动交给独立等待进程。"""
    global _registration
    if _registration is not None:
        return _registration
    if not getattr(sys, "frozen", False):
        return None
    root = suite_root(Path(sys.executable))
    if not (root / INSTALLED_MANIFEST).is_file():
        return None  # 兼容独立单应用构建。
    try:
        gate = FileLock(state_dir(root) / "launch.lock")
    except OSError:
        logging.getLogger(__name__).warning("Read-only installation: automatic update unavailable")
        return None
    if not gate.acquire():
        gate.close()
        launch_process(updater_command(root, "--wait-app", app, "--resume-args", *sys.argv[1:]), cwd=root)
        raise SystemExit(0)
    try:
        if journal_path(root).exists():
            # 主应用可能已加载部分待恢复运行库，不在自身进程执行恢复。
            launch_process(updater_command(root, "--wait-app", app, "--resume-args", *sys.argv[1:]), cwd=root)
            raise SystemExit(0)
        token = uuid.uuid4().hex
        user = getpass.getuser()
        server = "sbt-" + hashlib.sha256(f"{installation_id(root)}:{user}:{token}".encode()).hexdigest()[:36]
        data = {"root": str(root), "app": app, "pid": os.getpid(), "server": server, "token": token,
                "user": user, "created": time.time()}
        file = state_dir(root) / "instances" / f"{token}.json"
        atomic_json(file, data)
        _registration = data
        atexit.register(lambda: file.unlink(missing_ok=True))
        return data
    finally:
        gate.close()


def get_registration() -> dict | None:
    return _registration


def isolated_updater(root: Path) -> tuple[list[str], Path]:
    """复制完整独立运行库到安装目录外，支持更新器自身替换与失败恢复。"""
    directory = Path(tempfile.mkdtemp(prefix="SuperBirdUpdater-"))
    try:
        if getattr(sys, "frozen", False):
            component = root / ("SuperBirdUpdater.app" if platform_id() == "macos" else "SuperBirdUpdater")
            shutil.copytree(component, directory / component.name, symlinks=True)
            program = executable(directory, "SuperBirdUpdater")
            command = [str(program), "--root", str(root)]
        else:
            # 开发/集成测试使用隔离后的模块快照。
            shutil.copytree(Path(__file__).resolve().parent, directory / "SuperBirdUpdater",
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
            command = [sys.executable, str(directory / "SuperBirdUpdater" / "entry.py"), "--root", str(root)]
        return command, directory
    except Exception:
        shutil.rmtree(directory)
        raise


def wait_and_relaunch(root: Path, app: str, arguments: list[str] | None = None) -> None:
    # 文件锁的 wait 不依赖 Qt，等待进程不会持有待替换的主应用运行库。
    with_lock = FileLock(state_dir(root) / "launch.lock")
    try:
        if not with_lock.acquire(timeout=600):
            raise UpdateError("等待更新完成超时，请稍后重新启动应用")
        if journal_path(root).exists():
            if active_instances(root):
                raise UpdateError("仍有应用运行，不能恢复更新事务")
            recover(root)
    finally:
        with_lock.close()
    launch_process([str(executable(root, app)), *(arguments or [])], cwd=root)


def retire_helper(root: Path) -> None:
    """自身运行库不能在 Windows 原地删除，交给下一次更新器启动清理。"""
    marker = state_dir(root) / "helper.json"
    if journal_path(root).exists() or not marker.exists():
        return
    helper = read_json(marker)
    directory = Path(helper["directory"]).resolve()
    if not Path(__file__).resolve().is_relative_to(directory) and not Path(sys.executable).resolve().is_relative_to(directory):
        return
    path = user_state(root) / "cleanup.json"
    pending = read_json(path) if path.exists() else []
    pending.append({"directory": str(directory), "pid": os.getpid()})
    atomic_json(path, pending)
    marker.unlink(missing_ok=True)


def cleanup_helpers(root: Path) -> None:
    path = user_state(root) / "cleanup.json"
    if not path.exists():
        return
    retained = []
    temporary_root = Path(tempfile.gettempdir()).resolve()
    for item in read_json(path):
        directory = Path(item["directory"]).resolve()
        if directory.parent != temporary_root or not directory.name.startswith("SuperBirdUpdater-"):
            continue
        if process_alive(item["pid"]):
            retained.append(item)
            continue
        try:
            if directory.exists():
                shutil.rmtree(directory)
        except OSError:
            retained.append(item)
    atomic_json(path, retained)
