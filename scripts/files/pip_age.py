"""Versions under 14 days old are never installed by Spiderweb's install offers (spiderweb.py run_pip; user
2026-11-18, against poisoned uploads: those are usually found and pulled within days). Asks PyPI when each version
came out and adds "!=" for the new ones to pip's request, so pip still picks the newest one that suits this Python.
PyPI unreachable (offline, a mirror only): the request stays as it was. Plain Python only (runs before NumPy)."""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

DAYS = 14
TIMEOUT = 15  # s for each PyPI question
DEPS = {"numba": ("llvmlite", "numpy")}  # (packages pip brings along: the same rule for them)


def _fetch(name):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=TIMEOUT) as r:
        return json.load(r)


def too_new(name, now=None, fetch=None):
    """Versions of name whose last file was uploaded under DAYS days ago (sorted), or None when PyPI can't say."""
    now = now or datetime.now(timezone.utc)
    try:
        releases = (fetch or _fetch)(name)["releases"]
        out = []
        for v, files in releases.items():
            times = [datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00")) for f in files]
            if times and now - max(times) < timedelta(days=DAYS):
                out.append(v)
        return sorted(out)
    except Exception:  # noqa: BLE001 (offline, a mirror, a changed page: no dates = as before)
        return None


def _with_out(req, versions):
    """req ("numpy", "uharfbuzz>=0.57") with "!=" for each of versions added."""
    if not versions:
        return req
    outs = ",".join("!=" + v for v in versions)
    return req + ("," if re.search(r"[<>=!~]", req) else "") + outs


def requests(pip, base, now=None, fetch=None):
    """pip's install arguments for pip (base = its usual request): base without the new versions, plus its DEPS
    likewise (only those with new versions)."""
    out = [_with_out(base, too_new(pip, now, fetch))]
    for dep in DEPS.get(pip, ()):
        new = too_new(dep, now, fetch)
        if new:
            out.append(_with_out(dep, new))
    return out
