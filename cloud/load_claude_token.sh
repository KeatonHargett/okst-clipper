#!/usr/bin/env bash
# Mask every okst/dev secret in the job log and export CLAUDE_CODE_OAUTH_TOKEN for claude -p.
# Fails loudly, with a one line report for the GitHub issue, instead of letting claude start with an empty token.
# Background: on Nov 7 2026, 18 runs "succeeded" with "No report written" because claude -p exited in 3 seconds
# (bad Claude token) and nothing here or in the Claude step checked anything.
set -uo pipefail
fail() { mkdir -p out; printf '**Doppler failed:** %s, so nothing ran.\n' "$1" > out/report.md; echo "::error::$1"; exit 1; }
json=$(doppler secrets download -p okst -c dev --no-file --format json 2>/tmp/doppler.err) \
  || fail "could not read okst/dev secrets with DOPPLER_TOKEN ($(head -c 300 /tmp/doppler.err | tr '\n' ' '))"
printf '%s' "$json" | python3 -c "import sys,json;[print('::add-mask::'+l) for v in json.load(sys.stdin).values() for l in str(v).splitlines() if len(l)>5]"
tok=$(printf '%s' "$json" | python3 -c "import sys,json;print(json.load(sys.stdin).get('CLAUDE_CODE_OAUTH_TOKEN',''))")
[ -n "$tok" ] || fail "CLAUDE_CODE_OAUTH_TOKEN is empty in okst/dev"
echo "CLAUDE_CODE_OAUTH_TOKEN=$tok" >> "$GITHUB_ENV"
echo "Claude token loaded (${#tok} chars)"
