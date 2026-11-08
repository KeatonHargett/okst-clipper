"""okst_highlights - postgame highlight Reels for @okstprobs from official 16:9 highlights videos.

    python okst_highlights.py sheets <video.mp4> --out <dir>            contact sheets (1 frame/s, timestamped) + cuts.txt
    python okst_highlights.py render <plan.json>                          render one Reel (format A, B, or C)
    python okst_highlights.py check <base>.mp4                            export spec + safe zone check (exit 1 on problems)
    python okst_highlights.py debug <base>.mp4                            <base>.debug.jpg: frames with the margins drawn
    python okst_highlights.py check-caption <base>.txt                    highlight caption format check
    python okst_highlights.py verify-headline "<TEXT>" --facts facts.json [--storyline]

Layout (1080x1920), the same for every format so nothing is ever cropped out of the broadcast:
    y    0-640   zoomed copy of the action (footage only; the top 250 px sits under the app's own header, so the
                 zoom point lands at y 445, the middle of the visible 250-640 band)
    y  640-646   orange seam
    y  646-1254  the full 16:9 frame, never cropped: network logo, score bug and watermarks stay exactly as broadcast
    y 1270-1460  OUR graphics: headline box (A), OSU logo + storyline box (B), score bar (C)
    countdown (C) top left at x 60, y 270. Nothing of ours enters the top 250, bottom 450, right 140, or left 60 px;
    render writes every graphic's box to <base>.layout.json and refuses to finish if one leaves the safe area.

Plan JSON (written by the okst-highlights-cloud skill):
    {"format": "A"|"B"|"C", "facts": "out/facts.json", "source": "work/hl/<id>/source.mp4",
     "out": "out/<slug>/<base>.mp4", "headline": "BOWICK BURNS THEM FOR 32 YARDS",
     "cover_at": 61.2,                                   # source second for the cover still (optional)
     "segments": [{"start": 12.4, "end": 17.9, "zoom": {"x": 0.5, "y": 0.55, "w": 0.45},
                   "play": 0, "score_at": 3.1},          # C: index into facts.scoring_plays, score flips at score_at
                  {"card": true, "play": 9, "dur": 1.6}]} # C: a scoring play the video does not show (text from ESPN)
Scores and play text always come from facts.json (ESPN), never from the plan. Headlines are checked against facts.json.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
from make_cover import balanced, has_orphan   # noqa: E402  same line balancing as the grid covers

FONT = PROJECT / "fonts" / "impact.ttf"
OSU_LOGO = PROJECT / "assets" / "osu_logo.png"
LOGO_CACHE = PROJECT / "work" / "logos"
W, H, FPS = 1080, 1920, 30
SAFE = (60, 250, W - 140, H - 450)          # x0, y0, x1, y1: where our text, scores and logos may go
GRID = (240, 1680)                          # Instagram's 3:4 profile grid crop of a 9:16 Reel
ZOOM_H, SEAM, FRAME_H = 640, 6, 608
ZOOM_FOCUS = (SAFE[1] + ZOOM_H) / 2 / ZOOM_H    # 445/640: the zoom point sits mid visible band, not under the header
FRAME_Y = ZOOM_H + SEAM
BAND = (1270, 1460)
ORANGE = (254, 92, 0)
BG = "0x101010"
FORMATS = {"A": (15, 25), "B": (10, 20), "C": (45, 65)}
FINAL_CARD_S = 2.5
DASHES = ("-", "‐", "‑", "‒", "–", "—", "―", "−")


def font(size):
    return ImageFont.truetype(str(FONT), size)


def zoom_crop(sw, sh, z):
    """Crop (w, h, x, y) in source pixels for a zoom {x, y, w}: x, y is the action in the source frame (0..1), placed
    at the horizontal centre and at ZOOM_FOCUS of the panel height, so it is never hidden under Instagram's header."""
    cw = int(sw * z["w"]) // 2 * 2
    ch = int(cw * ZOOM_H / W) // 2 * 2
    cx = int(min(max(sw * z["x"] - cw / 2, 0), sw - cw))
    cy = int(min(max(sh * z["y"] - ch * ZOOM_FOCUS, 0), sh - ch))
    return cw, ch, cx, cy


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{r.stderr[-2500:]}")
    return r.stdout


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                           "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,pix_fmt,bit_rate:"
                           "format=duration,bit_rate", "-of", "json", str(path)]))


