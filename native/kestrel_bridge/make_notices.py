"""Writes NOTICES.txt: the licences of everything the Kestrel connector (kestrel_bridge.dll / libkestrel_bridge.so)
carries inside it: Kestrel itself, the code Kestrel took from others, the Rust crates it is built from, and the
shader compiler (DXC) linked into the Windows file. Read from the crates cargo downloaded, so the notices match the
versions built in. Needs cargo; run it after every rebuild against a new Kestrel or new crates. Spiderweb's own
make_notices.py adds NOTICES.txt to THIRD-PARTY-NOTICES.txt."""

import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "NOTICES.txt")
LINE = "=" * 78
TARGETS = ("x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu")
OURS = ("kestrel_bridge", "kestrel")
# Where our copy of Kestrel's source is public (MPL-2.0 asks for it). Set when the fork is up.
KESTREL_SOURCE = "https://github.com/UnPrioritized/kestrel-midi (branch \"spiderweb\")"


def cargo(*args):
    return subprocess.run(["cargo", *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def built_in():
    """(name, version) of every crate linked into the connector on Windows or Linux (no build-only crates)."""
    found = set()
    for t in TARGETS:
        for line in cargo("tree", "-e", "normal", "--prefix", "none", "--target", t).splitlines():
            m = re.match(r"(\S+) v(\S+)", line)
            if m:
                found.add((m.group(1), m.group(2)))
    return found


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip("\n")


def licence_files(folder, expr):
    """The crate's licence / notice files. With a choice of licences ("MIT OR Apache-2.0") only MIT is kept."""
    files = sorted(n for n in os.listdir(folder)
                   if re.match(r"(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT)", n, re.I) and os.path.isfile(
                       os.path.join(folder, n)))
    if " OR " in expr or "/" in expr:
        mit = [n for n in files if "MIT" in n.upper()]
        if mit:
            files = mit + [n for n in files if "NOTICE" in n.upper() or "COPYRIGHT" in n.upper()]
    return [os.path.join(folder, n) for n in files]


def section(title, *texts):
    body = "\n\n".join(t.strip("\n") for t in texts if t)
    return f"\n\n{LINE}\n{title}\n{LINE}\n\n{body}\n"


def main():
    meta = json.loads(cargo("metadata", "--format-version", "1"))
    pkgs = {(p["name"], p["version"]): p for p in meta["packages"]}
    kestrel = next(p for p in meta["packages"] if p["name"] == "kestrel")
    kdir = os.path.dirname(kestrel["manifest_path"])
    parts = [section(
        f"Kestrel {kestrel['version']} (the GPU synth behind Kestrel (GPU) playback)",
        "By thtstickyboi (github.com/thtstickyboi/kestrel-midi), with the live playing interface by mbms\n"
        "(mbms0, github.com/mbms0/kestrel-midi). Mozilla Public License 2.0. Spiderweb's copy has small\n"
        f"changes of its own; its source: {KESTREL_SOURCE}.",
        read(os.path.join(kdir, "LICENSE"))),
        section("Code inside Kestrel from others",
                "Analytic phase rotation (src/phase.rs, shaders/phase.wgsl): adapted from SYNCore by DixelU\n"
                "(SAFSYN/phase.cpp, phase.h), released under the Unlicense:",
                read(os.path.join(kdir, "third_party", "SYNCore-LICENSE.txt")),
                "The \"omni\" limiter (src/limiter.rs): a port of OmniConverter's Limiter.cs, itself from Kiva\n"
                "by Arduano (github.com/arduano/Kiva), (C) 2020 Arduano, under the DON'T BE A DICK PUBLIC\n"
                "LICENSE, Version 1.1. This is its credit."),
        section("DirectX Shader Compiler (DXC), linked into the Windows file",
                "Microsoft's DirectXShaderCompiler (github.com/microsoft/DirectXShaderCompiler), built as a\n"
                "static library by hexops' mach-dxcompiler (github.com/hexops/mach-dxcompiler).",
                read(os.path.join(HERE, "licences", "DXC-LICENSE.TXT")),
                "--- mach-dxcompiler ---",
                read(os.path.join(HERE, "licences", "mach-dxcompiler-LICENSE")))]
    # Crates sharing the very same licence text are listed together under it, so each text shows once.
    texts = {}
    for name, ver in sorted(built_in()):
        if name in OURS:
            continue
        p = pkgs[(name, ver)]
        expr = p.get("license") or "see its files"
        files = licence_files(os.path.dirname(p["manifest_path"]), expr)
        body = "\n\n".join(read(f) for f in files) or f"(no licence file shipped; declared: {expr})"
        texts.setdefault(body, []).append(f"{name} {ver} ({expr})")
    crates = [section(", ".join(n.split(" (")[0] for n in names), "\n".join(names), body)
              for body, names in sorted(texts.items(), key=lambda kv: kv[1][0].lower())]
    head = ("The Kestrel connector (scripts/kestrel, also inside Spiderweb.exe) carries the programs and\n"
            f"libraries below inside it ({sum(len(n) for n in texts.values())} Rust crates besides Kestrel). "
            "Their licences ask for\ntheir notices to come with it, so here they are.\n")
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "".join(parts) + "".join(crates))
    print(f"Wrote {OUT}: {len(texts)} licence texts")


if __name__ == "__main__":
    main()
