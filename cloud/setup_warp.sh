#!/usr/bin/env bash
# Start the official Cloudflare WARP client in proxy mode (SOCKS5 127.0.0.1:40000) and point yt-dlp at it.
# YouTube bot-checks GitHub's Azure IPs; through WARP it serves normally (tested 2026-11-03).
set -euo pipefail
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
sudo apt-get update -qq && sudo apt-get install -y -qq cloudflare-warp >/dev/null
sleep 3
warp-cli --accept-tos registration new >/dev/null 2>&1 || true
warp-cli --accept-tos mode proxy
warp-cli --accept-tos connect
for i in $(seq 1 30); do warp-cli --accept-tos status | grep -q Connected && break; sleep 2; done
warp-cli --accept-tos status
mkdir -p ~/.config/yt-dlp
echo "--proxy socks5://127.0.0.1:40000" > ~/.config/yt-dlp/config
echo "WARP exit: $(curl -s --socks5-hostname 127.0.0.1:40000 --max-time 20 https://ipinfo.io/org || echo unknown)"
