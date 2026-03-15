# -*- coding: utf-8 -*-
"""
路径、配置文件与应用身份：程序目录、用户状态目录、last folder、cfg 读写、窗口标题与进程名。
仅依赖标准库与 app_common，不依赖其它 SuperViewer 业务模块。
"""

import json
import os
import re
import sys


CONFIG_FILENAME = "super_viewer.cfg"
LAST_SELECTED_DIRECTORY_FILENAME = "last_selected_directory.txt"
LEGACY_LAST_FOLDER_FILENAME = ".last_folder.txt"
USER_STATE_DIRNAME = "SuperViewer"
APP_ICON_CANDIDATES = (
    os.path.join("icons", "app_icon.png"),
    os.path.join("icons", "app_icon.ico"),
    os.path.join("icons", "app_icon.icns"),
)


def _sanitize_display_string(s: str) -> str:
    """
    清理用于界面显示的字符串：去掉控制字符与空字节，避免各系统显示异常或截断。
    """
    if not s:
        return s
    result = []
    for c in s:
        code = ord(c)
        if code == 0:
            result.append(" ")
        elif code < 32 and c not in "\t\n\r":
            result.append(" ")
        else:
            result.append(c)
    return "".join(result).strip()


def _get_app_dir() -> str:
    """返回当前程序目录（脚本目录或打包后可执行文件目录）。"""
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        app_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if not app_dir:
        app_dir = os.getcwd()
    return app_dir


def _get_resource_path(relative_path: str) -> str | None:
    """按运行环境查找资源文件路径。"""
    candidates = [os.path.join(_get_app_dir(), relative_path)]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, relative_path))
        if sys.platform == "darwin":
            candidates.append(
                os.path.abspath(os.path.join(_get_app_dir(), "..", "Resources", relative_path))
            )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _get_app_icon_path() -> str | None:
    """返回应用图标路径。"""
    for rel in APP_ICON_CANDIDATES:
        p = _get_resource_path(rel)
        if p:
            return p
    return None


def _get_user_state_dir() -> str:
    """
    返回存放用户级状态文件的目录。
    Windows 优先用 %APPDATA%，macOS 用 ~/Library/Application Support，其他回退到用户 home。
    """
    if sys.platform.startswith("win"):
        base_dir = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base_dir, USER_STATE_DIRNAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", USER_STATE_DIRNAME)
    return os.path.join(os.path.expanduser("~"), f".{USER_STATE_DIRNAME.lower()}")


def _get_last_selected_directory_file_path() -> str:
    """返回用户目录下的 last_selected_directory.txt 完整路径。"""
    return os.path.join(_get_user_state_dir(), LAST_SELECTED_DIRECTORY_FILENAME)


def _get_legacy_last_folder_file_path() -> str:
    """兼容旧版程序目录下的 .last_folder.txt。"""
    return os.path.join(_get_app_dir(), LEGACY_LAST_FOLDER_FILENAME)


def _get_legacy_last_selected_directory_file_path() -> str:
    """兼容旧版程序目录下的 last_selected_directory.txt。"""
    return os.path.join(_get_app_dir(), LAST_SELECTED_DIRECTORY_FILENAME)


def _read_last_selected_directory_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.readline().strip()
    except Exception:
        return None
    if not raw:
        return None
    resolved = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(resolved):
        return None
    return resolved


def load_last_folder_from_file() -> str | None:
    """读取上次打开的目录；优先读用户目录，兼容旧版程序目录文件。"""
    path = _read_last_selected_directory_file(_get_last_selected_directory_file_path())
    if path:
        return path
    path = _read_last_selected_directory_file(_get_legacy_last_selected_directory_file_path())
    if path:
        return path
    return _read_last_selected_directory_file(_get_legacy_last_folder_file_path())


def save_last_folder_to_file(path: str) -> None:
    """将上次打开的目录写入用户目录下的 last_selected_directory.txt。"""
    if not path or not os.path.isdir(path):
        return
    try:
        file_path = _get_last_selected_directory_file_path()
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(os.path.abspath(path))
    except Exception:
        pass


def _get_config_path() -> str:
    """返回 super_viewer.cfg 的完整路径，与当前运行的主程序同目录。"""
    app_dir = _get_app_dir()
    return os.path.join(app_dir, CONFIG_FILENAME)


def _get_config_resource_path() -> str:
    """返回可读取的 super_viewer.cfg 路径，打包后优先使用资源目录内的配置。"""
    return _get_resource_path(CONFIG_FILENAME) or _get_config_path()


