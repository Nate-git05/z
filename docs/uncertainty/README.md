# Z Uncertainty Tree

**See what a careful human developer would worry about — before you ship.**

The Uncertainty Tree is Z’s risk-and-confidence layer. After the agent edits your code, Z checks concrete signals the way an experienced engineer mentally reviews a change: untested paths, unverified assumptions, high-stakes surfaces, requirement gaps, fragile logic, failure modes, and integration ripples. You review what actually needs attention — not every line the same way.

---

## The problem it solves

AI coding agents can edit a whole repo in one pass. A wrong change often looks identical to a correct one until something breaks in production.

Traditional “confidence scores” don’t help — they’re usually the model guessing how sure it feels. Z doesn’t do that. It derives **risk** (how bad if wrong) and **confidence** (how much real evidence we have) from **checkable signals**, then puts those notes in a tree you can browse, fix, or ignore.

| Human worry | Without the tree | With Z |
|-------------|------------------|--------|
| “I haven’t tested this path” | Green chat, red CI later | **Untested Path** + verify gate |
| “I’m assuming this API behaves…” | Silent invented shapes | **Unverified Assumption** |
| “This is money/auth — be paranoid” | Looks like any other diff | **High-Stakes Surface** |
| “We didn’t finish what was asked” | Half-built features ship | **Requirement Gap** (evidence-bound) |
| “What if this fails?” | Happy-path only | **Failure Blind Spot** |
| “Side effects elsewhere?” | Shared util breaks callers | **Integration Ripple** |

---

## Control flow

```text
task
  → structured checklist (confirm when ambiguous)
  → edit (dirty-commits held while verify reflect is pending)
  → settle edits
  → verify → structured VerifyState (NO_TESTS / PASSED / FAILED / …)
  → detect (human-worry detectors)  # context-aware, capped
  → checklist semantic re-score     # evidence + optional model JSON
  → auto-act OFF by default (Z_UNCERTAINTY_AUTO_ACT=1 to enable)
  → gate (High block / Medium ack / Low ok)
  → commit only with VerificationRecord
  → /uncertainties for leftovers
```

Tiers are **Low / Medium / High**. There are **no fake confidence percentages**.

**VerifyState** distinguishes `NO_TESTS` from `TESTS_FAILED` — a suite that reports
`2 failed, 7 passed` always takes the fix path, never “generate tests.”

Dependency-free Python projects under `tests/` use
`python -m unittest discover -s tests -v` unless pytest is declared.

Checklist items are typed (`product` / `process` / `verification` / `decision`).
Process phrases like “use uncertainty” are scored from the **session/execution log**,
not by searching product source for the word “uncertainty”. Gate messages show the
**effective** risk tier (so “high-risk” never labels a Medium-stored node).

---

## Risk vs confidence

| Axis | Means | Raised by (examples) |
|------|--------|----------------------|
| **Risk** | How bad if this is wrong | High-stakes paths; failing/missing verify; core requirement gaps; blast radius |
| **Confidence** | How much real evidence we have | Meaningful tests passed; live-verified APIs; matched tested patterns |

A change can be **high risk + low confidence** (stop and look) or **low risk + high confidence** (lighter review).

---

## Human-like node types

| Node type | Typical signal |
|-----------|----------------|
| Untested Path | No relevant tests, zero discovered, or tests failed |
| Edge Case Blind Spot | **Structural** branches (else/except/None/empty…) undiscussed & untested; model list is supplement only |
| Unverified Assumption | External API/MCP without live verification this session |
| High-Stakes Surface | Payment / auth / security / data-loss paths |
| Migration Risk | Schema migrations without clear data-impact handling |
| Fragile Logic | Nested/brittle patterns, broad excepts, “hack” markers |
| Pattern Misfit | Conflicting conventions (**mature repos only**) |
| Integration Ripple | Widely referenced symbols changed |
| Failure Blind Spot | I/O without failure handling |
| Unverifiable Config | Env/secrets referenced but not present |
| TODO / Unclear Comment | TODO/FIXME near the change |
| Requirement Gap | Checklist item Partial/Not Addressed (evidence-bound) |
| Evidence of Safety | Matched tested pattern **and** tests passed (never blocks) |

**Noise rules:** greenfield/young repos suppress “new file has no pattern” alarms and soften blast-radius noise. Scaffold files (README, `__init__.py`, …) are skipped.

---

## Using it

