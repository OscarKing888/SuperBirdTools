from __future__ import annotations

import locale


def decode_subprocess_output(data: bytes | None) -> str:
    if not data:
        return ""

    preferred = locale.getpreferredencoding(False) or "utf-8"
    encodings = ["utf-8", preferred, "gbk", "latin-1"]
    seen: set[str] = set()
    for encoding in encodings:
        normalized = encoding.lower().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            return data.decode(normalized)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")

