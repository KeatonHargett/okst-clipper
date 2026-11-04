---
name: okst-clip
description: Clip a YouTube video, a single chapter, or a time window into OKST Reels with OSU styled captions and the Probs logo, then ship the recommended clips automatically. Use when Keaton runs /okst-clip with a URL.
argument-hint: <youtube-url> [orange|turquoise] [chapter="text"] [start=mm:ss] [end=mm:ss] [highlights=<url>|highlights=none] [intro=none] [hold]
disable-model-invocation: true
---

# OKST Clip

Input: $ARGUMENTS
First token is the URL. Then optional, in any order:
  orange or turquoise (default orange)
  chapter="text"
  start=mm:ss and/or end=mm:ss
  highlights=<YouTube URL>: the highlights video for the split screen (step 2d). highlights=none renders the old full frame layout.
  intro=none: skip the opening title card and whoosh (step 4c).
  hold: make and stage the clips, but do not ship. Otherwise the recommended clips ship automatically (step 9).
chapter cannot be combined with start or end. If both are given, stop and ask me which to use.

PROJECT: C:\Projects\chopify
LOCAL OUTPUT: output\okst\<slug>\
REVIEW FOLDER: "G:\My Drive\OKST-Review\<slug>\"
<slug> is the video slug, plus the slugified chapter title when chapter is used (for example yt-video-title-eric-morris).

## Steps
1. cd into PROJECT. Confirm okst_clip.py, logo.png, and fonts\impact.ttf exist. If any is missing, stop and tell me.
2. Pick and render the clips:
   a. Run: .venv\Scripts\python okst_clip.py "<URL>" --transcript-only [--chapter "<text>"] [--start <t>] [--end <t>]
      If the script stops and prints a chapter list, show me the list and wait for my pick. Never guess the chapter.
      The run folder is printed as "Run folder:". Its words.txt is the word list with video timestamps, sentences.txt gives every sentence's start and end to a tenth of a second (use these for exact ranges), and corrections.json lists the corrections already applied.
   b. Spelling check before picking: flag any word that looks like a misspelled name or place (compare against the video title, chapter title, CORRECTIONS, and PROJECT\OSU_ROSTER.txt including its CONFIRMED_NAMES section).
      Add a fix to CORRECTIONS in okst_clip.py only if the corrected spelling is on OSU_ROSTER.txt, in CONFIRMED_NAMES, or on an official team site. Never add a lone first name or a shared last name, and never guess a spelling. Ambiguous names get flagged in the report, never added.
      If you add any, rerun step a so words.txt and sentences.txt carry the fixes, and tell me which you added.
   c. Read words.txt and sentences.txt and pick 3 to 6 clips. Each clip must:
        be 15 to 60 seconds,
        start at the beginning of a sentence,
        end on a complete thought,
        make sense with no outside context (names spelled out or clear from the clip),
        have a strong first 3 seconds.
      Prefer emotion, strong opinions, specific player praise, and quotable lines. Skip reporter questions and filler.
      Stay inside the chapter or start/end window when one was given.
   d. Pick the highlights video (split screen: speaker on top, muted looping highlights below, captions on the seam).
      If I gave highlights=<url>, use it. If I said highlights=none, leave --highlights out. Otherwise search YouTube
      (.venv\Scripts\python -m yt_dlp --flat-playlist --print "%(id)s | %(channel)s | %(duration)s | %(title)s" "ytsearch12:Oklahoma State vs <opponent> highlights <year>")
      for the game the video talks about most (the most recent game if it is not about one game), taking the opponent only from the
      title, description, or transcript. Use, in this order: an Oklahoma State Athletics channel highlights video, then the
      Big 12 Conference channel, then a network (FOX, ESPN). Never a reupload channel. It must be at least 30 seconds long.
      If no official one exists, render without --highlights and say so in the report.
      Run: .venv\Scripts\python okst_clip.py "<URL>" --color <color> --ranges "<m:ss.s-m:ss.s>,<m:ss.s-m:ss.s>,..." [--highlights "<highlights URL>"] --out "output\okst\<slug>"
      Ranges are video times (never combine --ranges with --chapter, --start, or --end). Each start snaps to the beginning of a word and each clip gets the clean ending check. ranges.json in the run folder logs each clip's requested, snapped, and final range, its ending check, and its render time.
      If a requested start equals the previous sentence's last word end, it snaps back onto that word: add 0.05 s to the start.
      If a section download stalls (no progress for 2 minutes) or writes an audio only cut.mp4, stop it and use the HLS full
      download + local cuts workaround (memory: okst-section-download-stall), then rerun the same command.
      Each clip also gets <base>.frame.jpg, a caption free middle frame for its cover (step 4b).
   Fallback: only if --transcript-only fails, run the old scorer flow instead and tell me:
      .venv\Scripts\python okst_clip.py "<URL>" --color <color> [--chapter "<text>"] [--start <t>] [--end <t>] --out "output\okst\<slug>"
      If fewer than 2 clips pass, rerun with --min-score 5.5 and tell me you lowered it.
