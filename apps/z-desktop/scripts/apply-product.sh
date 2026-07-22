#!/usr/bin/env bash
# Phase 12 — merge apps/z-desktop/product.z.json into vendor/vscode/product.json
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../.." && pwd)"
OVERLAY="$ROOT/product.z.json"
VENDOR="${Z_VSCODE_VENDOR:-$ROOT/vendor/vscode}"
TARGET="$VENDOR/product.json"
CHECK=0

if [[ "${1:-}" == "--check" ]]; then
  CHECK=1
fi

if [[ ! -f "$OVERLAY" ]]; then
  echo "Missing overlay: $OVERLAY" >&2
  exit 1
fi

if [[ ! -d "$VENDOR" ]]; then
  echo "Missing vendor vscode at $VENDOR" >&2
  echo "Clone: gh repo clone Nate-git05/Seam apps/z-desktop/vendor/vscode -- --depth 1" >&2
  exit 1
fi

if [[ ! -f "$TARGET" ]]; then
  echo "Missing $TARGET" >&2
  exit 1
fi

python3 - <<'PY' "$OVERLAY" "$TARGET" "$CHECK"
import json, sys
from pathlib import Path

overlay_path, target_path, check = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "1"
overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
base = json.loads(target_path.read_text(encoding="utf-8"))

def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if k.startswith("$"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out

merged = deep_merge(base, overlay)
# Brand sanity
assert merged.get("nameShort") == "Z", merged.get("nameShort")
assert merged.get("applicationName") == "z-editor", merged.get("applicationName")
assert merged.get("urlProtocol") == "z-editor", merged.get("urlProtocol")

if check:
    # Verify key fields already applied
    for key in ("nameShort", "nameLong", "applicationName", "urlProtocol"):
        if base.get(key) != overlay.get(key):
            print(f"CHECK FAIL: {key} is {base.get(key)!r}, want {overlay.get(key)!r}")
            sys.exit(2)
    print("CHECK OK: product.json already branded as Z Editor")
    sys.exit(0)

target_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
print(f"Applied {overlay_path} → {target_path}")
print(f"  nameLong={merged.get('nameLong')} urlProtocol={merged.get('urlProtocol')}")
PY
