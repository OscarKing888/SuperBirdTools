# Super Viewer AI Coding Rules

This document defines project-level rules for any coding assistant (Codex, Claude, Cursor, etc.).

## 1) File Encoding and Text Safety

- Default encoding for source/config/docs is `UTF-8`.
- Prefer `UTF-8 without BOM` for Python and markdown files.
- Do not mass-convert encodings or line endings unless explicitly requested.
- Never "fix" Chinese text by retyping or replacing large blocks without confirming semantic equivalence.
- When editing existing files, preserve:
  - existing line ending style
  - existing language content (Chinese/English mixed text)
  - existing comments unless directly related to the task

## 2) Windows + macOS Cross-Platform Rules

- Never hardcode platform-specific path separators; use `os.path` or `pathlib`.
- Any subprocess call must consider Windows and macOS behavior.
- On Windows GUI workflows, prefer hidden console for background tools when applicable.
- Avoid shell-dependent assumptions that break on PowerShell vs bash.

## 3) ExifTool and Non-ASCII Metadata

- For metadata values that may contain Chinese or special characters, avoid direct command-line assignment like:
  - `-XMP:Title=中文`
- Prefer UTF-8 temp-file redirection style:
  - `-XMP:Title<=tmp_utf8_file.txt`
  - `-XMP:Description<=tmp_utf8_file.txt`
- Always clean up temp files in `finally`.
- For persistent `exiftool -stay_open` usage:
  - provide explicit close/shutdown entrypoints
  - ensure close is invoked on normal completion and app shutdown
  - keep cleanup idempotent

## 4) CUDA / Torch Stability in Packaged Builds

- Treat packaged runtime as different from source runtime.
- For model loading:
  - load checkpoints on CPU first (`map_location='cpu'`)
  - then move model to target device
  - if CUDA init/inference fails, provide safe fallback path (usually CPU)
- Do not force CUDA FP16 optimizations unless verified stable in packaged builds.
- When a bug appears only in packaged builds, prioritize packaging/runtime differences over algorithm changes.

## 5) PyInstaller Rules (Windows)

- For Torch/CUDA-related apps, do not enable UPX compression by default.
- Keep packaging changes minimal and explicit in `.spec`.
- Prefer deterministic packaging over aggressive size optimization.
- Any packaging optimization must include a startup smoke test in packaged app.

## 6) Change Discipline

- Make minimal, task-scoped diffs.
- Do not touch unrelated files.
- If unexpected unrelated modifications are detected, pause and confirm direction.
- When implementing new features, always evaluate modularization / encapsulation first:
  - prefer reusable module-level functions or class-based (OOP) encapsulation for coherent responsibilities
  - avoid embedding core logic directly in GUI/event handlers or one-off scripts when it can be extracted
  - design APIs so they are testable and reusable from GUI, CLI, and batch workflows
- For core features (rendering, metadata processing, detection, export, etc.), provide or preserve CLI support whenever practical:
  - core logic should live in non-UI modules first, with GUI calling into the same API
  - if a feature is GUI-triggered, consider whether a CLI flag/subcommand should expose the same capability
  - document notable reasons when CLI support is intentionally deferred (e.g., high UI coupling, unclear UX contract)

## 7) Required Validation for Code Changes

When editing Python code, run at least:

- `py -3 -m py_compile <changed_python_files>`

When changing metadata write logic:

- verify with real sample files containing Chinese metadata
- verify both write and read-back

When changing packaging/spec:

- build and run packaged app smoke test
- verify model preload path and basic inference path

## 8) Logging and Error Handling

- Error logs must include concrete failing component (e.g., `YOLO`, `Keypoint`, `Flight`, `BirdID`).
- For preload/startup pipelines, avoid "all-or-nothing" failure where possible.
- Keep fallback behavior explicit and visible in logs.

## 9) Priority Order

If rules conflict, apply this order:

1. Data correctness (metadata correctness, no mojibake)
2. Runtime stability (no crash/leak)
3. Cross-platform compatibility
4. Performance optimization

## 10) High Regression Areas in This Project

These areas have repeatedly regressed during feature work. Treat them as protected flows.

