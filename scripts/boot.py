"""The folder version's start (built into data/<version>/boot.pyc; run by Spiderweb.exe, native/starter): tells the
program where its own files are (files.about.BUNDLE = this folder), then runs spiderweb.pyc as the main program.
--spiderweb-repair (the starter's / a repair offer's) = repair mode instead (files/repair.py). An error at start that
damaged program files cause (a module or DLL that won't load) = the repair offered (files.repair.offer).
Not used when running from the scripts."""
import os
import sys

REPAIR = "--spiderweb-repair"

sys.spiderweb_bundle = os.path.dirname(os.path.abspath(__file__))
sys.argv[0] = sys.executable  # (Spiderweb.exe, as the user started it)
if REPAIR in sys.argv:
    sys.argv.remove(REPAIR)
    try:
        from files import repair  # noqa: E402
    except BaseException:  # (the repair's own files damaged: a plain message, the starter's words, then the page)
        import ctypes
        if ctypes.windll.user32.MessageBoxW(
                None, "Spiderweb can't repair itself: its program files are too damaged.\n\nOpen the download page "
                "to get Spiderweb again? Your settings, autosave and exports (the user and output folders) stay as "
                "they are.", "Spiderweb", 0x04 | 0x10 | 0x10000) == 6:
            import webbrowser
            webbrowser.open("https://github.com/UnPrioritized/Spiderweb/releases/latest")
        sys.exit(3)  # (3: said already; the starter adds nothing)
    sys.exit(repair.run(sys.argv[1:]))
import runpy  # noqa: E402

try:
    runpy.run_path(os.path.join(sys.spiderweb_bundle, "spiderweb.pyc"), run_name="__main__")
except SystemExit:
    raise
except BaseException as e:  # (an error before Spiderweb's own error handling is up)
    try:
        from files import repair
        offered = repair.offer(e)
    except BaseException:  # noqa: BLE001 (the repair's own files damaged too: the plain error)
        offered = False
    if offered:
        sys.exit(1)
    raise
