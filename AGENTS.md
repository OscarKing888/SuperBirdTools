# AGENTS.md (Codex / OpenAI Coding Agents)

Follow [ai_rules/AI_CODING_RULES.md](ai_rules/AI_CODING_RULES.md) as the cross-tool coding baseline. This file is the authoritative project behavior and validation contract for **both** applications and `app_common`; architecture documents explain where that behavior is implemented.

## Start Here: Code And Documentation Map

- [SuperViewer architecture](SuperViewer/docs/ARCHITECTURE.md): directory selection, thumbnail/preview policy, metadata, tags/history, Qt workers and extension points.
- [SuperBirdStamp architecture](SuperBirdStamp/docs/ARCHITECTURE.md): editor mixins, workspace restore, metadata providers, processing stages, image/GIF/video exports and extension points.
- [Repository README](README.md): environment, startup and multi-app builds.
- [AI rules setup](ai_rules/AI_RULES_SETUP.md): instruction entry points and precedence. Keep architecture maps aligned when moving modules or adding a feature; link to symbols and files rather than copying implementations.
- Before modifying shared code, inspect `git status --short` in both the superproject and `app_common`. Commit shared changes in `app_common` first, then commit the superproject gitlink with its integration changes. Preserve known unrelated user modifications and stage explicit paths only. When feature-by-feature commits are requested, keep each independently reviewable fix and its tests together; do not push without authorization.

## Mandatory Project Constraints

- Keep files in UTF-8; avoid introducing mojibake.
- For ExifTool non-ASCII metadata writes, prefer UTF-8 temp-file redirection (`-Tag<=file`) over inline command args.
- Preserve Windows/macOS compatibility for paths and subprocess behavior.
- Ensure persistent external processes (like `exiftool -stay_open`) have explicit shutdown and are closed on exit.
- For packaged-only CUDA issues, first suspect packaging/runtime differences.
- In Windows PyInstaller spec for Torch/CUDA, keep `upx=False` unless explicitly re-validated.

## Current Workspace

- Treat `<repo>` as the active checkout root; do not hardcode a machine-specific checkout path in code or scripts.
- Example Windows checkout: `E:\SuperApps\SBT\SuperBirdTools`
- Example macOS checkout: `/Users/oscar/Pictures/SuperApps/SuperBirdTools`
- Shared development virtual environment: `<repo>/.venv`
- On Windows 64-bit, use `<repo>\.venv\Scripts\python.exe`
- On macOS, use `<repo>/.venv/bin/python3`
- Unless a script explicitly requires an app subdirectory, run commands from the repository root above.
- Resolve file links and commands against the current `<repo>` checkout.

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
- GUI tests must isolate runtime configuration **before window construction**. For BirdStamp, patch `birdstamp.config.get_user_data_dir` to a temporary directory; redirect local caches as needed. Disabling an autosave method after constructing the window is insufficient. Never remove or overwrite a pre-existing workspace/autosave merely to clean up a test.
- After GUI/template smoke checks, inspect `SuperBirdStamp/config/editor_export_state.json` and `SuperBirdStamp/config/templates/*.json` for unintended runtime state changes. Do not leave accidental config/template mutations in the diff; if a change might be user-authored, report it instead of silently reverting it.

## Packaging Layout

