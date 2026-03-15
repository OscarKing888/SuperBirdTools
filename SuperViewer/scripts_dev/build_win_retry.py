#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 打包自动修正迭代：执行 build_win.bat，失败时按错误类型应用修正并重试，最多 5 次。
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT_DIR / "SuperViewer_win.spec"
DIST_DIR = ROOT_DIR / "dist" / "SuperViewer"
MAX_ATTEMPTS = 5


def run_build():
    """在项目根目录执行 build_win.bat，返回 (returncode, combined_output)."""
    bat = ROOT_DIR / "scripts" / "build_win.bat"
    proc = subprocess.run(
        ["cmd", "/c", str(bat)],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, out


def success():
    """打包成功条件：dist\\SuperViewer 目录存在且含可执行程序."""
    if not DIST_DIR.is_dir():
        return False
    exe_name = "SuperViewer.exe"
    return (DIST_DIR / exe_name).exists()


def classify_error(output):
    """
    根据输出判断错误类型，返回: 'pyinstaller_missing' | 'upx' | 'datas' | 'hiddenimport' | 'python_missing' | 'unknown'
    """
    out_lower = output.lower()
    if "no module named" in out_lower and "pyinstaller" in out_lower:
        return "pyinstaller_missing"
    if "upx" in out_lower or "cannot find upx" in out_lower:
        return "upx"
    if "modulenotfounderror" in out_lower or "importerror" in out_lower:
        if "no module named" in out_lower:
            return "hiddenimport"
    if "unable to find" in out_lower and "when adding binary and data" in out_lower:
        return "datas"
    if "exiftools_win" in out_lower or "super_viewer.cfg" in out_lower or "cannot find" in out_lower:
        if "not found" in out_lower or "error" in out_lower:
            return "datas"
    if "'py'" in output or "python" in out_lower and ("not found" in out_lower or "not recognized" in out_lower):
        return "python_missing"
    return "unknown"


def extract_missing_module(output):
    """从 ModuleNotFoundError 中解析缺失模块名."""
    m = re.search(r"no module named ['\"]([^'\"]+)['\"]", output, re.IGNORECASE)
    return m.group(1).strip() if m else None


def apply_fix_pyinstaller():
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        check=False,
    )


def apply_fix_upx():
    """将 spec 中 EXE 与 COLLECT 的 upx=True 改为 upx=False."""
    text = SPEC_PATH.read_text(encoding="utf-8")
    if "upx=True" not in text:
        return
    text = text.replace("upx=True", "upx=False")
    SPEC_PATH.write_text(text, encoding="utf-8")


def apply_fix_hiddenimport(module_name):
    """在 Analysis 的 hiddenimports 中追加模块名."""
    text = SPEC_PATH.read_text(encoding="utf-8")
    # 匹配 hiddenimports=[...]
    pattern = r"hiddenimports=\[([^\]]*)\]"
    match = re.search(pattern, text)
    if not match:
        return
    inner = match.group(1).strip()
    if inner:
        new_inner = inner.rstrip() + ", " + repr(module_name)
    else:
        new_inner = repr(module_name)
    new_line = "hiddenimports=[" + new_inner + "]"
    text = re.sub(pattern, new_line, text, count=1)
    SPEC_PATH.write_text(text, encoding="utf-8")


def extract_missing_datas_path(output):
    """从 PyInstaller 的 'Unable to find \"...\" when adding binary and data' 中解析缺失路径，返回相对路径（正斜杠）。"""
    m = re.search(r"Unable to find ['\"]([^'\"]+)['\"]", output, re.IGNORECASE)
    if not m:
        return None
    abs_path = m.group(1).strip().replace("\\", "/")
    root_str = str(ROOT_DIR).replace("\\", "/")
    if abs_path.startswith(root_str + "/"):
        return abs_path[len(root_str) + 1:]
    return abs_path


def apply_fix_datas(output):
    """从 spec 的 datas 中移除报错中提到的缺失路径对应项。"""
    rel_path = extract_missing_datas_path(output)
    if not rel_path:
        return
    text = SPEC_PATH.read_text(encoding="utf-8")
    rel_norm = rel_path.replace("\\", "/")
    # 移除一行形如 "        ('app_common/about_dialog/osk_banner.jpg', 'app_common/about_dialog'),"
    escaped = re.escape(rel_norm)
    pattern = re.compile(
        r"^\s*\(\s*['\"]" + escaped + r"['\"]\s*,\s*[^)]+\)\s*,?\s*\n",
        re.MULTILINE,
    )
    new_text = pattern.sub("", text)
    if new_text != text:
        SPEC_PATH.write_text(new_text, encoding="utf-8")


def main():
    os.chdir(ROOT_DIR)
    attempts = 0
    fixes_applied = []
    last_output = ""

    while attempts < MAX_ATTEMPTS:
        attempts += 1
        print(f"[尝试 {attempts}/{MAX_ATTEMPTS}] 执行 build_win.bat ...")
        returncode, output = run_build()
        last_output = output

        if returncode == 0 and success():
            print("[OK] 打包完成: dist\\SuperViewer")
            return 0

        if attempts >= MAX_ATTEMPTS:
            print(f"[失败] 已达最大尝试次数 {MAX_ATTEMPTS}，停止。")
            print("--- 最后一次构建输出 ---")
            print(last_output)
            print("--- 已应用的修正 ---")
            for f in fixes_applied:
                print("  -", f)
            return 1

        kind = classify_error(output)
        fix_desc = None

        if kind == "pyinstaller_missing":
            apply_fix_pyinstaller()
            fix_desc = "安装 PyInstaller"
        elif kind == "upx":
            apply_fix_upx()
            fix_desc = "spec 中 upx=False"
        elif kind == "hiddenimport":
            mod = extract_missing_module(output)
            if mod:
                apply_fix_hiddenimport(mod)
                fix_desc = f"hiddenimports 添加: {mod}"
            else:
                apply_fix_upx()
                fix_desc = "保守修正: spec 中 upx=False"
        elif kind == "datas":
            apply_fix_datas(output)
            fix_desc = "从 spec 中移除缺失的 datas 项"
        elif kind == "python_missing":
            fix_desc = "未修正: 请确保 py -3 或 python 可用"
        else:
            apply_fix_upx()
            fix_desc = "保守修正: spec 中 upx=False"

        if fix_desc:
            fixes_applied.append(fix_desc)
            print(f"  应用修正: {fix_desc}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
