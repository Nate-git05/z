<p align="center">
  <img src="assets/z-mark.svg" alt="Z" width="96" height="96">
</p>

<h1 align="center">The Coding Agent That Tells You When It's Guessing</h1>

<p align="center">
  Z is a coding agent, built on top of Aider, that flags what it's confident about,
  what it assumed, and what it never verified — so you know exactly where to focus
  review instead of reading every line.
</p>

<p align="center">
  <img alt="Early Access" src="https://img.shields.io/badge/status-early%20access-C96A2B?style=flat-square&labelColor=0A0A0A"/>
</p>

> **Z is built on top of [Aider](https://github.com/Aider-AI/aider), an open-source AI pair-programming tool.**
> Z adds an uncertainty-tracking layer, shared team workspaces, MCP tool integration,
> and reusable skills on top of Aider's core editing engine.

This is **early / private access** — not a generally available public release.
[Join the waitlist](https://github.com/Nate-git05/z) (landing page at `/` when you run the web app)
to request access.

## Features

### Uncertainty tree

Every change comes with notes on what's confident, assumed, untested, or risky.
No fake confidence percentages — tiers are derived from concrete signals
(tests run, patterns matched, live API calls verified).

### Ask, don't guess

When the agent's confidence is genuinely too low to proceed safely, it pauses
and asks a specific question instead of hallucinating a fix.

### Shared team workspaces

A workspace-wide uncertainty tree so a whole team can see where any teammate's
agent is uncertain — not just their own.

### MCP tool integration

Connect MCP servers through the web dashboard. Tools load into the agent
automatically — no local config files.

### Reusable skills

Generate a skill from any task with your own model. Skills are auto-discovered
and applied by the agent in future sessions — no manual file navigation.

### Bring your own model

Works with Claude, GPT, or other providers via your own API key.
Z doesn't host or resell model access. Account login is separate from model keys.

### Built on Aider's proven core

Inherits Aider's battle-tested codebase mapping, git integration, and
multi-language editing — rather than reinventing it.

## Getting Started

A one-line `curl | sh` installer is **coming soon**. Until then, install from this repository:

```bash
# Early access — install from the repo (package name on PyPI TBA)
pip install "git+https://github.com/Nate-git05/z.git"

# Or, from a local checkout:
# pip install -e .

export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY — bring your own
z login                        # optional — unlocks workspace / MCP / shared skills
cd /path/to/your/project
z
```

You'll need a model API key from your provider. `z login` is for Z account /
workspace features; it does **not** replace your model key.

## Core commands

| Command | What it does |
|---------|----------------|
| `z login` / `z logout` / `z whoami` | Sign in to your Z account, sign out, show current user/workspace |
| `z models` | List curated available models |
| `z mcp list` | View MCP tools connected via the web dashboard |
| `z skill create "..."` / `z skill list` | Create a reusable skill with your model, or list skills |
| `/uncertainties` | Browse the uncertainty tree during a coding session |
| `/skills` | List or create skills from inside a session |
| `z --model sonnet` | Start the agent with a specific model (same flags as Aider) |

Any other arguments pass through to the coding agent (same surface as Aider).

## More Information

| | |
|--|--|
| **Repository** | https://github.com/Nate-git05/z |
| **Docs** | _Coming soon_ |
| **Community** | _Coming soon_ |
| **Waitlist / landing** | Run the `z_server` web app and open `/`, or check the repo for updates |

Z builds on Aider's core editing engine — see
[Aider's documentation](https://aider.chat/docs/) for details on the underlying agent
(repo map, edit formats, git integration, and model configuration).

## License

Apache License 2.0 — same as Aider. See [LICENSE.txt](LICENSE.txt).
