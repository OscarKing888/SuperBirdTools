# AGENTS.md (Codex / OpenAI Coding Agents)

This file is the authoritative behavior and validation contract for the **img_mgr / res_mgr image-manager checkout**. Follow [ai_rules/AI_CODING_RULES.md](ai_rules/AI_CODING_RULES.md) as the cross-tool baseline. The [Viewer architecture map](SuperViewer/docs/ARCHITECTURE.md) explains implementation locations; it does not override these requirements.

## Scope and workspace

- This branch contains one application, `SuperViewer`, plus root-level `app_common`. Do not restore removed applications or a multi-application build as a side effect of a backport.
- `<repo>` means the active checkout. Never hardcode another checkout's path or interpreter.
- Use `<repo>/.venv` for execution, tests, compilation and packaging. Windows: `.venv\Scripts\python.exe`; macOS: `.venv/bin/python3`. Use global Python only to bootstrap a missing environment through `init_dev.py`.
- Before changing shared code, inspect `git status --short` in both the root and `app_common`. Preserve unrelated user changes. Shared work is committed in the shared repository before any root integration reference, when commits are authorized.
- `.gitmodules` targets `res_mgr`; do not switch it to main or replace the existing shared checkout to make a merge easier. Check whether the root actually tracks a gitlink before assuming submodule commands will initialize it.
- Prefer feature-level adaptation over whole-file replacement from the other branch. Keep the target's JSON, permissions, cache scope, report and preview behavior unless the user explicitly requests a change.

## Encoding, processes and builds

- Keep UTF-8 without introducing mojibake; preserve existing line endings and unrelated text.
- ExifTool writes containing non-ASCII text should use UTF-8 temporary-file redirection such as `-XMP:Title<=file`, not inline values.
- Preserve Windows/macOS paths and hidden background subprocess behavior. Persistent ExifTool processes need explicit shutdown.
- The current `build_all.bat` uses `SuperViewer/SuperViewer_win.spec`; `build_all.sh` runs the Viewer macOS script. Both produce one application under root `dist/`, with intermediates in root `build/` by default.
- Windows builds are incremental unless `--clean` is requested. A clean build deletes the configured output/build directories: verify resolved paths before recursive deletion, including environment overrides.
- If touching a Torch/CUDA packaging path, keep `upx=False` unless revalidated; investigate packaged runtime differences before changing algorithms. Do not add unrelated model dependencies to Viewer.

## Metadata, sidecars and permissions

- New Viewer tag/comment/rating/Pick metadata writes use `PhotoMetaDataJSON` and the existing writer routes. Keep XMP compatibility and collaborative edit journals; do not replace the target with the main branch's XMP-only policy.
- Resolve JSON paths with `app_common.exif_io.json_sidecar`: central `.superpicky/<configured-dir>/<relative-image-path>.superviewer.json` first, legacy sibling `<image filename>.superviewer.json` as read fallback. The default directory is `metadata`; `.superpicky/config.ini` supports `[sidecar] dir` as a safe relative path within `.superpicky`.
- Use the nearest library `.superpicky` scope. Do not substitute the other branch's report.db-dependent ancestor rule or volume-depth limit.
- Subject writes preserve non-configured subjects and unrelated JSON fields. History snapshots use strict JSON/XMP reads; malformed or unreadable sidecars are failures, not empty tag sets. Missing source photos must not create orphan JSON files.
- Keep existing permission routing: file operations and info-panel comment/rename entry points use file-write permission; tags and rating/Pick use sidecar permission. The Viewer override of `rating_writes_allowed()` is dynamically dispatched by the shared rating entry points. Do not bypass these gates as part of another fix.
- Per-file read-only checks must continue to block destructive operations even when a directory is writable. Copy, reveal, preview and filtering remain available through their existing read paths.
- The file list has `use_report_db = False` by default. Shared report compatibility APIs, including explicit write entry points, still exist. Do not force the entire shared database layer read-only or silently enable report-backed listing.
- A successful metadata edit must update path-bound local cache fields before notifying the UI. Local comment/rating/Pick values override older queued metadata batches; preserve valid empty values and zeroes. Changing directory or forcing reload clears that local overlay so external edits can be observed.
- Background refresh of the same photo must preserve unsaved comment and filename drafts. Use partial cached-field/tag refresh on the visible info page; mark hidden pages pending. A genuine photo switch may perform a full refresh.

## File operations and recovery