3. For each clip, export a still frame from its middle. Check: Impact, slanted, all caps, spoken word in the chosen color, other words white, black outline, logo clear of the text. With highlights: the speaker fills the top half with no ticker or lower third at the seam, and highlights fill the bottom half. Flag any clip that fails.
4. For each clip, read its .draft.txt and transcript, then write the final caption to <base>.txt (exact same base name as the mp4):
   Press conferences and media availability use exactly 3 lines (no hook, no extra sentences):
     Line 1: <Full name> spoke <day> ahead of <day's> game against <opponent>.
             If the video is not tied to an upcoming game: <Full name> spoke <day>.
     Line 2: <Last name> on <topic of the clip>. Under 12 words, plain and factual, no wordplay, based only on what is said in the clip.
     Line 3: exactly 5 hashtags, #OKState first, the opponent tag included (when there is an opponent), the rest matching what the clip covers.
     Put one blank line between each of the 3 lines.
     Example:
       Eric Morris spoke Tuesday ahead of Saturday's game against UCF.

       Morris on settling for field goals in the score zone.

       #OKState #UCF #OKStateFootball #EricMorris #RedZone
     Take the day and opponent only from the video title, the video description, or what is said in the clip. Never guess them.
   All other videos:
     Line 1: a strong hook, under 10 words. Vary hook openings across the batch: never start more than one hook in a batch with the same name or phrase (for example only one hook may open with "Eric Morris").
     Then 2 short sentences, each under 15 words.
     Then exactly 5 hashtags: #OKState first, then 4 that match what the clip actually covers (sport, event, venue, people named in the clip).
     Put one blank line after the hook and one before the hashtags (the two sentences stay together).
   ig_publish.py refuses a caption with no blank lines or no blank line before the hashtags.
   No hyphens or dashes anywhere. Use only facts said in the clip, the video title, or the chapter title. Never invent scores, dates, or quotes.
   Spell names exactly as they appear in the video title, chapter title, or OSU_ROSTER.txt (for example Eric Morris).
   Keep the .draft.txt file.
4b. Covers. For each clip, from PROJECT run: .venv\Scripts\python make_cover.py "<LOCAL OUTPUT>\<base>.mp4" --color <color>
   It writes <base>.cover.jpg from <base>.frame.jpg and the hook (line 1, or line 2 for press conferences), sized to the
   profile grid's center crop. Look at every cover (one contact sheet is fine): text readable, no lone short word on a line.
   If one fails, run it again with --text "<shorter hook>" after moving the bad cover to the scratchpad (never overwrite).
   ig_publish.py uploads <base>.cover.jpg as the Reel's cover when it sits next to the mp4.
4c. Opening photo (Colin Cowherd style). Unless I said intro=none, each clip opens on an ON FIELD game photo of the
   player the clip talks about. NEVER a headshot, roster photo, or posed portrait.
   - Pick the player named in the clip. Get his jersey number from his okstate.com roster page, and confirm no other
     player on the roster wears it (the roster page lists "<Name> jersey number <N>").
   - From PROJECT run: .venv\Scripts\python game_photos.py  (official okstate.com game galleries, newest first). Look
     through every contact sheet it prints for that number, open any candidate at full size, and pick the photo where
     he is largest and sharpest. Prefer him in action (tackling, running, celebrating) and the most recent game.
   - Then: .venv\Scripts\python game_photos.py --get <gallery>_<index> --name <first-last>-<number>-<opponent>-<date>
     (reuse a photo already in PROJECT\assets\photos when it fits).
   - If several players are discussed, use the one the clip opens on. If the clip is about a coach or the team, use
     an on field game photo that fits (team running out, a celebration) with no single player singled out.
   - If no official game photo shows the player's number clearly, skip the intro for that clip and say so. Never guess
     who is in a photo, never use fan, reupload, or wire service photos.
   Then from PROJECT run: .venv\Scripts\python add_intro.py "<LOCAL OUTPUT>\<base>.mp4" --photo "assets\photos\<file>" --focus <x>,<y>
   (--focus is where the player is in the photo, as fractions of width and height, so the 9:16 crop centers on him.)
   It writes <base>.intro.mp4: the photo fills the screen with a slow push in, the clip's audio and live captions run
   over it, and at the first caption line change after 3 s it slides off left with a whoosh into the split screen.
   Check one contact sheet per clip at 0.1 s, the middle of the hold, and 0.2 s after the slide: the player's number
   visible in the crop, captions readable, clean slide into the clip.
