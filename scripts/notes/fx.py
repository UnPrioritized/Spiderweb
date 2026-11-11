"""A shape's note tool pages (user, 2026-10-06): sh["fx"] = the steps done to its notes after they're made (and
after its glue, unless a glue step says where it goes), in the order they were added. The drawing stays editable: every step runs again on its new notes.

  {"tool": "chop" / "claw" / "strum" / "path" / "trim", "cfg": that tool's settings, "off": True (switched off; optional)}
      a page of the Chop / Claw machine / Strum / Copy along a path window (each window shows its own pages; the
      same tool can be used again and again)
  {"tool": "flip", "axis": "time" / "keys"}
      the shape was flipped after the steps before it: they run on the notes flipped back, and their result is
      flipped (each flip within the box of the notes it gets: they stay where they were)
  {"tool": "turn", "deg": degrees (clockwise on screen, -180..180, not 0), "r": beats per key on screen then}
      the shape was turned after the steps before it (Turn 90 or a custom shape's corner dragged; user, 2026-10-06):
      they run on its drawing turned back, their result is turned within its own box (each note becomes the short
      notes of the key rows it then crosses: close, not exact). Turns in a row at the same zoom add up (turning
      back right after cancels it out)
  {"tool": "vel", "pts": [[u, velocity], ...]}
      velocities drawn after the steps before it: they replace those steps' velocities (only between the first and
      last u: 0..1 across the shape's time, as its velocity line)
  {"tool": "glue"}
      where the shape's glue (sh["glue"]) was last used among its pages (user, 2026-10-31, as FL: the last thing done
      is what happens; Chop then Glue joins the pieces back). None = before every page (old files, or glued before
      any page); at most one; kept only after a page

Old files kept one chop / claw / strum per shape: they load as the first pages, in the order they were applied."""

import json
import math

import numpy as np

from notes.chop import clean_chop
from notes.claw import clean_claw
from notes.copypath import clean_path
from notes.envelope import env_values
from notes.glue import clean_glue
from notes.strum import clean_strum
from notes.trim import clean_trim

# (old files' order first; "path" = Copy along a path, copypath.py; "trim" = Gate trim, trim.py)
TOOLS = ("chop", "claw", "strum", "path", "trim")
CLEAN = {"chop": clean_chop, "claw": clean_claw, "strum": clean_strum, "path": clean_path, "trim": clean_trim}
AXES = ("time", "keys")
MAX_STEPS = 2000  # (a file's list is cut there: a broken one; flips in a row make one step each way, see flipped)


def is_page(step):
    return step["tool"] in TOOLS


def pages(fx):
    """The tool pages of a step list."""
    return [st for st in fx or () if is_page(st)]


def idle(fx):
    """Is no page of a step list switched on? (Then a flip / turn after them does nothing: engine.before_step.)"""
    return not any(is_page(st) and not st.get("off") for st in fx or ())


