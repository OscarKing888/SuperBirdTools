"""保留去抖动失败照片的身份，GUI 不从本地化错误文字猜测文件名。"""
from contextlib import contextmanager
from pathlib import Path

from .video_export_cancelled_error import VideoExportCancelledError


class SequencePhotoError(ValueError):
    def __init__(self, source_path, message):
        self.source_path = Path(source_path)
        super().__init__(f'{self.source_path.name}：{message}')


@contextmanager
def sequence_photo_errors(path):
    try:
        yield
    except (SequencePhotoError, VideoExportCancelledError):
        raise
    except Exception as exc:
        raise SequencePhotoError(path, str(exc)) from exc
