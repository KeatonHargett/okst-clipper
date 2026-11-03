"""
okst_clip - OKST house style on top of chopify, without editing chopify.

    .venv\\Scripts\\python okst_clip.py "URL" --color orange
    .venv\\Scripts\\python okst_clip.py "URL" --color turquoise --preview --clips 1
    .venv\\Scripts\\python okst_clip.py "URL" --chapter "Eric Morris"
    .venv\\Scripts\\python okst_clip.py "URL" --start 12:30 --end 20:00
    .venv\\Scripts\\python okst_clip.py "URL" --transcriber whisper --whisper-model medium
    .venv\\Scripts\\python okst_clip.py "URL" --transcript-only [--chapter "Recruiting"]
    .venv\\Scripts\\python okst_clip.py "URL" --ranges "2:00.5-2:40,15:01-15:45"

--transcript-only stops after the corrected words.txt and sentences.txt (no
video download). --ranges skips scoring and renders exactly the given video
time ranges from section downloads.

Stage 1 downloads the video (or, with --chapter/--start/--end, only the window
padded 2s each side via yt-dlp --download-sections). The transcript comes from
YouTube's own English captions when they are usable (--transcriber auto, the
default), converted to chopify's transcript.json format; otherwise from
download_and_transcribe.transcribe() (Whisper). Transcripts are cached per
source and model (youtube-human, youtube-auto, whisper-small, ...). CORRECTIONS
fixes known misspelled names before scoring. Stage 2 runs chopify's
score_clips.py on the window's words, then clip ends are moved to clean stops
(before another speaker's question, or back to a sentence end; trims.json).
Stage 3 reuses chopify's render_clips
functions as a library (crop, face detection for a fixed locked crop, build_ass for caption position/grouping/timing), then
restyles the generated .ass and renders crop + captions + logo + loudnorm in a
single ffmpeg pass.

Never overwrites: scoring goes to a fresh run folder, and output names get a
number added if they already exist. Never deletes anything it did not make.
"""
import os
import re
import sys
import json
import time
import shutil
import argparse
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

import chopify          # noqa: E402  preflight check
import render_clips     # noqa: E402  crop/track/caption helpers
import download_and_transcribe as dl_tr  # noqa: E402  transcribe()

PY = sys.executable

# Known misspellings in either transcript source -> correct spelling. Whole
# words, case insensitive, multi word keys allowed. Applied to each run's word
# list before scoring and captions; every hit is logged to corrections.json.
CORRECTIONS = {
    # Add only if the fix is on OSU_ROSTER.txt, its CONFIRMED_NAMES, or an official team site; flag ambiguous names (shared last names, lone first names, unsure spellings) instead.
    "Stillrotter": "Stillwater",
    "Gallagher Eba": "Gallagher Iba",
    "Boone Pickins": "Boone Pickens",
    "Messamaker": "Mestemaker",
    "Messemaker": "Mestemaker",
    "Colin Klein": "Collin Klein",
    "Morgan Scali": "Morgan Scalley",
    "Morgan Scally": "Morgan Scalley",
    "Boom Pickings": "Boone Pickens",
    "Braden Nelson": "Braydon Nelson",
    "Brilen Presley": "Braylin Presley",
    "Mo Phillips": "Mose Phillips",
    "Dennis Franchion": "Dennis Franchione",
    "Mike Leech": "Mike Leach",
    "Leech family": "Leach family",
    "Abene Christian": "Abilene Christian",
    "Coach Krill": "Coach Crill",
    "Coach Cassidy": "Coach Cassity",
    "Coach Cassie": "Coach Cassity",
    "yardd": "yard",
    "Jaylen practices": "Jalen practices",   # Jalen Montonati, okstate.com MBB roster #7 (scoped: Jaylen is a common name)
    "Case Keenan": "Case Keenum",
    "Mestermaker": "Mestemaker",
    "Emily Charles": "Malik Charles",
    "Mtomaker": "Mestemaker",
    "Memaker": "Mestemaker",
    "Ethan Weslosski": "Ethan Wesloski",
    "Devon Standard": "Dayvon Standard",
    "White Young": "Wyatt Young",
    "most Phillips": "Mose Phillips",
    "Coach Sabota": "Coach Svoboda",
    "Boick": "Bowick",
    "Sadique Clemens": "Saadiq Clements",
    "Morell Davis": "Marrel Davis",
    "Carson Cobb": "Carson Kolb",
    "Trey Paige": "Tre Page",
    "Mohorn": "Mo Horn",
    "Leenian Fields": "LaDainian Fields",
    "Luke Web": "Luke Webb",
    "Metomaker": "Mestemaker",
    "Mesmaker": "Mestemaker",
    "Ethan Mosowski": "Ethan Wesloski",
    "Ethan Masowski": "Ethan Wesloski",
    "Cole Seatin": "Kole Seaton",
    "Eli Hullbrook": "Eli Holbrook",
    "Gunner Wilson": "Gunnar Wilson",
    "Carvian Young": "Carl'veon Young",
    "Torian Davis": "Taurean Davis",
    "Skyler Cassid": "Skyler Cassity",
    "Shawn Broofphy": "Sean Brophy",
    "Iman Oats": "Iman Oates",
    "Brilland Riggsby": "Braylon Rigsby",
    "Sadique Clements": "Saadiq Clements",
    "Boon Pickin": "Boone Pickens",
    "Deshawn Brown": "DeSean Brown",
    "Justin Bowie": "Justin Bowick",
    "Carson Cole": "Carson Kolb",
}

# Everything okst_clip uses from chopify. Checked at startup so a chopify
# update that renames or removes one fails fast with the name, not mid-run.
REQUIRED = {
    chopify: ["check_environment"],
    render_clips: ["CAPTION_STYLES", "ASPECTS", "PREVIEW", "sanitize",
                   "ffprobe_dims", "crop_for", "detect_track", "build_ass"],
    dl_tr: ["transcribe"],
}
REQUIRED_SCRIPTS = ["download_and_transcribe.py", "score_clips.py"]


def check_chopify_api():
    missing = [f"{mod.__name__}.{name}" for mod, names in REQUIRED.items()
               for name in names if not hasattr(mod, name)]
    missing += [s for s in REQUIRED_SCRIPTS if not (PROJECT / s).exists()]
    if "hormozi" not in getattr(render_clips, "CAPTION_STYLES", {}):
        missing.append("render_clips.CAPTION_STYLES['hormozi']")
    if missing:
        print("CHOPIFY API CHECK FAILED - missing or renamed:",
              *("  - " + m for m in missing), sep="\n")
        sys.exit(1)

# ---------------------------------------------------------------------------
# OKST caption style
# ---------------------------------------------------------------------------
# Fill colours in ASS &HAABBGGRR form. Only the word being spoken gets the fill
# colour; the rest of the line stays white (chopify's highlight behaviour).
ORANGE_ASS = "&H00005CFE"        # #FE5C00
# PLACEHOLDER - swap this hex when the final turquoise is picked.
TURQUOISE_ASS = "&H00D1CE00"     # #00CED1
COLORS = {"orange": ORANGE_ASS, "turquoise": TURQUOISE_ASS}
DEFAULT_COLOR = "orange"

# Split screen (--highlights): speaker on top, muted looping highlights below,
# a SEAM_PX accent bar between them and the captions centred on the seam.
HL_HEAD, HL_TAIL = 4.0, 6.0      # seconds cut off the highlights' intro and end slates
HL_STEP = 23.0                   # each clip starts this much further into the highlights
SEAM_PX = 8                      # at 1080 wide
SPEAKER_KEEP = 0.72              # speaker half crops from the top 72% of the source,
                                 # leaving out tickers and lower thirds that would sit on the seam
SPEAKER_X = None                 # --speaker-x: fixed crop centre as a fraction of source width
                                 # (overrides the median face, e.g. 0.5 for the centre seat)

FONT_NAME = "Impact"
FONT_SRC = Path(r"C:\Windows\Fonts\impact.ttf")
FONTS_DIR = PROJECT / "fonts"
BOLD = 1
ITALIC = 1
OUTLINE_PX = 7                   # black outline at full 1080x1920
SHADOW = 3
SHADOW_COLOUR = "&H80000000"
OUTLINE_COLOUR = "&H00000000"

BASE_STYLE = "hormozi"           # chopify preset whose captions we restyle

# Logo: logo.png, 130px wide, 40px from top and right (at 1080 wide).
LOGO = PROJECT / "logo.png"
LOGO_W = 130
LOGO_MARGIN = 40
FULL_W = 1080

ASPECT = "9:16"
DEFAULT_OUT = PROJECT / "output" / "okst"

# Only hashtag in the draft caption. The okst-clip skill picks the other 4.
CORE_HASHTAG = "#OKState"

HF_MODEL_CACHE = Path.home() / ".cache" / "huggingface" / "hub"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def run_logged(cmd, env=None):
    """Run cmd, stream its output, return (returncode, full_output)."""
    print("\n>", " ".join(str(c) for c in cmd), flush=True)
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", env=env, cwd=str(PROJECT))
    lines = []
    for line in p.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    p.wait()
    return p.returncode, "".join(lines)


