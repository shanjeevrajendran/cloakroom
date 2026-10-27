"""Renders assets/demo-clip.gif: a short loop of Claude Code reading .env through Cloakroom.

The placeholder on screen is not typed in by hand: Cloakroom masks the same fake .env that demo/record.sh writes
(rules mode, a throwaway CLOAKROOM_HOME) and the clip shows exactly what it printed. Uses SF Pro and Menlo on a
Mac; elsewhere Inter and DejaVu Sans Mono stand in.

    uv run --with pillow python demo/render_clip.py
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "demo-clip.gif"
W, H = 1200, 600
S = 2  # draw at 2x, downsample for crisp text

TITLEBAR, BODY, EDGE = "#2c2c2e", "#1c1c1e", "#3a3a3c"  # macOS dark window
TEXT, DIM, FAINT = "#f5f5f7", "#98989d", "#636366"
CLAUDE = "#d97757"  # Claude Code's orange
PH_BG, PH_FG = "#3d2f00", "#f2cc60"  # placeholder chip (what the model sees), as in demo.gif
REAL_FG, CARD = "#ff7b72", "#242426"  # the real value, on disk only
LIGHTS = [("#ff5f57", "#e0443e"), ("#febc2e", "#dea123"), ("#28c840", "#1aab29")]

# the fake .env that demo/record.sh writes, read from there so the fake key lives in one file (the one secret
# scanning already skips)
ENV = re.search(r"printf '(STRIPE_SECRET_KEY=[^']*)' > \"\$OUT/proj/\.env\"", (ROOT / "demo" / "record.sh").read_text()
                ).group(1).replace("\\n", "\n")
PROMPT = "What's in .env?"
LABELS = ("ON YOUR DISK", "WHAT CLAUDE SEES")  # the card's label before and after the swap
HEADLINE = ("Claude never sees ", "your key.")  # second part in the placeholder yellow
SUBLINE = "Cloakroom swaps it for a placeholder before it leaves your machine."
FPS = 25
PH = re.compile(r"PII_[A-Z]+_\d+(?:_[a-z2-7]{6})?")
MASCOT = [" ▐▛███▜▌", "▝▜█████▛▘", "  ▘▘ ▝▝"]  # Claude Code's start-up banner


def font(options, size):
    for path, index, variation in options:
        if pathlib.Path(path).exists():
            f = ImageFont.truetype(path, size, index=index)
            if variation:
                try:
                    f.set_variation_by_name(variation)
                except (OSError, ValueError):
                    pass
            return f
    sys.exit(f"no font found: {options}")


def sans(weight, size):
    inter = {"Regular": "Regular", "Medium": "Medium", "Semibold": "SemiBold", "Bold": "Bold"}[weight]
    return font([("/System/Library/Fonts/SFNS.ttf", 0, weight),
                 (f"/usr/share/fonts/opentype/inter/Inter-{inter}.otf", 0, None),
                 ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0, None)], size * S)


def mono(bold, size):
    return font([("/System/Library/Fonts/Menlo.ttc", 1 if bold else 0, None),
                 (f"/usr/share/fonts/truetype/dejavu/DejaVuSansMono{'-Bold' if bold else ''}.ttf", 0, None)], size * S)


f_mono, f_mono_b = mono(False, 21), mono(True, 21)
f_title = sans("Medium", 14)
f_label = sans("Medium", 14)
f_head = sans("Semibold", 40)
f_sub = sans("Regular", 19)
LH = 32 * S


def masked_env():
    """What Claude's Read gets back: Cloakroom's own output for the fake .env."""
    with tempfile.TemporaryDirectory() as tmp:
        env_file = pathlib.Path(tmp) / ".env"
        env_file.write_text(ENV)
        exe = ROOT / ".venv" / "bin" / "cloakroom"
        out = subprocess.run([str(exe if exe.exists() else "cloakroom"), "mask", "--mode", "rules", str(env_file)],
                             capture_output=True, text=True, check=True,
                             env={**os.environ, "CLOAKROOM_HOME": str(pathlib.Path(tmp) / "home")}).stdout
    if "sk_live" in out or not PH.search(out):
        sys.exit(f"unexpected mask output: {out!r}")
    return out.rstrip("\n").split("\n")


# ---------- the still parts: wallpaper, shadow, window ----------
WIN = (110 * S, 44 * S, (W - 110) * S, 428 * S)
R = 12 * S


def backdrop():
    """A soft macOS-style wallpaper with the window's shadow on it."""
    small = Image.new("RGB", (3, 3))
    for (x, y), c in {(0, 0): (58, 46, 120), (1, 0): (44, 52, 128), (2, 0): (30, 64, 120),
                      (0, 1): (70, 44, 110), (1, 1): (40, 46, 104), (2, 1): (24, 58, 100),
                      (0, 2): (84, 42, 96), (1, 2): (44, 40, 86), (2, 2): (20, 44, 78)}.items():
        small.putpixel((x, y), c)
    im = small.resize((W * S, H * S), Image.BICUBIC).filter(ImageFilter.GaussianBlur(40 * S))
    shadow = Image.new("L", im.size, 0)
    x0, y0, x1, y1 = WIN
    ImageDraw.Draw(shadow).rounded_rectangle([x0, y0 + 18 * S, x1, y1 + 18 * S], R, fill=150)
    im.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(28 * S)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(WIN, R, fill=BODY, outline=EDGE, width=1 * S)
    d.rounded_rectangle([x0, y0, x1, y0 + 36 * S], R, fill=TITLEBAR)
    d.rectangle([x0, y0 + 24 * S, x1, y0 + 36 * S], fill=TITLEBAR)
    d.line([x0, y0 + 36 * S, x1, y0 + 36 * S], fill="#000000", width=1 * S)
    d.rounded_rectangle(WIN, R, outline=EDGE, width=1 * S)
    for i, (fill, ring) in enumerate(LIGHTS):
        cx, cy = x0 + (20 + i * 20) * S, y0 + 18 * S
        d.ellipse([cx - 6 * S, cy - 6 * S, cx + 6 * S, cy + 6 * S], fill=fill, outline=ring, width=1 * S)
    # "my-app — claude": the dash is drawn, since a 14px em dash goes ragged once the GIF is quantized
    left, right, dash, gap = "my-app", "claude", 11 * S, 7 * S
    tw = f_title.getlength(left) + f_title.getlength(right) + dash + 2 * gap
    tx, ty = (x0 + x1) // 2 - tw / 2, y0 + 18 * S
    d.text((tx, ty), left, font=f_title, fill=DIM, anchor="lm")
    tx += f_title.getlength(left) + gap
    d.rectangle([tx, ty - 1 * S, tx + dash, ty], fill=DIM)
    d.text((tx + dash + gap, ty), right, font=f_title, fill=DIM, anchor="lm")
    return im


