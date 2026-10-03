"""目录快照、跨平台输出命名及带内存预算的批量降噪协调器。"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import os
import logging
from pathlib import Path
import re
import threading
import unicodedata

from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS

from .types import DenoiseCancelled, DenoiseOptions, DenoiseResult, UnsupportedImage, check_cancelled

GIB = 1024 ** 3
MIB = 1024 ** 2
_PORTABLE_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
_OUTPUT_STEM = re.compile(r"_denoised(?:_[0-9]+)?$", re.I)
_log = logging.getLogger("image_denoise.batch")


def validate_options(options: DenoiseOptions) -> None:
    if options.output_mode not in {"source_subdir", "fixed", "ask"}:
        raise ValueError("无效的降噪输出位置模式")
    name = options.subdir
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or name != name.strip() or name.endswith(".")
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or _PORTABLE_RESERVED.match(name)):
        raise ValueError("降噪子目录名称必须是兼容 Windows 的单个目录名")
    if options.output_mode in {"fixed", "ask"} and not options.output_directory.strip():
        raise ValueError("请先选择降噪输出目录")
    if options.format not in {"tiff", "jpeg"}:
        raise ValueError("降噪输出格式必须为 TIFF 或 JPEG")
    if not 0 <= options.strength <= 100:
        raise ValueError("降噪强度必须为 0–100")
    if not 1 <= options.workers <= 4:
        raise ValueError("降噪并行照片数必须为 1–4")
    if options.device not in {"auto", "cpu", "cuda", "mps"}:
        raise ValueError("无效的降噪计算设备")


def _canonical(path) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def _portable_key(name) -> str:
    return unicodedata.normalize("NFC", str(name)).casefold()


def collect_image_paths(inputs, *, recursive=False, options=None, cancelled=None) -> list[str]:
    """先取得输入快照，跳过缓存、输出目录和符号链接目录，避免递归处理成片。"""
    options = options or DenoiseOptions()
    excluded_names = {_portable_key("denoised"), _portable_key(options.subdir)}
    output = _canonical(options.output_directory) if options.output_directory else ""
    found, seen = [], set()

    def append(path):
        check_cancelled(cancelled)
        key = _canonical(path)
        if Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS and key not in seen:
            seen.add(key)
            found.append(os.path.abspath(os.fspath(path)))

    def excluded(path):
        return (path.name.startswith(".") or _portable_key(path.name) in excluded_names
                or bool(output and _canonical(path) == output) or path.is_symlink())

    def scan(directory):
        check_cancelled(cancelled)
        # 权限错误由调用方报告，不能把未完整扫描的目录伪装成成功。
        for child in sorted(directory.iterdir(), key=lambda p: (_portable_key(p.name), p.name)):
            check_cancelled(cancelled)
            if child.name.startswith("."):
                continue
            if child.is_file():
                # 固定输出目录也可能就是输入目录；只在目录扫描时跳过历史成片。
                if not _OUTPUT_STEM.search(child.stem):
                    append(child)
            elif recursive and child.is_dir() and not excluded(child):
                scan(child)

    for item in inputs:
        check_cancelled(cancelled)
        path = Path(item).expanduser()
        if path.is_file():
            append(path)
        elif path.is_dir():
            scan(path)
    return found


def allocate_destinations(paths, options: DenoiseOptions) -> list[str]:
    """提交工作前预分配整批名称；图像/XMP 一起做 NFC 和大小写无关冲突检查。"""
    validate_options(options)
    allocated, occupied = [], {}
    extension = ".tif" if options.format == "tiff" else ".jpg"
    for source in map(Path, paths):
        directory = (source.parent / options.subdir if options.output_mode == "source_subdir"
                     else Path(options.output_directory).expanduser())
        directory = directory.resolve()
        key = _portable_key(directory)
        if key not in occupied:
            try:
                occupied[key] = {_portable_key(p.name) for p in directory.iterdir()}
            except OSError:
                # 无法列举某张的输出位置时，交给单张导出报告错误；不能中断其他源目录。
                # 导出仍会检查现有图像/XMP，并以不覆盖方式发布文件。
                occupied[key] = set()
        used = occupied[key]
        stem = f"{source.stem}_denoised"
        count = 1
        while True:
            name = stem if count == 1 else f"{stem}_{count}"
            image_name, sidecar_name = name + extension, name + ".xmp"
            if _portable_key(image_name) not in used and _portable_key(sidecar_name) not in used:
                used.update((_portable_key(image_name), _portable_key(sidecar_name)))
                allocated.append(str(directory / image_name))
                break
            count += 1
    return allocated


def memory_snapshot() -> tuple[int, int]:
    """返回物理内存与当前可用量；探测失败时采用保守的单图预算。"""
    try:
        import psutil

        memory = psutil.virtual_memory()
        return int(memory.total), int(memory.available)
    except Exception:
        return 8 * GIB, 4 * GIB


def estimate_image_memory(width: int, height: int) -> int:
    if width <= 0 or height <= 0:
        raise ValueError("无法确定图片尺寸")
    return 48 * int(width) * int(height) + 512 * MIB


@dataclass(frozen=True)
class _Item:
    index: int
    source: str
    destination: str
    memory: int


class _InlineExecutor:
    """GUI 的共享池无分析容量时，由协调线程串行执行，不创建另一个池。"""
    def submit(self, callback):
        future = Future()
        future.set_running_or_notify_cancel()
        try:
            future.set_result(callback())
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, **_kwargs):
        pass


def _notify(callback, *args):
    if callback is not None:
        try:
            callback(*args)
        except Exception:
            _log.exception("[Denoise] progress callback failed")


def run_batch(paths, options: DenoiseOptions, *, pool=None, engine=None, cancelled=None,
              on_result=None, on_progress=None, on_status=None, on_tile=None,
              probe=None, processor=None, memory_provider=None, serial=False) -> list[DenoiseResult]:
    """结果按输入顺序返回，回调在协调线程按完成顺序触发。

    GUI 传入浏览器池；CLI 在当前协调线程创建并关闭有界 executor。
    内存在提交前预留，排队动作也计入预算，不让池线程等待内存锁。
    此函数返回时，本批次所有已提交动作都已完成或撤回。
    """
    validate_options(options)
    paths = list(dict.fromkeys(os.path.abspath(os.fspath(p)) for p in paths))
    destinations = allocate_destinations(paths, options)
    if probe is None:
        from .image_io import probe_image

        probe = probe_image
    memory_provider = memory_provider or memory_snapshot
    total_ram, available_ram = memory_provider()
    budget = min(8 * GIB, int(total_ram * 0.25), int(available_ram * 0.5))
    stopped = threading.Event()
    is_cancelled = lambda: stopped.is_set() or bool(cancelled and cancelled())
    results, items = {}, []
    completed = 0

    def report(index, result):
        nonlocal completed
        results[index] = result
        completed += 1
        _notify(on_result, result)
        _notify(on_progress, completed, len(paths), result.source)

    _notify(on_progress, 0, len(paths), "")
    for index, (path, destination) in enumerate(zip(paths, destinations)):
        if is_cancelled():
            break
        try:
            width, height = probe(path)
            items.append(_Item(index, path, destination, estimate_image_memory(width, height)))
        except UnsupportedImage as exc:
            report(index, DenoiseResult(path, destination, "skipped", str(exc)))
        except Exception as exc:
            report(index, DenoiseResult(path, destination, "failed", str(exc)))
    if not items or is_cancelled():
        return [results.get(i, DenoiseResult(p, destinations[i], "cancelled"))
                for i, p in enumerate(paths)]

    owns_engine = engine is None and bool(options.strength)
    if owns_engine:
        from .engine import DenoiseEngine

        engine = DenoiseEngine(options.device)
    executor, token = None, None
    pending, next_index, reserved = {}, 0, 0
    workers = max(1, min(options.workers, getattr(pool, "analysis_workers", options.workers)))
    if serial and pool is None:
        workers = 1
    try:
        if engine is not None and options.strength:
            _notify(on_status, "正在加载降噪模型…")
            engine.load(cancelled=is_cancelled)
        if pool is not None:
            from app_common.file_browser._work_policy import WorkKind

            token = pool.begin_producer(WorkKind.ANALYSIS)
        else:
            executor = (_InlineExecutor() if serial else
                        ThreadPoolExecutor(max_workers=workers, thread_name_prefix="image-denoise"))
        _notify(on_status, f"正在降噪（最多 {workers} 张并行，内存预算 {budget / GIB:.1f} GB）…")
        cancellation_sent = False
        while pending or (next_index < len(items) and not is_cancelled()):
            while next_index < len(items) and len(pending) < workers and not is_cancelled():
                item = items[next_index]
                if reserved + item.memory > budget:
                    if pending:
                        break
                    # 单张超过整批预算也不能强行放行；明确失败，避免永久等待或挤占浏览内存。
                    if item.memory > budget:
                        report(item.index, DenoiseResult(item.source, item.destination, "failed",
                               f"可用内存不足：此图片预计需要 {item.memory / GIB:.1f} GB"))
                        next_index += 1
                        continue
                tile_callback = lambda d, t, p=item.source: _notify(on_tile, p, d, t)
                if pool is not None:
                    # 共享 WorkerAction 入口的包会加载 Qt，只允许 GUI 分支导入。
                    from .actions import DenoiseAction

                    action = DenoiseAction(item.source, item.destination, options, engine=engine,
                                           cancelled=is_cancelled, processor=processor,
                                           progress=tile_callback)
                    future = pool.submit_action(action, kind=WorkKind.ANALYSIS)
                else:
                    def execute(current=item, progress=tile_callback):
                        if is_cancelled():
                            return DenoiseResult(current.source, current.destination, "cancelled")
                        process = processor
                        if process is None:
                            from .pipeline import denoise_file

                            process = denoise_file
                        return process(current.source, current.destination, options, engine=engine,
                                       cancelled=is_cancelled, progress=progress)

                    future = executor.submit(execute)
                pending[future] = item
                reserved += item.memory
                next_index += 1
            if not pending:
                continue
            if is_cancelled() and not cancellation_sent:
                cancellation_sent = True
                for future in pending:
                    pool.cancel(future) if pool is not None else future.cancel()
            finished, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in finished:
                item = pending.pop(future)
                reserved -= item.memory
                if future.cancelled():
                    result = DenoiseResult(item.source, item.destination, "cancelled")
                else:
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = DenoiseResult(item.source, item.destination, "failed", str(exc))
                report(item.index, result)
    except DenoiseCancelled:
        stopped.set()
    except Exception as exc:
        stopped.set()
        # 加载失败或浏览器池已关闭；保留成功结果并为尚未提交的照片报告原因。
        for item in items[next_index:]:
            report(item.index, DenoiseResult(item.source, item.destination,
                   "cancelled" if cancelled and cancelled() else "failed", str(exc)))
    finally:
        stopped.set()
        # 无论异常发生在提交还是回调，都先撤回队列并等待运行中的动作，才释放模型。
        for future in pending:
            pool.cancel(future) if pool is not None else future.cancel()
        if pending:
            wait(pending)
            for future, item in pending.items():
                if item.index not in results:
                    if future.cancelled():
                        result = DenoiseResult(item.source, item.destination, "cancelled")
                    else:
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = DenoiseResult(item.source, item.destination, "failed", str(exc))
                    report(item.index, result)
        if token is not None:
            pool.end_producer(WorkKind.ANALYSIS, token)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if owns_engine:
            engine.close()
    return [results.get(i, DenoiseResult(p, destinations[i], "cancelled"))
            for i, p in enumerate(paths)]
