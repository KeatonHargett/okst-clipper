"""Print the newest @okstprobs Instagram posts (date + first caption lines), for the duplicate check.
Run from chopify/: doppler run -p okst -c dev -- python ../cloud/ig_recent.py [count]
Another brand: IG_ACCOUNT=fps doppler run ... (accounts in ig_publish.ACCOUNTS)."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "chopify"))
import ig_publish  # noqa: E402

ig_publish.use_account(os.environ.get("IG_ACCOUNT", ""))
token = ig_publish.load_token()
n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
media = ig_publish.get(ig_publish.media_path(), token, fields="caption,timestamp,permalink", limit=n).get("data", [])
for m in media:
    cap = " / ".join(line for line in (m.get("caption") or "").splitlines() if line.strip())[:220]
    print(f"{m.get('timestamp', '')[:16]}  {cap}")
