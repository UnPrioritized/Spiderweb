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
HANDED_WAIT_S = 4  # (a second start waits this long for the open Spiderweb to take its file)
STARTED = None  # (spiderweb.py: when this Spiderweb started; hand-overs written before are old)
HOLD = [False, None]  # (started with a file: [the autosave's MIDI reload held, the link it was for])
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
    """A second start (spiderweb.py): the files go to the open Spiderweb. The file written (None: couldn't)."""
    from files.safefile import write_text
    try:
        os.makedirs(HANDED, exist_ok=True)
        path = os.path.join(HANDED, f"{time.time_ns()}-{os.getpid()}.txt")
        write_text(path, "\n".join(paths) + "\n")
        return path
    except OSError:
        return None


def handed_taken(path, wait_s=HANDED_WAIT_S):
    """A second start: True once the open Spiderweb took its file. Not taken in time (it's closing / stuck) = the
    file is removed again and False: this start goes on by itself (hunt 2026-10-29: the file was lost)."""
    end = time.time() + wait_s
    while time.time() < end:
        if not os.path.exists(path):
            return True
        time.sleep(0.1)
    try:
        os.remove(path)
    except OSError:
        return not os.path.exists(path)
    return False


def take_handed():
    """The paths second starts handed over (oldest first); their files are removed. Ones written before this
    Spiderweb started are dropped (left when one closed; never by age: a busy Spiderweb takes them late)."""
    try:
        names = sorted(n for n in os.listdir(HANDED) if n.endswith(".txt"))
    except OSError:
        return []
    paths = []
    for n in names:
        p = os.path.join(HANDED, n)
        try:
            if STARTED is None or os.path.getmtime(p) >= STARTED - 1:
                with open(p, encoding="utf-8") as f:
                    paths += [line.strip() for line in f if line.strip()]
            os.remove(p)
        except OSError:
            pass
    return paths


def _state(app):
    st = getattr(app, "_given", None)
    if st is None:
        st = app._given = {"pending": None, "busy": False, "left": set()}
    return st


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def open_given(app, paths):
    """Opens the first of the given paths (the rest said on the status line). Waits for a question / window that
    holds the mouse (a modal one) to close first. ONE at a time (hunt 2026-10-29): files arriving while one waits
    or is being opened (its questions showing) only add to the "left out" count."""
    paths = list(dict.fromkeys(os.path.abspath(p) for p in paths if p))  # (the same file twice = once)
    if not paths:
        return
    st = _state(app)
    if st["busy"] or st["pending"]:
        now = st["pending"] or st["busy"]
        st["left"] |= {_key(p) for p in paths} - {_key(p) for p in now}
        if st["left"]:
            app.status.config(text=trn("open.left_out", len(st["left"]), n=len(st["left"])))
        return
    st["pending"] = paths
    _try_open(app)


def _try_open(app):
    st = _state(app)
    if app.grab_current() is not None or getattr(app, "_exporting", None):
        app.after(HANDED_POLL_MS, lambda: _try_open(app))
        return
    paths, st["pending"] = st["pending"], None
    if not paths:
        return
    path = paths[0]
    st["left"] |= {_key(p) for p in paths[1:]}
    st["left"].discard(_key(path))
    st["busy"] = paths
    try:
        try:
            app.lift()
            app.focus_force()
        except Exception:
            pass
        if not os.path.isfile(path):
            app.status.config(text=tr("open.missing", name=os.path.basename(path)))
        elif is_midi(path):
            from window.load_midi import load_midi
            load_midi(app, path)
        else:
            app.open_project(path)
    finally:
        st["busy"] = False
        left, st["left"] = len(st["left"]), set()
        if left:
            app.status.config(text=trn("open.left_out", left, n=left))
        _let_reload(app)


def hold_reload(app):
    """At start with a file: the autosave's MIDI isn't read again (a big one held the file back, a missing one asked
    first). True = held; _let_reload reads it after all if the project is still the last session's."""
    if not HOLD[0]:
        return False
    HOLD[1] = app.midi_link
    return True


def _let_reload(app):
    if not HOLD[0]:
        return
    HOLD[0] = False
    held, HOLD[1] = HOLD[1], None

    def later():  # (after a Load MIDI window / question the open left showing)
        if app.grab_current() is not None:
            app.after(HANDED_POLL_MS, later)
        elif held is not None and app.midi_link is held and app.loaded is None:
            app.find_loaded_later()
    if held is not None:
        later()


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
        elif HOLD[0]:  # (the given files were all handed elsewhere / gone)
            _let_reload(app)
        app.after(HANDED_POLL_MS, poll)
    app.after(HANDED_POLL_MS, poll)
