# Local mode classifier — extensive implementation plan

**Status:** plan only — **do not implement until this plan is accepted**  
**Parent open question:** [terminal-ux-for-engineers.md](./terminal-ux-for-engineers.md) §15  
**Related:** `aider/z/task_mode.py`, `aider/z/uncertainty/intent.py`, `tests/basic/test_z_p0_control_flow.py`  
**Branch intent:** `cursor/z-mode-classifier-…` (implementation PR after approval)

---

## 0. Verdict: is Option A a good idea?

**Yes — with a phased Option A, not a train-heavy model first.**

| Approach | Verdict for Z CLI |
|----------|-------------------|
| **A — local classifier** (rules → optional embeddings) | **Do this.** Offline, fast, no Ollama, matches binary/few-class decision |
| **B — fold into LLM TaskIntent call** | **Defer.** `extract_intent` is **not** an LLM call today (pure heuristics). Adding a model call just for mode adds latency/cost/failure for little gain |
| **Ollama / remote helper** | Optional later only; never required |

**Why A fits:**

1. Mode is a **few-class** decision (ask / investigate / implement / …) decided **before** the main coding model runs — must be free and local.  
2. The painful case (`users and sessions` → IMPLEMENT → plan panel) is mostly **shape**, not deep semantics — rules catch it.  
3. Embeddings (if needed) stay local and optional; no user install beyond what Z already has.  
4. Training a custom NLP model is **data/eval time**, not sklearn time — don’t block the fix on a training pipeline.

**Phasing inside A (mandatory):**

1. **A1 — heuristic “ambiguous noun phrase → ASK”** (hours; ships the open-question fix)  
2. **A2 — optional embedding / example-bank scorer** (only if A1 leaves real misses; still no fine-tune required)  
3. **A3 — labeled eval set + CI fixture** (locks behavior; cheap even if A2 skipped)

Do **not** start with fine-tuning.

---

## 1. One-sentence goal

Stop ambiguous noun-ish prompts from opening the **implement / plan** path by classifying them as **ASK** with a **local, dependency-light** classifier — preserving explicit `/code` implement intent and verb-led requests — without requiring Ollama or a new LLM round-trip.

---

## 2. Scope lock

### In scope

| ID | Work package | Outcome |
|----|--------------|---------|
| **A1** | Ambiguous-phrase heuristic | `users and sessions` → `TaskMode.ASK`; flip today’s control-flow test |
| **A2** | Optional local similarity scorer | Example-bank boost for gray cases when enabled; default off or silent no-op if deps missing |
| **A3** | Eval fixtures | Table of (prompt → expected mode) in tests; no live LLM |

### Out of scope

- Requiring Ollama / any network model for mode  
- Replacing clause extraction with an LLM  
- Changing plan-gate / verify / uncertainty **cores**  
- Fine-tuned transformer training pipeline  
- Changing sticky `/plan` / `/ask` / `/code` command behavior  

### Non-negotiable invariants

1. Explicit `edit_format in (ask, context)` and sticky `forced_task_mode` still win.  
2. Casual chat and pure questions stay ASK (already true).  
3. Clear implement verbs (`add`, `fix`, `implement`, …) stay IMPLEMENT.  
4. No new hard dependency for default path (stdlib / existing stack only).  
5. Classifier failure must **fail soft** → current heuristic chain (never crash the CLI).

---

## 3. Decisions

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| **D1** | Ambiguous noun phrases → ? | **ASK** | Avoid surprise plan panels; user can clarify with a verb or `/code` |
| **D2** | Ship fine-tuned model first? | **No** — A1 rules first | Fixes the named bug without train/eval debt |
| **D3** | Where does A1 live? | New helpers on `task_mode.py` (or tiny `aider/z/mode_classify.py` imported by it) | Single source for `classify_task_mode` |
| **D4** | Wire into `extract_intent`? | **Yes** — after A1, both `classify_task_mode` and `extract_intent` must agree (same helper) | Today intent can fabricate `requested_action` for long bare text; ambiguous short phrases must not |
| **D5** | A1 definition of “ambiguous noun phrase” | See §4.2 | Conservative; prefer false IMPLEMENT over false ASK on long product briefs |
| **D6** | A2 default | **Off** unless `Z_MODE_EMBED=1` **or** examples file present + deps available | Zero surprise latency/deps |
| **D7** | A2 algorithm | Bag-of-words / char n-gram cosine **or** optional `sentence-transformers` if already installed — prefer **stdlib TF‑IDF-ish / token overlap** first | No new required pip dep |
| **D8** | Escape to force IMPLEMENT | User types a verb, or `/code …`, or sticky code mode with a real request | Don’t invent a new command in A1 |
| **D9** | Update existing test | Rename `test_ambiguous_defaults_to_implement` → expects **ASK** | That test encodes the old policy |