- Single-app build scripts should default to repository-root `dist/` and `build/`, not per-app output directories.
- Use `build_all.sh` for macOS full builds and `build_all.bat` for Windows full builds when the goal is to produce both apps together.
- On macOS, full build output should end up with `dist/SuperViewer.app` and `dist/SuperBirdStamp.app` at repo root.
- On macOS, aggregate size reduction is implemented via post-build hardlink deduplication; do not describe this as true cross-bundle shared runtime.
- On Windows, `build_all.bat` should prefer the merged spec workflow (`build_all_win_merged.spec`) so shared runtime files are referenced instead of duplicated when possible.
- Windows merged build outputs must be distributed together; do not assume one merged app directory is independently relocatable.
- When invoking PyInstaller directly on Windows, use `.\.venv\Scripts\python.exe -m PyInstaller ...`; for normal full builds prefer `.\build_all.bat` so the merged spec and repo-root `dist/` / `build/` layout are used consistently.
- A normal local `build_all.bat` run is incremental and must preserve `build/merged_win`; pass `--clean` only when a clean build is required.
- Use `build_all.bat --clean` after Python/dependency changes, spec or hook changes, module additions/removals/renames, CPU/CUDA environment changes, suspected stale cache, and for release validation.
- GitHub release builds must continue to invoke `build_all.bat --clean`; local incremental caching must not weaken clean release builds.
- Keep the two app Analysis/PYZ targets in separate merged-spec workpaths; sharing one `base_library.zip` makes each app invalidate the other's Analysis cache on every run.
- In `build_all_win_merged.spec`, collect Torch/Ultralytics before starting Viewer/Qt analysis as defense in depth. `build_all.bat` must also prepend the build-only `build_tools/pyinstaller_bootstrap` path so every PyInstaller isolated worker preloads the system MSVC runtime before PyQt/Torch imports.
- `.github/workflows/build-release.yml` is the release build source of truth: manual runs upload Actions artifacts, while valid `v*` tags also publish a GitHub Release.
- CI release builds use `build_tools/set_build_version.py` to synchronize the two app versions and BirdStamp's macOS bundle version without committing generated changes.
- Release staging must omit `SuperBirdStamp/config/editor_autosave.birdstamp-workspace.json` and `SuperBirdStamp/config/editor_export_state.json`; these files may contain user-specific paths and are runtime state, not distributable defaults.
- Existing Release assets are not overwritten automatically. Replace them only through an explicit manual maintenance step.
- `app_common/about_dialog/about.cfg` is the shared About fallback. SuperViewer and SuperBirdStamp each use an app-specific `about.cfg` at the app root; every app and merged spec must collect it and its referenced `images/` at the bundle resource root. Do not put About fields back into `super_viewer.cfg`.
- About cfg files are UTF-8 JSON. Keep them syntactically valid; parse failures must retain a file/line/column diagnostic instead of silently obscuring why `_DEFAULT_ABOUT` was used.

## Validation Minimum

- Run `<repo>\.venv\Scripts\python.exe -m py_compile ...` on changed Python files on Windows.
- Run `<repo>/.venv/bin/python3 -m py_compile ...` on changed Python files on macOS.
- From the repo root, `pytest.ini` supplies both `.` and `SuperBirdStamp` on `PYTHONPATH`; use `<repo>\.venv\Scripts\python.exe -m pytest ...` on Windows or `<repo>/.venv/bin/python3 -m pytest ...` on macOS.
- For headless Qt checks on Windows PowerShell, set `$env:QT_QPA_PLATFORM='offscreen'` before invoking pytest or a GUI smoke test.
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
  - For large HEIF/HIF, first reuse the exact selected-tier cache. If no cached quick image exists, show a loading placeholder and decode the full preview in the owned worker; Pillow HEVC thumbnailing would otherwise synchronously decode the entire image only to shrink it. Do not decode it twice to fabricate an intermediate quick frame.
  - RAW images should directly use the high-resolution embedded RAW preview JPEG when available; prefer exiftool/rawpy camera previews and treat tiny piexif EXIF thumbnails such as 160x120 only as last-resort fallbacks. Do not force a full RAW demosaic for ordinary preview switching.
