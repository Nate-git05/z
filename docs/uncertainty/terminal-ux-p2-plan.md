# P2 Terminal UX — extensive implementation plan

**Status:** plan only — **do not implement until this plan is accepted**  
**Parent:** [terminal-ux-for-engineers.md](./terminal-ux-for-engineers.md)  
**Siblings:** [terminal-ux-p0-plan.md](./terminal-ux-p0-plan.md), [terminal-ux-p1-plan.md](./terminal-ux-p1-plan.md)  
**Branch intent:** `cursor/z-terminal-ux-p2-…` (implementation PR after approval)  
**Depends on:** P0 + P1 on `main` (already merged: #131–#133, P1 via `main`)

---

## 0. One-sentence goal

Finish the SE terminal surface: **`/uncertainties` looks like one product** (Rich hierarchy on the real tree, same Fix/Test/Explain/Ignore/Custom actions), post-edit noise collapses to **one triage line**, and a **golden implement-turn transcript fixture** locks the P0/P1 noise budget so it cannot silently regress.

---

## 1. Scope lock

### In P2 (exactly two work packages)

| ID | Work package | User-visible outcome |
|----|--------------|----------------------|
| **P2.A** | Unify `/uncertainties` presentation | One UI stack on the **production** `UncertaintyStore` / `UncertaintyNode` tree; Rich (when pretty) hierarchy with risk-first grouping; actions unchanged; compact post-edit summary line |
| **P2.B** | Golden “one implement turn” transcript fixtures | Automated assert that a quiet gated implement turn stays within the noise budget (preamble ≤2, no Tokens, no status-history spam patterns, compact uncertainty line) |

### Explicitly out of P2

- Changing detector logic, gate tiers, verify-before-commit, or `apply_action` semantics  
- Changing TaskMode heuristics (open design Q1: ambiguous noun phrases)  
- Web / landing UI  
- Rewriting the full uncertainty schema to match the prototype’s `UncertaintyNote`  
- New browse commands beyond polishing `/uncertainties`  
- Re-opening P0/P1 color / chrome / usage / history decisions (only **guard** them via fixtures)

### Non-negotiable invariants

1. Production store remains `aider.z.uncertainty.store.UncertaintyStore` + `UncertaintyNode`.  
2. Actions stay **F / T / E / I / C** with existing `apply_action` outcomes (Ignore still does **not** clear High for the commit gate).  
3. Risk-first default sort stays; file/session sort keys remain.  
4. Non-pretty / dumb terminals get plain-text listing (no crash).  
5. Commit gate / verify pipeline untouched except the **summary string** they already call via `print_summary_line`.  
6. Fixture tests must not require a live LLM.

---

## 2. Decisions (resolve design §10 / §12 for P2)

| # | Question | Decision for P2 | Rationale |
|---|----------|-----------------|-----------|
| **D1** | Port Rich into production UI **or** wire prototype to `/uncertainties`? | **Port Rich hierarchy into `aider/z/uncertainty/ui.py`** (production path). Do **not** point `/uncertainties` at `aider/z/uncertainty_ui.py`. | Prototype uses a **different** model (`UncertaintyNote` / fake store) and is only exercised by `test_z_ui`. Dual stores would drift forever. |
| **D2** | Fate of `aider/z/uncertainty_ui.py`? | **Deprecate**: keep file as thin wrappers that adapt *demo* notes for theme tests, **or** migrate `test_z_ui` tree/detail tests onto production render helpers and delete unused APIs in a follow-up commit in the same PR if tests stay green. Prefer **migrate tests → delete unused prototype store** if low risk; otherwise deprecate with docstring + “not used by `/uncertainties`”. | Design called out dual UIs as friction (§3.5). |
| **D3** | Pretty listing look? | Rich `Text` tree: bold header, tier sections ordered High → Medium → Low (by **gate-effective** tier when available), numbered rows, dim path/type, orange **only** on High (and maybe Medium marker). Detail view: Rich `Panel` for the selected node (border orange for High, muted/status for others) **or** keep plain `format_detail` inside a panel body. | Matches prototype’s hierarchy without inventing a third aesthetic. |
| **D4** | Interactive loop? | Keep `browse_interactive` control flow (list → prompt → detail → action → optional return prompt). Only swap **render** functions. | Lowest risk; actions already correct. |
| **D5** | Post-edit summary string? | Change `print_summary_line` to: `Uncertainty · {high} High · {med} Medium — /uncertainties` when any new nodes; omit zero tiers; if only Low: `Uncertainty · {n} Low — /uncertainties`. Drop “tree: N new node(s) (gate High=…)” prose. | Design §10 exact shape. |
| **D6** | When zero High and zero Medium? | Still print one T1 line if there are new Low nodes (so engineer knows the tree grew). If `new_nodes` empty: print nothing (today’s behavior). | Avoid silent growth of Low-only debt. |
| **D7** | Verbose escape for summary? | `Z_UX_VERBOSE=1`: may append `(N new)` or keep longer legacy line. Default stays compact. | Operators debugging detectors. |
| **D8** | Golden fixture style? | **Captured scrollback list** from a harness that drives real P0/P1 helpers + mocked confirm/LLM — **not** a full end-to-end CLI subprocess. Assert allowlist / denylist of line patterns. Optional checked-in `tests/fixtures/terminal_ux/one_implement_turn.txt` as the golden expected **status** lines. | Matches existing `test_z_p0_control_flow` / `test_z_terminal_ux_p0` style; no network. |
| **D9** | What the golden turn includes? | One **gated implement** happy path: quiet preamble flush → compact plan confirm (auto-Yes) → fake model reply → post-edit uncertainty summary. Assert: ≤2 preamble status lines; **no** `Tokens:`; **no** full plan wall; summary matches D5; optional: history file lacks blockquoted T1 status. | Locks P0+P1+P2.A summary together. |
| **D10** | Scope of fixture vs unit tests? | P2.A keeps focused unit tests for render/summary. P2.B is the integration noise-budget guard. Both in `tests/basic/test_z_terminal_ux_p2.py` (+ optional fixture file). | One suite name for the tranche. |

Open design Q1 (*ambiguous noun phrases*) remains **out of P2**.

---

## 3. Current call graph (what we will change)

```text
/uncertainties [args]
  └─ Commands.cmd_uncertainties
       ├─ stats → format_stats()                         # KEEP
       ├─ digit → format_detail + apply_action           # KEEP logic; prettier detail optional
       └─ browse_interactive(io, store, mode)            # production ui.py
            ├─ render_tree_listing(...)  # ANSI strings via tool_output  ← P2.A Rich
            ├─ prompt_ask (f/s/r / node #)
            ├─ format_detail + actions                   # KEEP apply_action
            └─ return optional follow-up prompt

Post-edit / gate
  └─ print_summary_line(io, new_nodes)                   # ← P2.A compact string
       called from base_coder._run_uncertainty_analysis
       and uncertainty/gate.py (duplicate path)

Unused / dual
  └─ aider/z/uncertainty_ui.py  (Rich + UncertaintyNote)
       └─ only test_z_ui.py                                  # ← P2.A deprecate/migrate
```

### Target shape

```text
uncertainty/ui.py
  ├─ render_tree_listing / render_tree_rich  → pretty uses Rich Console (io.console)
  ├─ format_detail / render_detail_rich
  ├─ browse_interactive                      → same prompts, new renders
  └─ print_summary_line                      → compact D5 string

uncertainty_ui.py
  └─ deleted or thin demo-only leftovers after test migration

tests/basic/test_z_terminal_ux_p2.py
  ├─ summary line + rich listing smoke
  └─ golden one-implement-turn noise budget
```

---

## 4. Work package P2.A — Unify `/uncertainties` presentation

### 4.1 Behavior today (bug for SE)

- `/uncertainties` prints a **flat ANSI** numbered list (`render_tree_listing`) — functional but visually weak next to Rich escalation/mascot.  
- A separate Rich tree lives in `uncertainty_ui.py` with **incompatible** types; engineers never see it in product.  
- Post-edit line is wordy:  
  `Uncertainty tree: 3 new node(s) (gate High=1 Medium=2). Use /uncertainties to review.`

### 4.2 Target behavior

**Listing (pretty):**

```text
Uncertainty · 3 open · sort=risk
‼ High
  1. [auth/session] Title…    type    risk=High
▸ Medium
  2. …
· Low
  3. …
Select # · [f]ile [s]ession [r]isk · Enter exits
```

(Exact markers can reuse prototype `‼` / `▸` / `·` or ASCII `!!` / `>` / `*` under `Z_PROMPT_ASCII` / non-unicode — prefer reusing theme colors from `aider.z.theme`.)

**Detail:** Panel or structured Rich block; actions prompt unchanged:  
`[F]ix / [T]est / [E]xplain / [I]gnore / [C]ustom / [B]ack`

**Post-edit:**

```text
Uncertainty · 1 High · 2 Medium — /uncertainties
```

Failing-tests `tool_warning` in `_run_uncertainty_analysis` **stays** (T2 real signal).

### 4.3 API / file changes

#### `aider/z/uncertainty/ui.py`

- Import Rich (`Console`, `Text`, `Panel`, `Style`) and `aider.z.theme` colors (STATUS / ACCENT / TEXT) so ANSI hex constants in this file don’t drift from P0.  
- Add `render_tree_rich(store, console, *, mode=...)` and/or make `render_tree_listing` return plain text while `browse_interactive` calls Rich when `io.pretty`.  
- Prefer printing via `io.console` when available (same pattern as `escalation.render_escalation`) so colors match the session.  
- Gate-effective tier for grouping: reuse `_effective_gate_tier` (already used in `print_summary_line`) so listing order matches commit-gate severity.  
- Update `print_summary_line` per D5–D7.  
- Keep `format_collapsed` / `format_detail` for non-pretty and for digit-arg path (or route digit-arg through Rich detail too when pretty).

#### `aider/commands.py`

- No behavior change required beyond benefiting from ui.py. Optional: if listing is Rich-printed inside `browse_interactive`, ensure `cmd_uncertainties` doesn’t double-print.

#### `aider/z/uncertainty_ui.py` + `tests/basic/test_z_ui.py`

- **Option A (preferred):** Move “Rich tree looks right” asserts onto production helpers with synthetic `UncertaintyNode`s; delete or gut prototype.  
- **Option B:** Leave prototype for visual sandbox; mark module docstring deprecated; stop expanding it.

#### Call sites of `print_summary_line`

- `base_coder._run_uncertainty_analysis`  
- `uncertainty/gate.py`  
Both automatically pick up the new string — **one function change**.

### 4.4 Tests P2.A

| Test | Assert |
|------|--------|
| `test_print_summary_line_compact` | Exact/regex: `Uncertainty · 1 High · 2 Medium — /uncertainties` |
| `test_print_summary_line_low_only` | Includes `Low` and `/uncertainties`; no “new node(s)” prose |
| `test_print_summary_line_empty` | No `tool_output` |
| `test_render_tree_pretty_smoke` | Rich path doesn’t raise; output contains High section before Low when both present |
| `test_browse_actions_unchanged` | Mock `prompt_ask` sequence → `apply_action` still invoked (existing edges tests may already cover actions) |
| `test_z_ui` | Updated if prototype removed |

### 4.5 Risks P2.A

| Risk | Mitigation |
|------|------------|
| Rich + Windows / dumb TERM | Fallback to existing plain `render_tree_listing` when `not io.pretty` |
| Grouping by gate tier vs stored `risk_tier` surprises | Document; use same `_effective_gate_tier` as summary |
| Prototype deletion breaks external importers | Grep shows only `test_z_ui`; safe |
| Panel detail too tall | Cap explanation lines or keep plain detail inside panel |

---

## 5. Work package P2.B — Golden implement-turn transcript fixtures

### 5.1 Problem

P0/P1 quietness is covered by **unit** tests (preamble, usage flag, history flag). Nothing asserts the **composed** scrollback of a typical gated implement turn stays within the SE noise budget. Regressions can land as “one more helpful `tool_output`” in `base_coder`.

### 5.2 Target

A deterministic harness produces an ordered list of **user-visible status/warning lines** for one implement turn and compares to a golden allowlist.

**Budget (default / non-verbose):**

| Channel | Budget |
|---------|--------|
| T1 preamble / planning status | ≤ **2** lines before confirm |
| Full plan dump before confirm | **0** (unless View / `Z_UX_FULL_PLAN_FIRST`) |
| `Tokens:` / `Cost:` | **0** |
| Post-edit uncertainty | **0 or 1** compact summary line (D5) |
| Capability gap | at most **1** `tool_warning` if gaps exist |
| Blockquoted T1 in chat history | **0** (P1 default) |

**Denylist substrings (examples):**

- `Tokens:`  
- `Cost:`  
- `Uncertainty tree:` (legacy summary)  
- `Tracking checklist` wall opener (if still present anywhere)  
- `Skills:` multi-name dump when quiet preamble active  

Exact denylist finalized in implementation; keep list in the test module.

### 5.3 Harness design

Prefer extending patterns from `test_z_terminal_ux_p0.py` / preamble helpers rather than the abstract `run_agent` in `test_z_p0_control_flow.py` (that harness doesn’t capture TTY lines).

Sketch:

```text
RecordingIO(InputOutput)
  - records tool_output / tool_warning / tool_error text
  - yes=True for plan confirm
  - pretty=False for stable strings (or pretty=True with plain capture)

Drive:
  1. Build minimal Coder mock or real Coder with fake model
  2. Set task_mode IMPLEMENT, force a thin/high-stakes path OR call:
       TurnPreamble.flush → interactive_plan_confirm (yes) →
       print_summary_line with synthetic nodes →
       show_usage_report with usage_report set (must stay silent)
  3. Optionally write chat_history_file and assert no "> Skills"

Assert recorded lines vs golden file OR inline expected list.
```

**Decision D11:** Start with a **synthetic pipeline** (call the real UX helpers in order) rather than full `Coder.run_one` with patched LLM — faster and less brittle. Add a second test later that patches `run_one` only if synthetic proves insufficient.

Optional golden file:

`tests/fixtures/terminal_ux/one_implement_turn_status.txt`

Update with care; PR description must call out golden edits.

### 5.4 Tests P2.B

| Test | Assert |
|------|--------|
| `test_golden_implement_turn_status_budget` | Line count / pattern budget above |
| `test_golden_no_usage_by_default` | `show_usage_report` silent |
| `test_golden_uncertainty_summary_shape` | Matches D5 when nodes present |
| `test_golden_history_omits_status` | temp history has no blockquoted preamble line |

### 5.5 Risks P2.B

| Risk | Mitigation |
|------|------------|
| Overfitting to exact wording | Prefer regex / substring budgets over full-string equality where copy may churn |
| Flaky ordering | RecordingIO list is append-only; harness is synchronous |
| False confidence (misses new call sites) | Document that fixture is necessary but not sufficient; still review new `tool_output` in `run_one` |

---

## 6. Implementation order (mandatory)

1. **P2.A.1** — Compact `print_summary_line` + unit tests (tiny, high value).  
2. **P2.A.2** — Rich listing/detail in `uncertainty/ui.py`; wire `browse_interactive`.  
3. **P2.A.3** — Migrate/deprecate `uncertainty_ui.py` + fix `test_z_ui`.  
4. **P2.B** — Recording harness + golden budget tests.  
5. Docs: design §12 P2 → implemented; short HISTORY bullet.  
6. Manual QA (§10).

Do **not** mix detector/gate refactors into this PR.

---

## 7. Env / flags

| Flag | Default | Effect on P2 |
|------|---------|----------------|
| `Z_UX_VERBOSE=1` / `--verbose` | off | May restore longer uncertainty summary (D7); preamble already verbose from P0 |
| `Z_SHOW_USAGE=1` / `--show-cost` | off | Unchanged (P1); golden test expects off |
| `Z_UX_HISTORY_FULL=1` | off | Unchanged (P1) |
| (none new required for P2.A Rich) | | pretty/dumb already on `io` |

No new CLI flag required for P2 if the above suffice.

---

## 8. Acceptance criteria (merge bar)

1. **`/uncertainties`** in a pretty session shows a **tier-grouped** tree (not only a flat ANSI dump).  
2. **Actions** F/T/E/I/C still work; Ignore does not clear High for gates.  
3. **Post-edit** line matches `Uncertainty · … — /uncertainties` (no “tree: N new node(s)” default).  
4. **One UI stack** for product browse: production store only; prototype not imported by `commands.py`.  
5. **Golden fixture** fails if Tokens print by default or preamble exceeds 2 status lines in the harness.  
6. **Cores:** gate/verify/detectors unchanged aside from summary copy.  
7. **Tests:** `test_z_terminal_ux_p2` (+ updated `test_z_ui`) green.  
8. **Docs:** design §12 P2 marked implemented in the **implementation** PR (not this plan PR).

---

## 9. Manual QA script (human)

1. `z` in a small repo with z-theme on.  
2. Make an edit turn that creates uncertainties (or seed via a debug path if needed) → confirm **one** compact summary line.  
3. `/uncertainties` → tier sections visible; open `#1` → detail; `I` ignore → back to list.  
4. `/uncertainties f` and `r` sort still work.  
5. `TERM=dumb` or `--no-pretty` → plain list, no traceback.  
6. `Z_SHOW_USAGE=1` still prints Tokens (P1 intact).  
7. Skim chat history: no wall of `> Planning…` status (P1 intact).

---

## 10. Effort / risk summary

| Package | Invasiveness | Risk to cores | Notes |
|---------|--------------|---------------|-------|
| P2.A summary string | Trivial | None | Copy-only |
| P2.A Rich browse | Medium (ui.py + tests) | Low | Control flow unchanged |
| P2.A prototype cleanup | Low–medium | None | Test migration |
| P2.B golden fixture | Medium (harness design) | None | Highest maintenance cost if too brittle |

Overall: presentation + regression locks; no triage policy changes.

---

## 11. What “done” looks like for the engineer

Before P2: flat `/uncertainties` dump; unused Rich twin; chatty “Uncertainty tree: N new node(s)…”; quietness only unit-tested in pieces.  
After P2: one Rich risk tree on the real nodes; one triage crumb after edits; CI fails if an implement turn gets noisy again.

---

## 12. Approval checklist (before coding)

- [ ] D1–D11 accepted (or note amendments below)  
- [ ] Agree: **port Rich into production ui.py**, do not wire prototype store to `/uncertainties`  
- [ ] Compact summary format accepted  
- [ ] Synthetic golden harness (D11) accepted vs full `run_one`  
- [ ] Prototype delete vs deprecate preference noted  

### Amendment log

| Date | Change |
|------|--------|
| (none yet) | |

---

## 13. Relationship to open / merged PRs

| PR | Topic | Relation to P2 |
|----|-------|----------------|
| #132 / design | Terminal UX contract | Parent; add P2 plan link under §12 |
| #133 P0 / P1 on main | Scanability + orientation | **Required base** (done) |
| #134 | P1 plan-only | Historical; supersede by implemented P1 |
| (this PR) | P2 extensive plan | Docs only |
| (future) | P2 implementation | Cite this doc |

### Design-doc follow-up

In [terminal-ux-for-engineers.md](./terminal-ux-for-engineers.md) §12 P2:

```text
**Extensive implementation plan (no code yet):** [terminal-ux-p2-plan.md](./terminal-ux-p2-plan.md)
```

---

## 14. File touch list (implementation PR checklist)

| File | Change |
|------|--------|
| `docs/uncertainty/terminal-ux-p2-plan.md` | This plan |
| `docs/uncertainty/terminal-ux-for-engineers.md` | §12 P2 link + status |
| `aider/z/uncertainty/ui.py` | Rich listing/detail; compact summary |
| `aider/z/uncertainty_ui.py` | Deprecate or remove after test migration |
| `tests/basic/test_z_ui.py` | Point at production renders if prototype removed |
| `tests/basic/test_z_terminal_ux_p2.py` | New |
| `tests/fixtures/terminal_ux/one_implement_turn_status.txt` | Optional golden |
| `HISTORY.md` | One bullet |

**Not touched:** `uncertainty/gate.py` logic (except existing `print_summary_line` call), detectors, verify, plan confirm, skill router.
