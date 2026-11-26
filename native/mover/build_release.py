"""Builds the mover (the small Spiderweb.exe on the release page: native/mover) -> build/mover/Spiderweb.exe, with the
icon, version info and manifest (native/starter/build_release.py's), the public key from scripts/files/signing.py
and every language's mover texts built in. Windows only: Rust + the Windows SDK's rc.exe. Crates: Cargo.lock,
--locked (versions only by the 14-day rule: dev/harness/safe_crates.py). User paths taken out like the loops'.
Run: python native/mover/build_release.py"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "scripts")
OUT = os.path.join(ROOT, "build", "mover")
KEYS = ("updates.connecting", "updates.stopping")  # (besides every mover.* and updates.fail.* text)
sys.path.insert(0, os.path.join(ROOT, "native", "starter"))
import build_release as starter  # noqa: E402


def texts():
    """Every language's mover texts as "<code>\\t<key>\\t<text>" lines (\\n \\t \\\\ escaped)."""
    lines = []
    lang = os.path.join(SCRIPTS, "lang")
    for code in sorted(os.listdir(lang)):
        folder = os.path.join(lang, code)
        if not os.path.isdir(folder) or code.startswith("_"):
            continue
        got = {}
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    got.update(json.load(f))
        for k, v in sorted(got.items()):
            if isinstance(v, str) and (k.startswith(("mover.", "updates.fail.")) or k in KEYS):
                v = v.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
                lines.append(f"{code}\t{k}\t{v}")
    path = os.path.join(OUT, "texts.txt")
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    sys.path.insert(0, SCRIPTS)
    from files.about import VERSION
    with open(os.path.join(SCRIPTS, "files", "signing.py"), encoding="utf-8") as f:
        key = re.search(r'^PUBLIC_KEY = "([^"]*)"', f.read(), re.M).group(1)
    if not key:
        sys.exit("No public key in scripts/files/signing.py.")
    home = os.path.expanduser("~")
    cargo = os.environ.get("CARGO_HOME") or os.path.join(home, ".cargo")
    above_repo = os.path.dirname(os.path.dirname(ROOT))
    flags = []
    for path, name in ((home, "home"), (above_repo, "src"), (cargo, "cargo")):  # (rustc: the LAST match wins)
        flags += ["--remap-path-prefix=%s=%s" % (p, name) for p in starter.forms(path)]
    env = dict(os.environ, SPIDERWEB_RES=starter.resources(VERSION, OUT), SPIDERWEB_VERSION=VERSION,
               SPIDERWEB_PUBLIC_KEY=key, SPIDERWEB_TEXTS=texts(), CARGO_ENCODED_RUSTFLAGS="\x1f".join(flags))
    env.pop("RUSTFLAGS", None)
    code = subprocess.call(["cargo", "build", "--release", "--locked"], cwd=HERE, env=env)
    if code:
        return code
    place = os.path.join(OUT, "Spiderweb.exe")
    shutil.copyfile(os.path.join(HERE, "target", "release", "Spiderweb.exe"), place)
    print("Built", os.path.relpath(place, ROOT), os.path.getsize(place), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
