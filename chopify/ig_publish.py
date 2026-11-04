"""Publish an approved OKST carousel from its review folder to Instagram through the official Instagram API.

Usage (always under Doppler so the token never touches disk or the command line):
    doppler run -p okst -c dev -- .venv\\Scripts\\python ig_publish.py --check
    doppler run -p okst -c dev -- .venv\\Scripts\\python ig_publish.py "<review folder>" [--dry-run]
    doppler run -p okst -c dev -- .venv\\Scripts\\python ig_publish.py --reel "<mp4>" [--dry-run | --stage-only]
    doppler run -p okst -c dev -- .venv\\Scripts\\python ig_publish.py --check-location <page id> [...]

The review folder holds carousel.json, the slides it lists, and <slug>.txt (the final caption).
Flow: validate -> serve the slides briefly from this PC through a Cloudflare quick tunnel (Instagram fetches media by URL)
-> create one container per slide -> carousel container with the caption -> publish -> take the slides down.
An optional <base>.location.json ({"key": "<venue>"}, keys in locations.json) tags the post's location.
An optional <base>.cover.jpg (make_cover.py) next to a Reel's mp4 becomes its grid cover (cover_url).
Writes published.json into the review folder and refuses to run again once it exists, so nothing double posts.
The token refreshes itself (60 day token, refreshed weekly) and the new value goes straight back into Doppler.

Every post also goes to the OKSTProbs Facebook Page (FB_PAGE_TOKEN in okst/dev): Reels as FB Reels (or a Page video
when Facebook won't take it as a Reel), photos and carousels as a photo post. Instagram never auto shares API posts.
A Facebook failure never blocks the Instagram post; it is saved in published.json and --fb-only retries just Facebook.
    ... ig_publish.py --reel "<mp4>" --fb-only      |  ... ig_publish.py "<review folder>" --fb-only
"""

import argparse
import datetime as dt
import functools
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPH = "https://graph.instagram.com/v23.0"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
FB_GRAPH = "https://graph.facebook.com/v23.0"
FB_PAGE_ID = "116302308209995"   # OKSTProbs; token from ~/.claude/scripts/okst-fb-page-token.ps1
FB_REEL_MAX_S = 90
DOPPLER = ["-p", "okst", "-c", "dev"]
STATE = Path.home() / ".okst" / "ig_token_state.json"
LOCK = Path.home() / ".okst" / "ig_publish.lock"
REFRESH_EVERY = dt.timedelta(days=7)
MAX_SLIDES = 10
DASHES = ("-", "‐", "‑", "‒", "–", "—", "―", "−")


class ApiError(RuntimeError):
    pass


# ---------- Instagram API ----------

def _call(method, url, params, token, timeout=60):
    params = dict(params or {})
    params["access_token"] = token
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode(), method="GET")
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(e)
            code = f" (code {err.get('code')}/{err.get('error_subcode', '')})"
        except Exception:
            msg, code = str(e), ""
        raise ApiError(f"{method} {url.replace(GRAPH, '').replace(FB_GRAPH, 'fb:')}: {msg}{code}") from None


def get(path, token, **params):
    return _call("GET", f"{GRAPH}/{path}", params, token)


def post(path, token, **params):
    return _call("POST", f"{GRAPH}/{path}", params, token)


