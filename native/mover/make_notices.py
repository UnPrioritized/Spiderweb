"""Writes NOTICES.txt: the licences of the Rust crates the mover (the small Spiderweb.exe on the release page) is built
from. Read from the crates cargo downloaded, so the notices match the versions built in. Needs cargo; run it after a
crate changes. Spiderweb's own make_notices.py adds NOTICES.txt to THIRD-PARTY-NOTICES.txt."""

import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "NOTICES.txt")
LINE = "=" * 78


def cargo(*args):
    return subprocess.run(["cargo", *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip("\n")


def licence_files(folder, expr):
    """The crate's licence / notice files. With a choice of licences ("MIT OR Apache-2.0") only MIT is kept."""
    files = sorted(n for n in os.listdir(folder) if re.match(r"(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)", n, re.I)
                   and os.path.isfile(os.path.join(folder, n)))
    if " OR " in expr or "/" in expr:
        mit = [n for n in files if "MIT" in n.upper()]
        if mit:
            files = mit + [n for n in files if "NOTICE" in n.upper() or "COPYRIGHT" in n.upper()]
    return [os.path.join(folder, n) for n in files]


def main():
    meta = json.loads(cargo("metadata", "--format-version", "1"))
    built = set()
    for line in cargo("tree", "-e", "normal", "--prefix", "none", "--target", "x86_64-pc-windows-msvc").splitlines():
        m = re.match(r"(\S+) v(\S+)", line)
        if m and m.group(1) != "spiderweb_mover":
            built.add((m.group(1), m.group(2)))
    pkgs = {(p["name"], p["version"]): p for p in meta["packages"]}
    parts = []
    for name, ver in sorted(built):
        p = pkgs[(name, ver)]
        expr = p.get("license") or "see its files"
        body = "\n\n".join(read(f) for f in licence_files(os.path.dirname(p["manifest_path"]), expr)) or \
            f"(no licence file shipped; declared: {expr})"
        parts.append(f"\n\n{LINE}\n{name}\n{LINE}\n\n{name} {ver} ({expr})\n\n{body}\n")
    head = ("The mover (the small Spiderweb.exe on each release page that sets up the folder version; source in\n"
            f"native/mover) is built with the Rust crates below ({len(parts)} crates). Their licences ask for their\n"
            "notices to come with it, so here they are.\n")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "".join(parts))
    print(f"Wrote {OUT}: {len(parts)} crates")


if __name__ == "__main__":
    main()
