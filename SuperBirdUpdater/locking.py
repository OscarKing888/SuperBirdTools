from __future__ import annotations

import os
from pathlib import Path
import time

from .common import UpdateError


class FileLock:
    """内核持有的跨进程锁；进程退出自动释放，不删除锁文件避免 inode 竞争。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        if path.stat().st_size == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.locked = False

    def acquire(self, timeout: float = 0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.locked = True
                return True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)

    def close(self) -> None:
        if self.stream.closed:
            return
        if self.locked:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.locked = False
        self.stream.close()

    def __enter__(self):
        if not self.acquire():
            self.close()
            raise UpdateError("另一更新或启动操作正在进行")
        return self

    def __exit__(self, *_):
        self.close()