def wait_ready(container_id, token, label, timeout_s=600):
    start = time.time()
    backoff = 30
    while True:
        try:
            st = get(container_id, token, fields="status_code,status")
        except ApiError as e:
            # Polling too fast trips Meta's per-object limit (code 4); back off instead of abandoning the upload.
            if "(code 4/" not in str(e) or time.time() - start + backoff > timeout_s:
                raise
            print(f"  rate limited while checking {label}; waiting {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
            continue
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise ApiError(f"{label} failed on Instagram's side: {st.get('status') or code}")
        if time.time() - start > timeout_s:
            raise ApiError(f"{label} still {code} after {timeout_s}s")
        time.sleep(10)


# ---------- Facebook Page ----------
# Uploads local files straight to Facebook (no tunnel). Runs after the Instagram publish.

def fb_get(path, token, **params):
    return _call("GET", f"{FB_GRAPH}/{path}", params, token)


def fb_post(path, token, **params):
    return _call("POST", f"{FB_GRAPH}/{path}", params, token)


def fb_status():
    token = os.environ.get("FB_PAGE_TOKEN")
    if not token:
        return "off (FB_PAGE_TOKEN not set)"
    try:
        return fb_get(FB_PAGE_ID, token, fields="name").get("name")
    except ApiError as e:
        return f"BROKEN ({e}); run okst-fb-page-token.ps1"


def fb_wait_uploaded(video_id, token, timeout_s=600):
    """Facebook pulls file_url in the background; the tunnel must stay up until it has the whole file."""
    start = time.time()
    while True:
        try:
            st = fb_get(video_id, token, fields="status").get("status") or {}
        except ApiError as e:
            # A fresh video id can read as "does not exist" (100/33) for a moment after the upload.
            if "(code 100/33)" not in str(e) or time.time() - start > 120:
                raise
            time.sleep(5)
            continue
        phase = (st.get("uploading_phase") or {}).get("status")
        if st.get("video_status") == "error" or phase == "error":
            raise ApiError(f"Facebook could not take the video: {st}")
        if phase == "complete":
            return
        if time.time() - start > timeout_s:
            raise ApiError(f"Facebook upload still {phase} after {timeout_s}s")
        time.sleep(5)


def _fb_multipart(url, token, params, file_field=None, filename=None, data=None, label="upload"):
    """multipart POST with optional file bytes. Facebook gets the bytes straight from this machine, so its step
    needs no tunnel (the Reels fetcher rejects trycloudflare URLs, and quick tunnels get rate limited)."""
    boundary = secrets.token_hex(16)
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in {**params, "access_token": token}.items())
    if file_field:
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
            msg = f"{err.get('error_user_msg') or err.get('message')} (code {err.get('code')}/{err.get('error_subcode', '')})"
        except Exception:
            msg = f"HTTP {e.code}"
        raise ApiError(f"POST fb {label}: {msg}") from None


FB_CHUNKED_MIN = 40 * 1024 * 1024   # one shot uploads of large files get dropped mid stream (SSL EOF); chunk them


def fb_upload(url, token, path, **params):
    """Upload one local video to a /videos endpoint: one multipart POST for small files, Facebook's resumable
    start/transfer/finish chunks for large ones. Returns the response with "id"."""
    size = path.stat().st_size
    if size < FB_CHUNKED_MIN or not url.endswith("/videos"):   # photos never use the chunked API
        return _fb_multipart(url, token, params, "source", f"upload{path.suffix}", path.read_bytes(), path.name)
    start = _fb_multipart(url, token, {"upload_phase": "start", "file_size": size}, label="upload start")
    session, vid = start["upload_session_id"], start["video_id"]
    lo, hi = int(start["start_offset"]), int(start["end_offset"])
    with open(path, "rb") as f:
        while lo < hi:
            f.seek(lo)
            chunk = f.read(hi - lo)
            for attempt in range(3):   # a dropped chunk is safe to resend: same session, same offset
                try:
                    r = _fb_multipart(url, token, {"upload_phase": "transfer", "upload_session_id": session,
                                                   "start_offset": lo}, "video_file_chunk", "chunk", chunk,
                                      f"upload chunk at {lo}")
                    break
                except (urllib.error.URLError, OSError) as e:
                    if attempt == 2:
                        raise ApiError(f"Facebook chunk at {lo} failed 3 times: {e}") from None
                    time.sleep(5)
            lo, hi = int(r["start_offset"]), int(r["end_offset"])
            print(f"  Facebook upload {min(lo, size) * 100 // size}%")
    _fb_multipart(url, token, {"upload_phase": "finish", "upload_session_id": session, **params},
                  label="upload finish")
    return {"id": vid}


