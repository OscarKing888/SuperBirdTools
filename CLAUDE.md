# CLAUDE.md (Claude / Anthropic Coding Agents)

Read [AGENTS.md](AGENTS.md) as the authoritative behavior and validation contract for this `img_mgr` checkout, then follow [ai_rules/AI_CODING_RULES.md](ai_rules/AI_CODING_RULES.md) as the cross-tool coding baseline. The [Viewer architecture map](SuperViewer/docs/ARCHITECTURE.md) locates implementations and extension points.

- Preserve UTF-8, Windows/macOS compatibility, and deterministic worker/process cleanup.
- Use this checkout's `<repo>/.venv`; on Windows run `.venv\Scripts\python.exe`, on macOS `.venv/bin/python3`.
- Keep `img_mgr` / `res_mgr` behavior: centralized JSON sidecars with compatibility reads, existing permission gates, default-disabled report listing, and the current preview policy.
- Inspect both Git working trees, preserve unrelated changes, and stage explicit feature paths only when authorized.
- Compile changed Python files; run relevant tests, real temporary-file Chinese metadata write/read-back checks, and packaged startup checks for packaging changes.
- Isolate GUI state before constructing windows. Do not delete or overwrite existing user configuration as test cleanup.
