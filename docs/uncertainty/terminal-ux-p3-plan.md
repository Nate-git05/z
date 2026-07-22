# P3 Terminal UX — Turn flow (busy · queue · interrupt)

**Status:** implemented — Idle / Busy / WaitingInput orchestrator + message queue  
**Parent:** [terminal-ux-for-engineers.md](./terminal-ux-for-engineers.md)  
**Siblings:** [terminal-ux-p0-plan.md](./terminal-ux-p0-plan.md), [terminal-ux-p1-plan.md](./terminal-ux-p1-plan.md), [terminal-ux-p2-plan.md](./terminal-ux-p2-plan.md)  
**Branch intent:** `cursor/z-turn-flow-p3-…` (implementation PR after approval)  
**Depends on:** P0–P2 on `main`; verify/busy honesty fixes from #141 (ctest parse + spinner stop-before-prompt) either merged or landed with P3.A

---

## 0. One-sentence goal

Make the TTY feel like **one product loop**: when Z is working you always know it, you can always type the next thought, blocking asks pause cleanly, and queued messages drain in a predictable order — without fighting spinners, slash menus, or escalation panels.

---

## 1. Why this tranche (dogfood)

P0–P2 fixed *what* prints (colors, plan confirm, mode chrome, uncertainties).  
#141 fixed *honesty* of busy vs green ctest.

What still fails the “Claude Code / Codex” bar:

| Friction | What the engineer feels |
|----------|-------------------------|
| No input during work | “Is it stuck? Can I type? Do I Ctrl+C?” |
| Ghost prompt chrome | Spinner + leftover `/` menu + `>` at once |
| Blocking asks mid-turn | Plan Y/N/C appears while “Planning…” still feels live |
| Next thought lost | Have to wait for idle, then retype |
| Piecewise fixes | Each fix helps; the **flow** still doesn’t feel designed |

P3 is **not** “add a queue.” P3 is **one turn orchestrator** that owns Idle / Busy / WaitingInput / Queued so every surface (spinner, files, escalations, verify, uncertainties) plugs into the same state machine.

---

## 2. Scope lock

### In P3 (three work packages, one flow)

| ID | Work package | User-visible outcome |
|----|--------------|----------------------|
| **P3.A** | Turn orchestrator + busy chrome | Single owner of “who has the bottom of the screen”; unmistakable Busy; never spinner+prompt together |
| **P3.B** | Message queue while Busy | Type anytime; messages enqueue; drain after turn (or on explicit send); count visible in status |
| **P3.C** | Interrupt / cancel contract | One ^C story that matches Busy vs WaitingInput vs Queued; no double-meaning chrome |

### Explicitly out of P3

- Changing plan/verify/uncertainty **semantics** (gates stay)  
- Mid-turn *steering* that cancels LLM and injects new instructions mid-stream (that’s **P4** — harder cancel tokens through litellm/tools)  
- Web / GUI  
- Rewriting Rich streaming (`MarkdownStream`) architecture beyond what’s needed for a reserved status/input band  
- Mode classifier / ASK heuristics  

### Non-negotiable invariants

1. Plan Y/N/C/V, drift explicit-yes, verify force/ack stay **blocking** (WaitingInput) — never silently auto-answered by queued text.  
2. Queued messages are **full next user turns**, not keystrokes into a confirm prompt.  
3. Cores stay: plan gates, uncertainty tree, verify-before-commit.  
4. Non-pretty / dumb terminals: queue may degrade to “type after idle” with a printed `Queued · N` line; no crash.  
5. FileWatcher / ClipboardWatcher keep working at Idle; during Busy they enqueue or no-op cleanly (no `interrupt_input` on a dead session).  
6. Tests must not require a live LLM for the orchestrator/queue unit suite.

---

## 3. Decisions (lock before code)

