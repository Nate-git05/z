# Terminal UX for software engineers

**Status:** design — P0/P1/P2 implemented (see tranche links)  
**Audience:** people shipping product code with Z in a TTY  
**Non-goals:** landing page, marketing, web dashboard

Related: [z-features-and-usage.md](../codex/z-features-and-usage.md) §3.9–4,  
[coding-quality-tranche4-plan.md](./coding-quality-tranche4-plan.md) (model inject budget — human scrollback still full).

---

## 1. Verdict

Z’s **mechanisms** are SE-grade (plan gates, uncertainty tree, verify-before-commit, Y/N/C revise).  
Z’s **presentation** is not yet SE-grade: too much orange sameness, plan confirm is a triple stack, mode is invisible, and routine status fights the assistant reply for attention.

This doc is the contract for fixing that **without** removing plan / uncertainty / verify cores.

---

## 2. What an SE actually needs in the TTY

| Need | Today | Target |
|------|--------|--------|
| See what mode they’re in | Implicit (`TaskMode` + sticky `/plan`) | Prompt chrome: `ASK›` / `PLAN›` / `›` |
| Decide only when blocked | Escalation panel good; everything else also orange | Orange = **blocking only** |
| Approve a plan fast | Full plan dump + panel + CLI prompt | Compact confirm first; full on demand |
| Keep focus on code | Pre-turn walls + token lines + skill chatter | One-line preamble unless verbose |
| Triage risk after edits | `/uncertainties` plain tree | Same actions, clearer hierarchy |
| Trust verify | Gate messages exist | Keep; don’t bury under status spam |

---

## 3. Current friction (from code)

### 3.1 Color collapse

`TOOL_OUTPUT`, `TOOL_WARNING`, `TEXT_DIM`, `TEXT_MUTED` all map to `#C96A2B` (`aider/z/theme.py`).  
Escalation panels use the same accent (`aider/z/escalation.py`).  
→ Status, warnings, and “needs input” compete; the box no longer means “stop.”

### 3.2 Triple-stack plan confirm

On gated implement turns (`base_coder._maybe_require_implementation_plan` / thin checklist):

1. Full `format_plan_for_user` (contracts, invariants, journeys, …) into scrollback  
2. Rich escalation with `format_plan_for_confirm`  
3. `(Y)es/(N)o/(C)hange` prompt  

Revision rounds reprint the full plan (`plan.interactive_plan_confirm`).

### 3.3 Pre-model control-plane wall

`run_one` runs skills → explore → checklist → plan before the first model token.  
Even with phase spinners, completed milestones still print multiple lines.

### 3.4 Mode discoverability

- `/ask` with args ≠ sticky ASK; bare `/ask` only flips edit format  
- `/plan` is sticky until `/plan-exit`, but `/code` mode help omits plan  
- No persistent mode indicator in the input line

### 3.5 Scrollback / history noise

- Token/cost after every LLM round (`show_usage_report`)  
- Most `tool_output` mirrored into chat history as blockquotes (`io.tool_output`)  
- Dual uncertainty UIs: production `uncertainty/ui.py` vs unused Rich `uncertainty_ui.py` (P2: product uses production Rich; prototype deprecated)

---

## 4. Design principles

1. **One job per visual channel** — status ≠ warning ≠ blocking ask.  
2. **Compact by default, expand on demand** — full plans exist; don’t force them every confirm.  
3. **Mode is always visible** — never make the engineer guess PLAN vs IMPLEMENT.  
4. **Quiet until blocked or edited** — progress may animate; don’t log every subsystem.  
5. **Cores stay** — plan gates, uncertainty, verify-before-commit remain; only the *surface* changes.

---

## 5. Signal tiers (proposed)

| Tier | Color / form | Allowed content | Default |
|------|----------------|-----------------|---------|
| **T0 Progress** | Mascot eyes spinner, no scrollback | Planning phase labels, “Waiting for model” | On |
| **T1 Status** | Neutral white/dim white `tool_output` | One-line turn preamble; explore/skill done | On, ≤2 lines/turn |
| **T2 Warning** | Orange `tool_warning` (no panel) | Capability gaps, drift soft notes, gate hold | On |
| **T3 Blocking** | Orange-bordered escalation panel | Plan Y/N/C, drift explicit-yes, commit ack, file-add groups | On |
| **T4 Debug** | Status + prefix | Retrieve traces, detector debug | Off unless `--verbose` / env |

**Theme change:** restore semantic tiers in `theme.py`:

- `TOOL_OUTPUT` → light neutral (e.g. `#C8C8C8` or `#F5F5F5` at lower emphasis)  
- `TOOL_WARNING` / escalation → accent orange  
- Keep assistant reply white  

Do **not** reintroduce muddy grey that fails on dark TTYs; use readable off-white, not `#666`.

---

## 6. Plan confirm UX (proposed)

### Default path

1. **Stop spinner**  
2. Show **one** escalation panel with `format_plan_for_confirm` (title, approach, ≤7 steps, out-of-scope)  
3. Prompt `(Y)es/(N)o/(C)hange` [add `(V)iew` optional]  
4. On **Yes** → inject compact plan into model context (already tranche‑4)  
5. On **View** or first Change → print full `format_plan_for_user` once  

