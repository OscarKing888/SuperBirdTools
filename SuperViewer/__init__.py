# -*- coding: utf-8 -*-
"""SuperViewer 单仓模块入口。入口与主窗口可从 main 子模块导入。"""

try:
    from .main import MainWindow, main
except ImportError:
    MainWindow = None  # type: ignore[misc, assignment]
    main = None  # type: ignore[misc, assignment]

__all__ = ["MainWindow", "main"]
