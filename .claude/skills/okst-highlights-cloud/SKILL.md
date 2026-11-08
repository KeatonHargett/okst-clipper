---
name: okst-highlights-cloud
description: Unattended postgame highlights for one Oklahoma State game, run by the okst-highlights GitHub Actions workflow after the game goes final. Picks up to 3 posts (a network clip repost or a big play recut, a Game in 60, and a milestone on wins), renders them from the official highlights videos, makes covers and captions, and posts them to Instagram @okstprobs and the OKSTProbs Facebook Page with no approval step.
argument-hint: <espn event id> <sport> posts_left=<n> [hold]
disable-model-invocation: true
---

# OKST postgame highlights (cloud)

Input: $ARGUMENTS
Tokens: ESPN event id, sport key (football, mens-basketball), `posts_left=<n>` (how many more posts this game may get; 0 means
make nothing), and `hold` (make, check, and stage everything on Instagram, publish nothing: the first game of each sport runs
this way as a test).

You run alone on a GitHub Actions runner. Nobody can answer a question and nobody approves anything: Keaton approved posting
up to 3 highlights per game in advance. When something is unclear, choose the safe option (post less, never invent), say why
in REPORT, and finish. Never wait for input.

ROOT: $GITHUB_WORKSPACE
PROJECT: ROOT/chopify (run every python command from here as `python ...`)
FACTS: ROOT/out/facts.json (ESPN: final score, records, ranks, every scoring play with the score after it, OSU leaders, and
  `storylines`: milestone headlines already checked against ESPN data). The only source of numbers and claims.
SOURCES: ROOT/out/sources.json. `youtube`: official highlights videos found after kickoff (`kind`: scoring, condensed,
  highlights, player, defense). The ones with `file` are downloaded (PROJECT/work/hl/<id>/source.mp4) with contact sheets in
  `sheets` (sheet_NN.jpg: 30 frames, one per second, each stamped with its time; sheet k starts at (k-1)*30 s) and cuts.txt
  (scene cut times). `network`: Instagram videos the network, league, and OSU accounts posted about this game (caption,
  permalink, thumbnail_file).
OUT: ROOT/out/<slug>/ per post. <slug> = hl-<event>-<a|b|c|net>. The workflow uploads all of ROOT/out.
REPORT: ROOT/out/report.md. SUMMARY: ROOT/out/summary.json = {"rendered": n, "posted": n, "hold": true|false}.
ISSUES: ROOT/out/issues/<n>.json, one per post (or per held Reel): {"title", "body", "images": ["../<slug>/<file>", ...]}.
LOG: ROOT/state/highlights-log.jsonl, one line per post that went out (the workflow commits it).

Already done: Doppler is logged in, ffmpeg, yt-dlp, cloudflared and Pillow are installed, Cloudflare WARP is on, FACTS and
SOURCES exist. Never print a secret, never run `doppler secrets get` or `doppler secrets download`.

## 1. Read
Read FACTS and SOURCES. Note OSU's scoring plays (`scoring_plays` with `"osu": true`, their index in the full list matters),
the result, `storylines`, `result_headline`, and the leaders. Look at every contact sheet of each downloaded video. Find where
each OSU score happens: the broadcast score bug inside the frame and ESPN's play text (player, yards) tell you which play is
which. Write the mapping down (play index, video, snap time, score time) before planning anything.