def fb_video(mp4, caption, info, token):
    """A Reel when Facebook takes it as one (9:16, 90s max), otherwise a regular Page video. Returns (id, kind)."""
    ratio = info["width"] / info["height"]
    if info["duration"] <= FB_REEL_MAX_S and abs(ratio - 9 / 16) < 0.01 and info["width"] >= 540:
        vid = fb_post(f"{FB_PAGE_ID}/video_reels", token, upload_phase="start")["video_id"]
        size = mp4.stat().st_size
        try:
            with open(mp4, "rb") as f:
                req = urllib.request.Request(
                    f"https://rupload.facebook.com/video-upload/v23.0/{vid}", data=f, method="POST",
                    headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                             "Content-Length": str(size), "Content-Type": "application/octet-stream"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    json.load(r)
        except urllib.error.HTTPError as e:
            raise ApiError(f"Facebook Reel upload: HTTP {e.code} {e.read()[:300]!r}") from None
        fb_wait_uploaded(vid, token)
        fb_post(f"{FB_PAGE_ID}/video_reels", token, upload_phase="finish", video_id=vid,
                video_state="PUBLISHED", description=caption)
        return vid, "reel"
    vid = fb_upload(f"https://graph-video.facebook.com/v23.0/{FB_PAGE_ID}/videos", token, mp4,
                    description=caption)["id"]
    fb_wait_uploaded(vid, token)
    return vid, "video"


def fb_photos(paths, caption, token):
    """One photo post, or a multi photo post for a carousel. Returns (id, kind)."""
    if not paths:
        raise ApiError("no photo slides; refusing a text-only Facebook post")
    if len(paths) == 1:
        r = fb_upload(f"{FB_GRAPH}/{FB_PAGE_ID}/photos", token, paths[0], caption=caption)
        return r.get("post_id") or r["id"], "photo"
    ids = [fb_upload(f"{FB_GRAPH}/{FB_PAGE_ID}/photos", token, p, published="false")["id"] for p in paths]
    media = {f"attached_media[{i}]": json.dumps({"media_fbid": m}) for i, m in enumerate(ids)}
    return fb_post(f"{FB_PAGE_ID}/feed", token, message=caption, **media)["id"], "album"


def fb_share(done, post_fn):
    """Run post_fn(token) -> (id, kind) and record the outcome in published.json. Never raises."""
    rec = json.loads(done.read_text(encoding="utf-8"))
    token = os.environ.get("FB_PAGE_TOKEN")
    if not token:
        rec["facebook_error"] = "FB_PAGE_TOKEN not set"
        print("WARNING: FB_PAGE_TOKEN is not set; skipped Facebook.")
    else:
        try:
            fid, kind = post_fn(token)
            try:
                link = fb_get(fid, token, fields="permalink_url").get("permalink_url") or ""
            except ApiError:
                link = ""
            if link and not link.startswith("http"):
                link = "https://www.facebook.com" + link
            link = link or (f"https://www.facebook.com/reel/{fid}" if kind == "reel" else None)
            rec["facebook"] = {"id": fid, "kind": kind, "url": link,
                               "published_at": dt.datetime.now(dt.timezone.utc).isoformat()}
            rec.pop("facebook_error", None)
            print(f"FACEBOOK {kind} {link or fid}")
        except Exception as e:
            rec["facebook_error"] = str(e)
            print(f"WARNING: Facebook post failed ({e}). Instagram is live; retry Facebook alone with --fb-only.")
    done.write_text(json.dumps(rec, indent=2), encoding="utf-8")


def fb_only_guard(done):
    if not done.exists():
        sys.exit("Not on Instagram yet. Publish it normally first (that also posts to Facebook).")
    rec = json.loads(done.read_text(encoding="utf-8"))
    if rec.get("facebook"):
        sys.exit(f"Already on Facebook: {rec['facebook'].get('url') or rec['facebook'].get('id')}")


# ---------- token ----------

def load_token():
    tok = os.environ.get("IG_ACCESS_TOKEN")
    if not tok:
        sys.exit("IG_ACCESS_TOKEN is not set. Run through: doppler run -p okst -c dev -- ...")
    return tok


def maybe_refresh(token, force=False):
    """Refresh the long lived token weekly so it never hits its 60 day expiry. Value goes to Doppler via stdin."""
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    last = dt.datetime.fromisoformat(state["refreshed_at"]) if state.get("refreshed_at") else None
    now = dt.datetime.now(dt.timezone.utc)
    if not force and last and now - last < REFRESH_EVERY:
        return token
    try:
        with urllib.request.urlopen(REFRESH_URL + "?" + urllib.parse.urlencode(
                {"grant_type": "ig_refresh_token", "access_token": token}), timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        # A token under 24 hours old cannot be refreshed yet. That is fine: keep using it.
        print(f"Token refresh skipped: HTTP {e.code}")
        return token
    new = data.get("access_token")
    if not new:
        print("Token refresh returned no token; keeping the current one.")
        return token
    subprocess.run(["doppler", "secrets", "set", "IG_ACCESS_TOKEN", *DOPPLER, "--silent"],
                   input=new, text=True, check=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    days = round(int(data.get("expires_in", 0)) / 86400)
    STATE.write_text(json.dumps({"refreshed_at": now.isoformat(), "expires_in_days": days}))
    print(f"Token refreshed (valid {days} more days) and saved to Doppler okst/dev.")
    return new


# ---------- hosting ----------
# Instagram only accepts media by public URL. While a publish runs, this PC serves a temp copy of the slides
# through a Cloudflare quick tunnel (free, no account, random trycloudflare.com address that dies with the process).

_hosting = {}


class _SlideHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *args):
        pass


def find_cloudflared():
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    for p in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe"):
        if Path(p).exists():
            return p
    sys.exit("cloudflared not found. Install it: winget install --id Cloudflare.cloudflared")


def host_slides(folder, slides):
    """Serve the slides under a random path through a quick tunnel and return {file: public url}."""
    tmp = Path(tempfile.mkdtemp(prefix="okst-media-"))
    _hosting["tmp"] = tmp
    key = secrets.token_urlsafe(18)
    (tmp / key).mkdir()
    for s in slides:
        shutil.copy2(folder / s["file"], tmp / key / s["file"])

    handler = functools.partial(_SlideHandler, directory=str(tmp))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _hosting["server"] = server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    proc = subprocess.Popen([find_cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    _hosting["proc"] = proc
    found = {}

    def read_log():
        for line in proc.stderr:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and "base" not in found:
                found["base"] = m.group(0)
            if "Registered tunnel connection" in line:
                found["registered"] = True

    threading.Thread(target=read_log, daemon=True).start()
    for _ in range(60):
        if ("base" in found and "registered" in found) or proc.poll() is not None:
            break
        time.sleep(1)
    if "base" not in found or "registered" not in found:
        raise RuntimeError("Cloudflare quick tunnel did not start.")

    urls = {s["file"]: f"{found['base']}/{key}/{urllib.parse.quote(s['file'])}" for s in slides}
    # A brand new tunnel name takes a few seconds to appear in DNS, and asking Windows too early makes it cache
    # the miss. So look the name up through Cloudflare's DNS over HTTPS, then check each slide through the edge
    # with curl --resolve, never touching the local DNS cache. Instagram resolves the name on Meta's side.
    host = urllib.parse.urlparse(found["base"]).hostname
    time.sleep(8)   # the name takes ~6s to exist; asking sooner gets a "does not exist" answer that resolvers cache
    ip = None
    for _ in range(30):
        ip = _doh_ip(host)
        if ip:
            break
        time.sleep(3)
    if not ip:
        raise RuntimeError(f"Tunnel name never appeared in DNS: {host}")
    for u in urls.values():
        for _ in range(15):
            if _edge_status(u, host, ip) == "200":
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"Hosted slide never came up: {u}")
    return urls


def _doh_ip(host):
    """IPv4 address for host via DNS over HTTPS (Cloudflare, then Google), or None."""
    for base in ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"):
        req = urllib.request.Request(f"{base}?name={host}&type=A", headers={"accept": "application/dns-json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                answers = json.load(r).get("Answer") or []
        except Exception:
            continue
        ip = next((a["data"] for a in answers if a.get("type") == 1), None)
        if ip:
            return ip
    return None


def _edge_status(url, host, ip):
    curl = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
    res = subprocess.run([curl, "-s", "-o", os.devnull, "-I", "-w", "%{http_code}", "--max-time", "20",
                          "--resolve", f"{host}:443:{ip}", url], capture_output=True, text=True)
    return res.stdout.strip()


def unhost():
    proc = _hosting.pop("proc", None)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    server = _hosting.pop("server", None)
    if server:
        server.shutdown()
        server.server_close()
    tmp = _hosting.pop("tmp", None)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- checks ----------

def caption_problems(caption):
    tags = re.findall(r"#\w+", caption)
    out = []
    if len(tags) != 5:
        out.append(f"has {len(tags)} hashtags, needs exactly 5")
    if tags and tags[0].lower() != "#okstate":
        out.append("first hashtag is not #OKState")
    if any(d in caption for d in DASHES):
        out.append("contains a hyphen or dash")
    if len(caption) > 2200:
        out.append(f"is {len(caption)} characters, over 2,200")
    return out


REEL_MIN_S, REEL_MAX_S = 3, 900          # Instagram API Reels: 3 seconds to 15 minutes
REEL_MAX_BYTES = 300 * 1024 * 1024


def load_caption(txt):
    if not txt.is_file():
        sys.exit(f"Missing final caption {txt.name}. A .draft.txt is never published.")
    caption = txt.read_text(encoding="utf-8-sig").strip()
    problems = caption_problems(caption)
    if problems:
        sys.exit(f"Caption {txt.name} " + "; ".join(problems) + ".")
    return caption


def load_reel(mp4):
    if not mp4.is_file():
        sys.exit(f"Not found: {mp4}")
    caption = load_caption(mp4.with_suffix(".txt"))
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,pix_fmt:format=duration",
                          "-of", "json", str(mp4)], capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    dur = float(info["format"].get("duration") or 0)
    problems = []
    if not v or v.get("codec_name") not in ("h264", "hevc"):
        problems.append("video must be H.264 or HEVC")
    if a and a.get("codec_name") != "aac":
        problems.append("audio must be AAC")
    if a and int(a.get("sample_rate") or 0) > 48000:
        problems.append(f"audio is {int(a['sample_rate']) // 1000} kHz; Reels take 48 kHz max")
    if v and v.get("pix_fmt") not in (None, "yuv420p", "yuvj420p"):
        problems.append(f"pixel format is {v.get('pix_fmt')}; Reels need yuv420p")
    if not REEL_MIN_S <= dur <= REEL_MAX_S:
        problems.append(f"is {dur:.1f}s; Reels must be {REEL_MIN_S}s to {REEL_MAX_S // 60} minutes")
    if mp4.stat().st_size > REEL_MAX_BYTES:
        problems.append(f"is {mp4.stat().st_size / 1e6:.0f} MB; the limit is 300 MB")
    if v:
        num, den = (v.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
        if float(num) / float(den or 1) > 60.5:
            problems.append("frame rate is over 60 fps")
    if problems:
        sys.exit(f"{mp4.name} " + "; ".join(problems) + ".")
    return caption, {"duration": dur, "width": int(v["width"]), "height": int(v["height"]), "audio": bool(a)}


LOCATIONS = Path(__file__).with_name("locations.json")


def load_location(sidecar):
    """<base>.location.json names a venue key from locations.json. Missing or unknown means no location."""
    if not sidecar.is_file():
        return None
    try:
        key = json.loads(sidecar.read_text(encoding="utf-8-sig")).get("key")
        venue = json.loads(LOCATIONS.read_text(encoding="utf-8"))["venues"].get(key)
    except (OSError, ValueError, AttributeError) as e:
        print(f"WARNING: ignoring {sidecar.name}: {e}")
        return None
    if not venue:
        print(f"WARNING: {sidecar.name} names unknown venue {key!r}; posting without a location.")
        return None
    return {"key": key, "name": venue["name"], "id": venue["id"]}


def create_container(path, token, location, **params):
    """POST a media container, with the location when there is one. A rejected location never blocks the post:
    the container is made again without it."""
    if location:
        try:
            return post(path, token, location_id=location["id"], **params)["id"]
        except ApiError as e:
            if "location" not in str(e).lower():
                raise
            print(f"WARNING: Instagram rejected the location {location['name']} ({e}); posting without it.")
            location.clear()
    return post(path, token, **params)["id"]


def load_job(folder):
    man_path = folder / "carousel.json"
    if not man_path.is_file():
        sys.exit(f"No carousel.json in {folder}")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    slides = man["slides"]
    caption = load_caption(folder / f"{man['slug']}.txt")
    missing = [s["file"] for s in slides if not (folder / s["file"]).is_file()]
    if missing:
        sys.exit(f"Slides missing from the review folder: {', '.join(missing)}")
    if not 1 <= len(slides) <= MAX_SLIDES:
        sys.exit(f"{len(slides)} slides. Instagram takes 1 to {MAX_SLIDES}.")
    if len(slides) == 1 and slides[0]["type"] == "video":
        sys.exit("A single video is a Reel. Publish it with --reel.")
    for s in slides:
        r = s["width"] / s["height"]
        if not 0.745 <= r <= 1.915:
            sys.exit(f"Slide {s['n']} is {s['width']}x{s['height']}, outside Instagram's 3:4 to 1.91:1 range.")
    return man, slides, caption


# ---------- commands ----------

def cmd_check(force_refresh=False):
    token = maybe_refresh(load_token(), force=force_refresh)
    me = get("me", token, fields="user_id,username,account_type")
    print(f"Connected: @{me.get('username')} ({me.get('account_type')}) id {me.get('user_id')}")
    try:
        lim = get(f"{me['user_id']}/content_publishing_limit", token, fields="quota_usage,config")
        d = (lim.get("data") or [{}])[0]
        cfg = d.get("config", {})
        print(f"API posts in the last 24h: {d.get('quota_usage')} of {cfg.get('quota_total')}")
    except ApiError as e:
        print(f"Could not read the publishing limit: {e}")
    print(f"Facebook Page: {fb_status()}")


def cmd_publish(folder, dry_run, fb=True, fb_only=False):
    folder = folder.resolve()
    done = folder / "published.json"
    if fb_only:
        fb_only_guard(done)
    elif done.exists():
        sys.exit(f"Already published: {json.loads(done.read_text()).get('permalink')}")
    man, slides, caption = load_job(folder)
    photos = [s for s in slides if s["type"] != "video"]
    if not fb_only:
        location = load_location(folder / f"{man['slug']}.location.json")
        token = maybe_refresh(load_token())
        me = get("me", token, fields="user_id,username")
        ig = me["user_id"]
        print(f"Account: @{me['username']}  Slides: {len(slides)}  Caption: {len(caption)} chars  "
              f"Location: {location['name'] if location else 'none'}")
    if fb or fb_only:
        print(f"Facebook Page: {fb_status()}  ({len(photos)} photo slides"
              f"{'; video slides stay Instagram only' if len(photos) < len(slides) else ''})")
    if fb_only and not photos:
        sys.exit("SKIP: no photo slides, so nothing to post to Facebook.")
    if dry_run:
        print("Dry run: checks passed, nothing hosted or posted.")
        return

    take_lock(folder.name)
    try:
        if fb_only:
            fb_share(done, lambda t: fb_photos([folder / s["file"] for s in photos], caption, t))
            return
        urls = host_slides(folder, slides)
        print("Slides hosted. Creating Instagram containers...")
        if len(slides) == 1:
            c = create_container(f"{ig}/media", token, location, image_url=urls[slides[0]["file"]], caption=caption)
            wait_ready(c, token, "Image")
            final = c
        else:
            kids = []
            for s in slides:
                if s["type"] == "video":
                    cid = post(f"{ig}/media", token, media_type="VIDEO", video_url=urls[s["file"]],
                               is_carousel_item="true")["id"]
                    wait_ready(cid, token, f"Slide {s['n']} video")
                else:
                    cid = post(f"{ig}/media", token, image_url=urls[s["file"]], is_carousel_item="true")["id"]
                    wait_ready(cid, token, f"Slide {s['n']}")
                kids.append(cid)
                print(f"  slide {s['n']} ready")
            final = create_container(f"{ig}/media", token, location, media_type="CAROUSEL",
                                     children=",".join(kids), caption=caption)
            wait_ready(final, token, "Carousel")
        media_id = post(f"{ig}/media_publish", token, creation_id=final)["id"]
        permalink = get(media_id, token, fields="permalink").get("permalink")
        done.write_text(json.dumps({
            "media_id": media_id, "permalink": permalink, "account": me["username"],
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "location": location.get("name") if location else None,
            "slides": [s["file"] for s in slides]}, indent=2), encoding="utf-8")
        print(f"PUBLISHED {permalink}")
        if fb and photos:
            fb_share(done, lambda t: fb_photos([folder / s["file"] for s in photos], caption, t))
        elif fb:
            print("Facebook skipped: no photo slides.")
    finally:
        release(folder)


def take_lock(label):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, label.encode())
        os.close(fd)
    except FileExistsError:
        sys.exit(f"Another publish is running (lock {LOCK}). If none is, delete the lock file and retry.")


def release(_label=None):
    try:
        unhost()
        print("Hosted media taken down.")
    except Exception as e:
        print(f"WARNING: could not take the hosted media down: {e}")
    LOCK.unlink(missing_ok=True)


def cmd_reel(mp4, dry_run, stage_only=False, fb=True, fb_only=False):
    """Publish one mp4 as a Reel (shared to the feed). Caption is the .txt with the same base name."""
    mp4 = mp4.resolve()
    done = mp4.with_name(mp4.stem + ".published.json")
    if fb_only:
        fb_only_guard(done)
    elif done.exists():
        sys.exit(f"Already published: {json.loads(done.read_text()).get('permalink')}")
    caption, info = load_reel(mp4)
    cover = mp4.with_name(mp4.stem + ".cover.jpg")
    cover = cover if cover.is_file() and not fb_only else None
    if not fb_only:
        location = load_location(mp4.with_name(mp4.stem + ".location.json"))
        token = maybe_refresh(load_token())
        me = get("me", token, fields="user_id,username")
        ig = me["user_id"]
        print(f"Account: @{me['username']}  Reel: {mp4.name} {info['width']}x{info['height']} "
              f"{info['duration']:.1f}s{'' if info['audio'] else ' (no audio)'}  Caption: {len(caption)} chars  "
              f"Location: {location['name'] if location else 'none'}  Cover: {cover.name if cover else 'none'}")
    if (fb or fb_only) and not stage_only:
        print(f"Facebook Page: {fb_status()}")
    if dry_run:
        print("Dry run: checks passed, nothing hosted or posted.")
        return

    take_lock(mp4.name)
    try:
        if fb_only:
            fb_share(done, lambda t: fb_video(mp4, caption, info, t))
            return
        urls = host_slides(mp4.parent, [{"file": mp4.name}] + ([{"file": cover.name}] if cover else []))
        print("Video hosted. Instagram is processing the Reel...")
        extra = {"cover_url": urls[cover.name]} if cover else {}
        c = create_container(f"{ig}/media", token, location, media_type="REELS", video_url=urls[mp4.name],
                             caption=caption, share_to_feed="true", **extra)
        wait_ready(c, token, "Reel", timeout_s=900)
        if stage_only:
            print(f"STAGED container {c}: Instagram accepted and processed the video. Not published.")
            return
        media_id = post(f"{ig}/media_publish", token, creation_id=c)["id"]
        permalink = get(media_id, token, fields="permalink").get("permalink")
        done.write_text(json.dumps({
            "media_id": media_id, "permalink": permalink, "account": me["username"],
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(), "file": mp4.name,
            "cover": cover.name if cover else None,
            "location": location.get("name") if location else None}, indent=2),
            encoding="utf-8")
        print(f"PUBLISHED {permalink}")
        if fb:
            fb_share(done, lambda t: fb_video(mp4, caption, info, t))
    finally:
        release()


def cmd_check_location(ids):
    """Check Facebook Page IDs as location_id: one unpublished image container each (they expire on their own)."""
    token = maybe_refresh(load_token())
    ig = get("me", token, fields="user_id")["user_id"]
    tmp = Path(tempfile.mkdtemp(prefix="okst_loc_"))
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=1080x1350",
                    "-frames:v", "1", str(tmp / "check.jpg")], check=True)
    take_lock("check-location")
    try:
        url = host_slides(tmp, [{"file": "check.jpg"}])["check.jpg"]
        for i in ids:
            try:
                post(f"{ig}/media", token, image_url=url, location_id=i)
                print(f"OK {i}")
            except ApiError as e:
                print(f"REJECTED {i}: {e}")
    finally:
        release()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Publish OKST posts to Instagram: a carousel review folder or one Reel.")
    ap.add_argument("folder", nargs="?", help="Review folder with carousel.json")
    ap.add_argument("--reel", help="Path to one mp4 to publish as a Reel (caption: same name .txt)")
    ap.add_argument("--stage-only", action="store_true",
                    help="With --reel: upload and let Instagram process it, but never publish (a live test)")
    ap.add_argument("--check", action="store_true", help="Verify the token and account, post nothing")
    ap.add_argument("--refresh", action="store_true", help="With --check, force a token refresh")
    ap.add_argument("--dry-run", action="store_true", help="Run every check but host and post nothing")
    ap.add_argument("--check-location", nargs="+", metavar="PAGE_ID",
                    help="Check Facebook Page IDs as Instagram locations before adding them to locations.json")
    ap.add_argument("--no-fb", action="store_true", help="Post to Instagram only, skip the OKSTProbs Facebook Page")
    ap.add_argument("--fb-only", action="store_true",
                    help="Post an item already on Instagram to the Facebook Page only (retry after a Facebook failure)")
    args = ap.parse_args()
    if args.fb_only and (args.no_fb or args.stage_only):
        ap.error("--fb-only cannot be combined with --no-fb or --stage-only")
    try:
        if args.check_location:
            cmd_check_location(args.check_location)
        elif args.check:
            cmd_check(args.refresh)
        elif args.reel:
            cmd_reel(Path(args.reel), args.dry_run, args.stage_only, not args.no_fb, args.fb_only)
        elif args.folder:
            cmd_publish(Path(args.folder), args.dry_run, not args.no_fb, args.fb_only)
        else:
            ap.error("give a review folder, --reel <mp4>, or --check")
    except ApiError as e:
        sys.exit(f"Instagram API error: {e}")


if __name__ == "__main__":
    main()