def video_info(url):
    """yt-dlp metadata (id, title, duration, chapters) without downloading.
    Returns {} on failure."""
    try:
        out = subprocess.run(
            [PY, "-m", "yt_dlp", "--no-playlist", "--skip-download", "-J", url],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            check=True).stdout
        return json.loads(out)
    except Exception as e:                                   # noqa: BLE001
        print("  could not read video metadata:", e, flush=True)
        return {}


# ---------------------------------------------------------------------------
# segment window (--chapter / --start / --end)
# ---------------------------------------------------------------------------
PAD = 2.0              # seconds of context cut on each side of the window


def parse_time(s):
    """ss, mm:ss or hh:mm:ss (fractions allowed) -> seconds."""
    parts = s.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise argparse.ArgumentTypeError(f"bad time {s!r}: use ss, mm:ss or hh:mm:ss")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad time {s!r}: use ss, mm:ss or hh:mm:ss")
    if any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
        raise argparse.ArgumentTypeError(f"bad time {s!r}")
    secs = 0.0
    for n in nums:
        secs = secs * 60 + n
    return secs


def fmt_ts(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def slug_ts(sec):
    """754 -> 12m34s, 3723 -> 1h02m03s (safe in file names)."""
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


SLUG_MAX = 60


def cap_slug(slug, n=SLUG_MAX):
    """Trim a hyphen slug to <= n chars, cutting at a word boundary."""
    if len(slug) <= n:
        return slug
    cut = slug[:n + 1].rsplit("-", 1)[0] if "-" in slug[:n + 1] else slug[:n]
    return cut.strip("-") or slug[:n]


SLUG_FILLER = {"the", "a", "an", "and", "of", "with", "to", "in", "on", "at", "for"}


def strip_filler(slug):
    """Drop trailing filler words and dashes: 'eric-morris-the' -> 'eric-morris'."""
    parts = slug.split("-")
    while len(parts) > 1 and (parts[-1] in SLUG_FILLER or not parts[-1]):
        parts.pop()
    return "-".join(parts).strip("-") or slug


def run_slug(video_slug, win_slug):
    """<video>-<window>, without repeating a chapter already in the title,
    capped at SLUG_MAX on a word boundary (window part always kept), then
    stripped of trailing filler words."""
    k = video_slug.find(win_slug)
    if k >= 0 and k + len(win_slug) <= SLUG_MAX:
        return strip_filler(cap_slug(video_slug))
    room = SLUG_MAX - len(win_slug) - 1
    if room < 10:
        return strip_filler(cap_slug(f"{video_slug}-{win_slug}"))
    return strip_filler(f"{cap_slug(video_slug, room)}-{win_slug}")


class Tee:
    """Mirror stdout into a log file; buffers until the file is attached."""
    def __init__(self, stream):
        self.stream, self.buf, self.fh = stream, [], None

    def attach(self, path):
        self.fh = open(path, "a", encoding="utf-8")
        self.fh.write("".join(self.buf))
        self.buf = None
        self.fh.flush()

    def write(self, s):
        self.stream.write(s)
        if self.fh:
            self.fh.write(s)
        else:
            self.buf.append(s)

    def flush(self):
        self.stream.flush()
        if self.fh:
            self.fh.flush()


def print_chapters(chapters):
    for c in chapters:
        print(f"  {fmt_ts(c['start_time']):>8}  {c['title']}")


def resolve_window(args, info):
    """None for the full video, else {"chapter", "start", "end", "slug"}."""
    if args.chapter is None and args.start is None and args.end is None:
        return None
    duration = info.get("duration")
    if args.chapter is not None:
        if not info:
            raise SystemExit("Could not read the video's metadata, so --chapter "
                             "cannot be resolved. Try again or use --start and --end.")
        chapters = info.get("chapters") or []
        if not chapters:
            raise SystemExit("This video has no chapters. Use --start and --end instead.")
        print("Chapters:")
        print_chapters(chapters)
        q = args.chapter.strip().lower()
        hits = [i for i, c in enumerate(chapters) if q in c["title"].lower()]
        if len(hits) != 1:
            why = "No chapter matches" if not hits else f"{len(hits)} chapters match"
            print(f"\n{why} {args.chapter!r}. Pick one of the chapters above.")
            sys.exit(1)
        i = hits[0]
        c = chapters[i]
        start = float(c["start_time"])
        end = float(chapters[i + 1]["start_time"]) if i + 1 < len(chapters) \
            else float(duration or c.get("end_time") or 0)
        return {"chapter": c["title"], "start": start, "end": end,
                "slug": render_clips.sanitize(c["title"])}
    start = args.start or 0.0
    end = args.end if args.end is not None else duration
    if end is None:
        raise SystemExit("Could not read the video length; pass --end as well.")
    if duration and end > duration + 0.5:
        raise SystemExit(f"--end {fmt_ts(end)} is past the video end ({fmt_ts(duration)}).")
    if end <= start:
        raise SystemExit(f"Window is empty: start {fmt_ts(start)} >= end {fmt_ts(end)}.")
    return {"chapter": None, "start": float(start), "end": float(end),
            "slug": f"{slug_ts(start)}-{slug_ts(end)}"}


# ---------------------------------------------------------------------------
# transcript source: YouTube captions (json3) or Whisper
# ---------------------------------------------------------------------------
MIN_WPM = 80           # captions sparser than this fall back to Whisper
SEG_PAUSE = 0.6        # caption segments split at pauses longer than this...
SEG_MAX = 12.0         # ...or once they reach this many seconds
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")


def unique_dir(path):
    """path, or path-2, path-3... - a folder that does not exist yet."""
    n, p = 1, path
    while p.exists():
        n += 1
        p = path.with_name(f"{path.name}-{n}")
    p.mkdir(parents=True)
    return p


def media_duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, check=True).stdout
        return round(float(out.strip()), 3)
    except Exception:                                        # noqa: BLE001
        return None


def transcript_key(path):
    """Source/model key of a cached transcript. Transcripts written before
    source keys existed carry only a whisper model name (or nothing, which
    counts as whisper-small)."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    model = str(data.get("model") or "small")
    return model if model.startswith(("youtube-", "whisper-")) else f"whisper-{model}"


def cached_transcript(cache_dir, key):
    """<cache_dir>/<key>/transcript.json, or a pre-key <cache_dir>/transcript.json
    made by the same source and model. Never a transcript from another key."""
    p = cache_dir / key / "transcript.json"
    if p.exists():
        return p
    legacy = cache_dir / "transcript.json"
    if legacy.exists() and transcript_key(legacy) == key:
        return legacy
    return None


def fetch_captions(url, workdir, info, retries):
    """Fetch English captions as json3 with yt-dlp (no video download).
    Returns (json3_path, key, note) or (None, None, why_not)."""
    dest = unique_dir(workdir / "captions" / time.strftime("%Y%m%d-%H%M%S"))
    cmd = [PY, "-m", "yt_dlp", "--no-playlist", "--skip-download",
           "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
           "--sub-format", "json3", "-o", str(dest / "subs.%(ext)s"), url]
    code = None
    for attempt in range(1, retries + 1):
        code, _ = run_logged(cmd)
        files = sorted(dest.glob("subs.*.json3"))
        if code == 0 or files:
            break
        if attempt < retries:
            print(f"\nCaption fetch failed (exit {code}) - retrying "
                  f"({attempt}/{retries - 1})...", flush=True)
            time.sleep(5)
    else:
        return None, None, f"caption fetch failed after {retries} attempt(s) (exit {code})"
    if not files:
        return None, None, "no English captions on this video"

    def lang(p):
        return p.name[len("subs."):-len(".json3")]

    def looks_asr(p):
        d = json.loads(p.read_text(encoding="utf-8"))
        return any("acAsrConf" in s for e in d.get("events", [])
                   for s in e.get("segs") or [])

    # yt-dlp takes the human track when a language has both; info says which
    # languages have human subtitles. Without info, ASR markers decide.
    if "subtitles" in info or "automatic_captions" in info:
        human_langs = set(info.get("subtitles") or {})
        human = [p for p in files if lang(p) in human_langs]
    else:
        human = [p for p in files if not looks_asr(p)]
    if human:
        human.sort(key=lambda p: (lang(p) != "en", lang(p)))
        return human[0], "youtube-human", f"human made {lang(human[0])} subtitles"
    auto = sorted(files, key=lambda p: (lang(p) != "en-orig", lang(p) != "en", lang(p)))
    return auto[0], "youtube-auto", \
        f"no human English subtitles; auto generated {lang(auto[0])} captions"


_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|>>+")


def _tokens(text):
    """Caption text -> spoken words ([music], (laughs), >> and lone
    punctuation dropped)."""
    return [t for t in _BRACKETED.sub(" ", text).split() if re.search(r"\w", t)]


def _est_dur(word):
    """Seconds a word lasts when json3 gives only its start time."""
    return min(1.0, 0.15 + 0.06 * len(re.sub(r"\W", "", word)))


def _spread(tokens, a, b):
    """Spread words over [a, b] in proportion to word length."""
    weights = [max(1, len(re.sub(r"\W", "", t))) for t in tokens]
    total, acc, out = float(sum(weights)), 0, []
    for t, wt in zip(tokens, weights):
        s = a + (b - a) * acc / total
        acc += wt
        out.append({"word": t, "start": s, "end": a + (b - a) * acc / total})
    return out


def json3_words(path):
    """YouTube json3 captions -> ([{"word", "start", "end"}] in video seconds,
    lines_spread). Word starts come from json3 word offsets; a line without
    word timing has its words spread over the line's duration by length."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = [(e, [s for s in e["segs"] if _tokens(s.get("utf8", ""))])
             for e in data.get("events", []) if e.get("segs")]
    lines = [(e, segs) for e, segs in lines if segs]
    words, spread = [], 0
    for k, (e, segs) in enumerate(lines):
        t0 = e.get("tStartMs", 0) / 1000.0
        t1 = t0 + e.get("dDurationMs", 0) / 1000.0
        if k + 1 < len(lines):                 # lines overlap on screen
            nxt = lines[k + 1][0].get("tStartMs", 0) / 1000.0
            t1 = min(t1, nxt) if t1 > t0 else nxt
        first = _tokens(segs[0]["utf8"])
        timed = all("tOffsetMs" in s for s in segs[1:]) and (len(segs) > 1 or len(first) == 1)
        if t1 <= t0:
            t1 = t0 + sum(_est_dur(t) for s in segs for t in _tokens(s["utf8"]))
        if not timed:
            spread += 1
            words += _spread([t for s in segs for t in _tokens(s["utf8"])], t0, t1)
            continue
        starts = [t0 + s.get("tOffsetMs", 0) / 1000.0 for s in segs]
        for j, s in enumerate(segs):
            span_end = starts[j + 1] if j + 1 < len(segs) else t1
            toks = _tokens(s["utf8"])
            if len(toks) == 1:
                words.append({"word": toks[0], "start": starts[j],
                              "end": min(span_end, starts[j] + _est_dur(toks[0]))})
            else:
                words += _spread(toks, starts[j], span_end)
    words.sort(key=lambda w: w["start"])
    for w, nxt in zip(words, words[1:]):
        w["end"] = min(w["end"], nxt["start"])
    for w in words:
        w["end"] = max(w["end"], w["start"] + 0.01)
    return words, spread


