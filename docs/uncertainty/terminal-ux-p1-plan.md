# P1 Terminal UX — extensive implementation plan

**Status:** plan only — **do not implement until this plan is accepted**  
**Parent:** [terminal-ux-for-engineers.md](./terminal-ux-for-engineers.md)  
**Sibling:** [terminal-ux-p0-plan.md](./terminal-ux-p0-plan.md) (scanability; implement first)  
**Branch intent:** `cursor/z-terminal-ux-p1-…` (implementation PR after approval)  
**Depends on (recommended merge order):** #133 P0 implementation on `main`, then #132 design doc if not already merged

---

## 0. One-sentence goal

Make **mode, cost, and chat-history noise** stop fighting the engineer: the prompt always shows whether Z will edit, token/cost lines stay opt-in, and `.chat.md` stops becoming a blockquoted copy of every T1 status line — without touching plan gates, uncertainty, or verify.

---

## 1. Scope lock

### In P1 (exactly three work packages)

| ID | Work package | User-visible outcome |
|----|--------------|----------------------|
| **P1.A** | Prompt mode chrome + mode help | Sticky plan → `PLAN› `; ask/context → `ASK› `; default implement → `› `; `/plan` listed in chat-mode help; one status line on mode switch |
| **P1.B** | Usage report opt-in | No `Tokens:` / `Cost:` scrollback by default; `Z_SHOW_USAGE=1` or `--show-cost` restores |
| **P1.C** | History mirroring filter | Chat history no longer blockquotes every `tool_output`; warnings/errors/confirms still mirrored |

### Explicitly out of P1

