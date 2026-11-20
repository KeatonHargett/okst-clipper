"""Monday report for @okstprobs and @frontporchsports: what posted, what reached people, which kinds of post and
which hours work. Free: each account's own token (okst/dev). Numbers are computed here; Claude only writes the
words (.claude/skills/okst-weekly), and `finish` drops any line with a number the data doesn't contain.

  collect  -> work/weekly/data.json
  finish   -> out/report.md (the issue), state/weekly/last.json + history.jsonl

This repo is public, so the issue is too: it holds only public-facing numbers, never a token.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work" / "weekly"
STATE = ROOT / "state" / "weekly"
CT = dt.timezone(dt.timedelta(hours=-6))
WINDOW_DAYS = 7
ACCOUNTS = {
    "okst": {"name": "OKSTProbs", "handle": "okstprobs", "graph": "https://graph.instagram.com/v23.0",
             "token_env": "IG_ACCESS_TOKEN", "ig_id": "me"},
    "fps": {"name": "Front Porch Sports", "handle": "frontporchsports", "graph": "https://graph.facebook.com/v23.0",
            "token_env": "FPS_PAGE_TOKEN", "ig_id": "17841473798615133"},
}
ACCT_METRICS = ("reach", "views", "profile_views", "accounts_engaged", "total_interactions")
MEDIA_METRICS = ("reach", "views", "likes", "comments", "saved", "shares")
HOURS = [(6, 11, "morning (6-11 am)"), (11, 15, "midday (11 am-3 pm)"), (15, 19, "afternoon (3-7 pm)"),
         (19, 23, "evening (7-11 pm)")]


class GraphError(Exception):
    pass


def gget(base, path, token, **params):
    params.update(access_token=token)
    req = urllib.request.Request(f"{base}/{path}", data=urllib.parse.urlencode(dict(params, method="get")).encode(),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace").replace(token, "<token>")
        try:
            msg = json.loads(raw)["error"]["message"]
        except Exception:
            msg = raw[:200]
        raise GraphError(msg) from None


def parse_ts(s):
    return dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def code(url):
    m = re.search(r"/(?:p|reel|reels|tv)/([^/?]+)", url or "")
    return m.group(1) if m else None


def _jsonl(path):
    p = ROOT / path
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def post_kinds():
    """shortcode -> what the pipelines posted it as. Anything else was posted by hand."""
    kinds = {}
    for r in _jsonl("state/posted-log.jsonl"):
        c = code(r.get("instagram"))
        if c:
            kinds[c] = {"interview": "interview clip", "podcast": "podcast clip"}.get(r.get("kind"), "clip")
    for r in _jsonl("state/highlights-log.jsonl"):
        c = code(r.get("instagram"))
        if c:
            kinds[c] = "game highlights"
    for r in _jsonl("state/reposted.jsonl"):
        c = code(r.get("instagram"))
        if c:
            kinds[c] = "repost (auto scout)" if r.get("auto") else "repost (Post to OKST)"
    for r in _jsonl("state/fps/reposted.jsonl"):
        c = code(r.get("instagram"))
        if c:
            kinds[c] = ("trial reel repost" if r.get("trial") else
                        "repost (auto scout)" if r.get("auto") else "repost (Post to Front Porch)")
    return kinds


def media_insights(base, mid, token):
    metrics = list(MEDIA_METRICS)
    for _ in range(3):   # some media refuse a metric: drop it and retry
        try:
            return {d["name"]: d["values"][0]["value"] for d in gget(base, f"{mid}/insights", token, metric=",".join(metrics))["data"]}
        except GraphError as e:
            bad = [m for m in metrics if m in str(e)]
            if not bad:
                return {}
            metrics = [m for m in metrics if m not in bad]
    return {}


def collect_account(key, acfg, since, kinds, log=print):
    base, token = acfg["graph"], os.environ.get(acfg["token_env"])
    out = {"name": acfg["name"], "handle": acfg["handle"], "posts": [], "errors": []}
    if not token:
        out["errors"].append(f"{acfg['token_env']} is not set")
        return out
    try:
        a = gget(base, acfg["ig_id"], token, fields="id,username,followers_count")
        if a.get("username", "").lower() != acfg["handle"]:   # routing guard
            raise GraphError(f"token reaches @{a.get('username')}, not @{acfg['handle']}")
        ig_id, out["followers"] = a["id"], a.get("followers_count")
    except GraphError as e:
        out["errors"].append(f"account: {e}")
        return out
    now = dt.datetime.now(dt.timezone.utc)
    try:
        r = gget(base, f"{ig_id}/insights", token, metric=",".join(ACCT_METRICS), period="day", metric_type="total_value",
                 since=int(since.timestamp()), until=int(now.timestamp()))
        out["account"] = {m["name"]: (m.get("total_value") or {}).get("value") for m in r.get("data", [])}
    except GraphError as e:
        out["errors"].append(f"account insights: {e}")
    after, pages = None, 0
    while pages < 6:
        pages += 1
        params = dict(fields="id,caption,media_product_type,timestamp,permalink,like_count,comments_count", limit=50)
        if after:
            params["after"] = after
        try:
            r = gget(base, f"{ig_id}/media", token, **params)
        except GraphError as e:
            out["errors"].append(f"media: {e}")
            break
        rows = r.get("data", [])
        for m in rows:
            if parse_ts(m["timestamp"]) < since:
                continue
            p = {"id": m["id"], "permalink": m.get("permalink"), "timestamp": m["timestamp"],
                 "type": m.get("media_product_type"), "kind": kinds.get(code(m.get("permalink")), "posted by hand"),
                 "caption": (m.get("caption") or "")[:240], "likes": m.get("like_count"), "comments": m.get("comments_count")}
            p.update({k: v for k, v in media_insights(base, m["id"], token).items() if k not in ("likes", "comments")})
            out["posts"].append(p)
        if not rows or parse_ts(rows[-1]["timestamp"]) < since:
            break
        after = ((r.get("paging") or {}).get("cursors") or {}).get("after")
        if not after:
            break
    log(f"{key}: {len(out['posts'])} posts, followers {out.get('followers')}, errors {len(out['errors'])}")
    return out


def group_avg(posts, keyfn, metric="reach", now=None):
    """Average reach per group, skipping posts under a day old (their numbers are still climbing)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    groups = {}
    for p in posts:
        v = p.get(metric)
        if isinstance(v, int) and (now - parse_ts(p["timestamp"])).total_seconds() >= 86400:
            groups.setdefault(keyfn(p), []).append(v)
    return {k: {"posts": len(v), "avg": round(sum(v) / len(v))} for k, v in groups.items() if k}


