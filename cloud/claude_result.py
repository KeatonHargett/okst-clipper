"""Print the final result of a `claude -p ... --output-format stream-json` run and, when Claude never wrote
out/report.md, write one that says why (so the GitHub issue explains the failure instead of "No report written").
Exits 1 in that case so the run shows red instead of a green run with an empty issue.

Usage: python3 cloud/claude_result.py <claude.jsonl> <claude.err> <claude exit code>
Issues are public: token looking strings are scrubbed and only the stderr tail is included."""
import json
import pathlib
import re
import sys

jsonl, err, rc = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
events = []
if jsonl.exists():
    for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
results = [e for e in events if e.get("type") == "result"]
final = results[-1] if results else None
print(str(final.get("result", ""))[:3000] if final else "no result")

report = pathlib.Path("out/report.md")
if report.exists() and report.stat().st_size > 0:
    sys.exit(0)


def scrub(text):
    return re.sub(r"(sk-ant-[\w-]+|dp\.\w+\.[\w-]+|EAA\w{20,}|IG[A-Za-z0-9]{20,}|ghp_\w+|github_pat_\w+)",
                  "[masked]", text)


why = []
if final is None:
    why.append(f"`claude -p` exited {rc} with no result event after {len(events)} stream events "
               "(Claude token, model name, or startup failure).")
else:
    if final.get("is_error"):
        why.append(f"result is_error (exit {rc}): {str(final.get('result', ''))[:800]}")
    else:
        why.append(f"Claude finished (exit {rc}) but never wrote out/report.md. Last message: "
                   f"{str(final.get('result', ''))[:800]}")
    if final.get("subtype") not in (None, "success"):
        why.append(f"subtype: {final['subtype']}")
if err.exists():
    tail = "\n".join(err.read_text(encoding="utf-8", errors="replace").splitlines()[-15:])[-1500:]
    if tail.strip():
        why.append("stderr tail:\n```\n" + tail + "\n```")
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text("**Claude did not finish.** " + scrub("\n\n".join(why)) + "\n", encoding="utf-8")
print("::error::Claude did not finish; the reason is in out/report.md")
sys.exit(1)
