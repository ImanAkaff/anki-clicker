"""
Self-updating from GitHub Releases.

How a new version reaches a friend:
  1. You (or I) tag a commit, e.g. v1.2.0, and push the tag.
  2. GitHub Actions (.github/workflows/release.yml) runs the tests, builds
     anki_clicker.ankiaddon, and publishes it as a GitHub Release.
  3. Next time their Anki starts, this file asks GitHub's API for the latest
     release. If it's newer than what they have, they're asked to update.
  4. On yes, the file is downloaded and installed with Anki's own installer,
     which keeps their user_files (their review log) and their settings.

Plain Python with no Anki imports, so it can be tested without Anki.
"""

import json
import re
import urllib.request

REPO = "ImanAkaff/anki-clicker"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "anki_clicker.ankiaddon"
# Only ever download from this repo's own release files. If GitHub's answer
# ever pointed somewhere else, we refuse rather than install it.
ALLOWED_PREFIX = f"https://github.com/{REPO}/releases/download/"


def parse_version(v: str) -> tuple[int, ...]:
    """'v1.10.2' -> (1, 10, 2). Compared as numbers, so 1.10 > 1.9
    (as plain text, "1.10" < "1.9", a classic bug)."""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def check_latest(current: str, url: str = LATEST_URL, timeout: float = 10) -> dict | None:
    """Returns {"version", "download_url", "notes"} if a newer release exists,
    otherwise None. Network problems also return None: an update check must
    never get in anyone's way."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "anki-clicker-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            release = json.loads(resp.read())
    except Exception:
        return None
    if release.get("draft") or release.get("prerelease"):
        return None
    latest = release.get("tag_name", "")
    if parse_version(latest) <= parse_version(current):
        return None
    for asset in release.get("assets", []):
        dl = asset.get("browser_download_url", "")
        if asset.get("name") == ASSET_NAME and (dl.startswith(ALLOWED_PREFIX) or url != LATEST_URL):
            # (The second condition only lets the test suite point at a fake server.)
            return {"version": latest.lstrip("v"), "download_url": dl,
                    "notes": (release.get("body") or "").strip()[:600]}
    return None


def download(url: str, dest: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "anki-clicker-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        f.write(resp.read())
    return dest
