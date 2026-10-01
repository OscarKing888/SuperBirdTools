from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    from SuperBirdUpdater.main import main
    raise SystemExit(main())