def hour_bucket(p):
    h = parse_ts(p["timestamp"]).astimezone(CT).hour
    return next((label for a, b, label in HOURS if a <= h < b), "late night (11 pm-6 am)")


def totals(a):
    posts = a.get("posts", [])
    t = {"posts": len(posts), "followers": a.get("followers")}
    for k in ("likes", "comments", "shares", "saved"):
        t[k] = sum(p.get(k) or 0 for p in posts)
    t.update({k: v for k, v in (a.get("account") or {}).items() if isinstance(v, int)})
    return t


def deltas(now, prev):
    return {k: v - prev[k] for k, v in now.items() if isinstance(v, int) and isinstance((prev or {}).get(k), int)}


def cmd_collect():
    WORK.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=WINDOW_DAYS)
    kinds = post_kinds()
    prev = json.loads((STATE / "last.json").read_text(encoding="utf-8")) if (STATE / "last.json").exists() else {}
    data = {"week_of": since.astimezone(CT).strftime("%Y-%m-%d"), "through": now.astimezone(CT).strftime("%Y-%m-%d"),
            "accounts": {}}
    for key, acfg in ACCOUNTS.items():
        a = collect_account(key, acfg, since, kinds)
        a["totals"] = totals(a)
        a["vs_last_week"] = deltas(a["totals"], (prev.get("accounts") or {}).get(key))
        a["by_kind"] = group_avg(a["posts"], lambda p: p["kind"], now=now)
        a["by_hour"] = group_avg(a["posts"], hour_bucket, now=now)
        ranked = sorted((p for p in a["posts"] if isinstance(p.get("reach"), int)), key=lambda p: p["reach"], reverse=True)
        a["best"], a["worst"] = ranked[:5], ranked[-3:] if len(ranked) > 6 else []
        data["accounts"][key] = a
    (WORK / "data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _numbers(text):
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}


