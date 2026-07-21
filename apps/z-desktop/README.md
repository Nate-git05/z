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

## Bootstrap (when app work starts)

```bash
# From repo root — shallow clone OpenAI Codex (Apache-2.0) as reference shell
git clone --depth 1 https://github.com/openai/codex.git apps/z-desktop/vendor/codex
```

Then strip/replace Codex branding and wire Z’s uncertainty / skills / verify APIs.
Do not commit the full vendor tree until the app slice is actively built.
