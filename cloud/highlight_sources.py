"""Find highlight sources for one finished Oklahoma State game (okst-highlights).

  doppler run -p okst -c dev -- python cloud/highlight_sources.py --facts out/facts.json --out out/sources.json \
      [--wait] [--download chopify/work/hl]

Sources, in priority order:
  network   vertical videos the network, league, and OSU accounts in cloud/highlights.json posted on Instagram after
            kickoff that are about this game (Graph API Business Discovery, FB_PAGE_TOKEN). Reposted through okst-repost.
  youtube   the official highlights videos on the Oklahoma State Athletics and Big 12 YouTube channels (yt-dlp, WARP),
            for the recut formats. Kinds: scoring (all scoring plays), condensed, highlights, player, defense.
Links already in state/reposted.jsonl or state/highlights-log.jsonl are dropped (shared duplicate check).

--wait polls every 3 minutes until there is a highlights video and either a scoring plays video exists or
proceed_after_minutes have passed since the final, or source_wait_minutes run out (whatever exists then is used).
--download saves the best scoring plays video and the best highlights video (1080p preferred) plus their contact
sheets, and each network video's thumbnail.
"""
import argparse
import datetime as dt
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
CFG = json.loads((ROOT / "cloud" / "highlights.json").read_text(encoding="utf-8"))
IG_ID = "17841434949988123"   # @okstprobs
KINDS = [("scoring", r"scoring plays|all \w+ touchdowns|every touchdown"), ("condensed", r"condensed"),
         ("defense", r"defensive highlights"), ("player", r"^[A-Z][\w.'-]+ [A-Z][\w.'-]+ highlights"),
         ("highlights", r"highlights")]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def parse_time(s):
    s = s.replace("Z", "+00:00")
    if re.match(r".*T\d\d:\d\d[+-]", s):
        s = s[:16] + ":00" + s[16:]
    if re.search(r"[+-]\d{4}$", s):
        s = s[:-2] + ":" + s[-2:]
    return dt.datetime.fromisoformat(s)


def already_used():
    used = set()
    for f in ("reposted.jsonl", "highlights-log.jsonl"):
        p = ROOT / "state" / f
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                used.update(v for k, v in r.items() if k in ("url", "source") and v)
    return used


def graph(path, **params):
    tok = os.environ.get("FB_PAGE_TOKEN")
    if not tok:
        log("FB_PAGE_TOKEN missing: Instagram sources skipped")
        return {}
    params["access_token"] = tok
    try:
        with urllib.request.urlopen(f"https://graph.facebook.com/v23.0/{path}?" + urllib.parse.urlencode(params),
                                    timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message")
        except Exception:
            msg = f"HTTP {e.code}"
        log(f"  Graph API error: {msg}")
        return {}


def names(team):
    out = {team["location"], team["display"], team["abbr"]}
    loc = team["location"] or ""
    out.add(loc.replace("State", "St"))
    if loc.endswith(" State"):
        out.add(loc[:-6] + "-State")          # "K-State" style shorthand is matched below too
        out.add(loc[0] + "-State")
    return {n.lower() for n in out if n and len(n) > 2}


def mentions(text, words):
    t = (text or "").lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t) for w in words)


def network_clips(f, sport, used, kick):
    clips = []
    opp = names(f["opp"])
    for user in sport["instagram_sources"]:
        d = graph(IG_ID, fields=f"business_discovery.username({user}){{media.limit(40){{id,caption,media_type,"
                                f"media_product_type,permalink,timestamp,thumbnail_url,like_count}}}}")
        for m in (d.get("business_discovery") or {}).get("media", {}).get("data", []):
            if m.get("media_type") != "VIDEO" or not m.get("timestamp"):
                continue
            when = parse_time(m["timestamp"])
            cap = m.get("caption") or ""
            if when < kick - dt.timedelta(minutes=15) or m.get("permalink") in used:
                continue
            osu_strong = mentions(cap, [w for w in CFG["osu_words"] if w != "osu"]) or mentions(cap, ["osu"])
            if not (osu_strong or (mentions(cap, ["cowboys", "cowboy"]) and mentions(cap, opp))):
                continue
            clips.append({"account": user, "url": m["permalink"], "id": m["id"], "posted": m["timestamp"],
                          "caption": cap[:600], "likes": m.get("like_count"), "thumbnail": m.get("thumbnail_url"),
                          "product": m.get("media_product_type")})
    log(f"  network clips about this game: {len(clips)}")
    return clips


def yt_list(url, n=25):
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", str(n),
                        "--print", "%(id)s\t%(duration)s\t%(title)s", url], capture_output=True, text=True, timeout=240)
    if r.returncode and not r.stdout:
        log(f"  listing failed for {url}: {r.stderr.strip()[-200:]}")
    return [l.split("\t", 2) for l in r.stdout.splitlines() if l.count("\t") >= 2]