def clean_fx(sh, steps=False):
    """sh["fx"] from a file -> a valid step list, or None. Without one: the old chop / claw / strum as pages.
    steps: flips / turns / velocities before any page count too (a sliced piece's, see flipped). A merged shape's
    velocity steps always count (with_velocity always)."""
    vels = bool(sh.get("merge"))
    fx = sh.get("fx")
    if not isinstance(fx, list):
        fx = [{"tool": k, "cfg": sh[k]} for k in TOOLS[:3] if sh.get(k)]
    out = []
    for st in fx[:MAX_STEPS]:
        if not isinstance(st, dict):
            continue
        tool = st.get("tool")
        if tool in TOOLS:
            cfg = CLEAN[tool](st.get("cfg"))
            if cfg:
                out.append({"tool": tool, "cfg": cfg, **({"off": True} if st.get("off") is True else {})})
        elif not pages(out) and not steps and not (vels and tool == "vel"):
            continue  # (a flip / velocities before any page change nothing)
        elif tool == "flip" and st.get("axis") in AXES:
            out.append({"tool": "flip", "axis": st["axis"]})
        elif tool == "turn" and isinstance(st.get("r"), (int, float)) and 1e-9 < st["r"] < 1e9:
            deg = st.get("deg", (90 if st["cw"] else -90) if "cw" in st else None)  # (first tries: "cw")
            if isinstance(deg, (int, float)) and math.isfinite(deg) and abs(norm_deg(deg)) > 1e-9:
                out.append({"tool": "turn", "deg": norm_deg(deg), "r": float(st["r"])})
        elif tool == "vel":
            pts = clean_pts(st.get("pts"))
            if pts:
                out.append({"tool": "vel", "pts": pts})
        elif tool == "glue" and pages(out):
            k = st.get("from", 0)
            if not isinstance(k, int) or isinstance(k, bool) or k < 0:
                continue
            if out[-1]["tool"] == "glue":  # (two in a row: one place, the earlier's)
                continue
            out.append(dict(GLUE, **({"from": k} if k else {})))
    g = clean_glue(sh.get("glue"))
    if not g:  # (no glue: no place for it)
        out = without_glue(out) or []
    else:  # (each place glues the boxes from its "from" on, up to the next place's: hunt 2026-11-07)
        n = len(g) if isinstance(g, list) else 1
        if g is True:  # (glue everything: one place, the last)
            at = [j for j, st in enumerate(out) if st["tool"] == "glue"][-1:]
            out = [dict(GLUE) if [j] == at else st for j, st in enumerate(out) if st["tool"] != "glue" or [j] == at]
        last, kept = -1, []
        for st in out:
            if st["tool"] == "glue":
                k = st.get("from", 0)
                if k <= last or k >= n:
                    continue
                last = k
            kept.append(st)
        out = kept
    return out or None


GLUE = {"tool": "glue"}


def glue_index(fx):
    """Where the glue is first done among the steps fx (its step's index), or None: before them all."""
    for k, st in enumerate(fx or ()):
        if st["tool"] == "glue":
            return k
    return None


def without_glue(fx):
    """The steps without the glue's places (None when none are left)."""
    out = [st for st in fx or () if st["tool"] != "glue"]
    return out or None


def glue_from(glue, fx, k=None):
    """glue cut to what's glued before step k of fx (k None: before every step = before the pages): the boxes
    before the first glue place at or after k. True / no place there: glue as it is (True: only before its one place
    or not at all)."""
    for j, st in enumerate(fx or ()):
        if st["tool"] == "glue" and (k is None or j >= k):
            n = st.get("from", 0)
            return glue[:n] or None if isinstance(glue, list) else None
    return glue


def at_step(glue, step):
    """The boxes a glue place glues (step: its {"tool": "glue", "from"?}; the boxes after the later places are already
    gone: engine.before_step)."""
    n = step.get("from", 0)
    return glue[n:] or None if isinstance(glue, list) and n else glue


def glue_after(fx, glue, before, whole=False):
    """The steps after the shape is glued (again) (user: the last thing done happens; hunt 2026-11-07: each glue
    keeps its own place, an earlier box stays where it was used): glue = its glue now, before = its glue before,
    whole: glued all over (not a box).
    Nothing new glued: the steps as they were. Glue on everything (True): its one place after every page. New boxes:
    a place for them after every page (from the first new box on); none needed when no page comes after the last
    place. No page at all: no place (glued before them all)."""
    if glue is True and before is True and not whole:  # (a box on a shape glued all over: nothing new)
        return fx
    if glue is True:  # (glued all again: it glues again after the pages, user: "glue does nothing" confused)
        fx = without_glue(fx) or []
        return fx + [dict(GLUE)] if pages(fx) else (fx or None)
    if glue == before or not glue:
        return fx
    fx = list(fx or [])
    if not pages(fx) or fx[-1]["tool"] == "glue":
        return fx or None
    k = len(before) if isinstance(before, list) else 0
    return fx + [dict(GLUE, **({"from": k} if k else {}))]


