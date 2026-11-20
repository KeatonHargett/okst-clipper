"""Find new Oklahoma State interviews and press conferences (every sport) on YouTube and print one URL per line for
okst-clip to clip. Uses yt-dlp flat listings (YouTube's RSS feed returns 404 as of 2026-11-03).

Sources, in priority order:
  1. Oklahoma State Athletics (official): Videos + Live tabs. Football, plus basketball/wrestling when they upload them.
  2. Pistols Firing: films every media session (football players, Steve Lutz, Jacie Hoyt, David Taylor, wrestlers...),
     much of which the official channel never uploads. Clips from it carry a "🎥 Pistols Firing" credit (skill rule).
  3. The Daily O'Collegian: occasional basketball/football press conferences.
A secondary-source video is skipped when the official channel already has the same person on the same date.

Rules:
- Scheduled or still-live streams are left out of state/seen.json until they end, so a later run queues them (2026-11-20:
  three pressers were queued while still "This live event will begin in N minutes" and never clipped).
- Only videos dated within MAX_AGE_DAYS (date read from the title, else upload_date) are queued; older ones are marked seen.
- At most DAILY_QUEUE clip runs per America/Chicago day (each run is a full Claude session); head coaches go first.
--seed: mark everything currently listed as seen and print nothing."""
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

CHI = ZoneInfo("America/Chicago")
SOURCES = [
    ("official", "Oklahoma State Athletics", "https://www.youtube.com/channel/UCiPbE1TBmEfgUWcQJUztxrQ/streams"),
    ("official", "Oklahoma State Athletics", "https://www.youtube.com/channel/UCiPbE1TBmEfgUWcQJUztxrQ/videos"),
    ("pistolsfiring", "Pistols Firing", "https://www.youtube.com/channel/UCCuruNGNiEZlivs5e2ptFfw/videos"),
    ("ocollegian", "The Daily O'Collegian", "https://www.youtube.com/channel/UCoTJcRGMQcnByE7EijpQgvQ/videos"),
]
SEEN = Path(__file__).resolve().parent.parent / "state" / "seen.json"
INTERVIEW = re.compile(r"news conference|press conference|postgame|post game|media availability|availability|"
                       r"interview|media day|presser|one.on.one|talks? with the media", re.I)
POST_OPP = re.compile(r"\bpost [A-Z]")          # Pistols Firing: "Steve Lutz post Central Arkansas"
SKIP = re.compile(r"highlights|hype|trailer|recap|hall of honor|cinematic|letter from|inside oklahoma state|"
                  r"the reload|preview|tail waggin|injury report", re.I)
COACHES = re.compile(r"head coach|eric morris|steve lutz|jacie hoyt|david taylor|josh holliday|kenny gajewski|"
                     r"coach", re.I)
MAX_AGE_DAYS = 3
DAILY_QUEUE = 8
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


def listing():
    """(source, channel, id, live_status, upload_date, title), newest first within each source."""
    out = []
    for src, channel, tab in SOURCES:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", "20",
                            "--print", "%(id)s\t%(live_status)s\t%(upload_date)s\t%(title)s", tab],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode and not r.stdout:
            print(f"listing failed for {tab}: {r.stderr.strip()[-300:]}", file=sys.stderr)
        out += [(src, channel, *line.split("\t", 3)) for line in r.stdout.splitlines() if line.count("\t") >= 3]
    if not any(o[0] == "official" for o in out):
        sys.exit("No videos listed from the official channel: YouTube listing failed.")
    return out


def main():
    seed = "--seed" in sys.argv
    seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    today = dt.datetime.now(CHI).date()
    queued_today = sum(1 for v in seen.values() if v.get("queued") and v.get("queued_on") == today.isoformat())
    official = {(v.get("person"), v.get("day")) for v in seen.values() if v.get("source", "official") == "official"}
    cands = []
    for src, channel, vid, status, upload, title in listing():
        if vid in seen:
            continue
        if status in ("is_upcoming", "is_live", "post_live"):   # not downloadable yet: check again next run
            print(f"waiting: {vid} {status} {title}", file=sys.stderr)
            continue
        day = title_date(title, upload)
        entry = {"title": title, "source": src, "channel": channel, "person": person(title),
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
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for vid in new:
        print(f"https://www.youtube.com/watch?v={vid}")


if __name__ == "__main__":
    main()
