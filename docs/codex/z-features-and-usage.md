# Z for Codex — Features & How to Program With It

This brief is for **Codex** (and any external agent/evaluator): what Z is, every major product surface, and the concrete workflow a human uses when coding with Z day to day.

Z is a terminal coding agent built on **Aider**. Account login, uncertainty, skills, MCP, planning gates, and reliability layers are Z-specific. Editing, repo map, git, and most `/` commands inherit from Aider.

---

## 1. One-sentence product

**Z edits your repo like a coding agent, but surfaces what it assumed, never verified, or left unfinished — so you review risk, not every line the same way.**

North-star failure mode Z optimizes against: **false completion** (claiming “done” when the central journey is still broken).

---

## 2. Install & first run

```bash
# Install (macOS / Linux)
curl -fsSL https://raw.githubusercontent.com/Nate-git05/z/main/install.sh | sh

cd /path/to/your/project
z
```

First-run flow:

1. **Z account login** (email / phone / Google) — workspace features, MCP list, skill sync  
2. **Model access** — choose **BYOK** (bring your own provider key — Anthropic, OpenAI, DeepSeek, Groq, Gemini, Kimi/Moonshot) or **Z's router** (preferred model + escalation, no local keys). Both are first-class, always offered.
3. Agent chat starts in the project directory  

Important: **Z login ≠ model key.** You still need a provider key (or router mode). Keys live in env / `~/.z/byok.env`; account tokens in `~/.z/credentials`.

```bash
z login          # sign in without starting a session
z logout
z whoami
z reset          # re-pick Z router model (keeps login)
z auth switch    # same — re-pick preferred router model
z --model sonnet # start with a specific model (Aider-compatible flags)
```

Escape hatch for automation: `Z_SKIP_ACCOUNT=1` skips the account gate.

---

## 3. Feature inventory

### 3.1 Uncertainty tree (core differentiator)

After edits settle, Z runs **signal-based detectors** (tests, high-stakes paths, unverified APIs, requirement gaps, blast radius, etc.) and builds a **risk × confidence** tree — not fake “87% sure” scores.

| Idea | Meaning |
|------|---------|
| **Risk** | How bad if wrong |
| **Confidence** | How much real evidence exists |
| **Tiers** | Low / Medium / High |

Typical node types: Untested Path, Unverified Assumption, High-Stakes Surface, Requirement Gap, Integration Ripple, Failure Blind Spot, Verification Integrity, …

**In session:**

```text
/uncertainties              # browse, risk-first
/uncertainties file         # group by file
/uncertainties session      # group by task
/uncertainties stats        # detector override rates
/uncertainties 3            # open note #3 → Fix / Test / Explain / Ignore / Custom
```

**CLI:**

```bash
z uncertainty stats
```

**Commit gate** (when engine is active):

| Tier | Behavior |
|------|----------|
| High | Blocks commit until Resolved (or logged force override) |
| Medium | Requires explicit acknowledgment (`--yes` cannot bypass) |
| Low / Evidence of Safety | Never blocks |

Escape: `--no-verify-commit-gate` or `Z_SKIP_VERIFY_GATE=1`.

Local store: `~/.z/uncertainty/<repo>.json`. Optional sync when signed in.

Full guide: [docs/uncertainty/README.md](../uncertainty/README.md).

---

### 3.2 Ask, don’t guess (escalation)

When Z is unsure about a high-impact choice, it **stops and asks** in an orange “Z needs your input” panel instead of inventing a silent default.

Examples:

- Ambiguous requirements / checklist confirmation  
- Implementation plan approval (high-stakes / high blast-radius tasks)  
- Medium-risk uncertainty acknowledgment before commit  
- Skill capture opt-ins after a good turn  

Programmers should treat these prompts as **real review checkpoints**, not noise — answering them steers the agent; rejecting a plan aborts before diffs.

---

### 3.3 Gated implementation plans

For high-stakes / architecture / journey-heavy requests, Z drafts a **reviewable plan before writing code**:

- Clean title + approach (not a dump of your raw message)  
- Numbered steps  
- Out of scope  
- Validation contracts, invariants, capability/architecture/journey extensions when relevant  

You see the full plan in scrollback; the orange confirm shows the compact approach + steps. **Reject → no edits for that turn.**

---

### 3.4 Skills (reusable playbooks)

Teach Z once; it retrieves and applies playbooks on matching tasks.

| Create path | How |
|-------------|-----|
| Paste | `z skill add` / `/skills add` |
| Generate | `z skill create "…"` / `/skills create …` |
| Capture | After a solid turn: opt-in “save as skill?” |

