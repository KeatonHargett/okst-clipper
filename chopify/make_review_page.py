"""Build review.html for an OKST review folder and open it in the default browser.

Usage:
    .venv\\Scripts\\python make_review_page.py "<review folder>" [--flags flags.json] [--no-open]

flags.json maps an mp4 file name (or its base name) to a list of flag strings:
    {"clip-one-logo.mp4": ["Cuts off mid thought"], "clip-two-logo": []}

Clips are numbered by file name, case insensitive. /okst-ship uses the same order.
The only file this script writes is review.html inside the review folder.
"""

import argparse
import html
import json
import os
import re
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote

ACCENT = "#FE5C00"
DASHES = ("-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212")
HASHTAG_RE = re.compile(r"#\w+")


def list_clips(folder):
    """mp4 files in the folder, in /okst-ship order."""
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"),
                  key=lambda p: p.name.lower())


def load_flags(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        sys.exit(f"Flags file must hold a JSON object of {{file name: [flags]}}: {path}")
    return {str(k): ([v] if isinstance(v, str) else list(v or [])) for k, v in data.items()}


def auto_checks(caption, hashtags):
    """The same caption checks /okst-ship runs, so problems show up during review."""
    if caption is None:
        return ["No final .txt caption yet. /okst-ship will refuse this clip."]
    out = []
    if len(hashtags) != 5:
        out.append(f"Has {len(hashtags)} hashtags, needs exactly 5.")
    if hashtags and hashtags[0].lower() != "#okstate":
        out.append("First hashtag is not #OKState.")
    if any(d in caption for d in DASHES):
        out.append("Caption contains a hyphen or dash.")
    if len(caption) > 2200:
        out.append(f"Caption is {len(caption):,} characters, over the 2,200 limit.")
    return out


def carousel_card(folder, manifest, flags):
    """A carousel review folder is one item (#1): every slide plus one caption named <slug>.txt."""
    slug = manifest["slug"]
    txt = folder / f"{slug}.txt"  # never the .draft.txt
    caption = txt.read_text(encoding="utf-8-sig").strip() if txt.is_file() else None
    hashtags = HASHTAG_RE.findall(caption or "")
    slides = [{"n": s["n"], "file": s["file"], "type": s["type"], "src": quote(s["file"]),
               "size": f'{s["width"]}x{s["height"]}'} for s in manifest["slides"]]
    missing = [s["file"] for s in manifest["slides"] if not (folder / s["file"]).is_file()]
    checks = auto_checks(caption, hashtags)
    if missing:
        checks.append("Missing slide files: " + ", ".join(missing))
    return {
        "n": 1,
        "name": f"{slug} (carousel, {len(slides)} slide{'' if len(slides) == 1 else 's'})",
        "slides": slides,
        "caption": caption,
        "chars": len(caption) if caption is not None else 0,
        "hashtags": len(hashtags),
        "flags": list(manifest.get("flags", [])) + flags.get(slug, []) + flags.get("carousel.json", []),
        "checks": checks,
    }


def build_cards(folder, flags):
    manifest_path = folder / "carousel.json"
    if manifest_path.is_file():
        return [carousel_card(folder, json.loads(manifest_path.read_text(encoding="utf-8")), flags)]
    cards = []
    for n, mp4 in enumerate(list_clips(folder), start=1):
        txt = mp4.with_suffix(".txt")  # never the .draft.txt
        caption = txt.read_text(encoding="utf-8-sig").strip() if txt.is_file() else None
        hashtags = HASHTAG_RE.findall(caption or "")
        cards.append({
            "n": n,
            "name": mp4.name,
            "src": quote(mp4.name) + "#t=0.1",  # shows a first frame instead of a black box
            "caption": caption,
            "chars": len(caption) if caption is not None else 0,
            "hashtags": len(hashtags),
            "flags": flags.get(mp4.name, []) + flags.get(mp4.stem, []),
            "checks": auto_checks(caption, hashtags),
        })
    return cards


def render(slug, cards):
    e = html.escape
    nums = ",".join(str(c["n"]) for c in cards)
    ship_cmd = f"/okst-ship {slug} {nums}"

    card_html = []
    for c in cards:
        chips = "".join(f'<li class="flag">{e(f)}</li>' for f in c["flags"])
        chips += "".join(f'<li class="flag check">{e(f)}</li>' for f in c["checks"])
        flag_block = f'<ul class="flags">{chips}</ul>' if chips else '<p class="ok">No flags</p>'
        if c["caption"] is None:
            caption_block = '<p class="missing">Missing final caption (.txt)</p>'
            copy_btn = ""
        else:
            caption_block = f'<pre class="caption" id="cap{c["n"]}">{e(c["caption"])}</pre>'
            copy_btn = f'<button class="copy" data-target="cap{c["n"]}">Copy caption</button>'
        if c.get("slides"):
            items = []
            for s in c["slides"]:
                media = (f'<video controls preload="metadata" src="{e(s["src"])}#t=0.1"></video>' if s["type"] == "video"
                         else f'<a href="{e(s["src"])}" target="_blank"><img src="{e(s["src"])}" alt="Slide {s["n"]}"></a>')
                items.append(f'<figure>{media}<figcaption>{s["n"]} &middot; {e(s["size"])}</figcaption></figure>')
            media_block = f'<div class="slides">{"".join(items)}</div>'
        else:
            media_block = f'<video controls preload="metadata" src="{e(c["src"])}"></video>'
        card_html.append(f"""
<article class="card{' carousel' if c.get('slides') else ''}">
  {media_block}
  <div class="info">
    <div class="head">
      <span class="num">{c["n"]}</span>
      <span class="name">{e(c["name"])}</span>
      <label class="pick"><input type="checkbox" class="ship" value="{c["n"]}" checked> Ship</label>
    </div>
    <p class="stats"><b>{c["chars"]:,}</b> characters &middot; <b>{c["hashtags"]}</b> hashtags</p>
    {flag_block}
    {caption_block}
    {copy_btn}
  </div>
</article>""")

    body = "\n".join(card_html) or '<p class="missing">No mp4 files in this folder.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review {e(slug)}</title>
<style>
  :root {{ --bg:#121214; --card:#1d1e22; --line:#2e3036; --text:#ececec; --muted:#a3a6ad; --accent:{ACCENT}; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:16px/1.5 system-ui, "Segoe UI", sans-serif; }}
  header {{ position:sticky; top:0; z-index:1; background:#0c0c0e; border-bottom:3px solid var(--accent); padding:14px 24px; }}
  header h1 {{ margin:0 0 8px; font-size:20px; }}
  header h1 span {{ color:var(--accent); }}
  .cmdrow {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  code {{ background:#000; border:1px solid var(--line); border-radius:6px; padding:6px 10px; font-size:16px; color:#fff; }}
  main {{ max-width:1100px; margin:0 auto; padding:24px; display:grid; gap:24px; }}
  .card {{ display:grid; grid-template-columns:minmax(220px, 340px) 1fr; gap:24px; background:var(--card);
           border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:10px; padding:18px; }}
  video {{ width:100%; max-height:78vh; background:#000; border-radius:8px; }}
  .card.carousel {{ grid-template-columns:1fr; }}
  .slides {{ display:flex; gap:12px; overflow-x:auto; padding-bottom:8px; scroll-snap-type:x mandatory; }}
  .slides figure {{ margin:0; flex:0 0 auto; width:min(300px, 70vw); scroll-snap-align:start; }}
  .slides img, .slides video {{ width:100%; display:block; border-radius:8px; background:#000; }}
  .slides figcaption {{ color:var(--muted); font-size:13px; margin-top:4px; text-align:center; }}
  .head {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}
  .num {{ background:var(--accent); color:#000; font-weight:800; border-radius:50%; width:34px; height:34px;
          display:inline-flex; align-items:center; justify-content:center; font-size:18px; flex:none; }}
  .name {{ font-weight:600; word-break:break-all; flex:1; }}
  .pick {{ color:var(--muted); white-space:nowrap; cursor:pointer; }}
  .pick input {{ accent-color:var(--accent); width:18px; height:18px; vertical-align:-3px; }}
  .stats {{ color:var(--muted); margin:10px 0; }}
  .stats b {{ color:var(--text); }}
  .flags {{ list-style:none; padding:0; margin:0 0 12px; display:flex; flex-wrap:wrap; gap:8px; }}
  .flag {{ border:1px solid var(--accent); color:#ffb68c; border-radius:999px; padding:3px 12px; font-size:14px; }}
  .flag.check {{ border-color:#e5484d; color:#ff9a9d; }}
  .ok {{ color:#6fcf8a; margin:0 0 12px; }}
  .missing {{ color:#ff9a9d; font-weight:600; }}
  .caption {{ white-space:pre-wrap; word-wrap:break-word; font:17px/1.55 system-ui, "Segoe UI", sans-serif;
              background:#141518; border:1px solid var(--line); border-radius:8px; padding:14px; margin:0 0 12px; }}
  button {{ background:var(--accent); color:#000; border:0; border-radius:6px; padding:8px 16px; font-weight:700;
            font-size:15px; cursor:pointer; }}
  button:hover {{ filter:brightness(1.1); }}
  button.done {{ background:#6fcf8a; }}
  @media (max-width:720px) {{ .card {{ grid-template-columns:1fr; }} main, header {{ padding-left:16px; padding-right:16px; }} }}
</style>
</head>
<body>
<header>
  <h1>OKST Review &middot; <span>{e(slug)}</span> &middot; {len(cards)} clip{"" if len(cards) == 1 else "s"}</h1>
  <div class="cmdrow">
    <code id="shipcmd">{e(ship_cmd)}</code>
    <button class="copy" data-target="shipcmd">Copy ship command</button>
  </div>
</header>
<main>
{body}
</main>
<script>
  const slug = {json.dumps(slug)};
  function copyText(text, btn) {{
    const done = () => {{ const t = btn.textContent; btn.textContent = "Copied"; btn.classList.add("done");
      setTimeout(() => {{ btn.textContent = t; btn.classList.remove("done"); }}, 1200); }};
    const fallback = () => {{ const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
      ta.select(); document.execCommand("copy"); ta.remove(); done(); }};
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }}
  document.querySelectorAll("button.copy").forEach(b =>
    b.addEventListener("click", () => copyText(document.getElementById(b.dataset.target).textContent, b)));
  function updateCmd() {{
    const nums = [...document.querySelectorAll("input.ship:checked")].map(i => i.value);
    document.getElementById("shipcmd").textContent = nums.length ? `/okst-ship ${{slug}} ${{nums.join(",")}}` : "No clips picked";
  }}
  document.querySelectorAll("input.ship").forEach(i => i.addEventListener("change", updateCmd));
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="Build review.html for an OKST review folder.")
    ap.add_argument("folder", help="Review folder, e.g. G:\\My Drive\\OKST-Review\\<slug>")
    ap.add_argument("--flags", help="JSON file of {file name: [flags]}")
    ap.add_argument("--no-open", action="store_true", help="Build the page without opening it")
    args = ap.parse_args()

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        sys.exit(f"Review folder not found: {folder}")
    slug = folder.name
    cards = build_cards(folder, load_flags(args.flags))

    page = folder / "review.html"
    page.write_text(render(slug, cards), encoding="utf-8")

    print(f"Wrote {page}")
    print()
    print(f"- [Review page]({page.as_uri()})")
    print(f"- [Review folder]({folder.as_uri()}/)")
    for c in cards:
        if c.get("slides"):
            for s in c["slides"]:
                print(f"- [Slide {s['n']}. {s['file']}]({(folder / s['file']).as_uri()})")
        else:
            print(f"- [{c['n']}. {c['name']}]({(folder / c['name']).as_uri()})")
    print()
    print(f"/okst-ship {slug} {','.join(str(c['n']) for c in cards)}")

    if not args.no_open:
        if hasattr(os, "startfile"):
            os.startfile(page)
        else:
            webbrowser.open(page.as_uri())


if __name__ == "__main__":
    main()
