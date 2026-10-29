"""Files given to Spiderweb from outside (14, user 2026-10-28 / 10-29): dropped onto the main window, opened with it
(double-click a .spwb, dropped onto Spiderweb's icon: they come in sys.argv), or handed over by a second start
while this one is open (spiderweb.py writes them into user/open-next/, polled here). A MIDI = Load MIDI (a NEW
project), a project = Open project; Save first is offered by both. Several = the first, the status line says the
others were left out (user 2026-10-29)."""

import os
import time

from files.about import USER
from files.lang import tr, trn

HANDED = os.path.join(USER, "open-next")  # (one small text file per handed-over start: the paths, one a line)
HANDED_POLL_MS = 500
HANDED_KEEP_S = 60
MIDI_EXTS = (".mid", ".midi", ".rmi", ".smf")
PROJECT_EXTS = (".spwb", ".json")


def usable(paths):
    """The dropped / given paths Spiderweb can open (by name only: runs inside the drop's own call)."""
    return [p for p in paths if os.path.splitext(p)[1].lower() in MIDI_EXTS + PROJECT_EXTS]


def is_midi(path):
    """A MIDI by its first bytes (MThd, or an RMID's RIFF), else by its name."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        head = b""
    if head in (b"MThd", b"RIFF"):
        return True
    return not head and os.path.splitext(path)[1].lower() in MIDI_EXTS


def hand_over(paths):
    """A second start (spiderweb.py): the files go to the open Spiderweb. True when written."""
    from files.safefile import write_text
    try:
        os.makedirs(HANDED, exist_ok=True)
        write_text(os.path.join(HANDED, f"{time.time_ns()}-{os.getpid()}.txt"), "\n".join(paths) + "\n")
        return True
    except OSError:
        return False


def take_handed():
    """The paths second starts handed over (oldest first); their files are removed."""
    try:
        names = sorted(n for n in os.listdir(HANDED) if n.endswith(".txt"))
    except OSError:
        return []
    paths = []
    for n in names:
        p = os.path.join(HANDED, n)
        try:
            if time.time() - os.path.getmtime(p) < HANDED_KEEP_S:  # (older: left when Spiderweb closed, not wanted now)
                with open(p, encoding="utf-8") as f:
                    paths += [line.strip() for line in f if line.strip()]
            os.remove(p)
        except OSError:
            pass
    return paths


def open_given(app, paths):
    """Opens the first of the given paths (the rest said on the status line). Waits for a question / window that
    holds the mouse (a modal one) to close first."""
    paths = [os.path.abspath(p) for p in paths if p]
    if not paths:
        return
    if app.grab_current() is not None or getattr(app, "_exporting", None):
        app.after(HANDED_POLL_MS, lambda: open_given(app, paths))
        return
    path, rest = paths[0], len(paths) - 1
    try:
        app.lift()
        app.focus_force()
    except Exception:
        pass
    if not os.path.isfile(path):
        app.status.config(text=tr("open.missing", name=os.path.basename(path)))
        return
    if is_midi(path):
        from window.load_midi import load_midi
        load_midi(app, path)
    else:
        app.open_project(path)
    if rest:
        app.status.config(text=trn("open.left_out", rest, n=rest))


def start(app, given=()):
    """At start: drops onto the main window, the second starts' files (polled), and the files Spiderweb was
    started with (after the autosave opened)."""
    from files.dropfiles import accept_drops
    app.drops = accept_drops(app, usable, lambda paths: open_given(app, paths))

    first = [list(given)]  # (several files opened from Explorer at once = one start each: taken together)

    def poll():
        got = first.pop() + take_handed() if first else take_handed()
        if got:
            open_given(app, got)
        app.after(HANDED_POLL_MS, poll)
    app.after(HANDED_POLL_MS, poll)
