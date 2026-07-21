# P0 Terminal UX — extensive implementation plan

**Status:** plan only — **do not implement until this plan is accepted**  
**Parent:** [terminal-ux-for-engineers.md](./terminal-ux-for-engineers.md)  
**Branch intent:** `cursor/z-terminal-ux-p0-…` (implementation PR after approval)  
**Depends on (recommended merge order):** #131 casual-chat ASK (optional but reduces bad plan panels), #132 design doc (this PR)

---

## 0. One-sentence goal

Make the default TTY feel like a **senior engineer’s tool**: orange means “decide or worry,” plans confirm in **one compact panel**, and pre-model subsystems **spin instead of spamming scrollback** — without removing plan gates, uncertainty, or verify-before-commit.

---

## 1. Scope lock

### In P0 (exactly three work packages)

| ID | Work package | User-visible outcome |
|----|--------------|----------------------|
| **P0.A** | Semantic color tiers | Status ≠ warning ≠ blocking panel |
| **P0.B** | Compact-first plan confirm | No full plan dump before Y/N/C; optional View |
| **P0.C** | Quiet turn preamble | ≤2 status lines per turn when not verbose; spinner carries detail |

### Explicitly out of P0

- Mode chrome (`PLAN›` / `ASK›`) — P1  
- Usage report opt-in / history mirroring filter — P1  
- `/uncertainties` Rich unify — P2  
- Changing TaskMode defaults beyond #131  
- Removing or auto-skipping verify / high-stakes planning  
- Web / landing UI  
- Changing pygments `z-terminal` code colors (unless a status hex forces it)

### Non-negotiable invariants

1. High-stakes / thin-checklist **still require** human confirm (unless `--yes-always`).  
2. `format_plan_for_context` / tranche‑4 compact **model** injects stay as-is.  
3. Full plan text remains available (View / Change / verbose).  
4. Escalation panel remains the **only** Rich bordered ask for plan confirm.  
5. Non-TTY / dumb terminals degrade to plain text without crashing.

---

## 2. Decisions (resolve design-doc open questions for P0)

| # | Question | Decision for P0 | Rationale |
|---|----------|-----------------|-----------|
| D1 | `(V)iew` letter? | **Yes** — extend confirm to `(Y)es/(N)o/(C)hange/(V)iew` | Engineers need one key to expand without starting a Change revision |
| D2 | Status color hex? | **`TOOL_OUTPUT = #D8D8D8`** (off-white). Ban-list greys in `test_z_ui` stay banned; `#D8D8D8` is not in that set and stays readable on black | Orange-for-everything was for “grey was unreadable”; we need a *third* channel, not a muddy mid-grey |
| D3 | `TEXT_DIM` / spinner label? | Keep spinner label **accent orange** (branded progress). Escalation *body* context stays **white/off-white** | Progress animation can be branded; scrollback status must not look like a warning |
| D4 | Full plan on Change? | After revise, re-confirm with **compact** again; print full plan only if user hits **View** or `Z_UX_VERBOSE=1` | Avoid re-introducing the wall on every revision |
| D5 | Thin checklist wall? | Stop printing `format_checklist_for_user` as a separate scrollback block before the panel; fold tracking items into confirm subject | Fixes the screenshot pattern: checklist wall + panel + prompt |
| D6 | Capability gap line? | Demote to **one** `tool_warning` only when gaps exist; drop the follow-up “not stopped” `tool_output` when preamble is active | Gaps are real signal; the reassurance line is noise once preamble exists |
| D7 | Verbose escape | `Z_UX_VERBOSE=1` or `--verbose` restores today’s multi-line trail (skills names, explore done, full plan before confirm) | Operators/debugging must not lose information |

---

## 3. Current call graph (what we will change)

```text
run_one
  ├─ _phase_spinner_start/update/stop          # KEEP (T0 progress)
  ├─ _maybe_pull_skills
  │    └─ tool_output(label) + tool_warning(gaps)   # → preamble / T2 only
  ├─ _start_explore_pass_async
  │    └─ tool_output("Exploring…")                 # → spinner / preamble
  ├─ _finish_explore_pass → _inject_explore_block
  │    └─ tool_output("Explore scout…")             # → preamble bit
  ├─ _maybe_begin_uncertainty_task
  │    ├─ tool_output(format_checklist_for_user)    # → REMOVE default dump
  │    └─ interactive_plan_confirm(...)             # → compact only
  └─ _maybe_require_implementation_plan
       ├─ tool_output(format_plan_for_user)         # → REMOVE default dump
       └─ interactive_plan_confirm(...)             # → Y/N/C/V

io.plan_confirm_ask
  └─ render_escalation(subject=format_plan_for_confirm)
```

