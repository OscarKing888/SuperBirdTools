# AI Rules Setup Guide (Codex / Claude / Cursor)

This file explains what was added and what (if anything) needs manual setup.

## Added Files

- `scripts_dev/AI_CODING_RULES.md` (canonical shared rules)
- `AGENTS.md` (Codex/OpenAI agents entry rules)
- `CLAUDE.md` (Claude entry rules)
- `.cursor/rules/cursor-rules.mdc` (Cursor project rules)
- `.cursorrules` (Cursor legacy compatibility)

## 1) Codex

Status: already applied by repository file.

- File used: `AGENTS.md`
- No extra setup needed in most Codex-enabled environments.

## 2) Claude

Status: repository-level rule file added.

- File used: `CLAUDE.md`
- If your Claude tool supports custom instruction path, point it to repo root so `CLAUDE.md` is discovered.

## 3) Cursor

Status: project rule added.

- File used: `.cursor/rules/cursor-rules.mdc`
- Ensure Cursor "Project Rules" is enabled for the workspace.
- If your Cursor version does not auto-read `.mdc` rules:
  - keep `.cursorrules` in repo root (already added)
  - open Cursor Settings
  - enable project rules / workspace instructions
  - reload the workspace

## Recommended Team Convention

- Keep all cross-tool coding policy updates in `scripts_dev/AI_CODING_RULES.md`.
- Keep tool-specific files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`) lightweight and aligned with canonical rules.
