"""Find new Oklahoma State Athletics interviews (any sport) on the channel's Videos and Live tabs (yt-dlp flat listing;
YouTube's RSS feed returns 404 as of 2026-11-03).
Prints one URL per line for videos not in state/seen.json and adds them to it.
--seed: mark everything currently in the feed as seen and print nothing."""
import json
import re
import sys
import subprocess
from pathlib import Path

CHANNEL = "UCiPbE1TBmEfgUWcQJUztxrQ"   # Oklahoma State Athletics
TABS = [f"https://www.youtube.com/channel/{CHANNEL}/videos", f"https://www.youtube.com/channel/{CHANNEL}/streams"]
SEEN = Path(__file__).resolve().parent.parent / "state" / "seen.json"
INTERVIEW = re.compile(r"news conference|press conference|postgame|post game|media availability|availability|"
                       r"interview|media day|presser|one.on.one", re.I)
SKIP = re.compile(r"highlights|hype|trailer|recap|hall of honor|cinematic|letter from|inside oklahoma state", re.I)


def listing():
    """(id, title) for the newest uploads on each tab, newest first."""
    out = []
    for tab in TABS:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", "15",
                            "--print", "%(id)s	%(title)s", tab], capture_output=True, text=True)
        if r.returncode and not r.stdout:
            print(f"listing failed for {tab}: {r.stderr.strip()[-300:]}", file=sys.stderr)
        out += [tuple(line.split("	", 1)) for line in r.stdout.splitlines() if "	" in line]
    if not out:
        sys.exit("No videos listed: YouTube listing failed.")
    return out


def main():
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    new = []
    for vid, title in listing():
        if vid in seen:
            continue
        keep = bool(INTERVIEW.search(title)) and not SKIP.search(title)
        seen[vid] = {"title": title, "queued": keep and "--seed" not in sys.argv}
        if keep and "--seed" not in sys.argv:
            new.append(vid)
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(seen, indent=1, ensure_ascii=False) + "\n")
    for vid in reversed(new):          # oldest first
        print(f"https://www.youtube.com/watch?v={vid}")


if __name__ == "__main__":
    main()
