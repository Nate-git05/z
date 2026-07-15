# Z Architecture

Reader-facing product overview lives in [README.md](README.md). This file covers
implementation detail that would clutter the main README.

Z is built on [Aider](https://github.com/Aider-AI/aider)'s editing engine and
adds account auth, uncertainty tracking, MCP loading, reusable skills, and a
small web backend (`z_server`).

---

## Layout (high level)

| Area | Location | Role |
|------|----------|------|
| CLI entry | `aider/z/cli.py` (`z` script) | Login gate, skill/MCP subcommands, passthrough to agent |
| Agent core | `aider/` (Aider) | Editing, repo map, git, models |
| Z layers | `aider/z/` | Auth, skills, uncertainty, MCP client, terminal UI |
| Web / API | `z_server/` | Accounts, workspaces, skills sync, waitlist, dashboard |

---

## Skills (implementation)

Product guide: [docs/skills/README.md](docs/skills/README.md).

**On disk.** Skill bodies are markdown files under `~/.z/skills/*.md` (override
root with `Z_HOME`) with YAML frontmatter. Frontmatter includes `title`,
`description`, `tags`, `project_types`, `triggers`, `source`, scope/ids, and
**`path`** (absolute path to the file so the agent can load the body after a
retrieval hit).

**Metadata.** Z infers metadata on paste / generate / capture (`aider/z/skills/infer.py`).
Users author the body; they are not required to fill a metadata form.

**Vector index.** Metadata (+ embedding text from title/description/tags/triggers/
project_types) is stored in a local **ChromaDB** collection under
`~/.z/chroma/skills` (`aider/z/skills/vector.py`). The full body is **not**
stored in Chroma — only metadata including `path`.

**Retrieve & apply.** On a user task, Z queries ChromaDB, opens each hit’s
`path`, and injects matching skill bodies into context. If ChromaDB is
unavailable, keyword matching over the session index is used as a fallback
(`aider/z/skills/index.py`, `aider/z/skills/session.py`).

**Create paths.**

| Path | Entry | Notes |
|------|-------|--------|
| Paste | `z skill add` / `/skills add` | No model required |
| Generate | `z skill create` / `/skills create` | BYOK model |
| Capture | Post-task prompts in `base_coder` | Ask create → ask see metadata |

**Sync.** When signed in, skills may sync to `z_server` (`/v1/skills`) and be
managed at `/app/skills`. Create remains CLI-first.

**Reindex.** `z skill reindex` rebuilds the Chroma collection from local files.

---

## Uncertainty tree

Product guide: [docs/uncertainty/README.md](docs/uncertainty/README.md).

Detectors in `aider/z/uncertainty/` score checkable signals (tests, high-stakes
keywords, API/MCP assumptions, migrations, TODOs, etc.) into separate **risk**
and **confidence** tiers — not model self-rated percentages. Nodes persist
locally under `~/.z/uncertainty/` and can sync to `/v1/uncertainty/*`. In-session
browse: `/uncertainties`.

---

## Auth vs model keys

- **Z account** (`z` / `z login`) → workspace features, MCP list, skills sync;
  credentials in `~/.z/credentials`
- **Model API keys** → BYOK via env (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …);
  never replaced by Z login

`Z_CLI=1` is set when the process is started via the `z` entrypoint so Aider’s
OpenRouter “login” onboarding is not offered in place of Z account auth.

---

## MCP

Connections are managed in the web dashboard. At session start the CLI loads
tools for the signed-in workspace via `aider/z/mcp_client.py` — no local MCP
config file required for the default path.

---

## Web backend

`z_server` (FastAPI) serves the landing/waitlist page, auth APIs, dashboard
(` /app/integrations`, `/app/skills`), and sync endpoints for uncertainty and
skills. See `z_server/README.md`.
