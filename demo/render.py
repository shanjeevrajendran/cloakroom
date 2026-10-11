"""Turns the real run recorded by demo/record.sh into assets/demo.gif and assets/social.png.

Every line shown comes from that run: the prompt, what the model's tools returned (from the session transcript),
what Claude wrote, and the files on disk. Real values are found by diffing what the model saw against the
original files, so nothing is typed in by hand.

    uv run --with pillow python demo/render.py
"""
import json
import pathlib
import re

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = ROOT / "demo" / "run"
OUT = ROOT / "assets"
W, H = 1200, 675
S = 2  # draw at 2x, downsample for crisp text

BG, PANEL, BORDER = "#0d1117", "#161b22", "#30363d"
TEXT, DIM, CLAUDE = "#e6edf3", "#8b949e", "#d2a8ff"
PH_BG, PH_FG = "#3d2f00", "#f2cc60"         # placeholder chip (what the model sees)
REAL_FG = "#ff7b72"                          # real value (never reaches the model)
OK_BG, OK_FG = "#0f2e1a", "#56d364"          # restored on disk

MONO = "/System/Library/Fonts/Menlo.ttc"
SANS = "/System/Library/Fonts/SFNS.ttf"
f_mono = ImageFont.truetype(MONO, 16 * S)
f_mono_b = ImageFont.truetype(MONO, 16 * S, index=1)
f_small = ImageFont.truetype(SANS, 15 * S)
f_label = ImageFont.truetype(SANS, 18 * S)
f_title = ImageFont.truetype(SANS, 25 * S)
f_title.set_variation_by_name("Bold")
f_big = ImageFont.truetype(SANS, 46 * S)
f_mid = ImageFont.truetype(SANS, 22 * S)
CW = f_mono.getlength("M")
LH = 25 * S
PH = re.compile(r"PII_[A-Z]+_\d+")


# ---------- data from the run ----------
def load():
    prompt = (RUN / "prompt.txt").read_text().strip()
    steps = []
    uses = {}
    for line in open(RUN / "transcript.jsonl"):
        msg = json.loads(line).get("message") or {}
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                uses[b["id"]] = (b["name"], b["input"])
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                name, inp = uses[b["tool_use_id"]]
                c = b.get("content")
                c = c if isinstance(c, str) else "".join(x.get("text", "") for x in c if isinstance(x, dict))
                steps.append((name, inp, c))
    answer = json.loads((RUN / "run.json").read_text())["result"]
    return prompt, steps, answer


def strip_numbers(s):
    return [re.sub(r"^\s*\d+\t", "", ln) for ln in s.rstrip("\n").split("\n") if ln.strip() and not re.match(r"^\s*\d+\t?$", ln)]


def real_spans(masked, original):
    """Spans in `original` that the model saw as placeholders: the masked line with each placeholder as a
    wildcard, matched against the original line."""
    parts = PH.split(masked)
    if len(parts) == 1:
        return []
    m = re.fullmatch("(.+?)".join(re.escape(p) for p in parts), original)
    return [m.span(i) for i in range(1, len(parts))] if m else []


def short_key(s):  # keep the fake key short on screen
    return re.sub(r"(sk_live_\w{6})\w+", r"\1…", s)


# ---------- drawing ----------
def segs_placeholders(line):
    out, i = [], 0
    for m in PH.finditer(line):
        out.append((line[i:m.start()], None))
        out.append((m.group(), "ph"))
        i = m.end()
    out.append((line[i:], None))
    return out


def segs_real(line, spans, style="real"):
    out, i = [], 0
    for a, b in spans:
        out.append((line[i:a], None))
        out.append((line[a:b], style))
        i = b
    out.append((line[i:], None))
    return [(t, s) for t, s in out if t]


def wrap_segs(segs, width_chars):
    lines, cur, n = [], [], 0
    for text, style in segs:
        parts = [text] if style else re.split(r"(\s+)", text)
        for p in parts:
            if not p:
                continue
            if n + len(p) > width_chars and n > 0:
                lines.append(cur)
                cur, n = [], 0
                if not style and p.isspace():
                    continue
            cur.append((p, style))
            n += len(p)
    if cur:
        lines.append(cur)
    return lines


def draw_segs(d, x, y, segs):
    for text, style in segs:
        w = f_mono.getlength(text)
        if style == "ph":
            d.rounded_rectangle([x - 2 * S, y - 1 * S, x + w + 2 * S, y + 20 * S], 4 * S, fill=PH_BG)
            d.text((x, y), text, font=f_mono_b, fill=PH_FG)
        elif style == "real":
            d.text((x, y), text, font=f_mono_b, fill=REAL_FG)
        elif style == "ok":
            d.rounded_rectangle([x - 2 * S, y - 1 * S, x + w + 2 * S, y + 20 * S], 4 * S, fill=OK_BG)
            d.text((x, y), text, font=f_mono_b, fill=OK_FG)
        else:
            d.text((x, y), text, font=f_mono, fill=TEXT)
        x += w