def yt_meta(vid):
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "-J",
                        f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True, timeout=240)
    try:
        return json.loads(r.stdout)
    except ValueError:
        log(f"  metadata failed for {vid}: {r.stderr.strip()[-200:]}")
        return {}


def youtube_videos(f, sport, used, kick, known):
    vids = []
    opp = names(f["opp"])
    for ch in sport["youtube_channels"]:
        for vid, dur, title in yt_list(ch["url"]):
            t = title.lower()
            url = f"https://www.youtube.com/watch?v={vid}"
            if url in used or not mentions(t, opp) or any(w in t for w in CFG["youtube_skip_words"]):
                continue
            if not any(w in t for w in CFG["youtube_title_words"]):
                continue
            if ch["name"] != "Oklahoma State Athletics" and not mentions(t, ["oklahoma state", "okstate", "oklahoma st"]):
                continue
            meta = known.get(vid) or yt_meta(vid)
            if not meta:
                continue
            known[vid] = meta
            ts = meta.get("timestamp") or meta.get("release_timestamp")
            if ts and dt.datetime.fromtimestamp(ts, dt.timezone.utc) < kick:
                continue        # an older meeting with the same opponent
            kind = next(k for k, pat in KINDS if re.search(pat, title, re.I))
            vids.append({"id": vid, "url": url, "title": title, "channel": ch["name"], "kind": kind,
                         "duration": meta.get("duration") or (float(dur) if dur not in ("NA", "None") else None),
                         "height": meta.get("height"), "uploaded": ts})
    log(f"  YouTube highlights videos: {[(v['channel'], v['kind'], v['height']) for v in vids]}")
    return vids


def pick_downloads(vids):
    """Best scoring plays video and best highlights video: 1080p first, then the longest (covers the most plays)."""
    def rank(v):
        return (-(v.get("height") or 0), -(v.get("duration") or 0), v["channel"] != "Oklahoma State Athletics")
    out = []
    for kinds in (("scoring",), ("highlights", "condensed")):
        cands = sorted((v for v in vids if v["kind"] in kinds), key=rank)
        if cands:
            out.append(cands[0])
    return out


def download(vids, net, dest):
    dest = Path(dest)
    for v in pick_downloads(vids):
        d = dest / v["id"]
        d.mkdir(parents=True, exist_ok=True)
        src = d / "source.mp4"
        if not src.exists():
            cmd = [sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=1080][vcodec^=avc1]+ba/bv*[height<=1080]+ba/b",
                   "--merge-output-format", "mp4", "-N", "4", "--no-playlist", "-o", str(d / "source.%(ext)s"), v["url"]]
            for attempt in range(3):
                if subprocess.run(cmd).returncode == 0 and src.exists():
                    break
                time.sleep(10 * (attempt + 1))
        if src.exists():
            v["file"] = str(src)
            subprocess.run([sys.executable, str(ROOT / "chopify" / "okst_highlights.py"), "sheets", str(src),
                            "--out", str(d / "sheets")], check=False)
            v["sheets"] = str(d / "sheets")
        else:
            log(f"  download failed: {v['url']}")
    for c in net:
        if c.get("thumbnail"):
            p = dest / "net" / f"{c['id']}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                urllib.request.urlretrieve(c["thumbnail"], p)
                c["thumbnail_file"] = str(p)
            except Exception as e:
                log(f"  thumbnail failed for {c['url']}: {e}")


def ready(vids, final_at, start):
    hl = any(v["kind"] in ("highlights", "condensed", "scoring") for v in vids)
    scoring = any(v["kind"] == "scoring" for v in vids)
    since = (dt.datetime.now(dt.timezone.utc) - (final_at or start)).total_seconds() / 60
    return hl and (scoring or since >= CFG["proceed_after_minutes"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--download", help="folder for downloaded videos, sheets, thumbnails")
    a = ap.parse_args()
    f = json.loads(Path(a.facts).read_text(encoding="utf-8"))
    sport = CFG["sports"][f["sport"]]
    kick = parse_time(f["kickoff"])
    final_at = parse_time(f["final_at"]) if f.get("final_at") else None
    start = dt.datetime.now(dt.timezone.utc)
    used, known = already_used(), {}
    while True:
        net = network_clips(f, sport, used, kick)
        vids = youtube_videos(f, sport, used, kick, known)
        if not a.wait or ready(vids, final_at, start):
            break
        if (dt.datetime.now(dt.timezone.utc) - start).total_seconds() > CFG["source_wait_minutes"] * 60:
            log("  source wait ran out; using what exists")
            break
        log(f"  {dt.datetime.now(dt.timezone.utc):%H:%M}Z waiting for highlights videos")
        time.sleep(180)
    if a.download:
        download(vids, net, a.download)
    out = {"found_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "network": net,
           "youtube": vids}
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"network {len(net)}, youtube {len(vids)} "
          f"({', '.join(sorted({v['kind'] for v in vids})) or 'none'}), downloaded "
          f"{sum(1 for v in vids if v.get('file'))}")


if __name__ == "__main__":
    main()
