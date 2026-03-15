STANDARD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
HEIF_EXTENSIONS = {".heic", ".heif", ".hif"}
RAW_EXTENSIONS = {
    ".arw",
    ".cr2",
    ".cr3",
    ".nef",
    ".raf",
    ".rw2",
    ".orf",
    ".dng",
}
SUPPORTED_EXTENSIONS = STANDARD_EXTENSIONS | HEIF_EXTENSIONS | RAW_EXTENSIONS

DEFAULT_SHOW_FIELDS = {
    "bird",
    "time",
    "location",
    "gps",
    "camera",
    "lens",
    "settings",
}

VALID_MODES = {"keep", "fit", "square", "vertical"}
VALID_FRAME_STYLES = {"crop", "pad"}

# 单例 IPC 用：接收「发送到本应用」文件列表时的服务名
SEND_TO_APP_ID = "birdstamp"

