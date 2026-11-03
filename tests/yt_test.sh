#!/usr/bin/env bash
# Usage: yt_test.sh <method> <url> <section>
# Tries one YouTube download method and prints a one-line verdict.
set -u
M="$1"; URL="$2"; SEC="$3"
FULL=0; case "$M" in *-full*) FULL=1; M="${M%%-full*}";; esac
PROXY=""; EXTRA=()
ip() { curl -s ${PROXY:+--socks5-hostname ${PROXY#socks5://}} --max-time 20 https://ipinfo.io | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("ip"),"|",d.get("org"))' 2>/dev/null || echo "ip lookup failed"; }

start_warp() {
  curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq cloudflare-warp >/dev/null
  sleep 3
  warp-cli --accept-tos registration new || true
  warp-cli --accept-tos mode proxy
  warp-cli --accept-tos connect
  for i in $(seq 1 20); do warp-cli --accept-tos status | grep -q Connected && break; sleep 2; done
  warp-cli --accept-tos status
  PROXY="socks5://127.0.0.1:40000"
}
start_tor() { sudo apt-get install -y -qq tor >/dev/null; sudo systemctl start tor || sudo service tor start; sleep 15; PROXY="socks5://127.0.0.1:9050"; }
start_pot() { docker run -d --name bgutil -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider >/dev/null; pip install -q -U bgutil-ytdlp-pot-provider; sleep 8; }

case "$M" in
  direct) ;;
  tv) EXTRA=(--extractor-args "youtube:player_client=tv,web_safari") ;;
  mweb) EXTRA=(--extractor-args "youtube:player_client=mweb") ;;
  pot) start_pot ;;
  warp) start_warp ;;
  warp-tv) start_warp; EXTRA=(--extractor-args "youtube:player_client=tv,web_safari") ;;
  warp-pot) start_pot; start_warp ;;
  tor) start_tor ;;
esac
echo "IP: $(ip)"
rm -f out.*
timeout 300 yt-dlp -v --no-playlist -f "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b" \
  $( [ $FULL = 0 ] && echo --download-sections "*$SEC" ) --merge-output-format mp4 ${PROXY:+--proxy $PROXY} "${EXTRA[@]}" -o out.mp4 "$URL" > yt.log 2>&1
RC=$?
if [ $FULL = 1 ] && [ -s out.mp4 ]; then
  echo "full download: $(du -h out.mp4 | cut -f1) in ${SECONDS}s"
  A=${SEC%-*}; B=${SEC#*-}; ffmpeg -v error -y -ss $A -to $B -i out.mp4 -c copy cut.mp4 && mv cut.mp4 out.mp4
fi
grep -E "ERROR|WARNING|Sign in|429|403" yt.log | tail -6
if [ -s out.mp4 ]; then
  INFO=$(ffprobe -v error -show_entries format=duration:stream=codec_type,width,height -of csv=p=0 out.mp4 | tr '\n' ' ')
  echo "VERDICT $M: OK $(du -h out.mp4 | cut -f1) $INFO"
else
  echo "VERDICT $M: FAIL rc=$RC $(grep -m1 ERROR yt.log | cut -c1-160)"
fi