# ---------- facts checks ----------

def fact_numbers(f):
    texts = [p["text"] or "" for p in f["scoring_plays"]] + [l["value"] or "" for l in f["leaders"]]
    texts += [s["headline"] for s in f["storylines"]] + [f["result_headline"]]
    nums = {str(f["osu"]["score"]), str(f["opp"]["score"])}
    for t in (f["osu"], f["opp"]):
        texts += [t["record"] or "", t["conf_record"] or ""]
        if t["rank"]:
            nums.add(str(t["rank"]))
    for t in texts:
        nums.update(re.findall(r"\d+", t))
    return nums


ORDINALS = {"SECOND": 2, "2ND": 2, "THIRD": 3, "3RD": 3, "FOURTH": 4, "4TH": 4, "FIFTH": 5, "5TH": 5}
UNVERIFIABLE = re.compile(r"\b(RECORD|CAREER|SCHOOL|HISTORY|ALL.TIME|FRANCHISE|SINCE \d{4}|FIRST TIME|LONGEST|BEST)\b")


def verify_headline(text, f, storyline=False):
    """Problems with a headline: every number must be in facts.json, ordinal claims ("SECOND TD") must be counted
    from ESPN's scoring plays, and claims ESPN cannot back (records, career highs) are refused."""
    t = text.upper().strip()
    if storyline:
        allowed = [s["headline"] for s in f["storylines"]] + [f["result_headline"]]
        return [] if t in allowed else [f"B headline must be one of the verified storylines: {allowed}"]
    probs = []
    nums = fact_numbers(f)
    for n in re.findall(r"\d+", t):
        if n not in nums:
            probs.append(f"number {n} is not in ESPN's game data")
    if UNVERIFIABLE.search(t):
        probs.append(f"'{UNVERIFIABLE.search(t).group(0)}' cannot be verified from ESPN's game data")
    for word, n in ORDINALS.items():
        if re.search(rf"\b{word}\b", t):
            m = re.search(r"\b([A-Z][A-Z.-]+)'(?:S\b)?", t)
            if not m:
                probs.append(f"'{word}' needs a player name before it (e.g. HAWKINS' / HAWKINS'S)")
                continue
            name = m.group(1).title()
            count = sum(1 for p in f["scoring_plays"] if p["osu"] and name.lower() in (p["text"] or "").lower())
            if count < n:
                probs.append(f"'{word}' claims {n} scores for {name}, ESPN lists {count}")
    return probs


EMOJI_CATS = ("So", "Sk")


def is_emoji(ch):
    return unicodedata.category(ch) in EMOJI_CATS or ord(ch) >= 0x1F000


def check_caption(text):
    """Highlight caption: opening line / 3 emoji bullet lines / credit / exactly 5 hashtags, #OKState first."""
    probs = []
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text.strip())]
    if len(blocks) != 4:
        probs.append(f"needs 4 blocks separated by blank lines (opening, bullets, credit, hashtags); has {len(blocks)}")
    else:
        opening, bullets, credit, tags = blocks
        if "\n" in opening or len(opening.split()) < 3:
            probs.append("opening must be one line of at least 3 words")
        bl = bullets.splitlines()
        if len(bl) != 3 or not all(l and is_emoji(l[0]) for l in bl):
            probs.append("bullets must be exactly 3 lines, each starting with an emoji")
        if not credit.startswith(("🎥", "📸")) or "\n" in credit:
            probs.append("credit must be one line starting with 🎥")
        hs = tags.split()
        if len(hs) != 5 or not all(h.startswith("#") for h in hs) or hs[0].lower() != "#okstate":
            probs.append("last line must be exactly 5 hashtags with #OKState first")
    if any(d in text for d in DASHES):
        probs.append("contains a hyphen or dash (write scores as '66 to 28')")
    if re.search(r"tag (a|your) (friend|buddy)", text, re.I):
        probs.append("no 'tag a friend'")
    if len(text) > 2200:
        probs.append(f"{len(text)} characters, over Instagram's 2,200")
    return probs


# ---------- graphics (PIL) ----------