def segment_words(words):
    """Group words into chopify-style segments, splitting at pauses longer
    than SEG_PAUSE or once a segment reaches SEG_MAX seconds."""
    segs, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > SEG_PAUSE
                    or w["end"] - cur[0]["start"] > SEG_MAX):
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return [{"start": s[0]["start"], "end": s[-1]["end"],
             "text": "".join(w["word"] for w in s)} for s in segs]


def captions_transcript(json3, key, video, duration, window=None, shift=0.0):
    """json3 -> chopify transcript dict (same fields as
    download_and_transcribe.transcribe writes; "model" holds the source key).
    window=(lo, hi) in video seconds keeps only words inside it; shift moves
    times so they line up with the section download. Returns (dict, wpm, spread)."""
    words, spread = json3_words(json3)
    if window:
        lo, hi = window
        words = [w for w in words if w["start"] >= lo and w["end"] <= hi]
        minutes = (hi - lo) / 60.0
    else:
        minutes = (duration or (words[-1]["end"] if words else 0)) / 60.0
    words = [{"word": " " + w["word"], "start": round(w["start"] - shift, 2),
              "end": round(w["end"] - shift, 2)} for w in words]
    wpm = len(words) / minutes if minutes > 0 else 0.0
    return ({"video": str(video), "language": "en", "duration": duration,
             "model": key, "segments": segment_words(words), "words": words},
            wpm, spread)