---

## 4. Work package A1 — Heuristic ambiguous → ASK

### 4.1 Behavior today (bug)

```text
classify_task_mode(None, "users and sessions") → IMPLEMENT
# test_z_p0_control_flow.test_ambiguous_defaults_to_implement
```

No implement verb, not a question, not casual chat → falls through to default IMPLEMENT → can open plan UX.

### 4.2 Target heuristic (`looks_like_ambiguous_topic`)

Propose **all** of the following (tune in impl, lock in tests):

1. Non-empty after strip.  
2. **Not** already casual / ask-question / investigate / review / verify / implement-signal.  
3. Length / shape caps (starting point):  
   - ≤ **8** whitespace tokens  
   - ≤ **60** characters  
   - No `?`  
   - No path-like tokens (`/`, `\\`, `.py`, `` ` ``)  
   - Mostly letters/digits/spaces/hyphens/commas/`and`/`or`/`the`/`a`  
4. **No finite verb** from `_IMPLEMENT_RE` / investigate / review / verify sets.  
5. Looks like a **noun phrase / topic list**: optional leading articles; conjunctions `and`/`or`/`,`; no imperative mood.

Examples → **ASK:**

- `users and sessions`  
- `auth middleware`  
- `the checkout flow`  
- `redis cache`

Examples → still **IMPLEMENT:**

- `add users and sessions`  
- `fix the session store`  
- `implement users and sessions`  
- `users and sessions — add JWT refresh`  
- long paragraphs describing a feature (> caps)

### 4.3 Call-site edits

```text
classify_task_mode
  … casual → ASK
  … intent_mode if set
  … ask question → ASK
  … investigate / review / verify …
  ★ NEW: looks_like_ambiguous_topic → ASK
  … default IMPLEMENT

extract_intent
  ★ use same helper before fabricating requested_action
  ★ ambiguous topic → mode ask, no fabricated action clause
```

### 4.4 Tests A1

| Test | Expect |
|------|--------|
| `users and sessions` | ASK (`classify` + `extract_intent.mode`) |
| `auth middleware` | ASK |
| `Add users and sessions` | IMPLEMENT |
| `What are users and sessions?` | ASK (existing question path) |
| `hello` | ASK (unchanged) |
| Flip `test_ambiguous_defaults_to_implement` | → ASK |

File: extend `test_z_p0_control_flow.py` and/or new `tests/basic/test_z_mode_classify.py`.

### 4.5 Risks A1

| Risk | Mitigation |
|------|------------|
| Real shorthand feature requests become ASK | Caps stay tight; verb unlocks IMPLEMENT; document in HISTORY |
| Over-matching “redis” as topic when user meant “set up redis” | Require multi-token **or** known topic shape; single technical token alone may stay IMPLEMENT if too risky — **Decision D10:** single token ≤12 chars with no verb → **ASK** only if in a small stoplist of chatty tokens; else IMPLEMENT. Multi-token noun phrases → ASK. |
| Intent fabricates action anyway | D4 — shared helper before fabricate |

**D10 detail:** Prefer multi-token ambiguous phrases for ASK; single-word topics stay IMPLEMENT unless clearly casual (already handled).

---

## 5. Work package A2 — Optional local similarity (stretch)

### 5.1 When to build

Only if A1 ships and we still see false IMPLEMENT/ASK in real usage. Otherwise skip in the first implementation PR.

### 5.2 Design

```text
aider/z/mode_examples.json   # optional checked-in bank
  [{"text": "users and sessions", "mode": "ask"},
   {"text": "add login endpoint", "mode": "implement"}, ...]

aider/z/mode_embed.py        # optional scorer
  score(text) -> (mode, confidence) | None
```

- Default path: if `Z_MODE_EMBED` not set → return `None` (A1 only).  
- Algorithm v1: tokenize + Jaccard / cosine on binary bag-of-words against example bank (stdlib).  
- If confidence ≥ threshold (e.g. 0.55) and top label disagrees with A1 default, prefer scorer **only for ask vs implement** gray zone.  
- Never override explicit verbs or forced modes.

### 5.3 Tests A2

- Scorer prefers ASK for near-neighbors of ask examples.  
- Missing file / empty bank → no-op.  
- Verb-led prompt never flipped to ASK by scorer.

### 5.4 Risks A2

| Risk | Mitigation |
|------|------------|
| New dependency creep | Stdlib only for v1 |
| Latency | Microseconds for bag-of-words; keep bank small (≤200) |

---

## 6. Work package A3 — Eval fixtures

Even if A2 is skipped, add a **table-driven** fixture:

`tests/fixtures/mode_classify_cases.json` (or inline parametrize):

```json
[
  {"text": "users and sessions", "mode": "ask"},
  {"text": "add users and sessions", "mode": "implement"},
  ...
]
```

CI runs `classify_task_mode` + `extract_intent.mode` against the table.

---

## 7. Implementation order (mandatory)

1. A1 helper + `classify_task_mode` hook + `extract_intent` agreement  
2. Flip / replace control-flow test; add table cases  
3. HISTORY one-liner; resolve design §15 Q1 → ASK via this plan  
4. **Stop** — ship PR unless product asks for A2  
5. Optional follow-up PR: A2 embed scorer + more fixtures  

---

## 8. Env / flags

| Flag | Default | Effect |
|------|---------|--------|
| (none) | A1 on | Ambiguous multi-token noun phrases → ASK |
| `Z_MODE_EMBED=1` | off | Enable A2 scorer if implemented |
| `Z_MODE_CLASSIFY=0` | — | Escape: disable A1 ambiguous→ASK (restore old default) — **optional** |

---

## 9. Acceptance criteria

1. `classify_task_mode(None, "users and sessions") is ASK`  
2. `extract_intent("users and sessions").mode == "ask"` and no fabricated coding action  
3. `Add a new REST endpoint for users` still IMPLEMENT  
4. `/ask`, `/plan`, casual, pure questions unchanged  
5. No Ollama/network in default path  
6. Tests green; design §15 Q1 marked resolved  

---

## 10. Manual QA

1. `z` → type `users and sessions` → **no** plan panel; ASK-ish reply path.  
2. `add session expiry` → implement/plan path as today.  
3. `hello` → still ASK.  
4. `/code users and sessions` or `implement users and sessions` → implement path.

---

## 11. Effort / risk

| Package | Invasiveness | Risk | Notes |
|---------|--------------|------|-------|
| A1 | Low | Low–medium (false ASK) | Highest user-visible win |
| A2 | Low | Low | Optional |
| A3 | Low | None | Do with A1 |

---

## 12. Approval checklist (before coding)

- [ ] Agree Option A is the right strategy (not LLM fold / not Ollama-required)  
- [ ] D1: ambiguous noun phrases → **ASK**  
- [ ] D10: multi-token focus; single-word stays IMPLEMENT (unless casual)  
- [ ] A2 deferred to follow-up unless requested in same PR  
- [ ] Existing `test_ambiguous_defaults_to_implement` may be flipped  

### Amendment log

| Date | Change |
|------|--------|
| (none yet) | |

---

## 13. File touch list (implementation PR)

| File | Change |
|------|--------|
| `docs/uncertainty/mode-classifier-local-plan.md` | This plan |
| `docs/uncertainty/terminal-ux-for-engineers.md` | §15 resolve Q1 → link plan |
| `aider/z/task_mode.py` (and/or `mode_classify.py`) | A1 helper + hook |
| `aider/z/uncertainty/intent.py` | Shared helper; no false action fabricate |
| `tests/basic/test_z_p0_control_flow.py` | Flip ambiguous case |
| `tests/basic/test_z_mode_classify.py` | New table tests |
| `HISTORY.md` | One bullet |

**Not touched:** gate, verify, plan confirm UI, Ollama docs (except “not required”).