Storage: `~/.z/skills/*.md` + ChromaDB index at `~/.z/chroma/skills`.  
Router decides **apply vs skip** (stack match, scaffold already done, `needs_review`, etc.).

```bash
z skill list
z skill show stripe
z skill accept <name>    # allow auto-apply after capture review
z skill reject <name>
z skill reindex
```

When a skill matches you’ll see: `Applying skill(s): …`

Full guide: [docs/skills/README.md](../skills/README.md).

---

### 3.5 Model routing

| Mode | Behavior |
|------|----------|
| **Router** | Pick a preferred Z model; router classifies task complexity/domain and can escalate within budget — no local provider keys |
| **BYOK** | Pick a provider (Anthropic, OpenAI, DeepSeek, Groq, Gemini, Kimi/Moonshot), paste its key. Z routes per task across that provider's own model tiers; `z byok add` connects more providers so Z can route across all of them |

Both are first-class and always offered right after login — reconfigure anytime with `z reset` or `z auth switch`. List models: `z models` / `z models --all`. Manage BYOK provider keys: `z byok add` / `z byok list`.

---

### 3.6 MCP tool integration

MCP servers are managed in the **web dashboard** for the workspace. On session start, Z loads connected tools automatically — no local MCP config file for the default path.

```bash
z mcp list
```

---

### 3.7 Shared team workspaces

```bash
z workspace create "Acme"
z workspace invite you@company.com
z workspace members
z workspace switch
```

Workspace-scoped: shared uncertainty visibility, skills sync, MCP connections. Personal skills stay under `~/.z/skills/` unless synced.

---

### 3.8 Reliability / anti-false-completion layers

Shipped under `aider/z/uncertainty/` (see [reliability-9.md](../uncertainty/reliability-9.md)):

| Subsystem | What it does for the programmer |
|-----------|----------------------------------|
| Verification integrity | Blocks weakening typecheck/tests/CI to go green |
| Failure classification | Distinguishes env vs type vs assertion failures; backtracks to root cause |
| Capability plan | Names required abilities even when no skill matches |
| Architecture checkpoint | Pre-coding structure questions for non-trivial work |
| Critical journeys | Typed evidence (unit ≠ multi-session E2E) |
| Completion gate | May report **PARTIAL** instead of claiming complete |
| Evidence ledger | Stale-marks evidence after later edits |
| Clean-room (optional) | Lockfile → install → check pipeline (`Z_RUN_CLEANROOM=1`) |
| Drift detection | Notices when work wanders off the approved plan / checklist |

---

### 3.9 Terminal UX

| Piece | Role |
|-------|------|
| Z theme | Black / white + burnt-orange accent (`--z-theme` / `--no-z-theme`) |
| Startup banner + scientist mascot | Identity / idle pose |
| Compact mascot spinner | Single-line `[o.o]` wait while planning / model works |
| Escalation panel | Orange-bordered box when Z needs a decision (plan, gate, drift) |

**Design target (not fully implemented):** [terminal-ux-for-engineers.md](../uncertainty/terminal-ux-for-engineers.md) — signal tiers, compact plan confirm, mode chrome.

---

### 3.10 Inherited Aider programming surface

Z keeps Aider’s day-to-day editing loop:

| Habit | Commands |
|-------|----------|
| Put files in context | `/add path`, `/drop`, `/ls`, `/read-only` |
| Ask without editing | `/ask …` |
| Edit | plain English → `/code` mode (default coding) |
| Architect mode | `/architect` |
| See change | `/diff` |
| Undo last agent commit | `/undo` |
| Commit | `/commit` (subject to uncertainty gate) |
| Run / test | `/run …`, `/test …`, `!command` |
| Repo orientation | `/map`, `/map-refresh`, `/tokens` |
| Lint | `/lint` |
| Model switch | `/model`, `/models` |

Any unknown `z …` args pass through to the agent CLI (same flag surface as Aider).

---

## 4. How to program with Z (recommended workflow)

### Step A — Start in the repo

```bash
cd ~/src/my-app
z
```

Prefer starting **inside a git repo**. Z (via Aider) maps the tree, commits when appropriate, and can undo its own commits.

### Step B — Load the right context

- `/add` the files you want edited or deeply reviewed  
- Use `/read-only` for reference APIs / docs you don’t want rewritten  
- Let the repo map cover the rest; don’t dump the whole monorepo unless needed  

### Step C — State the outcome, not just the edit

Good prompts for Z:

- What “done” looks like (user path, API contract, CLI behavior)  
- Constraints (no new deps, keep public API, text-only, etc.)  
- How you’ll verify (test command, manual smoke)  

For non-trivial / high-stakes work, expect a **plan confirm**. Review approach + steps; say **No** if it’s wrong.

