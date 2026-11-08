---
name: okst-ship
description: Post approved OKST clips (as Reels) and carousels straight to Instagram through the API. Use only when Keaton runs /okst-ship, or when /okst-repost or /okst-clip ship automatically by following this file.
argument-hint: <video-slug> <clip numbers, e.g. 1,3>
disable-model-invocation: true
---

# OKST Ship

Input: $ARGUMENTS
First token is the video slug. Second token is the approved clip numbers.

PROJECT: C:\Projects\chopify
REVIEW FOLDER: "G:\My Drive\OKST-Review\<video-slug>\"
POSTED FOLDER: "G:\My Drive\OKST-Posted\<video-slug>\"
HOLD FOLDER: "G:\My Drive\OKST-Hold\<video-slug>\"

Everything posts straight to Instagram through the API, the moment it ships: clips as Reels (shared to the feed),
carousels and single photos as posts. The same ig_publish.py run then posts it to the OKSTProbs Facebook Page
(FB Reel, or a Page video when over 90s or not 9:16; photo or multi photo post for carousels; video slides stay
Instagram only). Instagram never auto shares API posts to Facebook. Instagram and Facebook are the only places anything
posts, both through ig_publish.py. Nothing goes to "G:\My Drive\OKST-Clips" anymore (an old, retired folder; never
put anything there).

CAROUSELS: if the REVIEW FOLDER has a carousel.json, follow CAROUSEL SHIP below instead of Steps.

APPROVAL: the command is the approval. Never ask me to type yes. Running /okst-ship, or sending a link to /okst-repost
or /okst-clip (which ship automatically by following this file), means ship it. The checks below still run: if any check
fails, ship nothing for that item, leave it in the REVIEW FOLDER, and tell me why plus the /okst-ship command to retry.
Flags (rights, credit, watermarks) never stop a ship; just list them in the report.

## Steps (clips and single videos, posted as Reels)
1. Open the REVIEW FOLDER and list every mp4 with a number, sorted by file name, case insensitive. This is the same numbering as review.html and the okst-clip and okst-repost reports.
2. For each approved number, confirm the mp4 and a final .txt with the exact same base name both exist. Never ship a .draft.txt. If the final .txt is missing, stop.
   A <base>.cover.jpg next to the mp4 is optional; when it is there, ig_publish.py uploads it as the Reel's grid cover.
3. For each approved caption, confirm: #OKState is the first of exactly 5 hashtags, there are no hyphens or dashes anywhere, and blank lines separate the parts (always one right before the hashtags). If not, stop and show me the caption.
4. Confirm no file with the same name already exists in the POSTED FOLDER, or in the HOLD FOLDER for anything that will go there. If one does, stop and tell me.
5. (No confirmation. See APPROVAL.)
6. Post the approved clips one at a time, in number order. For each, from PROJECT run once:
   doppler run -p okst -c dev -- .venv\Scripts\python ig_publish.py --reel "<REVIEW FOLDER>\<mp4 file name>"
   It checks the video and caption, serves the file briefly through a Cloudflare quick tunnel, waits for Instagram to
   process it (usually about a minute), publishes, writes <base>.published.json next to the mp4, then posts to Facebook.
   A Facebook failure prints "WARNING: Facebook post failed" but the Instagram post still counts as shipped: keep going,
   and in the report give the retry command (from PROJECT, same doppler prefix): ig_publish.py --reel "<POSTED FOLDER>\<mp4>" --fb-only
   If <base>.published.json already exists, that clip already went out: skip it.
   If a post fails, stop the batch there. Never retry on your own: show me the error and the /okst-ship command to retry.
   Clips already posted stay posted.
7. Move each posted mp4, its .txt, its .cover.jpg (if any), and its .published.json to the POSTED FOLDER (create it if missing).
   If every approved clip posted, move everything left in the REVIEW FOLDER (unapproved clips, their .txt and .cover.jpg
   files, all .draft.txt files, and review.html) to the HOLD FOLDER (create it if missing). If a post failed, leave the rest in
   the REVIEW FOLDER so the retry command works.
8. Report each posted clip with its Instagram permalink and its Facebook link (the FACEBOOK line, also in published.json),
   what moved where, and anything that did not post and why.
   End with clickable links to the POSTED FOLDER and the HOLD FOLDER.

## CAROUSEL SHIP
A carousel is always item 1. If the approved numbers are not exactly 1, stop and tell me.
1. If published.json is already in the REVIEW FOLDER, stop: it was posted. Show me its permalink.
2. Confirm <slug>.txt (never the .draft.txt) and every slide listed in carousel.json exist, and the caption passes the step 3 checks.
3. From PROJECT run the dry run: doppler run -p okst -c dev -- .venv\Scripts\python ig_publish.py "<REVIEW FOLDER>" --dry-run
   If it fails, stop and show me why.
4. (No confirmation. See APPROVAL.)
5. Run once: doppler run -p okst -c dev -- .venv\Scripts\python ig_publish.py "<REVIEW FOLDER>"
   Never rerun it after a failure: show me the error and the /okst-ship command to retry. Check published.json first: if it exists, the post went out.
   A Facebook failure after PUBLISHED is not a failed ship: continue, and give the retry command
   ig_publish.py "<POSTED FOLDER>" --fb-only (from PROJECT, same doppler prefix) in the report.
6. On PUBLISHED, move everything in the REVIEW FOLDER (slides, caption, carousel.json, published.json, review.html) to the POSTED FOLDER. Create it if missing.
   If the POSTED FOLDER already has a file with the same name, stop and tell me.
7. Report the Instagram permalink, the Facebook link, the account, the slides and caption that went out, the flags, what moved where,
   and a clickable link to the POSTED FOLDER.

If the script says the token expired or was revoked, tell me to run /okst-ship again after Claude refreshes it (Claude reopens the Meta token page and I copy a new token).
If "Facebook Page:" shows BROKEN, Facebook needs a new token: Claude regenerates the Keaton Social Assistant token in Graph API
Explorer, runs ~/.claude/scripts/meta-token-exchange.ps1, then ~/.claude/scripts/okst-fb-page-token.ps1. Instagram still posts meanwhile.
Instagram allows 100 API posts per 24 hours, shared with anything else posting through the API. If a post fails on that limit, say so.

## Clickable links
Write every local path as a markdown link with a file:/// URL so I can Ctrl+click it in the terminal:
use forward slashes and encode spaces as %20 (also # as %23 and % as %25). For example
[clip.mp4](file:///G:/My%20Drive/OKST-Posted/<video-slug>/clip.mp4)

## Rules
Never delete anything.
Never overwrite files.
Never log into Instagram or Meta (token refreshes above are the only exception).
Never post a .draft.txt caption.
Never copy or move anything to "G:\My Drive\OKST-Clips".