Key files:

| File | Role in P0 |
|------|------------|
| `aider/z/theme.py` | P0.A palette |
| `aider/z/mascot.py` | Spinner dim color (keep accent) |
| `aider/z/escalation.py` | Ensure panel body uses TEXT not warning orange for prose |
| `aider/io.py` | `plan_confirm_ask` → Y/N/C/V |
| `aider/z/uncertainty/plan.py` | `interactive_plan_confirm` View path; no full dump on revise |
| `aider/z/uncertainty/checklist.py` | Optional `format_checklist_for_confirm` helper |
| `aider/coders/base_coder.py` | Stop full dumps; preamble collector; wire verbose |
| `aider/z/ux_preamble.py` | **New** — quiet turn status aggregator |
| `tests/basic/test_z_ui.py` | Color assertions |
| `tests/basic/test_z_plan_confirm_ux.py` | Extend for View / no pre-dump |
| `tests/basic/test_z_plan_checklist_ui.py` | Thin path no wall |
| `tests/basic/test_z_terminal_ux_p0.py` | **New** integration-ish unit tests |

---

## 4. Work package P0.A — Semantic colors

### 4.1 Palette changes (`theme.py`)

| Constant | Today | P0 |
|----------|-------|-----|
| `TEXT` | `#F5F5F5` | unchanged |
| `ACCENT` / warnings / panel border | `#C96A2B` | unchanged |
| `TOOL_OUTPUT` | `#C96A2B` | **`#D8D8D8`** |
| `TOOL_WARNING` | `#C96A2B` | **`#C96A2B`** (unchanged) |
| `TEXT_DIM` | `#C96A2B` | **Split:** keep `TEXT_DIM=#C96A2B` for mascot/spinner; add `STATUS_DIM=#D8D8D8` used by status tool_output if needed |
| `TEXT_MUTED` | `#C96A2B` | Use for escalation subtitle only → set to `#D8D8D8` or keep accent for “awaiting reply” italic |

**Recommended concrete mapping:**

```text
TOOL_OUTPUT   = #D8D8D8   # routine status
TOOL_WARNING  = #C96A2B   # gaps, soft stops, drift notes
TOOL_ERROR    = #F5F5F5   # keep (bold/reverse for emphasis)
TEXT_DIM      = #C96A2B   # mascot spinner label (branded progress)
TEXT_MUTED    = #D8D8D8   # panel subtitle / secondary
```

`apply_z_palette(args)` must assign `args.tool_output_color` / `tool_warning_color` from the new values.

### 4.2 Escalation panel (`escalation.py`)

- Title + border: accent  
- Question: `TEXT`  
- Context (plan body): `TEXT` or `TEXT_MUTED` (**not** accent)  
- Option bullets: accent caret + `TEXT` labels  

Today context uses `TEXT_DIM` which is accent — after P0.A that would paint the whole plan orange again. **Must** switch context style to `TEXT` / `TEXT_MUTED` when `TEXT_DIM` stays accent for spinner.

### 4.3 Uncertainty UI ANSI (`uncertainty/ui.py`)

`DIM` currently equals accent. For browse readability, set collapsed secondary fields to a status dim (`#D8D8D8` ANSI), keep risk tier on accent. Small, same PR as theme.

### 4.4 Tests P0.A

Update `tests/basic/test_z_ui.py`:

- `tool_output_color != tool_warning_color`  
- `TOOL_OUTPUT == #D8D8D8`  
- `TOOL_WARNING == ACCENT`  
- Relax/replace `test_no_grey_in_palette` so it still bans the **unreadable** greys (`#6B6B6B`, `#808080`, `#888888`, `#A0A0A0`) but **allows** `#D8D8D8` as intentional status channel  
- Assert `TEXT_DIM == ACCENT` (spinner) and escalation context does not use warning color for long prose (unit-test `render_escalation` styles if practical)

