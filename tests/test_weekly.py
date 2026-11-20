"""Checks for the OKST + FPS Monday report (cloud/weekly.py). Run: python tests/test_weekly.py"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cloud"))
import weekly  # noqa: E402

now = dt.datetime(2026, 11, 20, 18, tzinfo=dt.timezone.utc)
posts = [{"kind": "interview clip", "timestamp": "2026-11-18T18:00:00+0000", "reach": 3000},
         {"kind": "interview clip", "timestamp": "2026-11-17T18:00:00+0000", "reach": 1000},
         {"kind": "repost (auto scout)", "timestamp": "2026-11-17T03:00:00+0000", "reach": 500},
         {"kind": "repost (auto scout)", "timestamp": "2026-11-20T12:00:00+0000", "reach": 9}]   # under a day old
g = weekly.group_avg(posts, lambda p: p["kind"], now=now)
assert g == {"interview clip": {"posts": 2, "avg": 2000}, "repost (auto scout)": {"posts": 1, "avg": 500}}, g
h = weekly.group_avg(posts, weekly.hour_bucket, now=now)
assert set(h) == {"midday (11 am-3 pm)", "evening (7-11 pm)"}, h   # 18:00 UTC = noon CST, 03:00 UTC = 9 pm CST
a = {"totals": {"reach": 1200, "posts": 4}, "vs_last_week": {"reach": 200}, "by_kind": g, "by_hour": {}}
assert weekly.bad_numbers("Interview clips averaged 2,000 reach, 4x the reposts.", a) == []
assert weekly.bad_numbers("Reach is up 20%.", a) == [], "200 on a base of 1000"
assert weekly.bad_numbers("Reposts hit 7,777.", a) == ["7777"]
assert weekly.deltas({"reach": 5, "x": "y"}, {"reach": 2}) == {"reach": 3}
assert weekly.code("https://www.instagram.com/reel/Df92fdygTRw/") == "Df92fdygTRw"
print("weekly: all checks passed")
