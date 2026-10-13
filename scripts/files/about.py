"""The program's version and its folder (output and the logs go there; autosave, settings, shapes in user/)."""

import os
import sys

VERSION = "1.5.3"
WEBSITE = "https://github.com/UnPrioritized/Spiderweb"  # new versions, the source code, problem reports

# The spiderweb folder (scripts/<group>/ is two levels down); the .exe build unpacks to a temp folder, so there it's
# the folder the .exe is in instead.
HERE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# The app's own files (autosave, settings, presets, saved shapes): one folder, so the spiderweb folder
# holds only the program and the user's own files
USER = os.path.join(HERE, "user")
# What older versions kept loose in the spiderweb folder (move_old_files puts them in USER)
OLD_FILES = ("autosave.json", "autosave-backup.json", "look.json", "hz_presets.json", "hz_presets-broken.json",
             "patterns.json", "curves.json", "curves-old.json", "rhythms.json", "colour_formats.json",
             "packages.json", "errors.log", "errors-old.log", "shapes")


def move_old_files():
    """Moves what older versions kept loose next to Spiderweb into USER, once (a file already in USER wins and
    the old one stays where it was). Never stops Spiderweb from starting. Returns the names it couldn't move (held
    by another program...): the user is told, so the old work isn't thought lost."""
    try:
        os.makedirs(USER, exist_ok=True)
        names = list(OLD_FILES) + [f for f in os.listdir(HERE) if f.startswith(("autosave-kept-", "autosave-broken-"))
                                   and f.endswith(".json")]
    except OSError:
        return []
    failed = []
    for name in names:
        old, new = os.path.join(HERE, name), os.path.join(USER, name)
        if os.path.exists(old) and not os.path.exists(new):
            try:
                os.replace(old, new)
            except OSError:
                failed.append(name)
    return failed

# The window icons (made by make_icon.py); the .exe carries them inside, unpacked with the program
ICONS = os.path.join(getattr(sys, "_MEIPASS", os.path.join(HERE, "scripts")), "icons")
# The "Spiderweb" picture on the About page (written with the Text tool); -half = the same smoothly shrunk to half
BANNER = os.path.join(ICONS, "spiderweb.png")
BANNER_HALF = os.path.join(ICONS, "spiderweb-half.png")
# The license: the copy next to Spiderweb, else the one the .exe carries inside
LICENSE = os.path.join(HERE, "LICENSE")
if not os.path.exists(LICENSE) and hasattr(sys, "_MEIPASS"):
    LICENSE = os.path.join(sys._MEIPASS, "LICENSE")
