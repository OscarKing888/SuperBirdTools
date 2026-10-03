"""离线 NAFNet 推理、重叠分块融合和可取消的设备调度。

本模块可以安全地随界面导入；Torch 与模型只在后台首次 load/denoise 时加载。
CPU 模型只读共享，GPU 每次只执行一块，避免多张照片占满设备内存。
"""
from __future__ import annotations

from contextlib import contextmanager
import gc
import logging
from pathlib import Path
import threading

import numpy as np

from .types import check_cancelled

_LOG = logging.getLogger(__name__)
_TILE_SIZES = (512, 256, 128)


class _DeviceUnavailable(RuntimeError):
    pass


@contextmanager
def _acquire(lock, cancelled):
    """等待其他照片的模型载入或 GPU 分块时也能响应停止。"""
    check_cancelled(cancelled)
    while not lock.acquire(timeout=0.05):
        check_cancelled(cancelled)
    try:
        check_cancelled(cancelled)
        yield
    finally:
        lock.release()


def _tile_starts(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [0]
    starts = list(range(0, length - tile_size + 1, tile_size - overlap))
    if starts[-1] != length - tile_size:
        starts.append(length - tile_size)
    return starts


def _axis_weights(length: int, before: int, after: int) -> np.ndarray:
    """图像外边缘权重为 1；相邻块重叠部分用互补余弦渐变。"""
    weights = np.ones(length, dtype=np.float32)
    for overlap, at_end in ((before, False), (after, True)):
        overlap = min(length, overlap)
        if overlap <= 0:
            continue
        ramp = (1.0 - np.cos(np.pi * (np.arange(overlap) + 0.5) / overlap)) * 0.5
        if at_end:
            weights[-overlap:] *= ramp[::-1]
        else:
            weights[:overlap] *= ramp
    return weights


def _is_oom(error: Exception) -> bool:
    message = str(error).casefold()
    return any(text in message for text in (
        "out of memory", "cannot allocate memory", "can't allocate memory",
        "allocation failed", "not enough memory", "bad_alloc",
    ))


class DenoiseEngine:
    """一个批次共用一个引擎；close 等待实际推理结束后释放模型。"""

    def __init__(self, device: str = "auto", model_path=None):
        if device not in {"auto", "cuda", "mps", "cpu"}:
            raise ValueError(f"不支持的降噪设备：{device}")
        self.requested_device = device
        self.model_path = Path(model_path) if model_path is not None else None
        self._verified_path = None
        self._torch = None
        self._device = None
        self._models = {}
        self._disabled_devices = set()
        self._preferred_tiles = {}
        self._state = threading.Condition(threading.RLock())
        self._load_lock = threading.Lock()
        self._gpu_lock = threading.Lock()
        self._active = 0
        self._closed = False

    @contextmanager
    def _operation(self):
        with self._state:
            if self._closed:
                raise RuntimeError("降噪引擎已经关闭")
            self._active += 1
        try:
            yield
        finally:
            with self._state:
                self._active -= 1
                self._state.notify_all()

    def _candidate_devices(self):
        torch = self._torch
        choices = ("cuda", "mps") if self.requested_device == "auto" else (self.requested_device,)
        for device in choices:
            if device == "cpu":
                continue
            try:
                backend = torch.cuda if device == "cuda" else torch.backends.mps
                available = backend.is_available()
            except (AttributeError, RuntimeError) as exc:
                available = False
                _LOG.warning("NAFNet 无法检查 %s 设备，将回退：%s", device, exc)
            if available:
                yield device
            elif self.requested_device != "auto":
                _LOG.warning("NAFNet 请求的 %s 不可用，将使用 CPU", device)
        yield "cpu"

    def _create_model(self, device, cancelled):
        from .architecture import NAFNet
        from .models import resolve_model_path, verify_model

        if self._verified_path is None:
            path = self.model_path if self.model_path is not None else resolve_model_path()
            self._verified_path = verify_model(path)
        check_cancelled(cancelled)
        # 只从经哈希验证的本地文件读取权重，不执行 checkpoint 内的任意 Python。
        checkpoint = self._torch.load(self._verified_path, map_location="cpu", weights_only=True)
        parameters = checkpoint.get("params_ema", checkpoint.get("params", checkpoint))
        model = NAFNet()
        model.load_state_dict(parameters, strict=True)
        del parameters, checkpoint
        model.eval().requires_grad_(False)
        check_cancelled(cancelled)
        model.to(device=device, dtype=self._torch.float32)
        check_cancelled(cancelled)
        _LOG.info("NAFNet-SIDD width64 已载入，设备=%s，模型=%s", device, self._verified_path)
        return model

    def _load(self, cancelled):
        with self._state:
            if self._device is not None:
                return
        with _acquire(self._load_lock, cancelled):
            with self._state:
                if self._device is not None:
                    return
            import torch

            self._torch = torch
            # intra-op 线程数是进程级设置，不能在工作动作中修改，避免干扰鸟类分析。
            _LOG.info("NAFNet Torch CPU intra-op 线程数=%s", torch.get_num_threads())
            for device in self._candidate_devices():
                check_cancelled(cancelled)
                try:
                    model = self._create_model(device, cancelled)
                except RuntimeError:
                    if device == "cpu":
                        raise
                    _LOG.warning("NAFNet 初始化 %s 失败，将尝试后续设备", device, exc_info=True)
                else:
                    with self._state:
                        self._models[device] = model
                        self._device = device
                    return
                # traceback 清除后再回收部分载入的设备张量。
                self._empty_cache(device)

    def load(self, cancelled=None):
        with self._operation():
            check_cancelled(cancelled)
            self._load(cancelled)
        return self

    def _get_model(self, device, cancelled):
        with self._state:
            model = self._models.get(device)
        if model is not None:
            return model
        with _acquire(self._load_lock, cancelled):
            with self._state:
                model = self._models.get(device)
            if model is None:
                model = self._create_model(device, cancelled)
                with self._state:
                    self._models[device] = model
            return model

    def _empty_cache(self, device):
        if self._torch is None or device == "cpu":
            return
        try:
            getattr(self._torch, device).empty_cache()
        except (AttributeError, RuntimeError):
            _LOG.debug("NAFNet 无法清理 %s 缓存", device, exc_info=True)

    def _infer_tile(self, model, tile, device, cancelled):
        def infer():
            check_cancelled(cancelled)
            with self._state:
                if device in self._disabled_devices:
                    raise _DeviceUnavailable(f"{device} 已因前一张照片的推理错误停用")
            with self._torch.inference_mode():
                # 设备上只存在一块输入与其激活；整张累积图始终位于 CPU。
                tensor = self._torch.from_numpy(np.ascontiguousarray(tile.transpose(2, 0, 1)))
                tensor = tensor.unsqueeze(0).to(device=device, dtype=self._torch.float32)
                prediction = model(tensor)
                result = prediction.squeeze(0).permute(1, 2, 0).contiguous().cpu().numpy()
                # copy 解除结果对 Torch 张量存储的依赖，锁释放前完成设备同步。
                return result.copy()

        if device == "cpu":
            return infer()
        with _acquire(self._gpu_lock, cancelled):
            return infer()

    def _run_tiled(self, rgb, model, device, tile_size, cancelled, progress):
        height, width = rgb.shape[:2]
        overlap = min(128, tile_size // 2)
        ys = _tile_starts(height, tile_size, overlap)
        xs = _tile_starts(width, tile_size, overlap)
        accumulated = np.zeros_like(rgb, dtype=np.float32)
        total_weight = np.zeros((height, width), dtype=np.float32)
        total = len(ys) * len(xs)
        done = 0
        if progress is not None:
            progress(0, total)
        for row, y in enumerate(ys):
            bottom = min(height, y + tile_size)
            before_y = max(0, ys[row - 1] + tile_size - y) if row else 0
            after_y = max(0, bottom - ys[row + 1]) if row + 1 < len(ys) else 0
            wy = _axis_weights(bottom - y, before_y, after_y)
            for column, x in enumerate(xs):
                check_cancelled(cancelled)
                right = min(width, x + tile_size)
                before_x = max(0, xs[column - 1] + tile_size - x) if column else 0
                after_x = max(0, right - xs[column + 1]) if column + 1 < len(xs) else 0
                wx = _axis_weights(right - x, before_x, after_x)
                weight = wy[:, None] * wx[None, :]
                prediction = self._infer_tile(model, rgb[y:bottom, x:right], device, cancelled)
                if prediction.shape != (bottom - y, right - x, 3) or not np.isfinite(prediction).all():
                    raise RuntimeError("NAFNet 返回了无效的 RGB 像素")
                accumulated[y:bottom, x:right] += prediction * weight[:, :, None]
                total_weight[y:bottom, x:right] += weight
                done += 1
                if progress is not None:
                    progress(done, total)
        check_cancelled(cancelled)
        accumulated /= total_weight[:, :, None]
        np.clip(accumulated, 0.0, 1.0, out=accumulated)
        return accumulated

    def denoise(self, rgb, *, cancelled=None, progress=None):
        """返回 (float32 HWC sRGB, 实际设备, 实际分块大小)。

        progress(done_tiles, total_tiles) 在每次完整重试时从 0 重新开始。
        失败的整张累积图会丢弃，结果不会混用不同分块或设备的像素。
        """
        if not isinstance(rgb, np.ndarray) or rgb.dtype != np.float32:
            raise ValueError("NAFNet 输入必须是 float32 RGB 数组")
        if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) <= 0:
            raise ValueError("NAFNet 输入必须是非空 HWC 三通道 RGB")
        if not np.isfinite(rgb).all() or rgb.min() < 0.0 or rgb.max() > 1.0:
            raise ValueError("NAFNet 输入像素必须在 [0, 1] 内且为有限数值")
        with self._operation():
            check_cancelled(cancelled)
            self._load(cancelled)
            with self._state:
                first_device = "cpu" if self._device in self._disabled_devices else self._device
            devices = [first_device] if first_device == "cpu" else [first_device, "cpu"]
            for device in devices:
                model = self._get_model(device, cancelled)
                with self._state:
                    preferred = self._preferred_tiles.get(device, _TILE_SIZES[0])
                for tile_size in (size for size in _TILE_SIZES if size <= preferred):
                    try:
                        result = self._run_tiled(rgb, model, device, tile_size, cancelled, progress)
                    except RuntimeError as exc:
                        oom = _is_oom(exc)
                        if device == "cpu" and (not oom or tile_size == _TILE_SIZES[-1]):
                            raise
                        _LOG.warning(
                            "NAFNet 推理失败，整张重新处理：设备=%s，分块=%s，原因=%s",
                            device, tile_size, exc,
                        )
                    else:
                        with self._state:
                            self._preferred_tiles[device] = min(
                                tile_size, self._preferred_tiles.get(device, tile_size),
                            )
                        return result, device, tile_size
                    # 离开 except 后 traceback 不再持有失败分块的 GPU 张量。
                    check_cancelled(cancelled)
                    if device != "cpu":
                        with _acquire(self._gpu_lock, cancelled):
                            self._empty_cache(device)
                    if not oom:
                        break
                if device != "cpu":
                    with self._state:
                        self._disabled_devices.add(device)
                        if oom:
                            # 最小 GPU 分块仍不足时，CPU 也从最小分块开始，
                            # 避免整张照片再次经历高内存峰值与重复重试。
                            self._preferred_tiles["cpu"] = _TILE_SIZES[-1]
                    _LOG.warning("NAFNet 停用 %s，整张照片回退 CPU 推理", device)
            raise RuntimeError("NAFNet 没有可用的推理设备")

    def close(self):
        with self._state:
            self._closed = True
            while self._active:
                self._state.wait(timeout=0.05)
            devices = tuple(self._models)
            self._models.clear()
        gc.collect()
        for device in devices:
            self._empty_cache(device)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