### Step D — Let skills fire (or teach one)

- If you see `Applying skill(s): …`, the agent is following a saved playbook — good  
- Missing house rules? `/skills add` or `/skills create "how we do X here"` once  
- After a great turn, accept the capture prompts so the next session starts smarter  

### Step E — Watch escalations

- Compact mascot spinner = model is thinking  
- Orange panel = Z needs a decision; answer it; don’t auto-yes blind  

### Step F — Review the uncertainty tree before you trust “done”

```text
/uncertainties
```

Triage:

1. **High** — fix or consciously force (logged)  
2. **Medium** — acknowledge with eyes open  
3. **Requirement gaps / unverified APIs** — usually worth a `/test` or Fix action  

Do **not** treat green unit tests alone as journey-complete when Z says PARTIAL.

### Step G — Commit through the gate

```text
/commit
```

If blocked: resolve High nodes, ack Medium, or fix verification — don’t weaken CI/typecheck to silence the tree (integrity will flag that).

### Step H — Iterate with tight loops

Classic Z loop:

```text
describe task → (plan?) → edit → verify → /uncertainties → fix → /commit
```

Use `/undo` if the last agent commit was wrong. Use `/diff` before commit when you want a human pass.

---

## 5. Mental model (control flow)

```text
you: task in natural language
        │
        ├─ skill retrieve + router (+ capability plan)
        ├─ checklist / plan gate (when high stakes)
        ▼
   agent edits (Aider engine + git)
        │
        ├─ mascot spinner during model calls
        ├─ settle → verify (tests / typecheck when available)
        ├─ detectors → uncertainty tree
        ├─ integrity / journeys / completion gate
        ▼
   escalate if needed  →  /uncertainties triage  →  /commit
        │
        └─ optional: capture skill for next time
```

---

## 6. Command cheat sheet

### Outside the session (`z …`)

| Command | Purpose |
|---------|---------|
| `z` | Login if needed → coding agent |
| `z login` / `logout` / `whoami` | Account |
| `z reset` / `z auth switch` | Re-pick model mode |
| `z models` | Curated model list |
| `z mcp list` | Connected MCP tools |
| `z skill …` | add / create / list / show / accept / reject / reindex |
| `z workspace …` | create / invite / members / switch |
| `z uncertainty stats` | Detector disposition rates |
| `z taxonomy review` | Bug-concept taxonomy candidates (read-only) |

### Inside the session (`/…`)

| Command | Purpose |
|---------|---------|
| `/uncertainties` […] | Browse / act on risk tree |
| `/skills` […] | Manage skills without leaving chat |
| `/add` `/drop` `/ls` `/read-only` | Context |
| `/ask` `/code` `/architect` | Modes |
| `/diff` `/commit` `/undo` | Change control |
| `/run` `/test` `/lint` `/map` | Verify & orient |
| `/model` `/models` | Switch models mid-session |
| `/help` | Help (Aider + Z commands) |

---

## 7. Useful environment knobs

| Variable | Effect |
|----------|--------|
| `Z_HOME` | Override `~/.z` root |
| `Z_SKIP_ACCOUNT=1` | Skip account login gate |
| `Z_SKIP_VERIFY_GATE=1` | Disable verify-before-commit gate |
| `Z_UNCERTAINTY_AUTO_ACT=1` | Allow auto-act on high worries (off by default) |
| `Z_RUN_CLEANROOM=1` | Actually run clean-room verification pipeline |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / … | BYOK providers |

---

## 8. What Codex should evaluate Z on

When judging Z sessions, prefer these checks over “did the chat sound confident?”:

1. **Did it ask** when the request was ambiguous or high blast-radius?  
2. **Did the plan** describe approach + steps (not echo the user)?  
3. **Did verification integrity** hold (no weakened typecheck/tests to pass)?  
4. **Did completion language** match evidence (COMPLETE vs PARTIAL)?  
5. **Did `/uncertainties`** surface real human worries with actionable tiers?  
6. **Did skills** apply only when stack/step-appropriate?  
7. **Would a careful engineer** ship after clearing High / acking Medium?

Deep reliability mapping: [docs/uncertainty/reliability-9.md](../uncertainty/reliability-9.md).

---

## 9. Related docs

| Doc | Audience |
|-----|----------|
| [README.md](../../README.md) | Product overview + install |
| [ARCHITECTURE.md](../../ARCHITECTURE.md) | Implementation layout |
| [docs/uncertainty/README.md](../uncertainty/README.md) | Uncertainty tree deep dive |
| [docs/skills/README.md](../skills/README.md) | Skills deep dive |
| [Aider usage docs](https://aider.chat/docs/) | Underlying edit/git/model mechanics |
