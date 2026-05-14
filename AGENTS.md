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

- Current Windows checkout root: `E:\SuperApps\SuperBirdTools`
- Current macOS checkout root: `/Users/oscar/Pictures/SuperApps/SuperBirdTools`
- Shared development virtual environment: `<repo>/.venv`
- On Windows 64-bit, use the repo-root interpreter `E:\SuperApps\SuperBirdTools\.venv\Scripts\python.exe`
- On macOS, use the repo-root interpreter `/Users/oscar/Pictures/SuperApps/SuperBirdTools/.venv/bin/python3`
- Unless a script explicitly requires an app subdirectory, run commands from the repository root above.
- Treat `<repo>` as the active checkout root for Codex file links and commands. In this Windows checkout, prefer paths under `E:\SuperApps\SuperBirdTools`.

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
