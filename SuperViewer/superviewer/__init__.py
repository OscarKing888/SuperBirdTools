# -*- coding: utf-8 -*-
"""SuperViewer 子包：Qt 兼容、路径/配置、EXIF、焦点与预览加载、各 UI 控件与对话框。"""

from app_identity import load_app_identity

APP_INFO = load_app_identity("SuperViewer")
DEFAULT_APP_NAME = APP_INFO.app_name
__version__ = APP_INFO.version

__all__ = ["APP_INFO", "DEFAULT_APP_NAME", "__version__"]
