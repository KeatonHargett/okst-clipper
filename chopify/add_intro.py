"""Opening photo for an OKST clip (Cowherd / Barstool style): a game photo of the player the clip is about fills the
screen with a slow push in while the clip's audio and captions run over it, then it slides off to the left with a
whoosh and the split screen takes over.

Usage:
    .venv\\Scripts\\python add_intro.py "<folder>\\<base>.mp4" --photo "<player photo>" [--hold 3.0] [--ass clip.ass]

--photo   an official okstate.com GAME photo of the player the clip is about, on the field. Never a headshot.
--focus   where that player is in the photo, as x,y fractions (default 0.5,0.4); the 9:16 crop and the push in
          center on it.
--hold    target seconds before the photo leaves; it snaps to the nearest caption line change at or after it.
--ass     the clip's caption file. Found automatically from the newest ranges.json that lists this mp4.

Writes <base>.intro.mp4 next to the mp4 and never overwrites. The okst-clip skill copies it to the review folder
as <base>.mp4. The whoosh is synthesized (no third party audio) into assets\\whoosh.wav on first use.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

PROJECT = Path(__file__).resolve().parent
WHOOSH = PROJECT / "assets" / "whoosh.wav"
LOGO = PROJECT / "logo.png"
FONTS_DIR = PROJECT / "fonts"
W, H = 1080, 1920
SS = 2               # card is built at 2x so the slow push in stays smooth
HOLD = 3.0           # default target for how long the photo stays up
SLIDE = 0.32         # seconds to slide off screen
WHOOSH_LEAD = 0.08   # whoosh starts this much before the photo moves
WHOOSH_GAIN = 1.4    # about level with the -14 LUFS voice; a limiter keeps the mix from clipping
ZOOM = 0.07          # push in over the hold (7%)
EDGE_PX = 18         # accent bar just off the right edge: only seen while the photo slides away
ACCENT = (254, 92, 0)
CAPTION_MARGIN_V = 520   # captions sit in the lower third over the photo (split screen keeps them on the seam)
LOGO_W, LOGO_MARGIN = 130, 40
RATE = 48000


def make_whoosh(path, dur=0.55, seed=7):
    """Filtered noise swept upward then down, fast attack, soft tail, panned left to right."""
    rng = np.random.default_rng(seed)
    n = int(dur * RATE)
    t = np.linspace(0, 1, n)
    noise = rng.standard_normal(n)
    # one pole low pass whose cutoff rises then falls: the "swish"
    cutoff = 300 + 5200 * np.sin(np.pi * np.clip(t * 1.15, 0, 1)) ** 2
    alpha = 1 - np.exp(-2 * np.pi * cutoff / RATE)
    out, y = np.empty(n), 0.0
    for i in range(n):
        y += alpha[i] * (noise[i] - y)
        out[i] = y
    out -= np.convolve(out, np.ones(64) / 64, mode="same")      # strip rumble
    env = np.minimum(t / 0.12, 1) * np.exp(-((t - 0.12).clip(0) / 0.38) ** 2 * 3)
    out *= env
    out /= np.abs(out).max() + 1e-9
    out *= 0.7
    pan = t                                                      # left -> right
    stereo = np.stack([out * np.sqrt(1 - pan), out * np.sqrt(pan)], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((stereo * 32767).astype("<i2").tobytes())


def find_ass(mp4):
    """Caption file for this clip from the newest ranges.json that rendered it."""
    for rj in sorted((PROJECT / "work").glob("*/okst_runs/*/ranges.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True):
        for c in json.loads(rj.read_text(encoding="utf-8")):
            if c.get("file") == mp4.name:
                ass = rj.parent / f"range{c['clip']}" / f"clip{c['clip']}.ass"
                if ass.exists():
                    return ass
    return None


def ts(s):
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def fmt(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def snap_hold(ass_text, target):
    """First caption line change at or after target, so the photo leaves on a phrase break."""
    lines = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:"):
            f = line.split(",", 9)
            # a new caption line starts where its first word is the highlighted (non white) one
            if re.match(r"\{\\c&H(?!FFFFFF&)", f[9]):
                lines.append(ts(f[1]))
    pool = [t for t in lines if t >= target]
    return pool[0] if pool else target


def intro_ass(src, dst, hold):
    """Copy of the clip's captions for the photo: lower third instead of the seam, cut at the hold."""
    out = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if line.startswith("Style:"):
            f = line.split(",")
            f[18], f[21] = "2", str(CAPTION_MARGIN_V)       # Alignment, MarginV
            line = ",".join(f)
        elif line.startswith("Dialogue:"):
            f = line.split(",", 9)
            start, end = ts(f[1]), ts(f[2])
            if start >= hold:
                continue
            f[2] = fmt(min(end, hold))
            line = ",".join(f)
        out.append(line)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")


