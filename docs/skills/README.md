# Z Skills

**Teach Z once. It remembers how your repo works.**

Z Skills turns the way you build — Stripe webhooks, migrations, auth patterns, team conventions — into reusable playbooks. Paste one, generate one, or let Z capture one after a good turn. Next time the task shows up, Z finds the right skill and applies it automatically.

---

## The problem it solves

Coding agents forget your project’s rules the moment the chat ends.

You re-explain the same Stripe signature check. You restate the same Alembic expand/contract rule. You paste the same “how we do auth here” notes into every session. That friction is pure waste — and it gets worse as the codebase grows.

**Z Skills fixes that.** You save the playbook once. Z indexes it, retrieves it when a task matches, and follows it without you attaching files or re-prompting.

| Pain | Without skills | With Z Skills |
|------|----------------|---------------|
| Repeat yourself every session | Re-paste conventions into the chat | Skill auto-applies on match |
| “Where did we document that?” | Hunt old chats / Notion / READMEs | `~/.z/skills/` + ChromaDB retrieval |
| Agent invents the wrong pattern | Guessy defaults | Your playbook, loaded by path |
| Capture good work | Lost after the turn | Opt-in “save as skill?” after a task |

---

## How it works

<img src="assets/z-skills-flow.png" alt="Z Skills flow: paste or capture → save markdown with metadata and path → index in ChromaDB → agent retrieves and applies on matching tasks" width="100%" />

1. **Create** — paste a playbook, generate from a prompt, or capture after a completed task  
2. **Store** — markdown file in `~/.z/skills/*.md` with metadata (including `path`)  
3. **Index** — metadata + embedding in local **ChromaDB** (`~/.z/chroma/skills`)  
4. **Route + apply** — before a task *and again on each workflow step* (reflections), Z retrieves candidates, then a **skill router** decides apply vs skip (language/stack match, scaffold already done, already injected). Only approved skills are injected.

Z owns metadata inference (`title`, `description`, `tags`, `triggers`, `project_types`, `kind`, `languages`, `artifacts`, `path`). You write the body; you don’t fill out a form.

### Skill router (apply vs skip)

| Kind | Meaning | When it applies |
|------|---------|-----------------|
| **scaffold** | One-shot bootstrap (“create Go project”) | Only while artifacts are missing and the step is scaffolding |
| **playbook** | Ongoing guidance (“how we do auth”) | When the *current* step matches; not re-injected every turn |

Wrong-stack skills are skipped (e.g. HTML skill on a Go repo). Scaffold skills stop firing once `go.mod` / listed artifacts exist — so creating a Go server won’t keep re-applying “create Go file” on every later turn.

Skills can be injected **progressively** during a task (turn start + each reflection), not only once at the beginning.

---

## Quick start

```bash
# Paste / import (simplest)
z skill add
# or inside a session:
# /skills add

# Generate from a prompt (uses your BYOK model)
z skill create "how this repo validates Stripe webhooks"

# List what you have
z skill list

# Inspect metadata for one skill
z skill show stripe
```

Then just work:

```bash
cd your-project
z
```

When your task matches a skill, you’ll see:

```text
Applying skill(s): Stripe webhook validation
```

By default Z injects a **compact skill directive** (title, meta, truncated body +
path to the full playbook on disk) so the coding turn stays thin. Set
`Z_SKILL_INJECT_FULL=1` if you want the legacy full-markdown inject.

---

## After a task (capture flow)

When Z finishes a non-trivial edit turn:

1. **“Want me to save this as a reusable skill?”** → Yes / No  
2. If Yes → Z builds a **grounding pack** (git diff + final file contents + extracted symbols), then generates the skill from that evidence only  
3. A **grounding check** verifies named classes/methods exist in the changed files. Invented APIs → `needs_review: true` (blocked from auto-apply) + an uncertainty node  
4. **“Want to see the new skill?”** → Yes / No  
5. If Yes → shows **name + metadata only** (path, grounded symbols, needs_review, …)  

Accept a reviewed capture with `z skill accept <name>` so it can auto-apply.

Two opt-ins. No surprise dumps. No invented TokenBuckets.

---

## Skill file shape

