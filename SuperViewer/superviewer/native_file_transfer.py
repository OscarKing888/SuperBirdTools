"""系统文件移动/复制；系统拥有进度、重名选择与取消，不自行覆盖文件。"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys

from app_common.exif_io.xmp_sidecar import find_same_stem_xmp_sidecar


@dataclass(frozen=True)
class TransferResult:
    cancelled: bool = False
    error: str = ""


def _validate_directory_name(name):
    if (not name or name in (".", "..") or name != name.strip()
            or name.endswith(".") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", name)):
        raise ValueError("请输入有效的子目录名称，不要包含路径、特殊字符或末尾空格/句点。")


def create_child_directory(parent, name):
    """只创建一级目录；两平台统一拒绝路径、保留设备名和不可移植名称。"""
    _validate_directory_name(name)
    path = Path(parent).absolute() / name
    path.mkdir()  # 不接受已存在目录，也不递归创建父目录。
    return str(path)


def rename_directory(source, name):
    """同一父目录内重命名，拒绝覆盖；大小写改名失败时保留并报告恢复路径。"""
    _validate_directory_name(name)
    source = Path(os.path.abspath(source))
    if source == source.parent or os.path.ismount(source):
        raise ValueError("不能重命名磁盘或挂载根目录。")
    if not source.is_dir():
        raise FileNotFoundError(f"目录不存在：{source}")
    target = source.with_name(name)
    if str(source) == str(target):
        return str(source)
    if os.path.lexists(target):
        # samefile 也能识别大小写不敏感的 macOS 卷；不同名字的符号链接不能当作同一入口。
        case_only = source.name.casefold() == name.casefold()
        if not case_only or target.is_symlink() or not os.path.samefile(source, target):
            raise FileExistsError(f"已存在同名文件或文件夹：{target}")
        import uuid
        temporary = source.with_name(f".folder-rename-{uuid.uuid4().hex}")
        os.rename(source, temporary)
        try:
            os.rename(temporary, target)
        except OSError as exc:
            try:
                os.rename(temporary, source)
            except OSError as restore_exc:
                raise OSError(f"重命名失败：{exc}；恢复失败：{restore_exc}；目录保留在：{temporary}") from exc
            raise
    else:
        os.rename(source, target)
    return str(target)


def transfer_paths_with_sidecars(sources, destination, *, copy=False):
    """解析实际文件后的最终快照；同目录操作跳过，共用 XMP 只提交一次。"""
    destination = Path(destination).absolute()
    if not destination.is_dir():
        raise ValueError(f"目标目录不存在：{destination}")
    paths, seen = [], set()
    for source in sources:
        source = Path(source).absolute()
        if not source.is_file():
            raise FileNotFoundError(f"找不到源文件：{source}")
        if not copy and os.path.samefile(source.parent, destination):
            continue
        sidecar = find_same_stem_xmp_sidecar(str(source))
        for path in (str(source), sidecar):
            if not path:
                continue
            key = os.path.normcase(os.path.realpath(path))
            if key not in seen:
                seen.add(key)
                paths.append(os.path.abspath(path))
    return tuple(paths)


def _windows_transfer(paths, destination, copy, owner):
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = owner or None
    operation.wFunc = 2 if copy else 1  # FO_COPY / FO_MOVE
    operation.pFrom = "\0".join(paths) + "\0\0"
    operation.pTo = destination + "\0\0"
    # 允许系统撤销；保留系统的冲突、进度和取消 UI。
    operation.fFlags = 0x0040  # 不启用自动覆盖、静默或自动重命名。
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell.SHFileOperationW.restype = ctypes.c_int
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    ole.CoUninitialize.argtypes, ole.CoUninitialize.restype = [], None
    initialized = ole.CoInitializeEx(None, 2)  # 本操作 worker 拥有独立 STA。
    if initialized < 0:
        return TransferResult(error=f"Windows Shell 初始化失败（HRESULT {initialized & 0xffffffff:08X}）")
    try:
        code = shell.SHFileOperationW(ctypes.byref(operation))
    finally:
        ole.CoUninitialize()
    if operation.fAnyOperationsAborted or code == 0x75:
        return TransferResult(cancelled=True)
    return TransferResult(error=f"Windows 文件操作未完成（错误 {code}）" if code else "")


class _AEDesc(ctypes.Structure):
    # AEDataModel.h 使用 pack(2)，macOS 64 位下结构体大小为 12 字节。
    _pack_ = 2
    _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]


def _fourcc(value):
    return int.from_bytes(value.encode("ascii"), "big")


class _FinderEvents:
    def __init__(self):
        self.api = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        pointer = ctypes.POINTER(_AEDesc)
        signatures = {
            "AECreateDesc": [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long, pointer],
            "AECreateList": [ctypes.c_void_p, ctypes.c_long, ctypes.c_ubyte, pointer],
            "AEPutDesc": [pointer, ctypes.c_long, pointer],
            "AECreateAppleEvent": [ctypes.c_uint32, ctypes.c_uint32, pointer, ctypes.c_int16, ctypes.c_int32, pointer],
            "AEPutParamDesc": [pointer, ctypes.c_uint32, pointer],
            "AEDisposeDesc": [pointer],
            "AEGetParamPtr": [pointer, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p],
        }
        for name, args in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, ctypes.c_int16
        self.api.AESendMessage.argtypes = [pointer, pointer, ctypes.c_int32, ctypes.c_long]
        self.api.AESendMessage.restype = ctypes.c_int32
        self.descriptors = []

    @staticmethod
    def check(code):
        if code:
            raise OSError(f"Finder Apple Event 错误 {code}")

    def new(self):
        value = _AEDesc()
        self.descriptors.append(value)
        return value

    def data(self, kind, data):
        value = self.new()
        self.check(self.api.AECreateDesc(_fourcc(kind), data, len(data), ctypes.byref(value)))
        return value

    def close(self):
        for descriptor in reversed(self.descriptors):
            self.api.AEDisposeDesc(ctypes.byref(descriptor))


def _macos_transfer(paths, destination, copy, owner):
    events = _FinderEvents()
    try:
        target = events.data("bund", b"com.apple.finder")
        items = events.new()
        events.check(events.api.AECreateList(None, 0, False, ctypes.byref(items)))
        for path in paths:
            item = events.data("furl", Path(path).as_uri().encode("utf-8"))
            events.check(events.api.AEPutDesc(ctypes.byref(items), 0, ctypes.byref(item)))
        folder = events.data("furl", Path(destination).as_uri().encode("utf-8"))
        event, reply = events.new(), events.new()
        events.check(events.api.AECreateAppleEvent(_fourcc("core"), _fourcc("clon" if copy else "move"),
                                                  ctypes.byref(target), -1, 0, ctypes.byref(event)))
        events.check(events.api.AEPutParamDesc(ctypes.byref(event), _fourcc("----"), ctypes.byref(items)))
        events.check(events.api.AEPutParamDesc(ctypes.byref(event), _fourcc("insh"), ctypes.byref(folder)))
        # kAEWaitReply | kAEAlwaysInteract | kAECanSwitchLayer；等待用户冲突选择，不强制覆盖。
        # 系统操作不能因 UI 关闭而中断回收；由拥有者保留 worker 到真正完成。
        code = events.api.AESendMessage(ctypes.byref(event), ctypes.byref(reply), 0x03 | 0x30 | 0x40, -2)
        if code == -1743:
            return TransferResult(error="macOS 未允许控制 Finder。请在系统设置 → 隐私与安全性 → 自动化中允许 SuperViewer 控制 Finder。")
        if code == -128:
            return TransferResult(cancelled=True)
        events.check(code)
        error = ctypes.c_int32()
        status = events.api.AEGetParamPtr(ctypes.byref(reply), _fourcc("errn"), _fourcc("long"),
                                         None, ctypes.byref(error), ctypes.sizeof(error), None)
        if status == -1701:  # 回复没有错误字段。
            return TransferResult()
        events.check(status)
        if error.value == -1743:
            return TransferResult(error="macOS 未允许控制 Finder。请在系统设置 → 隐私与安全性 → 自动化中允许 SuperViewer（或启动它的终端）控制 Finder。")
        if error.value == -128:
            return TransferResult(cancelled=True)
        return TransferResult(error=f"Finder 文件操作未完成（错误 {error.value}）" if error.value else "")
    finally:
        events.close()


def native_transfer(paths, destination, *, copy=False, owner=0):
    """提交一个系统批次；源文件和侧车由调用者一起传入。"""
    if not paths:
        return TransferResult()
    destination = os.path.abspath(destination)
    paths = tuple(os.path.abspath(path) for path in paths)
    if any("\0" in path for path in (*paths, destination)):
        return TransferResult(error="文件路径含有无效字符")
    try:
        from app_common.photo_edit.file_assets import copy_photo_edit_assets
        for source in paths:
            copy_photo_edit_assets(source,Path(destination)/Path(source).name)
        if sys.platform == "win32":
            return _windows_transfer(paths, destination, copy, owner)
        if sys.platform == "darwin":
            return _macos_transfer(paths, destination, copy, owner)
        return TransferResult(error="系统拖放移动/复制目前仅支持 Windows 和 macOS")
    except Exception as exc:
        return TransferResult(error=str(exc))