5. Flag any clip that teases something it never delivers, cuts off mid thought, or needs outside context. Include any still frame that failed step 3, and a "Hold: <reason>" flag for each clip you recommend holding.
   Write the flags to LOCAL OUTPUT\review-flags.json as {"<mp4 file name>": ["flag", ...]} (an empty list for a clean clip).
6. Copy each finished mp4, its .txt, its .draft.txt, and its .cover.jpg to the REVIEW FOLDER. Create it if missing.
   When <base>.intro.mp4 exists, copy it to the REVIEW FOLDER as <base>.mp4 (that is the version that ships) instead of
   the plain <base>.mp4, so the .txt and .cover.jpg names still match.
   Add a rights flag to every clip that has highlights, naming the highlights video.
7. From PROJECT run: .venv\Scripts\python make_review_page.py "<REVIEW FOLDER>" --flags "<LOCAL OUTPUT>\review-flags.json" --no-open
   (Leave out --no-open only when I said hold, so the page opens for me.) It writes review.html as a record and prints the
   clickable links and clip numbers for steps 8 and 9.
8. Report: the window used (full video, chapter title with start and end, or custom start and end), the highlights video used (or none and why), the transcript source used (youtube human, youtube auto, or whisper), any corrections applied, and any ambiguous names flagged but not added, then a table with clip number, clip file, range (video time), length, why it was picked (one line), hook line (line 2 for press conferences), hashtags, flags, and ship or hold recommendation. Include the total render time.
   Number the clips by file name, case insensitive. This is the same numbering as review.html and /okst-ship.
   End the report with clickable markdown links (file:/// URLs) to the review page, the REVIEW FOLDER, and each mp4.
   Use the links make_review_page.py printed.
9. SHIP. Sending the link is my approval: do not ask me to confirm and do not wait for a yes.
   Unless I said hold, ship the clips you recommended shipping in step 8 by following ~/.claude/skills/okst-ship/SKILL.md
   for <slug> <those clip numbers>, exactly as written there. Its checks still apply; its step 7 moves the held clips,
   all .draft.txt files, and review.html to the HOLD FOLDER.
   Ship nothing, and say why, when: I said hold, you recommend holding every clip, a chapter or name question is still
   open, or an okst-ship check fails. Flags alone never stop a clip you recommended shipping.
   Each shipped clip posts straight to Instagram as a Reel and to the OKSTProbs Facebook Page. Add to the report each
   shipped clip's Instagram permalink, its Facebook link, and
   what went to HOLD. If anything did not ship, end with the command to ship it later in a code block: /okst-ship <slug> <numbers>

## Clickable links
Write every local path as a markdown link with a file:/// URL so I can Ctrl+click it in the terminal:
use forward slashes and encode spaces as %20 (also # as %23 and % as %25). For example
[OKST-Review/<slug>](file:///G:/My%20Drive/OKST-Review/<slug>/)

## Rules
Never copy or move anything to "G:\My Drive\OKST-Clips" (Repurpose's old Drive watch folder; it auto posts).
Never log into Instagram, Meta, X, TikTok, or Repurpose. Nothing posts except through step 9.
Never invent a fact or a name. If something is missing, stop and tell me.
No hyphens in any caption text.
Never delete or overwrite files. The only exceptions are generated helper files: review.html in the REVIEW FOLDER and review-flags.json in LOCAL OUTPUT may be rewritten.
Never edit chopify's code (CORRECTIONS in okst_clip.py excepted, per step 2b).
The camera never follows anyone's head: every vertical crop is locked (one fixed position per clip, from okst_clip.py and render_clips.py as of 2026-10-13). Never reintroduce speaker tracking, smooth_track, or sendcmd crops. If a still frame from step 3 shows the frame moving, flag it and hold the clip.