| # | Question | Decision for P3 | Rationale |
|---|----------|-----------------|-----------|
| **D1** | Concurrent full PromptSession during Busy? | **No.** Suspend full completer/menu during Busy. Use a **minimal line reader** (or prompt_toolkit app in “queue-only” mode) that only buffers text + Enter. | Full session + `\r` spinner + Rich Live is what garbled dogfood. Claude-like *feel* ≠ same implementation. |
| **D2** | Where does queue live? | `InputOutput.message_queue: deque[str]` + `TurnOrchestrator` in `aider/z/turn_ux.py` (new). Coder calls orchestrator enter/exit; does not invent a second queue. | One place; testable without Coder. |
| **D3** | When do queued messages run? | **After** current `run_one` returns to Idle — auto-drain one message into the next `run_one` (FIFO). Optional: status shows `Queued · N — will run next`. | Predictable; no interrupt-steering in P3. |
| **D4** | Can queued text answer a WaitingInput confirm? | **No.** While WaitingInput, typing still goes to the **confirm** prompt only. Queue accepts new lines only in Busy (and Idle if we keep a side buffer — prefer Busy-only enqueue). | Avoids “I typed the next task and accidentally Yes’d the plan.” |
| **D5** | Busy UI chrome | One reserved **status line** (mascot T0) + optional dim `type to queue…`. No slash completer. Label: `[o.o] Planning — … · Queued 1 · Ctrl+C interrupt`. | Matches #141 honesty; adds queue count. |
| **D6** | WaitingInput UI | Always: stop Busy → T3 panel (if any) → **short** prompt_toolkit line (existing escalation pattern). Queue frozen (not discarded). | Existing P0/resize fix stays the law. |
| **D7** | Idle after Busy with queue | Skip empty `get_input` if queue non-empty: pop → `run_one` immediately; print `▶ queued: …` one-liner before run. Bell once when entering Idle with queue. | Flow: work → next thought without re-prompt friction. |
| **D8** | ^C during Busy | Stop spinner; cancel in-flight explore future; abort LLM stream (existing partial-reply path); **keep** queue. First ^C = interrupt turn; second within 2s = exit (today). | Don’t punish queued thoughts. |
| **D9** | ^C during WaitingInput | Leave confirm (treat as No / empty per existing EOF/default behavior where safe); keep queue. | Confirms already have No. |
| **D10** | Esc / clear queue | At Idle: `Esc Esc` or `/queue-clear` clears queue. During Busy: `Ctrl+U` clears last queued item only (optional P3.B.2 if time). Minimum: `/queue` lists, `/queue-clear` clears. | Discoverable; low risk. |
| **D11** | Slash commands while Busy | Typing `/commit` while Busy **enqueues** the string; does not run until drain. Exception: none in P3 (no privileged interrupt commands). | Keeps state machine simple. |
| **D12** | Relation to placeholder / clipboard | Clipboard paste during Busy → enqueue (don’t call `interrupt_input`). Idle keeps today’s interrupt→placeholder. | Fixes “watcher assumes live PromptSession.” |
| **D13** | Feature flag | Default **on** for Z theme; `Z_TURN_QUEUE=0` disables queue reader (Busy blocks input like today, but still uses orchestrator busy chrome). | Escape hatch for CI/dumb terminals. |
| **D14** | Implementation order | **P3.A → P3.B → P3.C** in one PR if tight; else A+C first (honesty), B second PR. Prefer **one PR** so the flow ships together. | User ask: “everything needs to be a flow.” |

Open: mid-stream steer (cancel + inject) = **P4**, not P3.

---

## 4. State machine (the product)

```text
                    ┌──────────────┐
         ┌─────────►│    Idle      │◄────────────┐
         │          │ full › prompt│             │
         │          │ drain queue? │──yes──► run_one
         │          └──────┬───────┘             │
         │                 │ Enter message       │
         │                 ▼                     │
         │          ┌──────────────┐             │
         │          │ Busy(phase)  │◄──reflect───┤
         │          │ T0 spinner   │             │
         │          │ queue reader │             │
         │          └──────┬───────┘             │
         │        need     │              turn done
         │        human    │                     │
         │                 ▼                     │
         │          ┌──────────────┐             │
         │          │ WaitingInput │             │
         │          │ T3 + short › │─────────────┘
         │          │ queue frozen │
         │          └──────────────┘
         │
         └──── ^C abort Busy (queue kept)
```

