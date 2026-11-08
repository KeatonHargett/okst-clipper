"""Plain checks for okst-highlights (no network, no ffmpeg): python tests/test_highlights.py"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "chopify"))
import okst_highlights as hl   # noqa: E402

FACTS = {
    "event": "1", "sport": "football", "final": True, "conference_game": True,
    "osu": {"id": "197", "location": "Oklahoma State", "abbr": "OKST", "score": 66, "record": "6-3",
            "conf_record": "4-2", "rank": None, "winner": True, "color": "#FE5C00", "logo": ""},
    "opp": {"id": "2306", "location": "Kansas State", "abbr": "KSU", "score": 28, "record": "4-5",
            "conf_record": "2-4", "rank": None, "winner": False, "color": "#330a57", "logo": ""},
    "result_headline": "COWBOYS BEAT KANSAS STATE 66-28",
    "scoring_plays": [
        {"osu": True, "text": "Caleb Hawkins 21 Yd Run (Sam Keltner Kick)", "osu_score": 7, "opp_score": 0},
        {"osu": True, "text": "Caleb Hawkins 37 Yd Run (Sam Keltner Kick)", "osu_score": 14, "opp_score": 0},
        {"osu": False, "text": "Someone 18 Yd Interception Return", "osu_score": 14, "opp_score": 7},
        {"osu": True, "text": "Justin Bowick 32 Yd pass from Drew Mestemaker", "osu_score": 21, "opp_score": 7},
    ],
    "leaders": [{"stat": "Rushing", "player": "Caleb Hawkins", "value": "18 CAR, 154 YDS, 3 TD"}],
    "storylines": [{"id": "record", "headline": "COWBOYS MOVE TO 6-3"},
                   {"id": "big_score", "headline": "COWBOYS HANG 66 ON KANSAS STATE"}],
}


def test_headlines():
    ok = ["CALEB HAWKINS TAKES IT 37 YARDS TO THE HOUSE", "HAWKINS' SECOND TD OF THE DAY", "HAWKINS RUNS FOR 154"]
    for h in ok:
        assert hl.verify_headline(h, FACTS) == [], h
    bad = {"HAWKINS 40 YARD RUN": "number 40", "BOWICK'S SECOND TD": "claims 2", "CAREER DAY FOR HAWKINS": "CAREER",
           "HAWKINS' THIRD TD": "claims 3", "FIRST TIME SINCE 1998": "cannot be verified"}
    for h, why in bad.items():
        probs = hl.verify_headline(h, FACTS)
        assert probs and any(why in p for p in probs), (h, probs)
    assert hl.verify_headline("COWBOYS HANG 66 ON KANSAS STATE", FACTS, storyline=True) == []
    assert hl.verify_headline("COWBOYS RULE", FACTS, storyline=True)


def test_caption():
    good = ("Cowboys run away from Kansas State in Manhattan\n\n"
            "🏈 Final: Oklahoma State 66, Kansas State 28\n"
            "🔥 Caleb Hawkins: 154 rushing yards, 3 touchdowns\n"
            "📈 Cowboys move to 6 and 3\n\n"
            "🎥 Oklahoma State Athletics, TNT Sports broadcast\n\n"
            "#OKState #GoPokes #OKStateFootball #KansasState #Big12")
    assert hl.check_caption(good) == [], hl.check_caption(good)
    assert any("dash" in p for p in hl.check_caption(good.replace("6 and 3", "6-3")))
    assert any("hashtags" in p for p in hl.check_caption(good.replace(" #Big12", "")))
    assert any("bullets" in p for p in hl.check_caption(good.replace("🔥 ", "")))
    assert any("tag a friend" in p for p in hl.check_caption(good.replace("in Manhattan", "tag a friend")))
    assert any("2,200" in p for p in hl.check_caption(good.replace("Manhattan", "x" * 2300)))


def test_safe_zones():
    with tempfile.TemporaryDirectory() as td:
        lay = hl.Layout(td)
        hl.headline_box(lay, "CALEB HAWKINS TAKES IT 37 YARDS TO THE HOUSE", 0, 1)
        hl.storyline_box(lay, "COWBOYS MOVE TO 4-2 IN BIG 12 PLAY", 0, 1)
        hl.countdown_ring(lay, 0, 1)
        assert lay.problems() == [], lay.problems()
        x0, y0, x1, y1 = hl.SAFE
        assert (x0, y0, hl.W - x1, hl.H - y1) == (60, 250, 140, 450)
        for it in lay.items:
            b = it["box"]
            assert x0 <= b[0] and y0 <= b[1] and b[2] <= x1 and b[3] <= y1, it
        lay.items.append({"name": "bad", "png": None, "box": [10, 300, 500, 400], "start": 0, "end": 1})
        assert lay.problems()
    # the full broadcast frame sits whole inside the canvas: never cropped
    assert hl.FRAME_Y + hl.FRAME_H <= hl.BAND[0] and hl.FRAME_H == round(hl.W * 9 / 16)


def test_storylines():
    sys.path.insert(0, str(ROOT / "cloud"))
    import games
    f = json.loads(json.dumps(FACTS))
    lines = [s["headline"] for s in games.storylines(f, "football", [])]
    assert lines == ["COWBOYS MOVE TO 6-3", "COWBOYS MOVE TO 4-2 IN BIG 12 PLAY", "COWBOYS HANG 66 ON KANSAS STATE"], lines
    f["opp"]["conf_record"] = "0-0"                     # ESPN flags some non conference games as conference games
    assert all("BIG 12" not in s["headline"] for s in games.storylines(f, "football", []))
    f["osu"]["winner"] = False
    assert games.storylines(f, "football", []) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