def _load_settings() -> dict:
    """读取 EXIF.cfg，失败返回空字典。"""
    candidates = [_get_config_path()]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, CONFIG_FILENAME))
        if sys.platform == "darwin":
            candidates.append(os.path.abspath(os.path.join(_get_app_dir(), "..", "Resources", CONFIG_FILENAME)))
    seen = set()
    for path in candidates:
        norm = os.path.normpath(path)
        if norm in seen or not os.path.isfile(path):
            continue
        seen.add(norm)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.pop("last_selected_directory", None)
                return data
        except Exception:
            continue
    return {}


def _save_settings(data: dict) -> None:
    """写入 EXIF.cfg（UTF-8）。"""
    path = _get_config_path()
    try:
        data = dict(data or {})
        data.pop("last_selected_directory", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_last_selected_directory_from_settings() -> str | None:
    """读取上次在目录树中选中的目录路径。"""
    return load_last_folder_from_file()


def save_last_selected_directory_to_settings(path: str) -> None:
    """保存目录树最后一次选中的目录路径。"""
    save_last_folder_to_file(path)
    if os.path.isfile(_get_config_path()):
        _save_settings(_load_settings())


def _build_windows_app_id(app_name: str) -> str:
    """构造稳定的 Windows AppUserModelID。"""
    base = re.sub(r"[^A-Za-z0-9.]+", "", app_name) or "SuperViewer"
    return f"oskch.{base}"


def _get_product_display_name(about_info: dict | None = None) -> str:
    """Return the short product name used for window/app titles."""
    raw_name = ""
    if isinstance(about_info, dict):
        raw_name = _sanitize_display_string(about_info.get("app_name", "")) or ""
    if not raw_name:
        raw_name = "Super Viewer"
    short_name = raw_name.split(" - ", 1)[0].strip()
    return short_name or "Super Viewer"


def _build_main_window_title(about_info: dict | None = None) -> str:
    """Build the visible main window title from about config fields."""
    if not isinstance(about_info, dict):
        return "Super Viewer"

    app_name = _sanitize_display_string(about_info.get("app_name", "")) or "Super Viewer"
    version = _sanitize_display_string(about_info.get("version", "")) or ""
    author = _sanitize_display_string(about_info.get("作者", "")) or ""

    parts: list[str] = [app_name]
    if version:
        parts.append(version)
    if author:
        parts.append(author)
    return " - ".join(parts)


def _set_macos_process_name_via_objc(name: str) -> bool:
    """使用 Objective-C runtime 直接设置 macOS 进程名。"""
    try:
        import ctypes

        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    except Exception:
        return False

    try:
        objc_get_class = objc.objc_getClass
        objc_get_class.restype = ctypes.c_void_p
        objc_get_class.argtypes = [ctypes.c_char_p]

        sel_register_name = objc.sel_registerName
        sel_register_name.restype = ctypes.c_void_p
        sel_register_name.argtypes = [ctypes.c_char_p]

        msg_send_noarg = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
        msg_send_cstr = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
        )(("objc_msgSend", objc))
        msg_send_obj = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(("objc_msgSend", objc))

        ns_string_cls = objc_get_class(b"NSString")
        ns_process_info_cls = objc_get_class(b"NSProcessInfo")
        if not ns_string_cls or not ns_process_info_cls:
            return False

        sel_string_with_utf8 = sel_register_name(b"stringWithUTF8String:")
        sel_process_info = sel_register_name(b"processInfo")
        sel_set_process_name = sel_register_name(b"setProcessName:")

        ns_name = msg_send_cstr(ns_string_cls, sel_string_with_utf8, name.encode("utf-8"))
        if not ns_name:
            return False
        proc_info = msg_send_noarg(ns_process_info_cls, sel_process_info)
        if not proc_info:
            return False
        msg_send_obj(proc_info, sel_set_process_name, ns_name)
        return True
    except Exception:
        return False


def _apply_runtime_app_identity(app_name: str) -> None:
    """
    尽量把系统层面的应用名设置为 app_name。
    macOS: NSProcessInfo 名称与 NSBundle 名称；Windows: AppUserModelID。
    """
    name = _sanitize_display_string(app_name or "Super Viewer") or "Super Viewer"

    if sys.platform == "darwin":
        pyobjc_process_name_ok = False
        try:
            from Foundation import NSProcessInfo

            NSProcessInfo.processInfo().setProcessName_(name)
            pyobjc_process_name_ok = True
        except Exception:
            pass
        if not pyobjc_process_name_ok:
            _set_macos_process_name_via_objc(name)
        try:
            from Foundation import NSBundle

            bundle = NSBundle.mainBundle()
            if bundle is not None:
                info = bundle.localizedInfoDictionary()
                if info is None:
                    info = bundle.infoDictionary()
                if info is not None:
                    info["CFBundleName"] = name
                    info["CFBundleDisplayName"] = name
                    info["CFBundleExecutable"] = name
        except Exception:
            pass

    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_build_windows_app_id(name))
        except Exception:
            pass