def glue_groups(glue, fx):
    """glue's boxes by where they're done: [before the pages, then each glue place in order] (lists; None: glue
    isn't a list of boxes)."""
    if not isinstance(glue, list):
        return None
    cuts = [st.get("from", 0) for st in fx or () if st["tool"] == "glue"]
    ends = cuts + [len(glue)]
    return [glue[:ends[0]]] + [glue[ends[i]:ends[i + 1]] for i in range(len(cuts))]


def regrouped(groups, fx):
    """(glue, steps) back from glue_groups' groups (each changed alike, e.g. cut to a piece): a place whose boxes are
    all gone goes too; the froms counted again."""
    out, glue, g = [], list(groups[0]), 1
    for st in fx or ():
        if st["tool"] != "glue":
            out.append(st)
            continue
        boxes = groups[g] if g < len(groups) else []
        g += 1
        if boxes:
            out.append(dict(GLUE, **({"from": len(glue)} if glue else {})))
            glue += boxes
    return glue or None, out or None


def before_page(sh, k):
    """sh as its step k gets the notes: only the steps before it, and only the glue done before it (a shape whose
    steps run in an order of its own: every step ordered before it, Sustain... too, steps.before_view)."""
    from notes.steps import FX, before_view, order_of, runs_own_way
    if runs_own_way(sh) and (FX, k) in order_of(sh):
        return before_view(sh, (FX, k))
    fx = sh.get("fx") or []
    out = {key: v for key, v in sh.items() if key != "fx"}
    if fx[:k]:
        out["fx"] = fx[:k]
    if out.get("glue"):
        g = glue_from(out["glue"], fx, k)
        if g:
            out["glue"] = g
        else:
            out.pop("glue")
    return out


def tidy_glue(sh):
    """sh without glue places when it has no glue any more. Changes sh."""
    if not sh.get("glue") and glue_index(sh.get("fx")) is not None:
        fx = without_glue(sh["fx"])
        if fx:
            sh["fx"] = fx
        else:
            sh.pop("fx", None)
    return sh


def chained(first, then, own_glue, first_n=0):
    """Steps first, then steps then (a sliced piece's whole's, then the piece's own). own_glue: the piece has glue of
    its own (its boxes come after the whole's first_n boxes in the joined glue): its glue before its own pages gets
    a place right after first's pages, its places' froms move on by first_n. Else then's places go."""
    first, then = list(first or []), list(then or [])
    if own_glue:
        then = [dict(st, **{"from": st.get("from", 0) + first_n}) if st["tool"] == "glue" else st for st in then]
        if pages(first) and first[-1]["tool"] != "glue" and (not then or then[0]["tool"] != "glue"):
            then = [dict(GLUE, **({"from": first_n} if first_n else {}))] + then
    else:
        then = without_glue(then) or []
    return (first + then) or None


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


def flipped(fx, axis, always=False):
    """The steps after the shape is flipped: a flip step added, or nothing when there's no page (flipping the drawing
    is enough). Flips in a row make at most one step each way (the same one twice cancels out; sideways, upside
    down, sideways = upside down: long lists of them made files too long to open whole). always: a step even so (a
    sliced piece whose notes come from the shape it was cut from's pages, sliced.steps_kept)."""
    if not pages(fx) and not always:
        return fx
    fx = list(fx or [])
    axes = {axis}
    while fx and fx[-1]["tool"] == "flip":  # (flips within one box: the order they're done in doesn't matter)
        axes ^= {fx.pop()["axis"]}
    fx += [{"tool": "flip", "axis": a} for a in sorted(axes)]
    return fx or None


