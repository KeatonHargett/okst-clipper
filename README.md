# okst-clipper

Unattended cloud version of `/okst-clip`. No PC needed.

- **okst-watch** (every 2 hours, 9 am to midnight Central): lists the Oklahoma State Athletics YouTube channel and queues
  **okst-clip** for each new interview (any sport). `state/seen.json` remembers what it has seen.
- **okst-clip** (one video per run): Cloudflare WARP for YouTube, full video download, then Claude runs
  `.claude/skills/okst-clip-cloud` (pick clips, render with chopify, captions, covers, ship to Instagram + Facebook).
  Report = a GitHub issue; clip files = the run's `okst-clips` artifact (30 days). `state/posted-log.jsonl` feeds the 6 a day cap.
- **okst-repost** (started by the iPhone "OKST Repost" share sheet Shortcut): cloud `/okst-repost` for TikTok, Instagram, X, and YouTube links.
  Claude runs `.claude/skills/okst-repost-cloud`; WARP only for TikTok and YouTube; `state/reposted.jsonl` blocks reposting the same link twice.
- **okst-highlights** (started by okst-kick, once per game): postgame highlight Reels, see below.
- **yt-download-test**: diagnostics for YouTube download methods.

## Postgame highlights

okst-kick runs `cloud/games.py due` every 30 minutes (ESPN public API, team 197, sports in `cloud/highlights.json`) and
starts **okst-highlights** for each Oklahoma State game about to start, live, or just ended, once per game (it skips any
event id already in an okst-highlights run title). That run:

1. `games.py gate`: stops if the kill switch is on, the game is done, or it already has `max_posts_per_game` (3) posts.
   The first game of each sport runs as a **hold** test (staged on Instagram, nothing published); once a hold run renders,
   `state/highlights-first.json` lets the next game post for real.
2. `games.py wait` until ESPN says final (long games hand off to a fresh run), then `games.py facts` writes `out/facts.json`:
   score, records, ranks, every scoring play, leaders, and checked milestone headlines.
3. `cloud/highlight_sources.py`: Instagram videos about the game from the accounts in `highlights.json` (Business Discovery),
   and the official highlights videos on the OSU Athletics and Big 12 YouTube channels (waits up to 4 hours for them;
   "highlights" titles are allowed here only, okst-watch still skips them). Downloads the best two with contact sheets.
4. Claude runs `.claude/skills/okst-highlights-cloud`: up to 3 posts. Best play = a network clip reposted untouched through
   okst-repost (`highlight=true`), else **A** (15 to 25 s, headline box with a stat); **C** Game in 60 (every OSU score in
   order, countdown, score bar from ESPN, orange final card); **B** milestone on wins (OSU logo + a verified storyline).
   `chopify/okst_highlights.py` renders a stacked layout (zoomed action on top, the full untouched 16:9 broadcast frame
   below, our graphics under it), so network logos and score bugs are never cropped or covered, and every graphic stays
   out of the top 250, bottom 450, right 140, and left 60 px. Headlines are checked against ESPN data (`verify-headline`);
   covers come from `make_cover.py --template game`.
5. Posts with `ig_publish.py --strict-rights`, verifies each post is still up, logs `state/highlights-log.jsonl`, and opens
   one GitHub issue per post with the cover (images live on the `covers` branch).

**Kill switch**: `state/highlights_off`. Actions > okst-highlights-switch > Run workflow > off (or on). iPhone: the GitHub app,
or a Shortcut "Get contents of URL" POST to
`https://api.github.com/repos/KeatonHargett/okst-clipper/actions/workflows/okst-highlights-switch.yml/dispatches` with
headers `Authorization: Bearer <the Shortcut PAT>`, `Accept: application/vnd.github+json` and JSON body
`{"ref":"main","inputs":{"state":"off"}}`. A copyright or rights error (Instagram or Facebook) creates the file itself and
opens an issue; nothing is retried. Manual run or replay: Actions > okst-highlights > Run workflow (event, sport, hold, replay).
Tests: `python tests/test_highlights.py`.

Secrets: only `DOPPLER_TOKEN` (okst/dev) lives in GitHub. Claude and Meta tokens are read from Doppler and masked in logs.

`chopify/` is a copy of C:\Projects\chopify (MIT, mehbul/chopify) with two cloud patches: `OKST_FULL_SOURCE=1` (full download +
local cuts, because ffmpeg ignores the WARP SOCKS proxy) and a Linux font fallback in game_photos.py.

Manual run: Actions > okst-clip > Run workflow (url, hold).
