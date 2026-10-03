"""单张照片降噪入口，由 GUI worker 和 CLI 共用。"""
from __future__ import annotations

import logging
from pathlib import Path
import numpy as np

from .types import DenoiseOptions, DenoiseResult, DenoiseCancelled, UnsupportedImage, check_cancelled
from .image_io import decode_image
from .export import export_image

_LOG = logging.getLogger(__name__)


def denoise_file(source, destination, options: DenoiseOptions, *, cancelled=None,
                 progress=None, engine=None) -> DenoiseResult:
    source, destination = Path(source), Path(destination)
    own_engine = engine is None
    try:
        check_cancelled(cancelled)
        if not 0 <= options.strength <= 100:
            raise ValueError("降噪强度必须为 0–100")
        decoded = decode_image(source, cancelled=cancelled)
        if options.format == "jpeg" and decoded.alpha is not None and np.any(decoded.alpha < 1):
            raise ValueError("JPEG 不支持透明通道，请将降噪输出格式改为 TIFF")
        device, tile_size = "none", 0
        output = decoded.rgb
        if options.strength:
            if engine is None:
                from .engine import DenoiseEngine
                engine = DenoiseEngine(device=options.device)
            output, device, tile_size = engine.denoise(decoded.rgb, cancelled=cancelled, progress=progress)
            if output.shape != decoded.rgb.shape or not np.isfinite(output).all():
                raise ValueError("降噪模型返回了无效的像素结果")
            if options.strength < 100:
                output *= options.strength / 100
                output += decoded.rgb * (1 - options.strength / 100)
        check_cancelled(cancelled)
        export_image(source, destination, output, decoded.alpha, options, cancelled=cancelled)
        return DenoiseResult(str(source), str(destination), device=device, tile_size=tile_size)
    except DenoiseCancelled:
        return DenoiseResult(str(source), status="cancelled", error="已取消")
    except UnsupportedImage as exc:
        return DenoiseResult(str(source), status="skipped", error=str(exc))
    except Exception as exc:
        _LOG.exception("[Denoise] source=%s failed", source)
        return DenoiseResult(str(source), status="failed", error=str(exc))
    finally:
        if own_engine and engine is not None:
            engine.close()
