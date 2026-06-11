"""Generate the PNG app icons from a simple vector-ish drawing with Pillow.

Run once: ``python scripts/make_icons.py``. Produces icon-192/512/180 in web/icons.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

BG = (11, 15, 23)
BLUE = (79, 140, 255)
GREEN = (61, 220, 151)


def rounded(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), BG)
    d = ImageDraw.Draw(img)
    # App background tile.
    rounded(d, (0, 0, s, s), int(s * 0.22), BG)
    # Phone body.
    pw, ph = int(s * 0.41), int(s * 0.62)
    px, py = (s - pw) // 2, int(s * 0.19)
    rounded(d, (px, py, px + pw, py + ph), int(s * 0.066), BLUE)
    # Screen.
    m = int(s * 0.055)
    sx0, sy0 = px + m, py + int(s * 0.086)
    sx1, sy1 = px + pw - m, py + ph - int(s * 0.15)
    rounded(d, (sx0, sy0, sx1, sy1), int(s * 0.023), BG)
    # Chat lines.
    lx = sx0 + int(s * 0.04)
    lh = int(s * 0.027)
    for i, w in enumerate((0.16, 0.22, 0.12)):
        ly = sy0 + int(s * 0.06) + i * int(s * 0.063)
        rounded(d, (lx, ly, lx + int(s * w), ly + lh), lh // 2, BLUE)
    # Status dot (green = online).
    r = int(s * 0.02)
    cx, cy = sx1 - int(s * 0.05), sy0 + int(s * 0.2)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GREEN)
    # Home dot.
    hr = int(s * 0.03)
    hcx, hcy = s // 2, py + ph - int(s * 0.075)
    d.ellipse((hcx - hr, hcy - hr, hcx + hr, hcy + hr), fill=BG)
    return img


for size, name in [(192, "icon-192.png"), (512, "icon-512.png"), (180, "icon-180.png")]:
    render(size).save(OUT / name)
    print("wrote", name)
