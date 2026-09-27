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
