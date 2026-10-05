"""Selectable models: YOLO bird detectors (boxes or masks) and SAM2 mask refiners.

Weights are never committed. Besides the existing lookup directories
(:func:`bird_sharpness.models.find_model`) every model can be downloaded on
request into a per-user directory (:func:`user_model_dir`) from the Ultralytics
assets release that the installed ``ultralytics`` package itself uses.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

AUTO_DETECTOR = "auto"  # the built-in choice: yolo11l-seg ... yolo11n-seg, else a box detector
RELEASE_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0"
SIZE_LABELS = {"n": "nano", "s": "small", "m": "medium", "l": "large", "x": "xlarge",
               "t": "tiny", "b": "base+"}

# Exact size (bytes) and SHA-256 of every v8.4.0 release asset (GitHub release digests):
# downloads and the workspace prefetch (build_tools/download_models.py) are verified against them.
_ASSETS = {
    "sam2.1_b": (161935802, "f1a9cf2dd69d84bb463b5ad98246d03e2d47a130a9295db0ec967e6cd95e2e47"),
    "sam2.1_l": (449239354, "ab7e1ac9cb9f6eb3bcf197ece044f06a707ec49129361a2b47e93e1db6989efd"),
    "sam2.1_s": (92319866, "60f9e43f1307be192eef341437e02c40f32cd61cf36a97a203a0998a2952873a"),
    "sam2.1_t": (78105722, "3c1e81ca9b037dd39d70a014ddb9a813d6c4c4e12555420db7eaff31689bd4e3"),
    "sam2_b": (161894082, "39722bb0ce2a086058cf64e50dffd6f9e9931b5fcbee79e33b30441c6c40264d"),
    "sam2_l": (449203114, "fd618bcfc7b84c8f2e0a6997548e197b907098196dd075387d01f64d9cf8a93b"),
    "sam2_s": (92278178, "6ba91d739a6adfc1dcaf76336b2f380f3eae8d24a88f56c62798907f7fd62dad"),
    "sam2_t": (78064050, "94375f988270836169320bd901960c67b5770e8bef3867d70102f01a8b5ca501"),
    "yolo11l-seg": (56096965, "cabe90049795dfc9a370b7934d6dec7f6b9e44a20e573b0ff81b7e205512c872"),
    "yolo11l": (51387343, "9ebd0e09d59811db4b1d61e2bc6730649608b1ac47f8dd01e2da6bca7c20023f"),
    "yolo11m-seg": (45400152, "eb9a06f63e2206c35d68d839b08c362429ebecf933ad54c1ad68b2fd001c17cf"),
    "yolo11m": (40684120, "d5ffc1a674953a08e11a8d21e022781b1b23a19b730afc309290bd9fb5305b95"),
    "yolo11n-seg": (6182636, "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"),
    "yolo11n": (5613764, "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"),
    "yolo11s-seg": (20669228, "1caa81c0195412efa411b632bcfb8c184939dddb6ae41f6a80c41b211ff257c3"),
    "yolo11s": (19313732, "85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5"),
    "yolo11x-seg": (125090821, "4e53a5f5fd3ee2ae3361c62169c6bb3ed4ae251dd0e57606e230955aa52d919c"),
    "yolo11x": (114636239, "7bc158aa95c0ebfdd87f70f01653c1131b93e92522dbe15c228bcd742e773a24"),
    "yolo12l": (53699086, "0babd8dc8f775bb64bb052debdff3d8b9e9b57efa9d7bfa11c84bb82c3fec336"),
    "yolo12m": (40904955, "4c6d179786eddf6134ee469ae2f4ce04cbe4e9d1a47d6b669d9cd6b9c6c513d8"),
    "yolo12n": (5595063, "419ff3dca37d69bacc93a50fa0c186a1c6f9fe62fae0f108b0872829689e9ca6"),
    "yolo12s": (19006455, "e915c2c4286e3f6f8610ef106fa3f94a7b8c19b30eccede5887e22c33ef75f58"),
    "yolo12x": (119322638, "682ce8dadee004dbe964950f1bf3eda451671815a6ed62db80b398916b9b7c6f"),
    "yolo26l-seg": (63700037, "636024306410afa1732692322fba57d22ea2b1c2f07613fcee131a93d7dd380c"),
    "yolo26l": (53211173, "9fe3c544f2b19bebad7ea41e76d7ad3d88b7c2f10d11d24430c5311f6b32db26"),
    "yolo26m-seg": (54750385, "16b636f04e8fb6a325b3370f22dc5e5535ff473e384f4d041fd28d788f6ee9f5"),
    "yolo26m": (44255705, "401cea9ab23ad19246ff7744859816bc599f350e93c9dd30367b6f0a0745d0b7"),
    "yolo26n-seg": (6719965, "361fbfabab285c3237700b6bb91d7ecfa602cd945fffda8dbe1242829b71e73f"),
    "yolo26n": (5544453, "9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef"),
    "yolo26s-seg": (23467933, "3da1d83e31caec96f9300eb4064f4f62882c133c7c264d63dfe61a7c197837a4"),
    "yolo26s": (20422725, "646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b"),
    "yolo26x-seg": (142129861, "92b3de0065766a17180d6219858717dc9d03cdce8a3ca9576c97fd75aabb64f3"),
    "yolo26x": (118667365, "9fdd44a31c504547ffb81d2c6d9e6dac3493c8eaa8b0398d3f43bae6c7003e92"),
    "yolov8l-seg": (92417004, "ca3ec94c445aeaf79c2b57982fb451060f18a7561e2c20b4d26bb9dc1fcabf93"),
    "yolov8l": (87792836, "64c9115303f6a25575f82200d1b22ec409fa6bd7d08d0313884fc20d919478cd"),
    "yolov8m-seg": (54921020, "51fa7e5ef385efa6d5b1d8e31b73399be6ed5d7ca71bda4bd4b2794bb445c4f4"),
    "yolov8m": (52136884, "5d4a90cdc7a21786cc59cd19778e9eafff836df9e2da32524737c7ee6efe4fe5"),
    "yolov8n-seg": (7071756, "a7cd8f929e1903d78a12a48efecab430209f18dc46cb96c3599a5980c63c423c"),
    "yolov8n": (6549796, "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36"),
    "yolov8s-seg": (23914764, "0bac0770b55e5eb5b76a61bc535673288dcec36c2bc0cd25ee0d584c632f3413"),
    "yolov8s": (22588772, "1f47a78bf100391c2a140b7ac73a1caae18c32779be7d310658112f7ac9aa78a"),
    "yolov8x-seg": (144101612, "c333d6a7aff884ec821050eacdab29871f3edd10d996cf48746a02f8e01f1b5d"),
    "yolov8x": (136890692, "3df4ada6b4dad6d657868f2fdf7faecfb34dcfccf3a25c4b82079064718524c8"),
}


@dataclass(frozen=True)
class CatalogModel:
    name: str        # file name, e.g. "yolo11x-seg.pt"
    kind: str        # "detector" | "sam"
    family: str      # "YOLO11", "YOLO26", "YOLO12", "YOLOv8", "SAM2.1", "SAM2"
    size: str        # n/s/m/l/x (YOLO), t/s/b/l (SAM)
    masks: bool      # detector with segmentation masks (SAM always gives masks)
    size_bytes: int
    sha256: str

    @property
    def megabytes(self) -> float:
        return round(self.size_bytes / 1e6, 1)

    @property
    def label(self) -> str:
        what = "" if self.kind == "sam" else (" 分割" if self.masks else " 检测框")
        return f"{self.family} {SIZE_LABELS.get(self.size, self.size)}{what}（{self.megabytes:g} MB）"


def _detectors() -> Tuple[CatalogModel, ...]:
    out = []
    for family, stem, seg in (("YOLO11", "yolo11", True), ("YOLO26", "yolo26", True),
                              ("YOLO12", "yolo12", False), ("YOLOv8", "yolov8", True)):
        for masks in ((True, False) if seg else (False,)):
            for size in "nsmlx":
                key = f"{stem}{size}{'-seg' if masks else ''}"
                out.append(CatalogModel(f"{key}.pt", "detector", family, size, masks, *_ASSETS[key]))
    return tuple(out)


DETECTORS: Tuple[CatalogModel, ...] = _detectors()
SAM_MODELS: Tuple[CatalogModel, ...] = tuple(
    CatalogModel(f"{stem}_{size}.pt", "sam", family, size, True, *_ASSETS[f"{stem}_{size}"])
    for family, stem in (("SAM2.1", "sam2.1"), ("SAM2", "sam2")) for size in "tsbl")
_BY_NAME = {m.name: m for m in (*DETECTORS, *SAM_MODELS)}


def catalog_model(name: str) -> Optional[CatalogModel]:
    return _BY_NAME.get(str(name or ""))


def user_model_dir() -> Path:
    """Per-user directory for downloaded models (writable also when the app bundle is not)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "SuperBirdTools" / "models"