- `report.db` root semantics:
  - A directory containing `.superpicky/report.db` defines a `root`.
  - Selecting any descendant directory must continue to reuse the same in-memory report cache for that root.
  - When the selected directory is outside the current root, search upward for a new root, but keep the search bounded.
- `report.db` file listing semantics:
  - In report mode, file list construction should come from cached `report.db` rows first, not from ad-hoc filesystem guesses.
  - Subdirectory selection must filter by relative path under the same root, and results must stay consistent whether the user selects root, parent, or leaf directories.
- `current_path` normalization:
  - Some rows store `current_path` as `.xmp` while the real source file extension lives in `original_path`.
  - Normalize display/source path construction to the source extension, but preserve the raw report `current_path` for XMP fallback access.
  - Never overwrite the raw sidecar path in memory unless the database is intentionally being corrected.
- Actual-path mismatch repair:
  - If `report.db` path is stale, UI should still resolve metadata and actions by actual file path when found.
  - Actual path lookup is a separate concern from report path normalization; do not collapse them into a single mutable path variable.
  - When auto-fixing `report.db.current_path`, write the path relative to `root`, never an absolute path.
- Preview path vs source path:
  - Preview rendering may use `temp_jpeg_path`.
  - Metadata, EXIF, focus extraction, copy/reveal actions, and sidecar logic must continue to resolve against source-file semantics.
  - Do not assume preview JPEG carries the same metadata as the source file.
- Focus extraction pipeline:
  - Focus extraction is format-dependent.
  - HIF/HEIF/HEIC and RAW cannot share a single metadata acquisition path blindly.
  - `report.db.focus_x/focus_y` is only a fallback, not the primary source when file metadata is available.
- List vs thumbnail mode:
  - List mode must not do thumbnail work.
  - Thumbnail mode must avoid full-list thumbnail churn and only load what the viewport needs.
- Filtering:
  - Filtering must happen at the data-source layer, not by repeatedly hiding/showing existing view items.
  - Tree/list/thumbnail views must be rebuilt from the same filtered dataset to avoid divergence and flicker.
- Batched metadata apply:
  - Metadata updates for large directories must be incremental and time-budgeted.
  - Never reintroduce a single synchronous loop that applies thousands of UI row updates on the GUI thread without yielding.
- Tree numbering column:
  - The `#` column is display-only.
  - It must always be natural-order numbering from `1`, refreshed after rebuild/filter/sort, and must not become a real sort key.

## 11) Recommended Design Patterns for This Codebase

- Prefer MVC / MVVM-style GUI structure:
  - Treat Qt widgets/views as the View layer.
  - Treat cached datasets, filtered file lists, report-row mappings, and explicit UI state as the Model layer.
  - Treat event handlers, worker orchestration, and view-state transitions as the Controller/ViewModel layer.
  - Do not let widgets become the implicit source of truth for business state.
  - If state must survive view rebuilds, keep it in explicit model/controller fields, not only inside widget items.
- Separate source-of-truth layers:
  - `report.db` cached rows
  - filesystem reality
  - derived UI paths
  - preview asset paths
  Keep them distinct. Bugs often came from mixing them.
- Use helper-based path resolution:
  - Centralize path resolution in reusable helpers.
  - GUI handlers should call helpers like "resolve source", "resolve preview", "resolve reveal target", not duplicate path heuristics inline.
- Keep a full-cache + scoped-view architecture:
  - Cache the full `report.db` for a root once.
  - Derive selected-directory subsets from the full cache.
  - This is more stable than reopening databases or rescanning from scratch on every click.
- Use append-only enhancement instead of destructive replacement:
  - Prefer "DB first, then fallback file/XMP enrichment" over replacing one source with another.
  - Preserve raw input fields when normalizing or enriching records.
- Follow the Open-Closed Principle:
  - Prefer extension by adding helpers, strategy branches, delegates, workers, or adapters instead of rewriting stable flows.
  - When supporting a new file format, metadata source, or UI badge/filter, extend the existing pipeline at the designated seam.
  - Avoid scattering format-specific or platform-specific `if/else` logic across multiple GUI handlers.
  - If a flow already has a stable abstraction boundary, add a new implementation branch there rather than bypassing it.
