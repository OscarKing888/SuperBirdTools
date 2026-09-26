# -*- coding: utf-8 -*-
"""Read-only check using exactly the packaged preview decoder and metadata path."""
import argparse
import json
from pathlib import Path

from app_common.video import find_ffmpeg, probe_video, video_thumbnail_rgb


def main(argv=None):
    parser = argparse.ArgumentParser(description='检查视频信息与封面解码，不修改视频')
    parser.add_argument('path')
    parser.add_argument('--output', help='UTF-8 JSON 诊断结果文件')
    args = parser.parse_args(argv)
    try:
        executable = find_ffmpeg()
        info = probe_video(args.path)
        _, width, height = video_thumbnail_rgb(args.path, 1024)
        result = dict(ok=True, ffmpeg=executable, info=info, poster=dict(width=width, height=height))
    except Exception as exc:
        result = dict(ok=False, error=str(exc))
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output + '\n', encoding='utf-8')
    else:
        print(output)
    return 0 if result['ok'] else 1