BASE = None  # wallpaper, window and start-up banner: the same in every frame
X0, Y0 = WIN[0] + 28 * S, WIN[1] + 56 * S
Y_PROMPT = Y0 + 104 * S
Y_READ = Y_PROMPT + LH + 10 * S
Y_CARD = Y_READ + LH + 4 * S


def ease(t):  # cubic ease-in-out, t clamped to 0..1
    t = min(max(t, 0.0), 1.0)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def mix(a, b, t):
    a, b = ImageColor.getrgb(a), ImageColor.getrgb(b)
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def layer(im, alpha, draw):
    """Draws onto a clear layer and lays it over `im` at `alpha` (0..1)."""
    if alpha <= 0:
        return
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw(ImageDraw.Draw(lay))
    if alpha < 1:
        lay.putalpha(lay.getchannel("A").point(lambda v: round(v * alpha)))
    im.alpha_composite(lay)


def backdrop_with_banner():
    im = backdrop().convert("RGBA")
    d = ImageDraw.Draw(im)
    for k, art in enumerate(MASCOT):
        d.text((X0, Y0 + k * 26 * S), art, font=f_mono, fill=CLAUDE)
    tx = X0 + f_mono.getlength(MASCOT[1]) + 18 * S  # two lines, centred on the three-line mascot
    d.text((tx, Y0 + 13 * S), "Claude Code", font=f_mono_b, fill=TEXT)
    d.text((tx, Y0 + 39 * S), "~/my-app", font=f_mono, fill=DIM)
    d.text((X0, Y_PROMPT), ">", font=f_mono_b, fill=DIM)
    return im


