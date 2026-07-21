<p align="center">
  <img src="assets/z-mark.svg" alt="Z" width="96" height="96">
</p>

# Z — The Coding Agent That Tells You When It's Guessing

Z is a coding agent, built on top of Aider, that flags what it's confident about, what it assumed, and what it never verified — so you review what actually needs it instead of reading every line the same way.

In testing, editing one file with an unverified secret key and a partially finished feature produced 6 correctly-traced notes in a single pass — including catching that a requested feature (receipt emails) was never actually built.

<p align="center">
  <img alt="Early Access" src="https://img.shields.io/badge/status-early%20access-C96A2B?style=flat-square&labelColor=0A0A0A"/>
</p>

This is **early / private access** — not a generally available public release.
[Join the waitlist](https://github.com/Nate-git05/z) (landing page at `/` when you run the web app)
to request access.

## Why Z

AI coding agents are getting trusted with more autonomy — editing across a codebase without a human watching every step. The problem isn't that they're wrong sometimes. It's that a wrong change looks identical to a correct one until something breaks. Z makes that difference visible before you ship, not after.

<!-- TODO: insert real terminal capture here — actual /uncertainties output from a live Z session (screenshot or short GIF), with caption e.g. "Z flagging a real change — six notes from one edit, each traceable to a concrete signal." Do not use mockups or the landing-page demo. -->

## Features

### Uncertainty tree

**Every change comes with a risk and confidence tree.** Notes are derived from concrete signals — tests, patterns, unverified config — not fake confidence percentages. Guide: [docs/uncertainty/README.md](docs/uncertainty/README.md).

### Ask, don't guess

**Ask, don't guess.** When Z isn't sure, it stops and asks — instead of quietly shipping a hallucinated fix.

### Shared team workspaces

**Your team's uncertainty is shared, not siloed.** A workspace-wide tree shows where any teammate's agent is unsure — not just yours.

### MCP tool integration

**Connect tools once; they show up in the agent.** MCP servers are managed in the web dashboard and load automatically — no local config files.

### Reusable skills

**Z can learn reusable playbooks from your work and apply them automatically on future tasks — no manual setup.** Guide: [docs/skills/README.md](docs/skills/README.md).

### Bring your own model

**Your keys, your models.** Works with Claude, GPT, and other providers via your own API key — account login is separate from model access.

### Built on Aider's proven core

**Built on Aider, not a rewrite.** Z inherits battle-tested repo mapping, git integration, and multi-language editing — and adds the uncertainty layer on top.

## Getting Started

```bash
# One-line install (macOS / Linux)
curl -fsSL https://raw.githubusercontent.com/Nate-git05/z/main/install.sh | sh
```

That script detects your OS, installs Z via pip from this repo, and puts `z` on your PATH.
A shorter `https://z.dev/install.sh` URL is planned once that domain is live.

Alternatively:

```bash
pip install "git+https://github.com/Nate-git05/z.git"
# or from a local checkout: pip install -e .
```

Then:

```bash
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY — bring your own
cd /path/to/your/project
z                              # signs in if needed, then starts the coding agent
```

Running `z` is enough: if you aren't signed in, you get the login screen first,
then the agent chat. You'll still need a model API key from your provider —
Z account login does **not** replace your model key. Use `z login` only when
you want to sign in (or switch accounts) without starting a session.

## Core commands

| Command | What it does |
|---------|----------------|
| `z` | Sign in if needed, then start the coding agent |
| `z login` / `z logout` / `z whoami` | Sign in only, sign out, show current user/workspace |
| `z models` | List curated available models |
| `z mcp list` | View MCP tools connected via the web dashboard |
| `z skill add` / `create` / `list` / `show` | Paste, generate, list, or show skill metadata |
| `/uncertainties` | Browse the uncertainty tree during a coding session |
| `/skills add` / `create` / `list` / `show` | Same skill commands inside a session |
| `z --model sonnet` | Start the agent with a specific model (same flags as Aider) |

Any other arguments pass through to the coding agent (same surface as Aider).

## More Information

| | |
|--|--|
| **Repository** | https://github.com/Nate-git05/z |
| **Architecture** | [ARCHITECTURE.md](ARCHITECTURE.md) |
| **Uncertainty tree** | [docs/uncertainty/README.md](docs/uncertainty/README.md) |
| **Skills guide** | [docs/skills/README.md](docs/skills/README.md) |
| **Codex brief (features + how to use)** | [docs/codex/z-features-and-usage.md](docs/codex/z-features-and-usage.md) |
| **Docs** | _Coming soon_ |
| **Community** | _Coming soon_ |
| **Waitlist / landing** | Run the `z_server` web app and open `/`, or check the repo for updates |

Z builds on Aider's core editing engine — see
[Aider's documentation](https://aider.chat/docs/) for details on the underlying agent
(repo map, edit formats, git integration, and model configuration).

## License

Apache License 2.0 — same as Aider. See [LICENSE.txt](LICENSE.txt).
