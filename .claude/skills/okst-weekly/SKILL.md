---
name: okst-weekly
description: Monday report step for @okstprobs and @frontporchsports. Read work/weekly/data.json (last 7 days of posts with reach, by kind of post and by posting time) and write what worked, what didn't and next week's moves into work/weekly/notes.json. Run headless from GitHub Actions.
---

# OKST + Front Porch Sports weekly: the words

Keaton runs two accounts from this repo: **OKSTProbs** (`okst`, Oklahoma State fan account: interview and
podcast clips, game highlights, reposts) and **Front Porch Sports** (`fps`, Barstool-style sports reposts, new
since 2026-11-20). Code already built every table. You write only the words. Work alone, never ask questions.

Read `work/weekly/data.json`: per account `totals`, `vs_last_week` (empty on the first run), `by_kind` and
`by_hour` (average reach, only posts at least a day old), `best` / `worst` posts with captions, and `posts`.

Write `work/weekly/notes.json`:

```json
{"accounts": {"okst": {"worked": ["1-3 lines"], "flopped": ["0-2 lines"], "next": ["exactly 3 moves"]},
              "fps": {"worked": [], "flopped": [], "next": []}}}
```

Rules:
- Numbers only from data.json (or a percent change from `totals` and `vs_last_week`, or a whole-number ratio
  between two group averages). Code deletes any line with a number it can't find.
- `next` must be things the pipelines can change: which sources the scout watches, more or fewer of a kind of
  post, which hours to favor, hooks or caption style, Trial Reels vs main feed, clip length. Keaton never wants
  approval steps, so never suggest "review before posting" or anything that slows posting down. Interviews
  ship first and fast: never suggest delaying them.
- An account with fewer than 5 posts: say there isn't enough yet and give setup-level moves (more sources,
  posting volume), not conclusions.
- "posted by hand" means Keaton posted it himself. Say what those posts had that the automated ones didn't.
- Plain words, short sentences, no hype.

Write the file, check it parses as JSON, and stop.
