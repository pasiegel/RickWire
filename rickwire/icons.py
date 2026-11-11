"""Draws the RickWire portal icon (no external art) for the tray, PWA and file associations."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from .config import WEB_DIR

ICON_DIR = WEB_DIR / "icons"


def portal(size: int, background: bool = False, pad: float = 0.06) -> Image.Image:
    s = size * 4
    img = Image.new("RGBA", (s, s), (11, 15, 26, 255) if background else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    r_max = s * (0.5 - pad)
    # glow
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([c - r_max, c - r_max, c + r_max, c + r_max], fill=(151, 206, 76, 170))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(s * 0.04)))
    d.ellipse([c - r_max * 0.94, c - r_max * 0.94, c + r_max * 0.94, c + r_max * 0.94], fill=(46, 125, 50, 255))
    # swirl arms
    greens = [(178, 247, 70), (151, 206, 76), (98, 175, 60), (214, 255, 130)]
    for arm in range(5):
        pts = []
        for t in range(0, 240):
            a = arm * 2 * math.pi / 5 + t * 0.045
            rr = r_max * 0.92 * (1 - t / 260)
            pts.append((c + rr * math.cos(a), c + rr * math.sin(a)))
        d.line(pts, fill=greens[arm % len(greens)] + (255,), width=max(2, int(s * 0.035)), joint="curve")
    d.ellipse([c - r_max * 0.16, c - r_max * 0.16, c + r_max * 0.16, c + r_max * 0.16], fill=(230, 255, 180, 255))
    return img.resize((size, size), Image.LANCZOS)


def ensure_icons() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    targets = {
        "icon-192.png": lambda: portal(192),
        "icon-512.png": lambda: portal(512),
        "maskable-512.png": lambda: portal(512, background=True, pad=0.16),
        "apple-touch-icon.png": lambda: portal(180, background=True, pad=0.1),
    }
    for name, make in targets.items():
        p = ICON_DIR / name
        if not p.exists():
            make().save(p)
    ico = ICON_DIR / "rickwire.ico"
    if not ico.exists():
        portal(256).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    ensure_icons()
    print("icons written to", Path(ICON_DIR))
