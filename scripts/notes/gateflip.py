"""Gate aware flip (right-click, above the normal flips): only how a spam shape's gates are laid is flipped sideways,
not the drawing. sh["gate_flip"] = True.

The notes are made from the drawing flipped sideways (its Range graph NOT reversed, its velocities as they are), then
flipped back onto the real drawing: each key row is laid from its right edge, an exact mirror image of the shape
laid from its left (Colours, area colours, outline gate band and leftover slivers go along). Not the same as
reversing the Range graph: that still lays every row from its left edge (user's pictures, 2026-10-11).
Done before glue, note tool pages and Sustain. Switching it on / off turns the velocities round once, like a flip
(drawing them again in the velocity pane overrides that as usual)."""

import json
import math

from notes.custom import SPAM_FILLS, gate_ticks
from notes.fx import flip_shape, reverse_velocity
from notes.gaterange import steps_to

LEFT_OUT = ("cut", "merge", "notes", "picture", "hz")  # (sliced pieces / merged / pasted notes: later steps)


def offered(sh):
    """True if the menu item is shown for sh: no Hz bass, funnel or image (user)."""
    return sh["kind"] != "funnel" and not sh.get("hz") and "picture" not in sh


def why_not(sh, ppq):
    """None if a gate aware flip changes sh's notes, else why not: "aligned", "one_gate" or "no_gates"."""
    if sh["kind"] != "custom" or sh.get("fill") not in SPAM_FILLS or any(k in sh for k in LEFT_OUT):
        return "no_gates"
    if sh.get("align") == "aligned":  # (its notes sit on the song's gate grid: mirrored they wouldn't)
        return "aligned"
    rg = sh.get("range")
    if not rg:
        return "one_gate"
    a, b = gate_ticks(sh["gate"], ppq), gate_ticks(rg["to"], ppq)
    if a == b or rg.get("halves") and not steps_to(a, b):
        return "one_gate"
    return None


def is_flipped(sh):
    """True if sh's notes are made gate flipped (an Aligned one keeps the setting but is made as usual)."""
    return (sh.get("gate_flip") is True and sh["kind"] == "custom" and sh.get("fill") in SPAM_FILLS
            and sh.get("align") != "aligned" and not any(k in sh for k in LEFT_OUT))


def mirror_beats(right):
    """Twice the beat the drawing is flipped around: a whole bar line (4 beats) at or after its right end, so the
    flipped drawing starts at or after it (never before the song's start) and the ticks flip back exactly."""
    return 2 * 4 * max(1, math.ceil(right / 4))


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
