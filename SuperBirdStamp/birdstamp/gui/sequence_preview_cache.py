"""成片分析与有界预览的磁盘缓存。仅由成片 worker 读写，不阻塞工作区保存。"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from PyQt6.QtGui import QImage, QImageReader

from app_common.log import get_logger
from birdstamp.config import get_config_path
from birdstamp.export_stage.sequence_preview import SequencePreview, sequence_input_key
from birdstamp.export_stage.video_frame_job import VideoFrameJob
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult
from birdstamp.image_dejitter.sequence_geometry import aligned_crop_plan
from . import editor_options
from .editor_utils import path_key
from .sequence_preview_frame import SequencePreviewFrame

CACHE_VERSION = 1
_log = get_logger('birdstamp.sequence_cache')


class SequencePreviewCache:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else get_config_path().parent / 'cache' / 'sequence_preview'

    def _folder(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('无效的成片缓存签名')
        return self.root / key

    @staticmethod
    def _save_image(image, path):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.png', delete=False) as stream:
                temporary = Path(stream.name)
            if not image.save(str(temporary), 'PNG'):
                raise OSError(f'无法写入成片缓存：{path}')
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, sequence, frames, *, cancelled=lambda: False):
        """先完整写临时桶，再发布 manifest；取消/失败不会发布半成品。"""
        staging = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            folder = self._folder(sequence.input_key)
            staging = Path(tempfile.mkdtemp(prefix='.pending-', dir=self.root))
            records = []
            for index, (key, job) in enumerate(sequence.jobs.items()):
                if cancelled():
                    return
                frame = frames[key]
                self._save_image(frame.image, staging / f'quick-{index}.png')
                self._save_image(frame.source_image, staging / f'source-{index}.png')
                records.append(dict(path=str(job.path), settings=job.settings, raw_metadata=job.raw_metadata,
                                    source_size=sequence.source_sizes[key], pixel_box=sequence.pixel_boxes[key],
                                    tracking=asdict(sequence.tracking[key])))
            document = dict(version=CACHE_VERSION, input_key=sequence.input_key,
                            output_size=sequence.output_size, signatures=sequence.signatures, frames=records,
                            bird_boxes=list(sequence.bird_boxes.items()))
            (staging / 'manifest.json').write_text(json.dumps(document, ensure_ascii=False, default=str), encoding='utf-8')
            if cancelled() or not sequence.files_current():
                return
            # 无 manifest 的桶仅可能是早先失败的缓存，不含用户输出。
            if folder.exists():
                shutil.rmtree(folder)
            os.replace(staging, folder)
            staging = None
            self.prune(keep=folder)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _log.warning('成片分析缓存写入失败，保留内存结果：%s', exc)
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _read_image(path, budget):
        reader = QImageReader(str(path))
        size = reader.size()
        if not size.isValid() or size.width()*size.height()*4 > budget:
            raise ValueError('成片缓存图像尺寸无效或超过预算')
        image = reader.read()
        if image.isNull() or image.sizeInBytes() > budget:
            raise ValueError('成片缓存图像损坏')
        return image.convertToFormat(QImage.Format.Format_RGB888)

    def load(self, seeds, *, cancelled=lambda: False):
        try:
            key = sequence_input_key(seeds)
            folder = self._folder(key)
            manifest = folder / 'manifest.json'
            if not manifest.exists():
                return None
            if manifest.stat().st_size > 64*1024*1024:
                raise ValueError('成片缓存清单过大')
            raw = json.loads(manifest.read_text(encoding='utf-8'))
            if raw['version'] != CACHE_VERSION or raw['input_key'] != key:
                return None
            records = raw['frames']
            if [path_key(seed.path) for seed in seeds] != [path_key(Path(r['path'])) for r in records]:
                return None
            signatures = tuple((path, tuple(sig) if sig is not None else None) for path, sig in raw['signatures'])
            sequence = SequencePreview(key, {}, signatures, output_size=tuple(raw['output_size']))
            sequence.bird_boxes = {tuple(signature): tuple(box) for signature, box in raw.get('bird_boxes', [])}
            frames = {}
            budget = editor_options.DEJITTER_QUICK_CACHE_BYTES
            for index, record in enumerate(records):
                if cancelled():
                    return None
                path = Path(record['path'])
                frame_key = path_key(path)
                size, box = tuple(record['source_size']), tuple(record['pixel_box'])
                if (len(size) != 2 or min(size) <= 0 or len(box) != 4 or
                        (box[2]-box[0], box[3]-box[1]) != sequence.output_size or min(sequence.output_size) <= 0):
                    raise ValueError('成片缓存几何无效')
                tracking = record['tracking']
                result = RegionTrackingResult(
                    tuple(tuple(b) if b is not None else None for b in tracking['boxes']),
                    tuple(tracking['signature']) if tracking['signature'] else None,
                    tracking['error'], tuple(tracking['scores']), tuple(tracking['reasons']),
                    tuple(tuple(b) for b in tracking['predicted_boxes']))
                image = self._read_image(folder / f'quick-{index}.png', budget)
                budget -= image.sizeInBytes()
                source = self._read_image(folder / f'source-{index}.png', budget)
                budget -= source.sizeInBytes()
                sequence.jobs[frame_key] = VideoFrameJob(path, record['settings'], record['raw_metadata'], {})
                sequence.source_sizes[frame_key], sequence.pixel_boxes[frame_key] = size, box
                sequence.tracking[frame_key] = result
                frames[frame_key] = SequencePreviewFrame(path, image, size, sequence.output_size,
                                                        aligned_crop_plan(size, box), source)
            if not sequence.files_current() or sequence_input_key(sequence.jobs.values()) != key:
                return None
            os.utime(folder, None)
            return sequence, frames
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
            _log.warning('成片缓存不可用，需要重新分析：%s', exc)
            return None

    def _sharp_path(self, sequence, path):
        name = hashlib.sha256(path_key(path).encode('utf-8')).hexdigest()
        return self._folder(sequence.input_key) / f'sharp-{name}.png'

    def save_sharp(self, sequence, frame):
        try:
            path = self._sharp_path(sequence, frame.path)
            if not (path.parent / 'manifest.json').is_file():
                return
            self._save_image(frame.image, path)
            self.prune(keep=path.parent)
        except (OSError, ValueError) as exc:
            _log.warning('清晰成片缓存写入失败：%s', exc)

    def load_sharp(self, sequence, path):
        try:
            image_path = self._sharp_path(sequence, path)
            if not image_path.exists():
                return None
            image = self._read_image(image_path, editor_options.DEJITTER_PREVIEW_CACHE_BYTES)
            os.utime(image_path, None)
            key = path_key(path)
            size = sequence.source_sizes[key]
            return SequencePreviewFrame(path, image, size, sequence.output_size,
                                        aligned_crop_plan(size, sequence.pixel_boxes[key]))
        except (OSError, ValueError, KeyError):
            return None

    def prune(self, *, keep):
        # 优先淘汰旧清晰帧，再淘汰旧分析桶；当前整组快速预览始终保留。
        sharp = sorted(self.root.glob('*/sharp-*.png'), key=lambda p: p.stat().st_mtime, reverse=True)
        used = 0
        for path in sharp:
            used += path.stat().st_size
            if used > editor_options.DEJITTER_DISK_CACHE_BYTES // 2:
                path.unlink(missing_ok=True)
        folders = sorted((p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith('.')),
                         key=lambda p: (p == keep, p.stat().st_mtime), reverse=True)
        used = 0
        for index, folder in enumerate(folders):
            used += sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
            if folder != keep and (index >= 8 or used > editor_options.DEJITTER_DISK_CACHE_BYTES):
                shutil.rmtree(folder)
