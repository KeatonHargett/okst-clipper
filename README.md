# okst-clipper

Unattended cloud version of `/okst-clip`. No PC needed.

- **okst-watch** (every 2 hours, 9 am to midnight Central): lists the Oklahoma State Athletics YouTube channel and queues
  **okst-clip** for each new interview (any sport). `state/seen.json` remembers what it has seen.
- **okst-clip** (one video per run): Cloudflare WARP for YouTube, full video download, then Claude runs
  `.claude/skills/okst-clip-cloud` (pick clips, render with chopify, captions, covers, ship to Instagram + Facebook).
  Report = a GitHub issue; clip files = the run's `okst-clips` artifact (30 days). `state/posted-log.jsonl` feeds the 6 a day cap.
- **yt-download-test**: diagnostics for YouTube download methods.

Secrets: only `DOPPLER_TOKEN` (okst/dev) lives in GitHub. Claude and Meta tokens are read from Doppler and masked in logs.

`chopify/` is a copy of C:\Projects\chopify (MIT, mehbul/chopify) with two cloud patches: `OKST_FULL_SOURCE=1` (full download +
local cuts, because ffmpeg ignores the WARP SOCKS proxy) and a Linux font fallback in game_photos.py.

Manual run: Actions > okst-clip > Run workflow (url, hold).