### Phase labels (map to existing strings)

| Phase | Today | Orchestrator `Busy.phase` |
|-------|--------|---------------------------|
| Skills / explore / checklist / plan | `_phase_spinner_*` | `planning:*` |
| LLM wait / stream | `waiting_display` | `llm` |
| Post tools (shell/lint before confirm) | scattered | `post` until WaitingInput |

### Visual contract (one screen, one meaning)

| Region | Idle | Busy | WaitingInput |
|--------|------|------|--------------|
| Scrollback | history | stream + tools | panel + history |
| Status line | empty / mode only | mascot + phase + `Queued N` | **off** |
| Input line | full chrome + completer | `queue›` (dim) or hidden until key | short confirm only |
| Orange panel | never | never | only blocking |

If removing a UI element doesn’t change the state story, remove it.

---

## 5. Call graph (what we change)

```text
Coder.run
  └─ loop:
       orchestrator.enter_idle()
       if queue: msg = pop; print ▶ queued
       else: msg = get_input()          # Idle full prompt
       orchestrator.enter_busy("planning:…")
       run_one(msg)
         ├─ _phase_spinner_*  →  orchestrator.set_phase / set_busy
         ├─ plan_confirm_ask  →  orchestrator.enter_waiting_input → … → enter_busy
         ├─ send_message spinner → orchestrator.set_phase("llm")
         ├─ confirm_ask (shell/gate/…) → enter_waiting_input
         └─ finally: orchestrator.enter_idle()

InputOutput
  ├─ message_queue
  ├─ queue_reader (thread or nested app) started only in Busy
  ├─ _ensure_prompt_ready → orchestrator.ensure_waiting_or_idle
  └─ watchers: Idle interrupt; Busy enqueue
```

### New module: `aider/z/turn_ux.py`

- `TurnState` enum: `IDLE | BUSY | WAITING_INPUT`
- `TurnOrchestrator`: `enter_idle`, `enter_busy(phase)`, `enter_waiting_input(kind)`, `set_phase`, `enqueue`, `pop_queued`, `clear_queue`, `status_label`
- No Rich dependency beyond optional formatting helpers

### Touch points (implementation checklist)

| File | Change |
|------|--------|
| `aider/z/turn_ux.py` | **New** FSM + queue API |
| `aider/io.py` | Own queue; start/stop queue reader; watchers respect state; status helpers |
| `aider/coders/base_coder.py` | Wire `run` / `run_one` / spinner / keyboard_interrupt through orchestrator |
| `aider/watch.py`, `aider/copypaste.py` | Busy → enqueue path |
| `aider/commands.py` | `/queue`, `/queue-clear` |
| `aider/z/mascot.py` | Optional: don’t steal input row; write status via orchestrator callback |
| `docs/uncertainty/terminal-ux-for-engineers.md` | §3.3 + §12 P3 |
| Tests | `tests/basic/test_z_turn_ux_p3.py` |

---

## 6. Work packages (detailed)

### P3.A — Orchestrator + busy honesty

1. Add `TurnOrchestrator` with state transitions and assertions (`BUSY` cannot open full completer).  
2. Replace ad-hoc `io.agent_busy` / `_stop_agent_busy` with orchestrator (keep thin wrappers for back-compat).  
3. Guarantee: starting Busy clears leftover PromptSession chrome (newline + status row ownership).  
4. WaitingInput always goes through `enter_waiting_input` before `confirm_ask` / `plan_confirm_ask` / `prompt_ask`.  
5. Golden unit tests: illegal transition raises or no-ops safely; status label includes phase.

**Acceptance:** Never see spinner + slash menu + full `›` together in a scripted fake-TTY test.

### P3.B — Message queue

1. `deque` on IO; `enqueue` strips empty; max length (e.g. 20) with warning on overflow.  
2. During Busy: minimal reader — Enter enqueues; show `Queued · N` on status line.  
3. On Idle entry: if queue non-empty, auto-pop one and `run_one` (D7).  
4. `/queue` lists; `/queue-clear` clears; print T1 `Queued · N` when N changes (rate-limit).  
5. Clipboard/file during Busy enqueue (D12).

