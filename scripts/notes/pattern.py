"""Patterns along a curve: a formula (like a wave) laid along the curve, which stays as the dotted "origin path".

sh["pattern"] = {"preset": which preset it came from ("" = typed by hand), "formula": y of x for ONE loop (x goes
0 -> 1 along the loop, y = how far to the side, in keys), "vars": {name: number} for the other names in it (like
height), "loops": how many times the loop repeats, "each": on a joined curve, every piece gets all the loops
(else they run on across the pieces), "k": beats per key on screen when it was put on (sideways is worked out as
the piano roll looked then, like arcs), "mirror": the other side (flipping the shape), "scale": sideways size after
turning the shape (like tumours)}.

Sideways = to the left of the way the curve goes, so on a curve going forward in time, up."""

import functools
import math

import numpy as np

from files.lang import tr
from files.mathexpr import formula

LOOP_SAMPLES = 256  # points per loop (a multiple of 4, so a zigzag's tips are exact)
MAX_POINTS = 400000  # the most points a pattern makes (so a huge loop count can't hang the program)

# (id, name, formula, its numbers)
PATTERN_PRESETS = [
    ("wave", tr("pattern.wave"), "height * sin(x * 2 * pi)", {"height": 4.0}),
    ("zigzag", tr("pattern.zigzag"), "height * 2 / pi * asin(sin(x * 2 * pi))", {"height": 4.0}),
    ("bounce", tr("pattern.bounce"), "height * abs(sin(x * pi))", {"height": 4.0}),
]
PRESET_NAMES = {pid: name for pid, name, _, _ in PATTERN_PRESETS}
LOOPS_DEFAULT = 4.0


def new_pattern(preset, k, old=None):
    """A pattern from a preset. k: beats per key on screen. old: the pattern it replaces (its loops and how it
    runs over pieces stay)."""
    _, _, text, values = next(p for p in PATTERN_PRESETS if p[0] == preset)
    old = old or {}
    return {"preset": preset, "formula": text, "vars": dict(values), "loops": old.get("loops", LOOPS_DEFAULT),
            "each": old.get("each", False), "k": k, "mirror": False, "scale": 1.0}


def clean_pattern(p):
    """A pattern from a file made valid, or None."""
    if not isinstance(p, dict):
        return None
    try:
        fn = formula(str(p["formula"]), named=True)
        values = {str(a): float(b) for a, b in dict(p.get("vars", {})).items()}
        out = {"preset": str(p.get("preset", "")), "formula": str(p["formula"]),
               "vars": {n: values.get(n, 1.0) for n in fn.names}, "loops": float(p.get("loops", LOOPS_DEFAULT)),
               "each": bool(p.get("each", False)), "k": float(p.get("k", 1.0)), "mirror": bool(p.get("mirror")),
               "scale": float(p.get("scale", 1.0))}
    except (KeyError, TypeError, ValueError):
        return None
    if not (out["loops"] > 0 and out["k"] > 0 and math.isfinite(out["scale"])):
        return None
    return out


def pattern_name(p):
    return PRESET_NAMES.get(p.get("preset"), tr("pattern.custom"))


@functools.lru_cache(maxsize=256)
def _loop(text, values):
    fn = formula(text, named=True)
    vals = dict(values)
    u = np.linspace(0.0, 1.0, LOOP_SAMPLES + 1)
    v = []
    for x in u.tolist():
        try:
            y = fn(x, vals)
        except (ValueError, ArithmeticError, TypeError, KeyError):
            raise ValueError(tr("pattern.can_t_work_it_out_at", x=round(x, 3)))
        if not math.isfinite(y):
            raise ValueError(tr("pattern.can_t_work_it_out_at", x=round(x, 3)))
        v.append(y)
    return u, np.array(v)


def loop_points(p):
    """One loop as (along 0 -> 1, sideways in keys) arrays. ValueError if the formula can't be worked out."""
    u, v = _loop(p["formula"], tuple(sorted(p["vars"].items())))
    return u, v * (-p["scale"] if p["mirror"] else p["scale"])


def _normals(pts):
    """Unit vectors to the left of the path at each point (the average of the two stretches meeting there)."""
    d = np.diff(pts, axis=0)
    n = np.hypot(d[:, 0], d[:, 1])[:, None]
    d = np.where(n > 1e-12, d / np.where(n > 1e-12, n, 1.0), 0.0)
    t = np.vstack([d[:1], d[:-1] + d[1:], d[-1:]])
    n = np.hypot(t[:, 0], t[:, 1])[:, None]
    t = np.where(n > 1e-12, t / np.where(n > 1e-12, n, 1.0), 0.0)
    return np.column_stack([-t[:, 1], t[:, 0]])