def frame(prompt_shown, left, right, footer=None, caret=False):
    im = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(im)
    # window bar
    for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        d.ellipse([(18 + i * 20) * S, 16 * S, (30 + i * 20) * S, 28 * S], fill=c)
    d.text((W * S // 2, 22 * S), "Claude Code  +  Cloakroom", font=f_small, fill=DIM, anchor="mm")
    # prompt
    d.text((28 * S, 52 * S), ">", font=f_mono_b, fill=CLAUDE)
    py, last = 52 * S, 0
    for ln in wrap_segs([(prompt_shown, None)], int((W - 80) * S / CW)) or [[]]:
        draw_segs(d, 48 * S, py, ln)
        last = sum(f_mono.getlength(t) for t, _ in ln)
        py += LH
    if caret:
        d.rectangle([48 * S + last + 2 * S, py - LH, 48 * S + last + 10 * S, py - LH + 17 * S], fill=DIM)
    # panels
    top, bottom = 140 * S, (H - 74) * S
    pw = (W - 28 * 2 - 20) * S // 2
    for k, (title, sub, color, rows) in enumerate([
            ("What Claude sees", "sent to the model", CLAUDE, left),
            ("On your disk", "never leaves your machine", OK_FG, right)]):
        x0 = 28 * S + k * (pw + 20 * S)
        d.rounded_rectangle([x0, top, x0 + pw, bottom], 10 * S, fill=PANEL, outline=BORDER, width=1 * S)
        d.text((x0 + 18 * S, top + 12 * S), title, font=f_title, fill=color)
        d.text((x0 + 18 * S + f_title.getlength(title) + 12 * S, top + 21 * S), sub, font=f_small, fill=DIM)
        y = top + 54 * S
        for row in rows:
            if row[0] == "head":
                d.text((x0 + 18 * S, y), row[1], font=f_small, fill=DIM)
                y += LH
                continue
            for ln in wrap_segs(row[1], int((pw - 36 * S) / CW)):
                draw_segs(d, x0 + 18 * S, y, ln)
                y += LH
            y += 4 * S
    if footer:
        d.text((28 * S, (H - 57) * S), "Claude:", font=f_label, fill=CLAUDE)
        for k, ln in enumerate(wrap_segs(footer, int((W - 130) * S / CW))[:2]):
            draw_segs(d, 100 * S, (H - 56 + k * 25) * S, ln)
    return im.resize((W, H), Image.LANCZOS)


def end_card():
    im = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(im)
    d.text((W * S // 2, 250 * S), "Cloakroom", font=f_big, fill=TEXT, anchor="mm")
    d.text((W * S // 2, 318 * S), "A privacy layer for Claude Code", font=f_mid, fill=TEXT, anchor="mm")
    d.text((W * S // 2, 356 * S), "Names, emails, addresses and API keys are swapped out before the model sees them.", font=f_small, fill=DIM, anchor="mm")
    cmd = 'uv tool install "cloakroom[model,torch]"'
    cw = f_mono.getlength(cmd)
    d.rounded_rectangle([W * S // 2 - cw // 2 - 16 * S, 410 * S, W * S // 2 + cw // 2 + 16 * S, 446 * S], 8 * S, fill=PANEL, outline=BORDER, width=1 * S)
    d.text((W * S // 2, 428 * S), cmd, font=f_mono, fill=OK_FG, anchor="mm")
    d.text((W * S // 2, 490 * S), "Local  ·  open source  ·  macOS, Windows, Linux  ·  no GPU needed", font=f_small, fill=DIM, anchor="mm")
    return im.resize((W, H), Image.LANCZOS)


# ---------- the story ----------
def main():
    prompt, steps, answer = load()
    orig = RUN / "original"
    left, right, frames = [], [], []

    def add(img, ms):
        frames.append((img, ms))

    # 1. prompt typed out
    for i in range(0, len(prompt) + 1, 5):
        add(frame(prompt[:i], [], [], caret=True), 35)
    add(frame(prompt, [], []), 700)

    # 2. each tool step: what the model got vs the file on disk
    for name, inp, result in steps:
        path = pathlib.Path(inp.get("file_path", ""))
        rel = path.name if path.parent.name == "proj" else f"{path.parent.name}/{path.name}"
        if name == "Read":
            seen = strip_numbers(result)
            real = (orig / rel).read_text().rstrip("\n").split("\n")
            left.append(("head", f"● Read {rel}"))
            right.append(("head", f"  {rel}"))
            for s, r in zip(seen, real):
                left.append(("line", segs_placeholders(short_key(s))))
                right.append(("line", segs_real(short_key(r), [(a, b) for a, b in real_spans(short_key(s), short_key(r))])))
                add(frame(prompt, left, right), 260)
            add(frame(prompt, left, right), 900)
        elif name == "Write":
            wrote = inp["content"].rstrip("\n").split("\n")[0]
            disk = (RUN / "proj" / rel).read_text().split("\n")[0]
            left.append(("head", f"● Write {rel}"))
            left.append(("line", segs_placeholders(wrote)))
            add(frame(prompt, left, right), 900)
            right.append(("head", f"  {rel}  (restored locally)"))
            right.append(("line", segs_real(disk, real_spans(wrote, disk), "ok")))
            add(frame(prompt, left, right), 1400)

    # 3. Claude's own words: it never saw the key
    said = next((ln for ln in answer.split("\n") if "placeholder" in ln), answer.split("\n")[0])
    said = re.sub(r"`", "", said)
    final = frame(prompt, left, right, footer=[(said, None)])
    add(final, 2600)
    add(end_card(), 3200)

    OUT.mkdir(exist_ok=True)
    pal = [im.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for im, _ in frames]
    pal[0].save(OUT / "demo.gif", save_all=True, append_images=pal[1:], duration=[ms for _, ms in frames],
                loop=0, optimize=True, disposal=1)
    final.resize((1280, 720), Image.LANCZOS).crop((0, 40, 1280, 680)).save(OUT / "social.png", optimize=True)
    final.save(OUT / "demo-still.png", optimize=True)
    print(f"{len(frames)} frames, {sum(ms for _, ms in frames) / 1000:.1f} s, "
          f"{(OUT / 'demo.gif').stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
