"""RAW 导出与独立去抖动共用内嵌预览的真实像素来源。"""
import io
from pathlib import Path

from PIL import Image

from birdstamp.decoders import image_decoder
from birdstamp.export_stage.core import render_video_frame_context
from birdstamp.export_stage.sequence_preview import SequencePreview, render_sequence_preview_frame
from birdstamp.export_stage.video_frame_job import VideoFrameJob
from birdstamp.gui.editor_utils import path_key


def test_regular_and_dejitter_render_use_embedded_raw_pixels(tmp_path, monkeypatch):
    source = tmp_path / '鸟.arw'
    source.write_bytes(b'RAW placeholder')
    buffer = io.BytesIO()
    Image.new('RGB', (1600, 800), '#2040c0').save(buffer, format='JPEG')
    monkeypatch.setattr('app_common.thumb_stream.get_raw_preview_jpeg', lambda _path: buffer.getvalue())
    monkeypatch.setattr(image_decoder, '_decode_raw',
                        lambda *_args, **_kwargs: Image.new('RGB', (2400, 1200), 'red'))
    settings = {'ratio': 'no_crop', 'draw_banner': False, 'draw_text': False,
                'draw_focus': False, 'max_long_edge': 0}
    job = VideoFrameJob(source, settings, {}, {})

    with render_video_frame_context(job).image as regular:
        assert regular.size == (1600, 800)
        assert regular.getpixel((800, 400))[2] > regular.getpixel((800, 400))[0]

    key = path_key(source)
    sequence = SequencePreview(
        'test', {key: job}, (), pixel_boxes={key: (10, 10, 1590, 790)},
        source_sizes={key: (1600, 800)}, output_size=(1580, 780),
    )
    with render_sequence_preview_frame(sequence, source, validate_files=False).image as aligned:
        assert aligned.size == (1580, 780)
        assert aligned.getpixel((790, 390))[2] > aligned.getpixel((790, 390))[0]
