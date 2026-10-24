"""Prepare and verify the pinned Windows CUDA build dependencies."""
from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys


CUDA_VERSION = "12.8"
PACKAGES = {"torch": "2.10.0+cu128", "torchvision": "0.25.0+cu128"}
INDEX_URL = "https://download.pytorch.org/whl/cu128"


def installed_pair() -> bool:
    # Do not load DLLs before pip replaces them on Windows.
    try:
        return all(importlib.metadata.version(name) == version for name, version in PACKAGES.items())
    except importlib.metadata.PackageNotFoundError:
        return False


def prepare(*, install: bool = False) -> None:
    if sys.platform != "win32":
        raise RuntimeError("This build entry requires Windows.")
    if not installed_pair():
        if not install:
            raise RuntimeError("Expected torch 2.10.0+cu128 and torchvision 0.25.0+cu128. Run build_all_cuda.bat to prepare them.")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade",
             *(f"{name}=={version}" for name, version in PACKAGES.items()),
             "--index-url", INDEX_URL], check=True,
        )

    import torch
    import torchvision

    if torch.version.cuda != CUDA_VERSION:
        raise RuntimeError(f"Expected CUDA {CUDA_VERSION} Torch; found {torch.__version__}, CUDA={torch.version.cuda!r}.")
    print(f"[CUDA] torch={torch.__version__}, torchvision={torchvision.__version__}, runtime={torch.version.cuda}")
    # Exercise the compiled torchvision operator, not just the Python imports.
    for device in (["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"]):
        boxes = torch.tensor([[0., 0., 8., 8.], [1., 1., 7., 7.]], device=device)
        scores = torch.tensor([.9, .8], device=device)
        kept = torchvision.ops.nms(boxes, scores, .5)
        if kept.cpu().tolist() != [0]:
            raise RuntimeError(f"Torchvision NMS check failed on {device}.")
        print(f"[CUDA] {device} operator check passed")
    if not torch.cuda.is_available():
        print("[CUDA] No usable GPU on this build host; CUDA libraries will be bundled, GPU inference is unverified.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install the pinned CUDA wheels if needed")
    args = parser.parse_args(argv)
    try:
        prepare(install=args.install)
    except (RuntimeError, ImportError, OSError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] CUDA build preparation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
