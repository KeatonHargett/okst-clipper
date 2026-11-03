"""Fetch and prepare an OKST carousel repost (Instagram photo posts, carousels, or images from the inbox).

Usage:
    doppler run -p okst -c dev -- .venv\\Scripts\\python okst_carousel.py fetch "<instagram post url>" [--handle @name]
    .venv\\Scripts\\python okst_carousel.py inbox "<folder or image files...>" --handle @name [--name <id>]

fetch asks Apify's Instagram Post Scraper for the post (no Instagram login, no cookies).
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
import pillow_heif

pillow_heif.register_heif_opener()

ROOT = Path(__file__).resolve().parent
REPOST_ROOT = ROOT / "output" / "repost"
APIFY_ACTOR = "apify~instagram-post-scraper"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
MAX_SLIDES = 10                 # Instagram API carousel limit
MIN_RATIO, MAX_RATIO = 0.75, 1.91  # Instagram feed limits: 3:4 portrait (since May 2025) to 1.91:1 landscape
VIDEO_MIN_S, VIDEO_MAX_S = 3.0, 60.0


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

def apify_fetch(url):
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("APIFY_TOKEN is not set. Run through: doppler run -p okst -c dev -- ...")
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
        sys.exit(f"Apify request failed twice: {last_err}")
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
    post = apify_fetch(url)
    owner = post.get("ownerUsername") or ""
    given = (args.handle or "").lstrip("@")
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
