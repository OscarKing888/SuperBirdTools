---
name: superviewer-preview-loading
description: >-
  Use when modifying SuperViewer preview image loading, PreviewPanel.set_image,
  file-list selection preview, thumbnail-size quick preview, RAW preview, or
  held direction-key navigation. Protects the required full/thumbnail/RAW
  preview policy and validation checks.
---

# SuperViewer Preview Loading Contract

Use this skill whenever a change touches:

- `SuperViewer/superviewer/preview_panel.py`
- `SuperViewer/main.py` preview selection handlers
- `app_common/file_browser/_panel.py` direction-key navigation or preview signals
- thumbnail-size preview routing between file browser and preview panel

## Required Behavior

- Do not simplify preview loading to always-thumbnail, always-sync-full, or always-async-full.
- Keep `PreviewPanel.set_image(path, *, load_full=True, quick_size=None)`.
- Keep `FileListPanel.preview_quick_size()` as the source of the selected thumbnail-size preview level.
- Normal single-image selection:
  - Small non-RAW images at or below the synchronous threshold show full preview synchronously: `SuperViewer_SYNC_FULL_PREVIEW_MAX_MP` (default 40 MP) for JPEG and other formats, `SuperViewer_SYNC_FULL_PREVIEW_HEIF_MAX_MP` (default 4 MP) for HEIF.
  - Larger non-RAW images first show the selected thumbnail-size preview, then asynchronously replace it with full preview.
  - RAW images show the selected-tier cache or a loading placeholder first (no RAW extraction in the GUI thread), then the full-preview worker replaces it with the high-resolution embedded RAW preview JPEG. exiftool/rawpy camera previews must outrank tiny piexif EXIF thumbnails such as 160x120; those are only last-resort fallbacks.
- Held direction-key navigation:
  - From the second auto-repeat image until key release, use only the selected thumbnail-size preview.
  - Call `PreviewPanel.set_image(..., load_full=False, quick_size=<current thumb size>)`.
  - Do not start full-preview loading or focus extraction while `load_full=False`.
- Quick-preview fallback must stay bounded to target preview size. Do not synchronously decode a full large image as fallback.

## Validation

Run at least:

```powershell
.\.venv\Scripts\python.exe -m py_compile SuperViewer\superviewer\preview_panel.py SuperViewer\main.py app_common\file_browser\_panel.py
.\.venv\Scripts\python.exe -m pytest SuperViewer\tests\test_preview_panel_policy.py -q
git diff --check -- SuperViewer\superviewer\preview_panel.py SuperViewer\main.py SuperViewer\tests\test_preview_panel_policy.py
git -C app_common diff --check -- file_browser\_panel.py
```

Manual or logged checks should cover:

- normal click on a small non-RAW image
- normal click on a large non-RAW image
- RAW image preview, including a check that the final preview is the high-resolution embedded JPEG rather than a tiny EXIF thumbnail
- held direction-key navigation from second repeated item through key release
