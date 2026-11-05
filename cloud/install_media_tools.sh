#!/usr/bin/env bash
# ffmpeg + fonts without apt. The runners' Ubuntu mirror stalled for 15 to 25 minutes on Nov 4 2026
# (even with timeouts and retries), so pull static files from GitHub, which is fast from GitHub runners.
# Falls back to apt only if a download fails.
set -uo pipefail
here="$(dirname "$0")"
ok=1
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  if curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 -o /tmp/ffmpeg.tar.xz \
       https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz \
     && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp \
     && sudo install -m 755 /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe /usr/local/bin/; then
    echo "ffmpeg: static build installed"
  else ok=0; fi
fi
# DejaVu at the system path (chopify/game_photos.py reads /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf).
if [ ! -f /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf ]; then
  if curl -fsSL --retry 3 --connect-timeout 20 --max-time 120 -o /tmp/dejavu.tar.bz2 \
       https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2 \
     && tar -xjf /tmp/dejavu.tar.bz2 -C /tmp \
     && sudo mkdir -p /usr/share/fonts/truetype/dejavu \
     && sudo cp /tmp/dejavu-fonts-ttf-2.37/ttf/*.ttf /usr/share/fonts/truetype/dejavu/; then
    echo "fonts: DejaVu installed"
  else ok=0; fi
fi
if [ "${WANT_EMOJI:-0}" = 1 ] && ! fc-list | grep -qi "Noto Color Emoji"; then
  if sudo mkdir -p /usr/share/fonts/truetype/noto \
     && sudo curl -fsSL --retry 3 --connect-timeout 20 --max-time 120 -o /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf \
          https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/fonts/NotoColorEmoji.ttf; then
    echo "fonts: Noto Color Emoji installed"
  else ok=0; fi
fi
fc-cache -f >/dev/null 2>&1 || true
if [ "$ok" = 1 ]; then ffmpeg -hide_banner -version | head -1; exit 0; fi
echo "::warning::a GitHub download failed, falling back to apt"
pkgs="ffmpeg fonts-dejavu-core"; [ "${WANT_EMOJI:-0}" = 1 ] && pkgs="$pkgs fonts-noto-color-emoji"
bash "$here/apt_install.sh" $pkgs
