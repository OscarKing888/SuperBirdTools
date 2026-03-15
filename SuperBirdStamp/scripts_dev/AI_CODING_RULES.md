# BirdStamp AI Coding Rules

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