- Pair source images with existing XMP and resolved JSON sidecars using shared path helpers. Central JSON locations must be recomputed for the destination library; do not append sibling names unconditionally.
- The default trash route is the target's `.superpicky/deleted` behavior, with supported fallbacks. Do not replace it with another branch's deletion policy during a safety fix.
- Clipboard copy/cut batches need one transaction owner. Plan destinations before source removal; commit without overwriting a file that appeared concurrently. Shared same-stem XMP inputs may need copies at multiple distinct destinations.
- Roll back the whole clipboard batch on failure. Restore from verified copies, retain and report staging paths when restoration fails, and never delete the last complete original or sidecar copy. Cleanup follows confirmed restoration/success.
- Windows reveal keeps Explorer selection formatting compatible with spaces and mapped/UNC paths. Resolve actions against the real source image, not a derived preview path.

## Preview contract

- Preserve `PreviewPanel.set_image(path, *, load_full=True, quick_size=None)` and `set_quick_pixmap` callers.
- Ordinary committed selection may synchronously display a full image when the header reports at most `40 * 1024 * 1024` pixels and no earlier full-preview worker still owns the decoder. Keep the actual constant; do not reinterpret it as decimal 40,000,000 pixels or import another branch's setting.
- Larger or unknown-size images first use a cached/bounded quick preview and then an owned background decoder. Same-path fast-to-committed selection must still request the missing full preview.
- In the quick-preview path, a HEIF/HIF/HEIC cache miss must not synchronously invoke Pillow thumbnail generation, which can decode the full HEVC source. Leave loading to the owned background path; held-key `load_full=False` must not launch full decode.
- RAW normal display prefers an embedded preview, with background half-size demosaic fallback. Display-ready RAW is not proof of full source resolution.
- Overlay export must explicitly obtain full-resolution source pixels. It drains any active display decoder, rejects stale display callbacks, and fails rather than exporting the thumbnail when full decode is unavailable.
- Keep one full-preview decoder owner until its real `QThread.finished` cleanup. A logical result, `isRunning() == False`, timeout or interrupted request must not let an old cleanup callback erase a newer worker. Keep only the latest pending selection.
- Held direction-key navigation uses quick previews after the initial step and commits full loading on the final selection; do not add EXIF/full-image I/O to that hot path.
- `app_common.preview_canvas` owns composition-grid and overlay export behavior. Keep active grids visible on the preview and in exported overlays.

## Lists, tags and UI lifecycle

- Directory discovery, metadata loading and thumbnail preparation remain asynchronous and incrementally applied. Thumbnail mode prioritizes the visible range; list mode must not trigger thumbnail work.
- Keep current per-library persistent caches and their size tiers. Directory switches prioritize new work without discarding the target's previously queued thumbnail work.
- Tag config supports a hierarchy, with leaves as assignable tags. Exact tag filtering requires all selected tags; partial mode matches any selected tag substring. Text filtering includes filename, comment and tag values and preserves the recursive scope switch.
- Preserve sender/request checks and per-photo tag generations so older reads cannot overwrite local writes.
- Tag undo/redo records actual per-path membership changes, not a batch-wide boolean inversion. No-op commands preserve redo; partial failures retain successful inverses and retryable failed subsets; each history stack defaults to at most 100 commands.
- Keep editable UI state separate from saved metadata. Theme changes only update styles and must not reload images/EXIF or rebuild user edits.
- The default window mounts image-info and tag pages. EXIF table and dedicated focus-worker modules remain reusable code, not automatically enabled UI. Adding a page requires explicit startup and lifecycle integration.
- Shutdown first latches stop requests and disables rescheduling. Retain QThreads until actual completion and let the owning worker shut down its executors. The main window retries bounded shutdown while hiding the closing window, rather than destroying live workers.

## Verification

- From `<repo>`, compile changed Python files with the repository interpreter and run focused tests, followed by relevant complete test directories.
- Windows headless Qt: set `$env:QT_QPA_PLATFORM='offscreen'`; then `.\.venv\Scripts\python.exe -m pytest app_common/tests SuperViewer/tests -q`. macOS uses `.venv/bin/python3` with the same pytest arguments.
- GUI tests must isolate `paths_settings` configuration/last-folder paths, user options, permission globals and relevant caches before constructing a window. Prevent startup from restoring the real user library; never remove pre-existing state as test cleanup.
- Keep a strong process-lifetime `QApplication` reference. Wait with a bounded event loop for actual timer/worker/state completion, not a longer arbitrary sleep.
- Metadata changes require real temporary-file Chinese write/read-back checks; failure paths need malformed/unreadable sidecar, missing image and permission cases.
- File transaction changes need real temporary image + XMP/central JSON bundles, multi-target shared XMP, destination races and injected restore failures.
- Preview changes include the relevant policy, HEIF responsiveness, RAW display/export, selection/metadata refresh and shutdown tests listed in the architecture map. Offscreen tests do not replace Windows/macOS packaged startup checks.
- After testing, inspect both Git working trees and runtime configuration. Report platform limitations and untested packaging behavior without claiming a build was validated by compilation alone.
