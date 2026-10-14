"""Compatibility alias for the shared high-depth decoder.

Keep the module identity so existing injected decoders and tests patch the same
implementation used by both denoising and photo editing.
"""
import sys
from app_common import image_pixels as _shared

sys.modules[__name__] = _shared