### 4.5 Risks P0.A

| Risk | Mitigation |
|------|------------|
| `#D8D8D8` still weak on some themes | Document `Z_STATUS_COLOR` env override (optional stretch); default chosen for black bg |
| Screenshot / brand “all orange” expectation | Design doc already moved away; banner/mascot stay orange |
| Tests assuming TOOL_OUTPUT==ACCENT | Update explicitly in same PR |

---

## 5. Work package P0.B — Compact-first plan confirm

### 5.1 Behavior today (bug for SE)

1. `_maybe_require_implementation_plan` prints **full** `format_plan_for_user`  
2. `interactive_plan_confirm` builds compact `format_plan_for_confirm` into escalation  
3. CLI prompt asks Y/N/C  
4. On Change + revise, prints **full** plan again  

Thin path also prints `format_checklist_for_user` (approach + steps + tracking) **before** the same confirm.

### 5.2 Target behavior

```text
[spinner stops]
┌─ Z needs your input ─────────────────────────┐
│ Proceed with this implementation plan?       │
│                                              │
│ <format_plan_for_confirm OR thin confirm>    │
│                                              │
│   ▸ Yes — proceed                            │
│   ▸ No — abort edits                         │
│   ▸ Change — revise the plan                 │
│   ▸ View — show full plan in scrollback      │
└────────────────────────── awaiting reply ────┘
Proceed with this … (Y)es/(N)o/(C)hange/(V)iew [Yes]:
```

- **No** `format_plan_for_user` before the panel (default).  
- **View** → print full plan once via `tool_output`, then re-show panel (same plan, same round; does not consume a Change round).  
- **Change** → revise → compact panel again (not full dump).  
- **Verbose** → may print full plan before first panel (legacy).

### 5.3 API changes

#### `io.plan_confirm_ask` (`aider/io.py`)

- Options string: `(Y)es/(N)o/(C)hange/(V)iew`  
- Escalation `options=` list adds `"View — show full plan"`  
- Parse leading `v` → return `"view"`  
- Free-text revision path unchanged (still → `"change"` + `_pending_plan_change`)  
- `--yes-always`: still `"yes"` (never auto-view)  
- Error hint: `Please answer Yes, No, Change, or View (Y/N/C/V).`  
- History line records `v` / `view`

#### `interactive_plan_confirm` (`plan.py`)

```text
loop:
  subject = format_plan_for_confirm(current)  # or thin variant
  choice = plan_confirm_ask(...)
  if choice == "view":
      io.tool_output(format_plan_for_user(current))
      continue   # same round budget? Decision D8 below
  if choice == "yes": return True, current
  if choice == "no": return False, current
  # change → revise; do NOT print full plan; loop
```

**D8 — View and max_rounds:** View does **not** increment the Change round counter. Implementation: handle `view` before counting change rounds, or use a separate `while True` with `changes_used`.

#### Call sites in `base_coder.py`

**`_maybe_require_implementation_plan` (~2560):**

```python
# DELETE default:
#   self.io.tool_output(format_plan_for_user(plan))
# KEEP:
approved, plan = interactive_plan_confirm(...)
```

**`_maybe_begin_uncertainty_task` thin path (~2112):**

```python
# DELETE default triple tool_output(format_checklist_for_user(...))
# INSTEAD: pass checklist into confirm subject builder
approved, plan = interactive_plan_confirm(
    ...,
    subject_plan=plan,
    tracking_checklist=checklist,  # new optional kw
)
```

#### New helper (checklist or plan module)

`format_thin_confirm(plan, checklist) -> str`:

- Title / approach / steps (from plan)  
- “Tracking:” numbered item texts (no giant header essay)  
- Cap steps ≤ 7, tracking ≤ 7  
- Must not include raw user dump / “Do: hello” when #131 landed  

Reuse `format_plan_for_confirm` when not thin.

### 5.4 Tests P0.B