def flip_shape(sh, sideways, mid2):
    """sh's drawing flipped sideways (time) or upside down (keys) around mid2 / 2 (beats or keys): its points, and
    its tumours, formulas, velocities (sideways), glue and gate range along (not its pages: flipped). Changes sh."""
    from notes.gaterange import flipped_range
    from notes.glue import flipped as glue_flipped
    from notes.joined import all_tumours
    sh["pts"] = [[mid2 - b, p] if sideways else [b, mid2 - p] for b, p in sh["pts"]]
    for tm in all_tumours(sh):  # mirrored: the bumps swap sides too
        tm["mirror"] = not tm["mirror"]
    for key in ("pattern", "shape"):  # and a curve's formulas (pattern.py)
        if sh.get(key):
            sh[key]["mirror"] = not sh[key]["mirror"]
    if sideways:  # the velocities flip with it
        reverse_velocity(sh)
        if sh.get("fx") and not pages(sh["fx"]):  # (a merged shape's velocity steps: across its time, mirrored too)
            sh["fx"] = [dict(st, pts=[[1 - u, v] for u, v in reversed(st["pts"])]) if st["tool"] == "vel" else st
                        for st in sh["fx"]]
    elif (sh.get("cycle") or {}).get("shift"):  # (Colours' Diagonal slants the other way, user)
        sh["cycle"] = dict(sh["cycle"], shift=[-sh["cycle"]["shift"][0], sh["cycle"]["shift"][1]])
    if not sideways and sh.get("kind") == "gliss":  # (a Glissando run's copies above are below it now)
        g = sh.get("gliss") or {}
        if g.get("above") or g.get("below"):
            sh["gliss"] = dict(g, above=g.get("below", 0), below=g.get("above", 0))
    if sh.get("glue"):  # (its boxes are shares of the shape's box)
        sh["glue"] = glue_flipped(sh["glue"], sideways)
    for k in ("range", "range_kept"):  # (a spam gate range runs the other way, the one kept while off too; a
        if sh.get(k) and sh["kind"] != "funnel":  # funnel's runs from its line start to its wall, wherever it is)
            sh[k] = flipped_range(sh[k], sideways)
    if sh.get("subtract"):  # (Gate subtract's graphs, like the Range's; sideways its end / start swap: subtract.py)
        from notes.subtract import flipped_subtract
        sh["subtract"] = flipped_subtract(sh["subtract"], sideways)
    if sh.get("extend"):  # (Gate extend's graph: extend.py)
        from notes.extend import flipped_extend
        sh["extend"] = flipped_extend(sh["extend"], sideways)
    stops = (sh.get("sustain") or {}).get("stops")
    if sideways and stops and stops.get("at"):  # (Sustain's stops put by hand: shares of the notes' time; past the
        at = sorted(1 - f if f < 1 else f for f in stops["at"])  # end ones stay)
        sh["sustain"] = dict(sh["sustain"], stops=dict(stops, at=at))
    from notes.recolour import SIDEWAYS, UPSIDE, moved
    moved(sh, SIDEWAYS if sideways else UPSIDE)  # (its colours mirrored with it: recolour.py FRAME)
    return sh


def reverse_velocity(sh):
    """sh's velocity line run the other way (what a sideways flip does to it). Changes sh."""
    if sh.get("vel_env"):
        sh["vel_env"] = [[1 - u, v] for u, v in reversed(sh["vel_env"])]
    if "vel0" in sh and "vel1" in sh:
        sh["vel0"], sh["vel1"] = sh["vel1"], sh["vel0"]
    return sh


def with_turn(fx, deg, r, always=False):
    """The steps after the shape is turned deg degrees clockwise on screen (r: beats per key there): a turn step
    added, or added to the last one if that was a turn at the same zoom (turning back right after cancels it out),
    or nothing when there's no page (turning the drawing is enough). always: a step even so (see flipped)."""
    if not pages(fx) and not always:
        return fx
    fx = fx or []
    last = fx[-1] if fx else {"tool": None}
    if last["tool"] == "turn" and abs(last["r"] - r) <= 1e-9 * max(r, 1e-9):
        deg, fx = deg + last["deg"], fx[:-1]
    deg = norm_deg(deg)
    if abs(deg) <= 1e-9:
        return fx or None
    return fx + [{"tool": "turn", "deg": deg, "r": r}]


