"""Find new Oklahoma State interviews and press conferences (every sport) on YouTube and print one URL per line for
okst-clip to clip. Uses yt-dlp flat listings (YouTube's RSS feed returns 404 as of 2026-11-03).

Sources, in priority order:
  1. Oklahoma State Athletics (official): Videos + Live tabs. Football, plus basketball/wrestling when they upload them.
  2. Pistols Firing: films every media session (football players, Steve Lutz, Jacie Hoyt, David Taylor, wrestlers...),
     much of which the official channel never uploads. Clips from it carry a "🎥 Pistols Firing" credit (skill rule).
  3. The Daily O'Collegian: occasional basketball/football press conferences.
A secondary-source video is skipped when the official channel already has the same person on the same date.

Podcasts (kind "podcast", added 2026-11-20; clips carry a "🎥 <show>" credit):
  - Inside OSU Athletics (official, incl. Cowgirls IRL), Locked On Oklahoma State, Pistols Firing Podcast (CC TV - Carson
    Cunningham channel), The Reload (Pistols Firing channel). Every episode over 5 minutes whose title fits the show.
  - A show listed for the first time is seeded (its back catalog marked seen), so only new episodes are queued.

Rules:
- Scheduled or still-live streams are left out of state/seen.json until they end, so a later run queues them (2026-11-20:
  three pressers were queued while still "This live event will begin in N minutes" and never clipped).
- Only videos dated within MAX_AGE_DAYS (date read from the title, else upload_date) are queued; older ones are marked seen.
- At most DAILY_QUEUE clip runs per America/Chicago day (each run is a full Claude session); head coaches go first.
--seed: mark everything currently listed as seen and print nothing."""
import datetime as dt
import os
import json
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

CHI = ZoneInfo("America/Chicago")
OSU, PF, LO, CC = ("UCiPbE1TBmEfgUWcQJUztxrQ", "UCCuruNGNiEZlivs5e2ptFfw", "UCJ2Sagc-dj4Fnya4itFOZIw",
                   "UC0xwjPmRH3Zwe0_MbtSJnMQ")
YT = "https://www.youtube.com/channel/"
PL = "https://www.youtube.com/playlist?list="
# (source key, channel/show name for the credit, listing URL, kind, title pattern a podcast episode must match)
SOURCES = [
    ("official", "Oklahoma State Athletics", YT + OSU + "/streams", "interview", None),
    ("official", "Oklahoma State Athletics", YT + OSU + "/videos", "interview", None),
    ("pistolsfiring", "Pistols Firing", YT + PF + "/videos", "interview", None),
    ("ocollegian", "The Daily O'Collegian", YT + "UCoTJcRGMQcnByE7EijpQgvQ/videos", "interview", None),
    ("insideosu", "Inside OSU Athletics", YT + OSU + "/videos", "podcast", r"inside (oklahoma state|osu) athletics"),
    ("insideosu", "Inside OSU Athletics", PL + "PLRt0iiQWJhs8", "podcast", r"."),
    ("cowgirlsirl", "Cowgirls IRL", PL + "PLbiZXmECAGBw", "podcast", r"cowgirls? ?irl|cowgirls podcast"),
    ("lockedon", "Locked On Oklahoma State", YT + LO + "/videos", "podcast", r"."),
    ("lockedon", "Locked On Oklahoma State", YT + LO + "/streams", "podcast", r"."),
    ("pfpodcast", "Pistols Firing Podcast", YT + CC + "/videos", "podcast", r"pistols firing"),
    ("pfpodcast", "Pistols Firing Podcast", YT + CC + "/streams", "podcast", r"pistols firing"),
    ("thereload", "The Reload (Pistols Firing)", YT + PF + "/videos", "podcast", r"the reload"),
]
OSU_TOPIC = re.compile(r"oklahoma state|\bOSU\b|okstate|cowboys?|cowgirls?|pokes|stillwater|bedlam", re.I)
INSIDE = re.compile(r"inside (oklahoma state|osu) athletics|the reload", re.I)
SEEN = Path(__file__).resolve().parent.parent / "state" / "seen.json"
INTERVIEW = re.compile(r"news conference|press conference|postgame|post game|media availability|availability|"
                       r"interview|media day|presser|one.on.one|talks? with the media", re.I)