def allowed_numbers(a):
    nums = {"1", "2", "3", "5", "7"}
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            nums.update({str(abs(x)), str(abs(round(x)))})
        elif isinstance(x, str):
            nums.update(_numbers(x))
    walk(a)
    t = a.get("totals", {})
    for k, d in a.get("vs_last_week", {}).items():
        base = t.get(k, 0) - d
        if base:
            nums.add(str(abs(round(100 * d / base))))
    groups = list(a.get("by_kind", {}).values()) + list(a.get("by_hour", {}).values())
    for g1 in groups:          # "2x" / ratio comparisons between groups
        for g2 in groups:
            if g2["avg"]:
                nums.add(str(round(g1["avg"] / g2["avg"])))
    return nums


def bad_numbers(text, a):
    return sorted(_numbers(text) - allowed_numbers(a))


def fmt(n):
    return f"{n:,}" if isinstance(n, int) else "–"


def signed(n):
    return "" if not isinstance(n, int) else (f" (+{n:,})" if n > 0 else f" ({n:,})" if n < 0 else " (=)")


def cmd_finish():
    data = json.loads((WORK / "data.json").read_text(encoding="utf-8"))
    npath = WORK / "notes.json"
    try:
        notes = json.loads(npath.read_text(encoding="utf-8")) if npath.exists() else {}
    except ValueError:
        notes = {}
    lines = [f"Week of **{data['week_of']}** to **{data['through']}**. Numbers straight from Instagram; words by Claude.", ""]
    flagged = []
    for key, a in data["accounts"].items():
        t, d = a["totals"], a.get("vs_last_week", {})
        n = (notes.get("accounts") or {}).get(key, {})
        lines += [f"## {a['name']} (@{a['handle']})", ""]
        lines += [f"> ⚠️ {e}" for e in a.get("errors", [])]
        cols = [c for c in ("posts", "followers", "reach", "views", "total_interactions", "shares", "saved", "profile_views") if c in t]
        lines += ["", "| " + " | ".join(c.replace("_", " ") for c in cols) + " |", "|" + "---|" * len(cols),
                  "| " + " | ".join(fmt(t[c]) + signed(d.get(c)) for c in cols) + " |", ""]
        for title, groups in (("Average reach by kind of post", a["by_kind"]), ("Average reach by posting time (Central)", a["by_hour"])):
            if groups:
                lines += [f"{title}: " + "; ".join(f"{k} {fmt(v['avg'])} ({v['posts']})"
                                                   for k, v in sorted(groups.items(), key=lambda x: -x[1]["avg"])), ""]
        if a["best"]:
            lines += ["Top posts: " + ", ".join(f"[{fmt(p['reach'])} reach]({p['permalink']})" for p in a["best"]), ""]
        for field, title in (("worked", "What worked"), ("flopped", "What didn't"), ("next", "Next week")):
            keep = []
            for s in n.get(field) or []:
                bad = bad_numbers(s, a)
                if bad:
                    flagged.append(f"{a['name']} / {title}: dropped a line (numbers {', '.join(bad)} not in the data)")
                else:
                    keep.append(s)
            if keep:
                lines += [f"**{title}**"] + [f"- {s}" for s in keep] + [""]
    if flagged:
        lines += ["<details><summary>Checks</summary>", ""] + [f"- {f}" for f in flagged] + ["", "</details>"]
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    STATE.mkdir(parents=True, exist_ok=True)
    snap = {"week_of": data["week_of"], "accounts": {k: a["totals"] for k, a in data["accounts"].items()}}
    (STATE / "last.json").write_text(json.dumps(snap, indent=1), encoding="utf-8")
    with open(STATE / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(snap) + "\n")
    print(f"report written; {len(flagged)} lines flagged")


if __name__ == "__main__":
    {"collect": cmd_collect, "finish": cmd_finish}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit("usage: weekly.py collect | finish"))()