- Held direction-key navigation:
  - The initial physical key step remains a normal committed selection. From the first auto-repeat step until physical release, preview must stay on the selected thumbnail-size image only.
  - `key_navigation_fps` is the target playback cadence, not merely an upper bound on OS keyboard repeat events. SuperViewer owns one precise application timer after auto-repeat begins, swallows later OS repeat press/release pairs, and must coalesce late ticks rather than queue catch-up frames.
  - Only a non-auto-repeat physical `KeyRelease` may stop playback and commit the final full selection. An `isAutoRepeat()` release must never emit `file_selected`, refresh image-info/EXIF, or start full/focus work.
  - Fast frames use `file_fast_preview_requested` with `PreviewPanel.set_image(..., load_full=False, quick_size=<current thumb size>)` or `file_fast_preview_pixmap_requested` with `PreviewPanel.set_quick_pixmap(...)` when the exact-tier pixmap is already decoded.
  - Fast lookup must prefer the exact selected `128/256/512/1024/2048` tier from memory, the correct per-file persistent cache, or the matching transient cache. SuperViewer-specific overrides must reuse the shared cache resolver and must not silently substitute a smaller tier.
  - While held, do not start full-preview loading, refresh info/EXIF, resolve or scan for focus sources, decode an original/RAW via ExifTool/rawpy, synchronously write a JPEG just to read it back, or synchronously flush per-frame logs. Aggregate hot-path performance logs instead.
  - An uncached fast frame must be prioritized for asynchronous thumbnail generation and may retain/skip the displayed frame; it must not block the GUI by decoding the original. Full selection work resumes once, for the final photo, after physical release.
  - A fast-only frame whose identity is the source path must re-enter the normal small/large/RAW loading policy on release; same-path short-circuiting must not downgrade it to async-only behavior.
  - Playback must stop cleanly on physical release, focus loss, view/directory changes, and shutdown.
- Application-driven key playback is an explicit SuperViewer capability. Shared `FileListPanel` defaults it off so SuperBirdStamp `PhotoListWidget` retains native direction-key, selection, and `currentItemChanged` behavior.
- Quick-preview fallback outside held playback must remain bounded to the target preview size; do not synchronously decode a full large image as the fallback.
- Any change touching `PreviewPanel.set_image`, `PreviewPanel.set_quick_pixmap`, `MainWindow._on_file_selected_from_list`, either fast-preview handler, or file-browser direction-key handling must include `SuperViewer/tests/test_preview_panel_policy.py`, `SuperViewer/tests/test_fast_preview_policy.py`, `app_common/tests/test_file_browser_key_navigation.py`, and a manual or logged check of normal click, large image, RAW, held navigation, and final release.

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
- RAW/PNG/HEIF and other non-JPEG memory-cache entries must record the largest satisfied request tier. A cached 128 image is a miss for a later 512/1024/2048 request and must be upgraded; a later small request must not replace a larger base image.
- The thumbnail QImage memory cache uses an adaptive budget of up to 25% of detected RAM with a 16 GB hard cap. Keep its documentation, implementation, byte accounting, and eviction tests aligned when changing the policy.
- Metadata reading and persistent thumbnail generation have separate worker budgets and may run concurrently; do not make all thumbnail generation wait for the complete metadata pass. Default metadata workers are roughly `CPU/4` capped at 8; default persistent-thumbnail workers use the remaining CPU threads, e.g. 32 total gives 8 metadata and 24 thumbnail workers. Keep progress text showing the active thread count for both.
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
- File browser/cache changes: run `app_common/tests/test_file_browser_cache_paths.py`, `app_common/tests/test_file_browser_metadata.py`, `app_common/tests/test_file_browser_burst_display.py`, `app_common/tests/test_thumbnail_memory_cache.py`, and `app_common/tests/test_superviewer_user_options.py`.
- Preview/key-playback changes: run `app_common/tests/test_file_browser_key_navigation.py`, `app_common/tests/test_preview_canvas_hot_path.py`, `SuperViewer/tests/test_preview_panel_policy.py`, `SuperViewer/tests/test_fast_preview_policy.py`, `SuperBirdStamp/tests/test_editor_photo_list_navigation_compat.py`, and `SuperBirdStamp/tests/test_editor_preview_grid.py`; manually/log-check normal small image, large image, RAW, held playback at representative FPS values, auto-repeat releases, and the single final full commit.
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

