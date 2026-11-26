"""The folder version's start (built into data/<version>/boot.pyc; run by Spiderweb.exe, native/starter): tells the
program where its own files are (files.about.BUNDLE = this folder), then runs spiderweb.pyc as the main program.
Not used when running from the scripts."""
import os
import sys

REPAIR = "--spiderweb-repair"  # (the starter's Repair answer: files.repair, stage F of the folder version)

sys.spiderweb_bundle = os.path.dirname(os.path.abspath(__file__))
sys.argv[0] = sys.executable  # (Spiderweb.exe, as the user started it)
if REPAIR in sys.argv:
    sys.argv.remove(REPAIR)
import runpy  # noqa: E402

runpy.run_path(os.path.join(sys.spiderweb_bundle, "spiderweb.pyc"), run_name="__main__")
