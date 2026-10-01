"""显式下载/离线导入官方模型；未安装时不访问网络。"""
import hashlib
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

MODEL_ID = 'ak-bird-hrnet-w32-256-20230519'
MODEL_BYTES = 114746485
MODEL_SHA256 = '566feff57cbf6ebcc84d111741c85ce49e07e940b450b646638f1e45587cfb15'
MODEL_URL = ('https://download.openmmlab.com/mmpose/v1/animal_2d_keypoint/topdown_heatmap/'
             'animal_kingdom/td-hm_hrnet-w32_8xb32-300e_animalkingdom_P3_bird-256x256-566feff5_20230519.pth')


def model_path():
    from birdstamp.config import get_user_data_dir
    return Path(get_user_data_dir()) / 'models' / (MODEL_ID + '.pth')


def model_file_signature(path):
    """状态缓存仅在同一文件未被替换或修改时有效，不凭文件名判定安装成功。"""
    path = Path(path)
    try:
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    except OSError:
        return (str(path), None)


@dataclass(frozen=True)
class ModelStatus:
    state: str
    signature: tuple
    message: str = ''


def inspect_model(path, cancelled=lambda: False):
    """供后台状态检查使用；只有完整校验通过才报告 ready。"""
    path = Path(path)
    signature = model_file_signature(path)
    try:
        verify_model(path, cancelled)
        state, message = 'ready', '部位模型完整性校验通过。'
    except (ValueError, OSError) as exc:
        state = 'invalid' if path.exists() else 'missing'
        message = str(exc)
    if model_file_signature(path) != signature:
        return ModelStatus('changed', signature, '模型文件已变化，正在重新校验。')
    return ModelStatus(state, signature, message)


def verify_model(path, cancelled=lambda: False):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != MODEL_BYTES:
        raise ValueError('鸟体部位模型缺失或大小不符，请下载或导入官方权重。')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            if cancelled():
                raise InterruptedError('已取消模型校验')
            digest.update(chunk)
    if digest.hexdigest() != MODEL_SHA256:
        raise ValueError('鸟体部位模型校验失败，请重新下载官方权重。')
    return path


def install_model(source=None, *, destination=None, cancelled=lambda: False, progress=lambda n, total: None):
    destination = Path(destination) if destination else model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.bird-model-', suffix='.part', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            with (Path(source).open('rb') if source else urlopen(MODEL_URL, timeout=10)) as stream:
                count = 0
                while True:
                    if cancelled():
                        raise InterruptedError('已取消模型下载')
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MODEL_BYTES:
                        raise ValueError('鸟体模型内容大小超出官方权重')
                    output.write(chunk)
                    progress(count, MODEL_BYTES)
        verify_model(temp, cancelled)
        if cancelled():
            raise InterruptedError('已取消模型安装')
        os.replace(temp, destination)
        return destination
    finally:
        Path(temp).unlink(missing_ok=True)
