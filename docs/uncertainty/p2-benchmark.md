# P2 — Software-engineering behavior benchmark

Standing regression signal for whether P0+P1 made Z better at **real work**, not just unit/orchestration tests.

| Piece | Location |
|-------|----------|
| Issue schema + loader | `aider/z/benchmark/issues.py` |
| Scripted / adapter agent | `aider/z/benchmark/agent.py` |
| Live adapter (z / hook / replay) | `aider/z/benchmark/live_adapter.py` |
| Harness | `aider/z/benchmark/harness.py` |
| Scoring / report | `aider/z/benchmark/scoring.py` |
| Issue set (v1, 30 issues) | `benchmarks/p2/issues/` |
| Fixture repos (pinned) | `benchmarks/p2/fixtures/*/PINNED_REF` |
| Raw results | `benchmarks/p2/results/run-*.jsonl` |

## Quick start

```bash
# List issues / balance
python -m aider.z.benchmark list --by-type

# Run full suite (full-layer + uncertainty-disabled baseline)
python -m aider.z.benchmark run

# Or via z CLI
z benchmark run
z benchmark score benchmarks/p2/results/run-<id>.jsonl
```

CI uses the **scripted agent adapter**: real P0/P1 mode, intent, clause, and shell-risk classifiers, plus authored solutions per issue.

### Live adapter (real model / hook / replay)

```bash
# Builtin Z coder against the fixture worktree (needs API keys)
Z_P2_LIVE=1 Z_P2_LIVE_MODEL=gpt-4o-mini \
  python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average --no-baseline

# External hook (edits worktree + writes AgentTrace JSON)
Z_P2_LIVE=1 Z_P2_LIVE_HOOK=scripts/p2_live_hook_example.py \
  python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average --no-baseline

# Offline replay through the live pipeline (no LLM — CI / dry-run)
Z_P2_LIVE=1 Z_P2_LIVE_BACKEND=replay \
  python -m aider.z.benchmark run --adapter live --ids p2-011-bugfix-average --no-baseline
```

| Env | Meaning |
|-----|---------|
| `Z_P2_LIVE=1` | Enable live adapter (otherwise timed-out stub) |
| `Z_P2_LIVE_BACKEND` | `z` (default), `hook`, or `replay` |
| `Z_P2_LIVE_MODEL` | Model for builtin `z` backend |
| `Z_P2_LIVE_HOOK` | Hook script path (implies hook backend if backend unset) |
| `Z_P2_LIVE_MAX_TURNS` | Max coder turns for builtin (default 3) |
| `Z_P2_LIVE_REPLAY` | Optional JSON with `file_edits` for replay |

Without `Z_P2_LIVE=1`, `--adapter live` returns a timed-out stub so accidental CI
selection cannot spend tokens. Scoring is unchanged.

## Design notes

- **Score the tree, not the claim.** `actually_complete` comes from hidden tests + root-cause match; `self_reported_complete` is captured only to compute false-completion rate.
- **Baseline comparison.** Every issue runs twice: uncertainty on vs `Z_UNCERTAINTY_DISABLED=1`. The disabled path intentionally reintroduces pre-P0 failure modes (always plan, over-edit, approval spam, trap-bait root causes) so deltas are measurable.
- **Hidden tests** live under each fixture’s `hidden_tests/` and are stripped from the worktree during the agent run, then restored for scoring.
- **Traps** cover P0.2/P0.3 negative controls, P1.1 noisy clause classification, P0.5 command-heavy migrations, and P1.2 in-run node resolution.
- **Clarifying questions:** unattended runs use a documented policy of *count and continue* (no scripted human answers). Recorded in `unnecessary_questions`.

## Regenerating fixtures/issues

```bash
python scripts/generate_p2_benchmark.py
```

Treat the issue set as a living, versioned asset (`version` field on each issue). Prefer adding new IDs over silently changing ground truth on old ones when historical scores must stay comparable.

## Metrics (P2.3)

Aggregate and per-task-type: resolution rate, hidden-test pass rate, false completion, unnecessary edits/planning, clause classification P/R, evidence-source rate, approval interruptions, time-to-first-edit / verified completion, time blocked on approval/sync — each for full vs baseline, plus a trade-off summary.
