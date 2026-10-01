from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from SuperBirdUpdater.common import UpdateError, hash_file
from SuperBirdUpdater.manifest import MAX_ASSET_SIZE, load


def validate_release(directory: Path) -> None:
    files = [p for p in directory.iterdir() if p.is_file()]
    if len(files) + (not (directory / "SHA256SUMS.txt").exists()) > 1000:
        raise UpdateError("Release 附件总数超过 1000")
    if any(p.stat().st_size > MAX_ASSET_SIZE for p in files):
        raise UpdateError("Release 存在超过 2 GiB 的附件")
    manifests = [load(directory / f"update-{platform}-{arch}.json")
                 for platform, arch in (("windows", "x86_64"), ("macos", "arm64"))]
    if len({(m["commit"], m["revision"], m["app_common_commit"]) for m in manifests}) != 1:
        raise UpdateError("不同平台的更新产物不是同一源码版本")
    for manifest in manifests:
        for name, asset in manifest["assets"].items():
            hashes = hash_file(directory / name)
            if any(hashes[k] != asset[k] for k in ("size", "sha256")):
                raise UpdateError(f"更新附件损坏: {name}")
        package = directory / f"SuperBirdTools-{manifest['version']}-{manifest['platform']}-{manifest['arch']}.zip"
        if not package.is_file():
            raise UpdateError(f"缺少首次安装包: {package.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="发布前验证两个平台的更新附件完整性")
    parser.add_argument("directory", type=Path)
    validate_release(parser.parse_args().directory)
