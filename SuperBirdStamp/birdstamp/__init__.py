from app_identity import load_app_identity

APP_INFO = load_app_identity("SuperBirdStamp")
__version__ = APP_INFO.version

__all__ = ["APP_INFO", "__version__"]
