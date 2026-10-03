"""有界的本机降噪成片索引；源照片及其 XMP 始终不因降噪而修改。"""
from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import tempfile
import threading

from app_common.log import get_logger

_log = get_logger("image_denoise.viewer")


class DenoisePreviewHistory:
    """启动时载入小型索引，成功导出的 worker 原子保存；预览热路径不读写它。"""

    def __init__(self, path: Path, *, limit: int = 2048):
        self.path = Path(path)
        self.limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        try:
            # 索引仅是查找提示，最终来源指纹和成片有效性仍由解码 worker 核验。
            if self.path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("降噪成片索引超出大小上限")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("降噪成片索引格式无效")
            for item in payload[-self.limit:]:
                if (isinstance(item, list) and len(item) == 2
                        and all(isinstance(value, str) and value for value in item)):
                    source, destination = item
                    self._entries[self._key(source)] = (source, destination)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            _log.warning("[Denoise] cannot read preview history %s: %s", self.path, exc)

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def entries(self):
        with self._lock:
            return tuple(self._entries.values())

    def record(self, source: str, destination: str) -> None:
        """在后台调用；索引保存失败不会否定已安全发布的成片。"""
        with self._lock:
            key = self._key(source)
            self._entries[key] = (str(source), str(destination))
            self._entries.move_to_end(key)
            while len(self._entries) > self.limit:
                self._entries.popitem(last=False)
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=".denoise-preview-", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = stream.name
                    json.dump(list(self._entries.values()), stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except (OSError, ValueError) as exc:
                _log.warning("[Denoise] cannot save preview history %s: %s", self.path, exc)
            finally:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        _log.warning("[Denoise] preview history temporary file retained %s: %s", temporary, exc)
