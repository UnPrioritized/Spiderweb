"""Gate aware flip (right-click, above the normal flips): only how a spam shape's gates are laid is flipped sideways,
not the drawing. sh["gate_flip"] = True.

The notes are made from the drawing flipped sideways (its Range graph NOT reversed, its velocities as they are), then
flipped back onto the real drawing: each key row is laid from its right edge, an exact mirror image of the shape
laid from its left (Colours, area colours, outline gate band and leftover slivers go along). Not the same as
reversing the Range graph: that still lays every row from its left edge (user's pictures, 2026-10-11). With Multi
channel its colours are handed out in mirrored order too (engine.render back: the turn at the right edge gets the
channel the one at the left edge had).
Done before glue, note tool pages and Sustain. Switching it on / off turns the velocities round once, like a flip
(drawing them again in the velocity pane overrides that as usual)."""

import json
import math

import numpy as np

from notes.custom import SPAM_FILLS, gate_ticks
from notes.fx import flip_shape, reverse_velocity

LEFT_OUT = ("cut", "merge", "notes", "picture", "hz")  # (sliced pieces / merged / pasted notes: later steps)


def offered(sh):
    """True if the menu item is shown for sh: no Hz bass, funnel or image (user)."""
    return sh["kind"] != "funnel" and not sh.get("hz") and "picture" not in sh


def why_not(sh, notes):
    """None if a gate aware flip changes sh's notes (notes: (start, end, key, ...) rows), else why not: "one_gate"
    (every key row has one note: nothing to lay the other way, user) or "no_gates"."""
    if sh["kind"] != "custom" or sh.get("fill") not in SPAM_FILLS or any(k in sh for k in LEFT_OUT):
        return "no_gates"
    if len(np.unique(notes[:, 2])) == len(notes):
        return "one_gate"
    return None


def is_flipped(sh):
    """True if sh's notes are made gate flipped."""
    return (sh.get("gate_flip") is True and sh["kind"] == "custom" and sh.get("fill") in SPAM_FILLS
            and not any(k in sh for k in LEFT_OUT))


def mirror_ticks(sh, right, ppq):
    """Twice the tick the drawing is flipped around (right = its right end in beats): a whole bar line (4 beats) at
    or past twice its right end, so the flipped drawing never starts before the song does and the ticks flip back
    exactly. Start Aligned with no Range: a line of its gate grid too (counted from tick 0), so the notes stay on
    it (only the colours' order turns round, user)."""
    step = 4 * ppq
    if sh.get("align") == "aligned" and not sh.get("range"):
        step = math.lcm(step, gate_ticks(sh["gate"], ppq))
    return step * max(1, math.ceil(2 * right * ppq / step))


def flipped_drawing(sh, mid2):
    """sh's drawing flipped sideways around mid2 / 2 for making its notes: no glue, pages or Sustain (done after, on
    the real shape), the Range as it was (laid the same way, from the left), velocities as they were."""
    out = json.loads(json.dumps({k: v for k, v in sh.items()
                                 if k not in ("gate_flip", "glue", "fx", "_m", "sustain")}))
    flip_shape(out, True, mid2)  # (its velocities turned round too: flipped back with the notes, they're as drawn)
    for k in ("range", "range_kept"):
        if k in sh:
            out[k] = json.loads(json.dumps(sh[k]))
    return out


def flipped_back(notes, ticks):
    """Notes made from the flipped drawing put back: start / end mirrored around ticks / 2."""
    out = notes.copy()
    out[:, 0], out[:, 1] = ticks - notes[:, 1], ticks - notes[:, 0]
    return out


def turn_velocities(sh):
    """The velocities turned round, as a sideways flip does (switching the gate aware flip on / off): the line, and
    velocities drawn after its note tool pages."""
    reverse_velocity(sh)
    for step in sh.get("fx") or ():
        if step["tool"] == "vel":
            step["pts"] = [[1 - u, v] for u, v in reversed(step["pts"])]