| Test | Assert |
|------|--------|
| `plan_confirm_ask` returns `view` for `v` / `view` | io double |
| `interactive_plan_confirm` on view calls `format_plan_for_user` once and asks again | mock io |
| `interactive_plan_confirm` on change does **not** call `format_plan_for_user` | mock io |
| `_maybe_require_implementation_plan` does not `tool_output` full plan before confirm when not verbose | coder test with fake engine/plan |
| Thin begin_task: no `Tracking checklist (confirm…` wall as standalone `tool_output` before ask | capture io |
| Existing `test_z_plan_confirm_ux` still: confirm has Steps, not raw request | keep |
| Compact subject line count ≤ ~40 lines / soft budget | new assert on `format_plan_for_confirm` |

### 5.5 Risks P0.B

| Risk | Mitigation |
|------|------------|
| Engineers miss contracts/invariants in compact view | View key + verbose; compact already has approach/steps/OOS; contracts stay in full View |
| NI / non-TTY EOF still returns no | Unchanged |
| Double panel flash on View | Acceptable; clearer than scrolling to find dump |
| Tests hard-code Y/N/C only | Grep and update |

---

## 6. Work package P0.C — Quiet turn preamble

### 6.1 Problem

Even with spinners, completed steps print:

- `Applying skill(s): …` / `Capability plan: …`  
- `Capability gaps (N): …` + “not stopped”  
- `Exploring related files (background)…`  
- `Explore scout: …`  
- Blank lines around checklist/plan  

### 6.2 Target (non-verbose implement turn)

```text
[o.o] Planning — matching skills…
[o.o] Planning — exploring `bus` (1/3)…
[o.o] Planning — drafting approach checklist…
┌─ panel compact confirm ─┐  ...
Plan approved — proceeding with implementation.
Waiting for <model> …
<assistant reply>
```

Status scrollback when quiet:

```text
Planning · skills · explore · plan-gate
```

or after approve:

```text
Planning · 0 skills · explore 2 files · plan approved
```

**Hard budget:** ≤ **2** `tool_output` status lines from control-plane before first model token (excluding the plan panel / prompts which are T3).

### 6.3 New module `aider/z/ux_preamble.py`

```python
class TurnPreamble:
    """Collect quiet-turn facts; flush ≤2 lines."""
    verbose: bool
    skills: list[str]
    capability_gaps: int
    explore_files: int
    plan_gated: bool
    plan_approved: bool | None

    def note_skills(...)
    def note_gaps(n: int)
    def note_explore(n_files: int)
    def note_plan(...)
    def flush(io) -> None  # one or two tool_output lines; no-op if verbose
```

Helpers:

- `ux_verbose(io, coder) -> bool` from `coder.verbose` or `Z_UX_VERBOSE`  
- When verbose: preamble becomes no-op; callers keep today’s prints  

### 6.4 Call-site edits (`base_coder.py`)

| Site | Quiet behavior |
|------|----------------|
| `_maybe_pull_skills` success prints | Record names/gaps on preamble; **skip** `tool_output(label)` unless verbose; **keep** single `tool_warning` for gaps (T2) |
| “not stopped” reassurance | Skip unless verbose |
| `_start_explore_pass_async` “Exploring…” | Skip `tool_output` (spinner already says exploring) |
| `_inject_explore_block` | Record file count; skip “Explore scout…” unless verbose; **still** inject into `cur_messages` |
| After planning `finally` / before `while message` | `preamble.flush(io)` |
| “Plan approved — proceeding…” | Keep one line (high signal) OR fold into flush — **Decision D9: keep** as its own line (counts toward budget) |
| “Approach noted — proceeding…” | Same as plan approved |

Instantiate preamble once at start of the `try` in `run_one` planning block; store on `self._turn_preamble`.

### 6.5 Tests P0.C

| Test | Assert |
|------|--------|
| Quiet turn: mock skills+explore+no plan → ≤2 status `tool_output` calls from preamble path | unit |
| Verbose: skill label still printed | unit |
| Explore inject still adds `cur_messages` when quiet | unit |
| Gap warning still emitted when gaps>0 | unit |

### 6.6 Risks P0.C

| Risk | Mitigation |
|------|------------|
| Hiding skill names makes debugging harder | Verbose / `Z_SKILL_RETRIEVE_LOG` unchanged |
| Users think explore “did nothing” | Preamble includes `explore N files` or `explore —` |
| Race: explore finishes after flush | Flush **after** `_finish_explore_pass` (already ordered in `run_one`) |

---

## 7. Implementation order (mandatory)