def fit(draw, text, max_w, max_lines, sizes):
    words = text.split()
    for size in sizes:
        f = font(size)
        for n in range(1, min(max_lines, len(words)) + 1):
            (_, widest), lines = balanced(words, n, lambda l: draw.textlength(l, font=f))
            if widest <= max_w and not (n > 1 and has_orphan(lines) and size > sizes[-1]):
                return f, lines
    raise SystemExit(f"Text does not fit: {text!r}")


def rounded(size, radius, fill):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=fill)
    return im


def team_logo(team, size):
    if team["id"] == "197" and OSU_LOGO.exists():
        p = OSU_LOGO
    else:
        p = LOGO_CACHE / f"{team['id']}.png"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                urllib.request.urlretrieve(team["logo"], p)
            except Exception:
                return None
    im = Image.open(p).convert("RGBA")
    im.thumbnail((size, size), Image.LANCZOS)
    return im


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Layout:
    """Graphics for one Reel: each is a full frame RGBA PNG plus its box, so every box can be checked."""

    def __init__(self, workdir):
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.items = []          # {"name", "png", "box", "start", "end"}

    def add(self, name, canvas, box, start=None, end=None):
        png = self.dir / f"{name}.png"
        canvas.save(png)
        self.items.append({"name": name, "png": str(png), "box": [int(v) for v in box], "start": start, "end": end})

    def problems(self):
        x0, y0, x1, y1 = SAFE
        return [f"{i['name']} box {i['box']} leaves the safe area {SAFE}" for i in self.items
                if i["box"][0] < x0 or i["box"][1] < y0 or i["box"][2] > x1 or i["box"][3] > y1]


def headline_box(lay, text, start, end):
    """Format A: white Impact caps in a black rounded box, centered, low in the safe area."""
    c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)
    max_w = 2 * (W // 2 - (W - SAFE[2])) - 64             # box centered on the frame, inside the right margin
    f, lines = fit(d, text.upper(), max_w, 2, range(78, 47, -2))
    lh = int(f.size * 1.08)
    bw = int(max(d.textlength(l, font=f) for l in lines)) + 64
    bh = lh * len(lines) + 36
    x0, y0 = (W - bw) // 2, BAND[1] - bh
    c.alpha_composite(rounded((bw, bh), 24, (0, 0, 0, 238)), (x0, y0))
    y = y0 + 16
    for l in lines:
        d.text(((W - d.textlength(l, font=f)) / 2, y), l, font=f, fill="white")
        y += lh
    lay.add("headline", c, (x0, y0, x0 + bw, y0 + bh), start, end)


def storyline_box(lay, text, start, end):
    """Format B: the OSU logo overlapping the left end of a black rounded box with the storyline."""
    c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)
    logo = team_logo({"id": "197"}, 170)
    lx = SAFE[0] + 10
    bx0 = lx + 105
    f, lines = fit(d, text.upper(), SAFE[2] - bx0 - 115, 2, range(76, 45, -2))
    lh = int(f.size * 1.08)
    bh = max(lh * len(lines) + 40, 150)
    tw = int(max(d.textlength(l, font=f) for l in lines))
    bx1 = bx0 + 95 + tw + 36
    y0 = BAND[1] - bh
    c.alpha_composite(rounded((bx1 - bx0, bh), 24, (0, 0, 0, 238)), (bx0, y0))
    y = y0 + (bh - lh * len(lines)) // 2
    for l in lines:
        d.text((bx0 + 95, y), l, font=f, fill="white")
        y += lh
    ly = y0 + (bh - logo.height) // 2
    c.alpha_composite(logo, (lx, ly))
    lay.add("storyline", c, (lx, min(y0, ly), bx1, max(y0 + bh, ly + logo.height)), start, end)


