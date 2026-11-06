"""Used by build.bat: writes build/version.txt, the version info PyInstaller puts into Spiderweb.exe (what Windows
shows under Properties -> Details), from the version in scripts/files/about.py."""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "scripts"))

from files.about import VERSION  # noqa: E402 (needs the path above)

numbers = [int(n) for n in re.match(r"[\d.]+", VERSION).group().strip(".").split(".")][:4]
numbers += [0] * (4 - len(numbers))
ver = tuple(numbers)
text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={ver}, prodvers={ver}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Spiderweb'),
      StringStruct('FileDescription', 'Spiderweb - draw lines and shapes into MIDI notes'),
      StringStruct('FileVersion', {VERSION!r}),
      StringStruct('ProductVersion', {VERSION!r}),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Kanade Tachibana, MIT License'),
      StringStruct('OriginalFilename', 'Spiderweb.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
os.makedirs(os.path.join(HERE, "build"), exist_ok=True)
with open(os.path.join(HERE, "build", "version.txt"), "w", encoding="utf-8") as f:
    f.write(text)
print("Version", VERSION)


def git(*args):
    import subprocess
    try:
        return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# The build stamp (files/about.py BUILD): the commit and the day it was built, "+" when files were changed since that
# commit. build.bat deletes it again after packing, so running from the source folder shows the plain version.
import datetime  # noqa: E402

commit = git("rev-parse", "--short", "HEAD") or "unknown"
if git("status", "--porcelain", "--untracked-files=no"):
    commit += "+"
stamp = f"{commit}, {datetime.date.today().isoformat()}"
# The commit's own time (What's new shows it under this version's heading, as the older versions' lines do)
when = git("log", "-1", "--format=%cd", "--date=format:%Y/%m/%d %H:%M:%S %z")
if when:
    day, clock, zone = when.split()
    when = f"{day} {clock} UTC{zone[0]}{zone[1:3]}:{zone[3:]} · {commit}"
with open(os.path.join(HERE, "scripts", "files", "build_stamp.py"), "w", encoding="utf-8", newline="\n") as f:
    f.write(f'"""Written by build_version.py for one build (deleted again after it)."""\n\nBUILD = {stamp!r}\n'
            f'BUILT = {when!r}\n')
# (plain letters only: a Japanese / Chinese Windows console can't print the "·" and the build stopped there)
print("Build", stamp, "|", when.encode("ascii", "replace").decode("ascii"))
