---
name: okst-repost-cloud
description: Unattended cloud version of /okst-repost, run by the okst-repost GitHub Actions workflow when Keaton shares a link from his iPhone. Reposts the TikTok, Instagram, X, or YouTube post to Instagram @okstprobs and the OKSTProbs Facebook Page and writes a report for the GitHub issue.
argument-hint: <url> [@handle] [hold]
disable-model-invocation: true
---

# OKST Repost (cloud)

Input: $ARGUMENTS
First token is the post URL (the workflow already stripped tracking parameters). Optional: `@handle` (the creator, overrides
the info JSON), `hold` (make and stage everything, ship nothing).

You run alone on a GitHub Actions runner. Nobody can answer a question. Wherever the desktop /okst-repost would stop and ask,
write the question into REPORT, ship nothing, and finish. Never wait. Sharing the link was Keaton's approval: ship without asking.

PROJECT: $GITHUB_WORKSPACE/chopify (run every python command from here as `python ...`)
LOCAL OUTPUT: PROJECT/output/repost/<slug>/ (the scripts write here)
STAGE: $GITHUB_WORKSPACE/out/<slug>/ (the review folder; the workflow uploads all of out/ as the run's artifact)
REPORT: $GITHUB_WORKSPACE/out/report.md (becomes the GitHub issue body; write it even when nothing ships)
TITLE: $GITHUB_WORKSPACE/out/title.txt (one line for the issue title, e.g. `Posted: @velsports1 TikTok` or `Held: @x (reason)`)
LOG: $GITHUB_WORKSPACE/state/reposted.jsonl (one JSON line per shipped post; the workflow commits it)
<slug>: repost-<creator handle>-<post id>, lowercase, capped at 60 characters (the fetch scripts print it for Instagram and X).

Already done by the workflow: the Doppler CLI is logged in (DOPPLER_TOKEN), ffmpeg, Chrome, yt-dlp and faster-whisper are
installed, and for TikTok and YouTube links Cloudflare WARP is on (yt-dlp's config has --proxy). The workflow also skipped the
run if LOG already has this URL. Never print a secret, never run `doppler secrets get` or `doppler secrets download`.

## Routing
- Instagram: `doppler run -p okst -c dev -- python okst_carousel.py fetch "<URL>" [--handle @handle]`.
  TYPE video: Steps 1 to 9 (download with yt-dlp as in step 1). TYPE carousel: CAROUSEL.
- X (x.com, twitter.com): `python okst_xcard.py fetch "<URL>" [--handle @handle]` (dark theme). TYPE video: FILE is the mp4;
  skip Steps 1, 3, 4. TYPE carousel: CAROUSEL from C2. If it fails on a video post, try Steps 1 to 9 on the bare video.
- TikTok, YouTube: Steps 1 to 9. A TikTok /photo/ link that yt-dlp cannot read: hold, and in REPORT tell Keaton to save the
  images and run /okst-repost on the PC.

## Steps
1. GET THE VIDEO: `python -m yt_dlp -f "bv*+ba/b" --merge-output-format mp4 --write-info-json -o "output/repost/<slug>/%(id)s.%(ext)s" "<URL>"`.
   Retry once after an HTTP 403 or rate limit. If it still fails or needs a login: hold, and say in REPORT to save the video
   to "G:\My Drive\OKST-Inbox" and run `/okst-repost latest` on the PC.
   Then compare the video and audio stream lengths with ffprobe. If they differ by more than 1 s (TikTok can pair the video
   with a longer separate music track), list formats with `-F`, download the best single format that has both video and audio
   (`-f "b[vcodec!=none][acodec!=none]"`) as `<id>_av.mp4`, and use that file. Say so in REPORT.
2. CREATOR HANDLE: the @handle given, else the info JSON (TikTok `uploader`, Instagram `channel`, YouTube `uploader_id` or
   `channel`). Never a numeric id. Conflict between the given handle and the info JSON, or no handle at all: hold and ask in REPORT.
3. CHECK THE FILE: H.264 + AAC mp4 stays exactly as is. Otherwise
   `ffmpeg -hide_banner -n -i in.mp4 -map 0:v:0 -map 0:a:0 -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart <id>_h264.mp4`.
   Never crop, trim content, add a logo or captions, or remove, blur, or cover a watermark.
4. BLACK TAIL: exactly as /okst-repost step 4 (`blackdetect=d=0.1:pix_th=0.10`; only a last segment ending within 0.15 s of
   the duration and longer than 0.5 s; speech check on the tail with faster-whisper small, vad_filter on; new `<id>_trim.mp4`
   cut at black_start, verified with ffprobe and a second blackdetect). Any unclear case: do not trim, ship the untrimmed file,
   and say why in REPORT.
5. UNDERSTAND: export 3 frames (start, middle, end) with `-vf "scale=720:-1:out_range=full"` and look at them. Transcribe speech
   with faster-whisper small (cpu, int8, vad_filter). Read the original caption from the info JSON.
6. CAPTION to <base>.txt (same base name as the mp4), UTF-8 without BOM:
     <hook under 10 words>

     <sentence 1 under 15 words>
     <sentence 2 under 15 words>

     🎥 @handle

     #OKState #Tag2 #Tag3 #Tag4 #Tag5
   Keaton's voice, never the original wording. No hyphens or dashes anywhere. Only facts seen, said, or in the original caption:
   never invent scores, dates, names, or quotes. Under 2,200 characters.
7. FLAGS to LOCAL OUTPUT/review-flags.json as {"<mp4 file name>": [...]}: other app watermarks (TikTok, CapCut), TV broadcast
   footage (rights risk), individual fan or photographer creators (DM for permission), copyrighted music, X handle not
   necessarily the same Instagram account. Flags never stop a ship.
8. STAGE: copy the mp4 and .txt to STAGE, then `python make_review_page.py "<STAGE>" --flags "<LOCAL OUTPUT>/review-flags.json" --no-open`.
9. SHIP (unless hold or an open question): check the caption (#OKState first of exactly 5 hashtags, no hyphens or dashes, a
   blank line between parts and right before the hashtags), then once:
   `doppler run -p okst -c dev -- python ig_publish.py --reel "<STAGE>/<mp4>"`.
   If <base>.published.json already exists, it went out: do not run it again. A failed publish: never retry; put the error in
   REPORT. "WARNING: Facebook post failed" still counts as shipped: give the retry command in REPORT.
   Append to LOG: {"date": "<YYYY-MM-DD America/Chicago>", "url": "<URL>", "slug": "<slug>", "instagram": "<permalink>", "facebook": "<link or FAILED>"}

## CAROUSEL
C2. Handle: the post owner from the fetch output or the given @handle. HANDLE_CONFLICT: hold and ask in REPORT.
C3. Look at every slide JPEG in LOCAL OUTPUT; video slides get 3 frames and a transcript. Read the original caption.
C4. Caption to LOCAL OUTPUT/<slug>.txt, same rules as step 6, credit line `📸 @handle`.
C5. Flags to LOCAL OUTPUT/review-flags.json as {"<slug>": [...]}: the FLAG lines, plus step 7 flags, plus screenshots of other
    accounts' posts or news graphics.
C6. Copy carousel.json, every slide it lists, and <slug>.txt to STAGE; run make_review_page.py as in step 8.
C7. Ship (unless hold or an open question): if STAGE/published.json exists, stop. Dry run
    `doppler run -p okst -c dev -- python ig_publish.py "<STAGE>" --dry-run`; if it fails, hold with its error. Then run once
    without --dry-run. Facebook failure after PUBLISHED: still shipped, give the --fb-only retry command. Append to LOG.

## REPORT
GitHub flavored markdown. First line: `**Posted**` with the Instagram permalink and the Facebook link, or `**Held:** <reason>`.
Then: source URL, creator handle and where it came from, length and resolution (or slide count with each slide's type and
size), trim result, the full caption in a code block with its character count, the flags, and any question for Keaton.
If it did not ship, end with: "Ship it from the PC: `/okst-repost <URL>`" (or `hold` removed).
Write TITLE too.

## Rules
Never log into Instagram, Meta, X, TikTok, or Repurpose. Never use cookies. Nothing posts except step 9 or C7.
Never delete or overwrite files except review.html and review-flags.json. Never edit chopify's code.
Never remove or hide a creator's watermark or credit. Never cut content; the only cut is a black tail (step 4).
