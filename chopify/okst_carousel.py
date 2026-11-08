"""Fetch and prepare an OKST carousel repost (Instagram photo posts, carousels, or images from the inbox).

Usage:
    doppler run -p okst -c dev -- .venv\\Scripts\\python okst_carousel.py fetch "<instagram post url>" [--handle @name]
    .venv\\Scripts\\python okst_carousel.py inbox "<folder or image files...>" --handle @name [--name <id>]

fetch asks Apify's Instagram Post Scraper for the post (no Instagram login, no cookies). When Apify fails (a 403 when the
  Free plan's monthly credit is used up, or any other error), it falls back to yt-dlp: video slides with their audio, image
  slides from the full size image URL. If that cannot get every slide complete (a slide missing, a video with no audio
  track, an image only available small), it prints HOLD lines, writes hold.json instead of carousel.json, and exits 4.
  A partial carousel is never prepared.
  Single video posts and reels print "TYPE video" and stop, so the normal yt-dlp video flow handles them.
  Photo posts and carousels download every slide in order into output\\repost\\<slug>\\.
inbox copies (never moves) images and videos from OKST-Inbox into output\\repost\\<slug>\\.

Both then prepare the slides:
  images become baseline JPEG (the only image format the Instagram API takes), pixels and framing unchanged;
  videos stay as is when already H.264 + AAC mp4, otherwise they are re-encoded with no change to size or length.
They write carousel.json (slide order, sizes, flags) and print the slug and folder.
Nothing is ever cropped, trimmed, covered, or overwritten.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

try:                      # HEIC support for inbox photos; App Control blocks it on one PC, the cloud has it
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
REPOST_ROOT = ROOT / "output" / "repost"
APIFY_ACTOR = "apify~instagram-post-scraper"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
MAX_SLIDES = 10                 # Instagram API carousel limit
MIN_RATIO, MAX_RATIO = 0.75, 1.91  # Instagram feed limits: 3:4 portrait (since May 2025) to 1.91:1 landscape
VIDEO_MIN_S, VIDEO_MAX_S = 3.0, 60.0
HOLD_EXIT = 4
MIN_IMAGE_W = 1000              # yt-dlp fallback: a smaller image means only a preview was available


# ---------- helpers ----------

def slugify(text, limit=60):
    s = re.sub(r"[^a-z0-9_]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-")


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def shortcode_from_url(url):
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def clean_ig_url(url):
    code = shortcode_from_url(url)
    if not code:
        sys.exit(f"Not an Instagram post URL: {url}")
    return f"https://www.instagram.com/p/{code}/"


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=duration,format_name",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def unique_path(path):
    """Never overwrite: add a counter when the name is taken."""
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
    sys.exit(f"Too many copies of {path.name}")


# ---------- Apify ----------

class ApifyUnavailable(Exception):
    pass


def apify_fetch(url):
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise ApifyUnavailable("APIFY_TOKEN is not set")
    body = json.dumps({"username": [url], "dataDetailLevel": "detailedData"}).encode()
    api = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?timeout=120"
    last_err = None
    for attempt in range(2):
        req = urllib.request.Request(api, data=body, method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "okst-carousel"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                items = json.load(r)
            break
        except Exception as e:  # one retry, same as the video flow
            last_err = e
            time.sleep(5)
    else:
        raise ApifyUnavailable(f"Apify request failed twice: {last_err}")
    items = [i for i in items if not i.get("error")]
    if not items:
        sys.exit("Apify returned no post. It may be private, deleted, or age restricted. "
                 "Save the slides from your phone to OKST-Inbox and use the inbox flow.")
    return items[0]


def slides_from_post(post):
    """Ordered list of {type, url} for every slide."""
    kids = post.get("childPosts") or []
    if kids:
        out = []
        for k in kids:
            if k.get("type") == "Video" and k.get("videoUrl"):
                out.append({"type": "video", "url": k["videoUrl"]})
            elif k.get("displayUrl"):
                out.append({"type": "image", "url": k["displayUrl"]})
        if out:
            return out
    if post.get("type") == "Sidecar" and post.get("images"):
        return [{"type": "image", "url": u} for u in post["images"]]
    if post.get("displayUrl"):
        return [{"type": "image", "url": post["displayUrl"]}]
    return []


# ---------- yt-dlp fallback ----------

def ytdlp_cmd():
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        return ["yt-dlp"]


def ytdlp_info(url):
    """The post as yt-dlp sees it (no login, no cookies). Image slides have no formats, only thumbnails."""
    err = ""
    for attempt in range(2):
        r = subprocess.run(ytdlp_cmd() + ["--skip-download", "--ignore-no-formats-error", "--dump-single-json",
                                          "--no-warnings", url], capture_output=True, text=True, timeout=240)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        err = (r.stderr.strip().splitlines() or ["no output"])[-1]
        time.sleep(5)
    sys.exit(f"Apify failed and yt-dlp could not read the post either: {err}")


def ytdlp_slides(info):
    """(entries, expected count). A single photo post is one entry; a single video post has no entries."""
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        return entries, int(info.get("playlist_count") or len(entries))
    return [info], 1


def ytdlp_video(entry, dst, work):
    """Download one video slide with its audio. Returns (ok, reason)."""
    meta = work / f"{dst.stem}.yt.json"
    meta.write_text(json.dumps(entry), encoding="utf-8")
    r = subprocess.run(ytdlp_cmd() + ["--load-info-json", str(meta), "-f", "bv*+ba/b", "--merge-output-format", "mp4",
                                      "--no-warnings", "-o", str(dst)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not dst.exists():
        return False, "the video would not download: " + ((r.stderr.strip().splitlines() or ["?"])[-1])[:200]
    kinds = {st.get("codec_type") for st in ffprobe(dst).get("streams", [])}
    if "video" not in kinds:
        return False, "the download has no video stream"
    if "audio" not in kinds:
        return False, "yt-dlp only got the picture, no audio track"
    return True, ""


def ytdlp_image(entry, dst):
    """Download one image slide from its full size URL (the last thumbnail). Returns (ok, reason)."""
    thumbs = [t for t in entry.get("thumbnails") or [] if t.get("url")]
    if not thumbs:
        return False, "no image URL"
    try:
        dst.write_bytes(http_get(thumbs[-1]["url"], timeout=120))
        with Image.open(dst) as im:
            w, h = im.size
    except Exception as e:
        return False, f"the image would not download: {e}"[:200]
    if w < MIN_IMAGE_W:
        return False, f"only a {w}x{h} preview was available"
    return True, ""


def hold(out_dir, url, reasons):
    for r in reasons:
        print(f"HOLD {r}")
    (out_dir / "hold.json").write_text(json.dumps({"source": url, "fetched_with": "yt-dlp (Apify unavailable)",
                                                   "reasons": reasons}, indent=2), encoding="utf-8")
    print(f"FOLDER {out_dir}")
    sys.exit(HOLD_EXIT)


def fetch_with_ytdlp(url, given, apify_error):
    print(f"APIFY_FAILED {apify_error}. Falling back to yt-dlp.")
    info = ytdlp_info(url)
    owner = info.get("channel") or ""
    if given and owner and given.lower() != owner.lower():
        print(f"HANDLE_CONFLICT post owner @{owner} vs given @{given}")
    creator = given or owner
    if not creator:
        sys.exit("No creator handle found. Rerun with --handle @name.")
    code = shortcode_from_url(url)
    playlist = info.get("_type") == "playlist"
    if not playlist and info.get("formats"):
        print(f"POST Video by @{owner or '?'} ({code})")
        print("TYPE video")
        return
    print(f"POST {'Sidecar' if playlist else 'Image'} by @{owner or '?'} ({code})")
    entries, expected = ytdlp_slides(info)

    slug = slugify(f"repost-{creator}-{code}")
    out_dir = REPOST_ROOT / slug
    raw_dir = out_dir / "source"
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta = {"url": url, "shortCode": code, "type": "Sidecar" if playlist else "Image",
            "caption": info.get("description"), "ownerUsername": owner, "ownerFullName": info.get("uploader"),
            "timestamp": info.get("timestamp"), "fetched_with": "yt-dlp (Apify unavailable)",
            "taggedUsers": None, "coauthors": None, "note": "yt-dlp does not list tagged users or coauthors"}
    (out_dir / f"{code}.info.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    reasons = []
    if len(entries) != expected:
        reasons.append(f"the post has {expected} slides but yt-dlp listed {len(entries)}")
    raw = []
    for n, e in enumerate(entries, start=1):
        if e.get("formats"):
            dst, kind = raw_dir / f"{code}_{n:02d}.mp4", "video"
            ok, why = (True, "") if dst.exists() else ytdlp_video(e, dst, raw_dir)
        else:
            dst, kind = raw_dir / f"{code}_{n:02d}.jpg", "image"
            ok, why = (True, "") if dst.exists() else ytdlp_image(e, dst)
        if not ok:
            reasons.append(f"slide {n} ({kind}): {why}")
            dst.unlink(missing_ok=True)
        raw.append((dst, kind))
    if not raw:
        reasons.append("no slides")
    if reasons:
        hold(out_dir, url, reasons)

    slides = build_slides(raw, out_dir, slug)
    manifest = write_manifest(out_dir, slug, url, creator, slides,
                              {"info_json": f"{code}.info.json", "owner": owner, "fetched_with": "yt-dlp"})
    manifest["flags"].append("Fetched with yt-dlp because Apify failed: tagged users and coauthors were not checked.")
    (out_dir / "carousel.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report(out_dir, manifest)


# ---------- preparing slides ----------

def prepare_image(src, dst):
    """Write a baseline JPEG with the same pixels and framing. Returns (width, height)."""
    with Image.open(src) as im:
        fmt = (im.format or "").upper()
        im = ImageOps.exif_transpose(im)  # bake phone rotation in so Instagram shows it upright
        w, h = im.size
        if fmt == "JPEG" and src.suffix.lower() in (".jpg", ".jpeg") and im.mode in ("RGB", "L") \
                and not getattr(im, "is_animated", False) and _no_rotation(src):
            shutil.copy2(src, dst)
            return w, h
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im.convert("RGB").save(dst, "JPEG", quality=95, subsampling=0, optimize=True)
        return w, h


def _no_rotation(path):
    with Image.open(path) as im:
        return im.getexif().get(0x0112, 1) in (1, None)


def prepare_video(src, dst):
    """Keep H.264 + AAC mp4 as is, else re-encode with no change to size, framing, or length."""
    info = ffprobe(src)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        sys.exit(f"No video stream in {src.name}")
    is_mp4 = "mp4" in info.get("format", {}).get("format_name", "")
    if is_mp4 and v.get("codec_name") == "h264" and (a is None or a.get("codec_name") == "aac"):
        shutil.copy2(src, dst)
    else:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-n", "-i", str(src),
                        "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)],
                       check=True)
    info = ffprobe(dst)
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    return int(v["width"]), int(v["height"]), float(info["format"].get("duration") or 0)


def build_slides(raw_files, out_dir, slug):
    """raw_files: ordered list of (path, kind). Writes <slug>_NN.jpg / .mp4 and returns slide dicts."""
    slides = []
    for n, (src, kind) in enumerate(raw_files, start=1):
        if kind == "image":
            dst = unique_path(out_dir / f"{slug}_{n:02d}.jpg")
            w, h = prepare_image(src, dst)
            slides.append({"n": n, "file": dst.name, "type": "image", "width": w, "height": h})
        else:
            dst = unique_path(out_dir / f"{slug}_{n:02d}.mp4")
            w, h, dur = prepare_video(src, dst)
            slides.append({"n": n, "file": dst.name, "type": "video", "width": w, "height": h,
                           "duration": round(dur, 2)})
    return slides


def slide_flags(slides):
    flags = []
    if len(slides) > MAX_SLIDES:
        flags.append(f"{len(slides)} slides. Instagram allows {MAX_SLIDES}; only the first {MAX_SLIDES} can post.")
    if not slides:
        return ["No slides."]
    first = slides[0]["width"] / slides[0]["height"]
    for s in slides:
        r = s["width"] / s["height"]
        if r < MIN_RATIO - 0.005 or r > MAX_RATIO + 0.005:
            flags.append(f"Slide {s['n']} is {s['width']}x{s['height']}, outside Instagram's 3:4 to 1.91:1 range. "
                         "The API will reject it.")
        elif abs(r - first) > 0.01:
            flags.append(f"Slide {s['n']} is a different shape than slide 1. Instagram crops every slide to slide 1's shape.")
        if s["type"] == "video" and not (VIDEO_MIN_S <= s.get("duration", 0) <= VIDEO_MAX_S):
            flags.append(f"Slide {s['n']} video is {s.get('duration')}s. Carousel videos must be 3 to 60 seconds.")
    return flags


def write_manifest(out_dir, slug, source, creator, slides, extra):
    manifest = {"kind": "carousel", "slug": slug, "source": source, "creator": creator,
                "slides": slides, "flags": slide_flags(slides), **extra}
    (out_dir / "carousel.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def report(out_dir, manifest):
    print(f"TYPE carousel")
    print(f"SLUG {manifest['slug']}")
    print(f"FOLDER {out_dir}")
    print(f"CREATOR {manifest['creator'] or '(unknown)'}")
    for s in manifest["slides"]:
        extra = f" {s['duration']}s" if s["type"] == "video" else ""
        print(f"SLIDE {s['n']:02d} {s['type']} {s['width']}x{s['height']}{extra} {s['file']}")
    for f in manifest["flags"]:
        print(f"FLAG {f}")


# ---------- commands ----------

def cmd_fetch(args):
    url = clean_ig_url(args.url)
    given = (args.handle or "").lstrip("@")
    try:
        post = apify_fetch(url)
    except ApifyUnavailable as e:
        return fetch_with_ytdlp(url, given, e)
    owner = post.get("ownerUsername") or ""
    if given and owner and given.lower() != owner.lower():
        print(f"HANDLE_CONFLICT post owner @{owner} vs given @{given}")
    creator = given or owner
    if not creator:
        sys.exit("No creator handle found. Rerun with --handle @name.")
    code = post.get("shortCode") or shortcode_from_url(url)
    kind = post.get("type")
    print(f"POST {kind} by @{owner or '?'} ({code})")
    if kind == "Video":
        print("TYPE video")
        return

    slug = slugify(f"repost-{creator}-{code}")
    out_dir = REPOST_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "source"
    raw_dir.mkdir(exist_ok=True)

    info = {k: post.get(k) for k in ("url", "shortCode", "type", "caption", "ownerUsername", "ownerFullName",
                                      "timestamp", "alt", "hashtags", "mentions")}
    info["taggedUsers"] = [t.get("username") for t in post.get("taggedUsers") or []]
    info["coauthors"] = [c.get("username") for c in post.get("coauthorProducers") or []]
    info["childAlts"] = [k.get("alt") for k in post.get("childPosts") or []]
    (out_dir / f"{code}.info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")

    raw = []
    for n, s in enumerate(slides_from_post(post), start=1):
        ext = ".mp4" if s["type"] == "video" else ".jpg"
        dst = raw_dir / f"{code}_{n:02d}{ext}"
        if not dst.exists():
            dst.write_bytes(http_get(s["url"], timeout=120))
        raw.append((dst, s["type"]))
    if not raw:
        sys.exit("The post has no downloadable slides.")

    slides = build_slides(raw, out_dir, slug)
    manifest = write_manifest(out_dir, slug, url, creator, slides,
                              {"info_json": f"{code}.info.json", "owner": owner})
    report(out_dir, manifest)


def cmd_inbox(args):
    files = []
    for p in map(Path, args.paths):
        if p.is_dir():
            files += [f for f in p.iterdir() if f.is_file()]
        elif p.is_file():
            files.append(p)
        else:
            sys.exit(f"Not found: {p}")
    files = [f for f in files if f.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS]
    if not files:
        sys.exit("No images or videos found.")
    files.sort(key=natural_key)
    creator = (args.handle or "").lstrip("@")
    if not creator:
        sys.exit("Inbox carousels need the creator: --handle @name")
    name = args.name or Path(args.paths[0]).stem
    slug = slugify(f"repost-{creator}-{name}")
    out_dir = REPOST_ROOT / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "source"
    raw_dir.mkdir(exist_ok=True)
    raw = []
    for f in files:
        dst = raw_dir / f.name
        if not dst.exists():
            shutil.copy2(f, dst)  # copy, never move
        raw.append((dst, "video" if f.suffix.lower() in VIDEO_EXTS else "image"))
    slides = build_slides(raw, out_dir, slug)
    manifest = write_manifest(out_dir, slug, "inbox: " + ", ".join(f.name for f in files), creator, slides, {})
    report(out_dir, manifest)


def main():
    ap = argparse.ArgumentParser(description="Fetch and prepare an OKST carousel repost.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="Instagram post URL")
    f.add_argument("url")
    f.add_argument("--handle")
    i = sub.add_parser("inbox", help="image/video files or a folder from OKST-Inbox")
    i.add_argument("paths", nargs="+")
    i.add_argument("--handle", required=True)
    i.add_argument("--name", help="name used in the slug (default: first file or folder name)")
    args = ap.parse_args()
    {"fetch": cmd_fetch, "inbox": cmd_inbox}[args.cmd](args)


if __name__ == "__main__":
    main()
