"""Look up a public Instagram business/creator account through Business Discovery (official, read only).

Usage: IG_ACCOUNT=fps doppler run -p okst -c dev -- python cloud/ig_lookup.py <username>
Prints the account's name, follower count and its 6 latest posts (date, type, first caption line), so you can confirm it is
the same creator before inviting it as a collaborator. Prints NOT FOUND for personal or missing accounts."""
import os, sys, json, urllib.request, urllib.parse, urllib.error
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "chopify"))
import ig_publish
ig_publish.use_account(os.environ.get("IG_ACCOUNT", ""))
user = sys.argv[1].lstrip("@").strip().lower()
token = ig_publish.load_token()
me = ig_publish.ACCOUNT["ig_id"] if ig_publish.ACCOUNT else ig_publish.whoami(token)["user_id"]
fields = (f"business_discovery.username({user}){{username,name,followers_count,"
          "media.limit(6){timestamp,media_product_type,caption,permalink}}")
url = f"https://graph.facebook.com/v23.0/{me}?" + urllib.parse.urlencode({"fields": fields, "access_token": token})
try:
    d = json.load(urllib.request.urlopen(url, timeout=30))["business_discovery"]
except urllib.error.HTTPError as e:
    print(f"NOT FOUND @{user} (personal, private or missing account)"); sys.exit(1)
print(f"@{d['username']}  {d.get('name', '')}  followers {d.get('followers_count', 0):,}")
for m in d.get("media", {}).get("data", []):
    cap = (m.get("caption") or "").split("\n")[0][:90]
    print(f"  {m['timestamp'][:10]} {m.get('media_product_type', '')}  {cap}  {m.get('permalink', '')}")
