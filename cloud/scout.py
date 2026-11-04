"""Find new Oklahoma State posts worth reposting and print their URLs, one per line (the okst-scout workflow
queues an okst-repost auto run for each). No cap: every candidate that passes the filters is printed; Claude
decides in the repost run whether it actually ships.

Sources (all free, no logins, no cookies):
  TikTok hashtags in cloud/scout.json   tikwm.com public API (posts wait until they pass tiktok_min_views)
  TikTok creators in scout.json         yt-dlp creator listing through WARP (flaky; hashtags are the main TikTok source)
  Instagram creators in scout.json      Graph API Business Discovery (FB_PAGE_TOKEN from Doppler)
  Posts that tag @okstprobs             Graph API /tags
  YouTube Shorts                        yt-dlp search sorted by upload date

Run from the repo root: doppler run -p okst -c dev -- python cloud/scout.py
State: state/scout-seen.json (every post id this has looked at; pruned after 21 days)."""
import calendar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "cloud" / "scout.json").read_text(encoding="utf-8"))
SEEN_F = ROOT / "state" / "scout-seen.json"
REPOSTED_F = ROOT / "state" / "reposted.jsonl"
IG_ID = "17841434949988123"   # @okstprobs
NOW = time.time()
MAX_AGE = CFG.get("max_age_hours", 48) * 3600


def log(*a):
    print(*a, file=sys.stderr)


def keywords():
    words = [w.lower() for w in CFG["keywords"]]
    roster = ROOT / "chopify" / "OSU_ROSTER.txt"
    if roster.exists():
        for line in roster.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and len(line.split()) >= 2:
                words.append(line.lower())
    return words


KW = keywords()
BLOCK = {b.lower().lstrip("@") for b in CFG.get("block", [])}


def is_osu(text):
    t = (text or "").lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t) for w in KW)


def ytdlp_json(url, items):
    for attempt in range(2):   # TikTok creator pages fail intermittently ("Failed to parse JSON"); a retry usually works
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "-I", items, "-j", url],
                           capture_output=True, text=True, timeout=180)
        if r.stdout.strip() or not r.returncode:
            break
        time.sleep(8 * (attempt + 1))
    if r.returncode and not r.stdout:
        log(f"  yt-dlp failed on {url}: {r.stderr.strip().splitlines()[-1:] or ''}")
    out = []
    for line in r.stdout.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def tikwm(path, **params):
    """Free public TikTok API (tikwm.com, about 1 request a second). Direct first, then through WARP if it is up."""
    try:
        from curl_cffi import requests as cr
    except ImportError:
        log("  curl_cffi missing: TikTok hashtags skipped")
        return {}
    routes = [None, {"https": "socks5h://127.0.0.1:40000"}]
    for attempt in range(4):
        time.sleep(1.3)
        try:
            r = cr.get("https://www.tikwm.com/api/" + path, params=params, impersonate="chrome", timeout=30,
                       proxies=routes[attempt % 2])
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0:
                    return d.get("data") or {}
                log(f"  tikwm {path}: {d.get('msg')}")
            else:
                log(f"  tikwm {path}: HTTP {r.status_code}")
        except Exception as e:
            log(f"  tikwm {path}: {type(e).__name__}")
        time.sleep(4 * (attempt + 1))
    return {}


def graph(path, **params):
    tok = os.environ.get("FB_PAGE_TOKEN")
    if not tok:
        log("  FB_PAGE_TOKEN missing: Instagram sources skipped")
        return {}
    params["access_token"] = tok
    url = f"https://graph.facebook.com/v23.0/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message")
        except Exception:
            msg = f"HTTP {e.code}"
        log(f"  Graph API error on {path.split('?')[0]}: {msg}")
        return {}


