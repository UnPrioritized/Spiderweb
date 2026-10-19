"""
Gate subtract (right-click "Gate subtract..."): every note of a shape ends a little earlier, so a blank space shows
between notes that touched. Only the end moves (the start is the rhythm); a note never gets shorter than 1 tick.

sh["subtract"] = {"by": beats (kept in beats like every length: a PPQ change keeps the look; whole ticks, at least 1),
"touch": True (only notes the next note on their key starts on or before the end of; default: every note)}.
Done LAST, after everything else that makes the shape's notes, Sustain too (engine.fx_notes), so no start moves.
Not on a Hz bass (its own note lengths) or an image (its notes are its picture), like Sustain.
"""

import math

import numpy as np

MOST = 10 ** 4  # beats a typed amount may be


def clean_subtract(s):
    """sh["subtract"] from a file -> valid, or None (not subtracted)."""
    if not isinstance(s, dict):
        return None
    try:
        by = float(s.get("by"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(by) or by <= 0:
        return None
    out = {"by": min(by, MOST)}
    if s.get("touch") is True:
        out["touch"] = True
    return out


def is_subtracted(sh):
    """True if sh's gates are made shorter (never a Hz bass or an image)."""
    return isinstance(sh.get("subtract"), dict) and not sh.get("hz") and "picture" not in sh


def ticks_of(sub, ppq):
    """The amount in whole ticks (at least 1)."""
    return max(1, int(round(sub["by"] * ppq)))


def touching(notes):
    """Per note: True if the next note on its key (every colour together, like Sustain) starts on or before its end."""
    out = np.zeros(len(notes), bool)
    if len(notes) < 2:
        return out
    order = np.lexsort((notes[:, 0], notes[:, 2]))
    s, e, p = notes[order, 0], notes[order, 1], notes[order, 2]
    out[order[:-1]] = (p[1:] == p[:-1]) & (s[1:] <= e[:-1])
    return out


def subtracted(notes, sub, ppq):
    """notes (start, end, pitch, velocity, ...) with their ends moved earlier (engine.fx_notes). Never under 1 tick,
    never longer than they were."""
    if not len(notes):
        return notes
    n = ticks_of(sub, ppq)
    out = notes.copy()
    s, e = out[:, 0], out[:, 1]
    new = np.maximum(e - n, np.minimum(e, s + 1))
    if sub.get("touch"):
        new = np.where(touching(notes), new, e)
    out[:, 1] = new
    return out
