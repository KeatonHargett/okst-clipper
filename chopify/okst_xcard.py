"""Turn an X (Twitter) post into a 4:5 (1080x1350) Instagram post that looks like the post itself:
profile picture, name, verified badge, @handle, text, and the photo or video, on a dark or light background.

Usage:
    .venv\\Scripts\\python okst_xcard.py fetch "<x.com post url>" [--handle @name] [--theme dark|light]

Post data comes from the free FxTwitter API (api.fxtwitter.com), with X's own embed feed as a backup.
No X login, no cookies, no API key. Cards are drawn with headless Chrome; videos are placed into the card with ffmpeg.

Output in output\\repost\\<slug>\\:
  One video (or GIF), nothing else -> <slug>.mp4, a 4:5 card video. Prints "TYPE video" and FILE, for the video flow.
  Anything else (text only, photos, several media) -> slide 1 is the card, every further photo or video gets its own
  4:5 slide with a small credit header. Writes carousel.json like okst_carousel.py and prints "TYPE carousel".
Media is never cropped or trimmed: photos and videos are fitted whole inside the card.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from okst_carousel import REPOST_ROOT, VIDEO_MIN_S, ffprobe, report, slugify, unique_path, write_manifest

W, H, PAD = 1080, 1350, 80
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
THEMES = {
    "dark": {"bg": "#000000", "text": "#e7e9ea", "muted": "#71767b", "line": "#2f3336"},
    "light": {"bg": "#ffffff", "text": "#0f1419", "muted": "#536471", "line": "#cfd9de"},
}
BADGE_COLORS = {"organization": "#e2b719", "business": "#e2b719", "government": "#829aab"}
BADGE_PATH = ("M22.25 12c0-1.43-.88-2.67-2.19-3.34.46-1.39.2-2.9-.81-3.91s-2.52-1.27-3.91-.81c-.66-1.31-1.91-2.19-3.34-2.19"
              "s-2.67.88-3.33 2.19c-1.4-.46-2.91-.2-3.92.81s-1.26 2.52-.8 3.91c-1.31.67-2.2 1.91-2.2 3.34s.89 2.67 2.2 3.34"
              "c-.46 1.39-.21 2.9.8 3.91s2.52 1.26 3.91.81c.67 1.31 1.91 2.19 3.34 2.19s2.68-.88 3.34-2.19c1.39.45 2.9.2 3.91-.81"
              "s1.27-2.52.81-3.91c1.31-.67 2.19-1.91 2.19-3.34zm-11.71 4.2L6.8 12.46l1.41-1.42 2.26 2.26 4.8-5.23 1.47 1.36-6.2 6.77z")


# ---------- fetching ----------

def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def status_id(url):
    m = re.search(r"(?:x|twitter|fxtwitter|vxtwitter|fixupx|fixvx)\.com/(?:i/web/|[^/?#]+/)status(?:es)?/(\d+)", url)
    if not m:
        sys.exit(f"Not an X post URL: {url}")
    return m.group(1)


def orig_photo(url):
    return url if "?" in url else url + "?name=orig"


def from_fx(t):
    a = t["author"]
    v = a.get("verification") or {}
    media = []
    for m in (t.get("media") or {}).get("all") or []:
        if m.get("type") == "photo":
            media.append({"type": "photo", "url": orig_photo(m["url"]), "width": m["width"], "height": m["height"]})
        elif m.get("type") in ("video", "gif"):
            mp4s = [x for x in m.get("variants") or [] if x.get("content_type") == "video/mp4"]
            best = max(mp4s, key=lambda x: x.get("bitrate") or 0)["url"] if mp4s else m["url"]
            media.append({"type": m["type"], "url": best, "width": m["width"], "height": m["height"],
                          "duration": m.get("duration") or 0, "thumb": m.get("thumbnail_url")})
    q = t.get("quote")
    quote = None
    if q:
        qv = (q.get("author") or {}).get("verification") or {}
        quote = {"name": q["author"]["name"], "handle": q["author"]["screen_name"], "text": q.get("text") or "",
                 "verified": bool(qv.get("verified")), "vtype": qv.get("type")}
    return {
        "id": t["id"], "text": t.get("text") or "",
        "created": dt.datetime.fromtimestamp(t["created_timestamp"], dt.timezone.utc),
        "author": {"name": a["name"], "handle": a["screen_name"], "avatar": a.get("avatar_url"),
                   "verified": bool(v.get("verified")), "vtype": v.get("type")},
        "media": media, "quote": quote,
        "reply_to": t.get("replying_to"), "sensitive": bool(t.get("possibly_sensitive")),
        "community_note": bool(t.get("community_note")), "likes": t.get("likes"), "views": t.get("views"),
        "source": "fxtwitter",
    }


def from_syndication(t):
    u = t["user"]
    vtype = {"Business": "organization", "Government": "government"}.get(u.get("verified_type"))
    text = t.get("text") or ""
    rng = t.get("display_text_range")
    if rng:
        text = text[rng[0]:rng[1]]
    text = re.sub(r"\s*https://t\.co/\w+$", "", html.unescape(text))
    media = []
    for m in t.get("mediaDetails") or []:
        oi = m.get("original_info") or {}
        if m.get("type") == "photo":
            media.append({"type": "photo", "url": orig_photo(m["media_url_https"]),
                          "width": oi.get("width"), "height": oi.get("height")})
        else:
            vi = m.get("video_info") or {}
            mp4s = [x for x in vi.get("variants") or [] if x.get("content_type") == "video/mp4"]
            if not mp4s:
                continue
            media.append({"type": "gif" if m.get("type") == "animated_gif" else "video",
                          "url": max(mp4s, key=lambda x: x.get("bitrate") or 0)["url"],
                          "width": oi.get("width"), "height": oi.get("height"),
                          "duration": (vi.get("duration_millis") or 0) / 1000, "thumb": m.get("media_url_https")})
    q = t.get("quoted_tweet")
    quote = None
    if q:
        quote = {"name": q["user"]["name"], "handle": q["user"]["screen_name"], "text": html.unescape(q.get("text") or ""),
                 "verified": bool(q["user"].get("is_blue_verified") or q["user"].get("verified")), "vtype": None}
    return {
        "id": t["id_str"], "text": text,
        "created": dt.datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")),
        "author": {"name": u["name"], "handle": u["screen_name"], "avatar": u.get("profile_image_url_https"),
                   "verified": bool(u.get("is_blue_verified") or u.get("verified") or vtype), "vtype": vtype},
        "media": media, "quote": quote,
        "reply_to": t.get("in_reply_to_screen_name"), "sensitive": bool(t.get("possibly_sensitive")),
        "community_note": False, "likes": t.get("favorite_count"), "views": None, "source": "x-embed",
    }


def fetch_tweet(tid):
    errors = []
    try:
        data = json.loads(http_get(f"https://api.fxtwitter.com/status/{tid}", timeout=30))
        if data.get("tweet"):
            return from_fx(data["tweet"]), data["tweet"]
        errors.append(f"FxTwitter: {data.get('message')}")
    except Exception as e:
        errors.append(f"FxTwitter: {e}")
    try:
        data = json.loads(http_get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&lang=en&token=a",
                                   timeout=30))
        if data.get("user"):
            return from_syndication(data), data
        errors.append("X embed: no post in the response")
    except Exception as e:
        errors.append(f"X embed: {e}")
    sys.exit("Could not load the post (private, deleted, age restricted, or both sources are down). "
             "Save the media from your phone to OKST-Inbox and use the inbox flow.\n  " + "\n  ".join(errors))


# ---------- drawing ----------

def find_browser():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):  # Linux (cloud runner)
        if shutil.which(name):
            return shutil.which(name)
    local = os.environ.get("LOCALAPPDATA", "")
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if p and Path(p).exists():
            return p
    sys.exit("Chrome or Edge is needed to draw the cards.")


def when(created):
    try:
        from zoneinfo import ZoneInfo
        t = created.astimezone(ZoneInfo("America/Chicago"))
    except Exception:
        t = created.astimezone(dt.timezone(dt.timedelta(hours=-5)))
    return f"{t.strftime('%I:%M %p').lstrip('0')} · {t.strftime('%b')} {t.day}, {t.year}"


def rich(text):
    out = html.escape(text.strip())
    out = re.sub(r"(https?://\S+)", r'<span class="link">\1</span>', out)
    out = re.sub(r"(?<![\w&/])([@#$][A-Za-z_][A-Za-z0-9_]*)", r'<span class="link">\1</span>', out)
    return out.replace("\n", "<br>")


def badge(verified, vtype, size):
    if not verified:
        return ""
    color = BADGE_COLORS.get(vtype or "", "#1d9bf0")
    return f'<svg style="width:{size}px;height:{size}px;flex:none" viewBox="0 0 24 24"><path fill="{color}" d="{BADGE_PATH}"/></svg>'


def css(c, page_bg):
    return f"""html,body{{margin:0;width:{W}px;height:{H}px;background:{page_bg};overflow:hidden}}