POST_OPP = re.compile(r"\bpost [A-Z]")          # Pistols Firing: "Steve Lutz post Central Arkansas"
SKIP = re.compile(r"highlights|hype|trailer|recap|hall of honor|cinematic|letter from|inside oklahoma state|"
                  r"the reload|preview|tail waggin|injury report", re.I)
COACHES = re.compile(r"head coach|eric morris|steve lutz|jacie hoyt|david taylor|josh holliday|kenny gajewski|"
                     r"coach", re.I)
MAX_AGE_DAYS = 3
DAILY_QUEUE = 12         # interviews and press conferences (they always go first)
PODCAST_QUEUE = 3        # podcast episodes, after interviews
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def title_date(title, upload_date):
    """Date of the session: '(11-17-2026)', '11/10/26', 'Nov. 16, 2026' in the title, else upload_date (YYYYMMDD)."""
    m = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b", title)
    if m:
        mo, d, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
    else:
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2}),? (\d{4})", title, re.I)
        if m:
            mo, d, y = MONTHS[m[1].lower()], int(m[2]), int(m[3])
        elif upload_date and upload_date.isdigit():
            y, mo, d = int(upload_date[:4]), int(upload_date[4:6]), int(upload_date[6:])
        else:
            return None
    today = dt.datetime.now(CHI).date()
    try:
        day = dt.date(y, mo, d)
    except ValueError:
        return None
    if day.year != today.year and abs((dt.date(today.year, mo, d) - today).days) <= 7:   # typo years ("Nov. 18, 2036")
        day = dt.date(today.year, mo, d)
    return day


def person(title):
    """Lowercase surname of the first named person, used to match the same session across channels."""
    m = re.search(r"([A-Z][a-z]+) ([A-Z][a-zA-Z']+)", title)
    return m[2].lower() if m else ""


def upload_day(vid):
    """upload_date for one video (flat listings leave it out); None when YouTube won't say."""
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--print", "%(upload_date)s",
                        f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
    d = r.stdout.strip()[:8]
    return dt.date(int(d[:4]), int(d[4:6]), int(d[6:])) if d.isdigit() and len(d) == 8 else None


def listing():
    """(source, channel, kind, pattern, id, live_status, upload_date, duration, title), newest first per source."""
    out = []
    for src, channel, tab, kind, pattern in SOURCES:
        if kind == "podcast" and os.environ.get("WATCH_PODCASTS") == "0":   # quick rechecks: interviews only
            continue
        items = ["-I", "-20:"] if "playlist?list=" in tab else ["--playlist-end", "20"]   # playlists list oldest first
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", *items,
                            "--print", "%(id)s\t%(live_status)s\t%(upload_date)s\t%(duration)s\t%(title)s", tab],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode and not r.stdout:
            print(f"listing failed for {tab}: {r.stderr.strip()[-300:]}", file=sys.stderr)
        out += [(src, channel, kind, pattern, *line.split("\t", 4)) for line in r.stdout.splitlines()
                if line.count("\t") >= 4]
    if not any(o[0] == "official" for o in out):
        sys.exit("No videos listed from the official channel: YouTube listing failed.")
    return out