- Semantic colors / compact confirm / quiet preamble — **P0** (#133)  
- Unifying `/uncertainties` Rich UI — **P2**  
- Golden “one implement turn” transcript fixtures — **P2**  
- Changing TaskMode classification heuristics (beyond what P0/#131 already did)  
- Removing or auto-skipping verify / high-stakes planning  
- Web / landing UI  
- Changing how `usage_report` is **computed** or how analytics `event("message_send", …)` fires  
- Rewriting `SwitchCoder` / inventing a new sticky mode system beyond existing `forced_task_mode` + `edit_format`

### Non-negotiable invariants

1. Sticky `/plan` still blocks product edits until `/plan-exit` (behavior unchanged; chrome only).  
2. `/ask` and `edit_format in ("ask","context")` still cannot apply product edits.  
3. Token/cost **accounting** (`total_cost`, `message_tokens_*`, analytics events) still runs every round even when the line is hidden.  
4. User prompts, assistant replies, confirm answers, warnings, and errors still land in chat history.  
5. `Z_UX_VERBOSE=1` / `--verbose` remains the operator escape for noisy **TTY** trails (P0); P1 adds separate escapes for usage + history (see §8).  
6. Non-TTY / dumb terminals must not crash; chrome may fall back to ASCII `>` if needed (see D3).

---

## 2. Decisions (resolve design-doc open questions for P1)

| # | Question | Decision for P1 | Rationale |
|---|----------|-----------------|-----------|
| **D1** | Prompt chrome exact strings? | **`PLAN› ` / `ASK› ` / `› `** (U+203A `›`, trailing space) | Matches design §8 table; short; distinct from aider’s old `ask> ` |
| **D2** | Default implement: `› ` vs `Z› `? | **Bare `› `** | Brand already lives in spinner/theme; repeating `Z` every prompt is noise. Stretch: `Z_PROMPT_BRAND=1` → `Z› ` |
| **D3** | ASCII fallback for `›`? | Prefer `›`; if `Z_PROMPT_ASCII=1` or encoding can’t render, use `>` | Rare; keep one code path with env override rather than locale sniffing |
| **D4** | Priority when sticky PLAN + non-default `edit_format`? | **Sticky `forced_task_mode is PLAN` wins** → always `PLAN› ` | Plan interview is the binding UX state; edit_format under plan is secondary |
| **D5** | `help` / `architect` / other formats? | Map only **ask/context → `ASK›`**; **help → `help› `**; other non-default formats keep **`{format}› `** | Don’t invent new chrome for architect; preserve discoverability |
| **D6** | Multiline? | Append ` multi` before the chevron: e.g. `PLAN multi› `, ` multi› ` | Same composition as today’s `multi> ` |
| **D7** | Mode-switch status line? | **Yes — one `tool_output` line** after `/plan`, `/plan-exit`, sticky `/ask` (no args), `/code` (mode switch), `/context` | Orientation is useless if chrome changes silently; keep to **one** line (not the current multi-sentence `/plan` blurb) |
| **D8** | Usage default? | **Off** for both TTY and non-TTY | Design said “default off in TTY”; scripts also benefit from quieter logs. Opt-in only |
| **D9** | Usage enable flags? | **`Z_SHOW_USAGE=1`** and new CLI **`--show-cost`** (env `AIDER_SHOW_COST` / `Z_SHOW_COST` via args) | Matches design §11; CLI for humans who never set env |
| **D10** | Does hiding usage skip `event(...)`? | **No** — still call analytics / still bump totals | Presentation-only |
| **D11** | History: what stops mirroring? | Default: **`tool_output` does not** `append_chat_history` | T1 status is ephemeral; history should read as conversation + decisions |
| **D12** | History: what still mirrors? | **`tool_warning`**, **`tool_error`**, confirm/prompt transcripts (`confirm_ask` / `prompt_ask` / `user_input` paths that already append), assistant output | T2+ and T3 per design §11 |
| **D13** | History escape? | **`Z_UX_HISTORY_FULL=1`** restores mirroring all `tool_output` (incl. usage lines if shown) | Debugging / support dumps |
| **D14** | Does `log_only=True` still write history? | **No** under default filter (same as interactive `tool_output`) | Today `log_only` still appends then returns — that is part of the bug |
| **D15** | `/plan` in mode help? | Add **`plan`** entry to `cmd_chat_mode` `show_formats` | Design §8; `/plan` is a first-class mode people miss |

Open design Q1 (*ambiguous noun phrases → ASK vs IMPLEMENT*) stays **out of P1** — classification, not chrome.

---

## 3. Current call graph (what we will change)

```text
Coder.run loop
  └─ get_input()
       ├─ edit_format = "" if default else self.edit_format
       └─ io.get_input(..., edit_format=edit_format)
            └─ prompt_prefix = f"{edit_format}[ multi]> "   # NO TaskMode today

Commands
  ├─ cmd_plan()           → forced_task_mode=PLAN; long tool_output blurb
  ├─ cmd_plan_exit()      → clears forced; tool_output exit line
  ├─ cmd_ask() / cmd_code / cmd_context → SwitchCoder / one-shot
  └─ cmd_chat_mode()      → show_formats WITHOUT "plan"

After each LLM round
  └─ show_usage_report()
       └─ ALWAYS io.tool_output(self.usage_report)   # Tokens/Cost

io.tool_output / tool_warning / tool_error
  ├─ tool_output  → ALWAYS append_chat_history(..., blockquote=True)
  ├─ tool_warning → append via _tool_message
  └─ tool_error   → append via _tool_message
```

### Target shape

```text
Coder.get_input()
  └─ label = resolve_prompt_chrome(forced_task_mode, edit_format, multiline)
  └─ io.get_input(..., prompt_chrome=label)   # or computed edit_format string

show_usage_report()
  ├─ always: totals + event(...)
  └─ io.tool_output(report)  ONLY if show_usage_enabled()

tool_output(...)
  └─ append_chat_history  ONLY if history_mirror_status_enabled()
tool_warning / tool_error / confirms
  └─ unchanged mirroring
```

---

## 4. Work package P1.A — Prompt mode chrome + mode help

### 4.1 Behavior today (bug for SE)

- Prompt shows aider **edit_format** only when it differs from the model default (`ask> `, `architect> `, …).  
- Sticky `/plan` sets `Coder.forced_task_mode = TaskMode.PLAN` but **`get_input` never reads it** — engineer sees plain `> ` while edits are blocked.  
- `cmd_chat_mode` help lists help/ask/code/architect/context and **omits `/plan`**.  
- Mode transitions print uneven copy (`cmd_plan` is a long paragraph; others vary).

Comment drift: `forced_task_mode` docstring says “set only by explicit `/ask`\|`/context`” but `/plan` also sets it — fix the comment in the implementation PR.

### 4.2 Target behavior

| State | Prompt prefix (pretty) |
|-------|-------------------------|
| `forced_task_mode is TaskMode.PLAN` | `PLAN› ` |
| `edit_format in ("ask", "context")` | `ASK› ` |
| `edit_format == "help"` | `help› ` |
| other non-default `edit_format` | `{format}› ` |
| default implement | `› ` |
| + multiline | insert ` multi` before `›` |

After mode-changing commands (no prompt args / sticky switches), print **exactly one** status line, e.g.:

```text
Mode: PLAN — product edits blocked until /plan-exit
Mode: ASK — questions only; no product edits
Mode: CODE — product edits allowed
```

For plan interview, optional short stage suffix when interview is enabled:

```text
Mode: PLAN (clarify) — product edits blocked until /plan-exit
```

Replace the current multi-sentence `/plan` welcome with this one-liner (details stay in `/help` / docs). Keep `/plan-exit` “loaded plan…” as a **separate** factual line (artifact path is T1 signal worth keeping) **or** fold path into the Mode line — prefer **Mode line + one path line only when a plan file was loaded** (max 2 lines on exit).

### 4.3 API / file changes

#### New helper (preferred location)

`aider/z/ux_prompt.py` (or a small section in existing `aider/z/ux_preamble.py` if we want fewer modules — **prefer new `ux_prompt.py`** so P0 preamble stays single-purpose):

```text
prompt_chevron() -> "›" | ">"
resolve_prompt_chrome(*, forced_task_mode, edit_format, default_edit_format, multiline) -> str
format_mode_status_line(...) -> str
```

Pure functions; unit-test without `Coder`.

#### `Coder.get_input` (`base_coder.py`)

- Compute chrome via helper using `self.forced_task_mode`, `self.edit_format`, `self.main_model.edit_format`, `self.io.multiline_mode`.  
- Pass result into `io.get_input` as either:
  - **Option A (preferred):** new kwarg `prompt_chrome: str` — `io` uses it verbatim as `prompt_prefix` (still appends nothing else except ensuring trailing space rules live in helper), or  
  - **Option B:** overload `edit_format=` to already-resolved string like `"PLAN"` / `"ASK"` / `""`.

Prefer **Option A** so `io` stops guessing and tests can call `get_input` with an explicit chrome string.

#### `InputOutput.get_input` (`aider/io.py`)

- Accept `prompt_chrome=None`.  
- If provided, `prompt_prefix = prompt_chrome` (helper already included multiline + chevron).  
- Else legacy behavior for any external callers (keep backward compatible).

#### Commands (`aider/commands.py`)

- `cmd_chat_mode.show_formats`: insert  
  `("plan", "Plan interview (clarify → draft → approve); product edits blocked until /plan-exit.")`  
  near ask/code. Note: selecting `plan` via `/chat-mode plan` should either:
  - **D16:** route to same sticky behavior as `/plan` (set `forced_task_mode`, do **not** require a fake edit_format), or  
  - print “use `/plan`” and list it as documentation-only.

  **Decision D16:** `/chat-mode plan` and bare `/plan` share one helper `_enter_plan_mode()` so help is not a lie.

- Tighten status lines on `cmd_plan`, `cmd_plan_exit`, ask/code sticky switches per §4.2.

### 4.4 Tests P1.A

| Test | Assert |
|------|--------|
| `test_resolve_prompt_chrome_plan` | forced PLAN → starts with `PLAN›` |
| `test_resolve_prompt_chrome_ask` | edit_format ask/context → `ASK›` |
| `test_resolve_prompt_chrome_default` | default → `›` (or `>` under ASCII flag) |
| `test_resolve_prompt_chrome_multiline` | contains `multi` before chevron |
| `test_cmd_chat_mode_lists_plan` | help output includes `plan` |
| `test_enter_plan_mode_sets_forced` | `/plan` / chat-mode plan sets `forced_task_mode` |
| Optional IO smoke | `get_input` stores `self.prompt_prefix` matching chrome (mock prompt_session) |

File: `tests/basic/test_z_terminal_ux_p1.py` (new), plus a small command test if easier.

### 4.5 Risks P1.A

| Risk | Mitigation |
|------|------------|
| prompt_toolkit / Windows codepage can’t print `›` | `Z_PROMPT_ASCII=1`; document in §8 |
| Users who script-match on `> ` break | Note in HISTORY; chevron change is intentional; ASCII escape |
| `/chat-mode plan` vs `/plan` divergence | Shared `_enter_plan_mode` (D16) |
| Chrome shows PLAN but one-shot `/plan <args>` path differs | One-shot with args already forces TaskMode.PLAN for that run; sticky chrome only when `forced_task_mode` set — document |

---

## 5. Work package P1.B — Usage report opt-in

### 5.1 Behavior today

- `Coder.calculate_cost` / usage assembly always builds `self.usage_report`.  
- `show_usage_report()` always `tool_output`s it after LLM rounds (`base_coder.py`).  
- No `--show-cost` / `Z_SHOW_USAGE` exists (design assumed them).

### 5.2 Target behavior

- **Default:** do not print `usage_report` to the TTY.  
- **Still:** update `total_tokens_*`, `total_cost`, reset per-message counters, fire `event("message_send", …)`.  
- **Enable print when any of:**
  - `os.environ["Z_SHOW_USAGE"]` in truthy set (`1/true/yes/on`)  
  - CLI `--show-cost` / config equivalent  
  - Stretch: `coder.show_cost` attribute wired from args  

`/tokens` (if present) and any explicit user-facing cost commands remain available and unaffected.

### 5.3 API / file changes

#### Helper

Put `show_usage_enabled(coder=None) -> bool` next to other UX flags — either `aider/z/ux_prompt.py` or tiny `aider/z/ux_flags.py`. Prefer **one small flags module** shared with history filter:

`aider/z/ux_flags.py`:

- `env_truthy(name) -> bool`  
- `show_usage_enabled(*, coder=None) -> bool`  
- `history_mirror_status_enabled() -> bool`  
- (P0 already has `ux_verbose` in `ux_preamble.py` — **do not move P0 APIs in P1** unless trivial; call or duplicate env_truthy carefully to avoid import cycles)

#### `show_usage_report` (`base_coder.py`)

```text
# pseudocode
if not self.usage_report:
    return
# ... existing total bumps + event ...
if show_usage_enabled(coder=self):
    self.io.tool_output(self.usage_report)
# reset counters as today
```

#### Args (`aider/args.py` + wiring in `main.py`)

- Add `--show-cost` / `--no-show-cost` boolean (default False).  
- Plumb onto coder or io (`coder.show_cost = args.show_cost`).  
- Website `options.md` one-liner in implementation PR (short).

### 5.4 Tests P1.B

| Test | Assert |
|------|--------|
| default | `show_usage_report` does **not** call `tool_output` (mock io) but still increments totals / would call event (mock) |
| `Z_SHOW_USAGE=1` | prints report |
| `coder.show_cost=True` | prints report |
| report empty | no-op as today |

### 5.5 Risks P1.B

| Risk | Mitigation |
|------|------------|
| Power users miss cost feedback | Document `--show-cost`; mention once in release notes |
| Tests assert on printed Tokens lines | Grep/update those tests; prefer mocking |
| Double-opt with verbose | Verbose does **not** imply show-usage (separate concerns) |

---

## 6. Work package P1.C — History mirroring filter

### 6.1 Behavior today

Every `tool_output` line is appended to `chat_history_file` as a markdown blockquote (`> …`). Same for warnings/errors via `_tool_message`. Confirms append their Q/A. Result: history is a second scrollback of skills/explore/usage/preamble, drowning the real transcript.

### 6.2 Target behavior

| Channel | Mirror to chat history (default) |
|---------|----------------------------------|
| `tool_output` (T1 status) | **No** |
| `tool_warning` / `tool_error` (T2) | **Yes** |
| Confirm / prompt asks (T3) | **Yes** (existing append paths) |
| User input / assistant | **Yes** (unchanged) |
| `tool_output(..., log_only=True)` | **No** (default) |
| `Z_UX_HISTORY_FULL=1` | Mirror `tool_output` again (legacy) |

TTY display of `tool_output` is **unchanged** by this package (P0 already quieted volume). This package is **file history only**.

### 6.3 API / file changes

#### `InputOutput.tool_output` (`aider/io.py`)

```text
if messages and history_mirror_status_enabled():
    append_chat_history(...)
# print path unchanged
```

#### Optional kwargs (stretch, only if needed)

`tool_output(..., mirror_history: Optional[bool] = None)` for rare T1 lines we *do* want in history (e.g. “Plan approved — proceeding…”). Default `None` → use global flag. **Decision D17:** start **without** per-call override; add only if a concrete line is missing from history in QA.

Candidates that might deserve mirroring even as T1 (evaluate in QA, don’t pre-build a kitchen sink):

- Mode status lines (P1.A) — **yes, useful in history**; implement by calling `tool_warning`? No — keep as `tool_output` and either accept absence or use D17 override for mode lines only.  
  **Decision D18:** mode status lines use `tool_output(..., mirror_history=True)` once the optional kwarg exists; if we skip kwarg, print mode lines via a tiny `io.note_history(msg)` helper that appends + prints as status. Prefer **`io.session_note(msg)`** = print as tool_output color + always append. Use for Mode: lines and plan-exit loaded path.

### 6.4 Tests P1.C

| Test | Assert |
|------|--------|
| `tool_output` default | history file **lacks** the status string |
| `tool_warning` | history **contains** warning |
| `Z_UX_HISTORY_FULL=1` | `tool_output` appears in history |
| `session_note` / mode line | appears in history |
| `log_only` default | not in history |

Use temp `chat_history_file` like other io tests.

### 6.5 Risks P1.C

| Risk | Mitigation |
|------|------------|
| Support “send me your chat.md” loses status context | `Z_UX_HISTORY_FULL=1`; warnings still present |
| Something relied on blockquoted status for automation | Unlikely; note in HISTORY |
| Forgetting mode lines in history | D18 `session_note` |

---

## 7. Implementation order (mandatory)

1. **Land P0 (#133) on `main`** (or branch P1 impl from the P0 branch if blocked — avoid re-fighting color/preamble churn).  
2. **P1.A** chrome helper + `get_input` + command status/`show_formats` + tests.  
3. **P1.B** usage flag + `show_usage_report` gate + args + tests.  
4. **P1.C** history filter + `session_note` for mode lines + tests.  
5. Short doc touch: design §12 P1 → “implemented”; `options.md` `--show-cost`; one HISTORY bullet.  
6. Manual QA script (§10).

Do **not** combine with P2 uncertainty unify in the same PR.

---

## 8. Env / flags

| Flag | Default | Effect |
|------|---------|--------|
| (none) | P1 quiet-orientation on | Chrome on; usage hidden; status not mirrored |
| `Z_SHOW_USAGE=1` | off | Print Tokens/Cost lines |
| `--show-cost` | off | Same as above |
| `Z_UX_HISTORY_FULL=1` | off | Mirror all `tool_output` into chat history |
| `Z_PROMPT_ASCII=1` | off | Use `>` instead of `›` |
| `Z_PROMPT_BRAND=1` | off | Stretch: default chrome `Z› ` |
| `Z_UX_VERBOSE=1` / `--verbose` | off | P0 trail (unchanged); does **not** force usage or full history |

---

## 9. Acceptance criteria (merge bar)

1. **Chrome:** With sticky `/plan`, the input prefix is `PLAN› ` (or `PLAN> ` under ASCII).  
2. **Chrome:** In `/ask` mode, prefix is `ASK› `.  
3. **Chrome:** Default code mode prefix is `› ` (not `code› `).  
4. **Help:** `/chat-mode` with no args lists `plan`.  
5. **Usage:** After an LLM round with default flags, scrollback has **no** `Tokens:` line; with `Z_SHOW_USAGE=1` it appears.  
6. **History:** A turn that prints preamble/status does not blockquote those lines into chat history; a `tool_warning` does.  
7. **Cores:** Plan gate, verify, uncertainty tree untouched (no logic changes outside presentation/commands wiring).  
8. **Tests:** P1 suite green.  
9. **Docs:** Design §12 P1 marked implemented in the **implementation** PR; this plan PR is docs-only.

---

## 10. Manual QA script (human)

1. Start from main **with P0 merged**; `z` in a small repo.  
2. Confirm prompt is `› `.  
3. `/plan` → prompt becomes `PLAN› `; one Mode status line; try a product edit request → still blocked as today.  
4. `/plan-exit` (with a plan file) → `› ` again; Mode CODE line.  
5. `/ask` → `ASK› `; ask a question; no edits. `/code` → `› `.  
6. `/chat-mode` → see `plan` in the list; `/chat-mode plan` enters sticky plan.  
7. Run one implement turn: **no** Tokens line.  
8. `Z_SHOW_USAGE=1 z …` (or `--show-cost`) → Tokens line returns.  
9. Open chat history file: status chatter absent; warnings/confirm decisions present.  
10. `Z_UX_HISTORY_FULL=1` → status chatter present again.

---

## 11. Effort / risk summary

| Package | Invasiveness | Risk to cores | Notes |
|---------|--------------|---------------|-------|
| P1.A | Low–medium (`get_input` + commands) | Low | Highest orientation win; chevron encoding edge case |
| P1.B | Low (one gate + args) | None | Accounting preserved |
| P1.C | Low (`tool_output` history branch) | Low | Support dumps need FULL flag |

Overall: presentation + prompt labeling; no triage/verify/commit-gate logic.

---

## 12. What “done” looks like for the engineer

Before P1: plain `> ` while secretly in plan mode; Tokens after every round; chat.md is a grey wall of `> Skills…` / `> Exploring…`.  
After P1: `PLAN› ` / `ASK› ` / `› ` tell the truth; cost is silent unless asked; history reads like a conversation with warnings and decisions.

---

## 13. Approval checklist (before coding)

- [ ] D1–D18 decisions accepted (or note amendments below)  
- [ ] Bare `› ` accepted over `Z› ` for default  
- [ ] Usage default-off accepted (including non-TTY)  
- [ ] History: drop `tool_output` mirroring accepted  
- [ ] `/chat-mode plan` shares sticky enter with `/plan` (D16) accepted  
- [ ] Implementation starts **after** P0 (#133) is on the base branch (preferred)

### Amendment log

| Date | Change |
|------|--------|
| (none yet) | |

---

## 14. Relationship to open PRs

| PR | Topic | Relation to P1 |
|----|-------|----------------|
| #132 | Terminal UX design | Parent contract; add link under §12 P1 → this doc when merging |
| #133 | P0 implementation | **Merge before P1 implementation** |
| (this PR) | P1 extensive plan | Docs only — no code |
| (future) | P1 implementation | Separate branch/PR; cite this doc |

### Design-doc follow-up (same or tiny follow-on commit)

In [terminal-ux-for-engineers.md](./terminal-ux-for-engineers.md) §12:

```text
### P1 — Orientation

Extensive implementation plan (no code yet): [terminal-ux-p1-plan.md](./terminal-ux-p1-plan.md)
```

And §15 open question 2 → “Resolved in p1-plan §2 (D1–D3).”

---

## 15. File touch list (implementation PR checklist)

| File | Change |
|------|--------|
| `docs/uncertainty/terminal-ux-p1-plan.md` | This plan (already) |
| `docs/uncertainty/terminal-ux-for-engineers.md` | Link + resolve Q2 |
| `aider/z/ux_prompt.py` | Chrome resolvers (new) |
| `aider/z/ux_flags.py` | Usage + history flags (new; optional merge into ux_prompt) |
| `aider/coders/base_coder.py` | `get_input` chrome; `show_usage_report` gate; comment fix |
| `aider/io.py` | `prompt_chrome` kwarg; history filter; `session_note` |
| `aider/commands.py` | `show_formats` + shared enter-plan + short Mode lines |
| `aider/args.py` / `aider/main.py` | `--show-cost` |
| `tests/basic/test_z_terminal_ux_p1.py` | New |
| `aider/website/docs/config/options.md` | Flag blurb |
| `HISTORY.md` | One bullet |

**Not touched:** `uncertainty/gate.py`, verify, plan confirm compact logic (P0), skill router, explore.
