from pathlib import Path

import pytest

from build_tools import hardlink_dedupe


def _bundles(tmp_path: Path):
    roots = [tmp_path / "Viewer.app", tmp_path / "BirdStamp.app"]
    files = []
    for root in roots:
        root.mkdir()
        path = root / "runtime.bin"
        path.write_bytes(b"shared runtime payload")
        files.append(path)
    return roots, files


@pytest.mark.parametrize("operation", ["link", "replace"])
def test_failed_dedupe_preserves_both_artifacts(tmp_path, monkeypatch, operation):
    roots, files = _bundles(tmp_path)
    original = [path.read_bytes() for path in files]

    def fail(*args, **kwargs):
        raise OSError("simulated filesystem failure")

    monkeypatch.setattr(hardlink_dedupe.os, operation, fail)
    with pytest.raises(OSError, match="simulated filesystem failure"):
        hardlink_dedupe.dedupe(roots, min_size=1)

    assert [path.read_bytes() for path in files] == original
    assert all(list(root.iterdir()) == [path] for root, path in zip(roots, files))


def test_successful_dedupe_is_repeatable(tmp_path):
    roots, files = _bundles(tmp_path)
    payload = files[0].read_bytes()

    assert hardlink_dedupe.dedupe(roots, min_size=1) == (1, len(payload))
    assert files[0].samefile(files[1])
    assert [path.read_bytes() for path in files] == [payload, payload]
    assert hardlink_dedupe.dedupe(roots, min_size=1) == (0, 0)
    assert all(list(root.iterdir()) == [path] for root, path in zip(roots, files))
