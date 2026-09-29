"""Looking on GitHub for a newer Spiderweb: the list of releases (tags like v1.3.0; drafts and pre-releases are
skipped, so test builds can go online without anyone being told). Only the release list is asked for; nothing is
sent and nothing is downloaded. The window side (asking, when to check, the popup) is window/updates.py."""

import json
import re
import urllib.request

from files.about import VERSION

RELEASES = "https://api.github.com/repos/UnPrioritized/Spiderweb/releases"
TIMEOUT = 8  # seconds before giving up (no connection)


def version_tuple(text):
    """"v1.3.0" / "1.3" -> (1, 3, 0); None if it isn't a version number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(text).strip())
    if not m:
        return None
    parts = [int(p) for p in m.group(1).split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def newer_releases():
    """The releases newer than this one, newest first: [{"version", "url", "notes"}]. Raises OSError / ValueError when
    GitHub can't be reached or answers oddly."""
    req = urllib.request.Request(RELEASES + "?per_page=30", headers={
        "User-Agent": "Spiderweb/" + VERSION, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("unexpected answer")
    mine = version_tuple(VERSION)
    found = []
    for rel in data:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        v = version_tuple(rel.get("tag_name", ""))
        if v is None or v <= mine:
            continue
        found.append((v, {"version": ".".join(map(str, v)), "url": str(rel.get("html_url") or ""),
                          "notes": plain_notes(rel.get("body") or "")}))
    found.sort(key=lambda f: f[0], reverse=True)
    return [f[1] for f in found]


def plain_notes(body):
    """A release's notes (written in GitHub's markdown) as plain text: no ** / ` / # marks, "- " / "* " lines as
    bullets, and the download instructions at the end (from a "Download:" line on) left out."""
    lines = []
    for line in body.replace("\r\n", "\n").split("\n"):
        bare = re.sub(r"^\s*[-*]\s+", "- ", line)
        bare = re.sub(r"\*\*|`", "", bare).strip()
        if bare.lower().startswith("download:"):
            break
        if re.match(r"#+\s", bare):  # a heading ("What's new" is already the popup's own heading)
            if re.sub(r"^#+\s*", "", bare).lower().rstrip(":") in ("what's new", "what’s new"):
                continue
            bare = re.sub(r"^#+\s*", "", bare)
        bare = re.sub(r"^- ", "•  ", bare)
        lines.append(bare)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
