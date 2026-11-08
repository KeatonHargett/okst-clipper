"""Open GitHub issues that show images (the okst-highlights post log).

  python cloud/post_issues.py --title "..." --body-file out/report.md [--image a.jpg ...] [--prefix 401856854]
  python cloud/post_issues.py --dir out/issues            # every *.json there: {"title", "body", "images": [...]}

Issue bodies cannot attach files through the API, so each image is shrunk to 540 px wide and committed to the
repo's `covers` branch (an orphan branch holding only these images, created on first use; main stays clean), then
embedded by its raw.githubusercontent.com URL. Needs GH_TOKEN with contents: write and issues: write.
An image that fails to upload is listed by name instead; the issue still opens.
"""
import argparse
import base64
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

BRANCH = "covers"


def gh(*args, data=None):
    r = subprocess.run(["gh", *args], input=json.dumps(data) if data is not None else None,
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()[-300:]}")
    return json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout.strip()


def repo():
    return os.environ.get("GITHUB_REPOSITORY") or gh("repo", "view", "--json", "nameWithOwner",
                                                     "-q", ".nameWithOwner")


def ensure_branch(rp):
    try:
        gh("api", f"repos/{rp}/git/ref/heads/{BRANCH}")
        return
    except RuntimeError:
        pass
    readme = "Cover images for the okst-highlights issue log. Written by cloud/post_issues.py.\n"
    blob = gh("api", f"repos/{rp}/git/blobs", "--input", "-", data={"content": readme, "encoding": "utf-8"})
    tree = gh("api", f"repos/{rp}/git/trees", "--input", "-",
              data={"tree": [{"path": "README.md", "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
    commit = gh("api", f"repos/{rp}/git/commits", "--input", "-",
                data={"message": "covers branch for issue images", "tree": tree["sha"], "parents": []})
    gh("api", f"repos/{rp}/git/refs", "--input", "-", data={"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    print(f"created orphan branch {BRANCH}")


def upload(rp, path, prefix):
    im = Image.open(path).convert("RGB")
    if im.width > 540:
        im = im.resize((540, round(im.height * 540 / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    dest = f"{prefix}/{Path(path).stem}.jpg"
    body = {"message": f"issue image {dest}", "branch": BRANCH,
            "content": base64.b64encode(buf.getvalue()).decode()}
    try:
        cur = gh("api", f"repos/{rp}/contents/{dest}?ref={BRANCH}")
        body["sha"] = cur["sha"]
    except RuntimeError:
        pass
    gh("api", "-X", "PUT", f"repos/{rp}/contents/{dest}", "--input", "-", data=body)
    return f"https://raw.githubusercontent.com/{rp}/{BRANCH}/{dest}"


def open_issue(title, body, images, prefix, labels=()):
    rp = repo()
    if images:
        try:
            ensure_branch(rp)
        except RuntimeError as e:
            print(f"covers branch unavailable: {e}")
    lines = []
    for img in images:
        try:
            lines.append(f"![{Path(img).name}]({upload(rp, img, prefix)})")
        except Exception as e:                      # never lose the log over an image
            lines.append(f"(image {Path(img).name} not uploaded: {str(e)[:120]})")
    full = body.rstrip() + ("\n\n" + "\n".join(lines) if lines else "") + "\n"
    tmp = Path(os.environ.get("RUNNER_TEMP", ".")) / f"issue-{abs(hash(title))}.md"
    tmp.write_text(full, encoding="utf-8")
    args = ["issue", "create", "--repo", rp, "--title", title[:250], "--body-file", str(tmp)]
    for lb in labels:
        args += ["--label", lb]
    try:
        url = gh(*args)
    except RuntimeError:
        url = gh(*args[:8])                           # a missing label must not block the log
    print(f"ISSUE {url}")
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title")
    ap.add_argument("--body-file")
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--prefix", default="misc")
    ap.add_argument("--dir", help="folder of issue json files")
    a = ap.parse_args()
    if a.dir:
        files = sorted(Path(a.dir).glob("*.json"))
        for f in files:
            spec = json.loads(f.read_text(encoding="utf-8"))
            base = f.parent
            imgs = [str(p if Path(p).is_absolute() else base / p) for p in spec.get("images", [])]
            open_issue(spec["title"], spec.get("body", ""), [i for i in imgs if Path(i).exists()],
                       spec.get("prefix", a.prefix), spec.get("labels", []))
        if not files:
            print("no issues to open")
    elif a.title:
        body = Path(a.body_file).read_text(encoding="utf-8") if a.body_file else ""
        open_issue(a.title, body, [i for i in a.image if Path(i).exists()], a.prefix)
    else:
        sys.exit("give --title or --dir")


if __name__ == "__main__":
    main()
