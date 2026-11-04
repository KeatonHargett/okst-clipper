#!/usr/bin/env bash
# Usage: bash cloud/apt_install.sh <package> [<package> ...]
# apt on GitHub runners sometimes stalls on a slow mirror for 10 to 25 minutes, which used up the whole run.
# Short network timeouts plus a hard time limit per try, retried up to 3 times, keep it to a few minutes at worst.
set -uo pipefail
echo 'Acquire::Retries "3"; Acquire::http::Timeout "20"; Acquire::https::Timeout "20"; Acquire::ftp::Timeout "20";' \
  | sudo tee /etc/apt/apt.conf.d/99okst-timeouts >/dev/null
for try in 1 2 3; do
  if sudo timeout 240 apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive timeout 300 apt-get install -y -qq "$@" >/dev/null; then
    echo "apt: installed $* (try $try)"; exit 0
  fi
  echo "::warning::apt try $try failed or timed out, retrying"
  sleep 5
done
echo "::error::apt could not install $*"; exit 1