- `SuperBirdStamp/birdstamp/image_pipeline/` is the interface source of truth for the image processing pipeline. New processing steps must be modeled as `ImageProcStage` implementations that receive and return an `ImageProcContext`.
- `ImageProcContext` is the shared processing state. Use it to carry the current `PIL.Image`, `source_path`, full `source_paths`, list index, raw metadata, normalized metadata context, template/photo info, normalized settings, precomputed values, crop plan, crop box, outer padding, original source size, and shared caches/locks.
- Terminal exporters must be represented by `ImageProcExportStage` subclasses. The editor may still own file dialogs and worker orchestration, but PNG/JPG image, GIF, and video export choices must be exposed as mutually exclusive export stages.
- Keep pipeline/core processing independent from Qt widgets. GUI code may build settings and display options, but image processing logic should live in pipeline stages or reusable non-widget helpers.
- The default export pipeline is built by `build_default_image_proc_pipeline()` in `birdstamp.export_stage.pipeline` and currently runs:
  - `ImageProcTemplateCropStage`
  - `ImageProcResizeLimitStage`
  - `ImageProcTemplateOverlayStage`
  - `ImageProcFocusOverlayStage`
- Existing image, GIF, and video export rendering should continue to converge through `VideoFrameJob -> render_video_frame() -> default image pipeline`. Do not add new export-only rendering behavior directly inside GUI handlers when it can be a stage.
- Stage parameters must be represented as normalized settings and exposed through `ImageProcStage.ui_descriptor()` / `ImageProcOptionSpec` so the global export UI can render or persist them consistently.
- The editor UI must display non-export stage settings in the current `ImageProcStage` order. Reordering stages must update `pipeline_stage_order`, dirty cached exports, and preserve the single selected `ImageProcExportStage` at the terminal export step.
- Each optional stage should have an explicit enabled setting key. When adding a new stage or stage parameter, update render-setting normalization and frame/cache signatures so cached frames are invalidated when that option changes.
- Batch/list-level work such as maximum-size precomputation, uniform auto-crop, crop-center stabilization, or future de-jitter should use pipeline batch hooks where available or the shared `prepare_uniform_auto_crop_plans()` precomputation path and precomputed context/job values instead of duplicating ad-hoc loops in GUI code.
- Template crop remains the default crop implementation stage. If crop semantics change, preserve photo-level crop overrides, `no_crop`, `free` ratio, custom center, focus center, bird center, crop padding, and uniform auto-crop behavior.
- Overlay changes must preserve the protected preview/export behavior: Banner/text/focus export should be controlled through pipeline settings, and preview behavior must be explicitly kept in sync or intentionally documented when it differs.
- New pipeline stages should include focused tests in `SuperBirdStamp/tests/test_image_pipeline.py` or a nearby test module. For export behavior changes, also cover relevant `render_video_frame`, GIF/video frame cache, and uniform auto-crop paths.
- Validation for pipeline changes must include repo-root `.venv` `py_compile` on changed Python files. If `pytest` is unavailable in `.venv`, run focused Python assertions with the repo-root `.venv` interpreter and report that pytest was unavailable.

## Directory Switching And Qt Worker Ownership

- GUI directory/selection handlers must not wait for a shared ExifTool session held by an earlier metadata batch. Render cached/report-backed fields immediately and schedule missing metadata in a worker; HIF directories can contain thousands of files even when the selected directory itself contains no photos.
- Distinguish directory scan time, report-cache load, GUI list application, selected-image decode, and ExifTool lock wait in diagnostics. A supported extension alone does not prove responsive decoding. Reproduce with temporary image copies and read-only metadata queries; never use a production photo library as a write test.
- Keep a QThread owned until its actual `QThread.finished` callback has been handled. A custom result/finished signal and `isRunning() == False` are not sufficient to hand pending work to a new owner: a queued old `finished` can otherwise steal the new request.
- Result and completion callbacks must verify worker identity/request generation before mutating current state or starting pending work. Shutdown is latched, clears pending work, stops owned timers, rejects late callbacks and retains every running worker until safe finalization; timeout is not permission to destroy a live QThread.
- Background metadata/tag updates must refresh only the affected display fields. Do not reset an in-progress filename/comment, cursor, focus or text undo history by calling a full photo refresh for a tag-only signal.

## Tags, History And Theme Contracts

