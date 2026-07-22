#!/usr/bin/env bash
# Merge apps/z-desktop/product.z.json into vendor/vscode/product.json (+ icons).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="$ROOT/product.z.json"
VENDOR="${Z_VSCODE_VENDOR:-$ROOT/vendor/vscode}"
TARGET="$VENDOR/product.json"
BRAND="$ROOT/brand"
CHECK=0
REQUIRE_ICONS=0

for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --require-icons) REQUIRE_ICONS=1 ;;
  esac
done

if [[ ! -f "$OVERLAY" ]]; then
  echo "Missing overlay: $OVERLAY" >&2
  exit 1
fi

if [[ ! -d "$VENDOR" ]]; then
  echo "Missing vendor vscode at $VENDOR" >&2
  echo "Clone (pinned SHA in apps/z-desktop/README.md):" >&2
  echo "  gh repo clone Nate-git05/Seam apps/z-desktop/vendor/vscode -- --depth 1" >&2
  exit 1
fi

if [[ ! -f "$TARGET" ]]; then
  echo "Missing $TARGET" >&2
  exit 1
fi

python3 - <<'PY' "$OVERLAY" "$TARGET" "$CHECK" "$REQUIRE_ICONS" "$BRAND" "$VENDOR"
import json, shutil, sys
from pathlib import Path

overlay_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
check = sys.argv[3] == "1"
require_icons = sys.argv[4] == "1"
brand = Path(sys.argv[5])
vendor = Path(sys.argv[6])

overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
base = json.loads(target_path.read_text(encoding="utf-8"))

omit = set(overlay.get("$omitKeys") or [])
preserve_builtins = bool(overlay.get("$preserveBuiltInExtensions", True))

def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if k.startswith("$"):
            continue
        if k == "builtInExtensions" and preserve_builtins:
            # Keep upstream built-ins; Z is injected via inject-builtin-extension.sh
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    for k in omit:
        out.pop(k, None)
    return out

merged = deep_merge(base, overlay)
assert merged.get("nameShort") == "Z", merged.get("nameShort")
assert merged.get("applicationName") == "z-editor", merged.get("applicationName")
assert merged.get("urlProtocol") == "z-editor", merged.get("urlProtocol")

icon_png = brand / "z-editor.png"
icon_ico = brand / "z-editor.ico"
icon_icns = brand / "z-editor.icns"
if require_icons:
    for p in (icon_png, icon_ico, icon_icns):
        if not p.is_file():
            print(f"CHECK FAIL: missing icon {p}")
            print("Run: python3 apps/z-desktop/scripts/generate-icons.py")
            sys.exit(2)

if check:
    for key in ("nameShort", "nameLong", "applicationName", "urlProtocol"):
        if base.get(key) != overlay.get(key):
            print(f"CHECK FAIL: {key} is {base.get(key)!r}, want {overlay.get(key)!r}")
            sys.exit(2)
    if "defaultChatAgent" in omit and "defaultChatAgent" in base:
        print("CHECK FAIL: defaultChatAgent still present (run apply without --check first)")
        sys.exit(2)
    print("CHECK OK: product.json already branded as Z Editor")
    sys.exit(0)

target_path.write_text(json.dumps(merged, indent="\t") + "\n", encoding="utf-8")
print(f"Applied {overlay_path} → {target_path}")
print(f"  nameLong={merged.get('nameLong')} urlProtocol={merged.get('urlProtocol')}")

# Copy icons into vendor resources (best-effort)
copies = []
if icon_png.is_file():
    for rel in (
        "resources/linux/code.png",
        "resources/linux/rpm/code.xpm",  # skip if missing parent
    ):
        dest = vendor / "resources/linux/code.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_png, dest)
        copies.append(str(dest))
        break
if icon_icns.is_file():
    dest = vendor / "resources/darwin/code.icns"
    if dest.parent.is_dir():
        shutil.copy2(icon_icns, dest)
        copies.append(str(dest))
if icon_ico.is_file():
    dest = vendor / "resources/win32/code.ico"
    if dest.parent.is_dir():
        shutil.copy2(icon_ico, dest)
        copies.append(str(dest))
# Extra linux sizes if present
png_dir = brand / "png"
linux_icons = vendor / "resources/linux"
if png_dir.is_dir() and linux_icons.is_dir():
    for sz in (16, 32, 48, 64, 128, 256, 512):
        src = png_dir / f"z-editor-{sz}.png"
        if src.is_file():
            # VS Code uses code.png primarily; also drop named copies for packaging scripts
            shutil.copy2(src, linux_icons / f"z-editor-{sz}.png")

if copies:
    print("Copied icons:")
    for c in copies:
        print(f"  {c}")
else:
    print("NOTE: no brand icons copied (run generate-icons.py)")
PY
