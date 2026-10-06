"""A shape's note tool pages (user, 2026-10-06): sh["fx"] = the steps done to its notes after they're made (and
after its glue), in the order they were added. The drawing stays editable: every step runs again on its new notes.

  {"tool": "chop" / "claw" / "strum", "cfg": that tool's settings, "off": True (switched off; optional)}
      a page of the Chop / Claw machine / Strum window (each window shows its own pages; the same tool can be
      used again and again)
  {"tool": "flip", "axis": "time" / "keys"}
      the shape was flipped after the steps before it: they run on the notes flipped back, and their result is
      flipped (each flip within the box of the notes it gets: they stay where they were)
  {"tool": "vel", "pts": [[u, velocity], ...]}
      velocities drawn after the steps before it: they replace those steps' velocities (only between the first and
      last u: 0..1 across the shape's time, as its velocity line)

Old files kept one chop / claw / strum per shape: they load as the first pages, in the order they were applied."""

import json
import math

import numpy as np

from notes.chop import clean_chop
from notes.claw import clean_claw
from notes.envelope import env_values
from notes.strum import clean_strum

TOOLS = ("chop", "claw", "strum")  # (old files' order)
CLEAN = {"chop": clean_chop, "claw": clean_claw, "strum": clean_strum}
AXES = ("time", "keys")
MAX_STEPS = 200


def is_page(step):
    return step["tool"] in TOOLS


def pages(fx):
    """The tool pages of a step list."""
    return [st for st in fx or () if is_page(st)]


def clean_fx(sh):
    """sh["fx"] from a file -> a valid step list, or None. Without one: the old chop / claw / strum as pages."""
    fx = sh.get("fx")
    if not isinstance(fx, list):
        fx = [{"tool": k, "cfg": sh[k]} for k in TOOLS if sh.get(k)]
    out = []
    for st in fx[:MAX_STEPS]:
        if not isinstance(st, dict):
            continue
        tool = st.get("tool")
        if tool in TOOLS:
            cfg = CLEAN[tool](st.get("cfg"))
            if cfg:
                out.append({"tool": tool, "cfg": cfg, **({"off": True} if st.get("off") is True else {})})
        elif not pages(out):
            continue  # (a flip / velocities before any page change nothing)
        elif tool == "flip" and st.get("axis") in AXES:
            out.append({"tool": "flip", "axis": st["axis"]})
        elif tool == "vel":
            pts = clean_pts(st.get("pts"))
            if pts:
                out.append({"tool": "vel", "pts": pts})
    return out or None


def clean_pts(pts):
    out = []
    for p in pts if isinstance(pts, list) else ():
        try:
            u, v = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(u) and math.isfinite(v):
            out.append([u, max(1.0, min(127.0, v))])
    return sorted(out, key=lambda p: p[0])


def copied(fx):
    return json.loads(json.dumps(fx))


def flipped(fx, axis):
    """The steps after the shape is flipped: a flip step added (two of the same in a row cancel out), or nothing
    when there's no page (flipping the drawing is enough)."""
    if not pages(fx):
        return fx
    if fx[-1] == {"tool": "flip", "axis": axis}:
        return fx[:-1] or None
    return fx + [{"tool": "flip", "axis": axis}]


def with_velocity(fx, pts):
    """The steps after velocities pts ([[u, v]], u across the shape's time) were drawn: a velocity step on top (one
    that covers the last one replaces it), or nothing when there's no page (the drawing's own velocities do)."""
    if not pages(fx) or not pts:
        return fx
    pts = clean_pts(pts)
    last = fx[-1]
    if last["tool"] == "vel" and pts[0][0] <= last["pts"][0][0] and pts[-1][0] >= last["pts"][-1][0]:
        fx = fx[:-1]
    return fx + [{"tool": "vel", "pts": pts}]


def notes_box(notes):
    """(first start + last end, lowest + highest key) of notes: flipping within it keeps them in their box."""
    if not len(notes):
        return 0, 0
    return int(notes[:, 0].min() + notes[:, 1].max()), int(notes[:, 2].min() + notes[:, 2].max())


def mirrored(notes, axes, box):
    """notes flipped within box (notes_box) along each of axes."""
    if not len(notes) or not axes:
        return notes
    out = notes.copy()
    if "time" in axes:
        out[:, 0], out[:, 1] = box[0] - notes[:, 1], box[0] - notes[:, 0]
    if "keys" in axes:
        out[:, 2] = box[1] - notes[:, 2]
    return out


def toggled(axes, axis):
    return sorted(set(axes or ()) ^ {axis})


def velocities(notes, pts, t_lo, t_hi):
    """notes' velocities with the drawn pts (u = 0..1 from tick t_lo to t_hi) put on the notes starting between the
    first and last u."""
    if not len(notes):
        return notes
    frac = (notes[:, 0] - t_lo) / (t_hi - t_lo) if t_hi > t_lo else np.zeros(len(notes))
    on = (frac >= pts[0][0] - 1e-9) & (frac <= pts[-1][0] + 1e-9)
    if not on.any():
        return notes
    out = notes.copy()
    out[on, 3] = np.clip(np.round(env_values(pts, frac[on])), 1, 127).astype(np.int64)
    return out
