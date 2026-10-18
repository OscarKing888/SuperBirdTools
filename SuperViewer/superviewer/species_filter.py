# -*- coding: utf-8 -*-
"""鸟种过滤键：去掉识别状态和置信度，保留原始元数据。"""
from __future__ import annotations

import re


_STATUS = r"(?:待确定|待确认|未确定|未确认|已确定|已确认|疑似)"
_CONFIDENCE = r"(?:(?:置信度|可信度)\s*[:：]?\s*)?\d+(?:\.\d+)?\s*[%％]"
_NOTE = rf"(?:{_STATUS}(?:\s*[:：]?\s*{_CONFIDENCE})?|{_CONFIDENCE})"
_SUFFIX = re.compile(rf"(?:[（(【\[]\s*{_NOTE}\s*[）)】\]]|{_NOTE})\s*$")


def species_filter_name(value: str | None) -> str:
    """同一鸟名的待确定/置信度变体合并；不截断其它鸟名或任意括号内容。"""
    name = str(value or "").strip()
    while match := _SUFFIX.search(name):
        name = name[:match.start()].rstrip(" \t\r\n·•,，;；:：-—_")
    return name
