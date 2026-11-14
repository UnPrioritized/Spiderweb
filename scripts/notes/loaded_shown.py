"""A loaded MIDI's ticks as SHOWN after Change visual time signature (PLAN.md 2026-11-14): what every reader of the
file's ticks shares (drawing, playback, Extract, Delete loaded notes, the Fill window, the track list, 31-EDO rows,
the song's length, the CC row, Keep timing's heard tempo, the export's overlap count). notes/loaded_map.py holds the
one rule (file tick -> shown tick, = what Export MIDI writes); here is what the readers need around it:
- chain_of(app): the links of app.loaded_map as arrays ([] = nothing moved: every reader then takes its old path
  untouched).
- map_key(saved): something to compare / key caches by (equal links = equal key; () = none).
- shown(chain, ticks): the way there (map_ticks; the same object back when nothing is moved).
- first_at(chain, x): the way BACK, for a box / view / window given in shown ticks: the first FILE tick shown at x
  or later. The map never falls, so the file ticks before first_at(x) are exactly the ones shown before x: a note
  whose note-on is shown in [t0, t1) is one whose file note-on is in [first_at(t0), first_at(t1)), nothing missed,
  nothing extra (never a plain stretch). Bisection over the map itself: exact for any anchors (squeezes, rounding,
  by-sound spans)."""

import numpy as np

from notes import loaded_map

TOP = 1 << 56  # (file ticks past this are never looked for: far past any song)


def chain_of(app):
    """app.loaded_map as the list of (anchors, moves) arrays, oldest first ([] = none)."""
    saved = getattr(app, "loaded_map", None)
    return loaded_map.links(saved) if saved else []


def map_key(saved):
    """The saved links as something to compare (equal links = equal key; the texts' hashes are kept by Python)."""
    return tuple((ln["n"], ln["f"]) for ln in saved or ())


def shown(chain, ticks):
    """File ticks as they are SHOWN (scalar in = int out; the very same object back when no link)."""
    if not chain:
        return ticks
    return loaded_map.map_ticks(chain, ticks)


def first_at(chain, x):
    """The first file tick (>= 0) shown at tick x or later, for a scalar or an int64 array x (the same kind out). No
    links: x itself (never below 0)."""
    scalar = np.ndim(x) == 0
    x = np.asarray(x, np.int64)
    if not chain:
        out = np.maximum(x, 0)
        return int(out) if scalar else out
    x = np.atleast_1d(x)
    hi = np.minimum(np.maximum(x, 1), TOP)
    while True:  # (a tick shown at x or later: doubled until it is)
        bad = (loaded_map.map_ticks(chain, hi) < x) & (hi < TOP)
        if not bad.any():
            break
        hi = np.where(bad, np.minimum(hi * 2, TOP), hi)
    lo = np.zeros_like(hi)
    while (lo < hi).any():
        mid = (lo + hi) // 2
        ok = loaded_map.map_ticks(chain, mid) >= x
        hi = np.where(ok, mid, hi)
        lo = np.where(ok, lo, mid + 1)
    return int(hi[0]) if scalar else hi


def shown_span(chain, lm):
    """(first, end) of the loaded MIDI's notes as SHOWN ticks."""
    if not chain:
        return lm.first, lm.end
    return shown(chain, int(lm.first)), shown(chain, int(lm.end))


def shown_end(app):
    """The loaded MIDI's last note end as shown (ticks of its own PPQ; 0 = no notes)."""
    lm = app.loaded
    if lm is None or not lm.notes:
        return 0
    return int(shown(chain_of(app), int(lm.end)))


def shown_first(app):
    """The loaded MIDI's first note start as shown."""
    lm = app.loaded
    if lm is None or not lm.notes:
        return 0
    return int(shown(chain_of(app), int(lm.first)))


def bind(lm, saved):
    """lm's reads of the file's notes by a shown box (lm._taken, what's hidden) were made under the links lm._map_key;
    another map = they may hold other notes: let go, the hidden set worked out again. True when it changed."""
    key = map_key(saved)
    if getattr(lm, "_map_key", ()) == key:
        return False
    lm._map_key = key
    lm._taken.clear()
    lm.boxes = None
    if len(lm.h_on):  # (and nothing hidden until they're read again: gather_box leaves hidden notes out, so the
        from notes.loaded import unhide_all  # boxes read under the new map found none of their own notes)
        unhide_all(lm)
    return True


def anchors_shown(lm, saved):
    """lm.anchors() (the file's note ticks, the other events' ticks; already read) as SHOWN ticks, for Keep timing's
    "notes too close" lines. Sorted still (the map never falls); kept on lm while the links stay the same."""
    got = lm._anchors
    chain = loaded_map.links(saved)
    if not chain:
        return got
    key = map_key(saved)
    memo = getattr(lm, "_anchors_shown", None)
    if memo is not None and memo[0] == key and memo[1] is got:
        return memo[2]
    out = (shown(chain, got[0]), shown(chain, got[1]))
    lm._anchors_shown = (key, got, out)
    return out