```text
1. P0.A theme + escalation context color + test_z_ui
2. P0.B io View + interactive_plan_confirm + remove pre-dumps + thin confirm helper
3. P0.C ux_preamble + wire base_coder quiet paths
4. Full regression: test_z_ui, test_z_plan_*, test_z_plan_checklist_ui,
   test_z_phase_spinner, test_z_p0_control_flow, new test_z_terminal_ux_p0
```

Do **not** mix P1 mode chrome into this PR.

Suggested commit split (still one PR ok):

1. `theme: status vs warning color tiers`  
2. `plan: compact-first confirm with View`  
3. `ux: quiet turn preamble`

---

## 8. Env / flags

| Flag | Default | Effect |
|------|---------|--------|
| (none) | quiet P0 on | Compact confirm + preamble + new colors |
| `Z_UX_VERBOSE=1` | off | Legacy multi-line status + full plan before confirm |
| `--verbose` | off | Same as verbose for preamble/skills trail |
| `Z_STATUS_COLOR=#RRGGBB` | unset | Optional override for `TOOL_OUTPUT` (stretch; skip if time-boxed) |
| `Z_UX_FULL_PLAN_FIRST=1` | off | Escape hatch: restore pre-dump full plan (stretch) |

No new CLI flag required for P0 if env + `--verbose` suffice.

---

## 9. Acceptance criteria (merge bar)

1. **Color:** In a default themed session, routine status lines are visibly **not** the same color as capability-gap warnings; escalation panel border remains orange; plan body text is not orange.  
2. **Confirm:** Gated implement turn shows **one** escalation panel with compact body; full plan appears only after `V` or verbose.  
3. **Thin path:** No standalone “Tracking checklist (confirm or correct…)” wall above the panel.  
4. **Quiet:** Non-verbose gated turn produces ≤2 control-plane status `tool_output` lines before the panel (plus “Plan approved…” after).  
5. **Cores:** Rejecting plan still aborts edits; `--yes-always` still auto-approves; verify gate untouched.  
6. **Tests:** Listed suites green in CI/local.  
7. **Docs:** Update `terminal-ux-for-engineers.md` §12 P0 checkboxes to “implemented” in the **implementation** PR (not this plan PR).

---

## 10. Manual QA script (human)

1. `z` in a small git repo, z-theme on.  
2. Type `hello` → **no** plan panel (needs #131).  
3. Type a greenfield feature request → spinner → **compact** panel → press `V` → full plan scrolls → press `Y` → model runs.  
4. Same request, press `C`, type a revision → compact panel again (**not** full dump).  
5. Confirm status lines look off-white; type a force-gap task and confirm gap warning is orange.  
6. `Z_UX_VERBOSE=1` → confirm old-style chatter returns.  

---

## 11. Effort / risk summary

| Package | Invasiveness | Risk to cores | Notes |
|---------|--------------|---------------|-------|
| P0.A | Low (theme + a few style refs) | None | Touches tests that encoded “all orange” |
| P0.B | Medium (`io` + plan loop + 2 coder call sites) | Low if View/verbose preserve full text | Highest UX impact |
| P0.C | Medium (new helper + many print sites) | None | Easy to miss a `tool_output` |

Overall: presentation-only; no change to triage, verify, or commit gate logic.

---

## 12. What “done” looks like for the engineer

Before P0: orange wall → full plan novel → orange box → prompt → maybe model.  
After P0: eyes spinner → one compact decision box → optional View → Yes → model, with warnings still orange when they matter.

---

## 13. Approval checklist (before coding)

- [ ] D1–D9 decisions accepted (or note amendments below)  
- [ ] `#D8D8D8` status color accepted (or substitute hex)  
- [ ] View letter accepted vs “full plan only on Change”  
- [ ] Thin checklist folded into panel accepted  
- [ ] Implementation may start on a new branch off `main` (after #131/#132 merge preference)

### Amendment log

| Date | Change |
|------|--------|
| (none yet) | |

---

## 14. Relationship to open PRs

| PR | Topic | Relation to P0 |
|----|-------|----------------|
| #131 | Casual chat → ASK | **Merge before or with P0** — stops `hello` plan panels |
| #132 | Terminal UX design | Parent; this plan is the P0 elaboration |
| (future) | P0 implementation | Separate PR; cite this doc |