### Thin greenfield path

- Same compact panel; tracking checklist as a short numbered list **inside** the panel, not a separate wall above it.

### Keep

- Free-text Change at the confirm prompt  
- `revise_plan_with_feedback` loop (cap 4)  
- `--yes-always` auto-approve matrix (document in confirm catalog)

---

## 7. Turn preamble (proposed)

Unless `--verbose` / `Z_UX_VERBOSE=1`:

```text
Planning · skills · explore · checklist · plan-gate
```

or after completion:

```text
Planning done · 1 skill · explore 3 files · plan approved
```

Replace separate lines:

- `Applying skill(s): …`  
- `Capability gaps (N): …` → demote to T2 one-liner or fold into preamble  
- `Exploring related files (background)…`  
- `Explore scout: …`  

Verbose restores today’s multi-line trail.

---

## 8. Mode chrome (proposed)

| State | Prompt prefix |
|-------|----------------|
| Sticky `/plan` | `PLAN› ` |
| `edit_format` ask/context | `ASK› ` |
| Default implement | `› ` (or `Z› `) |

After `/plan` / `/plan-exit` / `/ask` / `/code`, print one status line:

```text
Mode: PLAN (interview: clarify) — product edits blocked until /plan-exit
```

Add `/plan` to the `/code` mode help list.

---

## 9. Confirm catalog (proposed matrix)

| Confirm | UI | `--yes-always` |
|---------|-----|----------------|
| Implementation plan | T3 Y/N/C (+V) | Yes |
| Thin approach | T3 Y/N/C | Yes |
| Drift refocus | T3 explicit Yes | No (safe) |
| Medium uncertainty commit | T3 Y/N | No / ask |
| Add files group | T3 Y/N/All | Yes |
| Lint/test feed-back | T2 or T3 | existing |

Document this next to NI gate UX (`fault-plan-ni-verify-skills-gate.md`).

---

## 10. Uncertainty browse

- One stack: Rich hierarchy in production `uncertainty/ui.py` (prototype `uncertainty_ui.py` deprecated)  
- Keep risk-first sort; actions Fix / Test / Explain / Ignore / Custom  
- After edits: one T1 line `Uncertainty · 2 High · 1 Medium — /uncertainties` instead of long prose when possible

---

## 11. Usage / history

- Token/cost: default **off** in TTY; `Z_SHOW_USAGE=1` or `--show-cost` to enable  
- Chat history: only mirror T2+ (warnings) and T3 (confirms/gates), not every T1 status line

---

## 12. Implementation tranches

### P0 — Scanability (1–2 PRs)

1. Semantic colors (status vs warning vs panel)  
2. Compact-first plan confirm (+ optional View)  
3. Collapse pre-turn prints into preamble / spinner  

**Extensive plan:** [terminal-ux-p0-plan.md](./terminal-ux-p0-plan.md) · **Implemented:** #133

### P1 — Orientation

4. Prompt mode chrome + `/plan` in mode help  
5. Usage report opt-in  
6. History mirroring filter  

**Extensive plan:** [terminal-ux-p1-plan.md](./terminal-ux-p1-plan.md) · **Implemented:** this PR (on top of P0)

### P2 — Triage polish

7. Unify `/uncertainties` presentation  
8. Golden “one implement turn” transcript fixtures for noise budget  

**Extensive plan:** [terminal-ux-p2-plan.md](./terminal-ux-p2-plan.md) · **Implemented:** this PR

**Out of scope for these tranches:** removing plan gates, auto-skipping verify, web UI.

---

## 13. Acceptance tests

| Behavior | Suite / fixture |
|----------|-----------------|
| Casual `hello` → no plan panel | `test_z_p0_control_flow` (+ #131) |
| Plan confirm body ≤ N lines by default | new `test_z_terminal_ux_plan_compact` |
| Status color ≠ warning color | `test_z_ui` theme asserts |
| Preamble ≤ 2 status lines when not verbose | transcript fixture |
| Prompt shows `PLAN›` when sticky plan | command/io test |
| Escalation only on blocking asks | grep/guard test on `render_escalation` call sites |

---

## 14. What we keep as-is

- Y/N/C + Change revise loop  
- Phase mascot spinner during local planning  
- Verify-before-commit + uncertainty tree semantics  
- Compact **model** injects (tranche‑4)  
- Casual chat → ASK (#131)  

---

## 15. Open questions

Resolved for P0 in [terminal-ux-p0-plan.md](./terminal-ux-p0-plan.md) §2 (View letter, status hex `#D8D8D8`, thin checklist in panel).  
Resolved for P1 in [terminal-ux-p1-plan.md](./terminal-ux-p1-plan.md) §2 (`PLAN›`/`ASK›`/`›`, usage default-off, history filter).  

1. Ambiguous noun phrases (`users and sessions`) → **ASK** via local classifier (Option A).  
   **Extensive plan (no code yet):** [mode-classifier-local-plan.md](./mode-classifier-local-plan.md)  
   (Previously defaulted to IMPLEMENT; flipping avoids surprise plan panels.)