```markdown
---
id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
title: Stripe webhook validation
description: Verify signatures, idempotency, and raw body handling
kind: playbook
languages: [python]
artifacts: []
apply_once: false
tags: [stripe, webhooks, payments]
project_types: [api, backend]
triggers: [stripe, webhook, signature]
path: /Users/you/.z/skills/stripe-webhook-validation-a1b2c3d4.md
source: paste
scope: personal
created_at: 2026-07-14T20:00:00Z
updated_at: 2026-07-14T20:00:00Z
---

## When to use
…

## Steps
…
```

Scaffold example extras:

```yaml
kind: scaffold
languages: [go]
artifacts: [go.mod, main.go]
apply_once: true
```

- **Body** lives on disk  
- **Metadata + path + embedding** live in ChromaDB for retrieval  
- Retrieve → **route** → open `path` → inject only if approved  

---

## Commands

| Command | What it does |
|---------|----------------|
| `z skill add` / `/skills add` | Paste a skill; Z infers metadata and indexes it |
| `z skill create "…"` / `/skills create …` | Generate a skill with your connected model |
| `z skill list` / `/skills` | List local (+ workspace when signed in) |
| `z skill show <name>` / `/skills show <name>` | Show metadata; optionally open the body |
| `z skill accept <name>` / `/skills accept <name>` | Clear `needs_review` so a capture can auto-apply |
| `z skill reindex` | Rebuild the ChromaDB index from local files |

Manage / share synced skills in the web app at `/app/skills` (create stays CLI-first).

---

## Mental model (refactored)

```text
paste / generate / capture
            │
            ▼
   ~/.z/skills/*.md          ← body + frontmatter (path is ground truth)
            │
            ▼
   ChromaDB index            ← metadata + path + embedding (recall)
            │
            ▼
   ┌── workflow checkpoints ─────────────────────────────┐
   │  turn start                                         │
   │  each reflection (tests / gaps / next step)         │
   │       │                                             │
   │       ▼                                             │
   │  retrieve candidates                                │
   │       │                                             │
   │       ▼                                             │
   │  skill router (precision)                           │
   │    • needs_review? → skip until accept              │
   │    • language/stack match?                          │
   │    • scaffold artifacts already exist? → skip       │
   │    • grounded symbols still present? (stale check)  │
   │    • already injected this session? → skip          │
   │    • scaffold vs ongoing task intent                │
   │       │                                             │
   │       ▼                                             │
   │  inject only newly approved skills                  │
   └─────────────────────────────────────────────────────┘
```

UI:
- Turn start: `Applying skill(s): …`
- Mid-task step: `Injecting skill(s) for this step: …`
- Verbose: `Skill skip — <title>: <reason>`

Satisfaction for scaffolds is remembered per repo under `~/.z/skills/state.json`.

---

## Code map

| Piece | Path |
|-------|------|
| Router | `aider/z/skills/router.py` |
| Grounding pack + check | `aider/z/skills/grounding.py` |
| Multi-checkpoint pull | `aider/z/skills/session.py` (`pull_skills_for_checkpoint`) |
| Wire-in | `aider/coders/base_coder.py` (`_maybe_pull_skills`, `_maybe_suggest_skill`) |
| Schema | `aider/z/skills/schema.py` (`kind`, `needs_review`, `grounded_symbols`, …) |
| Infer defaults | `aider/z/skills/infer.py` |
| Tests | `tests/basic/test_z_skill_router.py`, `tests/basic/test_z_skill_grounding.py` |

---

## Requirements

- Z CLI installed from this repo  
- `chromadb` (pulled in with Z’s dependencies)  
- Optional: signed-in Z account to sync/share via `/app/skills`  
- Model API key (BYOK) only needed for **generate** / **capture**, not for paste  

```bash
pip install -U "git+https://github.com/Nate-git05/z.git"
```

---

## Design principles

- **Paste first** — importing a playbook must be one step  
- **Z writes metadata** — including `path`, `kind`, `languages`, `artifacts`  
- **Retrieve then route** — Chroma recalls; router decides apply/skip  
- **Progressive injection** — skills can enter mid-task as the step changes  
- **Scaffolds are one-shot** — stop once the project exists  
- **Opt-in capture, opt-in peek** — never dump a new skill unless asked  

That’s Z Skills: write the playbook once; the router pulls the *right* one when the *current* step needs it.
