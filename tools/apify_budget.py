"""Apify budget guard: keep at least $1 of the month's Apify credit for OKST reposts (okst_carousel.py fetch).

Apify's Free plan has a hard $5 monthly limit; when it is used up, every Apify call 403s until the cycle resets
(the 22nd). Reposts are the only automated Apify use. One-off research runs (the claude.ai Apify connector, or any
ad hoc script) must check first.

    python apify_budget.py check [--estimate 0.60]   prints the credit left; exit 1 if the run would leave < $1
    python apify_budget.py hook                       Claude Code PreToolUse hook for mcp__claude_ai_AApify__* tools

The hook lets read-only Apify tools through, refuses charged ones that could dip into the reserve, and caps every
call-actor run with callOptions.maxTotalChargeUsd (pay-per-event actors stop billing at the cap). If the credit cannot
be checked, it refuses (fails closed). The token comes from Doppler okst/dev and is never printed.
"""
import json
import shutil
import subprocess
import sys
import urllib.request

RESERVE_USD = 1.00          # always left for reposts
DEFAULT_RUN_USD = 0.60      # assumed cost of one research run (largest this cycle: $0.56)
SMALL_CALL_USD = 0.05       # web fetch / search style calls
READ_ONLY = {"search-actors", "fetch-actor-details", "fetch-actor-mcp-tool-list", "fetch-apify-docs",
             "search-apify-docs", "get-actor-run", "get-dataset-items", "get-key-value-store-record",
             "abort-actor-run"}
SMALL = {"apify--web-fetch", "apify--web-search"}


def token():
    doppler = shutil.which("doppler") or "doppler"
    r = subprocess.run([doppler, "secrets", "get", "APIFY_TOKEN", "-p", "okst", "-c", "dev", "--plain"],
                       capture_output=True, text=True, timeout=30)
    tok = r.stdout.strip()
    if r.returncode != 0 or not tok:
        raise RuntimeError("could not read APIFY_TOKEN from Doppler okst/dev")
    return tok


def credit_left():
    req = urllib.request.Request("https://api.apify.com/v2/users/me/limits",
                                 headers={"Authorization": "Bearer " + token()})
    d = json.load(urllib.request.urlopen(req, timeout=30))["data"]
    limit = float(d["limits"]["maxMonthlyUsageUsd"])
    used = float(d["current"]["monthlyUsageUsd"])
    return limit - used, limit, used, (d.get("monthlyUsageCycle") or {}).get("endAt", "?")[:10]


def cmd_check(estimate):
    left, limit, used, reset = credit_left()
    ok = left - estimate >= RESERVE_USD
    print(f"Apify credit: ${used:.2f} of ${limit:.2f} used, ${left:.2f} left (resets after {reset}). "
          f"A ${estimate:.2f} run would leave ${left - estimate:.2f}: {'OK' if ok else 'REFUSED (keep $1 for reposts)'}")
    return 0 if ok else 1


def emit(decision, reason, updated=None):
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}
    if updated is not None:
        out["updatedInput"] = updated
    print(json.dumps({"hookSpecificOutput": out}))
    return 0


def cmd_hook():
    ev = json.load(sys.stdin)
    name = ev.get("tool_name", "")
    short = name.split("__")[-1]
    if short in READ_ONLY:
        return 0                                    # free: no decision, normal permission flow
    inp = dict(ev.get("tool_input") or {})
    try:
        left, limit, used, reset = credit_left()
    except Exception as e:
        return emit("deny", f"Apify budget guard: could not check the month's credit ({e}). Refusing so the "
                            "$1 repost reserve is safe. Check Doppler login / network and retry.")
    if short == "call-actor":
        opts = dict(inp.get("callOptions") or {})
        cap = float(opts.get("maxTotalChargeUsd") or DEFAULT_RUN_USD)
        if left - cap < RESERVE_USD:
            return emit("deny", f"Apify budget guard: ${left:.2f} of this month's ${limit:.2f} is left (resets after "
                                f"{reset}). A run capped at ${cap:.2f} could leave less than the ${RESERVE_USD:.2f} "
                                "kept for OKST reposts, so it is refused. Do not retry; tell Keaton.")
        opts["maxTotalChargeUsd"] = round(cap, 2)
        inp["callOptions"] = opts
        return emit("allow", f"Apify budget guard: ${left:.2f} left; run capped at ${cap:.2f} "
                             f"(leaves at least ${left - cap:.2f}).", inp)
    est = SMALL_CALL_USD if short in SMALL else DEFAULT_RUN_USD
    if left - est < RESERVE_USD:
        return emit("deny", f"Apify budget guard: ${left:.2f} of this month's ${limit:.2f} is left (resets after "
                            f"{reset}). This call (about ${est:.2f}) could dip into the ${RESERVE_USD:.2f} kept for "
                            "OKST reposts, so it is refused. Do not retry; tell Keaton.")
    return 0                                        # within budget: normal permission flow


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        sys.exit(cmd_hook())
    est = DEFAULT_RUN_USD
    if "--estimate" in sys.argv:
        est = float(sys.argv[sys.argv.index("--estimate") + 1])
    sys.exit(cmd_check(est))