- Prefer view-state derived from data-state:
  - Filtered files, visible thumbnail range, and sorted tree order should each be explicit state.
  - Avoid implicit state hidden in widget visibility flags.
- Keep UI event handlers thin:
  - Event handlers should orchestrate.
  - Parsing, lookup, normalization, cache policy, and DB repair should live in helpers/workers.

## 12) Async and Performance Rules

- Use worker threads for I/O and metadata extraction:
  - directory scan
  - metadata batch loading
  - focus extraction
  - actual-path lookup
  - thumbnail decode
- Use request/version tokens for async UI work:
  - If results can arrive out of order, attach a request id and ignore stale results.
  - This is mandatory for preview/focus style flows tied to current selection.
- Batch GUI updates with a timer budget:
  - Large result sets should be applied in chunks.
  - Keep each GUI-thread chunk bounded by both item count and elapsed time.
- Thumbnails should be viewport-driven:
  - Load visible items first.
  - Recompute only when viewport range or thumb size changes.
  - Avoid triggering thumbnail work during list-mode operations.
- Cache carefully:
  - Caches need explicit ownership, size policy, and invalidation strategy.
  - If a cache can grow for the whole app lifetime, define an upper bound and an eviction rule.
  - Prefer evicting entries outside the current visible/active scope first.
- Thread-pool shutdown must be owned by the creating thread:
  - Do not shut down executors from another thread while submissions are still happening.
  - Signal stop first, then let the worker thread wind down and own final shutdown.

## 13) Metadata and Report-DB Rules

- If a file stem exists in cached `report.db`, list metadata should still be recoverable even when `current_path` is stale.
- `bird_species_cn` maps to UI title semantics in multiple places. Any change to species paste/writeback must update:
  - in-memory report row cache
  - in-memory metadata cache
  - visible file list rows
  - current preview-side metadata panel when affected
- Report-derived fields and file-derived fields may both be needed:
  - report rows are often incomplete for title/sharpness/focus display
  - file/XMP fallback should enrich missing fields instead of assuming report rows are sufficient
- When writing back to `report.db`, update both:
  - persistent storage
  - corresponding in-memory caches used by the current UI session

## 14) GUI Consistency Rules

- Keep GUI code aligned with MVC / MVVM-style boundaries:
  - View classes may format and present state, but should not own business rules for metadata, report resolution, path repair, or focus extraction.
  - Controller/ViewModel logic should be testable without requiring full widget interaction.
  - When adding a new GUI feature, first decide which part is view-only behavior and which part belongs in reusable model/controller helpers.
- If the same file is reachable from multiple selected directories under one root, visible metadata must remain consistent.
- Tooltip text, copy/reveal actions, preview loading, and row coloring must use the same resolved-path logic.
- Path mismatch coloring must only affect the file-name presentation, not unrelated columns.
- Any new tree/list column requires reviewing:
  - header labels
  - sort roles
  - width policy
  - tooltip column
  - foreground/background styling
  - debug logs that print row text

## 15) Required Validation for File-Browser / Preview Changes

When changing `main.py`, `app_common/file_browser/_browser.py`, `app_common/report_db.py`, or focus-related flows, validate at least:

- `py -3 -m py_compile <changed_python_files>`
- Root selection with `.superpicky/report.db`
- Descendant directory selection under the same root
- A case where `current_path` is correct
- A case where `current_path` is stale but filename stem exists
- A case where actual-path lookup repairs the row
- List mode behavior
- Thumbnail mode behavior
- Filtering by text / pick / rating / focus status
- Copy / reveal actions on both resolved and stale-path files
- Preview image loading from `temp_jpeg_path`
- Focus overlay loading from source file when preview JPEG lacks focus metadata

If the change touches a fragile pipeline, add temporary stat/debug logs or a small CLI reproducer rather than guessing.

## 16) Preferred Debugging Workflow

- Start from logs, not assumptions.
- Add narrowly scoped diagnostic logs with:
  - path
  - root
  - selected directory
  - cache hit/miss
  - fallback path chosen
  - item counts / elapsed time
- For non-trivial metadata/report/focus issues, prefer writing a small CLI that reuses the same core helpers.
- Remove or reduce noisy logs after the flow is confirmed, but keep the useful structured stat logs.
