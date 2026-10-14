"""Build/check the checkout-local extension and collect it with its notices."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import sysconfig


def collect_photo_edit(repo_root):
    root = Path(repo_root)/"app_common"
    native = root/"native/photo_edit"
    spec = importlib.util.spec_from_file_location("photo_edit_build",native/"tools/build_native.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    binary = root/"photo_edit"/("_native"+sysconfig.get_config_var("EXT_SUFFIX"))
    try:
        stamp = json.loads(binary.with_name("_native.build.json").read_text("utf-8"))
        ready = (stamp["release"] and stamp["fingerprint"]==builder.build_fingerprint() and
                 stamp["notices"]==hashlib.sha256(binary.with_name("_native.notices.txt").read_bytes()).hexdigest() and
                 stamp["binary"]==hashlib.sha256(binary.read_bytes()).hexdigest())
    except (OSError,ValueError,KeyError):
        ready = False
    # Verify the immutable snapshot even on an incremental build.
    builder.prepare()
    if not ready:
        subprocess.run([sys.executable,str(native/"tools/build_native.py")],check=True)
    notices = [native/"upstream"/name for name in
               ("LICENSE-MIT","LICENSE-APACHE","NOTICE","ATTRIBUTION.md")]
    notices.append(native/"upstream.json")
    notices.append(binary.with_name("_native.notices.txt"))
    return ([(str(p),"licenses/PhotoCraft") for p in notices],
            [(str(binary),"app_common/photo_edit")],["app_common.photo_edit._native"])