def score_bar(lay, f, osu_s, opp_s, name, start, end):
    """Format C: OSU orange half and opponent color half, logos, abbreviations, scores."""
    c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)
    bw, bh = 800, 150
    x0, y0 = (W - bw) // 2, BAND[1] - bh
    half = bw // 2
    c.alpha_composite(rounded((bw, bh), 22, (0, 0, 0, 240)), (x0, y0))
    for i, (team, score, color) in enumerate(((f["osu"], osu_s, ORANGE), (f["opp"], opp_s, hex_rgb(f["opp"]["color"])))):
        hx = x0 + i * half
        panel = rounded((half - 12, bh - 12), 18, color + (255,))
        c.alpha_composite(panel, (hx + 6, y0 + 6))
        disc = 112                                   # white disc so a logo in the team's own color still shows
        lx = hx + 18 if i == 0 else hx + half - 18 - disc
        ly = y0 + (bh - disc) // 2
        d.ellipse((lx, ly, lx + disc, ly + disc), fill="white")
        logo = team_logo(team, 84)
        if logo:
            c.alpha_composite(logo, (lx + (disc - logo.width) // 2, ly + (disc - logo.height) // 2))
        abbr = "OSU" if i == 0 else team["abbr"]
        fa, fs = font(44), font(104 if score < 100 else 84)        # basketball's 3 digit scores
        sw = d.textlength(str(score), font=fs)
        ax = hx + 142 if i == 0 else hx + half - 142 - d.textlength(abbr, font=fa)
        sx = hx + half - 26 - sw if i == 0 else hx + 26
        d.text((ax, y0 + 52), abbr, font=fa, fill="white", stroke_width=2, stroke_fill="black")
        d.text((sx, y0 + (bh - fs.size * 1.2) / 2), str(score), font=fs, fill="white", stroke_width=3,
               stroke_fill="black")
    lay.add(name, c, (x0, y0, x0 + bw, y0 + bh), start, end)


def countdown_ring(lay, start, end):
    c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)
    x0, y0, s = SAFE[0] + 6, SAFE[1] + 20, 150
    d.ellipse((x0, y0, x0 + s, y0 + s), fill=(0, 0, 0, 150))
    d.arc((x0 + 6, y0 + 6, x0 + s - 6, y0 + s - 6), -90, 220, fill=ORANGE + (255,), width=9)
    lay.add("countdown_ring", c, (x0, y0, x0 + s, y0 + s), start, end)
    return x0, y0, s


def card_frame(lay, name, title, line):
    """A scoring play the highlights video does not show: full frame card, text from ESPN."""
    c = Image.new("RGBA", (W, H), (16, 16, 16, 255))
    d = ImageDraw.Draw(c)
    logo = team_logo({"id": "197"}, 260)
    c.alpha_composite(logo, ((W - logo.width) // 2, 420))
    ft, lines_t = fit(d, title.upper(), SAFE[2] - SAFE[0] - 40, 1, range(130, 70, -4))
    fl, lines_l = fit(d, line.upper(), SAFE[2] - SAFE[0] - 40, 2, range(84, 44, -2))
    y = 760
    for l in lines_t:
        d.text(((W - d.textlength(l, font=ft)) / 2, y), l, font=ft, fill=ORANGE)
        y += int(ft.size * 1.1)
    y += 20
    for l in lines_l:
        d.text(((W - d.textlength(l, font=fl)) / 2, y), l, font=fl, fill="white")
        y += int(fl.size * 1.1)
    p = lay.dir / f"{name}.png"
    c.convert("RGB").save(p)
    lay.items.append({"name": name, "png": None, "box": [180, 420, 900, y], "start": None, "end": None})
    return p


def final_card(lay, f):
    """Format C ending: OSU orange card with the final score (all text inside the safe area)."""
    c = Image.new("RGBA", (W, H), ORANGE + (255,))
    d = ImageDraw.Draw(c)
    for y in range(H):                                   # light vertical gradient
        a = int(70 * y / H)
        d.line((0, y, W, y), fill=(0, 0, 0, a))
    c = Image.alpha_composite(Image.new("RGBA", (W, H), ORANGE + (255,)), c)
    d = ImageDraw.Draw(c)
    cx = W // 2
    logo = team_logo(f["osu"], 250)
    c.alpha_composite(logo, (cx - logo.width // 2, 300))
    ff = font(120)
    d.text((cx - d.textlength("FINAL", font=ff) / 2, 590), "FINAL", font=ff, fill="white",
           stroke_width=4, stroke_fill="black")
    rows = [("OSU", f["osu"]["score"]), (f["opp"]["abbr"], f["opp"]["score"])]
    fa, fs = font(110), font(200)
    y = 760
    for abbr, score in rows:
        d.text((150, y + 45), abbr, font=fa, fill="white", stroke_width=4, stroke_fill="black")
        sw = d.textlength(str(score), font=fs)
        d.text((SAFE[2] - 30 - sw, y), str(score), font=fs, fill="white", stroke_width=5, stroke_fill="black")
        y += 230
    rec = f["osu"]["record"]
    if rec:
        sub = f"{rec}" + (f"  ·  {f['osu']['conf_record']} BIG 12" if f.get("conference_game") and
                                f["osu"]["conf_record"] and f["osu"]["conf_record"] != "0-0" else "")
        fr = font(64)
        d.text((cx - d.textlength(sub, font=fr) / 2, y + 20), sub, font=fr, fill="black")
        y += 100
    p = lay.dir / "final_card.png"
    c.convert("RGB").save(p)
    lay.items.append({"name": "final_card_text", "png": None, "box": [150, 300, SAFE[2] - 30, y + 20],
                      "start": "final", "end": None})
    return p


# ---------- render ----------

def FILTER_SCRIPT_OPT():
    """ffmpeg 7+ reads a filtergraph from a file with -/filter_complex (8 dropped -filter_complex_script);
    Ubuntu's apt ffmpeg 6 only knows the old option."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True)
    m = re.search(r"ffmpeg version n?(\d+)\.", r.stdout)
    return "-filter_complex_script" if m and int(m.group(1)) < 7 else "-/filter_complex"


def esc(p):
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def has_audio(path):
    return any(s.get("codec_type") == "audio" for s in probe(path)["streams"])


def play_card_text(p):
    title = {"Field Goal Good": "FIELD GOAL"}.get(p["type"], (p["type"] or "SCORE").upper())
    line = re.sub(r"\s*\(.*?\)\s*", " ", p["text"] or "").strip()
    return title, line


def render(plan_path):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    fmt = plan["format"].upper()
    f = json.loads(Path(plan["facts"]).read_text(encoding="utf-8"))
    src = Path(plan["source"]).resolve()
    out = Path(plan["out"]).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"Already exists, not overwriting: {out}")
    if fmt in ("A", "B"):
        probs = verify_headline(plan["headline"], f, storyline=(fmt == "B"))
        if probs:
            raise SystemExit("Headline refused: " + "; ".join(probs))
    if fmt == "B" and not f["osu"]["winner"]:
        raise SystemExit("Format B (milestone) is for wins only.")
    lay = Layout(out.with_suffix(".parts"))
    sinfo = probe(src)
    v = next(s for s in sinfo["streams"] if s["codec_type"] == "video")
    sw, sh = int(v["width"]), int(v["height"])
    audio = has_audio(src)
    plays = f["scoring_plays"]

    inputs = ["-i", str(src)]
    chains, vlabels, t, segs = [], [], 0.0, []
    for i, sg in enumerate(plan["segments"]):
        if sg.get("card"):
            p = plays[sg["play"]]
            png = card_frame(lay, f"card{i}", *play_card_text(p))
            dur = float(sg.get("dur", 1.6))
            k = inputs.count("-i")
            inputs += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(png)]
            chains.append(f"[{k}:v]fps={FPS},scale={W}:{H},setsar=1,format=yuv420p,trim=duration={dur:.3f}[v{i}]")
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[a{i}]")
        else:
            s, e = float(sg["start"]), float(sg["end"])
            dur = e - s
            z = {"x": 0.5, "y": 0.55, "w": 0.45, **(sg.get("zoom") or {})}
            cw, ch, cx, cy = zoom_crop(sw, sh, z)
            chains.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,fps={FPS},setsar=1,split[za{i}][fa{i}]")
            chains.append(f"[za{i}]crop={cw}:{ch}:{cx}:{cy},scale={W}:{ZOOM_H},setsar=1[z{i}]")
            chains.append(f"[fa{i}]scale={W}:{FRAME_H},setsar=1[f{i}]")
            chains.append(f"color=c=0xFE5C00:s={W}x{SEAM}:r={FPS}:d={dur:.3f}[s{i}]")
            chains.append(f"[z{i}][s{i}][f{i}]vstack=3,pad={W}:{H}:0:0:color={BG},format=yuv420p[v{i}]")
            if audio:
                chains.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
                              f"aformat=sample_rates=48000:channel_layouts=stereo,"
                              f"afade=t=in:d=0.06,afade=t=out:st={max(dur - 0.12, 0):.3f}:d=0.12[a{i}]")
            else:
                chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[a{i}]")
        segs.append({"t0": t, "t1": t + dur, **sg})
        vlabels.append(f"[v{i}][a{i}]")
        t += dur
    action_end = t
    if fmt == "C":
        png = final_card(lay, f)
        k = inputs.count("-i")
        inputs += ["-loop", "1", "-t", f"{FINAL_CARD_S}", "-i", str(png)]
        n = len(plan["segments"])
        chains.append(f"[{k}:v]fps={FPS},scale={W}:{H},setsar=1,format=yuv420p,trim=duration={FINAL_CARD_S}[v{n}]")
        chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={FINAL_CARD_S}[a{n}]")
        vlabels.append(f"[v{n}][a{n}]")
        t += FINAL_CARD_S
    total = t
    for it in lay.items:
        if it["start"] == "final":
            it["start"], it["end"] = action_end, total
    lo, hi = FORMATS[fmt]
    if not lo <= total <= hi:
        raise SystemExit(f"Format {fmt} must run {lo} to {hi} s; this plan runs {total:.1f} s.")
    chains.append(f"{''.join(vlabels)}concat=n={len(vlabels)}:v=1:a=1[cv][ca]")

    # graphics
    if fmt == "A":
        headline_box(lay, plan["headline"], 0, total)
    elif fmt == "B":
        storyline_box(lay, plan["headline"], 0, total)
    else:
        rx, ry, rs = countdown_ring(lay, 0, action_end)
        for j, sg in enumerate(segs):
            n = int(sg["play"])
            p = plays[n]
            if not p["osu"]:
                raise SystemExit(f"Segment {j}: scoring play {n} is not an Oklahoma State score.")
            if j and n <= int(segs[j - 1]["play"]):
                raise SystemExit(f"Segment {j}: scoring plays must run in game order (play {n} after "
                                 f"{segs[j - 1]['play']}).")
            before = (plays[n - 1]["osu_score"], plays[n - 1]["opp_score"]) if n > 0 else (0, 0)
            after = (p["osu_score"], p["opp_score"])
            flip = sg["t0"] + (0 if sg.get("card") else float(sg.get("score_at", (sg["t1"] - sg["t0"]) * 0.6)))
            flip = min(flip, sg["t1"])
            end = segs[j + 1]["t0"] if j + 1 < len(segs) else action_end
            if flip > sg["t0"]:
                score_bar(lay, f, *before, f"score{j}a", sg["t0"], flip)
            score_bar(lay, f, *after, f"score{j}b", flip, end)
    cur = "cv"
    for k_, it in enumerate(i for i in lay.items if i["png"]):
        k = inputs.count("-i")
        inputs += ["-loop", "1", "-i", it["png"]]
        en = f":enable='between(t,{it['start']:.3f},{it['end'] - 0.001:.3f})'"
        chains.append(f"[{cur}][{k}:v]overlay=0:0:shortest=0{en}[g{k_}]")
        cur = f"g{k_}"
    if fmt == "C":
        x, y, s = rx, ry, rs
        expr = f"max(0\\,59-floor(t*59/{action_end:.3f}))"
        chains.append(f"[{cur}]drawtext=fontfile='{esc(FONT)}':text='\\:%{{eif\\:{expr}\\:d\\:2}}':fontsize=70:"
                      f"fontcolor=white:borderw=3:bordercolor=black:x={x}+({s}-tw)/2:y={y}+({s}-th)/2"
                      f":enable='lt(t,{action_end:.3f})'[gt]")
        cur = "gt"
    chains.append(f"[{cur}]format=yuv420p[vout]")
    chains.append("[ca]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    probs = lay.problems()
    if probs:
        raise SystemExit("Safe zone check failed: " + "; ".join(probs))
    script = lay.dir / "filter.txt"
    script.write_text(";\n".join(chains), encoding="utf-8")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, FILTER_SCRIPT_OPT(), str(script),
           "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}", "-r", str(FPS),
           "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-b:v", "10M", "-maxrate", "12M",
           "-bufsize", "20M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(out)]
    try:
        run(cmd)
    except SystemExit:
        out.unlink(missing_ok=True)          # never leave a half written Reel behind
        raise
    layout = {"format": fmt, "duration": round(total, 3), "safe": SAFE, "grid": GRID,
              "items": [{k: v for k, v in i.items() if k != "png"} for i in lay.items],
              "segments": segs, "source": str(src), "headline": plan.get("headline")}
    out.with_suffix(".layout.json").write_text(json.dumps(layout, indent=1), encoding="utf-8")
    # cover still: the full 16:9 source frame (never cropped) for make_cover.py --template game
    cover_at = plan.get("cover_at")
    if cover_at is None:
        real = [s for s in plan["segments"] if not s.get("card")]
        best = max(real, key=lambda s: s["end"] - s["start"])
        cover_at = (best["start"] + best["end"]) / 2
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{float(cover_at):.3f}", "-i", str(src), "-frames:v", "1",
         "-q:v", "2", str(out.with_suffix(".frame.jpg"))])
    print(f"RENDERED {out} ({fmt}, {total:.1f}s, {len(plan['segments'])} segments)")
    return out


# ---------- checks ----------

def check(mp4):
    mp4 = Path(mp4)
    info = probe(mp4)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    dur = float(info["format"]["duration"])
    rate = int(info["format"].get("bit_rate") or 0)
    num, den = (v.get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    probs = []
    if (v.get("width"), v.get("height")) != (W, H):
        probs.append(f"{v.get('width')}x{v.get('height')}, needs {W}x{H}")
    if v.get("codec_name") != "h264" or v.get("pix_fmt") != "yuv420p":
        probs.append("video must be H.264 yuv420p")
    if abs(fps - FPS) > 0.05:
        probs.append(f"{fps:.2f} fps, needs {FPS}")
    if a.get("codec_name") != "aac" or int(a.get("sample_rate") or 0) != 48000:
        probs.append("audio must be AAC 48 kHz")
    if dur >= 90:
        probs.append(f"{dur:.1f}s, must be under 90")
    if not 7.5e6 <= rate <= 12.5e6:
        probs.append(f"bitrate {rate / 1e6:.1f} Mbps, needs 8 to 12")
    lj = mp4.with_suffix(".layout.json")
    if lj.exists():
        lay = json.loads(lj.read_text(encoding="utf-8"))
        lo, hi = FORMATS[lay["format"]]
        if not lo - 0.2 <= dur <= hi + 0.2:
            probs.append(f"format {lay['format']} runs {lo} to {hi}s; this is {dur:.1f}s")
        x0, y0, x1, y1 = SAFE
        probs += [f"{i['name']} outside the safe area" for i in lay["items"]
                  if i["box"][0] < x0 or i["box"][1] < y0 or i["box"][2] > x1 or i["box"][3] > y1]
    else:
        probs.append("no layout.json (not rendered by okst_highlights.py)")
    print(f"{mp4.name}: {v.get('width')}x{v.get('height')} {v.get('codec_name')} {fps:.0f}fps "
          f"{a.get('codec_name')} {int(a.get('sample_rate') or 0) // 1000}kHz {dur:.1f}s {rate / 1e6:.1f}Mbps")
    for p in probs:
        print("PROBLEM:", p)
    print("CHECK OK" if not probs else "CHECK FAILED")
    return probs


def margins_overlay(img, boxes=()):
    """Red = no text zones (top 250, bottom 450, right 140, left 60). Cyan = Instagram grid 3:4 crop. Green = our boxes."""
    o = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(o)
    sx, sy = img.width / W, img.height / H
    red = (255, 0, 0, 90)
    x0, y0, x1, y1 = SAFE
    d.rectangle((0, 0, img.width, y0 * sy), fill=red)
    d.rectangle((0, y1 * sy, img.width, img.height), fill=red)
    d.rectangle((0, y0 * sy, x0 * sx, y1 * sy), fill=red)
    d.rectangle((x1 * sx, y0 * sy, img.width, y1 * sy), fill=red)
    for gy in GRID:
        d.line((0, gy * sy, img.width, gy * sy), fill=(0, 255, 255, 255), width=max(2, int(4 * sy)))
    for b in boxes:
        d.rectangle((b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy), outline=(0, 255, 0, 255), width=max(2, int(3 * sx)))
    return Image.alpha_composite(img.convert("RGBA"), o).convert("RGB")


def debug(mp4):
    mp4 = Path(mp4)
    lj = mp4.with_suffix(".layout.json")
    lay = json.loads(lj.read_text(encoding="utf-8")) if lj.exists() else {"items": [], "duration": None}
    dur = float(probe(mp4)["format"]["duration"])
    tiles = []
    with tempfile.TemporaryDirectory() as td:
        for k, frac in enumerate((0.12, 0.4, 0.7, 0.985)):
            t = dur * frac
            p = Path(td) / f"{k}.jpg"
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1", str(p)])
            boxes = [i["box"] for i in lay["items"] if i["start"] is None or i["start"] <= t <= (i["end"] or 1e9)]
            im = margins_overlay(Image.open(p).resize((540, 960)), boxes)
            ImageDraw.Draw(im).text((8, 8), f"t={t:.1f}s", font=font(30), fill="yellow")
            tiles.append(im)
    sheet = Image.new("RGB", (540 * len(tiles), 960), "black")
    for k, im in enumerate(tiles):
        sheet.paste(im, (540 * k, 0))
    out = mp4.with_suffix(".debug.jpg")
    sheet.save(out, quality=88)
    print(f"DEBUG {out}")
    return out


def sheets(video, out_dir, every=1.0):
    """Contact sheets: one frame per `every` seconds, 5x6 per sheet (30 frames), each stamped with its time."""
    video, out_dir = Path(video).resolve(), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = float(probe(video)["format"]["duration"])
    vf = (f"fps=1/{every},scale=384:-2,drawtext=fontfile=fonts/impact.ttf:text='%{{pts\\:hms}}':x=6:y=6:"
          f"fontsize=30:fontcolor=yellow:box=1:boxcolor=black@0.6,tile=5x6")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", vf, "-fps_mode", "vfr", str(out_dir / "sheet_%02d.jpg")],
        cwd=PROJECT)
    cuts = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-vf", "select='gt(scene,0.3)',showinfo",
                           "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    times = re.findall(r"pts_time:([\d.]+)", cuts)
    (out_dir / "cuts.txt").write_text("\n".join(f"{float(x):.2f}" for x in times) + "\n", encoding="utf-8")
    n = len(list(out_dir.glob("sheet_*.jpg")))
    print(f"SHEETS {out_dir} ({n} sheets, {dur:.0f}s, {len(times)} cuts; sheet k covers "
          f"{30 * every:.0f}s starting at (k-1)*{30 * every:.0f}s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sheets"); p.add_argument("video"); p.add_argument("--out", required=True)
    p.add_argument("--every", type=float, default=1.0)
    p = sub.add_parser("render"); p.add_argument("plan")
    p = sub.add_parser("check"); p.add_argument("mp4")
    p = sub.add_parser("debug"); p.add_argument("mp4")
    p = sub.add_parser("check-caption"); p.add_argument("txt")
    p = sub.add_parser("verify-headline"); p.add_argument("text"); p.add_argument("--facts", required=True)
    p.add_argument("--storyline", action="store_true")
    a = ap.parse_args()
    if a.cmd == "sheets":
        sheets(a.video, a.out, a.every)
    elif a.cmd == "render":
        render(a.plan)
    elif a.cmd == "check":
        sys.exit(1 if check(a.mp4) else 0)
    elif a.cmd == "debug":
        debug(a.mp4)
    elif a.cmd == "check-caption":
        text = Path(a.txt).read_text(encoding="utf-8-sig").strip()
        probs = check_caption(text)
        print(f"{len(text)} characters")
        for x in probs:
            print("PROBLEM:", x)
        print("CAPTION OK" if not probs else "CAPTION FAILED")
        sys.exit(1 if probs else 0)
    elif a.cmd == "verify-headline":
        f = json.loads(Path(a.facts).read_text(encoding="utf-8"))
        probs = verify_headline(a.text, f, a.storyline)
        for x in probs:
            print("PROBLEM:", x)
        print("HEADLINE OK" if not probs else "HEADLINE REFUSED")
        sys.exit(1 if probs else 0)


if __name__ == "__main__":
    main()
