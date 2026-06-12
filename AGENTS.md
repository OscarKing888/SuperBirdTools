# AGENTS.md (Codex / OpenAI Coding Agents)

Follow `ai_rules/AI_CODING_RULES.md` as the project baseline.

## Mandatory Project Constraints

- Keep files in UTF-8; avoid introducing mojibake.
- For ExifTool non-ASCII metadata writes, prefer UTF-8 temp-file redirection (`-Tag<=file`) over inline command args.
- Preserve Windows/macOS compatibility for paths and subprocess behavior.
- Ensure persistent external processes (like `exiftool -stay_open`) have explicit shutdown and are closed on exit.
- For packaged-only CUDA issues, first suspect packaging/runtime differences.
- In Windows PyInstaller spec for Torch/CUDA, keep `upx=False` unless explicitly re-validated.

## Current Workspace

- Current Windows checkout root: `E:\SuperApps\SBT\SuperBirdTools`
- Current macOS checkout root: `/Users/oscar/Pictures/SuperApps/SuperBirdTools`
- Shared development virtual environment: `<repo>/.venv`
- On Windows 64-bit, use the repo-root interpreter `E:\SuperApps\SBT\SuperBirdTools\.venv\Scripts\python.exe`
- On macOS, use the repo-root interpreter `/Users/oscar/Pictures/SuperApps/SuperBirdTools/.venv/bin/python3`
- Unless a script explicitly requires an app subdirectory, run commands from the repository root above.
- Treat `<repo>` as the active checkout root for Codex file links and commands. In this Windows checkout, prefer paths under `E:\SuperApps\SBT\SuperBirdTools`.

## Monorepo Environment

- `.venv` is the preferred shared development virtual environment for this monorepo.
- Use the repo-root `.venv` for normal app execution, `py_compile`, smoke checks, tests, PyInstaller invocations, and ad-hoc Python probes.
- Do not use `py -3`, global `python`, or another activated environment for validation when `.venv` exists. On Windows in particular, `py -3` may resolve to a global Python without project dependencies such as PIL/PyQt/pytest.
- Use `python init_dev.py` only for first-time/bootstrap initialization when `.venv` does not exist or needs repair; otherwise invoke scripts through the repo `.venv` interpreter.
- Root `init_dev.py` creates or reuses `<repo>/.venv`, then re-executes inside that environment before calling app-level setup scripts.
- `SuperViewer/init_dev.py` installs only SuperViewer dependencies.
- `SuperViewer/init_dev.py` and `SuperBirdStamp/init_dev.py` reuse the repo-root `.venv` when running inside this monorepo; only fall back to an app-local `.venv` when used outside the monorepo.
- `SuperBirdStamp/init_dev.py` installs SuperBirdStamp dependencies and then prepares app-specific assets such as `yolo11n.pt` and ffmpeg.
- Downloaded development assets should not be added to git unless the user explicitly asks for that workflow.

## Windows / PowerShell Command Rules

- The default Windows shell is PowerShell. Do not use bash heredoc syntax such as `python - <<'PY'`; PowerShell treats `<` as a redirection operator and fails before Python starts.
- For inline Python in PowerShell, pipe a here-string into the repo `.venv` interpreter:
  `@' ... '@ | .\.venv\Scripts\python.exe -`
- Keep command working directory at the repo root unless the command requires an app subdirectory.
- If pytest is missing from `.venv`, report that pytest is unavailable instead of retrying with global Python. Direct `py_compile` and focused Python assertions are acceptable fallback validation.
- Remove runtime autosave artifacts created during GUI smoke checks, such as `SuperBirdStamp/config/editor_autosave.birdstamp-workspace.json`, unless the user explicitly asks to keep them.
- After GUI/template smoke checks, inspect `SuperBirdStamp/config/editor_export_state.json` and `SuperBirdStamp/config/templates/*.json` for unintended runtime state changes. Do not leave accidental config/template mutations in the diff; if a change might be user-authored, report it instead of silently reverting it.

## Packaging Layout

- Single-app build scripts should default to repository-root `dist/` and `build/`, not per-app output directories.
- Use `build_all.sh` for macOS full builds and `build_all.bat` for Windows full builds when the goal is to produce both apps together.
- On macOS, full build output should end up with `dist/SuperViewer.app` and `dist/SuperBirdStamp.app` at repo root.
- On macOS, aggregate size reduction is implemented via post-build hardlink deduplication; do not describe this as true cross-bundle shared runtime.
- On Windows, `build_all.bat` should prefer the merged spec workflow (`build_all_win_merged.spec`) so shared runtime files are referenced instead of duplicated when possible.
- Windows merged build outputs must be distributed together; do not assume one merged app directory is independently relocatable.
- When invoking PyInstaller directly on Windows, use `.\.venv\Scripts\python.exe -m PyInstaller ...`; for normal full builds prefer `.\build_all.bat` so the merged spec and repo-root `dist/` / `build/` layout are used consistently.