def _lay(pts, lengths, s, v):
    """Points at distances s along the path (pts, with lengths = distance of each point), moved v to the side."""
    x = np.interp(s, lengths, pts[:, 0])
    y = np.interp(s, lengths, pts[:, 1])
    nrm = _normals(pts)
    nx, ny = np.interp(s, lengths, nrm[:, 0]), np.interp(s, lengths, nrm[:, 1])
    n = np.hypot(nx, ny)
    n = np.where(n > 1e-12, n, 1.0)
    return np.column_stack([x + nx / n * v, y + ny / n * v])


BAKE_TOLERANCE = 0.05  # how closely "Turn into plain curve" follows the pattern, in keys (as the piano roll looked)


def baked(sh):
    """The curve's pattern made into ordinary anchors and handles: {"pts", "sharp", "gaps"} (tumours aren't
    baked in: they stay a setting of the curve)."""
    from notes.arc import line_bezier
    from notes.bezier import anchor_count, fit
    from notes.joined import is_joined, joined_paths
    plain = dict(sh, tumour=None, tumours=None, splits=None)
    if is_joined(plain):
        paths = joined_paths(plain, None)
    else:
        from notes.bezier import sample
        paths = pattern_paths([sample([tuple(q) for q in sh["pts"]], 240)], sh["pattern"])
    k = sh["pattern"]["k"]
    pts, sharp, gaps = None, [], []
    for path in paths:
        corners = []
        got = [[b * k, q] for b, q in fit([(b / k, q) for b, q in path], BAKE_TOLERANCE, corners)]
        if pts is None:
            pts, sharp = got, corners
            continue
        last = anchor_count(pts) - 1
        gaps.append(last)
        pts += line_bezier(pts[-1], got[0])[1:3] + got
        sharp += [a + last + 1 for a in corners]
    return {"pts": pts, "sharp": sharp, "gaps": gaps}


def pattern_paths(paths, p):
    """The pattern laid along each path ((beat, pitch) point lists: a curve's pieces), as (beat, pitch) lists.
    Unchanged if the formula can't be worked out."""
    try:
        u, v = loop_points(p)
    except ValueError:
        return paths
    k = p["k"]
    screens = [np.asarray(path, float).reshape(-1, 2) / [k, 1.0] for path in paths]
    lengths = [np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(a, axis=0).T))]) if len(a) > 1 else np.zeros(1)
               for a in screens]
    if p["each"]:
        spans = [(0.0, ln[-1], p["loops"]) for ln in lengths]
    else:
        total = sum(ln[-1] for ln in lengths)
        starts = np.concatenate([[0.0], np.cumsum([ln[-1] for ln in lengths])])
        spans = [(starts[j], total, p["loops"]) for j in range(len(lengths))]
    out = []
    for a, ln, (offset, total, loops) in zip(screens, lengths, spans):
        size = ln[-1]
        if len(a) < 2 or size <= 1e-12 or total <= 1e-12:
            out.append([tuple(q) for q in (a * [k, 1.0]).tolist()])
            continue
        # the loops that reach this piece, with enough points per loop to follow the path's own bends too
        first = math.floor(offset / total * loops)
        last = math.ceil((offset + size) / total * loops)
        need = len(a) / max(1e-9, loops * size / total)  # the path's own points per loop
        per = max(LOOP_SAMPLES, math.ceil(need / LOOP_SAMPLES) * LOOP_SAMPLES)
        per = min(per, max(8, MAX_POINTS // max(1, last - first)))
        t = np.linspace(0.0, 1.0, per + 1)
        uu, vv = np.interp(t, np.linspace(0.0, 1.0, len(u)), u), np.interp(t, np.linspace(0.0, 1.0, len(v)), v)
        count = last - first
        if count * per > MAX_POINTS:
            count = MAX_POINTS // per
        loop_no = np.repeat(np.arange(first, first + count), per)
        along = np.tile(uu[:-1], count) + loop_no
        side = np.tile(vv[:-1], count)
        along = np.append(along, first + count - 1 + uu[-1])
        side = np.append(side, vv[-1])
        s = along / loops * total - offset  # distance along this piece
        inside = np.nonzero((s >= 0) & (s <= size))[0]
        if not len(inside):
            out.append([tuple(q) for q in (a * [k, 1.0]).tolist()])
            continue
        i0, i1 = inside[0], inside[-1]
        s_run, v_run = list(s[i0:i1 + 1]), list(side[i0:i1 + 1])
        if i0 > 0 and s[i0] > 0:  # exactly from the piece's start ...
            f = (0 - s[i0 - 1]) / (s[i0] - s[i0 - 1])
            s_run.insert(0, 0.0)
            v_run.insert(0, side[i0 - 1] + (side[i0] - side[i0 - 1]) * f)
        if i1 + 1 < len(s) and s[i1] < size:  # ... to its end
            f = (size - s[i1]) / (s[i1 + 1] - s[i1])
            s_run.append(size)
            v_run.append(side[i1] + (side[i1 + 1] - side[i1]) * f)
        got = _lay(a, ln, np.array(s_run), np.array(v_run)) * [k, 1.0]
        out.append([tuple(q) for q in got.tolist()])
    return out
