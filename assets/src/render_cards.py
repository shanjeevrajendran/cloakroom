"""Render assets/hero-card.png and assets/social-card.png from assets/hero.svg.

Edit the text in assets/hero.svg, then run from the repo root:
    uv run --with pillow python assets/src/render_cards.py

Needs Google Chrome (set CHROME to its binary if it is not in /Applications).
hero-card: the SVG at 880x400, 2x -> 1760x800 (LinkedIn).
social-card: the SVG stretched to 1280x640 (same as the old card), 2x, downscaled (GitHub/og preview).
"""
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def shot(svg_text: str, w: int, h: int, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "card.svg").write_text(svg_text)
        page = Path(tmp) / "card.html"
        page.write_text(f'<html><body style="margin:0;background:transparent">'
                        f'<img src="card.svg" style="display:block;width:{w}px;height:{h}px"></body></html>')
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--default-background-color=00000000", "--force-device-scale-factor=2",
                        f"--window-size={w},{h}", f"--screenshot={out}", page.as_uri()],
                       check=True, capture_output=True)
    img = Image.open(out)
    assert img.size == (2 * w, 2 * h), img.size
    if (w, h) == (880, 400):
        return  # the hero card is the 2x render itself
    img.resize((w, h), Image.LANCZOS).save(out, optimize=True)


svg = (ASSETS / "hero.svg").read_text()
shot(svg, 880, 400, ASSETS / "hero-card.png")
shot(svg.replace("<svg ", '<svg preserveAspectRatio="none" ', 1), 1280, 640, ASSETS / "social-card.png")
print("wrote hero-card.png (1760x800) and social-card.png (1280x640)")