## Validation Minimum

- These validation interpreter rules override older `py -3` examples in `ai_rules/AI_CODING_RULES.md`.
- Run `<repo>\.venv\Scripts\python.exe -m py_compile ...` on changed Python files on Windows.
- Run `<repo>/.venv/bin/python3 -m py_compile ...` on changed Python files on macOS.
- For metadata changes: write + read-back verification with Chinese sample values.
- For `.spec` changes: packaged startup smoke test.
- For `init_dev.py` changes: run at least `.venv\Scripts\python.exe init_dev.py --dry-run` on Windows or `.venv/bin/python3 init_dev.py --dry-run` on macOS from the repo root when `.venv` exists.
- For `build_all.*` changes: verify the final repo-root `dist/` layout matches the intended multi-app output.

## Merged SuperViewer / app_common Rules

- `app_common` is the shared codebase used by SuperViewer and SuperBirdStamp. In this superproject, keep `.gitmodules` on `branch = main`; do not switch the submodule/gitlink to feature branches such as `res_mgr`.
- `app_common.image_formats` is the source of truth for supported image extensions. Scanning, thumbnailing, preview decoding, PSD/HEIF/RAW handling, and SuperBirdStamp discovery should use its extension groups instead of local duplicate tuples.
- `run.bat` should keep resolving Python in this order when possible: `PYTHON_EXE`, `VIRTUAL_ENV`, the repo-root `.venv`, then any documented fallback. It should default SuperViewer logging to `logs\SuperViewer.log`.

## Protected SuperViewer Preview Loading Flow

- This is a protected behavior. Do not replace it with "always show thumbnail", "always sync load full image", or "always async full image" simplifications.
- Preserve `PreviewPanel.set_image(path, *, load_full=True, quick_size=None)` and keep `FileListPanel.preview_quick_size()` as the bridge from the selected thumbnail-size level to preview loading.
- Normal single-image selection:
  - Non-RAW images at or below `SuperViewer_SYNC_FULL_PREVIEW_MAX_MP` (default 40 MP) should synchronously show the full preview image.
  - Non-RAW images above that threshold should first show the selected thumbnail-size preview (`128/256/512/1024/2048`) and then asynchronously replace it with the full preview.
  - RAW images should directly use the high-resolution embedded RAW preview JPEG when available; prefer exiftool/rawpy camera previews and treat tiny piexif EXIF thumbnails such as 160x120 only as last-resort fallbacks. Do not force a full RAW demosaic for ordinary preview switching.
- Held direction-key navigation:
  - Starting from the second auto-repeat image and until key release, preview must stay on the selected thumbnail-size image only.
  - This path uses `file_fast_preview_requested` and `PreviewPanel.set_image(..., load_full=False, quick_size=<current thumb size>)`.
  - While `load_full=False`, do not start full-preview loading and do not start focus extraction. Full selection work resumes only after key release commits the final image.
- Quick-preview fallback must remain bounded. If thumbnail extraction/cache misses, use scaled reading at the target preview size; do not synchronously decode a full large image as the quick-preview fallback.
- Any change touching `PreviewPanel.set_image`, `MainWindow._on_file_selected_from_list`, `MainWindow._on_file_fast_preview_requested`, or file-browser direction-key handling must include `SuperViewer/tests/test_preview_panel_policy.py` and a manual or logged check of normal click, large image, RAW, and held-direction-key navigation.

## Metadata And Sidecars

- XMP sidecar is the only writable metadata sidecar path. Do not reintroduce `.superviewer.json`, JSON sidecar helpers/tests, or JSON sidecar copy/move/delete behavior.
- Do not write user metadata back into RAW/original image files. EXIF/XMP writes for title, description/comment, tags, rating, pick, camera-related editable fields, and other SuperViewer/SuperBirdStamp metadata must go through `PhotoMetaDataXMP` sidecar helpers.
- `report.db` is read-only fallback/hydration input for these apps. Do not restore old report.db write-back paths for user edits, and do not remove existing report.db compatibility reads.
- When a sidecar is created or modified and lacks a bird-species marker, `PhotoMetaDataXMP` should hydrate missing non-empty `PHOTO_COLUMNS` values from the matching `report.db` row into `XMP-superpicky:<column>` without overwriting existing sidecar fields or the current user edit. Numeric `0` and `False` are valid values.
- `PhotoMetaDataXMP.read()` / `xmp_sidecar.py` must preserve the custom namespace `https://superbirdtools.local/xmp/superpicky/1.0/`, expose `XMP-superpicky:*` keys, and mirror those fields back to raw `<column>` keys for UI/template callers.
- XMP-compatible keys such as title/bird name, description, rating, pick, camera, lens, ISO, shutter, aperture, focal length, GPS, capture time, sharpness/aesthetic/focus should keep their existing standard/compatibility mappings in addition to any `XMP-superpicky:*` raw-column storage.
- File copy, move, rename, cut, and delete operations should keep same-stem `.xmp` sidecars aligned with the photo. Deletion should continue to use the current trash/Send2Trash semantics and must not reintroduce `.superpicky/deleted`.
- F10-style read-only permission gates are not part of the selected merge. Do not add `_permissions.py` read-only UI gating unless the user explicitly asks for that feature.

