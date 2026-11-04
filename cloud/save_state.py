"""Commit this run's changes to state files without merge conflicts.

Many repost runs finish at the same time and all append to the same .jsonl files, so `git pull --rebase`
conflicted and the run failed (and its log lines were lost). This replays the run's own changes on top of
the latest main and retries the push:
  .jsonl files: lines this run added are appended, lines it removed are removed (scout empties pending.jsonl).
  any other file: this run's version wins, but only if the run changed it.
Usage: python cloud/save_state.py "<commit message>" <file> [<file> ...]
"""
import random
import subprocess
import sys
import time
from pathlib import Path


def git(*args, check=True):
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def base_text(path):
    r = git("show", f"HEAD:{path}", check=False)
    return r.stdout if r.returncode == 0 else None


def main():
    msg, files = sys.argv[1], sys.argv[2:]
    changes = []
    for f in files:
        p = Path(f)
        if not p.exists():
            continue
        mine, base = p.read_text(encoding="utf-8"), base_text(f)
        if mine == base:
            continue
        if f.endswith(".jsonl"):
            old = [l for l in (base or "").splitlines() if l.strip()]
            new = [l for l in mine.splitlines() if l.strip()]
            changes.append((f, "lines", [l for l in new if l not in old], set(old) - set(new)))
        else:
            changes.append((f, "whole", mine, None))
    if not changes:
        print("state: nothing to save")
        return
    git("config", "user.name", "okst-bot")
    git("config", "user.email", "okst-bot@users.noreply.github.com")
    for attempt in range(1, 9):
        git("fetch", "-q", "origin", "main")
        git("reset", "-q", "--hard", "origin/main")
        for f, kind, a, removed in changes:
            p = Path(f)
            if kind == "whole":
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(a, encoding="utf-8")
            else:
                cur = [l for l in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []) if l.strip()]
                cur = [l for l in cur if l not in removed]
                cur += [l for l in a if l not in cur]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("".join(l + "\n" for l in cur), encoding="utf-8")
            git("add", f)
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            print("state: already on main")
            return
        git("commit", "-q", "-m", msg)
        if git("push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            print(f"state: saved {', '.join(c[0] for c in changes)} (attempt {attempt})")
            return
        time.sleep(random.uniform(2, 6) * attempt)
    sys.exit("state: push kept failing after 8 attempts")


if __name__ == "__main__":
    main()