def ig_time(ts):
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def collect():
    # dicts: key, url, who, when (epoch or None), text, score, osu_only, src (source name), min_views (optional)
    cands = []
    min_views = CFG.get("tiktok_min_views", 0)
    for tag in CFG.get("tiktok_hashtags", []):
        found = tikwm("challenge/search", keywords=tag, count=5).get("challenge_list") or []
        cid = next((c.get("id") for c in found if (c.get("cha_name") or "").lower() == tag.lower()), None)
        if not cid:
            log(f"  TikTok #{tag}: hashtag not found")
            continue
        for v in tikwm("challenge/posts", challenge_id=cid, count=30, cursor=0).get("videos") or []:
            user = (v.get("author") or {}).get("unique_id") or ""
            vid = str(v.get("video_id") or "")
            if not user or not vid:
                continue
            cands.append({"key": f"tt:{vid}", "url": f"https://www.tiktok.com/@{user}/video/{vid}", "who": user,
                          "when": v.get("create_time"), "text": v.get("title") or "", "score": v.get("play_count") or 0,
                          "osu_only": True, "src": f"tt-tag:{tag}", "min_views": min_views})
    for src in CFG.get("tiktok", []):
        user = src["user"]
        for v in ytdlp_json(f"https://www.tiktok.com/@{user}", "1:8"):
            vid = str(v.get("id") or "")
            if not vid:
                continue
            cands.append({"key": f"tt:{vid}", "url": f"https://www.tiktok.com/@{user}/video/{vid}", "who": user,
                          "when": v.get("timestamp"), "text": f"{v.get('title') or ''} {v.get('description') or ''}",
                          "score": v.get("view_count") or 0, "osu_only": src.get("osu_only", False), "src": f"tt:{user}"})
    for src in CFG.get("instagram", []):
        user = src["user"]
        d = graph(IG_ID, fields=f"business_discovery.username({user})"
                                "{media.limit(8){id,caption,media_type,permalink,timestamp,like_count,comments_count}}")
        for m in (d.get("business_discovery", {}).get("media", {}).get("data") or []):
            cands.append({"key": f"ig:{m['id']}", "url": m.get("permalink"), "who": user, "when": ig_time(m.get("timestamp", "")),
                          "text": m.get("caption") or "", "score": (m.get("like_count") or 0) * 20,
                          "osu_only": src.get("osu_only", False), "src": f"ig:{user}"})
    if CFG.get("instagram_tags"):
        d = graph(f"{IG_ID}/tags", fields="id,caption,media_type,permalink,timestamp,username,like_count", limit=25)
        for m in d.get("data") or []:
            cands.append({"key": f"ig:{m['id']}", "url": m.get("permalink"), "who": m.get("username") or "",
                          "when": ig_time(m.get("timestamp", "")), "text": m.get("caption") or "",
                          "score": 10**9, "osu_only": True, "src": "ig-tags"})   # tagging @okstprobs = about OSU
    skip_ch = [c.lower() for c in CFG.get("youtube_skip_channels", [])]
    for q in CFG.get("youtube_queries", []):
        url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": q, "sp": "CAI="})
        for v in ytdlp_json(url, "1:15"):
            ch = (v.get("channel") or "").lower()
            dur = v.get("duration") or 0
            if not v.get("id") or not dur or dur > CFG.get("youtube_max_seconds", 90):
                continue
            if any(s in ch for s in skip_ch):
                continue
            cands.append({"key": f"yt:{v['id']}", "url": f"https://www.youtube.com/shorts/{v['id']}",
                          "who": v.get("channel") or "", "when": None, "text": v.get("title") or "",
                          "score": v.get("view_count") or 0, "osu_only": False, "src": f"yt:{q}"})
    return cands


def main():
    seen = json.loads(SEEN_F.read_text()) if SEEN_F.exists() else {}
    seen = {k: t for k, t in seen.items() if NOW - t < 21 * 86400}
    posted = set()
    if REPOSTED_F.exists():
        for line in REPOSTED_F.read_text(encoding="utf-8").splitlines():
            try:
                posted.add(json.loads(line).get("url"))
            except ValueError:
                pass
    picks, by_key = [], {}
    for c in collect():
        by_key.setdefault(c["key"], c)
    for key, c in by_key.items():
        why = None
        if key in seen:
            continue
        new_source = f"src:{c['src']}" not in seen
        if not c["url"] or c["url"] in posted:
            why = "already posted"
        elif c["who"].lower().lstrip("@") in BLOCK:
            why = "blocked account"
        elif c["when"] and NOW - c["when"] > MAX_AGE:
            why = "too old"
        elif not c["osu_only"] and not is_osu(c["text"]):
            why = "not about OSU"
        elif new_source:
            why = "first run baseline"   # a source's first run only records what exists; it never floods old posts
        elif c.get("min_views") and c["score"] < c["min_views"]:
            log(f"  wait {c['url']} ({c['score']} views, rechecked next run)")
            continue   # not marked seen: it gets another look until it is too old
        seen[key] = int(NOW)
        if why:
            log(f"  skip {c['url']} ({why})")
        else:
            picks.append(c)
    for c in by_key.values():
        seen[f"src:{c['src']}"] = int(NOW)   # refreshed every run, so source markers never age out
    SEEN_F.parent.mkdir(exist_ok=True)
    SEEN_F.write_text(json.dumps(seen, indent=0, sort_keys=True))
    for c in sorted(picks, key=lambda c: -c["score"]):
        log(f"  QUEUE {c['url']} (@{c['who']})")
        print(c["url"])


if __name__ == "__main__":
    main()
