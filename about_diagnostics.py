"""About 只读启动检查；源码和打包入口共用，不构造主窗口或读取工作区。"""
from __future__ import annotations

import argparse
import json
import importlib
from pathlib import Path
import time

from app_identity import load_app_identity


def main(app_id: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Check packaged About configuration and layout.')
    parser.add_argument('--output-dir', type=Path, help='Optional directory for a screenshot and JSON report.')
    args = parser.parse_args(argv)
    from PyQt6.QtWidgets import QApplication
    from app_common.about_dialog.dialog import AboutDialog
    if app_id == 'SuperViewer':
        try:
            application = importlib.import_module("SuperViewer.main")
        except ImportError:
            import main as application
        info = application._load_superviewer_about_info()
        images = application.load_about_images(application._get_about_config_resource_path())
    else:
        from birdstamp.gui import editor as application
        info = application._load_birdstamp_about_info()
        images = application._load_birdstamp_about_images()
    app = QApplication.instance() or QApplication([])
    dialog = AboutDialog(None, info, images=images)
    try:
        dialog.show()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.processEvents()
            if not dialog._relayout_timer.isActive():
                break
        if any(card._pixmap.isNull() for card in dialog.cards):
            raise RuntimeError('About image failed to decode')
        identity = load_app_identity(app_id)
        report = {'app_id': app_id, 'app_name': info['app_name'], 'version': info['version'],
                  'window_title': identity.window_title(info), 'images': images,
                  'dialog_size': [dialog.width(), dialog.height()]}
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            if not dialog.grab().save(str(args.output_dir / f'{app_id}-about.png')):
                raise RuntimeError('Unable to save About screenshot')
            (args.output_dir / f'{app_id}-about.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
        return 0
    finally:
        dialog.close()