def main():
    seed = "--seed" in sys.argv
    seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    today = dt.datetime.now(CHI).date()
    queued_today = sum(1 for v in seen.values() if v.get("queued") and v.get("queued_on") == today.isoformat())
    official = {(v.get("person"), v.get("day")) for v in seen.values() if v.get("source", "official") == "official"}
    known_sources = {v.get("source", "official") for v in seen.values()}
    pod_today = sum(1 for v in seen.values() if v.get("queued") and v.get("queued_on") == today.isoformat()
                    and v.get("kind") == "podcast")
    queued_today -= pod_today
    cands, pods = [], []
    live_wait = []                                         # interviews streaming right now: okst-watch rechecks every 2 min
    for src, channel, kind, pattern, vid, status, upload, duration, title in listing():
        if vid in seen or vid in {c[0] for c in pods}:
            continue
        if kind == "podcast":
            secs = float(duration) if duration.replace(".", "").isdigit() else 0
            fits = (re.search(pattern, title, re.I) and not (secs and secs < 300)
                    and OSU_TOPIC.search(title + " " + channel) and not re.search(r"highlights|trailer|#shorts", title, re.I))
            if not fits:
                continue                                   # not this show's episode: another source may claim it
            if src not in known_sources or seed:          # first listing of a show: seed its back catalog
                seen[vid] = {"title": title, "source": src, "channel": channel, "kind": kind, "queued": False,
                             "why": "seeded"}
                continue
            if status in ("is_upcoming", "is_live", "post_live"):
                print(f"waiting: {vid} {status} {title}", file=sys.stderr)
                continue
            day = title_date(title, upload) or upload_day(vid)
            entry = {"title": title, "source": src, "channel": channel, "kind": kind,
                     "day": day.isoformat() if day else None, "queued": False}
            if not day or (today - day).days > MAX_AGE_DAYS:
                entry["why"] = "too old"
                seen[vid] = entry
            else:
                pods.append((vid, entry))
            continue
        if INSIDE.search(title):                           # Inside OSU / The Reload episodes belong to podcast sources
            continue
        if status in ("is_upcoming", "is_live", "post_live"):   # not downloadable yet: check again next run
            print(f"waiting: {vid} {status} {title}", file=sys.stderr)
            if status != "is_upcoming":
                live_wait.append(vid)
            continue
        day = title_date(title, upload)
        entry = {"title": title, "source": src, "channel": channel, "person": person(title), "kind": kind,
                 "day": day.isoformat() if day else None, "queued": False}
        is_interview = bool((INTERVIEW.search(title) or (src != "official" and POST_OPP.search(title)))
                            and not SKIP.search(title))
        if seed or not is_interview:
            seen[vid] = entry
            continue
        if not day or (today - day).days > MAX_AGE_DAYS:
            entry["why"] = "too old"
            seen[vid] = entry
            continue
        if src != "official" and (entry["person"], entry["day"]) in official:
            entry["why"] = "official channel has it"
            seen[vid] = entry
            continue
        if src == "official":
            official.add((entry["person"], entry["day"]))
        cands.append((vid, entry))
    # official first, then head coaches, then oldest first
    cands.sort(key=lambda c: (c[1]["source"] != "official", not COACHES.search(c[1]["title"]), c[1]["day"] or ""))
    new = []
    for vid, entry in cands:
        if queued_today >= DAILY_QUEUE:
            entry["why"] = f"daily queue cap {DAILY_QUEUE}"
        else:
            entry.update(queued=True, queued_on=today.isoformat())
            queued_today += 1
            new.append(vid)
        seen[vid] = entry
    for vid, entry in sorted(pods, key=lambda c: c[1]["day"] or ""):
        if pod_today >= PODCAST_QUEUE:
            entry["why"] = f"daily podcast cap {PODCAST_QUEUE}"
        else:
            entry.update(queued=True, queued_on=today.isoformat())
            pod_today += 1
            new.append(vid)
        seen[vid] = entry
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    Path("waiting.txt").write_text("".join(f"{v}\n" for v in dict.fromkeys(live_wait)), encoding="utf-8")
    for vid in new:                                        # interviews first, then podcasts
        print(f"https://www.youtube.com/watch?v={vid}\t{seen[vid].get('kind', 'interview')}")


if __name__ == "__main__":
    main()