def get_transcript(args, info, video, cache_dir, workdir, window=None, shift=0.0,
                   media_fetch=None):
    """Transcript for `video` per --transcriber. cache_dir holds one folder per
    source/model key. media_fetch, if given, is called for the media file only
    when Whisper has to run (so caption runs download nothing).
    Returns {"path", "key", "reason", "seconds"}."""
    t = time.time()
    wkey = f"whisper-{args.whisper_model}"

    def done(path, key, reason):
        secs = time.time() - t
        print(f"\nTRANSCRIPT SOURCE: {key} - {reason}  ({secs:.1f}s)\n", flush=True)
        return {"path": Path(path), "key": key, "reason": reason, "seconds": round(secs, 1)}

    if args.transcriber == "whisper":
        reason = "--transcriber whisper"
    else:
        for key in ("youtube-human", "youtube-auto"):
            p = cached_transcript(cache_dir, key)
            if p:
                return done(p, key, f"cached {key} transcript {p}")
        print("\nFetching YouTube captions (no video download)...", flush=True)
        json3, key, note = fetch_captions(args.url, workdir, info, args.retries)
        if json3:
            dur = media_duration(video) if window else info.get("duration")
            data, wpm, spread = captions_transcript(json3, key, video, dur, window, shift)
            msg = (f"{note}; {len(data['words'])} words, {wpm:.0f} words per minute"
                   + (f", {spread} line(s) without word timing spread by word length"
                      if spread else ""))
            if wpm >= MIN_WPM:
                out = cache_dir / key
                out.mkdir(parents=True, exist_ok=True)
                with open(out / "transcript.json", "x", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                print(f"Wrote {out / 'transcript.json'} from {json3}", flush=True)
                return done(out / "transcript.json", key, msg)
            note = f"{msg} - under {MIN_WPM} words per minute"
        if args.transcriber == "youtube":
            raise SystemExit(f"\nSTOPPED (--transcriber youtube): {note}.\n"
                             "Use --transcriber auto to fall back to Whisper, "
                             "or --transcriber whisper.")
        reason = f"fell back to Whisper: {note}"
        print(f"\n{reason}", flush=True)

    p = cached_transcript(cache_dir, wkey)
    if p:
        return done(p, wkey, f"{reason} (cached {p})")
    import os
    if any(HF_MODEL_CACHE.glob(f"models--Systran--faster-whisper-{args.whisper_model}")):
        os.environ["HF_HUB_OFFLINE"] = "1"         # skip the flaky Hub API call
    if media_fetch:
        video = media_fetch()
    out = cache_dir / wkey
    out.mkdir(parents=True, exist_ok=True)
    dl_tr.transcribe(Path(video).resolve(), out, args.whisper_model, "cpu", "int8")
    return done(out / "transcript.json", wkey, reason)


def download_section(args, dest, ps, pe):
    """Download video seconds [ps, pe] with yt-dlp --download-sections into
    dest/cut.mp4 (reused when already there). Returns the cut path."""
    dest.mkdir(parents=True, exist_ok=True)
    cut = dest / "cut.mp4"
    if cut.exists():
        print(f"Using cached section {cut}", flush=True)
        return cut
    if os.environ.get("OKST_FULL_SOURCE") == "1":
        return cut_from_full_source(args, dest.parent.parent, cut, ps, pe)
    # No --force-keyframes-at-cuts: streams are copied untouched, and the
    # mp4 edit list makes t=0 land exactly on `ps` (checked against a
    # force-keyframes re-encode: frame-aligned at zero shift).
    # -S prefers H.264/AAC (faster to decode at render time than AV1)
    cmd = [PY, "-m", "yt_dlp", "-f", "bestvideo*+bestaudio/best",
           "-S", "vcodec:h264,res:1080,acodec:m4a",
           "--merge-output-format", "mp4", "--no-playlist",
           "--download-sections", f"*{ps:.3f}-{pe:.3f}",
           "-o", str(dest / "cut.%(ext)s"), args.url]
    t = time.time()
    for attempt in range(1, args.retries + 1):
        code, out = run_logged(cmd)
        if code == 0 and cut.exists():
            break
        if "HTTP Error 403" in out and attempt < args.retries:
            print(f"\nYouTube returned 403 - retrying ({attempt}/{args.retries - 1})...",
                  flush=True)
            time.sleep(5)
            continue
        raise SystemExit(f"Section download failed (exit {code}).")
    print(f"Section download: {cut.stat().st_size / 2**20:.1f} MiB "
          f"in {time.time() - t:.0f}s", flush=True)
    return cut


def cut_from_full_source(args, workdir, cut, ps, pe):
    """Cloud runs (OKST_FULL_SOURCE=1): YouTube is reached through a SOCKS proxy
    (Cloudflare WARP) that ffmpeg ignores, and --download-sections hands the
    video URL to ffmpeg, which then gets 403. So download the whole video once
    with yt-dlp's own downloader (which uses the proxy) into workdir/source.mp4,
    and cut each padded range locally."""
    src = workdir / "source.mp4"
    if not src.exists():
        cmd = [PY, "-m", "yt_dlp", "-f", "bestvideo*+bestaudio/best",
               "-S", "vcodec:h264,res:1080,acodec:m4a", "-N", "4",
               "--merge-output-format", "mp4", "--no-playlist",
               "-o", str(workdir / "source.%(ext)s"), args.url]
        t = time.time()
        for attempt in range(1, args.retries + 1):
            code, out = run_logged(cmd)
            if code == 0 and src.exists():
                break
            if attempt < args.retries:
                print(f"\nFull download failed - retrying ({attempt}/{args.retries - 1})...",
                      flush=True)
                time.sleep(10)
                continue
            raise SystemExit(f"Full download failed (exit {code}).")
        print(f"Full download: {src.stat().st_size / 2**20:.1f} MiB "
              f"in {time.time() - t:.0f}s", flush=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ps:.3f}", "-to", f"{pe:.3f}",
                    "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(cut)], check=True)
    print(f"Cut {ps:.2f}-{pe:.2f} from the full source", flush=True)
    return cut


def media_fps(path):
    """Video frame rate as an ffmpeg fps value ("30000/1001"), 30 if unknown."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout.strip()
    return out if re.fullmatch(r"\d+(/\d+)?", out) and not out.startswith("0") else "30"


def media_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True,
                         text=True).stdout.strip()
    return float(out or 0)


def fetch_highlights(url, retries):
    """Download a highlights video once (video only, H.264 HLS preferred: DASH
    sections stall) to work\\highlights\\<id>\\hl.mp4, and cut HL_HEAD/HL_TAIL
    off its slates into hl.trim.mp4. Returns {"path", "duration", "url", "title"}."""
    info = video_info(url)
    vid = info.get("id") or render_clips.sanitize(url)[-40:]
    d = PROJECT / "work" / "highlights" / vid
    d.mkdir(parents=True, exist_ok=True)
    raw, trim = d / "hl.mp4", d / "hl.trim.mp4"
    if not raw.exists():
        cmd = [PY, "-m", "yt_dlp", "-f", "270/137/bv*[height<=1080]", "-S", "vcodec:h264",
               "-N", "4", "--remux-video", "mp4", "--no-playlist",
               "-o", str(d / "hl.%(ext)s"), url]
        for attempt in range(1, retries + 1):
            code, out = run_logged(cmd)
            if code == 0 and raw.exists():
                break
            if "HTTP Error 403" in out and attempt < retries:
                time.sleep(5)
                continue
            raise SystemExit(f"Highlights download failed (exit {code}).")
    if not trim.exists():
        full = media_duration(raw)
        if full - HL_HEAD - HL_TAIL < 15:
            raise SystemExit(f"Highlights video is only {full:.0f}s; need at least "
                             f"{15 + HL_HEAD + HL_TAIL:.0f}s.")
        subprocess.run(["ffmpeg", "-n", "-loglevel", "error", "-ss", f"{HL_HEAD}",
                        "-to", f"{full - HL_TAIL:.3f}", "-i", str(raw), "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-pix_fmt", "yuv420p", str(trim)], check=True)
    hl = {"path": trim, "duration": media_duration(trim), "url": url,
          "title": info.get("title") or ""}
    print(f"HIGHLIGHTS: {hl['title'] or url}  ({hl['duration']:.0f}s usable)", flush=True)
    return hl


def fetch_media(args, workdir):
    """Media for Whisper without downloading the video: a cached full video
    if one is already there, else audio only (yt-dlp bestaudio)."""
    have = [p for p in sorted(workdir.glob("source.*")) if p.suffix.lower() in VIDEO_EXTS]
    have += sorted(workdir.glob("audio.*"))
    if have:
        print(f"Using cached media {have[0]}", flush=True)
        return have[0].resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = [PY, "-m", "yt_dlp", "-f", "bestaudio/best", "--no-playlist",
           "-o", str(workdir / "audio.%(ext)s"), args.url]
    for attempt in range(1, args.retries + 1):
        code, out = run_logged(cmd)
        if code == 0:
            break
        if "HTTP Error 403" in out and attempt < args.retries:
            print(f"\nYouTube returned 403 - retrying ({attempt}/{args.retries - 1})...",
                  flush=True)
            time.sleep(5)
            continue
        raise SystemExit(f"Audio download failed (exit {code}).")
    have = sorted(workdir.glob("audio.*"))
    if not have:
        raise SystemExit("Audio download failed: no file produced.")
    return have[0].resolve()


def stage1_window(args, info, workdir, win):
    """Download only the padded window with yt-dlp (same format chopify uses),
    then get its transcript (YouTube captions or Whisper).
    Returns (transcript_info, offset_seconds, method)."""
    ps = max(0.0, win["start"] - PAD)
    pe = win["end"] + PAD          # yt-dlp clamps past the video end
    wdir = workdir / "windows" / f"{win['slug']}-{int(win['start'])}-{int(win['end'])}"
    cut = download_section(args, wdir, ps, pe)

    # captions are kept to [start, end] and shifted so ps (start - PAD) = 0;
    # whisper transcribes the whole cut and stage2 trims to the window
    tinfo = get_transcript(args, info, cut, wdir, workdir,
                           window=(win["start"], win["end"]), shift=ps)
    method = "section download + " + ("youtube captions" if tinfo["key"].startswith(
        "youtube") else "transcribe window")
    return tinfo, ps, method


def stage1(args, info, workdir):
    """Download the full video (same yt-dlp command as chopify's
    download_and_transcribe.download, retrying after YouTube HTTP 403), then
    get its transcript (YouTube captions or Whisper)."""
    workdir.mkdir(parents=True, exist_ok=True)
    vids = [p for p in sorted(workdir.glob("source.*")) if p.suffix.lower() in VIDEO_EXTS]
    if vids:
        print(f"Using cached video {vids[0]}", flush=True)
    else:
        cmd = [PY, "-m", "yt_dlp", "-f", "bestvideo*+bestaudio/best",
               "--merge-output-format", "mp4", "--no-playlist",
               "-o", str(workdir / "source.%(ext)s"), args.url]
        for attempt in range(1, args.retries + 1):
            code, out = run_logged(cmd)
            if code == 0:
                break
            if "HTTP Error 403" in out and attempt < args.retries:
                print(f"\nYouTube returned 403 - retrying ({attempt}/{args.retries - 1})...",
                      flush=True)
                time.sleep(5)
                continue
            raise SystemExit(f"Download failed (exit {code}).")
        vids = [p for p in sorted(workdir.glob("source.*")) if p.suffix.lower() in VIDEO_EXTS]
        if not vids:
            raise SystemExit("Download failed: no video file produced.")
    return get_transcript(args, info, vids[0].resolve(), workdir, workdir)


# ---------------------------------------------------------------------------
# name corrections
# ---------------------------------------------------------------------------
_CORR = sorted(((k.lower().split(), v) for k, v in CORRECTIONS.items()),
               key=lambda kv: -len(kv[0]))


def _split_token(word):
    """" Stillrotter's," -> (" ", "Stillrotter", "'s,")"""
    m = re.match(r"^(\s*[^\w\s]*)(.*?)((?:['’]s)?[^\w\s]*)$", word, re.S)
    return m.group(1), m.group(2), m.group(3)


_HYPHENS = re.compile(r"[-‐-―]+")


def split_hyphens(words):
    """Treat hyphens as spaces: ' Boone-Pickins,' -> ' Boone' ' Pickins,'
    (time spread by word length). Words that are only dashes are dropped."""
    out = []
    for w in words:
        if not _HYPHENS.search(w["word"]):
            out.append(w)
            continue
        toks = [t for t in _HYPHENS.sub(" ", w["word"]).split() if t]
        if toks:
            out += [dict(w, word=" " + x["word"], start=round(x["start"], 2),
                         end=round(x["end"], 2))
                    for x in _spread(toks, w["start"], w["end"])]
    return out


def apply_corrections(words):
    """Fix CORRECTIONS in a word list: hyphens count as spaces, then whole
    words, case insensitive, multi word keys matched across consecutive words,
    single word keys also matched with a trailing s (plural). Punctuation is
    kept and an all caps original stays all caps. Returns (words, log)."""
    words = split_hyphens(words)
    parts = [_split_token(w["word"]) for w in words]
    out, log, i = [], [], 0
    while i < len(words):
        for key, repl in _CORR:
            n = len(key)
            got = [p[1].lower() for p in parts[i:i + n]]
            plural = n == 1 and got == [key[0] + "s"]
            if got != key and not plural:
                continue
            orig = " ".join(p[1] for p in parts[i:i + n])
            new = repl + ("s" if plural else "")
            new = new.upper() if orig.isupper() else new
            span = words[i:i + n]
            if len(new.split()) == n:
                for w, p, r in zip(span, parts[i:i + n], new.split()):
                    out.append(dict(w, word=p[0] + r + p[2]))
            else:
                out.append({"word": parts[i][0] + new + parts[i + n - 1][2],
                            "start": span[0]["start"], "end": span[-1]["end"]})
            log.append({"from": orig, "to": new, "start": span[0]["start"],
                        "end": span[-1]["end"]})
            i += n
            break
        else:
            out.append(words[i])
            i += 1
    return out, log


def correct_text(text):
    text = _HYPHENS.sub(" ", text)
    for k, v in CORRECTIONS.items():
        plural = "(s)?" if len(k.split()) == 1 else "()"
        pat = r"(?<!\w)" + r"\s+".join(map(re.escape, k.split())) + plural + r"(?!\w)"
        text = re.sub(pat, lambda m: v + (m.group(1) or ""), text, flags=re.I)
    return text


SENT_PAUSE = 1.5       # sentences.txt also splits at pauses longer than this


def fmt_tenths(sec):
    """754.26 -> 12:34.3 (minutes may pass 59; parse_time reads it back)."""
    m, s = divmod(max(0.0, round(sec, 1)), 60)
    return f"{int(m)}:{s:04.1f}"


def split_sentences(words):
    """Word list -> lists of words, one per sentence (. ? ! or a long pause)."""
    out, cur = [], []
    for w in words:
        if cur and w["start"] - cur[-1]["end"] > SENT_PAUSE:
            out.append(cur)
            cur = []
        cur.append(w)
        if SENT_END.search(w["word"].strip()):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def prepare_words(transcript_path, run_dir, bounds=None, offset=0.0):
    """Copy the transcript into run_dir with CORRECTIONS applied (logged to
    corrections.json), and write words.txt plus sentences.txt (start-end of
    every sentence, to a tenth of a second) in video time.
    bounds=(lo, hi) keeps only words inside the window (cut time).
    Returns the corrected transcript dict."""
    run_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(Path(transcript_path).read_text(encoding="utf-8-sig"))
    if bounds is not None:
        lo, hi = bounds
        data["words"] = [w for w in data["words"]
                         if w["start"] >= lo and w["end"] <= hi]
        data["segments"] = [s for s in data["segments"]
                            if s["end"] > lo and s["start"] < hi]
    data["words"], fixes = apply_corrections(data["words"])
    for s in data["segments"]:
        s["text"] = correct_text(s["text"])
    for f in fixes:
        f["video_time"] = fmt_ts(f["start"] + offset)
        print(f"CORRECTION {f['video_time']}  {f['from']!r} -> {f['to']!r}", flush=True)
    print(f"Corrections applied: {len(fixes)}", flush=True)
    (run_dir / "corrections.json").write_text(
        json.dumps(fixes, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "transcript.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # readable word list for the spelling review (video timestamps)
    (run_dir / "words.txt").write_text(
        "".join(f"{fmt_ts(s['start'] + offset):>8}  {s['text'].strip()}\n"
                for s in segment_words(data["words"])), encoding="utf-8")
    (run_dir / "sentences.txt").write_text(
        "".join(f"{fmt_tenths(s[0]['start'] + offset):>8}-{fmt_tenths(s[-1]['end'] + offset)}"
                f"  {''.join(w['word'] for w in s).strip()}\n"
                for s in split_sentences(data["words"])), encoding="utf-8")
    return data


def stage2(transcript_path, run_dir, args, bounds=None, offset=0.0):
    """score via chopify into a fresh run folder (never overwrites segments.json).
    bounds=(lo, hi) keeps only transcript words inside the window (cut time),
    so the 2s padding gives whisper context but is never scored. CORRECTIONS
    are applied to this run's copy and logged to corrections.json."""
    prepare_words(transcript_path, run_dir, bounds, offset)
    cmd = [PY, PROJECT / "score_clips.py", run_dir,
           "--max-clips", args.max_clips, "--min-score", args.min_score]
    if args.clips:
        cmd += ["--clips", args.clips]
    code, _ = run_logged(cmd)
    if code != 0:
        raise SystemExit(f"Scoring failed (exit {code}).")
    return json.loads((run_dir / "segments.json").read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------------
# clean clip endings
# ---------------------------------------------------------------------------
END_LOOKBACK = 10.0    # look this far back from the end for a sentence end
MIN_CLIP = 15.0        # never trim a clip shorter than this
TURN_LOOKAHEAD = 20.0  # follow another speaker's turn this far past the end
TAIL = 0.2             # breath kept after the last word (never into the next)
SHOT_FPS = 4
OTHER_SHOT = 0.08      # 16x9 frame distance from the clip's main shot (0-1).
                       # Same shot measured <= 0.011, a cut to the host ~0.21.
SENT_END = re.compile(r"[.?!][\"')\]]?$")


def shot_frames(source, t0, t1, fps=SHOT_FPS):
    """(times, frames) - frames downscaled to 16x9 RGB, as float arrays."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.3f}",
                          "-i", str(source), "-t", f"{t1 - t0:.3f}", "-an",
                          "-vf", f"fps={fps},scale=16:9", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, 16 * 9 * 3).astype(np.float32)
    return max(0.0, t0) + np.arange(len(a)) / fps, a


class ShotTrack:
    """Which frames show the clip's main shot. The transcript has no speaker
    labels, so a camera change away from the shot that fills most of the clip
    is taken as a change to a different speaker (shows cut to whoever talks).
    Off-shot runs under 1s (flashes, graphics) are ignored."""
    def __init__(self, source, start, end):
        import numpy as np
        self.np, self.source = np, source
        self.times, frames = shot_frames(source, start, end + TURN_LOOKAHEAD)
        if not len(frames):
            raise ValueError("no frames decoded")
        self.main = np.median(frames[self.times <= end], axis=0)
        other = self.dist(frames) > OTHER_SHOT
        k = 0
        while k < len(other):
            j = k
            while j < len(other) and other[j] == other[k]:
                j += 1
            if other[k] and j - k < SHOT_FPS:
                other[k:j] = False
            k = j
        self.other = other

    def dist(self, frames):
        return self.np.abs(frames - self.main).mean(axis=1) / 255.0

    def idx(self, t):
        return int(min(len(self.other) - 1, max(0, round((t - self.times[0]) * SHOT_FPS))))

    def is_other(self, w):
        return bool(self.other[self.idx((w["start"] + w["end"]) / 2)])

    def cut_before(self, t):
        """Exact time (30 fps) the off-shot run containing t began."""
        k = self.idx(t)
        while k > 0 and self.other[k - 1]:
            k -= 1
        if k == 0:
            return float(self.times[0])
        ts, fr = shot_frames(self.source, float(self.times[k - 1]),
                             float(self.times[k]) + 0.05, fps=30)
        off = self.dist(fr) > OTHER_SHOT
        return float(ts[int(off.argmax())]) if off.any() else float(self.times[k])


def _end_after(w, words, limit):
    """End time just after word w: a short tail, never into the next word."""
    nxt = [x["start"] for x in words if x["start"] >= w["end"]]
    return min(w["end"] + TAIL, nxt[0] if nxt else w["end"] + TAIL, limit)


def clean_endings(segments, words, source, run_dir, offset):
    """Move clip ends to clean stops; edits segments in place and logs every
    clip's ending to trims.json.
    1. If the clip ends inside another speaker's turn that holds a question,
       end before that turn.
    2. Else, if it ends mid sentence, move the end back to the last . ? or !
       within the final END_LOOKBACK seconds, keeping >= MIN_CLIP seconds.
    3. Else keep the end; mid sentence with no clean option is flagged."""
    log = []
    for n, seg in enumerate(segments, 1):
        start, end = float(seg["start"]), float(seg["end"])
        # words that start before the end, as build_ass picks them
        cw = [w for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.3
              and w["start"] < end]
        if not cw:
            continue
        entry = {"clip": n, "hook": seg.get("hook"), "old_end": round(end, 2),
                 "old_end_hms": fmt_ts(end + offset), "action": "clean",
                 "reason": "already ends on a sentence end"}
        try:
            shots = ShotTrack(source, start, end)
        except Exception as e:                               # noqa: BLE001
            shots = None
            print(f"  clip {n}: shot check failed ({e}); speaker rule skipped", flush=True)

        if shots and shots.is_other(cw[-1]):
            j = len(cw) - 1
            while j > 0 and shots.is_other(cw[j - 1]):
                j -= 1
            after = [w for w in words if cw[-1]["start"] < w["start"] <= end + TURN_LOOKAHEAD]
            turn = list(cw[j:])
            for w in after:
                if not shots.is_other(w):
                    break
                turn.append(w)
            # a word that starts before the camera change belongs to the
            # speaker who was on screen (editors often cut on the last word)
            cut = shots.cut_before((turn[0]["start"] + turn[0]["end"]) / 2)
            while turn and turn[0]["start"] < cut + 0.1:
                turn.pop(0)
                j += 1
            turn_text = "".join(w["word"] for w in turn).strip()
            if 0 < j < len(cw) and any(w["word"].strip().endswith("?") for w in turn):
                last = cw[j - 1]
                # end at the camera change: caption word ends are estimates
                # and run past it (checked: the cut sat in the silence
                # between speakers). Only if the cut lands right at the
                # word's start, finish the word instead.
                limit = cut if cut >= last["start"] + 0.15 else last["end"]
                new_end = _end_after(last, words, limit)
                if new_end - start >= MIN_CLIP:
                    entry.update(action="trimmed", new_end=round(new_end, 2),
                                 reason="ends before a question from a different speaker "
                                        f"(camera change at {fmt_ts(cut + offset)}, "
                                        f"cut time {cut:.2f}s)",
                                 other_shot_seconds_kept=round(max(0.0, new_end - cut), 2),
                                 removed=turn_text[:300],
                                 note=None if SENT_END.search(last["word"].strip()) else
                                 "no punctuation at the new end; the main speaker's turn ends here")
                else:
                    entry.update(action="flagged", reason="ends in a question from a different "
                                 f"speaker, but ending before it leaves under {MIN_CLIP:.0f}s",
                                 removed=None)

        if entry["action"] == "clean" and not SENT_END.search(cw[-1]["word"].strip()):
            cands = [w for w in cw if SENT_END.search(w["word"].strip())
                     and end - END_LOOKBACK <= w["end"] < end
                     and w["end"] - start >= MIN_CLIP
                     and not (shots and shots.is_other(w))]
            if cands:
                w = cands[-1]
                new_end = _end_after(w, words, end)
                cut_words = [x for x in cw if x["start"] >= w["end"]]
                entry.update(action="trimmed", new_end=round(new_end, 2),
                             reason="ended mid sentence; moved back to the last sentence end",
                             removed="".join(x["word"] for x in cut_words).strip())
            else:
                entry.update(action="flagged", reason="ends mid sentence and no sentence end "
                             f"in the final {END_LOOKBACK:.0f}s keeps it {MIN_CLIP:.0f}s or longer")

        if entry["action"] == "trimmed":
            seg["end"] = entry["new_end"]
            entry["new_end_hms"] = fmt_ts(entry["new_end"] + offset)
            entry["new_length"] = round(entry["new_end"] - start, 2)
        kept = [w for w in cw if w["start"] < seg["end"]]     # as build_ass picks
        entry["last_line"] = " ".join(x["word"].strip() for x in kept[-25:])
        print(f"  clip {n} ending: {entry['action'].upper()} - {entry['reason']}"
              + (f"  {entry['old_end_hms']} -> {entry['new_end_hms']}"
                 if entry["action"] == "trimmed" else ""), flush=True)
        log.append(entry)
    (run_dir / "trims.json").write_text(json.dumps(log, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return log


def ass_inline(colour):
    """&H00BBGGRR -> &HBBGGRR& for an inline {\\c...} override."""
    return "&H" + colour[-6:] + "&"


def upper_outside_tags(text):
    """All caps, and no hyphens or dashes, in the caption text (tags untouched)."""
    return re.sub(r"(\{[^}]*\})|([^{]+)",
                  lambda m: m.group(1) or re.sub(r"[-‐-―]+", " ",
                                                 m.group(2)).upper(), text)


def restyle_ass(path, colour, font_size, margin_v, scale, align=2):
    """Rewrite chopify's hormozi .ass: Impact, bold+italic, caps, OKST colours.
    Word grouping and timing are untouched. align is the ASS numpad alignment:
    2 bottom centre (MarginV up from the bottom), 5 middle centre (split screen)."""
    old_hl = "{\\c" + render_clips.CAPTION_STYLES[BASE_STYLE]["highlight"] + "}"
    new_hl = "{\\c" + ass_inline(colour) + "}"
    outline = max(1, round(OUTLINE_PX * scale))
    shadow = max(1, round(SHADOW * scale))
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Style: Cap,"):
            line = (f"Style: Cap,{FONT_NAME},{font_size},&H00FFFFFF,&H000000FF,"
                    f"{OUTLINE_COLOUR},{SHADOW_COLOUR},{BOLD},{ITALIC},0,0,100,100,0,0,"
                    f"1,{outline},{shadow},{align},80,80,{margin_v},1")
        elif line.startswith("Dialogue:"):
            head, text = line.split(",", 9)[:9], line.split(",", 9)[9]
            text = upper_outside_tags(text.replace(old_hl, new_hl))
            line = ",".join(head) + "," + text
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def unique_base(out_dir, base):
    """<base>-logo.mp4, or <base>-2-logo.mp4, -3-... if taken (mp4 or captions)."""
    n = 1
    while True:
        b = base if n == 1 else f"{base}-{n}"
        if not any((out_dir / f"{b}-logo{ext}").exists()
                   for ext in (".mp4", ".txt", ".draft.txt")):
            return b + "-logo"
        n += 1


def rel(p):
    """Project-relative, forward-slash path (keeps drive colons out of filters)."""
    return Path(p).resolve().relative_to(PROJECT).as_posix()


# ---------------------------------------------------------------------------
# caption .txt (draft built only from the transcript and the video title)
# ---------------------------------------------------------------------------
def _clean(s):
    s = re.sub(r"\([^)]*\)", "", s)                  # drop "(10-6-2026)" etc
    s = re.sub(r"\s*[-\u2010-\u2015]+\s*", " ", s)  # no hyphens or dashes
    return re.sub(r"\s+", " ", s).strip()


def _sentences(words, start, end):
    text = "".join(w["word"] for w in words
                   if w["start"] >= start - 0.05 and w["end"] <= end + 0.3)
    return [s.strip() for s in re.findall(r"[^.!?]+[.!?]", text) if s.strip()]


def write_caption_txt(path, seg, words, title):
    sents = [_clean(s) for s in _sentences(words, seg["start"], seg["end"])]
    short = [s for s in sents if 5 <= len(s.split()) <= 16]
    pool = short or sents or [_clean(seg.get("hook", ""))]
    first = pool[0]
    rest = [s for s in pool[1:] if s != first]
    second = rest[0] if rest else ""
    third = f"From {_clean(title)}." if title else "Watch the full clip."
    body = " ".join(s for s in (second, third) if s)
    text = f"{first}\n\n{body}\n\n{CORE_HASHTAG}\n"
    path.write_text(_clean_keep_lines(text), encoding="utf-8")


def _clean_keep_lines(text):
    return "\n".join(re.sub(r"[-\u2010-\u2015]", " ", l) for l in text.split("\n"))


# ---------------------------------------------------------------------------
# stage 3: one ffmpeg pass per clip
# ---------------------------------------------------------------------------
def render_one(i, seg, words, source, W, H, run_dir, out_dir, colour, preview,
               title, name_suffix="", hl=None):
    """hl: fetch_highlights() result for the split screen layout, or None."""
    full_w, full_h, font_size, margin_v = render_clips.ASPECTS[ASPECT]
    if preview:
        tw, th = render_clips.PREVIEW[ASPECT]
        scale = th / float(full_h)
        font_size = max(12, round(font_size * scale))
        margin_v = max(10, round(margin_v * scale))
    else:
        tw, th, scale = full_w, full_h, 1.0
    logo_w = max(2, round(LOGO_W * tw / FULL_W) // 2 * 2)
    logo_m = round(LOGO_MARGIN * tw / FULL_W)

    start, end = float(seg["start"]), float(seg["end"])
    dur = end - start
    top_h = th // 4 * 2 if hl else th             # speaker gets the top half
    if hl:
        cw, ch, x0, _ = render_clips.crop_for(W, int(H * SPEAKER_KEEP), tw, top_h)
        y0 = 0
    else:
        cw, ch, x0, y0 = render_clips.crop_for(W, H, tw, top_h)

    ass = run_dir / f"clip{i}.ass"
    render_clips.build_ass(words, start, end, ass, tw, th, font_size, margin_v,
                           BASE_STYLE)
    if hl:
        restyle_ass(ass, colour, font_size, 0, scale, align=5)
    else:
        restyle_ass(ass, colour, font_size, margin_v, scale)

    if cw < W * 0.95 and SPEAKER_X is not None:
        # Locked camera at a fixed position given by --speaker-x (still no tracking).
        x0 = max(0, min(int(round(SPEAKER_X * W - cw / 2.0)), W - cw))
        crop = f"crop={cw}:{ch}:{x0}:{y0}"
    elif cw < W * 0.95:
        # Locked camera: one fixed crop centred on the speaker's median face
        # position for the whole clip (no head following). Centre crop if no
        # face is found. YuNet model lives next to the project.
        faces = sorted(c for _, c in render_clips.detect_track(
            source, start, dur, W, PROJECT / "work") if c is not None)
        if faces:
            x0 = max(0, min(int(round(faces[len(faces) // 2] - cw / 2.0)), W - cw))
        crop = f"crop={cw}:{ch}:{x0}:{y0}"
    else:
        crop = f"crop={cw}:{ch}:{x0}:{y0}"

    def graph(subs):
        if hl:
            # highlights fill the bottom half (cover crop), muted, at the speaker's fps
            bot_h = th - top_h
            seam = max(2, round(SEAM_PX * tw / FULL_W) // 2 * 2)
            bgr = colour[-6:]                     # &HAABBGGRR -> RRGGBB
            g = (f"[0:v]{crop},scale={tw}:{top_h},setsar=1[top];"
                 f"[2:v]scale={tw}:{bot_h}:force_original_aspect_ratio=increase,"
                 f"crop={tw}:{bot_h},setsar=1,fps={media_fps(source)}[bot];"
                 f"[top][bot]vstack=shortest=1,drawbox=x=0:y={top_h - seam // 2}:"
                 f"w={tw}:h={seam}:color=0x{bgr[4:6]}{bgr[2:4]}{bgr[0:2]}:t=fill")
        else:
            g = f"[0:v]{crop},scale={tw}:{th}"
        if subs:
            g += f",subtitles=filename={rel(ass)}:fontsdir={rel(FONTS_DIR)}"
        return (g + f"[v];[1:v]scale={logo_w}:-2[lg];"
                f"[v][lg]overlay=W-w-{logo_m}:{logo_m}[out]")

    hl_in = []
    if hl:
        hl_off = ((i - 1) * HL_STEP) % hl["duration"]
        hl_in = ["-ss", f"{hl_off:.3f}", "-stream_loop", "-1", "-an", "-i", str(hl["path"])]

    hook = render_clips.sanitize(seg.get("hook", "clip"))
    base = unique_base(out_dir, hook + name_suffix + ("-preview" if preview else ""))
    out = out_dir / f"{base}.mp4"
    cmd = ["ffmpeg", "-n", "-ss", f"{start:.3f}", "-i", str(source),
           "-i", rel(LOGO), *hl_in, "-t", f"{dur:.3f}",
           "-filter_complex", graph(True), "-map", "[out]", "-map", "0:a?",
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-ar", "48000",  # loudnorm upsamples; Instagram Reels take 48 kHz max
           "-movflags", "+faststart", str(out)]
    print(f"\n--- Clip {i}: {seg.get('hook', '')!r} "
          f"({start:.2f}-{end:.2f}, score {seg.get('overall')}) ---", flush=True)
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=str(PROJECT))

    # caption free mid clip frame (same layout and logo) for make_cover.py
    if not preview:
        mid = dur / 2.0
        frame_hl = []
        if hl:
            frame_hl = ["-ss", f"{(hl_off + mid) % hl['duration']:.3f}", "-an",
                        "-i", str(hl["path"])]
        subprocess.run(["ffmpeg", "-n", "-loglevel", "error", "-ss", f"{start + mid:.3f}",
                        "-i", str(source), "-i", rel(LOGO), *frame_hl,
                        "-filter_complex", graph(False), "-map", "[out]",
                        "-frames:v", "1", "-q:v", "2", str(out_dir / f"{base}.frame.jpg")],
                       check=True, cwd=str(PROJECT))

    # draft only; the final <base>.txt is written later by the okst-clip skill
    txt = out_dir / f"{base}.draft.txt"
    write_caption_txt(txt, seg, words, title)
    print(f"SAVED {out}\n      {txt}", flush=True)
    return out


# ---------------------------------------------------------------------------
# --ranges: render hand picked clips (no scoring)
# ---------------------------------------------------------------------------
RANGE_MIN, RANGE_MAX = 15.0, 60.0      # outside this only warns
HOOK_WORDS = 7                         # file name = first words of the clip


def parse_ranges(text):
    """"2:00-2:40, 5:10.5-5:55" -> [(120.0, 160.0), (310.5, 355.0)]."""
    out = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        m = re.fullmatch(r"([\d:.]+)\s*-\s*([\d:.]+)", part)
        if not m:
            raise SystemExit(f"bad range {part!r}: use mm:ss-mm:ss")
        try:
            a, b = parse_time(m.group(1)), parse_time(m.group(2))
        except argparse.ArgumentTypeError as e:
            raise SystemExit(f"bad range {part!r}: {e}")
        if b <= a:
            raise SystemExit(f"bad range {part!r}: end is not after start")
        out.append((a, b))
    if not out:
        raise SystemExit("--ranges is empty")
    return out


def snap_start(t, words):
    """Start of the word under t, or of the next word when t is in a gap."""
    for w in words:
        if w["end"] > t:
            return w["start"]
    raise SystemExit(f"no words after {fmt_ts(t)}")


def snap_end(t, words):
    """Finish a word that t cuts in half; otherwise keep t."""
    for w in words:
        if w["start"] < t < w["end"]:
            return _end_after(w, words, float("inf"))
    return t


def render_ranges(args, ranges, words, workdir, run_dir, out_dir, title, duration,
                  hl=None):
    """Download each range (padded PAD seconds each side) and render it with
    the usual crop, captions, logo, loudnorm and clean ending check.
    `words` are corrected, in video time. Logs every clip to ranges.json."""
    log, saved = [], []
    for i, (a, b) in enumerate(ranges, 1):
        t = time.time()
        s0, s1 = snap_start(a, words), snap_end(b, words)
        if duration and s1 > duration + 0.5:
            raise SystemExit(f"range {i} ends past the video end ({fmt_ts(duration)})")
        if not RANGE_MIN <= s1 - s0 <= RANGE_MAX:
            print(f"WARNING range {i} is {s1 - s0:.1f}s (expected {RANGE_MIN:.0f} to "
                  f"{RANGE_MAX:.0f}s)", flush=True)
        ps, pe = max(0.0, s0 - PAD), s1 + PAD
        cut = download_section(args, workdir / "ranges" / f"{ps:.2f}-{pe:.2f}", ps, pe)
        cw = [dict(w, start=round(w["start"] - ps, 3), end=round(w["end"] - ps, 3))
              for w in words if w["start"] >= ps - 0.01 and w["end"] <= pe + 0.01]
        text = "".join(w["word"] for w in words
                       if w["start"] >= s0 - 0.01 and w["end"] <= s1 + 0.3)
        seg = {"start": s0 - ps, "end": s1 - ps,
               "hook": " ".join(_clean(text).split()[:HOOK_WORDS]) or f"clip {i}",
               "overall": None}
        sub = run_dir / f"range{i}"
        sub.mkdir()
        trims = clean_endings([seg], cw, cut, sub, ps)
        W, H = render_clips.ffprobe_dims(cut)
        out = render_one(i, seg, cw, cut, W, H, sub, out_dir, COLORS[args.color],
                         args.preview, title, hl=hl)
        saved.append(out)
        log.append({"clip": i, "file": out.name,
                    "highlights": hl["url"] if hl else None,
                    "requested": f"{fmt_tenths(a)}-{fmt_tenths(b)}",
                    "snapped": f"{fmt_tenths(s0)}-{fmt_tenths(s1)}",
                    "final": f"{fmt_tenths(seg['start'] + ps)}-{fmt_tenths(seg['end'] + ps)}",
                    "length": round(seg["end"] - seg["start"], 2),
                    "ending": trims[0]["action"] if trims else None,
                    "ending_reason": trims[0]["reason"] if trims else None,
                    "render_seconds": round(time.time() - t, 1)})
        (run_dir / "ranges.json").write_text(json.dumps(log, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    return saved, log


def main():
    t0 = time.time()
    tee = sys.stdout = Tee(sys.stdout)
    ap = argparse.ArgumentParser(
        description="OKST clips: chopify + Impact captions + logo, 9:16, loudnorm.")
    ap.add_argument("url")
    ap.add_argument("--color", default=DEFAULT_COLOR, choices=list(COLORS))
    ap.add_argument("--preview", action="store_true", help="480p drafts")
    ap.add_argument("--clips", default=None, metavar="1,3,5",
                    help="render only these numbered candidates")
    ap.add_argument("--chapter", default=None, metavar="TEXT",
                    help="clip only the chapter whose title contains TEXT "
                         "(case insensitive)")
    ap.add_argument("--start", type=parse_time, default=None, metavar="TIME",
                    help="window start: ss, mm:ss or hh:mm:ss (default video start)")
    ap.add_argument("--end", type=parse_time, default=None, metavar="TIME",
                    help="window end: ss, mm:ss or hh:mm:ss (default video end)")
    ap.add_argument("--min-score", type=float, default=6.5)
    ap.add_argument("--max-clips", type=int, default=10)
    ap.add_argument("--transcriber", default="auto", choices=["auto", "youtube", "whisper"],
                    help="auto: YouTube's English captions (human made preferred), "
                         "falling back to Whisper when there are none, they average "
                         f"under {MIN_WPM} words per minute, or the fetch fails; "
                         "youtube: captions only, stops with the reason in those "
                         "cases; whisper: always Whisper")
    ap.add_argument("--whisper-model", default="small",
                    help="faster-whisper model when Whisper is used (default small)")
    ap.add_argument("--workdir", default=None,
                    help="download/transcript folder (default work\\<video id>)")
    ap.add_argument("--out", default=None,
                    help=f"output folder (default {DEFAULT_OUT}, plus "
                         "\\<video-slug>-<window-slug> when a window is used)")
    ap.add_argument("--retries", type=int, default=3,
                    help="download attempts when YouTube returns 403")
    ap.add_argument("--transcript-only", action="store_true",
                    help="fetch the transcript (same source rules), apply CORRECTIONS, "
                         "write words.txt and sentences.txt in video time, and stop. "
                         "No video download (Whisper fallback fetches audio only)")
    ap.add_argument("--ranges", default=None, metavar="mm:ss-mm:ss,...",
                    help="skip scoring: download only these video time ranges (2s "
                         "padding, H.264 preferred) and render exactly these clips; "
                         "each start snaps to the beginning of a word")
    ap.add_argument("--highlights", default=None, metavar="URL",
                    help="split screen: speaker on top, this highlights video below "
                         "(muted, looped, each clip starting further in)")
    ap.add_argument("--speaker-x", type=float, default=None, metavar="0-1",
                    help="lock the vertical crop at this fraction of the source width "
                         "instead of the median detected face (multi person tables)")
    args = ap.parse_args()
    global SPEAKER_X
    if args.speaker_x is not None:
        if not 0.0 <= args.speaker_x <= 1.0:
            ap.error("--speaker-x must be between 0 and 1")
        SPEAKER_X = args.speaker_x
    if args.highlights and args.transcript_only:
        ap.error("--highlights needs a render; drop --transcript-only")
    if args.chapter is not None and (args.start is not None or args.end is not None):
        ap.error("--chapter cannot be combined with --start or --end")
    if args.ranges is not None and args.transcript_only:
        ap.error("--ranges cannot be combined with --transcript-only")
    if args.ranges is not None and (args.chapter is not None or args.start is not None
                                    or args.end is not None):
        ap.error("--ranges are video times; do not combine with --chapter, --start or --end")
    ranges = parse_ranges(args.ranges) if args.ranges is not None else None

    check_chopify_api()
    problems = chopify.check_environment()
    if not FONT_SRC.exists() and not (FONTS_DIR / "impact.ttf").exists():
        problems.append(f"Impact font missing: {FONT_SRC}")
    if not LOGO.exists():
        problems.append(f"Logo missing: {LOGO}")
    if problems:
        print("PREFLIGHT FAILED:", *("  - " + p for p in problems), sep="\n")
        sys.exit(1)
    FONTS_DIR.mkdir(exist_ok=True)
    if not (FONTS_DIR / "impact.ttf").exists():
        shutil.copy2(FONT_SRC, FONTS_DIR / "impact.ttf")
    hl = fetch_highlights(args.highlights, args.retries) if args.highlights else None

    info = video_info(args.url)
    vid = info.get("id") or render_clips.sanitize(args.url)[-40:]
    title = info.get("title") or ""
    win = resolve_window(args, info)
    if win:
        label = f"chapter {win['chapter']!r}" if win["chapter"] else "custom window"
        print(f"\nWINDOW: {label}  {fmt_ts(win['start'])} -> {fmt_ts(win['end'])}  "
              f"({win['end'] - win['start']:.0f}s)\n", flush=True)

    workdir = Path(args.workdir).resolve() if args.workdir \
        else (PROJECT / "work" / vid)
    slug = run_slug(render_clips.sanitize(title or vid, maxlen=500), win["slug"])         if win else None
    if args.out:
        out_dir = Path(args.out).resolve()
    elif win:
        out_dir = DEFAULT_OUT / slug
    elif ranges:
        out_dir = DEFAULT_OUT / strip_filler(cap_slug(
            render_clips.sanitize(title or vid, maxlen=500)))
    else:
        out_dir = DEFAULT_OUT
    if not args.transcript_only:
        out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = slug or ("ranges" if ranges else None)
    if args.transcript_only:
        tag = f"transcript-{slug}" if slug else "transcript"
    run_dir = workdir / "okst_runs" / (f"{stamp}-{tag}" if tag else stamp)
    run_dir.mkdir(parents=True, exist_ok=False)
    tee.attach(run_dir / "run.log")
    print(f"Run folder: {run_dir}" + ("" if args.transcript_only
                                      else f"\nOutput folder: {out_dir}"), flush=True)

    if args.transcript_only or ranges:
        # full video timeline transcript, shared with full video runs' cache
        tinfo = get_transcript(args, info, workdir / "source.mp4", workdir, workdir,
                               media_fetch=lambda: fetch_media(args, workdir))
        (run_dir / "transcript_source.json").write_text(json.dumps(
            {"source": tinfo["key"], "reason": tinfo["reason"], "seconds": tinfo["seconds"],
             "file": str(tinfo["path"])}, ensure_ascii=False, indent=2), encoding="utf-8")
        bounds = (win["start"], win["end"]) if win else None
        data = prepare_words(tinfo["path"], run_dir, bounds)
        fixes = json.loads((run_dir / "corrections.json").read_text(encoding="utf-8"))
        if args.transcript_only:
            print(f"\nTRANSCRIPT ONLY DONE  ({time.time() - t0:.0f}s)")
            if win:
                print(f"  window: {win['chapter'] or 'custom'}  {fmt_ts(win['start'])} -> "
                      f"{fmt_ts(win['end'])}")
        else:
            saved, log = render_ranges(args, ranges, data["words"], workdir, run_dir,
                                       out_dir, title, info.get("duration"), hl)
            print(f"\nOKST DONE: {len(saved)} clip(s) -> {out_dir}  (color={args.color})")
            for c in log:
                print(f"  clip {c['clip']}: {c['final']} ({c['length']:.1f}s)  ending "
                      f"{c['ending']}  render {c['render_seconds']:.0f}s  {c['file']}")
        print(f"  transcript: {tinfo['key']} - {tinfo['reason']}")
        print(f"  corrections: {len(fixes)}"
              + "".join(f"\n    {f['video_time']}  {f['from']} -> {f['to']}" for f in fixes))
        print(f"  words: {run_dir / 'words.txt'}\n  sentences: {run_dir / 'sentences.txt'}")
        print(f"  run time: {time.time() - t0:.0f}s")
        return

    if win:
        tinfo, offset, method = stage1_window(args, info, workdir, win)
        lo, hi = win["start"] - offset, win["end"] - offset      # cut time
        segments = stage2(tinfo["path"], run_dir, args, bounds=(lo, hi), offset=offset)
        kept = [s for s in segments if s["start"] >= lo - 1e-3 and s["end"] <= hi + 1e-3]
        for s in segments:
            if s not in kept:
                print(f"Dropped clip outside window: {s.get('hook')!r} "
                      f"({s['start'] + offset:.1f}-{s['end'] + offset:.1f}s)", flush=True)
        segments = kept
        name_suffix = "-" + win["slug"]
    else:
        tinfo = stage1(args, info, workdir)
        segments = stage2(tinfo["path"], run_dir, args)
        offset, name_suffix = 0.0, ""
    (run_dir / "transcript_source.json").write_text(json.dumps(
        {"source": tinfo["key"], "reason": tinfo["reason"], "seconds": tinfo["seconds"],
         "file": str(tinfo["path"])}, ensure_ascii=False, indent=2), encoding="utf-8")
    fixes = json.loads((run_dir / "corrections.json").read_text(encoding="utf-8"))

    transcript = json.loads((run_dir / "transcript.json").read_text(encoding="utf-8-sig"))
    words = transcript["words"]
    source = Path(transcript["video"])
    if not source.exists():
        cands = sorted(workdir.glob("source.*"))
        source = cands[0] if cands else source
    source = source.resolve()
    W, H = render_clips.ffprobe_dims(source)

    trims = clean_endings(segments, words, source, run_dir, offset)

    if win:
        log = {"video_id": vid, "title": title, "url": args.url,
               "chapter": win["chapter"], "start": win["start"], "end": win["end"],
               "start_hms": fmt_ts(win["start"]), "end_hms": fmt_ts(win["end"]),
               "pad_seconds": PAD, "method": method,
               "transcript_source": tinfo["key"], "transcript_reason": tinfo["reason"],
               "transcript_seconds": tinfo["seconds"], "transcript_file": str(tinfo["path"]),
               "cut_offset": offset, "slug": slug, "output_folder": str(out_dir),
               "clips": [{"hook": s.get("hook"), "score": s.get("overall"),
                          "cut_start": round(s["start"], 2),
                          "cut_end": round(s["end"], 2),
                          "video_start": round(s["start"] + offset, 2),
                          "video_end": round(s["end"] + offset, 2),
                          "video_start_hms": fmt_ts(s["start"] + offset),
                          "video_end_hms": fmt_ts(s["end"] + offset),
                          "ending": next((t["action"] for t in trims if t["clip"] == n), None)}
                         for n, s in enumerate(segments, 1)]}
        (run_dir / "window.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        for n, c in enumerate(log["clips"], 1):
            print(f"  clip {n}: cut {c['cut_start']:.2f}-{c['cut_end']:.2f}s = video "
                  f"{c['video_start_hms']}-{c['video_end_hms']}  {c['hook']!r}", flush=True)

    saved = []
    for i, seg in enumerate(segments, 1):
        try:
            saved.append(render_one(i, seg, words, source, W, H, run_dir, out_dir,
                                    COLORS[args.color], args.preview, title,
                                    name_suffix, hl))
        except subprocess.CalledProcessError as e:
            print(f"ERROR rendering clip {i}: {e}", flush=True)
    print(f"\nOKST DONE: {len(saved)} clip(s) -> {out_dir}  (color={args.color})")
    if win:
        print(f"  window: {win['chapter'] or 'custom'}  {fmt_ts(win['start'])} -> "
              f"{fmt_ts(win['end'])}  (log: {run_dir / 'window.json'})")
    print(f"  transcript: {tinfo['key']} in {tinfo['seconds']:.1f}s - {tinfo['reason']}")
    print(f"  corrections: {len(fixes)}"
          + "".join(f"\n    {f['video_time']}  {f['from']} -> {f['to']}" for f in fixes))
    for t in trims:
        print(f"  clip {t['clip']} ending: {t['action']} - {t['reason']}"
              + (f" ({t['old_end_hms']} -> {t['new_end_hms']})" if t["action"] == "trimmed" else ""))
    for s in saved:
        print("  ", s)
    print(f"  run time: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