def cover(img, w, h, fx=0.5, fy=0.5):
    """Fill w x h, keeping the point (fx, fy) of the image as close to the center as the edges allow."""
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x = int(max(0, min(img.width - w, fx * img.width - w / 2)))
    y = int(max(0, min(img.height - h, fy * img.height - h / 2)))
    return img.crop((x, y, x + w, y + h))


def vgrad(w, h, y0, y1, color, a0, a1):
    """RGBA layer: color fading from alpha a0 at y0 to a1 at y1 (fractions of h)."""
    col = np.zeros((h, 1), dtype=float)
    ys = np.linspace(0, 1, h)
    col[:, 0] = np.interp(ys, [y0, y1], [a0, a1])
    alpha = Image.fromarray(np.repeat(col, w, axis=1).astype(np.uint8), "L")
    layer = Image.new("RGBA", (w, h), color + (0,))
    layer.putalpha(alpha)
    return layer


def build_card(photo, focus, path):
    cw, ch = W * SS, H * SS
    src = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
    card = cover(src, cw, ch, *focus).convert("RGBA")
    card.alpha_composite(vgrad(cw, ch, 0.62, 1.0, (0, 0, 0), 0, 170))   # caption contrast
    card.convert("RGB").save(path)


def media_fps(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    n, d = r.split("/")
    return float(n) / float(d)


def main():
    ap = argparse.ArgumentParser(description="Add the OKST opening photo + whoosh to a clip.")
    ap.add_argument("mp4")
    ap.add_argument("--photo", required=True, help="official photo of the player the clip is about")
    ap.add_argument("--hold", type=float, default=HOLD, help="target seconds on the photo (snaps to a caption change)")
    ap.add_argument("--ass", help="the clip's caption file (default: found from ranges.json)")
    ap.add_argument("--focus", default="0.5,0.4", help="player position in the photo as x,y fractions")
    args = ap.parse_args()

    mp4 = Path(args.mp4).resolve()
    photo = Path(args.photo).resolve()
    out = mp4.with_name(mp4.stem + ".intro.mp4")
    if out.exists():
        sys.exit(f"Already exists, not overwriting: {out}")
    if not photo.exists():
        sys.exit(f"Missing photo: {photo}")
    ass = Path(args.ass).resolve() if args.ass else find_ass(mp4)
    if not ass or not ass.exists():
        sys.exit("No caption file found for this clip. Pass --ass <clipN.ass> from its run folder.")
    if not WHOOSH.exists():
        make_whoosh(WHOOSH)

    hold = snap_hold(ass.read_text(encoding="utf-8"), args.hold)
    end = hold + SLIDE
    fps = media_fps(mp4)

    focus = tuple(float(v) for v in args.focus.split(","))
    tmp = Path(tempfile.mkdtemp(prefix="okst-intro-", dir=PROJECT / "work"))
    try:
        card = tmp / "card.png"
        build_card(photo, focus, card)
        sub = tmp / "intro.ass"
        intro_ass(ass, sub, hold)

        rel = lambda p: Path(p).relative_to(PROJECT).as_posix()
        frames = int(round(end * fps)) + 2
        logo_w = LOGO_W // 2 * 2
        x = f"if(lt(t,{hold:.3f}),0,-w*pow((t-{hold:.3f})/{SLIDE},2))"
        delay = int((hold - WHOOSH_LEAD) * 1000)
        fc = (f"[1:v]zoompan=z='1+{ZOOM}*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
              f"d={frames}:s={W}x{H}:fps={fps:.5f},setsar=1,"
              f"subtitles=filename={rel(sub)}:fontsdir={rel(FONTS_DIR)}[ph];"
              f"[3:v]scale={logo_w}:-2[lg];[ph][lg]overlay=W-w-{LOGO_MARGIN}:{LOGO_MARGIN},"
              f"pad={W + EDGE_PX}:{H}:0:0:color=0x{bytes(ACCENT).hex()},format=rgba[card];"
              f"[0:v][card]overlay=x='{x}':y=0:eval=frame:enable='lt(t,{end:.3f})'[v];"
              f"[2:a]volume={WHOOSH_GAIN},adelay={delay}|{delay}[wh];"
              f"[0:a][wh]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0[a]")
        subprocess.run(["ffmpeg", "-n", "-loglevel", "error", "-i", str(mp4),
                        "-i", rel(card),
                        "-i", str(WHOOSH), "-i", rel(LOGO),
                        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out)],
                       check=True, cwd=str(PROJECT))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"INTRO {out}  (photo {hold:.2f}s + {SLIDE}s slide, "
          f"whoosh at {hold - WHOOSH_LEAD:.2f}s)")


if __name__ == "__main__":
    main()
