#!/usr/bin/env bash
# Install the Doppler CLI with retries.
# dopplerhq/cli-action@v3 runs Doppler's install.sh under a hard 30 s timeout and died with
# "spawnSync /bin/sh ETIMEDOUT" 11 times between Nov 5 and Nov 9 2026 (7 scouted reposts were lost).
# This tries install.sh up to 3 times with a 2 minute budget each, then falls back to the release tarball.
set -uo pipefail
dest="${1:-$HOME/.local/bin}"
mkdir -p "$dest"
if command -v doppler >/dev/null 2>&1; then echo "doppler already installed: $(doppler --version)"; exit 0; fi
for i in 1 2 3; do
  if curl -fsSL --connect-timeout 15 --max-time 60 --retry 2 https://cli.doppler.com/install.sh -o /tmp/doppler-install.sh \
     && timeout 120 sh /tmp/doppler-install.sh --no-package-manager --install-path "$dest" >/tmp/doppler-install.log 2>&1 \
     && [ -x "$dest/doppler" ]; then
    break
  fi
  echo "doppler install.sh attempt $i failed:"; tail -n 5 /tmp/doppler-install.log 2>/dev/null || true
  sleep $((i * 10))
done
if [ ! -x "$dest/doppler" ]; then
  echo "falling back to the release tarball"
  for i in 1 2 3; do
    if curl -fsSL --connect-timeout 15 --max-time 180 --retry 2 \
         "https://cli.doppler.com/download?os=linux&arch=amd64&format=tar" -o /tmp/doppler.tar.gz \
       && tar -xzf /tmp/doppler.tar.gz -C /tmp doppler \
       && install -m 755 /tmp/doppler "$dest/doppler"; then
      break
    fi
    echo "tarball attempt $i failed"; sleep $((i * 10))
  done
fi
[ -x "$dest/doppler" ] || { echo "::error::Doppler CLI could not be installed (cli.doppler.com and GitHub releases both failed)"; exit 1; }
echo "$dest" >> "${GITHUB_PATH:-/dev/null}"
echo "doppler installed: $("$dest/doppler" --version)"
