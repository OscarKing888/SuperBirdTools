# -*- coding: utf-8 -*-
"""Required video-preview payload for all Viewer PyInstaller entry points."""
from pathlib import Path
import sys


def collect_viewer_ffmpeg():
    # Optional imports in runtime code otherwise let PyInstaller silently build
    # an app that plays through Qt but cannot generate posters or read metadata.
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            f'SuperViewer 视频预览依赖缺失：请使用 {sys.executable} 安装 '
            'SuperViewer/requirements.txt 后重新打包。'
        ) from exc
    binary_dir = Path(imageio_ffmpeg.__file__).resolve().parent / 'binaries'
    binaries = [path for path in binary_dir.glob('ffmpeg*') if path.is_file()]
    if not binaries:
        raise RuntimeError(f'imageio-ffmpeg 未包含平台 FFmpeg 可执行文件：{binary_dir}')
    # Include the wheel payload explicitly, rather than depending on whether an
    # optional import was analyzed or a particular hook version was installed.
    return ([(str(path), 'imageio_ffmpeg/binaries') for path in binaries],
            ['imageio_ffmpeg', 'imageio_ffmpeg.binaries'])
