---
name: okst-clip-cloud
description: Unattended cloud version of /okst-clip, run by the okst-clip GitHub Actions workflow on a new Oklahoma State Athletics interview. Clips it, ships the clean clips to Instagram and the OKSTProbs Facebook Page, and writes a report for the GitHub issue.
argument-hint: <youtube-url> [hold]
disable-model-invocation: true
---

# OKST Clip (cloud)

Input: $ARGUMENTS
First token is the URL. `hold` anywhere after it means: make and stage everything, ship nothing.

You run alone on a GitHub Actions runner. Nobody can answer a question. Wherever the desktop /okst-clip would stop and
ask, write the question into the report, ship nothing, and finish. Never wait.

PROJECT: $GITHUB_WORKSPACE/chopify (run every python command from here as `python ...`)
OUT: $GITHUB_WORKSPACE/out/<slug>/ (the review folder; POSTED = OUT/posted, HOLD = OUT/hold)
REPORT: $GITHUB_WORKSPACE/out/report.md (the workflow turns it into a GitHub issue; write it even when nothing ships)
POST LOG: $GITHUB_WORKSPACE/state/posted-log.jsonl (one JSON line per shipped Reel; the workflow commits it)
<slug>: the video title slugified (lowercase, spaces to hyphens, drop punctuation), e.g. steve-lutz-postgame-news-conference-oklahoma-state-vs-incarnate-word-11-2-2026

Already done by the workflow before you start: YouTube goes through Cloudflare WARP (yt-dlp's config has --proxy), the full
video is at PROJECT/work/<video id>/source.mp4, OKST_FULL_SOURCE=1 makes okst_clip.py cut ranges from it, and the Doppler CLI
is logged in (DOPPLER_TOKEN). Never print a secret, never run `doppler secrets get` or `doppler secrets download`.

## Steps
0. Guards (any failure: write the reason in REPORT and stop):
   a. Title and description: `python -m yt_dlp --skip-download --print "%(title)s|%(channel)s|%(upload_date)s" --print description "<URL>"`.
      The channel must be Oklahoma State Athletics.
   b. Daily cap: count POST LOG lines whose "date" is today in America/Chicago. At 6 or more, ship nothing today (hold everything).
   c. Duplicate check: `doppler run -p okst -c dev -- python ../cloud/ig_recent.py` prints the latest Instagram captions.
      If a recent caption is clearly from this same press conference (same person, same day, same opponent), Keaton already
      clipped it by hand: stop with "already posted".
1. Confirm okst_clip.py, logo.png, fonts/impact.ttf exist.
2. Pick and render:
   a. `python okst_clip.py "<URL>" --transcript-only`. Read the run folder's words.txt and sentences.txt.
      If it prints a chapter list, use the whole video unless one chapter is clearly the interview; never guess between interviews.
   b. Spelling check exactly as /okst-clip step 2b, with one change: the roster for this sport comes from okstate.com, not only
      OSU_ROSTER.txt (football). Fetch https://okstate.com/sports/<sport>/roster (womens-basketball, mens-basketball, wrestling,
      baseball, softball, ...) and read the `aria-label="<Name> jersey number <N> full bio"` entries. A box score on okstate.com
      may be used to tell who a lone first name means, but a lone first name still never goes into CORRECTIONS.
      Prefer clips whose burned in captions contain no misspelled name.
   c. Pick 3 to 6 clips with the same rules as /okst-clip step 2c (15 to 60 s, sentence start, complete thought, no outside
      context needed, strong first 3 seconds; prefer emotion, opinions, player praise, quotable lines; skip reporter questions).
   d. Speaker crop (multi person tables). Grab frames from source.mp4 at 3 points
      (`ffmpeg -ss <t> -i work/<id>/source.mp4 -frames:v 1 -vf scale=960:-1 /tmp/src_<t>.jpg`) and look at them.
      If more than one face is at the table, work out each seat's x position as a fraction of the frame width and who speaks in
      each clip (from the questions and answers in the transcript). Render clips in groups by speaker, one okst_clip.py run per
      group with `--speaker-x <fraction>`. A single person at a podium needs no --speaker-x.
   e. Highlights video: same rules and search as /okst-clip step 2d
      (`python -m yt_dlp --flat-playlist --print "%(id)s | %(channel)s | %(duration)s | %(title)s" "ytsearch12:Oklahoma State vs <opponent> <sport> highlights <year>"`).
      Oklahoma State Athletics channel first, then the Big 12 Conference, then a network. Never a reupload channel. At least 30 s.
   f. Render: `python okst_clip.py "<URL>" --color orange --ranges "<m:ss.s-m:ss.s>,..." [--highlights "<url>"] [--speaker-x <f>] --out "$GITHUB_WORKSPACE/out/<slug>"`
      If a requested start equals the previous sentence's last word end, add 0.05 s to the start.
