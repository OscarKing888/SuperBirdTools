"""照片列表与模板共用的编号规则，不依赖 Qt。"""

MAX_START_NUMBER = 999_999_999


def normalize_start_number(value: object) -> int:
    try:
        return max(1, min(MAX_START_NUMBER, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 1


def photo_row_number(row: int, start_number: int = 1) -> int:
    """按当前列表中从零开始的位置计算显示编号。"""
    return normalize_start_number(start_number) + row