**Acceptance:** Scripted: start Busy → enqueue two lines → finish turn → both run FIFO without a manual idle type.

### P3.C — Interrupt contract

1. Document and implement D8–D9 in `keyboard_interrupt` + Busy cancel hooks.  
2. Explore pass cancel already exists — call it from orchestrator on Busy ^C.  
3. Queue preserved across single ^C.  
4. Status copy: `Ctrl+C interrupt turn · queued kept`.

**Acceptance:** Busy ^C leaves `Queued · N` intact; WaitingInput ^C doesn’t wipe queue.

---

## 7. Explicit non-goals (say no in review)

- “Just keep PromptSession open and hope”  
- Letting queue text satisfy Y/N confirms  
- Mid-stream “actually do this instead” steer (P4)  
- Redesigning uncertainties / verify messages in this tranche  

---

## 8. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Queue reader thread vs main stdout races | Only write status via orchestrator lock; never print Rich Live from reader thread |
| Resize garble returns | Keep short prompts for WaitingInput; don’t put long text in queue-reader prompt |
| Users think queue answered the plan | D4 + copy: `Waiting for your Yes/No — queued messages pause` |
| Dumb CI terminals | `Z_TURN_QUEUE=0`; orchestrator still tracks state |
| Double-drain / re-entrancy | `run()` drains at most one queued message per Idle entry; nested `run_one` doesn’t re-enter drain |

---

## 9. Test plan

| Test | Asserts |
|------|---------|
| `test_orchestrator_transitions` | IDLE→BUSY→WAITING→BUSY→IDLE; illegal paths safe |
| `test_queue_fifo_drain` | Two enqueues drain in order after Busy ends |
| `test_waiting_input_does_not_consume_queue` | Confirm answer ≠ queued string |
| `test_ctrl_c_preserves_queue` | enqueue → interrupt Busy → queue length unchanged |
| `test_no_spinner_with_full_prompt` | Mock: Busy true ⇒ full `get_input` path not entered |
| `test_clipboard_busy_enqueues` | Fake clipboard event during Busy → queue += 1 |
| Extend golden P2 fixture | Optional: one line `▶ queued:` allowed in drain path |

No live LLM required.

---

## 10. Rollout

1. Land plan PR (this doc + parent §12 update).  
2. Implementation PR(s) on `cursor/z-turn-flow-p3-…` with flag default-on for Z.  
3. Dogfood on CMake/event_bus style session: plan confirm → work → type next task while Busy → see drain.  
4. If painful, `Z_TURN_QUEUE=0` without removing orchestrator.

---

## 11. P4 preview (not this PR)

- Cancel token through `send_completion` / tool loops  
- “Steer”: ^C or special key → interrupt LLM → inject queued message as replacement instruction  
- Optional: show last queued preview in Idle placeholder  

---

## 12. Acceptance checklist (human)

- [ ] Always know Idle vs Busy vs WaitingInput from the bottom of the screen alone  
- [ ] Can type the next task while Planning/Waiting for model without losing it  
- [ ] Plan/drift/verify confirms never eat queued text  
- [ ] ^C interrupts work but keeps the queue  
- [ ] No glued file paths / duplicate lists / spinner-over-menu (regressions guarded)  
- [ ] Feels like one flow — not three features stacked  

---

## 13. Open questions for approval

1. Auto-drain on Idle (**D7**) vs require Enter on a `Queued · N — press Enter to run` gate?  
   **Recommendation:** auto-drain (faster flow); print `▶ queued:` so it’s visible.  
2. Queue reader look: dim `queue›` always visible in Busy, or appear only after first keypress?  
   **Recommendation:** appear on first keypress (less noise); status still shows `Queued N` when N>0.  
3. Ship A+B+C one PR or A+C then B?  
   **Recommendation:** one PR (flow coherence), flag-gated.
