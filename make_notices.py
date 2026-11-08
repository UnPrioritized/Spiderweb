"""Writes THIRD-PARTY-NOTICES.txt: the licences of everything Spiderweb.exe carries inside it besides Spiderweb itself
(Python, Tcl/Tk, NumPy, Pillow, PyNaCl + cffi + pycparser, PyInstaller's starter), read from the copies installed here
so the notices match the versions built in, then the own compiled loops' Rust crates (native/loops/NOTICES.txt) and
the Kestrel connector's (native/kestrel_bridge/NOTICES.txt), each written by its own make_notices.py. build.bat runs
it before building."""

import glob
import importlib.metadata as meta
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "THIRD-PARTY-NOTICES.txt")
LINE = "=" * 78

HEAD = """Spiderweb.exe carries these programs and libraries inside it. Their licences ask for their
notices to come with it, so here they are. Spiderweb's own licence is in LICENSE.

The Hz bass preview and the built-in synth use BASS, BASSMIDI and BASSmix by Un4seen Developments
(un4seen.com). They aren't covered by these licences or by Spiderweb's: they are free for
non-commercial use only (see README.txt).

The own compiled loops (scripts/loops, also inside the .exe) are built with Rust crates (rayon and
its helpers): their licences come after the packages below. Kestrel (GPU) playback uses the Kestrel
connector (scripts/kestrel, also inside the .exe): its licences, Kestrel's own among them, are at the
end of this file.
"""


def section(title, *texts):
    body = "\n\n".join(t.strip("\n") for t in texts if t)
    return f"\n\n{LINE}\n{title}\n{LINE}\n\n{body}\n"


def package(name, title):
    dist = meta.distribution(name)
    texts = []
    for f in sorted(dist.files or [], key=str):
        s = str(f).replace("\\", "/")
        if ".dist-info/" in s and any(w in s.upper() for w in ("LICEN", "COPYING")):
            path = f.locate()
            rel = s.split("/licenses/", 1)[-1]
            texts.append(f"--- {rel} ---\n\n" + open(path, encoding="utf-8", errors="replace").read())
    return section(f"{title} {dist.version}", *texts)


def tcl_tk():
    texts = []
    for z in sorted(glob.glob(os.path.join(sys.base_prefix, "tcl", "lib*.zip"))):
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if n.lower().endswith("license.terms"):
                    texts.append(f"--- {n.split('/')[0]} ---\n\n" + zf.read(n).decode("utf-8", "replace"))
    return section("Tcl/Tk", *texts)


def main():
    python = open(os.path.join(sys.base_prefix, "LICENSE.txt"), encoding="utf-8").read()
    parts = [HEAD,
             section(f"Python {sys.version.split()[0]}", python),
             tcl_tk(),
             package("numpy", "NumPy"),
             package("pillow", "Pillow"),
             package("pynacl", "PyNaCl (with libsodium; checks update signatures)"),
             package("cffi", "cffi (used by PyNaCl)"),
             package("pycparser", "pycparser (used by cffi)"),
             package("pyinstaller", "PyInstaller (its starter program runs Spiderweb.exe)")]
    for own in (("loops", "NOTICES.txt"), ("kestrel_bridge", "NOTICES.txt")):
        path = os.path.join(HERE, "native", *own)
        if os.path.isfile(path):
            parts.append(f"\n\n{LINE}\n{LINE}\n\n" + open(path, encoding="utf-8").read())
    text = "".join(parts).replace("\r\n", "\n")
    with open(OUT + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(OUT + ".tmp", OUT)
    print(f"{OUT}: {len(text):,} characters")


if __name__ == "__main__":
    main()