```text
/uncertainties              Browse the tree (risk-first)
/uncertainties risk         Sort by risk
/uncertainties file         Group by file
/uncertainties session      Group by task / session
/uncertainties stats        Per-detector disposition rates (noise signal)
/uncertainties 3            Open note #3

z uncertainty stats         Same disposition table from the CLI
```

| Action | Effect |
|--------|--------|
| **[F]ix** | Type-aware prompt (tests, gaps, assumptions, …) |
| **[T]est** | Queue focused tests for the worry |
| **[E]xplain** | Needs human review + explanation |
| **[I]gnore** | Dismiss for Medium/Low — **does not clear High for the commit gate** |
| **[C]ustom** | Your own follow-up |

---

## Verify-before-commit gate

When the uncertainty engine is active:

- **High** → hard block (must be Resolved, or `--force-commit` / logged override)
- **Medium** → explicit acknowledgment required (`--yes` cannot bypass)
- **Low** / Evidence of Safety → never blocks

Zero discovered tests count as **failure**, not success. Escape: `--no-verify-commit-gate` or `Z_SKIP_VERIFY_GATE=1`.

---

## Statuses

| Status | Meaning |
|--------|---------|
| Open | New / still active |
| In Progress | Auto-act or user fix/test/custom |
| Needs Human Review | Escalated — don’t ignore quietly |
| Resolved | Handled |
| Ignored | Deliberately skipped (not for High gate clear) |
| Blocked | Waiting on something external |

Default listing: **highest risk first**, then **lowest confidence**.

---

## Where it lives

| Layer | Location |
|-------|----------|
| Local store | `~/.z/uncertainty/<repo>.json` (or `$Z_HOME/uncertainty/`) |
| In session | Built as you edit; browse with `/uncertainties` |
| Optional sync | When signed in → `/v1/uncertainty/*` |

Code: `aider/z/uncertainty/` (`engine.py`, `detectors.py`, `checklist.py`, `context.py`, `gate.py`, `verify.py`, `auto_act.py`, …).

---

## Disposition telemetry (thin calibration loop)

Every create / ignore / resolve / force-commit / medium-ack increments counters in
`$Z_HOME/uncertainty/outcomes.json`, keyed by detector (node type).

`z uncertainty stats` (or `/uncertainties stats`) prints override rates. A detector
that is ignored or force-committed far more than it is resolved is a boy-who-cried-wolf
candidate — use that to raise its bar later. This release only **records + reports**;
it does not auto-tune thresholds yet.

## Reliability 9/10 (false-completion focus)

See [reliability-9.md](./reliability-9.md) for the Codex-gap roadmap.

Control-flow and evidence phases:

| Doc | Scope |
|-----|--------|
| [p0-control-flow.md](./p0-control-flow.md) | Modes, intent, async sync, shell risk |
| [p1-evidence-lifecycle.md](./p1-evidence-lifecycle.md) | Clauses, resolution contracts, exceptions |
| [p2-benchmark.md](./p2-benchmark.md) | Behavior benchmark harness + scoring |
| [coding-quality.md](./coding-quality.md) | Compact skills, output budget, plan/explore/done, strict SEARCH, AGENTS.md, live P2 adapter |

P0 subsystems:

| Module | Role |
|--------|------|
| `integrity.py` | Block weakening typecheck/tests/CI after failures |
| `failure_classify.py` | Env vs type vs assertion layers + causal backtrack |
| `capabilities.py` | Capability plan above named skills |
| `architecture.py` | Pre-coding architecture checkpoint |
| `journeys.py` | Critical user journeys with typed evidence |
| `completion.py` | Partial vs complete — never claim ready without evidence |

## Design principles

- **Human worries over metrics** — nodes should sound like a senior engineer’s checklist  
- **Signals over vibes** — tiers from tests, paths, evidence — not “I’m 87% sure”  
- **Risk ≠ confidence** — keep them separate  
- **Evidence-bound requirements** — not bag-of-words alone  
- **Structural edges over self-report** — empty model edge lists cannot silence the tree  
- **Gate drives behavior** — High worries block or auto-act; don’t only report  
- **Quality over quantity** — cap nodes; suppress greenfield scaffold noise  
- **Measure dispositions** — track which detectors get overridden  
- **Never weaken verification to go green** — repair product/environment, not the detector  
- **False completion is worse than honest partial** — unit green ≠ journey proven  

That’s the Uncertainty Tree: the agent still ships code — Z makes the guesswork inspectable the way a careful human would.
