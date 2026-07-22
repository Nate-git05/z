#!/usr/bin/env bash
# Phase 12 — apply brand overlay then invoke upstream Code - OSS package scripts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="${Z_VSCODE_VENDOR:-$ROOT/vendor/vscode}"

"$ROOT/scripts/apply-product.sh"

# Compile Z extension for bundling
if [[ -f "$ROOT/extension/package.json" ]]; then
  (cd "$ROOT/extension" && npm run compile)
fi

echo ""
echo "Brand overlay applied. Build Z Editor from the Seam/VS Code tree:"
echo "  cd $VENDOR"
echo "  # follow upstream docs: yarn/npm install + gulp vscode-linux-x64 / vscode-darwin-arm64"
echo ""
echo "Then copy or symlink the compiled extension into the built-in's extensions path,"
echo "or install apps/z-desktop/extension as a VSIX against the branded build."
echo ""
echo "Smoke checklist:"
echo "  1. Window title / About shows Z Editor"
echo "  2. Theme Z Terminal is default (or apply via command)"
echo "  3. Chat opens center; left Uncertainty/Skills/MCP/Profile; right Commit Gate"
echo "  4. z-editor:// deep links resolve (signin + mcp/oauth/done)"