## SuperViewer File Browser And Caches

- File browser metadata columns must keep important camera/report fields available: burst, aperture, shutter, ISO, focal length, lens, camera, capture time, sharpness, aesthetic score, and focus status.
- Burst display format is `({burst_position}/{burst_id})`, with `-` for a missing side. List mode uses a dedicated "连拍" column after filename; thumbnail mode prefixes the bottom filename text and must not draw a large overlay on the image.
- Metadata values should be resolved from XMP/sidecar first, then file metadata, then `report.db` fallback. Browser metadata should recognize raw keys, `report.*`, and `XMP-superpicky:*`.
- Persistent thumbnail caches are per-file `.superpicky` scopes, not one cache root for the selected directory. If subdirectories each have their own `.superpicky`, each image writes to its associated `.superpicky\thumb_cache\<size>`.
- Reuse an ancestor `.superpicky` only when it contains `report.db` and the selected directory is no more than 3 levels below the volume root such as `F:\A\B\C`. Do not interpret this as walking only 3 levels upward from a deep selected directory.
- If no valid `.superpicky` scope exists and persistent cache writing is needed, the UI may ask to create `<selected_dir>\.superpicky`; create cache directories only, not `report.db`. If the user declines or creation fails, fall back to local app cache and skip persistent pre-generation for those images for the session.
- Persistent thumbnail size levels are `128, 256, 512, 1024, 2048`. Background generation should continue after selecting a directory and should also generate newly enabled levels when the user raises the max size.
- Metadata reading and persistent thumbnail generation have separate worker budgets. Default metadata workers are roughly `CPU/4` capped at 8; default persistent-thumbnail workers use the remaining CPU threads, e.g. 32 total gives 8 metadata and 24 thumbnail workers. Keep progress text showing the active thread count for both.
- Environment overrides currently include `SuperViewer_METADATA_WORKERS` and `SuperViewer_PERSISTENT_THUMB_WORKERS`; preserve them when changing worker configuration.

## SuperViewer Image Info UI

- The right-side image information tabs should create both the image-info panel and EXIF panel by default.
- `ImageInfoTabPanel_ImageInfo` should show the same important camera/report fields as the file list where applicable, including aperture, shutter, ISO, focal length, lens, camera, capture time, burst, sharpness/aesthetic/focus, and should not depend on a quick-thumbnail pixmap for true image dimensions.
- Tags, comments/descriptions, ratings, and pick state should remain XMP sidecar writes. Tag config should prefer the nearest `.superpicky/tags.cfg` and fall back to `SuperViewer/tags.cfg`.

## SuperBirdStamp Metadata Templates

- Template metadata resolution priority is `ExifTemplateContextProvider > FromFileTemplateContextProvider > ReportDBTemplateContextProvider > EditorTemplateContextProvider`. Keep `AutoProxyTemplateContextProvider` and `SuperBirdStamp/config/template_context_routes.json` aligned with that order.
- `ExifTemplateContextProvider` should consider `XMP-superpicky:<PHOTO_COLUMN>` candidates for canonical report fields, while `report.<column>` remains available as lower-priority fallback.
- `_CANONICAL_META_FIELD_DEFINITIONS` should stay broad enough to cover `PHOTO_COLUMNS`, including path fields, burst fields, created/updated timestamps, confidence/has_bird, camera/lens/exposure fields, and other report-only values that templates may need.
- SuperBirdStamp metadata loading should continue to merge `app_common.exif_io.read_batch_metadata()` / XMP sidecar values so sidecar fields win over file-derived values, and both win over report.db/editor fallbacks.

## Recommended Regression Checks For Merged Features

- Metadata/sidecar changes: run `app_common/tests/test_photo_meta_proxy.py`, `app_common/tests/test_file_utils.py`, `SuperViewer/tests/test_photo_tags.py`, and `SuperViewer/tests/test_image_info_metadata.py`.
- File browser/cache changes: run `app_common/tests/test_file_browser_cache_paths.py`, `app_common/tests/test_file_browser_metadata.py`, `app_common/tests/test_file_browser_burst_display.py`, and `app_common/tests/test_superviewer_user_options.py`.
- Preview loading changes: run `SuperViewer/tests/test_preview_panel_policy.py` and manually/log-check normal small image, large image, RAW, and held-direction-key navigation.
- Template-context changes: run `SuperBirdStamp/tests/test_template_context_report_db.py`.
- Format/decoder changes: run `app_common/tests/test_image_formats.py` and `SuperBirdStamp/tests/test_psd_decoder.py` when available.
- Always include `git diff --check` in both the main repo and `app_common` after edits that touch shared browser/metadata code.