- `SuperViewer/superviewer/photo_tags.py` owns tag config and XMP tag storage; `tagged_file_list.py` coordinates cache/filter/UI work, while `photo_tag_commands.py` supplies per-path commands to `app_common/command_history.py`.
- Indented `tags.cfg` groups are navigation/filter structure; only leaf labels are writable. Flat files remain compatible. Searching uses AND across whitespace-separated tokens, and each token may match a different field (filename, comment or tag).
- Before an edit, read subjects strictly. An unreadable/malformed existing XMP must fail the edit, not masquerade as an empty tag set. Preserve unknown subjects and unrelated metadata; missing source images must not create orphan XMP.
- Undo/redo stores the actual previous membership for each successful file, not one assumed state for a batch. Preserve partial successes and retryable failures, keep no-op edits from clearing redo, and bound history to 100 entries. Rename or a changed tag scope/vocabulary invalidates history; ordering-only changes do not.
- The Viewer edit toolbar and menu share `TagHistoryActions` actions. Create the toolbar once, independently of menu rebuilds; keep native text undo/redo shortcuts available while an editor has focus. Toolbar buttons operate tag history.
- Per-path tag generations prevent in-flight metadata/tag batches from overwriting local edits, undo or redo. Viewer ordinary metadata edits use a per-path field overlay for the active directory, merged over late batches; changing the directory or forcing a reload clears that overlay. Use actual `QThread.finished` for loader handoff, and never restart pending tag work after shutdown.
- System theme changes are style-only. `app_common/qt_theme.py` provides shared colors and `SuperViewer/superviewer/ui_theme.py` owns Viewer propagation. Do not reread images/EXIF or rebuild editable panels to change colors. Deferred stylesheet refreshes must use a panel-owned, cancellable timer; weak callbacks must not retain deleted widgets.

## Data Retention On Failure

- In a failed cut/move rollback, a staging or destination file may be the only complete original. Keep that recovery copy and report its path even when a partial source file exists. Clean up staging only after confirmed restoration/success; cover real photo + XMP pairs with injected I/O failures.
- XMP editing must handle property attributes and multiple same-photo RDF descriptions, retain unrelated resource descriptions/namespaces, and prefer `x-default` text. Clearing the default translation must not resurrect an old translated value. Verify Chinese writes and clears by reading the actual sidecar back.
- `report.db` fallback reads must open SQLite read-only and must not initialize or migrate schemas, create indexes, set writable journal policy or repair stored paths. Compatibility readers should handle missing optional columns without changing the file.
- Generic ExifTool assignments must be validated on a same-directory staging sidecar before committing; a zero exit code alone does not prove every tag was accepted. Mixed native/generic edits fail together. Keep ExifTool command names distinct from XML properties (ISO versus ISOSpeedRatings; Lens versus legacy LensModel), use UTF-8 arguments, and reject unmapped file-operation pseudo-tags.
- Hardlink deduplication must create the replacement link before atomically replacing a destination. Link/replace failures must leave both build artifacts intact.
- BirdStamp workspace restoration is asynchronous: suspend automatic and manual saves until the full restore finishes. Closing midway preserves the last complete autosave and cancels remaining restore/autosave callbacks.
- Export temporary directories need an owner immediately after creation, including failure/cancel paths before a render plan is returned. Output names are allocated with Unicode normalization and case-insensitive collision detection before parallel writes.
- GIF timing is quantized on cumulative 10 ms boundaries, not truncated per frame. Input rates above 100 FPS are sampled to the GIF timeline, preserving requested total duration within quantization error; report actual FPS/frame count. A nonempty clip shorter than 10 ms still needs one 10 ms frame. Keep all size variants on the same timeline.

## Qt And Failure Regression Tests

- Keep a strong reference to one `QApplication` for the complete test process; multiple short-lived application instances can invalidate widgets across modules.
- Wait for the actual Qt state/owned timer/worker completion with a bounded event-processing loop. Do not assert asynchronous theme/worker outcomes after an arbitrary fixed sleep.
- Use real temporary image/XMP/workspace files and controlled failures for data-retention bugs; use controllable real QThreads to test queued-completion races. Test success, stale results and shutdown as well as the failure itself.
- For review fixes, run targeted tests first, then the repo-root full suite with `QT_QPA_PLATFORM=offscreen`; inspect both git working trees and runtime config afterwards. Report platform-specific validation limits honestly (offscreen tests do not replace Windows/macOS packaged UI smoke tests).
