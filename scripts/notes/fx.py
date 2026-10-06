"""A shape's note tool pages (user, 2026-10-06): sh["fx"] = the steps done to its notes after they're made (and
after its glue), in the order they were added. The drawing stays editable: every step runs again on its new notes.

  {"tool": "chop" / "claw" / "strum", "cfg": that tool's settings, "off": True (switched off; optional)}
      a page of the Chop / Claw machine / Strum window (each window shows its own pages; the same tool can be
      used again and again)
  {"tool": "flip", "axis": "time" / "keys"}
      the shape was flipped after the steps before it: they run on the notes flipped back, and their result is
      flipped (each flip within the box of the notes it gets: they stay where they were)
  {"tool": "turn", "cw": clockwise?, "r": beats per key on screen then}
      the shape was turned 90 degrees after the steps before it (user, 2026-10-06): they run on its drawing turned
      back, their result is turned within its own box (each note a column of short notes, one per key row: close,
      not exact). Turning back right after cancels it out
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
        elif tool == "turn" and isinstance(st.get("r"), (int, float)) and 1e-9 < st["r"] < 1e9:
            out.append({"tool": "turn", "cw": bool(st.get("cw")), "r": float(st["r"])})
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


def with_turn(fx, cw, r):
    """The steps after the shape is turned 90 degrees (r: beats per key on screen): a turn step added (turning back
    right after one cancels it out), or nothing when there's no page (turning the drawing is enough)."""
    if not pages(fx):
        return fx
    last = fx[-1]
    if last["tool"] == "turn" and last["cw"] != cw and abs(last["r"] - r) <= 1e-9 * max(r, 1e-9):
        return fx[:-1] or None
    return fx + [{"tool": "turn", "cw": cw, "r": r}]


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


def swapped(axes):
    """Flip axes seen after a quarter turn: time <-> keys."""
    return sorted({"keys" if a == "time" else "time" for a in axes or ()})


def turn_notes(a, cw, ticks, keys):
    """Notes a (start, end, key, then any columns riding along) turned 90 degrees within their own box, clockwise as
    seen on screen when cw; ticks = ticks per key on screen. Each note becomes a column: one note per key row it then
    covers (at least one), each one key's worth of ticks long. -> (the notes, which row of a each came from)."""
    if not len(a):
        return a, np.zeros(0, np.int64)
    s = 1 if cw else -1
    ct = (a[:, 0].min() + a[:, 1].max()) / 2
    ck = (a[:, 2].min() + a[:, 2].max()) / 2
    k0, k1 = ck - s * (a[:, 0] - ct) / ticks, ck - s * (a[:, 1] - ct) / ticks
    lo, hi = np.minimum(k0, k1), np.maximum(k0, k1)
    t0, t1 = ct + s * (a[:, 2] - 0.5 - ck) * ticks, ct + s * (a[:, 2] + 0.5 - ck) * ticks
    start = np.round(np.minimum(t0, t1)).astype(np.int64)
    end = np.maximum(np.round(np.maximum(t0, t1)).astype(np.int64), start + 1)
    first, last = np.ceil(lo - 1e-9), np.ceil(hi - 1e-9) - 1  # (the key rows whose middle it covers)
    none = last < first
    first[none] = last[none] = np.round((lo[none] + hi[none]) / 2)
    first, last = np.clip(first, 0, keys - 1).astype(np.int64), np.clip(last, 0, keys - 1).astype(np.int64)
    counts = np.where((hi < -0.5) | (lo > keys - 0.5), 0, last - first + 1)
    idx = np.repeat(np.arange(len(a)), counts)
    step = np.arange(len(idx)) - np.repeat(np.cumsum(counts) - counts, counts)
    out = a[idx].copy()
    out[:, 0], out[:, 1], out[:, 2] = start[idx], end[idx], first[idx] + step
    out[:, 0] = np.maximum(out[:, 0], 0)  # (none before the song's start)
    keep = out[:, 1] > out[:, 0]
    return out[keep], idx[keep]


def turn_shape(sh, clockwise, r, cb, cp):
    """sh turned 90 degrees around (cb, cp) (beats, keys) as it looks on screen, r = beats per key there (so how
    many beats one key becomes): its points, and its roundness, tumours, formulas, text, glue and gate range along.
    Changes sh."""
    from notes.gaterange import turned_range
    from notes.glue import turned as glue_turned
    from notes.joined import all_tumours
    sign = 1 if clockwise else -1
    sh["pts"] = [[cb + sign * (p - cp) * r, cp - sign * (b - cb) / r] for b, p in sh["pts"]]
    if sh["kind"] == "arc" or sh["kind"] == "free" and "k" in sh:  # still round (arc.py, smooth.py)
        sh["k"] = r * r / sh.get("k", 1.0)
    if sh.get("text"):  # its size / grow are measured the same way (see text.py)
        sh["text"]["k"] = r * r / sh["text"]["k"]
    for tm in all_tumours(sh):  # the bumps turn with it (sizes as they look on screen, see tumour.py)
        tm["size"] *= tm["k"] / r
        tm["length"] *= r / tm["k"]
        tm["dist"] *= r / tm["k"]
        tm["ease"] = tm.get("ease", 0.0) * r / tm["k"]
        tm["k"] = r * r / tm["k"]
    pat = sh.get("pattern")
    if pat:  # a pattern along a curve turns the same way (pattern.py)
        pat["scale"] *= pat["k"] / r
        pat["k"] = r * r / pat["k"]
    if sh.get("shape"):  # (its sizes are shares of the curve's length: only the screen proportions)
        sh["shape"]["k"] = r * r / sh["shape"]["k"]
    if sh.get("glue"):
        sh["glue"] = glue_turned(sh["glue"], clockwise)
    for k in ("range", "range_kept"):
        if sh.get(k):
            sh[k] = turned_range(sh[k], clockwise)
    return sh