## Protected Preview Overlay Flow

- `app_common.preview_canvas` is the shared source of truth for preview composition-grid / 9-grid overlay behavior; do not silently reduce supported grid modes to a disabled-only state.
- `PreviewOverlayOptions` / `PreviewCanvas` composition-grid fields and `render_source_pixmap_with_overlays()` / `save_source_pixmap_with_overlays()` are protected behavior because SuperViewer preview display and overlay export both depend on them.
- `SuperViewer` preview composition grids must continue to show in both the toolbar selector and the actual preview canvas.
- `SuperBirdStamp` preview composition grids must continue to be available in the main editor and template preview, and must be drawn only inside the active crop box rather than over the full preview image.
- Any change touching `app_common.preview_canvas`, `SuperViewer` preview overlay UI, or `SuperBirdStamp` preview canvas/toolbar must include a regression check for:
  - SuperViewer grid selector options visible + selected grid actually rendered.
  - SuperBirdStamp grid selector options visible + selected grid rendered only within crop bounds.
  - Overlay export path still includes active composition grids.

## New Feature: GUI Options
- Keep new GUI options feature reading from `SuperBirdStamp/config/editor_options.json` via `birdstamp.config.resolve_bundled_path("config", "editor_options.json")`.

## SuperBirdStamp Image Processing Pipeline

- `SuperBirdStamp/birdstamp/image_pipeline.py` is the interface source of truth for the image processing pipeline. New processing steps must be modeled as `ImageProcStage` implementations that receive and return an `ImageProcContext`.
- `ImageProcContext` is the shared processing state. Use it to carry the current `PIL.Image`, `source_path`, full `source_paths`, list index, raw metadata, normalized metadata context, template/photo info, normalized settings, precomputed values, crop plan, crop box, outer padding, original source size, and shared caches/locks.
- Terminal exporters must be represented by `ImageProcExportStage` subclasses. The editor may still own file dialogs and worker orchestration, but PNG/JPG image, GIF, and video export choices must be exposed as mutually exclusive export stages.
- Keep pipeline/core processing independent from Qt widgets. GUI code may build settings and display options, but image processing logic should live in pipeline stages or reusable non-widget helpers.
- The default export pipeline is built by `build_default_image_proc_pipeline()` in `birdstamp.video_export` and currently runs:
  - `TemplateCropStage`
  - `ResizeLimitStage`
  - `TemplateOverlayStage`
  - `FocusOverlayStage`
- Existing image, GIF, and video export rendering should continue to converge through `VideoFrameJob -> render_video_frame() -> default image pipeline`. Do not add new export-only rendering behavior directly inside GUI handlers when it can be a stage.
- Stage parameters must be represented as normalized settings and exposed through `ImageProcStage.ui_descriptor()` / `ImageProcOptionSpec` so the global export UI can render or persist them consistently.
- The editor UI must display non-export stage settings in the current `ImageProcStage` order. Reordering stages must update `pipeline_stage_order`, dirty cached exports, and preserve the single selected `ImageProcExportStage` at the terminal export step.
- Each optional stage should have an explicit enabled setting key. When adding a new stage or stage parameter, update render-setting normalization and frame/cache signatures so cached frames are invalidated when that option changes.
- Batch/list-level work such as maximum-size precomputation, uniform auto-crop, crop-center stabilization, or future de-jitter should use `process_batch()` or precomputed context/job values instead of duplicating ad-hoc loops in GUI code.
- Template crop remains the default crop implementation stage. If crop semantics change, preserve photo-level crop overrides, `no_crop`, `free` ratio, custom center, focus center, bird center, crop padding, and uniform auto-crop behavior.
- Overlay changes must preserve the protected preview/export behavior: Banner/text/focus export should be controlled through pipeline settings, and preview behavior must be explicitly kept in sync or intentionally documented when it differs.
- New pipeline stages should include focused tests in `SuperBirdStamp/tests/test_image_pipeline.py` or a nearby test module. For export behavior changes, also cover relevant `render_video_frame`, GIF/video frame cache, and uniform auto-crop paths.
- Validation for pipeline changes must include repo-root `.venv` `py_compile` on changed Python files. If `pytest` is unavailable in `.venv`, run focused Python assertions with the repo-root `.venv` interpreter and report that pytest was unavailable.
