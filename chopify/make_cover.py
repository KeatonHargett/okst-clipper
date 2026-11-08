"""Grid cover for an OKST Reel: the clip's caption free mid frame, darkened, with the hook in
slanted Impact caps (first line in the accent colour), kept inside the profile grid's centre crop.

Usage:
    .venv\\Scripts\\python make_cover.py "<folder>\\<base>.mp4" [--color orange|turquoise] [--text "..."]

Reads <base>.frame.jpg (written by okst_clip.py; falls back to the mp4's middle frame) and the hook from
<base>.txt (line 1, or line 2 when line 1 is a press conference "<name> spoke ..." line).
Writes <base>.cover.jpg (1080x1920). ig_publish.py uploads it as the Reel cover when it exists.
Never overwrites an existing cover.

Game template (okst-highlights postgame Reels):
    python make_cover.py "<base>.mp4" --template game --facts facts.json --text "COWBOYS HANG 66 ON KANSAS STATE"
OSU orange, OSU logo, the full 16:9 action still (<base>.frame.jpg, never cropped: network logos stay whole),
the headline in slanted Impact caps, and a black final score box. Everything sits inside the grid's 3:4 crop
(y 240 to 1680). Also writes <base>.cover.debug.jpg with that crop drawn on it.
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

PROJECT = Path(__file__).resolve().parent
FONT = PROJECT / "fonts" / "impact.ttf"
ACCENT = {"orange": (254, 92, 0), "turquoise": (0, 206, 209)}   # same as okst_clip COLORS
W, H = 1080, 1920
# Instagram's profile grid shows the middle 1080x1440 (3:4) of a 9:16 Reel; older views crop 1:1.
# The text block stays inside the 1:1 band so it survives both.
SAFE_TOP, SAFE_BOTTOM = (H - W) // 2 + 40, (H + W) // 2 - 40
TEXT_W = 940
SIZES = range(170, 89, -8)
MAX_LINES = 4
GOOD_SIZE = 120                   # fewer lines win as long as the text stays at least this big
MIN_CLEAN_SIZE = 100              # shrink to here before leaving a short word alone on a line
STROKE = 10
SLANT = 0.18                      # matches the italic captions
DARKEN = 0.62


def hook_text(txt):
    lines = [l.strip() for l in txt.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    if not lines:
        sys.exit(f"{txt.name} is empty")
    line = lines[1] if re.search(r"\bspoke\b", lines[0]) and len(lines) > 1 else lines[0]
    return re.sub(r"[.\s]+$", "", line)


def balanced(words, n, width):
    """Split words into n lines so the widest line is as narrow as possible (no orphan words)."""
    best = None

    def walk(start, left, acc):
        nonlocal best
        if left == 1:
            lines = acc + [" ".join(words[start:])]
            key = (has_orphan(lines), max(width(l) for l in lines))
            if best is None or key < best[0]:
                best = (key, lines)
            return
        for cut in range(start + 1, len(words) - left + 2):
            walk(cut, left - 1, acc + [" ".join(words[start:cut])])

    walk(0, n, [])
    return best


def has_orphan(lines):
    """A short word alone on a line (long single words like RECRUITING read fine)."""
    return len(lines) > 1 and any(len(l.split()) == 1 and len(l) < 9 for l in lines)


def fit(text, draw):
    """Fewest balanced lines that still fit at GOOD_SIZE or larger (largest size for that line count);
    otherwise the largest size that fits at all."""
    words = text.split()
    best = []                                         # (n, font, lines), largest fitting size per n
    for n in range(1, min(MAX_LINES, len(words)) + 1):
        for size in SIZES:
            font = ImageFont.truetype(str(FONT), size)
            (_, widest), lines = balanced(words, n, lambda l: draw.textlength(l, font=font))
            if widest <= TEXT_W and n * size * 1.08 <= SAFE_BOTTOM - SAFE_TOP:
                best.append((n, font, lines))
                break
    if not best:
        sys.exit(f"Hook too long for a cover: {text!r}. Pass a shorter --text.")
    clean = [b for b in best if not has_orphan(b[2])]
    pick = (next((b for b in clean if b[1].size >= GOOD_SIZE), None)
            or next((b for b in clean if b[1].size >= MIN_CLEAN_SIZE), None)
            or next((b for b in best if b[1].size >= GOOD_SIZE), None)
            or max(clean or best, key=lambda b: b[1].size))
    return pick[1], pick[2]


def base_frame(mp4):
    frame = mp4.with_name(mp4.stem + ".frame.jpg")
    if frame.exists():
        return Image.open(frame).convert("RGB")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(mp4)], capture_output=True, text=True, check=True).stdout.strip())
    tmp = Path(tempfile.mkdtemp(prefix="okst-cover-")) / "mid.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur / 2:.2f}", "-i", str(mp4), "-frames:v", "1",
                    "-q:v", "2", str(tmp)], check=True)
    print(f"WARNING: no {frame.name}; using the mp4's middle frame (captions included).")
    return Image.open(tmp).convert("RGB")


GRID = (240, 1680)                # the profile grid's 3:4 crop of a 9:16 cover
OSU_LOGO = PROJECT / "assets" / "osu_logo.png"


def game_cover(mp4, out, text, facts_path):
    """Postgame highlight cover: everything important inside GRID, the action frame shown whole."""
    import json
    f = json.loads(Path(facts_path).read_text(encoding="utf-8"))
    orange = ACCENT["orange"]
    img = Image.new("RGB", (W, H), orange)
    d = ImageDraw.Draw(img)
    for y in range(H):                                            # orange, a little deeper toward the bottom
        k = 1 - 0.28 * y / H
        d.line((0, y, W, y), fill=tuple(int(c * k) for c in orange))
    boxes = []
    logo = Image.open(OSU_LOGO).convert("RGBA")
    logo.thumbnail((300, 150), Image.LANCZOS)
    lx, ly = (W - logo.width) // 2, 262
    img.paste(logo, (lx, ly), logo)
    boxes.append((lx, ly, lx + logo.width, ly + logo.height))
    frame = base_frame(mp4)
    fw, fh = frame.size
    if fw / fh >= W / 608:                                        # 16:9 or wider: full width, never cropped
        frame = frame.resize((W, round(W * fh / fw)), Image.LANCZOS)
    else:                                                         # portrait or 4:3 still: fit inside, centered
        frame = frame.resize((round(608 * fw / fh), 608), Image.LANCZOS)
    fy = 430 + (608 - frame.height) // 2
    fx = (W - frame.width) // 2
    d.rectangle((0, 424, W, 1044), fill=(0, 0, 0))
    img.paste(frame, (fx, fy))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    top, bottom, maxw = 1070, 1440, 880
    font = lines = None
    for size in range(150, 69, -6):
        ft = ImageFont.truetype(str(FONT), size)
        for n in range(1, 4):
            words = text.split()
            if n > len(words):
                break
            (_, widest), ls = balanced(words, n, lambda l: draw.textlength(l, font=ft))
            if widest <= maxw and n * size * 1.05 <= bottom - top and not (n > 1 and has_orphan(ls) and size > 90):
                font, lines = ft, ls
                break
        if font:
            break
    if not font:
        sys.exit(f"Headline too long for a game cover: {text!r}")
    lh = font.size * 1.05
    y = top + (bottom - top - lh * len(lines)) / 2
    cy = y + lh * len(lines) / 2
    for line in lines:
        draw.text(((W - draw.textlength(line, font=font)) / 2, y), line, font=font, fill="white",
                  stroke_width=8, stroke_fill="black")
        y += lh
    layer = layer.transform((W, H), Image.AFFINE, (1, SLANT, -SLANT * cy, 0, 1, 0), Image.BICUBIC)
    img.paste(layer, (0, 0), layer)
    bbox = layer.getbbox()
    boxes.append(bbox)
    osu, opp = f["osu"], f["opp"]
    status = "FINAL" if f.get("final", True) else f.get("status", "").upper()
    score = f"OSU {osu['score']}  ·  {opp['abbr']} {opp['score']}  ·  {status}"
    sf = ImageFont.truetype(str(FONT), 76)
    sw = draw.textlength(score, font=sf)
    bw, bh = int(sw) + 90, 150
    bx, by = (W - bw) // 2, 1490
    box = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle((0, 0, bw - 1, bh - 1), 30, fill=(0, 0, 0, 245))
    img.paste(box, (bx, by), box)
    ImageDraw.Draw(img).text((bx + 45, by + (bh - 76 * 1.18) / 2), score, font=sf, fill="white")
    boxes.append((bx, by, bx + bw, by + bh))
    bad = [b for b in boxes if b[1] < GRID[0] or b[3] > GRID[1] or b[0] < 0 or b[2] > W]
    if bad:
        sys.exit(f"Cover element outside the grid crop {GRID}: {bad}")
    img.save(out, "JPEG", quality=92)
    dbg = img.copy()
    dd = ImageDraw.Draw(dbg)
    for gy in GRID:
        dd.line((0, gy, W, gy), fill=(0, 255, 255), width=6)
    for b in boxes:
        dd.rectangle(b, outline=(0, 255, 0), width=4)
    dbg.resize((540, 960)).save(out.with_name(out.stem + ".debug.jpg"), quality=85)
    print(f"COVER {out}  (game template, {len(lines)} lines, {font.size}px): {' / '.join(lines)} | {score}")


def main():
    ap = argparse.ArgumentParser(description="Make <base>.cover.jpg for an OKST Reel.")
    ap.add_argument("mp4")
    ap.add_argument("--color", default="orange", choices=list(ACCENT))
    ap.add_argument("--text", default=None, help="hook text (default: from <base>.txt)")
    ap.add_argument("--template", default="hook", choices=["hook", "game"])
    ap.add_argument("--facts", help="game template: facts.json from cloud/games.py facts")
    args = ap.parse_args()

    mp4 = Path(args.mp4).resolve()
    out = mp4.with_name(mp4.stem + ".cover.jpg")
    if out.exists():
        sys.exit(f"Already exists, not overwriting: {out}")
    text = (args.text or hook_text(mp4.with_suffix(".txt"))).upper()
    if args.template == "game":
        if not args.facts:
            sys.exit("--template game needs --facts")
        return game_cover(mp4, out, text, args.facts)

    img = base_frame(mp4).resize((W, H))
    img = ImageEnhance.Brightness(img).enhance(DARKEN)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font, lines = fit(text, draw)
    lh = font.size * 1.08
    y = (H - lh * len(lines)) / 2
    for n, line in enumerate(lines):
        x = (W - draw.textlength(line, font=font)) / 2
        fill = ACCENT[args.color] if n == 0 else (255, 255, 255)
        draw.text((x, y), line, font=font, fill=fill, stroke_width=STROKE, stroke_fill=(0, 0, 0))
        y += lh
    # italic slant around the vertical centre: x_src = x + SLANT * (y - H/2)
    layer = layer.transform((W, H), Image.AFFINE, (1, SLANT, -SLANT * H / 2, 0, 1, 0), Image.BICUBIC)
    img.paste(layer, (0, 0), layer)
    img.save(out, "JPEG", quality=92)
    print(f"COVER {out}  ({len(lines)} lines, {font.size}px): {' / '.join(lines)}")


if __name__ == "__main__":
    main()
