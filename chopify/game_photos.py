"""Official okstate.com football game photos, for add_intro.py's opening photo.

Usage:
    .venv\\Scripts\\python game_photos.py [--games 12]
    .venv\\Scripts\\python game_photos.py --get <gallery>_<index>

The first form caches the newest game galleries (okstate.com/services/galleries.ashx, the same feed the site's
Galleries page uses) into work\\galleries\\ and writes labeled contact sheets to look through for the player's jersey
number. The galleries don't tag players, so the number (from the okstate.com roster) is how a player is found.
--get downloads that photo at full size into assets\\photos\\ and prints its path.
"""

import argparse
import json
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT = Path(__file__).resolve().parent
CACHE = PROJECT / "work" / "galleries"
PHOTOS = PROJECT / "assets" / "photos"
SITE = "https://okstate.com"
FOOTBALL = 3
UA = {"User-Agent": "Mozilla/5.0"}
SKIP = ("press conference", "selfie", "visits", "reunion", "unveil", "hunger")   # not game action


def fetch(url, path=None):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if path:
        path.write_bytes(data)
    return data


def gallery(gid):
    f = CACHE / f"g{gid}.json"
    if not f.exists():
        fetch(f"{SITE}/services/galleries.ashx/gallery?id={gid}", f)
    return json.loads(f.read_text(encoding="utf-8"))["data"]


def sheets(games):
    CACHE.mkdir(parents=True, exist_ok=True)
    listing = json.loads(fetch(f"{SITE}/services/galleries.ashx/galleries?keyword=&sport_id={FOOTBALL}"
                               f"&season_id=0&page_size=40&page=1&player_ids=&tag_ids="))["data"]
    picked = [g for g in listing if not any(w in g["title"].lower() for w in SKIP)][:games]
    thumbs = []
    for g in picked:
        print(f"{g['id']}  {g['date'][:10]}  {g['title']}  ({g['image_count']})")
        for i, im in enumerate(gallery(g["id"])["images"]):
            t = CACHE / f"{g['id']}_{i:02d}.jpg"
            if not t.exists():
                fetch(SITE + im["original"].split("?")[0] + "?width=700", t)
            thumbs.append(t)
    fonts = (r"C:\Windows\Fonts\arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    font = next((ImageFont.truetype(f, 22) for f in fonts if Path(f).exists()), None) or ImageFont.load_default()
    out = []
    for k in range(0, len(thumbs), 30):
        sheet = Image.new("RGB", (6 * 340, 5 * 230), "white")
        d = ImageDraw.Draw(sheet)
        for j, p in enumerate(thumbs[k:k + 30]):
            im = Image.open(p).convert("RGB")
            im.thumbnail((336, 226))
            x, y = (j % 6) * 340, (j // 6) * 230
            sheet.paste(im, (x, y))
            d.rectangle((x, y, x + 120, y + 26), fill="black")
            d.text((x + 3, y + 1), p.stem, font=font, fill="yellow")
        path = CACHE / f"sheet_{k // 30}.jpg"
        sheet.save(path, quality=85)
        out.append(path)
    print("SHEETS", *out, sep="\n")


def get(key, name):
    gid, idx = key.split("_")
    im = gallery(int(gid))["images"][int(idx)]
    PHOTOS.mkdir(parents=True, exist_ok=True)
    dst = PHOTOS / f"{name or key}.jpg"
    if dst.exists():
        print("EXISTS", dst)
        return
    fetch(SITE + im["original"].split("?")[0], dst)
    print("PHOTO", dst, f"({im['width']}x{im['height']})", (im.get("caption") or "")[:120])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--games", type=int, default=12, help="newest game galleries to scan")
    ap.add_argument("--get", help="<gallery>_<index> from a contact sheet label")
    ap.add_argument("--name", help="file name for --get, e.g. luke-webb-98-vs-ucf-2026-10-10")
    a = ap.parse_args()
    get(a.get, a.name) if a.get else sheets(a.games)


if __name__ == "__main__":
    main()
