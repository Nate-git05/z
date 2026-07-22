#!/usr/bin/env python3
"""Generate Z Editor PNG / ICO / ICNS assets from brand/z-mark.svg (or drawn mark)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand"
OUT_PNG = BRAND / "png"
SIZES = (16, 32, 48, 64, 128, 256, 512)


def draw_mark(size: int) -> Image.Image:
    """Burnt-orange Z on near-black — matches Z Terminal palette."""
    img = Image.new("RGBA", (size, size), (10, 10, 10, 255))
    draw = ImageDraw.Draw(img)
    m = max(2, size // 10)
    # Z as two horizontals + diagonal
    orange = (201, 106, 43, 255)
    top = [(m, m), (size - m, m), (size - m, m + m), (m + m * 2, m + m), (m, m + m)]
    # Simpler thick Z polyline
    stroke = max(2, size // 9)
    # top bar
    draw.rectangle([m, m, size - m, m + stroke], fill=orange)
    # bottom bar
    draw.rectangle([m, size - m - stroke, size - m, size - m], fill=orange)
    # diagonal
    draw.line(
        [(size - m - stroke // 2, m + stroke), (m + stroke // 2, size - m - stroke)],
        fill=orange,
        width=stroke,
    )
    return img


def write_icns(path: Path, images: dict[int, Image.Image]) -> None:
    """Minimal ICNS writer embedding PNG payloads."""
    # type -> size
    mapping = [
        (b"icp4", 16),
        (b"icp5", 32),
        (b"icp6", 64),
        (b"ic07", 128),
        (b"ic08", 256),
        (b"ic09", 512),
    ]
    chunks: list[bytes] = []
    for tag, sz in mapping:
        im = images.get(sz)
        if im is None:
            continue
        from io import BytesIO

        buf = BytesIO()
        im.save(buf, format="PNG")
        data = buf.getvalue()
        chunks.append(tag + struct.pack(">I", 8 + len(data)) + data)
    body = b"".join(chunks)
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    OUT_PNG.mkdir(parents=True, exist_ok=True)
    images: dict[int, Image.Image] = {}
    for sz in SIZES:
        im = draw_mark(sz)
        images[sz] = im
        out = OUT_PNG / f"z-editor-{sz}.png"
        im.save(out, format="PNG")
        print(f"wrote {out}")

    # Primary linux icon
    images[512].save(BRAND / "z-editor.png", format="PNG")
    print(f"wrote {BRAND / 'z-editor.png'}")

    # Windows ICO
    ico_path = BRAND / "z-editor.ico"
    images[256].save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"wrote {ico_path}")

    # macOS ICNS
    icns_path = BRAND / "z-editor.icns"
    write_icns(icns_path, images)
    print(f"wrote {icns_path}")


if __name__ == "__main__":
    main()
