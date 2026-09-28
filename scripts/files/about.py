"""The program's version and its folder (where autosave, shapes, output and errors.log go)."""

import os
import sys

VERSION = "1.2.0"
WEBSITE = "https://github.com/UnPrioritized/Spiderweb"  # new versions, the source code, problem reports

# The spiderweb folder (scripts/<group>/ is two levels down); the .exe build unpacks to a temp folder, so there it's
# the folder the .exe is in instead.
HERE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# The window icons (made by make_icon.py); the .exe carries them inside, unpacked with the program
ICONS = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "icons")
# The "Spiderweb" picture on the About page (written with the Text tool); -half = the same smoothly shrunk to half
BANNER = os.path.join(ICONS, "spiderweb.png")
BANNER_HALF = os.path.join(ICONS, "spiderweb-half.png")
# The license: the copy next to Spiderweb, else the one the .exe carries inside
LICENSE = os.path.join(HERE, "LICENSE")
if not os.path.exists(LICENSE) and hasattr(sys, "_MEIPASS"):
    LICENSE = os.path.join(sys._MEIPASS, "LICENSE")
