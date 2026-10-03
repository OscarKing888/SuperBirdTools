"""SuperViewer 本地 RGB 降噪，公共入口不加载 Torch 或 Qt。"""
from .types import DenoiseOptions, DenoiseResult


def denoise_file(source, destination, options, *, cancelled=None, progress=None, engine=None):
    from .pipeline import denoise_file as process
    return process(source, destination, options, cancelled=cancelled, progress=progress, engine=engine)


__all__ = ["DenoiseOptions", "DenoiseResult", "denoise_file"]
