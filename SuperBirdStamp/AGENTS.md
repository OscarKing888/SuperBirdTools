# AGENTS.md (Codex / OpenAI Coding Agents)

Follow `scripts_dev/AI_CODING_RULES.md` as the project baseline.

## Mandatory Project Constraints

- Keep files in UTF-8; avoid introducing mojibake.
- Do not use placeholder docstrings or log messages (e.g. `???????`, `TODO`, `TBD`); write real Chinese or English descriptions for docstrings and log format strings.
- For ExifTool non-ASCII metadata writes, prefer UTF-8 temp-file redirection (`-Tag<=file`) over inline command args.
- Preserve Windows/macOS compatibility for paths and subprocess behavior.
- `app_common` 下模块视为独立 sub module；修改其代码时优先保证通用性，并遵循开放封闭原则（尽量通过扩展而非直接改动既有通用逻辑）。
- Ensure persistent external processes (like `exiftool -stay_open`) have explicit shutdown and are closed on exit.
- For packaged-only CUDA issues, first suspect packaging/runtime differences.
- In Windows PyInstaller spec for Torch/CUDA, keep `upx=False` unless explicitly re-validated.
- use .\.venv as the virtual environment for the project, also with cli testing.

## Validation Minimum

- Run `py -3 -m py_compile` on changed Python files.
- For metadata changes: write + read-back verification with Chinese sample values.
- For `.spec` changes: packaged startup smoke test.

## New Feature: GUI Options
- Keep new GUI options feature reading from json config file @config/editor_options.json.
