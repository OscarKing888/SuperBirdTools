# CLAUDE.md (Claude / Anthropic Coding Agents)

Read [ai_rules/AI_CODING_RULES.md](ai_rules/AI_CODING_RULES.md) for the cross-tool baseline and [AGENTS.md](AGENTS.md) for the authoritative project behavior and validation contract. Use the Viewer/BirdStamp architecture links in AGENTS.md to locate code.

## Always Enforce

- UTF-8 safety first; do not introduce Chinese text corruption.
- ExifTool Chinese metadata writes must use UTF-8 temp files (`-XMP:Title<=tmp.txt`) instead of inline CLI values.
- Keep changes cross-platform (Windows + macOS).
- Any persistent external process must have deterministic cleanup on task/app exit.
- Treat `report.db` as read-only compatibility/hydration input; write user metadata to same-stem XMP sidecars.
- Packaged CUDA failures: prioritize packaging/runtime diagnosis before algorithm refactors.
- Keep Windows Torch/CUDA packaging with `upx=False` unless explicitly requested and validated.

## Minimum Verification

- Repo-root `.venv` interpreter with `-m py_compile` for changed Python modules.
- Metadata write/read-back check for non-ASCII fields.
- Packaged app smoke test when `.spec` or runtime packaging behavior changes.

