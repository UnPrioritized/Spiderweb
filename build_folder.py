"""Builds the folder version of Spiderweb (Windows, 64-bit): Spiderweb.exe (the starter, native/starter) + data/<version>/
holding Python, the packages, the program as .pyc files, icons, lang, clips and the DLLs. Nothing is unpacked when it
starts. Also data/version.txt and data/<version>/checksum.txt (size, SHA-256 and place of every file: the starter checks
sizes at each start, Help -> About's "Check program files" the fingerprints; the list's own SHA-256 is baked into
the starter), and Spiderweb-<version>.zip.
  python build_folder.py [--out DIR]   (default: dist)
Uses the Python running it (its version, its installed packages) and needs Rust + the Windows SDK for the starter."""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from importlib import metadata
from importlib._bootstrap_external import _code_to_hash_pyc

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.base_prefix
SITE = os.path.join(PY, "Lib", "site-packages")
TAG = f"python{sys.version_info[0]}{sys.version_info[1]}"  # (python314)
# The packages inside (pip name -> the folders / files it installed that are needed)
PACKAGES = {"numpy": ["numpy", "numpy.libs"], "pillow": ["PIL"], "uharfbuzz": ["uharfbuzz"],
            "python-bidi": ["bidi"], "pynacl": ["nacl"], "cffi": [f"_cffi_backend.cp{TAG[6:]}-win_amd64.pyd"]}
OWN = ["files", "notes", "roll", "window"]  # (the program's code folders in scripts/)
SKIP_LIB = {"site-packages", "test", "idlelib", "turtledemo", "ensurepip", "venv", "pydoc_data", "__pycache__",
            "__phello__", "lib2to3"}
SKIP_DLLS = ("_test", "_ctypes_test", "py.ico", "pyc.ico", "pyd.ico", "python_lib.cat", "sqlite3.dll", "_sqlite3")
SKIP_DIRS = {"__pycache__", "tests", "testing", "include", "f2py"}  # (f2py: NumPy's Fortran compiler tool)
SKIP_ENDS = (".pyi", ".pyc", ".lib", ".h", ".md")  # (.md: the CLAUDE.md notes never go out)
# Parts of packages Spiderweb never uses (2026-11-26: no AVIF pictures, no Pillow font drawing): ~10 MB
SKIP_STARTS = ("_avif.", "_imagingft.")  # (Pillow's own code expects these may be missing)
# Beside Spiderweb.exe in the zip (README.txt + the licences)
TOP_FILES = ("LICENSE", "README.txt", "THIRD-PARTY-NOTICES.txt", "SPIDERWEB-NOTES-FORMAT.md")


def pyc(src, shown):
    """A .pyc made in memory (unchecked hash: never compared with a source, which isn't there)."""
    with open(src, "rb") as f:
        raw = f.read()
    code = compile(raw, shown, "exec", dont_inherit=True)
    return bytes(_code_to_hash_pyc(code, importlib.util.source_hash(raw), checked=False))


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def copy_code(src, dst):
    """A folder with every .py made a .pyc beside it (no source: nothing compiled or written when it runs)."""
    for d, dirs, names in os.walk(src):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        rel = os.path.relpath(d, src)
        for n in names:
            s = os.path.join(d, n)
            if n.startswith(SKIP_STARTS):
                continue
            if n.endswith(".py"):
                put(os.path.join(dst, rel, n[:-3] + ".pyc"), pyc(s, os.path.join(os.path.basename(src), rel, n)))
            elif not n.endswith(SKIP_ENDS):
                os.makedirs(os.path.join(dst, rel), exist_ok=True)
                shutil.copy2(s, os.path.join(dst, rel, n))