def norm_deg(deg):
    """deg as -180 < deg <= 180 (rounded a little, so turns that add up to a whole turn come out 0)."""
    deg = round(float(deg), 9) % 360
    return deg - 360 if deg > 180 else deg + 0.0


def with_velocity(fx, pts, always=False):
    """The steps after velocities pts ([[u, v]], u across the shape's time) were drawn: a velocity step on top (one
    that covers the last one replaces it), or nothing when there's no page (the drawing's own velocities do).
    always: a step even so (a merged shape: its notes are made again from its parts when flipped / reshaped, so a line
    written into its notes was lost; overlap hunt 2026-11-08)."""
    if not (pages(fx) or always) or not pts:
        return fx
    pts = clean_pts(pts)
    fx = fx or []
    last = fx[-1] if fx else {"tool": None}
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


def velocities(notes, pts, t_lo, t_hi, at=None):
    """notes' velocities with the drawn pts (u = 0..1 from tick t_lo to t_hi) put on the notes starting between the
    first and last u (at: where each reads it instead of its start, engine.from0_starts)."""
    if not len(notes):
        return notes
    at = notes[:, 0] if at is None else at
    frac = (at - t_lo) / (t_hi - t_lo) if t_hi > t_lo else np.zeros(len(notes))
    on = (frac >= pts[0][0] - 1e-9) & (frac <= pts[-1][0] + 1e-9)
    if not on.any():
        return notes
    out = notes.copy()
    out[on, 3] = np.clip(np.round(np.round(env_values(pts, frac[on]), 6)), 1, 127).astype(np.int64)
    return out


def swapped(axes):
    """Flip axes seen after a quarter turn: time <-> keys."""
    return sorted({"keys" if a == "time" else "time" for a in axes or ()})


def turn_notes(a, deg, ticks, keys, same=None):
    """Notes a (start, end, key, then any columns riding along) turned deg degrees within their own box, clockwise as
    seen on screen; ticks = ticks per key on screen. Each note (a bar one key tall) becomes one note per key row whose
    middle it then crosses (at least one), as long as it is on that row's middle line. Pieces that touch on a row
    and came from notes on different keys join (a filled area stays filled: user, 2026-10-06; notes that followed
    each other on one key stay apart) when same (one number per note of a; default: the columns after the key) is
    the same. -> (the notes, which row of a each came from: the first piece's)."""
    if not len(a):
        return a, np.zeros(0, np.int64)
    c, s = math.cos(-math.radians(deg)), math.sin(-math.radians(deg))  # (pitch goes up, screen y down)
    if abs(deg / 90 - round(deg / 90)) < 1e-12:
        c, s = round(c), round(s)  # (a quarter turn: exact)
    x0, x1, k = a[:, 0] / ticks, a[:, 1] / ticks, a[:, 2].astype(float)
    xc, kc = (x0.min() + x1.max()) / 2, (k.min() + k.max()) / 2
    mx, my = (x0 + x1) / 2 - xc, k - kc
    cx, cy = xc + mx * c - my * s, kc + mx * s + my * c  # each note's middle, turned
    h = (x1 - x0) / 2
    reach = h * abs(s) + 0.5 * abs(c)  # (how far up / down it reaches from its middle, turned)
    lo, hi = cy - reach, cy + reach
    first, last = np.ceil(lo - 1e-9), np.ceil(hi - 1e-9) - 1  # (the key rows whose middle it covers)
    none = last < first
    first[none] = last[none] = np.round(cy[none])
    first, last = np.clip(first, 0, keys - 1).astype(np.int64), np.clip(last, 0, keys - 1).astype(np.int64)
    counts = np.where((hi < -0.5) | (lo > keys - 0.5), 0, last - first + 1)
    idx = np.repeat(np.arange(len(a)), counts)
    rows = first[idx] + np.arange(len(idx)) - np.repeat(np.cumsum(counts) - counts, counts)
    dy, hh = rows - cy[idx], h[idx]
    # on the row's middle line, x - cx = d: along the note |d c + dy s| <= h, across it |dy c - d s| <= 1/2
    far = np.full(len(idx), 1e18)
    if c:
        u0, u1 = (-hh - dy * s) / c, (hh - dy * s) / c
        u0, u1 = np.minimum(u0, u1), np.maximum(u0, u1)
    else:
        u0, u1 = -far, far
    if s:
        v0, v1 = (dy * c - 0.5) / s, (dy * c + 0.5) / s
        v0, v1 = np.minimum(v0, v1), np.maximum(v0, v1)
    else:
        v0, v1 = -far, far
    d0, d1 = np.maximum(u0, v0), np.minimum(u1, v1)
    miss = d0 > d1  # (a row it doesn't quite reach: a short note where it's nearest)
    d0[miss] = d1[miss] = (d0[miss] + d1[miss]) / 2
    start = np.round((cx[idx] + d0) * ticks).astype(np.int64)
    end = np.maximum(np.round((cx[idx] + d1) * ticks).astype(np.int64), start + 1)
    out = a[idx].copy()
    out[:, 0], out[:, 1], out[:, 2] = np.maximum(start, 0), end, rows  # (none before the song's start)
    keep = out[:, 1] > out[:, 0]
    return joined_pieces(out[keep], idx[keep], a[:, 2], same if same is not None else groups(a[:, 3:]))


