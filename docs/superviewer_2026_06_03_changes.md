# SuperViewer Changes - 2026-06-03

This note summarizes the SuperViewer work completed on 2026-06-03 and the
behaviors future changes should preserve.

## Image Formats

- Centralized supported image extensions in `app_common/image_formats.py`.
- Added Photoshop `.psd` as a supported image format.
- Kept basic PSD support dependency-free beyond Pillow by using the flattened
  PSD image path.
- Confirmed HEIF/HIF support depends on `pillow-heif`.
- Confirmed RAW full-preview support depends on `rawpy`.

## Preview Loading

- Preserved the two-stage preview contract:
  - First frame: synchronously show a small cached thumbnail.
  - Final selected image: asynchronously decode and replace with full-size image.
- Kept keyboard fast navigation on thumbnail-only preview through
  `PreviewPanel.set_image(..., load_full=False)`.
- Extended the asynchronous full-preview worker:
  - Qt reader remains the normal path.
  - HIF/HEIC/HEIF fall back to Pillow with `pillow-heif`.
  - RAW files fall back to `rawpy`.
  - PSD and other Pillow-readable files fall back to Pillow when Qt fails or
    returns an obviously undersized image.
- Verified HIF and RAW samples load at full dimensions rather than staying on
  128/512 thumbnail previews.

## Directory Browser And File List

- Directory selection now recursively finds supported image files in child
  directories.
- Large recursive scans are handled asynchronously so the UI can continue to
  show progress instead of freezing.
- Status/progress messages now cover scan and thumbnail phases, for example:
  - `正在查找所有图像...`
  - `正在准备生成缩略图...`
  - `生成预览缩略图 5/9988...`
- Switching directories should raise the new directory's thumbnail priority
  without canceling already queued thumbnail work for previous directories.
- Selecting an image in the file list syncs/expands the directory browser to the
  image's containing directory.
- Large-list metadata/tag cache work was moved away from one synchronous UI
  blocking pass and should remain incremental or backgrounded.

## Metadata And Sidecars

- Added JSON sidecar support beside the existing XMP sidecar path.
- SuperViewer metadata writes now target the JSON sidecar path where applicable,
  while existing XMP read/write helpers remain available for compatibility.
- JSON sidecar filename convention:
  - `<image filename>.superviewer.json`
  - Example: `GreenCheck.psd.superviewer.json`
- Metadata reads should continue to merge/read existing XMP values and JSON
  sidecar values without losing comment/title/tag semantics.
- Copy/paste and delete/trash operations now need to move related sidecars with
  the source image:
  - `<image>.xmp`
  - `<image filename>.superviewer.json`
- `.superpicky/deleted` moves must preserve the source-relative directory layout
  for both the image and all related sidecars.

## Diagnostics And Build

- `run.bat` now routes SuperViewer logs through `APP_COMMON_LOG_FILE` to
  `logs/SuperViewer.log`, making local UI-freeze diagnostics reproducible.
- Performance probes remain opt-in and should not add steady-state overhead when
  disabled.
- Windows full build was run through `build_all.bat` after the changes.
- Build outputs:
  - `dist/SuperViewer/SuperViewer.exe`
  - `dist/SuperBirdStamp/SuperBirdStamp.exe`

## Regression Checks To Keep

- `PreviewPanel.set_image(path, load_full=False)` must not start a full-preview
  timer or full-image worker.
- Final committed selection must eventually attempt full-size decode.
- HIF/HIF and RAW full-preview samples should report original dimensions in the
  preview status label.
- Large directories such as `E:\_birds` must show scan/listing progress and not
  block the UI with a single synchronous apply step.
- Copy/delete operations must include both XMP and JSON sidecars.
- Metadata changes with Chinese text still need write/read-back validation.
