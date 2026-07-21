# Z Desktop (app shell)

**Status:** scaffold / north star only — not a shipping binary yet.

## Intent

Desktop app for Z, shaped like Cursor / Codex apps:

- Workspace-first coding (not terminal-primary)
- **Z model router only** (no BYOK as the product path)
- Visual **uncertainty tree** (risk-sorted nodes)
- **Skills** browser (generation + application)
- **Commit block** surface (blocked changes / PRs)
- **MCP** connections in-app

Full decision: [`docs/app/z-desktop-north-star.md`](../../docs/app/z-desktop-north-star.md)

## V1

V1 ships in the existing Z CLI/agent with **router-only** model setup.
This directory is reserved for the Codex-based app implementation.

## Codex upstream (local vendor)

Upstream: [openai/codex](https://github.com/openai/codex) (Apache-2.0).

```bash
# From repo root
git clone --depth 1 https://github.com/openai/codex.git apps/z-desktop/vendor/codex
# or: gh repo clone openai/codex apps/z-desktop/vendor/codex
```

Local path (gitignored — do not commit the vendor tree):

`apps/z-desktop/vendor/codex/`

Layout of interest for the Z app shell:

| Path | Role |
|------|------|
| `codex-rs/` | Rust agent / app core |
| `codex-cli/` | CLI packaging |
| `sdk/` | SDK surfaces |
| `docs/` | Upstream docs |

Next: strip/replace Codex branding and wire Z uncertainty / skills / verify / router APIs.