def groups(cols):
    """One number per row of cols: the same for the same values."""
    if not cols.shape[1]:
        return np.zeros(len(cols), np.int64)
    return np.unique(cols, axis=0, return_inverse=True)[1].ravel()


def joined_pieces(out, idx, keys_was, same):
    """Turned pieces out (from rows idx) joined where they touch on a row, came from notes on different keys
    (keys_was) and have the same `same` (see turn_notes)."""
    if len(out) < 2:
        return out, idx
    g, k = same[idx], keys_was[idx]
    order = np.lexsort((out[:, 0], g, out[:, 2]))
    out, idx, g, k = out[order], idx[order], g[order], k[order]
    part = np.r_[0, np.cumsum((out[1:, 2] != out[:-1, 2]) | (g[1:] != g[:-1]))]  # (one row, one kind)
    big = int(out[:, 1].max()) + 2
    reach = np.maximum.accumulate(out[:, 1] + part * big) - part * big  # (the furthest end so far there)
    new = np.r_[True, (part[1:] != part[:-1]) | (out[1:, 0] > reach[:-1] + 1) | (k[1:] == k[:-1])]
    first = np.flatnonzero(new)
    joined = out[first].copy()
    joined[:, 1] = np.maximum.reduceat(out[:, 1], first)
    return joined, idx[first]


def turn_pts(pts, deg, r, cb, cp):
    """Points (beats, keys) turned deg degrees clockwise around (cb, cp) as they look on screen (r = beats per key)."""
    c, s = math.cos(-math.radians(deg)), math.sin(-math.radians(deg))
    return [[cb + ((b - cb) / r * c - (p - cp) * s) * r, cp + (b - cb) / r * s + (p - cp) * c] for b, p in pts]


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
        if sh.get(k) and sh["kind"] != "funnel":
            sh[k] = turned_range(sh[k], clockwise)
    if sh.get("subtract"):  # (Gate subtract's graphs: subtract.py)
        from notes.subtract import turned_subtract
        sh["subtract"] = turned_subtract(sh["subtract"], clockwise)
    if sh.get("extend"):  # (Gate extend's graph: extend.py)
        from notes.extend import turned_extend
        sh["extend"] = turned_extend(sh["extend"], clockwise)
    from notes.recolour import ANTI, CLOCKWISE, moved
    moved(sh, CLOCKWISE if clockwise else ANTI)  # (its Clumps areas turned with it: recolour.py FRAME)
    return sh