def locate(name: str) -> Optional[Path]:
    """Where ``name`` is installed (any lookup directory), else ``None``."""
    from .models import find_model

    return find_model((name,)) if name else None


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, name: Optional[str] = None) -> bool:
    """Whether ``path`` is the complete, unmodified catalog model (size, then SHA-256)."""
    model = catalog_model(name or Path(path).name)
    path = Path(path)
    if model is None or not path.is_file() or path.stat().st_size != model.size_bytes:
        return False
    return file_sha256(path) == model.sha256


def download_url(name: str) -> str:
    return f"{RELEASE_URL}/{name}"


class DownloadCancelled(Exception):
    pass


MAX_RESUMES = 8  # GitHub's CDN cuts connections now and then; continue with a Range request


def download(name: str, *, progress: Optional[Callable[[int, int], None]] = None,
             cancelled: Callable[[], bool] = lambda: False, timeout: float = 30.0,
             directory: Optional[Path] = None) -> Path:
    """Download a catalog model into :func:`user_model_dir` (or ``directory``) and return its path.

    Streams into ``<name>.part`` and renames only when its size and SHA-256 match
    the catalog, so an interrupted, cancelled or corrupted download never leaves a
    bad model behind. A connection that ends early is resumed where it stopped
    (HTTP Range, up to :data:`MAX_RESUMES` times). ``progress(done_bytes, total_bytes)``.
    """
    model = catalog_model(name)
    if model is None:
        raise ValueError(f"不支持的模型：{name}")
    import hashlib
    import http.client
    import ssl
    import urllib.request

    target_dir = Path(directory) if directory is not None else user_model_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / model.name
    part = target_dir / f"{model.name}.part"
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    total = model.size_bytes
    done = 0
    digest = hashlib.sha256()
    resumes = 0
    try:
        with open(part, "wb") as fh:
            while done < total:
                headers = {"User-Agent": "SuperBirdTools"}
                if done:
                    headers["Range"] = f"bytes={done}-"
                request = urllib.request.Request(download_url(model.name), headers=headers)
                error = None
                try:
                    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                        if done and getattr(response, "status", 200) != 206:  # Range ignored: start over
                            fh.seek(0)
                            fh.truncate()
                            digest, done = hashlib.sha256(), 0
                        while True:
                            if cancelled():
                                raise DownloadCancelled(model.name)
                            chunk = response.read(1 << 20)
                            if not chunk:
                                break
                            fh.write(chunk)
                            digest.update(chunk)
                            done += len(chunk)
                            if progress is not None:
                                progress(done, total)
                except (OSError, http.client.HTTPException) as exc:  # cut connection, timeout
                    error = exc
                if done < total:
                    resumes += 1
                    if resumes > MAX_RESUMES:
                        raise IOError(f"下载不完整：{done} / {total} 字节" + (f"（{error}）" if error else ""))
        if done != total:
            raise IOError(f"下载不完整：{done} / {total} 字节")
        if digest.hexdigest() != model.sha256:
            raise IOError(f"{model.name} 校验失败（SHA-256 不符）")
        os.replace(part, final)
        return final
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