3. Still frame check exactly as /okst-clip step 3, plus: the person in the top half must be the one speaking in that clip.
   Also run `ffprobe -v error -select_streams a:0 -show_entries stream=duration -of csv=p=0 <mp4>` on every clip: audio must
   cover the clip's length. Wrong speaker or short audio = hold that clip.
4. Captions to <base>.txt exactly as /okst-clip step 4 (press conference 3 line format; all rules: no hyphens or dashes, only
   facts from the clip, title, or description, names spelled from the official roster). Hashtags: #OKState first, the opponent,
   the sport tag (#OKStateFootball, #OKStateMBB, #OKStateWBB, #OKStateWrestling, #OKStateBaseball, #OKStateSoftball), and tags
   that match the clip. Keep every .draft.txt.
4b. Covers: `python make_cover.py "<OUT>/<base>.mp4" --color orange`, look at every cover, rerun with --text when one fails
   (move the bad one to /tmp first, never overwrite).
4c. Opening photo: football only, exactly as /okst-clip step 4c (game_photos.py, jersey number from the okstate.com roster,
   on field game photos only, never headshots, never guess who is in a photo). Every other sport: skip the intro and say so.
5. Flags to OUT/review-flags.json as in /okst-clip step 5. Add a rights flag naming the highlights video to every split
   screen clip. A "Hold: <reason>" flag for each clip you recommend holding.
6. When <base>.intro.mp4 exists, rename the plain <base>.mp4 to <base>.plain.mp4 and the intro version to <base>.mp4
   (the version that ships), so the .txt and .cover.jpg names still match.
7. `python make_review_page.py "<OUT>" --flags "<OUT>/review-flags.json" --no-open`.
8. SHIP, unless the input said hold, the daily cap is reached, you recommend holding every clip, or a name question is open.
   For each recommended clip in file name order (case insensitive), stop if the day's total in POST LOG would pass 6:
   - Check: a final .txt with the same base name, #OKState first of exactly 5 hashtags, no hyphens or dashes, blank lines
     between the parts (one right before the hashtags). Never ship a .draft.txt.
   - `doppler run -p okst -c dev -- python ig_publish.py --reel "<OUT>/<base>.mp4"` once. If <base>.published.json already
     exists, skip it. If a post fails, stop the batch, never retry, and put the error in REPORT. A "WARNING: Facebook post
     failed" still counts as shipped.
   - Append to POST LOG: {"date": "<YYYY-MM-DD America/Chicago>", "video": "<URL>", "clip": "<base>.mp4", "instagram": "<permalink>", "facebook": "<link>"}
   - Move the mp4, .txt, .cover.jpg, and .published.json to POSTED.
   Then move everything left (held clips, all .draft.txt, review.html) to HOLD.
9. REPORT (GitHub flavored markdown, it becomes the issue body): first line `**Shipped N of M clips**` (or why nothing
   shipped), then the video title linked to its URL, the highlights video (or none and why), transcript source, corrections
   added, ambiguous names flagged, speaker crop used, intro photo used or skipped, then the /okst-clip step 8 table (clip
   number, file, range, length, why picked, hook line, hashtags, flags, ship or hold) with each shipped clip's Instagram
   permalink and Facebook link. End with: "Clip files: see the run's okst-clips artifact." List any held clip a human could
   still ship.

## Rules
Never log into Instagram, Meta, X, TikTok, or Repurpose. Nothing posts except through step 8.
Never invent a fact or a name. If something is missing, hold and say so in REPORT.
No hyphens or dashes in any caption text.
Never delete files; move them. Only review.html and review-flags.json may be rewritten.
Never edit chopify's code except CORRECTIONS in okst_clip.py (step 2b); the workflow commits CORRECTIONS changes back.
The crop never follows anyone's head: one fixed position per clip. Never reintroduce speaker tracking.