def python_itself(data):
    """Python's DLLs, its library as .pyc in one stored zip, Tcl / Tk, and the ._pth naming Python's paths."""
    for n in (f"{TAG}.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        shutil.copy2(os.path.join(PY, n), os.path.join(data, n))
    shutil.copy2(os.path.join(PY, "LICENSE.txt"), os.path.join(data, "PYTHON-LICENSE.txt"))
    os.makedirs(os.path.join(data, "DLLs"))
    for n in os.listdir(os.path.join(PY, "DLLs")):
        if not n.startswith(SKIP_DLLS):
            shutil.copy2(os.path.join(PY, "DLLs", n), os.path.join(data, "DLLs", n))
    shutil.copytree(os.path.join(PY, "tcl"), os.path.join(data, "tcl"),
                    ignore=shutil.ignore_patterns("*.lib", "*.sh", "nmake"))
    import zipfile
    lib = os.path.join(PY, "Lib")
    with zipfile.ZipFile(os.path.join(data, f"{TAG}.zip"), "w", zipfile.ZIP_STORED) as z:  # (read as it is)
        for d, dirs, names in os.walk(lib):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_LIB)
            for n in sorted(names):
                if n.endswith(".py"):
                    rel = os.path.relpath(os.path.join(d, n), lib)
                    z.writestr(rel[:-3].replace(os.sep, "/") + ".pyc", pyc(os.path.join(d, n), rel))
    with open(os.path.join(data, f"{TAG}._pth"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{TAG}.zip\nDLLs\n.\npackages\n")  # (no "import site": nothing from outside is added)


def packages(data):
    pk = os.path.join(data, "packages")
    os.makedirs(pk)
    for pip, parts in PACKAGES.items():
        for part in parts:
            s = os.path.join(SITE, part)
            if os.path.isdir(s):
                copy_code(s, os.path.join(pk, part))
            else:
                shutil.copy2(s, os.path.join(pk, part))
        info = metadata.distribution(pip)._path  # (its .dist-info: notes.shaping asks the versions)
        shutil.copytree(info, os.path.join(pk, os.path.basename(info)))


def program(data):
    for name in OWN:
        copy_code(os.path.join(SCRIPTS, name), os.path.join(data, name))
    put(os.path.join(data, "spiderweb.pyc"), pyc(os.path.join(ROOT, "spiderweb.py"), "spiderweb.py"))
    put(os.path.join(data, "boot.pyc"), pyc(os.path.join(SCRIPTS, "boot.py"), "boot.py"))
    for sub in ("icons", "lang"):
        shutil.copytree(os.path.join(SCRIPTS, sub), os.path.join(data, sub), ignore=shutil.ignore_patterns("*.md"))
    shutil.copytree(os.path.join(ROOT, "clips"), os.path.join(data, "clips"))
    for sub in ("bass", "kestrel", "loops"):
        x64 = os.path.join(SCRIPTS, sub, "x64")
        if os.path.isdir(x64):
            shutil.copytree(x64, os.path.join(data, sub, "x64"))
        elif sub == "kestrel":
            print("No scripts/kestrel/x64: building without Kestrel (GPU) playback.")
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(data, "LICENSE"))


def file_list(data):
    """checksum.txt: "<size>\\t<sha256>\\t<place>" for every file in data/<version> (not itself). Returns how many
    files and the list's own SHA-256 (baked into the starter)."""
    lines = []
    for d, _, names in os.walk(data):
        for n in names:
            p = os.path.join(d, n)
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            lines.append(f"{os.path.getsize(p)}\t{h.hexdigest()}\t{os.path.relpath(p, data)}")
    text = ("\n".join(sorted(lines)) + "\n").encode("utf-8")
    with open(os.path.join(data, "checksum.txt"), "wb") as f:
        f.write(text)
    return len(lines), hashlib.sha256(text).hexdigest()


def main():
    out = os.path.abspath(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(ROOT, "dist"))
    if sys.platform != "win32" or sys.maxsize < 2 ** 32:
        sys.exit("The folder version is built on 64-bit Windows.")
    started = time.time()
    subprocess.check_call([sys.executable, os.path.join(ROOT, "build_version.py")], cwd=ROOT)
    stamp = os.path.join(SCRIPTS, "files", "build_stamp.py")
    sys.path.insert(0, SCRIPTS)
    from files.about import VERSION
    app = os.path.join(out, "Spiderweb")
    shutil.rmtree(app, ignore_errors=True)
    data = os.path.join(app, "data", VERSION)
    os.makedirs(data)
    try:
        python_itself(data)
        packages(data)
        program(data)
    finally:
        if os.path.exists(stamp):
            os.remove(stamp)  # (the build stamp was only for this build)
    count, list_sha = file_list(data)  # (first: the starter is built with the list's own SHA-256)
    if subprocess.call([sys.executable, os.path.join(ROOT, "native", "starter", "build_release.py")],
                       env=dict(os.environ, SPIDERWEB_LIST_SHA256=list_sha)):
        sys.exit("The starter didn't build.")
    shutil.copy2(os.path.join(ROOT, "build", "starter", "Spiderweb.exe"), os.path.join(app, "Spiderweb.exe"))
    with open(os.path.join(app, "data", "version.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(VERSION + "\n")
    for n in TOP_FILES:
        shutil.copy2(os.path.join(ROOT, n), os.path.join(app, n))
    z = os.path.join(out, f"Spiderweb-{VERSION}.zip")
    if os.path.exists(z):
        os.remove(z)
    tar = os.path.join(os.environ["SystemRoot"], "System32", "tar.exe")  # (Windows' own: Git Bash's makes bad zips)
    subprocess.check_call([tar, "-a", "-cf", z, "Spiderweb"], cwd=out)
    size = sum(os.path.getsize(os.path.join(d, n)) for d, _, ns in os.walk(app) for n in ns)
    print(f"Built {app}: {count} files, {size / 1e6:.1f} MB; {os.path.basename(z)} {os.path.getsize(z) / 1e6:.1f} MB;"
          f" {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
