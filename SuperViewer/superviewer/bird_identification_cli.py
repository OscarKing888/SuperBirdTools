# -*- coding: utf-8 -*-
"""识鸟 CLI 入口，避免 Viewer 包预加载核心模块时的 runpy 重复执行。"""
from .bird_identification import main

if __name__ == "__main__":
    raise SystemExit(main())
