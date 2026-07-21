# Coding quality tranche 3 — plan

Stacked on tranche 2. Still Z-shaped (Aider edit loop + skills/uncertainty), not an
OpenCode rewrite.

## Goals

1. **Strict SEARCH apply** — SEARCH must match the named file; no silent cross-file
   patching onto other chat files. Prefer exact/whitespace-tolerant match on the
   target path only (OpenCode edit discipline).
2. **House instructions (`AGENTS.md`)** — load project (and ancestor) `AGENTS.md`
   like OpenCode’s instruction context; inject a compact block once per session
   into the coding turn.
3. **Live P2 adapter skeleton** — real adapter interface + opt-in subprocess runner
   for future live-model scoring (scripted adapter remains default for CI).

Non-goals: full tool-loop, explore subagent process, merging PRs.

---

## Design

### A. Strict SEARCH (`Z_STRICT_SEARCH=1` default)

In `EditBlockCoder.apply_edits`:
- When enabled, do **not** fall back to “try every other file in chat.”
- If the named path exists and SEARCH doesn’t match → fail that block (existing
  error path).
- New-file empty SEARCH still allowed.

### B. House instructions (`aider/z/house_instructions.py`)

- Walk from cwd/root up to repo root for `AGENTS.md` (and `~/.z/AGENTS.md`).
- Compact inject (budgeted) via `cur_messages` once per coder session
  (`_house_instructions_injected` flag).
- Escape: `Z_HOUSE_INSTRUCTIONS=0`.

### C. Live P2 adapter (`aider/z/benchmark/live_adapter.py`)

- `LiveAgentAdapter` implementing `AgentAdapter`.
- Default behavior without `Z_P2_LIVE=1`: raises clear “not enabled” / returns
  timed-out stub so CI never calls a model accidentally.
- With `Z_P2_LIVE=1`: documents/runs a best-effort path (record prompt, optional
  hook script `Z_P2_LIVE_HOOK` that receives worktree + prompt and writes a
  JSON trace). Keeps live scoring pluggable without baking credentials in.

CLI: `python -m aider.z.benchmark run --adapter live` (optional flag).

---

## Acceptance

- Cross-file SEARCH fallback disabled by default; test proves it.
- With an `AGENTS.md` in a fixture root, coding turn gets a house-instructions block.
- `LiveAgentAdapter` selectable; default suite still uses scripted adapter.
- Prior coding-quality + P0/P1/P2 tests green.