body{{font-family:"Segoe UI","Segoe UI Emoji","Segoe UI Symbol",system-ui,sans-serif;color:{c['text']};
     display:flex;align-items:center;justify-content:center}}
.wrap{{width:{W - 2 * PAD}px;display:flex;flex-direction:column;gap:34px;position:relative}}
.top,.text,.foot,.quote,.mini{{position:relative;z-index:1}}
.top{{display:flex;align-items:center;gap:26px}}
.av{{width:120px;height:120px;border-radius:50%;flex:none}}
.name{{font-size:44px;font-weight:700;display:flex;align-items:center;gap:10px;line-height:1.15}}
.handle{{font-size:36px;color:{c['muted']};margin-top:4px}}
.text{{font-size:48px;line-height:1.32;overflow-wrap:anywhere}}
.link{{color:#1d9bf0}}
.quote{{border:2px solid {c['line']};border-radius:28px;padding:26px 30px}}
.qhead{{display:flex;align-items:center;gap:8px;font-size:32px}}
.qname{{font-weight:700}} .qhandle{{color:{c['muted']}}}
.qtext{{font-size:34px;line-height:1.3;margin-top:8px;overflow-wrap:anywhere}}
.box{{position:relative;z-index:0;border-radius:32px;overflow:hidden;border:2px solid {c['line']};
     box-sizing:border-box;align-self:center;flex:none}}
.box img{{width:100%;height:100%;object-fit:contain;display:block;position:relative}}
.box img.fill{{position:absolute;inset:0;object-fit:cover;filter:blur(36px) brightness(.55);transform:scale(1.15)}}
.hole{{border-color:transparent;box-shadow:0 0 0 3000px {c['bg']}}}
.foot{{font-size:32px;color:{c['muted']}}}
.mini{{display:flex;align-items:center;gap:18px;font-size:34px}}
.mini .av{{width:72px;height:72px}} .mini .handle{{font-size:30px;margin:0}}"""


FIT_JS = """<script>
const H = %(H)d, PAD = %(PAD)d, maxW = %(maxW)d, aspect = %(aspect)s, base = %(base)d, minMedia = %(minMedia)d,
      fill = %(fill)s;
const wrap = document.getElementById('wrap'), text = document.getElementById('text'), box = document.getElementById('box');
const maxH = H - 2 * PAD;
let fs = base;
function lay() { if (text) text.style.fontSize = fs + 'px'; if (box) { box.style.width = maxW + 'px'; box.style.height = '0px'; } }
lay();
if (box) {
  const want = Math.min(Math.round(maxW * aspect), minMedia);
  while (text && fs > 30 && maxH - wrap.offsetHeight < want) { fs -= 2; lay(); }
  const avail = maxH - wrap.offsetHeight;
  let w = maxW, h = Math.round(w * aspect);
  // A photo too tall for the slot keeps the full width: it sits whole in the middle over a blurred copy of itself.
  if (h > avail) { h = Math.max(avail, 100); if (!fill) w = Math.round(h / aspect); }
  w -= w %% 2; h -= h %% 2;
  box.style.width = w + 'px'; box.style.height = h + 'px';
  const r = box.getBoundingClientRect();
  document.body.dataset.box = [Math.round(r.left), Math.round(r.top), w, h].join(',');
} else {
  while (text && fs > 30 && wrap.offsetHeight > maxH) { fs -= 2; lay(); }
}
document.body.dataset.fs = fs;
document.body.dataset.overflow = wrap.offsetHeight > maxH ? '1' : '0';
</script>"""


def box_inner(media, hole):
    """The photo whole, over a blurred copy that fills any space beside it. Videos (hole) are placed later by ffmpeg."""
    if hole:
        return ""
    return f'<img class="fill" src="{media["src"]}"><img src="{media["src"]}">'


def card_page(tw, theme, media=None, hole=False):
    """The main card. media: {"src", "width", "height"} or None."""
    c = THEMES[theme]
    a = tw["author"]
    quote = ""
    if tw.get("quote"):
        q = tw["quote"]
        quote = (f'<div class="quote"><div class="qhead"><span class="qname">{html.escape(q["name"])}</span>'
                 f'{badge(q["verified"], q["vtype"], 32)}<span class="qhandle">@{html.escape(q["handle"])}</span></div>'
                 f'<div class="qtext">{rich(q["text"])}</div></div>')
    box = ""
    if media:
        box = f'<div id="box" class="box{" hole" if hole else ""}">{box_inner(media, hole)}</div>'
    text = f'<div class="text" id="text">{rich(tw["text"])}</div>' if tw["text"].strip() else ""
    js = FIT_JS % {"H": H, "PAD": PAD, "maxW": W - 2 * PAD,
                   "aspect": (media["height"] / media["width"]) if media else 1, "base": 48 if media else 76,
                   "minMedia": 560, "fill": "false" if hole or not media else "true"}
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css(c, 'transparent' if hole else c['bg'])}</style></head>
<body><div class="wrap" id="wrap">
<div class="top"><img class="av" src="avatar.jpg"><div>
<div class="name">{html.escape(a['name'])}{badge(a['verified'], a['vtype'], 44)}</div>
<div class="handle">@{html.escape(a['handle'])}</div></div></div>
{text}{quote}{box}
<div class="foot">{when(tw['created'])}</div>
</div>{js}</body></html>"""


def media_page(tw, theme, media, hole=False):
    """A follow-up slide: small credit header and one photo or video fitted whole."""
    c = THEMES[theme]
    a = tw["author"]
    inner = box_inner(media, hole)
    js = FIT_JS % {"H": H, "PAD": PAD, "maxW": W - 2 * PAD, "aspect": media["height"] / media["width"],
                   "base": 48, "minMedia": 560, "fill": "false" if hole else "true"}
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{css(c, 'transparent' if hole else c['bg'])}</style></head>
<body><div class="wrap" id="wrap">
<div class="mini"><img class="av" src="avatar.jpg"><div>
<div class="name" style="font-size:34px">{html.escape(a['name'])}{badge(a['verified'], a['vtype'], 34)}</div>
<div class="handle">@{html.escape(a['handle'])}</div></div></div>
<div id="box" class="box{" hole" if hole else ""}">{inner}</div>
</div>{js}</body></html>"""


def chrome(browser, page_path, png=None, transparent=False):
    args = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
            f"--window-size={W},{H}", "--allow-file-access-from-files", "--virtual-time-budget=5000"]
    if sys.platform.startswith("linux"):
        args.append("--no-sandbox")
    if transparent:
        args.append("--default-background-color=00000000")
    url = page_path.resolve().as_uri()
    if png:
        subprocess.run(args + [f"--screenshot={png.resolve()}", url], check=True, capture_output=True)
        if not png.exists():
            sys.exit(f"Chrome did not write {png.name}")
        return None
    dom = subprocess.run(args + ["--dump-dom", url], check=True, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stdout
    info = {"overflow": 'data-overflow="1"' in dom}
    fs = re.search(r'data-fs="(\d+)"', dom)
    info["font"] = int(fs.group(1)) if fs else None
    m = re.search(r'data-box="(\d+),(\d+),(\d+),(\d+)"', dom)
    if m:
        info["box"] = tuple(map(int, m.groups()))
    return info


def draw_image(browser, work, page_html, name, out_jpg):
    page = work / f"{name}.html"
    page.write_text(page_html, encoding="utf-8")
    info = chrome(browser, page)
    png = work / f"{name}.png"
    chrome(browser, page, png)
    with Image.open(png) as im:
        im.convert("RGB").save(out_jpg, "JPEG", quality=95, subsampling=0, optimize=True)
    return info


def fps_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True).stdout.strip()
    try:
        num, den = (out.split("/") + ["1"])[:2]
        f = float(num) / float(den)
        return out if 10 <= f <= 60 else "30"
    except Exception:
        return "30"


def draw_video(browser, work, page_html, name, video, theme, out_mp4, loop_to=0.0):
    """Draw the card with a see-through media slot, then play the video inside it. Video is scaled whole, never cropped."""
    page = work / f"{name}.html"
    page.write_text(page_html, encoding="utf-8")
    info = chrome(browser, page)
    x, y, bw, bh = info["box"]
    frame = work / f"{name}_frame.png"
    chrome(browser, page, frame, transparent=True)
    bg = THEMES[theme]["bg"].lstrip("#")
    fc = (f"color=c=0x{bg}:s={W}x{H}:r={fps_of(video)}[bg];[1:v]scale={bw}:{bh}:flags=lanczos,setsar=1[v];"
          f"[bg][v]overlay={x}:{y}:shortest=1[b];[b][0:v]overlay=0:0:shortest=1,format=yuv420p[o]")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-n", "-loop", "1", "-i", str(frame)]
    if loop_to:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(video), "-filter_complex", fc, "-map", "[o]", "-map", "1:a?",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "192k"]
    if loop_to:
        cmd += ["-t", f"{loop_to:.2f}"]
    # No -shortest: the overlays already end the picture with the source video, and the full audio is kept.
    cmd += ["-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return info


def download_media(tw, raw, x_url):
    """Download avatar and media into raw/. Returns media dicts with local 'path'."""
    av = raw / "avatar.jpg"
    if not av.exists():
        big = re.sub(r"_(normal|200x200|bigger|mini)\.", "_400x400.", tw["author"]["avatar"] or "")
        for u in (big, tw["author"]["avatar"]):
            try:
                av.write_bytes(http_get(u))
                break
            except Exception:
                continue
        else:
            sys.exit("Could not download the profile picture.")
    out = []
    for n, m in enumerate(tw["media"], start=1):
        ext = ".jpg" if m["type"] == "photo" else ".mp4"
        p = raw / f"{tw['id']}_{n:02d}{ext}"
        if not p.exists():
            try:
                p.write_bytes(http_get(m["url"], timeout=300))
            except Exception:
                if m["type"] == "photo":
                    raise
                # Fallback: yt-dlp, still no cookies or login.
                subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "best[ext=mp4]/best", "--playlist-items", str(n),
                                "-o", str(p), x_url], check=True)
        item = dict(m, path=p)
        if m["type"] == "photo":
            with Image.open(p) as im:
                item["width"], item["height"] = im.size
        else:
            info = ffprobe(p)
            v = next(s for s in info["streams"] if s.get("codec_type") == "video")
            item["width"], item["height"] = int(v["width"]), int(v["height"])
            item["duration"] = float(info["format"].get("duration") or m.get("duration") or 0)
        out.append(item)
    return out


# ---------- command ----------

def text_flags(res):
    if res.get("overflow"):
        return ["The post text is too long to fit the card and is cut off at the bottom."]
    if res.get("font") and res["font"] < 38:
        return [f"Long post: the text shrank to {res['font']}px to fit, which may be hard to read on a phone."]
    return []


def cmd_fetch(args):
    tid = status_id(args.url)
    tw, raw_json = fetch_tweet(tid)
    owner = tw["author"]["handle"]
    given = (args.handle or "").lstrip("@")
    if given and given.lower() != owner.lower():
        print(f"HANDLE_CONFLICT post author @{owner} vs given @{given}")
    creator = given or owner
    x_url = f"https://x.com/{owner}/status/{tid}"
    print(f"POST by @{owner} ({tid}) via {tw['source']}: {len(tw['media'])} media")

    slug = slugify(f"repost-{creator}-x{tid}")
    out_dir = REPOST_ROOT / slug
    raw = out_dir / "source"
    work = out_dir / "render"
    for d in (out_dir, raw, work):
        d.mkdir(parents=True, exist_ok=True)

    info = dict(tw, created=tw["created"].isoformat(), url=x_url, card_theme=args.theme)
    info_name = f"x{tid}.info.json"
    (out_dir / info_name).write_text(json.dumps(info, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (raw / f"x{tid}.raw.json").write_text(json.dumps(raw_json, indent=2, ensure_ascii=False), encoding="utf-8")

    media = download_media(tw, raw, x_url)
    shutil.copy2(raw / "avatar.jpg", work / "avatar.jpg")
    for m in media:
        if m["type"] == "photo":
            shutil.copy2(m["path"], work / m["path"].name)
    browser = find_browser()

    flags = [f"Credit is the X handle @{owner}. Check that @{owner} on Instagram is the same account before "
             "posting, or the credit tags someone else."]
    if tw.get("reply_to"):
        flags.append(f"This post is a reply to @{tw['reply_to']}. The card shows it without the post it answers.")
    if tw.get("quote"):
        flags.append(f"Quotes a post by @{tw['quote']['handle']}. Only its text is shown in the card.")
    if tw.get("sensitive"):
        flags.append("X marked this post as possibly sensitive.")
    if tw.get("community_note"):
        flags.append("This post has a Community Note on X.")

    def loop_target(m):
        return max(6.0, m["duration"]) if m["type"] == "gif" and m["duration"] < VIDEO_MIN_S else 0.0

    def page_media(m):
        return {"src": m["path"].name, "width": m["width"], "height": m["height"]}

    # Single video or GIF: one 4:5 card video for the normal video flow.
    if len(media) == 1 and media[0]["type"] in ("video", "gif"):
        m = media[0]
        out = unique_path(out_dir / f"{slug}.mp4")
        page = card_page(tw, args.theme, page_media(m), hole=True)
        res = draw_video(browser, work, page, "card", m["path"], args.theme, out, loop_target(m))
        flags += text_flags(res)
        if m["type"] == "gif":
            flags.append("The media is an X GIF (silent, loops).")
        d = float(ffprobe(out)["format"]["duration"])
        print("TYPE video")
        print(f"SLUG {slug}")
        print(f"FOLDER {out_dir}")
        print(f"CREATOR {creator}")
        print(f"FILE {out}")
        print(f"CARD 1080x1350 {d:.1f}s theme {args.theme}")
        for f in flags:
            print(f"FLAG {f}")
        return

    # Everything else: slide 1 is the card, then one slide per further photo or video.
    slides = []
    first = media[0] if media else None
    rest = media[1:]
    n = 1
    if first is None or first["type"] == "photo":
        dst = unique_path(out_dir / f"{slug}_{n:02d}.jpg")
        res = draw_image(browser, work, card_page(tw, args.theme, page_media(first) if first else None), "card", dst)
        slides.append({"n": n, "file": dst.name, "type": "image", "width": W, "height": H})
    else:
        dst = unique_path(out_dir / f"{slug}_{n:02d}.mp4")
        res = draw_video(browser, work, card_page(tw, args.theme, page_media(first), hole=True), "card",
                         first["path"], args.theme, dst, loop_target(first))
        slides.append({"n": n, "file": dst.name, "type": "video", "width": W, "height": H,
                       "duration": round(float(ffprobe(dst)["format"]["duration"]), 2)})
    flags += text_flags(res)
    for m in rest:
        n += 1
        if m["type"] == "photo":
            dst = unique_path(out_dir / f"{slug}_{n:02d}.jpg")
            draw_image(browser, work, media_page(tw, args.theme, page_media(m)), f"slide{n:02d}", dst)
            slides.append({"n": n, "file": dst.name, "type": "image", "width": W, "height": H})
        else:
            dst = unique_path(out_dir / f"{slug}_{n:02d}.mp4")
            draw_video(browser, work, media_page(tw, args.theme, page_media(m), hole=True), f"slide{n:02d}",
                       m["path"], args.theme, dst, loop_target(m))
            slides.append({"n": n, "file": dst.name, "type": "video", "width": W, "height": H,
                           "duration": round(float(ffprobe(dst)["format"]["duration"]), 2)})
    manifest = write_manifest(out_dir, slug, x_url, creator, slides,
                              {"info_json": info_name, "owner": owner, "x_card": True, "theme": args.theme})
    manifest["flags"] = flags + manifest["flags"]
    (out_dir / "carousel.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report(out_dir, manifest)


def main():
    ap = argparse.ArgumentParser(description="Turn an X post into a 4:5 Instagram card.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="X post URL")
    f.add_argument("url")
    f.add_argument("--handle")
    f.add_argument("--theme", choices=sorted(THEMES), default="dark")
    args = ap.parse_args()
    {"fetch": cmd_fetch}[args.cmd](args)


if __name__ == "__main__":
    main()
