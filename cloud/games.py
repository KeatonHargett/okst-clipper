"""Oklahoma State game data for okst-highlights, from ESPN's public site API (no key).

Subcommands (run from the repo root; stdlib only):
  due                     print "<sport> <event id>" for OSU games that are live, start soon, or just ended (okst-kick)
  gate --event --sport [--replay] [--hold]
                          kill switch, repeat, per game cap and first game hold checks; writes go/hold/posts_left/reason
                          to $GITHUB_OUTPUT (and prints them)
  wait --event --sport [--minutes N]
                          poll every 2 minutes until the game is final (exit 0), canceled or postponed (exit 2),
                          or N (default final_wait_minutes) minutes pass (exit 1)
  facts --event --sport --out out/facts.json
                          final score, records, ranks, scoring plays, leaders, and the verified storyline headlines
  switch                  exit 1 (prints OFF) when state/highlights_off exists on origin/main, else exit 0 (ON)

Endpoints: site.api.espn.com/apis/site/v2/sports/<path>/teams/197/schedule and .../summary?event=<id>.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "cloud" / "highlights.json").read_text(encoding="utf-8"))
STATE = ROOT / "state"
OFF = STATE / "highlights_off"
GAMES = STATE / "highlights-games.json"
FIRST = STATE / "highlights-first.json"
LOG = STATE / "highlights-log.jsonl"
API = "https://site.api.espn.com/apis/site/v2/sports"
TEAM = CFG["team_id"]
UNRANKED = 99


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(s):
    s = s.replace("Z", "+00:00")
    if len(s) == 22:                       # "2026-11-07T18:00+00:00" has no seconds
        s = s[:16] + ":00" + s[16:]
    return dt.datetime.fromisoformat(s)


def espn(url, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "okst-highlights"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == tries - 1:
                raise SystemExit(f"ESPN request failed: {url} ({e})")
            time.sleep(5 * (attempt + 1))


def sport_cfg(sport):
    s = CFG["sports"].get(sport)
    if not s:
        raise SystemExit(f"Unknown sport {sport!r}; known: {', '.join(CFG['sports'])}")
    return s


def schedule(sport):
    return espn(f"{API}/{sport_cfg(sport)['espn']}/teams/{TEAM}/schedule").get("events", [])


def summary(sport, event):
    return espn(f"{API}/{sport_cfg(sport)['espn']}/summary?event={event}")


def jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# ---------- due (okst-kick) ----------

def cmd_due(_args):
    t = now()
    for sport, s in CFG["sports"].items():
        if not s.get("enabled"):
            continue
        try:
            events = schedule(sport)
        except SystemExit as e:
            print(e, file=sys.stderr)
            continue
        for e in events:
            c = e["competitions"][0]
            state = c["status"]["type"]["state"]
            kick = parse_time(e["date"])
            valid = e.get("timeValid", c.get("timeValid", True))
            soon = valid and dt.timedelta(0) <= kick - t <= dt.timedelta(minutes=s.get("start_before_minutes", 30))
            recent = state == "post" and t - kick <= dt.timedelta(hours=s.get("game_hours", 4) + 1)
            if state == "in" or (state == "pre" and soon) or recent:
                print(f"{sport} {e['id']}")


# ---------- gate ----------

def kill_switch_on():
    """True when state/highlights_off exists on origin/main (falls back to the checkout)."""
    if subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=ROOT, capture_output=True).returncode == 0:
        return subprocess.run(["git", "cat-file", "-e", "origin/main:state/highlights_off"], cwd=ROOT,
                              capture_output=True).returncode == 0
    return OFF.exists()


def posted_for(event):
    return [r for r in jsonl(LOG) if str(r.get("event")) == str(event) and r.get("instagram")]


def cmd_gate(a):
    out = {"go": "true", "hold": "true" if a.hold else "false", "posts_left": str(CFG["max_posts_per_game"]),
           "reason": "ok"}
    s = CFG["sports"].get(a.sport)
    games = json.loads(GAMES.read_text()) if GAMES.exists() else {}
    first = json.loads(FIRST.read_text()) if FIRST.exists() else {}
    left = CFG["max_posts_per_game"] - len(posted_for(a.event))
    if not s or not s.get("enabled"):
        out.update(go="false", reason=f"sport {a.sport} is not enabled in cloud/highlights.json")
    elif kill_switch_on():
        out.update(go="false", reason="kill switch is on (state/highlights_off)")
    elif games.get(a.event, {}).get("status") == "done" and not (a.replay and a.hold):
        out.update(go="false", reason=f"game {a.event} already done")
    elif left <= 0 and not a.hold:
        out.update(go="false", reason=f"game {a.event} already has {CFG['max_posts_per_game']} highlight posts")
    if CFG.get("first_game_hold") and a.sport not in first:
        out.update(hold="true")
        if out["reason"] == "ok":
            out["reason"] = f"first {a.sport} game: hold test"
    out["posts_left"] = str(max(left, 0) if out["hold"] == "false" else CFG["max_posts_per_game"])
    for k, v in out.items():
        print(f"{k}={v}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.writelines(f"{k}={v}\n" for k, v in out.items())


def cmd_switch(_args):
    if kill_switch_on():
        print("OFF: state/highlights_off exists, post nothing")
        sys.exit(1)
    print("ON")


# ---------- wait ----------

def cmd_wait(a):
    minutes = a.minutes or CFG["final_wait_minutes"]
    limit = time.time() + minutes * 60
    last = None
    while True:
        st = summary(a.sport, a.event)["header"]["competitions"][0]["status"]["type"]
        if st.get("completed") or st.get("state") == "post":
            if st.get("name") in ("STATUS_CANCELED", "STATUS_POSTPONED", "STATUS_FORFEIT"):
                print(f"Game {a.event}: {st.get('description')}")
                sys.exit(2)
            print(f"FINAL {a.event} at {now():%H:%M}Z")
            return
        if st.get("name") in ("STATUS_CANCELED", "STATUS_POSTPONED", "STATUS_SUSPENDED"):
            print(f"Game {a.event}: {st.get('description')}")
            sys.exit(2)
        if st.get("shortDetail") != last:
            last = st.get("shortDetail")
            print(f"{now():%H:%M}Z {last}", flush=True)
        if time.time() > limit:
            print(f"Not final after {minutes} minutes.")
            sys.exit(1)
        time.sleep(120)


# ---------- facts ----------

def team_info(comp, sched_comp):
    t = comp["team"]
    rec = {r.get("type"): r.get("displayValue") or r.get("summary") for r in comp.get("record") or []}
    rank = UNRANKED
    for sc in sched_comp or []:
        if sc["team"]["id"] == t["id"]:
            rank = (sc.get("curatedRank") or {}).get("current") or UNRANKED
    return {"id": t["id"], "location": t.get("location"), "name": t.get("name"), "abbr": t.get("abbreviation"),
            "display": t.get("displayName"), "color": "#" + (t.get("color") or "333333"),
            "logo": f"https://a.espncdn.com/i/teamlogos/ncaa/500/{t['id']}.png",
            "score": int(comp.get("score") or 0), "home": comp.get("homeAway") == "home",
            "record": rec.get("total"), "conf_record": rec.get("vsconf"), "rank": rank if rank < UNRANKED else None,
            "winner": bool(comp.get("winner"))}


def scoring_plays(d, osu, opp):
    if d.get("scoringPlays"):
        plays = d["scoringPlays"]
    else:
        plays = [p for p in d.get("plays") or [] if p.get("scoringPlay")]
    out = []
    for p in plays:
        team = (p.get("team") or {}).get("id") or (p.get("team") or {}).get("abbreviation")
        home, away = int(p.get("homeScore") or 0), int(p.get("awayScore") or 0)
        osu_s, opp_s = (home, away) if osu["home"] else (away, home)
        out.append({"period": (p.get("period") or {}).get("number"), "clock": (p.get("clock") or {}).get("displayValue"),
                    "osu": str(team) in (osu["id"], osu["abbr"]), "type": (p.get("type") or {}).get("text"),
                    "text": p.get("text"), "osu_score": osu_s, "opp_score": opp_s,
                    "value": p.get("scoreValue")})
    return out


def leaders(d, osu_id):
    out = []
    for g in d.get("leaders") or []:
        if str(g.get("team", {}).get("id")) != osu_id:
            continue
        for c in g.get("leaders") or []:
            for l in (c.get("leaders") or [])[:1]:
                out.append({"stat": c.get("displayName") or c.get("name"),
                            "player": l["athlete"].get("displayName"), "value": l.get("displayValue")})
    return out


def last_wallclock(d):
    walls = [p.get("wallclock") for p in d.get("plays") or [] if p.get("wallclock")]
    for drive in (d.get("drives") or {}).get("previous") or []:
        walls += [p.get("wallclock") for p in drive.get("plays") or [] if p.get("wallclock")]
    return max(walls) if walls else None


def win_streak(events, event_id):
    n = 0
    for e in events:
        c = e["competitions"][0]
        if not c["status"]["type"].get("completed"):
            continue
        me = next((t for t in c["competitors"] if t["team"]["id"] == TEAM), None)
        n = n + 1 if me and me.get("winner") else 0
        if e["id"] == str(event_id):
            return n
    return 0


def storylines(f, sport, events):
    osu, opp = f["osu"], f["opp"]
    o_name = (opp["location"] or opp["display"]).upper()
    lines = []
    if not osu["winner"]:
        return lines
    if osu["record"]:
        lines.append({"id": "record", "headline": f"COWBOYS MOVE TO {osu['record']}", "source": "ESPN record (total)"})
    conf_games = [sum(int(x) for x in (t["conf_record"] or "0-0").split("-")[:2]) for t in (osu, opp)]
    if f.get("conference_game") and min(conf_games) > 0:   # ESPN flags some non conference games too
        lines.append({"id": "conference", "headline": f"COWBOYS MOVE TO {osu['conf_record']} IN BIG 12 PLAY",
                      "source": "ESPN record (vsconf)"})
    if opp["rank"]:
        lines.append({"id": "ranked_win", "headline": f"COWBOYS TAKE DOWN NO. {opp['rank']} {o_name}",
                      "source": "ESPN curatedRank"})
    streak = win_streak(events, f["event"])
    if streak >= 3:
        lines.append({"id": "streak", "headline": f"{streak} STRAIGHT WINS FOR THE COWBOYS", "source": "ESPN schedule"})
    if osu["score"] >= sport_cfg(sport).get("big_score", 999):
        lines.append({"id": "big_score", "headline": f"COWBOYS HANG {osu['score']} ON {o_name}",
                      "source": "ESPN final score"})
    return lines


def cmd_facts(a):
    d = summary(a.sport, a.event)
    hc = d["header"]["competitions"][0]
    st = hc["status"]["type"]
    events = schedule(a.sport)
    sched = next((e for e in events if e["id"] == str(a.event)), None)
    sched_comp = sched["competitions"][0]["competitors"] if sched else []
    teams = [team_info(c, sched_comp) for c in hc["competitors"]]
    osu = next((t for t in teams if t["id"] == TEAM), None)
    if not osu:
        raise SystemExit(f"Event {a.event} is not an Oklahoma State game.")
    opp = next(t for t in teams if t is not osu)
    o_name = (opp["location"] or opp["display"]).upper()
    f = {"event": str(a.event), "sport": a.sport, "sport_label": sport_cfg(a.sport)["label"],
         "hashtag": sport_cfg(a.sport)["hashtag"], "kickoff": hc.get("date"),
         "final": bool(st.get("completed")), "status": st.get("description"),
         "final_at": last_wallclock(d), "conference_game": bool(hc.get("conferenceCompetition")),
         "venue": ((d.get("gameInfo") or {}).get("venue") or {}).get("fullName"),
         "osu": osu, "opp": opp}
    f["result_headline"] = (f"COWBOYS BEAT {o_name} {osu['score']}-{opp['score']}" if osu["winner"] else
                            f"{o_name} {opp['score']}, COWBOYS {osu['score']}")
    f["scoring_plays"] = scoring_plays(d, osu, opp)
    f["leaders"] = leaders(d, TEAM)
    f["storylines"] = storylines(f, a.sport, events)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(f, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sp = [p for p in f["scoring_plays"] if p["osu"]]
    print(f"{f['osu']['abbr']} {osu['score']}, {opp['abbr']} {opp['score']} ({f['status']}); "
          f"{len(sp)} OSU scoring plays; storylines: {[s['headline'] for s in f['storylines']] or 'none (plain result)'}")


def main():
    ap = argparse.ArgumentParser(description="ESPN game data for okst-highlights")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("due")
    sub.add_parser("switch")
    for name in ("gate", "wait", "facts"):
        p = sub.add_parser(name)
        p.add_argument("--event", required=True)
        p.add_argument("--sport", default="football")
        if name == "gate":
            p.add_argument("--replay", action="store_true")
            p.add_argument("--hold", action="store_true")
        if name == "facts":
            p.add_argument("--out", required=True)
        if name == "wait":
            p.add_argument("--minutes", type=int, help="override final_wait_minutes")
    a = ap.parse_args()
    {"due": cmd_due, "gate": cmd_gate, "wait": cmd_wait, "facts": cmd_facts, "switch": cmd_switch}[a.cmd](a)


if __name__ == "__main__":
    main()
