"""The program's version and its folders (output and the logs go in HERE; autosave, settings, shapes in user/;
the program's own parts in PARTS)."""

import os
import sys

VERSION = "1.8.1"
try:  # (written by build_version.py for a build: its commit + day, so builds with one version can be told apart)
    from files.build_stamp import BUILD
except ImportError:
    BUILD = ""
try:  # (the commit's date, time, timezone and short code: What's new shows it under this version)
    from files.build_stamp import BUILT
except ImportError:
    BUILT = ""
FULL_VERSION = f"{VERSION} ({BUILD})" if BUILD else VERSION  # (Help, errors.log; saved files keep it as "build")
WEBSITE = "https://github.com/UnPrioritized/Spiderweb"  # new versions, the source code, problem reports

# The folder version (Spiderweb.exe + data/<version>/, built by build.bat): its start code (scripts/boot.py) names
# the data/<version> folder before anything else loads. None = run from the scripts.
BUNDLE = getattr(sys, "spiderweb_bundle", None)
FROZEN = BUNDLE is not None
# The spiderweb folder: where spiderweb.py is (scripts/<group>/ is two levels down), or where Spiderweb.exe is.
HERE = (os.path.dirname(os.path.dirname(BUNDLE)) if FROZEN
        else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# The folder holding the program's own parts (icons, lang, loops, kestrel, bass): scripts/, or data/<version>/
PARTS = BUNDLE if FROZEN else os.path.join(HERE, "scripts")
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

# The window icons (made by make_icon.py)
ICONS = os.path.join(PARTS, "icons")
# The "Spiderweb" picture on the About page (written with the Text tool; Help shrinks it to fit)
BANNER = os.path.join(ICONS, "spiderweb.png")
# The license: the copy next to Spiderweb, else the one in data/<version>/
LICENSE = os.path.join(HERE, "LICENSE")
if not os.path.exists(LICENSE) and FROZEN:
    LICENSE = os.path.join(BUNDLE, "LICENSE")
