"""Generate the arc-reactor .ico for the JARVIS shortcuts.

Run with an ephemeral Pillow (no permanent dependency added):
    uv run --with pillow python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "jarvis.ico"

CYAN = (70, 214, 255, 255)
CYAN_SOFT = (127, 227, 255, 255)
CORE = (205, 251, 255, 255)
S = 256


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (4, 7, 13, 0))
    d = ImageDraw.Draw(img)

    # soft outer glow
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([24, 24, 232, 232], outline=CYAN, width=22)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    img.alpha_composite(glow)

    # crisp rings
    d.ellipse([28, 28, 228, 228], outline=CYAN, width=14)
    d.ellipse([74, 74, 182, 182], outline=CYAN_SOFT, width=8)

    # radiating spokes
    import math

    cx = cy = S // 2
    for k in range(8):
        ang = math.radians(k * 45)
        x1, y1 = cx + 54 * math.cos(ang), cy + 54 * math.sin(ang)
        x2, y2 = cx + 92 * math.cos(ang), cy + 92 * math.sin(ang)
        d.line([x1, y1, x2, y2], fill=CYAN_SOFT, width=6)

    # bright core
    d.ellipse([100, 100, 156, 156], fill=CORE)
    return img


def main() -> None:
    img = draw()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
