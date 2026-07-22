#!/usr/bin/env bash
# Brand Seam, inject Z extension, optionally run gulp package targets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="${Z_VSCODE_VENDOR:-$ROOT/vendor/vscode}"
TARGET="${1:-}"  # e.g. vscode-linux-x64 | vscode-darwin-arm64 | ""

"$ROOT/scripts/generate-icons.py" 2>/dev/null || python3 "$ROOT/scripts/generate-icons.py"
"$ROOT/scripts/apply-product.sh" --require-icons
"$ROOT/scripts/inject-builtin-extension.sh"

if [[ -z "$TARGET" ]]; then
  cat <<EOF

Brand + built-in extension ready under:
  $VENDOR

Build an unsigned Z Editor artifact (from vendor tree):
  cd $VENDOR
  nvm use   # Node $(cat "$VENDOR/.nvmrc" 2>/dev/null || echo 24)
  npm ci
  npm run gulp -- vscode-linux-x64
  # or: npm run gulp -- vscode-darwin-arm64

Or run this script with a target:
  $0 vscode-linux-x64

Smoke checklist:
  1. Window title / About shows Z Editor
  2. Theme Z Terminal applies
  3. Chat opens; MCP / Uncertainty / Commit Gate visible
  4. z-editor:// deep links (signin, mcp/oauth/done)
  5. Install Z engine if prompted: pip install -e '.[web]'  (or PyPI when published)

EOF
  exit 0
fi

if [[ ! -f "$VENDOR/package.json" ]]; then
  echo "Vendor missing package.json" >&2
  exit 1
fi

echo "Running gulp $TARGET (this can take a long time)…"
(
  cd "$VENDOR"
  if command -v nvm >/dev/null 2>&1 && [[ -f .nvmrc ]]; then
    # shellcheck disable=SC1091
    source "${NVM_DIR:-$HOME/.nvm}/nvm.sh" 2>/dev/null || true
    nvm use || true
  fi
  if [[ ! -d node_modules ]]; then
    npm ci
  fi
  npm run gulp -- "$TARGET"
)

echo "Done. Look under $VENDOR/.build/ or VSCode-*/ for artifacts."