## 2. Pick the posts (at most posts_left; never two posts of the same play)
1. BEST PLAY. If SOURCES.network has a vertical video of an OSU play from this game posted by one of the listed accounts
   (look at thumbnail_file and the caption; it must be this game and an OSU highlight, not a promo, interview, or the
   opponent's play), repost the best one through okst-repost:
   `gh workflow run okst-repost.yml -f url="<permalink>" -f handle="<account>" -f highlight=true -f game="<sport>:<event>"`
   (add `-f hold=true` when hold). That run reposts it untouched with full credit, a game cover, and its own log line and
   issue. It counts as one post. Otherwise make FORMAT A from the biggest OSU play (longest touchdown, a defensive score, a
   go ahead or clinching score).
2. FORMAT C (Game in 60), when a downloaded video shows most OSU scores (football: at least 70% of them; basketball: at
   least 8 OSU baskets in game order). Prefer the `scoring` kind.
3. On a WIN: FORMAT B (milestone). On a LOSS: a second network clip of a different play if one exists; otherwise nothing.
Never post more than posts_left. With posts_left=0 make nothing and say so in REPORT.

## 3. Plan and render (python okst_highlights.py render <plan.json>)
Write each plan to ROOT/out/plans/<slug>.json. Layout is fixed by the renderer (stacked: zoomed action on top, the full
untouched 16:9 frame below it, our graphics under the frame); it never crops or covers the broadcast's logos or score bug.
Plan keys: format, facts (ROOT/out/facts.json), source (the source.mp4), out (ROOT/out/<slug>/<slug>.mp4), headline (A, B),
cover_at (source second of the best still for the cover), segments.
Each segment: {"start": s, "end": s, "zoom": {"x": 0..1, "y": 0..1, "w": 0.35..0.6}} in source seconds. x, y is the centre of
the action in the source frame; w is the zoom crop's share of the frame width (smaller = closer). Wide shots of a long run
need w about 0.5 following the ball carrier; close ups need about 0.6.
- FORMAT A (15 to 25 s): the live play from just before the snap to just after the score (or the moment), then one replay
  angle if the video has one. Headline: white caps with a stat from FACTS, under 50 characters, e.g.
  `CALEB HAWKINS TAKES IT 37 YARDS TO THE HOUSE`, `HAWKINS' FOURTH TD OF THE DAY`.
- FORMAT B (10 to 20 s, wins only): the final whistle, celebration, or handshakes near the end of a highlights video (or the
  clinching score). Headline: exactly one entry of FACTS.storylines[].headline (pick the strongest: ranked win, streak,
  conference record, big score, record), or FACTS.result_headline when there are none. The renderer refuses anything else.
- FORMAT C (45 to 65 s with the 2.5 s final card): one segment per OSU score in game order, each 4 to 7 s, ending a beat after
  the score. Add "play": <index into FACTS.scoring_plays> and "score_at": <seconds from the segment start to the moment the
  score happens> (the bar flips then). An OSU score the video does not show: {"card": true, "play": <index>, "dur": 1.6} (text
  from ESPN). The countdown, score bar, and final card come from FACTS. Keep the action total 42 to 62 s: trim each segment
  rather than drop a score; basketball uses the shown baskets only.
Before rendering A or B run `python okst_highlights.py verify-headline "<headline>" --facts <FACTS>` (add `--storyline`
for B). A refused headline: rewrite it from FACTS. Never invent a stat, a record, a streak, a ranking, or a quote; "career
high", "school record", "first time since" and similar are never allowed (ESPN cannot confirm them).
After rendering: `python okst_highlights.py check <mp4>` (must print CHECK OK) and `python okst_highlights.py debug <mp4>`,
then LOOK at <base>.debug.jpg. Red = zones our text must stay out of (footage may show there), cyan = the grid crop, green =
our graphics. Check: the zoom panel shows the ball or the player, the score bar matches the broadcast score bug in the frame,
the headline reads cleanly. Fix the plan and render again (delete that <slug>.mp4 and its .layout.json first; render never
overwrites). At most 3 tries per post, then drop it and say why in REPORT.

## 4. Cover
`python make_cover.py "<mp4>" --template game --facts "<FACTS>" --text "<COVER HEADLINE>"`. The cover headline is short caps
(under 45 characters), passes verify-headline, and may differ from the Reel's headline (C: e.g. `EVERY COWBOYS SCORE AGAINST
KANSAS STATE`; never split a score like "66 TO 28" across lines: rephrase instead). Look at <base>.cover.debug.jpg: the
action still is whole and everything sits between the cyan lines. A bad still: change cover_at in the plan, re-extract
<base>.frame.jpg with ffmpeg at that second, delete the cover, run again.

## 5. Caption (<base>.txt next to the mp4, UTF-8 without BOM)
    <opening line: what happened, under 12 words, Keaton's voice, no hype words like INSANE>

    <emoji> <fact from FACTS>
    <emoji> <fact from FACTS>
    <emoji> <fact from FACTS>

    🎥 <source channel>, <network> broadcast

    #OKState #GoPokes <#OKStateFootball or #OKStateMBB> #<Opponent location, no spaces> #Big12
Bullets are facts only: final score, records, leaders' lines, the play's yards. Scores and records are written "66 to 28",
"6 and 3": no hyphens or dashes anywhere (Instagram check), so `KState` style names are out too; write `Kansas State`. Credit
names the YouTube channel the footage came from and the broadcaster whose logo is in the frame (e.g. `🎥 Oklahoma State
Athletics, TNT Sports broadcast`). Non conference opponent: swap #Big12 for #CollegeFootball or #CollegeBasketball. No "tag a
friend", no irony, no questions to the audience. Then `python okst_highlights.py check-caption <base>.txt` must print CAPTION OK.

## 6. Publish
Order: best play, then C, then B. For each Reel:
- hold: `doppler run -p okst -c dev -- python ig_publish.py --reel "<mp4>" --stage-only --strict-rights` (Instagram processes
  it, nothing is published).
- live: first `python ../cloud/games.py switch`; if it prints OFF, stop publishing everything and say so. Then
  `doppler run -p okst -c dev -- python ig_publish.py --reel "<mp4>" --strict-rights` once. Never retry a failed publish.
- Exit code 3 or output with COPYRIGHT_BLOCK: write the whole output to ROOT/out/copyright_stop, publish nothing more, stop
  (the workflow switches highlights off and opens an issue).
- Instagram's daily limit error (code 9 or 2207042): stop publishing, say so in REPORT; nothing is queued.
- A Facebook failure after PUBLISHED still counts as posted; put the `--fb-only` retry command in REPORT.
- After a post goes out, append to LOG: {"date": "<YYYY-MM-DD America/Chicago>", "event": "<event>", "sport": "<sport>",
  "format": "A|B|C", "slug": "<slug>", "source": "<YouTube url>", "instagram": "<permalink>", "facebook": "<link or
  FAILED>", "media_id": "<from <base>.published.json>"}.
When all posts are out (live only): wait 120 s, then `doppler run -p okst -c dev -- python ig_publish.py --verify <media_id>`
for each. Exit 3 (REMOVED): write ROOT/out/copyright_stop with the output (it was pulled, most likely over rights).

## 7. Issues, summary, report
For every rendered Reel write ISSUES/<n>.json: title `Highlight posted: <A|B|C> <headline or Game in 60> (<OSU> vs <OPP>)`
(or `Highlight HELD (hold test): ...`), body with the Instagram and Facebook links (or "staged, not published"), the caption in
a code block, the plan's segment list, and the check output; images: the cover jpg and the debug jpg. Write SUMMARY and
REPORT (what was picked and why, what was skipped and why, each post's links, the network clip dispatched if any, problems).

## Rules
Nothing posts except step 6. Never log into Instagram, Meta, YouTube, or TikTok; never use cookies. Never crop out, blur, or
cover a network logo, score bug, or watermark (the renderer keeps the full frame; never pass it a pre cropped source). Never
use House of Highlights or any account not in cloud/highlights.json. Never edit code or config. Never invent a fact.
