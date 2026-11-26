"""Builds the folder version's Spiderweb.exe (the starter) -> build/starter/Spiderweb.exe, with the icon, version info
(from scripts/files/about.py) and starter.manifest. Windows only: needs Rust (https://rustup.rs) and the Windows SDK's
rc.exe (comes with Visual Studio's C++ tools). No crates are downloaded.

Rust keeps source file paths inside the built file; plain builds would carry the builder's home folder (and so
their user name). This swaps them for short neutral names, as native/loops/build_release.py does.
Run: python native/starter/build_release.py"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "scripts")
OUT = os.path.join(ROOT, "build", "starter")


def forms(path):
    path = os.path.normpath(path)
    return [path] + ([path.replace("\\", "/")] if os.sep == "\\" else [])


def rc_exe():
    kits = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Windows Kits", "10", "bin")
    found = sorted(os.path.join(d, n) for d, _, names in os.walk(kits) for n in names
                   if n.lower() == "rc.exe" and os.path.basename(d).lower() == "x64")
    if not found:
        sys.exit("No rc.exe: install Visual Studio's C++ tools (Windows SDK).")
    return found[-1]


def resources(version, out=OUT):
    """The .res holding the icon, version info and manifest (the mover's build uses it too, own out folder)."""
    nums = [int(n) for n in re.match(r"[\d.]+", version).group().strip(".").split(".")][:4]
    nums += [0] * (4 - len(nums))
    commas = ",".join(map(str, nums))
    os.makedirs(out, exist_ok=True)
    rc = os.path.join(out, "starter.rc")
    esc = lambda p: p.replace("\\", "\\\\")  # noqa: E731
    with open(rc, "w", encoding="utf-8") as f:
        f.write(f'''#pragma code_page(65001)
1 ICON "{esc(os.path.join(SCRIPTS, "icons", "icon.ico"))}"
1 24 "{esc(os.path.join(HERE, "starter.manifest"))}"
1 VERSIONINFO
FILEVERSION {commas}
PRODUCTVERSION {commas}
FILEOS 0x40004
FILETYPE 0x1
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904B0"
    BEGIN
      VALUE "ProductName", "Spiderweb"
      VALUE "FileDescription", "Spiderweb - draw lines and shapes into MIDI notes"
      VALUE "FileVersion", "{version}"
      VALUE "ProductVersion", "{version}"
      VALUE "LegalCopyright", "Copyright (c) 2026 Kanade Tachibana, MIT License"
      VALUE "OriginalFilename", "Spiderweb.exe"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
''')
    res = os.path.join(out, "starter.res")
    subprocess.check_call([rc_exe(), "/nologo", "/fo", res, rc])
    return res


def main():
    sys.path.insert(0, SCRIPTS)
    from files.about import VERSION
    home = os.path.expanduser("~")
    cargo = os.environ.get("CARGO_HOME") or os.path.join(home, ".cargo")
    above_repo = os.path.dirname(os.path.dirname(ROOT))
    flags = []
    for path, name in ((home, "home"), (above_repo, "src"), (cargo, "cargo")):  # (rustc: the LAST match wins)
        flags += ["--remap-path-prefix=%s=%s" % (p, name) for p in forms(path)]
    env = dict(os.environ, SPIDERWEB_RES=resources(VERSION), SPIDERWEB_VERSION=VERSION,
               CARGO_ENCODED_RUSTFLAGS="\x1f".join(flags))
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
