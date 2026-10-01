from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from SuperBirdUpdater.build import generate, git_identity, package_suite
from SuperBirdUpdater.common import CONFIG_NAME, architecture, platform_id


def main() -> None:
    parser = argparse.ArgumentParser(description="生成短 commit 版本、并行 MD5 清单和增量更新载荷")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--platform", default=platform_id(), choices=["macos", "windows", "linux"])
    parser.add_argument("--arch", default=architecture())
    parser.add_argument("--hash-workers", type=int)
    parser.add_argument("--volume-mib", type=int, default=1024)
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    output = args.output or args.dist / "updates"
    data = generate(args.dist, output, git_identity(args.repo), args.platform, args.arch,
                    args.hash_workers, args.volume_mib * 1024**2)
    config = args.dist / CONFIG_NAME
    if not config.exists():
        shutil.copy2(args.repo / "SuperBirdUpdater" / CONFIG_NAME, config)
    if args.package:
        print(package_suite(args.dist, output, data, args.repo))
    print(f"Update {data['version']}: {len(data['files'])} entries, {len(data['assets'])} volumes → {output}")


if __name__ == "__main__":
    main()
