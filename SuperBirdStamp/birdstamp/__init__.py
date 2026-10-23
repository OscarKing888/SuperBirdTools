import os

# Runtime dependencies belong to the installer. Ultralytics patches PIL.Image.open
# and otherwise tries to install pi-heif when an unsupported RAW fails to open.
# Set both switches before any detector import, including CLI/diagnostic entry points.
os.environ["YOLO_AUTOINSTALL"] = "false"
os.environ["ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS"] = "1"

from app_identity import load_app_identity

APP_INFO = load_app_identity("SuperBirdStamp")
__version__ = APP_INFO.version

__all__ = ["APP_INFO", "__version__"]
