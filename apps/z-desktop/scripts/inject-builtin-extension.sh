#!/usr/bin/env bash
# Copy compiled Z extension into Seam as a built-in extension.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="${Z_VSCODE_VENDOR:-$ROOT/vendor/vscode}"
EXT_SRC="$ROOT/extension"
DEST="$VENDOR/extensions/z-editor"

if [[ ! -d "$VENDOR" ]]; then
  echo "Missing vendor at $VENDOR" >&2
  exit 1
fi

if [[ ! -f "$EXT_SRC/package.json" ]]; then
  echo "Missing extension at $EXT_SRC" >&2
  exit 1
fi

echo "Compiling extension…"
(cd "$EXT_SRC" && npm ci --ignore-scripts 2>/dev/null || npm install --ignore-scripts)
(cd "$EXT_SRC" && npm run compile)

rm -rf "$DEST"
mkdir -p "$DEST"

# Copy runtime bits only (no rsync dependency)
cp -a "$EXT_SRC/package.json" "$DEST/"
cp -a "$EXT_SRC/out" "$DEST/"
cp -a "$EXT_SRC/themes" "$DEST/"
cp -a "$EXT_SRC/media" "$DEST/"
[[ -f "$EXT_SRC/readme.md" ]] && cp -a "$EXT_SRC/readme.md" "$DEST/" || true
[[ -f "$EXT_SRC/README.md" ]] && cp -a "$EXT_SRC/README.md" "$DEST/" || true
[[ -f "$EXT_SRC/LICENSE.txt" ]] && cp -a "$EXT_SRC/LICENSE.txt" "$DEST/" || true
[[ -f "$EXT_SRC/LICENSE" ]] && cp -a "$EXT_SRC/LICENSE" "$DEST/" || true

# Ensure out/ and themes/media exist
if [[ ! -f "$DEST/out/extension.js" ]]; then
  echo "inject failed: out/extension.js missing" >&2
  exit 1
fi

# Mark as built-in friendly (optional package.json tweak)
python3 - <<'PY' "$DEST/package.json"
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
data = json.loads(p.read_text(encoding="utf-8"))
# Remove vsce-only fields if any; keep engines
data.pop("scripts", None)
data.pop("devDependencies", None)
p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"Injected built-in extension → {p.parent}")
PY

echo "OK: $DEST"
echo "Next: cd $VENDOR && npm run gulp vscode-linux-x64   # or darwin/win32 target"
