from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from app_common.log import get_log_file_path, get_logger
from app_common.send_to_app import (
    get_initial_file_list_from_argv,
    send_file_list_to_running_app,
)
from birdstamp.constants import SEND_TO_APP_ID

_log = get_logger("main")


def _filter_platform_startup_args(argv: list[str]) -> list[str]:
    """过滤平台启动器注入的参数，避免 GUI bundle 冷启动时被 argparse 误判。"""
    filtered_args: list[str] = []
    for arg in argv:
        if sys.platform == "darwin" and arg.startswith("-psn_"):
            continue
        filtered_args.append(arg)
    return filtered_args


def _install_exception_logging() -> None:
    """窗口版打包应用没有控制台时，将未捕获异常写入日志文件。"""

    def _log_uncaught_exception(exc_type, exc_value, exc_tb) -> None:
        try:
            message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            _log.error("uncaught exception\n%s", message.rstrip())
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _log_uncaught_exception


def main() -> None:
    _install_exception_logging()
    _log.info("startup argv=%s", sys.argv[1:])
    log_file = get_log_file_path()
    if log_file:
        _log.info("log file=%s", log_file)

    # 冷启动：从命令行解析出「用本应用打开」的文件列表（遇 - 停止）
    initial_file_list = get_initial_file_list_from_argv()
    _log.info("initial_file_list count=%s", len(initial_file_list))
    if initial_file_list:
        # 若已有实例在运行，将文件列表发过去并由其处理，本进程退出
        if send_file_list_to_running_app(SEND_TO_APP_ID, initial_file_list):
            _log.info("forwarded startup files to running instance, exiting current process")
            sys.exit(0)

    parser = argparse.ArgumentParser(description="Launch BirdStamp GUI editor.")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Open this image file on startup.",
    )
    filtered_args = _filter_platform_startup_args(sys.argv[1:])
    _log.info("filtered argv=%s", filtered_args)
    args = parser.parse_args(filtered_args)

    # 启动时待加入照片列表：优先用命令行位置参数列表，否则用 --file
    if initial_file_list:
        startup_files = [Path(p) for p in initial_file_list]
    elif args.file:
        startup_files = [args.file.resolve(strict=False)]
    else:
        startup_files = []
    _log.info("startup_files=%s", [str(path) for path in startup_files])

    try:
        from birdstamp.gui import launch_gui
    except Exception as exc:
        _log.error("GUI import failed: %s", exc)
        raise SystemExit(f"GUI is unavailable: {exc}") from exc

    _log.info("launching GUI")
    launch_gui(startup_files=startup_files)
    _log.info("GUI returned normally")


if __name__ == "__main__":
    main()