def scene(t, real, masked):
    """The frame at t seconds. `real` and `masked` are the .env lines on disk and as Cloakroom printed them."""
    im = BASE.copy()
    out = ease((t - 5.35) / 0.35)  # everything but the window fades out before the loop starts again
    typed = PROMPT[:max(0, min(len(PROMPT), int((t - 0.2) / 0.04)))]

    def prompt(d):
        d.text((X0 + 30 * S, Y_PROMPT), typed, font=f_mono, fill=TEXT)
        if t < 0.95 and int(t * 4) % 2 == 0 or 0.2 <= t < 0.8:
            cx = X0 + 30 * S + f_mono.getlength(typed) + 2 * S
            d.rectangle([cx, Y_PROMPT + 1 * S, cx + 11 * S, Y_PROMPT + 25 * S], fill=TEXT)
    layer(im, 1 - out, prompt)

    # Read(.env) and its result card slide up into place
    a = ease((t - 1.1) / 0.35) * (1 - out)
    dy = round((1 - ease((t - 1.1) / 0.35)) * 14 * S)
    m = ease((t - 2.05) / 0.45)  # 0: the file on disk, 1: what Claude got back; the chip grows across it
    gone = ease((t - 2.05) / 0.35)  # the real value lifts away, and before it is quite gone
    came = ease((t - 2.25) / 0.35)  # the placeholder rises into its place

    def read(d):
        d.ellipse([X0 + 4 * S, Y_READ + 8 * S + dy, X0 + 14 * S, Y_READ + 18 * S + dy], fill="#4ec27a")
        d.text((X0 + 30 * S, Y_READ + dy), "Read", font=f_mono_b, fill=TEXT)
        d.text((X0 + 30 * S + f_mono_b.getlength("Read"), Y_READ + dy), "(.env)", font=f_mono, fill=TEXT)
        bx0, bx1, y = X0 + 30 * S, WIN[2] - 28 * S, Y_CARD + dy
        d.rounded_rectangle([bx0, y, bx1, y + 112 * S], 10 * S, fill=CARD, outline=EDGE, width=1 * S)
        ly = y + 40 * S
        for r, k in zip(real, masked):
            ph = PH.search(k)
            x = bx0 + 16 * S
            if not ph:
                d.text((x, ly), k, font=f_mono, fill=TEXT)
            else:
                d.text((x, ly), k[:ph.start()], font=f_mono, fill=TEXT)
                x += f_mono.getlength(k[:ph.start()])
                shown = re.sub(r"(sk_live_\w{6})\w+", r"\1…", r[ph.start():])  # keep the fake key short
                w = f_mono_b.getlength(shown) + (f_mono_b.getlength(ph.group()) - f_mono_b.getlength(shown)) * m
                d.rounded_rectangle([x - 4 * S, ly - 3 * S, x + w + 4 * S, ly + 27 * S], 6 * S,
                                    fill=mix(CARD, PH_BG, m))
            ly += LH
    layer(im, a, read)

    # the real value lifts away while the placeholder rises into its place
    for r, k in zip(real, masked):
        ph = PH.search(k)
        if not ph:
            continue
        line_y = Y_CARD + dy + 40 * S + LH * masked.index(k)
        x = X0 + 30 * S + 16 * S + f_mono.getlength(k[:ph.start()])
        shown = re.sub(r"(sk_live_\w{6})\w+", r"\1…", r[ph.start():])
        layer(im, a * (1 - gone), lambda d: d.text((x, line_y - round(8 * S * gone)), shown, font=f_mono_b,
                                                    fill=REAL_FG))
        layer(im, a * came, lambda d: d.text((x, line_y + round(8 * S * (1 - came))), ph.group(), font=f_mono_b,
                                              fill=PH_FG))

    # the card's label moves with it
    def label(text, shift):
        return lambda d: d.text((X0 + 46 * S, Y_CARD + dy + 12 * S + shift), text, font=f_label, fill=DIM)
    layer(im, a * (1 - gone), label(LABELS[0], -round(5 * S * gone)))
    layer(im, a * came, label(LABELS[1], round(5 * S * (1 - came))))

    # headline, then the line under it, each rising a little as it fades in
    h = ease((t - 2.85) / 0.5)
    sub = ease((t - 3.15) / 0.5)

    def headline(d):
        y = (H - 106) * S + round((1 - h) * 16 * S)
        w0, w1 = f_head.getlength(HEADLINE[0]), f_head.getlength(HEADLINE[1])
        x = W * S // 2 - (w0 + w1) / 2
        d.text((x, y), HEADLINE[0], font=f_head, fill="#ffffff", anchor="lm")
        d.text((x + w0, y), HEADLINE[1], font=f_head, fill=PH_FG, anchor="lm")

    def subline(d):
        y = (H - 58) * S + round((1 - sub) * 12 * S)
        d.text((W * S // 2, y), SUBLINE, font=f_sub, fill="#d1d1d6", anchor="mm")
    layer(im, h * (1 - out), headline)
    layer(im, sub * (1 - out), subline)
    return im.convert("RGB").resize((W, H), Image.LANCZOS)


def main():
    global BASE
    masked = masked_env()
    real = ENV.rstrip("\n").split("\n")
    BASE = backdrop_with_banner()
    length = 5.9
    frames = [scene(i / FPS, real, masked) for i in range(round(length * FPS))]

    OUT.parent.mkdir(exist_ok=True)
    # one palette for every frame: the wallpaper's shades, the busiest frames' colors and the exact accents.
    # The wallpaper is dithered once (no banding) and reused wherever a frame leaves it untouched, so frames
    # only differ where something actually moves.
    still = BASE.convert("RGB").resize((W, H), Image.LANCZOS)
    picks = [1.8, 2.2, 2.3, 2.4, 3.05, 4.5]  # the real key, three swap frames, the caption fading in, the full caption
    sheet = Image.new("RGB", (W, H * len(picks)))
    for k, sec in enumerate(picks):
        sheet.paste(frames[round(sec * FPS)], (0, H * k))
    accents = [c for pair in LIGHTS for c in pair] + [PH_FG, PH_BG, REAL_FG, CLAUDE, "#4ec27a", TEXT, "#ffffff",
                                                      BODY, CARD, "#d1d1d6"]
    colors = (still.quantize(colors=112, method=Image.Quantize.MEDIANCUT).getpalette()[:112 * 3]
              + sheet.quantize(colors=120, method=Image.Quantize.MEDIANCUT).getpalette()[:120 * 3]
              + [v for c in accents for v in ImageColor.getrgb(c)])
    ref = Image.new("P", (1, 1))
    ref.putpalette(colors + [0] * (768 - len(colors)))
    still_p = still.quantize(palette=ref, dither=Image.Dither.FLOYDSTEINBERG)
    pal = []
    for im in frames:
        same = ImageChops.difference(im, still).convert("L").point(lambda v: 255 if v == 0 else 0)
        pal.append(Image.composite(still_p, im.quantize(palette=ref, dither=Image.Dither.NONE), same))
    merged = []  # identical frames in a row become one longer frame
    for p in pal:
        if merged and merged[-1][0].tobytes() == p.tobytes():
            merged[-1][1] += 1000 // FPS
        else:
            merged.append([p, 1000 // FPS])
    merged[0][0].save(OUT, save_all=True, append_images=[p for p, _ in merged[1:]],
                      duration=[ms for _, ms in merged], loop=0, optimize=True, disposal=1)
    print(f"{len(merged)} frames, {sum(ms for _, ms in merged) / 1000:.1f} s, {OUT.stat().st_size / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
