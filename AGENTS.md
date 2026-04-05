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

- Current repository root: `/Users/oscar/Pictures/SuperApps/SuperBirdTools`
- Shared development virtual environment: `/Users/oscar/Pictures/SuperApps/SuperBirdTools/.venv`
- On macOS, prefer repo-root interpreter `/Users/oscar/Pictures/SuperApps/SuperBirdTools/.venv/bin/python3`
- On Windows 64-bit, prefer the repo-root interpreter `<repo>\.venv\Scripts\python.exe`; for this project that means `SuperBirdTools\.venv\Scripts\python.exe` under the Windows checkout path
- Unless a script explicitly requires an app subdirectory, run commands from the repository root above

## Monorepo Environment

- `.venv` is the preferred shared development virtual environment for this monorepo.
- Use `python init_dev.py` from `/Users/oscar/Pictures/SuperApps/SuperBirdTools` to initialize the shared `.venv` and fan out to app-level initialization.
- Root `init_dev.py` creates or reuses `/Users/oscar/Pictures/SuperApps/SuperBirdTools/.venv`, then re-executes inside that environment before calling app-level setup scripts.
- `SuperViewer/init_dev.py` installs only SuperViewer dependencies.
- `SuperViewer/init_dev.py` and `SuperBirdStamp/init_dev.py` reuse the repo-root `.venv` when running inside this monorepo; only fall back to an app-local `.venv` when used outside the monorepo.
- `SuperBirdStamp/init_dev.py` installs SuperBirdStamp dependencies and then prepares app-specific assets such as `yolo11n.pt` and ffmpeg.
- Downloaded development assets should not be added to git unless the user explicitly asks for that workflow.

## Packaging Layout

- Single-app build scripts should default to repository-root `dist/` and `build/`, not per-app output directories.
- Use `build_all.sh` for macOS full builds and `build_all.bat` for Windows full builds when the goal is to produce both apps together.
- On macOS, full build output should end up with `dist/SuperViewer.app` and `dist/SuperBirdStamp.app` at repo root.
- On macOS, aggregate size reduction is implemented via post-build hardlink deduplication; do not describe this as true cross-bundle shared runtime.
- On Windows, `build_all.bat` should prefer the merged spec workflow (`build_all_win_merged.spec`) so shared runtime files are referenced instead of duplicated when possible.
- Windows merged build outputs must be distributed together; do not assume one merged app directory is independently relocatable.

## Validation Minimum

- Run `py -3 -m py_compile` on changed Python files.
- For metadata changes: write + read-back verification with Chinese sample values.
- For `.spec` changes: packaged startup smoke test.
- For `init_dev.py` changes: run at least `python init_dev.py --dry-run` from `/Users/oscar/Pictures/SuperApps/SuperBirdTools`.
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
- Keep new GUI options feature reading from json config file @birdstamp/gui/resources/editor_options.json.
