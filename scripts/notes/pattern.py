"""Patterns along a curve: a formula (like a wave) laid along the curve, which stays as the dotted "origin path".

sh["pattern"] = {"preset": which preset it came from ("" = typed by hand), "formula": y of x for ONE loop (x goes
0 -> 1 along the loop, y = how far to the side, in keys), "vars": {name: number} for the other names in it (like
height), maybe "along": a formula of x moving the line forward / back along the curve, in loops (going back makes
curls; none = 0), "loops": how many times the loop repeats, "each": on a joined curve, every piece gets all the loops
(else they run on across the pieces), "k": beats per key on screen when it was put on (sideways is worked out as
the piano roll looked then, like arcs), "mirror": the other side (flipping the shape), "scale": sideways size after
turning the shape (like tumours)}, and maybe "name" (a saved pattern's) and "loop" = the loop edited by hand as a
curve {"pts": anchors + handles in (along 0 -> 1, sideways in keys), "sharp": corners} (bezier.py): it's used instead
of the formula, which stays so the loop can go back to it. Maybe "graphs": {"loops" or a number's name: [[u, f],
...]} (u 0 -> 1 along the line, f = multiplier, like tumours' graphs): "loops" = how crowded the loops are there
(loop_warp; the count stays).
Both a pattern and a shape may have "sym" = symmetric halves picked by the user: "mirror" (the second half is the
first half mirrored, like an arch) or "turn" (turned half-way round, like an S); only the formula's first half counts.

Sideways = to the left of the way the curve goes, so on a curve going forward in time, up."""

import functools
import math

import numpy as np

from files.lang import tr
from files.mathexpr import formula, without_lead

LOOP_SAMPLES = 256  # points per loop (a multiple of 4, so a zigzag's tips are exact)
MAX_POINTS = 400000  # the most points a pattern makes (so a huge loop count can't hang the program)
MAX_LOOPS = MAX_POINTS // 8  # the most loops (8 points a loop at least, so each keeps its shape; user: the Loops
                             # box stops there, more used to stop the line short of its end)

# (id, name, formula, its numbers)
PATTERN_PRESETS = [
    ("wave", tr("pattern.wave"), "height * sin(x * 2 * pi)", {"height": 4.0}),
    ("zigzag", tr("pattern.zigzag"), "height * 2 / pi * asin(sin(x * 2 * pi))", {"height": 4.0}),
    ("bounce", tr("pattern.bounce"), "height * abs(sin(x * pi))", {"height": 4.0}),
    ("square", tr("pattern.square_wave"), "height * max(-1, min(1, steep * sin(x * 2 * pi)))",
     {"height": 4.0, "steep": 8.0}),
    ("saw", tr("pattern.sawtooth"), "height * 2 * (x - floor(x + 0.5))", {"height": 4.0}),
    ("teeth", tr("pattern.teeth"), "height * (1 - abs(2 * x - 1))", {"height": 4.0}),
    ("spikes", tr("pattern.spikes"), "height * (1 - abs(2 * x - 1))^sharp", {"height": 4.0, "sharp": 3.0}),
    ("bumps", tr("pattern.half_circles"), "height * sqrt(max(0, 1 - (2 * x - 1)^2))", {"height": 4.0}),
    ("stairs", tr("pattern.stair_steps"), "height * min(steps, floor((1 - abs(2 * x - 1)) * (steps + 1))) / steps",
     {"height": 4.0, "steps": 3.0}),
    ("swell", tr("pattern.swell"), "height * sin(x * pi) * sin(x * waves * 2 * pi)", {"height": 4.0, "waves": 4.0}),
    ("fade", tr("pattern.fading_wave"), "height * (1 - x) * sin(x * waves * 2 * pi)", {"height": 4.0, "waves": 4.0}),
    ("curls", tr("pattern.curls"), "height * (1 - cos(x * 2 * pi)) / 2", {"height": 4.0, "curl": 2.0}),
]
PRESET_NAMES = {pid: name for pid, name, _, _ in PATTERN_PRESETS}
# presets that also move the line along the curve (p["along"]); over 1, "curl" makes it run back: loops
PRESET_ALONG = {"curls": "curl * sin(x * 2 * pi) / (2 * pi)"}
LOOPS_DEFAULT = 4.0


SYMS = ("mirror", "turn")  # a formula's symmetric halves (see the top)


def keep_sym(new, old):
    """new with old's symmetric halves and Flatten / Softness (picking another preset keeps them; from a file:
    made valid)."""
    old = old or {}
    if old.get("sym") in SYMS:
        new["sym"] = old["sym"]
    for key in ("flatten", "soft"):
        try:
            v = float(old.get(key, 0.0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and (v > 0 or key == "soft" and key in old):
            new[key] = max(0.0, min(100.0, v))
    return new


SOFT_DEFAULT = 50.0  # % Softness shown before one is typed


def flattened(side, peak, flatten, soft):
    """Bump values (side: keys, + or -) pressed flat like a balloon on a wall: the wall at (100 - flatten) % of the
    loop's peak, both ways; soft % rounds where the flat top starts (0 = a hard cut with corners, 100 = as round as
    the room allows: the curve reaches the wall right at the peak)."""
    cap = (1 - flatten / 100) * peak
    x = np.abs(side)
    w = soft / 100 * min(cap, peak - cap)
    if w <= 1e-12:
        out = np.minimum(x, cap)
    else:  # (a smooth knee from cap - w to cap + w)
        out = np.where(x <= cap - w, x, np.where(x >= cap + w, cap, x - (x - (cap - w)) ** 2 / (4 * w)))
    return np.sign(side) * out


def new_pattern(preset, k, old=None):
    """A pattern from a preset. k: beats per key on screen. old: the pattern it replaces (its loops, how it runs
    over pieces, its symmetric halves and its graphs that still fit stay)."""
    _, _, text, values = next(p for p in PATTERN_PRESETS if p[0] == preset)
    old = old or {}
    out = {"preset": preset, "formula": text, "vars": dict(values), "loops": old.get("loops", LOOPS_DEFAULT),
           "each": old.get("each", False), "k": k, "mirror": False, "scale": 1.0}
    if preset in PRESET_ALONG:
        out["along"] = PRESET_ALONG[preset]
    graphs = clean_pattern_graphs(old.get("graphs"), values)  # (the Loops graph, and numbers it still has)
    if graphs:
        out["graphs"] = graphs
    return keep_sym(out, old)


def pattern_names(text, along=""):
    """The number names in a pattern's formula and its along formula (ValueError if one can't be read)."""
    names = formula(text, named=True).names
    if along.strip():
        names = names + [n for n in formula(along, named=True).names if n not in names]
    if "loops" in names:  # (its box would be the Loops box: that number could never be changed)
        raise ValueError(tr("pattern.loops_taken"))
    return names


def clean_loop(c):
    """A loop edited by hand from a file made valid, or None."""
    try:
        pts = [[float(a), float(b)] for a, b in c["pts"]]
        n = len(pts)
        if n < 4 or (n - 1) % 3 or not all(math.isfinite(a) for q in pts for a in q):
            return None
        out = {"pts": pts}
        sharp = sorted({int(a) for a in c.get("sharp", ()) if 0 < int(a) < (n - 1) // 3})
        if sharp:
            out["sharp"] = sharp
        if c.get("sym") in ("mirror", "turn", "flip") and (n - 1) % 6 == 0:  # symmetric halves (bezier.py)
            out["sym"] = c["sym"]
        return out
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def clean_pattern(p):
    """A pattern from a file made valid, or None."""
    if not isinstance(p, dict):
        return None
    loop = clean_loop(p["loop"]) if isinstance(p.get("loop"), dict) else None
    try:
        values = {str(a): float(b) for a, b in dict(p.get("vars", {})).items()}
        try:
            text, along = str(p.get("formula", "")), str(p.get("along", ""))
            names = pattern_names(text, along)
        except ValueError:
            if not loop:
                return None
            text, along, names = "", "", []  # (a loop drawn by hand needs no formula)
        out = {"preset": str(p.get("preset", "")), "formula": text,
               "vars": {n: values.get(n, 1.0) for n in names}, "loops": min(float(p.get("loops", LOOPS_DEFAULT)), MAX_LOOPS),
               "each": bool(p.get("each", False)), "k": float(p.get("k", 1.0)), "mirror": bool(p.get("mirror")),
               "scale": float(p.get("scale", 1.0))}
    except (KeyError, TypeError, ValueError):
        return None
    if not (out["loops"] > 0 and out["k"] > 0 and math.isfinite(out["scale"])):
        return None
    if along.strip():
        out["along"] = along
    if p.get("name"):
        out["name"] = str(p["name"])
    if loop:
        out["loop"] = loop
    graphs = clean_pattern_graphs(p.get("graphs"), out["vars"])
    if graphs:
        out["graphs"] = graphs
    return keep_sym(out, p)


def clean_pattern_graphs(graphs, values):
    """A pattern's graphs from a file ({"loops" or a number's name: [[u, f], ...]}, see the top), only those it can
    use; None if none are left."""
    from notes.tumour import clean_graph
    if not isinstance(graphs, dict):
        return None
    out = {key: g for key in ("loops", *values) if (g := clean_graph(graphs.get(key)))}
    return out or None


def pattern_name(p):
    """A saved pattern's name (as it was saved, even drawn by hand), else the preset's, "(edited by hand)" once its
    loop has been changed by hand."""
    if p.get("name"):
        return p["name"]
    name = PRESET_NAMES.get(p.get("preset"), tr("pattern.custom"))
    return tr("pattern.edited", name=name) if p.get("loop") else name


def loop_pieces(holder):
    """How many pieces a pattern on holder runs along: a polygon's sides (holder = its settings, polygon.py), a
    joined curve's pieces, else 1."""
    if "points" in holder and "style" in holder:  # (straight from the settings: a star's sides are twice its
        return holder["points"] * (2 if holder["style"] == "star" else 1)  # points; a crossing star's loops
    return len(holder.get("gaps") or ()) + 1                               # together have one side a point)


def most_loops(holder, each=None, layer="pattern"):
    """The most Loops a pattern (layer: a polygon's second one too) on holder can have: MAX_LOOPS in all, so with
    Each piece shared by the pieces (each piece gets all the loops: 50,000 on 200 sides made 80 million points)."""
    if each is None:
        each = (holder.get(layer) or {}).get("each", False)
    return MAX_LOOPS if not each else max(1, MAX_LOOPS // loop_pieces(holder))


FORMULA_KINDS = ("curve", "line", "poly", "arc")  # the piano roll's shapes that can have formulas


def origin_paths(sh):
    """A piano roll shape's path(s) before its formulas (the dashed origin path): a line's / polyline's points (one
    path, round its corners), an arc's points, a curve's (a joined curve: one per piece)."""
    from notes.bezier import anchor_count, sample
    pts = [tuple(q) for q in sh["pts"]]
    if sh["kind"] in ("line", "poly"):
        return [pts]
    if sh["kind"] == "arc":
        from notes.arc import arc_points
        return [arc_points(pts, sh.get("k", 1.0))]
    paths, a0 = [], 0
    for a1 in list(sh.get("gaps", [])) + [anchor_count(pts) - 1]:
        paths.append(sample(pts[3 * a0:3 * a1 + 1], 240))
        a0 = a1 + 1
    return paths


def loop_length(sh):
    """How long one loop of the curve's pattern is, in keys as the piano roll looked when it was put on (the first
    piece's, with Each piece), along its shape if it has one."""
    p = sh["pattern"]
    paths = origin_paths(sh)
    if sh.get("shape"):
        paths = shape_paths(paths, sh["shape"])
    lengths = [float(np.hypot(*np.diff(np.asarray(a, float) / [p["k"], 1.0], axis=0).T).sum()) for a in paths]
    return (lengths[0] if p["each"] else sum(lengths)) / p["loops"]


@functools.lru_cache(maxsize=256)
def _loop(text, values, sym, along=""):
    fns = [formula(text, named=True)] + ([formula(along, named=True)] if along.strip() else [])
    vals = dict(values)
    u = np.linspace(0.0, 1.0, LOOP_SAMPLES + 1)
    half = LOOP_SAMPLES // 2
    xs = u.tolist()[:half + 1] if sym else u.tolist()  # (symmetric: the first half makes the second)
    got = []
    for fn in fns:
        out = []
        for x in xs:
            try:
                y = fn(x, vals)
            except (ValueError, ArithmeticError, TypeError, KeyError):
                raise ValueError(tr("pattern.can_t_work_it_out_at", x=round(x, 3)))
            if not math.isfinite(y):
                raise ValueError(tr("pattern.can_t_work_it_out_at", x=round(x, 3)))
            out.append(y)
        got.append(np.array(out))
    v = got[0]
    if sym == "mirror":  # across the up-and-down line through the middle
        v = np.concatenate([v, v[-2::-1]])
    elif sym == "turn":  # turned half-way round the middle point
        v = np.concatenate([v, 2 * v[-1] - v[-2::-1]])
    if len(got) > 1:  # moved along: the second half runs the other way (both ways round the middle)
        a = got[1]
        if sym:
            a = np.concatenate([a, -a[-2::-1]])
        u = u + a
    return u, v


def formula_loop(p):
    """One loop of the pattern's formula as (along 0 -> 1, sideways in keys) arrays (not mirrored / scaled), with
    its symmetric halves. ValueError if it can't be worked out."""
    return _loop(p["formula"], tuple(sorted(p["vars"].items())), p.get("sym") if p.get("sym") in SYMS else None,
                 p.get("along", ""))


def loop_points(p):
    """One loop as (along 0 -> 1, sideways in keys) arrays: the loop edited by hand, else the formula's. ValueError
    if the formula can't be worked out."""
    if p.get("loop"):
        from notes.bezier import sample
        a = np.asarray(sample([tuple(q) for q in p["loop"]["pts"]], 64), float)
        u, v = a[:, 0], a[:, 1]
    else:
        u, v = formula_loop(p)
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
    """Points at distances s along the path (pts, with lengths = distance of each point), moved v to the side.
    Before its start / past its end (a shape reaching further, like a spiral): straight on the way it goes there."""
    x = np.interp(s, lengths, pts[:, 0])
    y = np.interp(s, lengths, pts[:, 1])
    nrm = _normals(pts)
    nx, ny = np.interp(s, lengths, nrm[:, 0]), np.interp(s, lengths, nrm[:, 1])
    n = np.hypot(nx, ny)
    n = np.where(n > 1e-12, n, 1.0)
    nx, ny = nx / n, ny / n
    for end, beyond in ((0, s < 0), (-1, s > lengths[-1])):
        if beyond.any():
            over = s[beyond] - lengths[end]
            tx, ty = nrm[end, 1], -nrm[end, 0]  # (the way the path goes there)
            x[beyond] += tx * over
            y[beyond] += ty * over
    return np.column_stack([x + nx * v, y + ny * v])


# ---------------------------------------------------------------- shapes of the curve
# sh["shape"] = {"preset", "x", "y": formulas of t (0 -> 1) drawing the shape, "vars", "k", "mirror", maybe "name"
# and "loop" (edited by hand, like a pattern's)}. The drawing is fitted to the curve: an open shape (its ends apart)
# is turned and sized so it starts at the curve's start and ends at its end; a closed one (like a circle) is as wide
# as from the start to the end and sits centred on that line, starting at its leftmost point. Then it's laid along
# the curve (the origin path) like a pattern, sizes measured along it: on a straight curve it's exact, bending the
# curve bends it. A pattern then runs along the shape.

SHAPE_SAMPLES = 1024  # points along a shape
# ends nearer than this share of its size = a closed shape. A root of sin's leftover 1e-16 at t = 1 (Flower's
# sharpness 3: 1e-4) made one "open", stretched 10,000 times between A and B (froze); ends that near are never meant
# to be stretched apart (1,000 times or more).
CLOSED_SHARE = 1e-3


# A shape may be typed as ONE polar line r(t) (p["polar"], the distance from the middle, once round as t goes over
# its span, usually 0 -> 1):
# its x / y are always written from it (polar_xy), so everything else only ever reads x / y. Typing x / y by hand
# drops it (the two ways can't both be there). Every preset starts as x / y (user 2026-11-16); Flower's are written
# with polar_xy, so picking Polar finds its r line (polar_of).
def polar_xy(r, span=None):
    """The x(t), y(t) texts of the polar line r(t): once round while t goes over its span (t_span's texts [from, to];
    None = 0 -> 1, the usual way)."""
    r = without_lead(r)  # (a pasted "r = ..." would end up inside the brackets)
    if span is None:
        turn = "t * 2 * pi"
    elif span[0].strip() == "0":
        turn = f"t / ({span[1]}) * 2 * pi"
    else:
        turn = f"(t - ({span[0]})) / (({span[1]}) - ({span[0]})) * 2 * pi"
    return f"({r}) * cos({turn})", f"({r}) * sin({turn})"


# t's span (user 2026-11-16: formulas from websites often run t from 0 to 2 pi): p["t_span"] = [from, to] as typed
# (like "2pi"), missing = 0 -> 1. Presets never have one (picking a preset drops it).
def span_numbers(span):
    """[from, to] texts -> (from, to) numbers; ValueError if one can't be read or they're the same."""
    try:
        lo, hi = (float(formula(str(s), var="\0")(0.0)) for s in span)
    except (TypeError, ValueError, ArithmeticError):
        raise ValueError(tr("pattern.t_span_bad"))
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo == hi:
        raise ValueError(tr("pattern.t_span_bad"))
    return lo, hi


def t_span(p):
    """A shape's span texts [from, to], or None (0 -> 1)."""
    s = p.get("t_span")
    return [str(s[0]), str(s[1])] if isinstance(s, (list, tuple)) and len(s) == 2 else None


def polar_ok(r):
    """r is a polar line that reads as a formula ON ITS OWN (inside polar_xy's brackets "1) + (2" worked too)."""
    if not isinstance(r, str):
        return False
    try:
        formula(r, named=True, var="t")
        return True
    except ValueError:
        return False


def polar_of(x, y, span=None):
    """The polar line x(t), y(t) were written from (polar_xy, with that span), or None."""
    tail = polar_xy("", span)[0][2:]  # (" * cos(...)" after the r line's brackets)
    if not (x.startswith("(") and x.endswith(tail)):
        return None
    r = x[1:len(x) - len(tail) - 1]
    return r if polar_xy(r, span) == (x, y) and polar_ok(r) else None


# Flower: sharpness 0 = the plain cos wave (1 - 2 * (1 - cos) / 2 = cos), plus = pointed tips (1 = straight-sided
# star: (1 - cos) / 2 = sin^2, ^(1/2) = abs(sin)), on to thin spikes; minus = fatter, rounder petals (user
# 2026-11-16: one box both ways). Written with cos, not abs(sin): sin(5 pi) leaves 1e-16, a root of it (sharpness
# 3+) opened the shape's ends; cos at its top is exactly 1.
FLOWER_R = "1 + depth * (1 - 2 * ((1 - cos(petals * t * 2 * pi)) / 2)^(2^(-sharpness)))"
# (id, name, x(t), y(t), its numbers)
SHAPE_PRESETS = [
    ("circle", tr("pattern.circle"), "cos(t * 2 * pi)", "sin(t * 2 * pi)", {}),
    ("spiral", tr("pattern.spiral"), "t * cos(t * turns * 2 * pi)", "t * sin(t * turns * 2 * pi)", {"turns": 3.0}),
    ("flower", tr("pattern.flower"), *polar_xy(FLOWER_R), {"petals": 5.0, "depth": 0.3, "sharpness": 0.0}),
    ("heart", tr("pattern.heart"), "16 * sin(t * 2 * pi)^3",
     "13 * cos(t * 2 * pi) - 5 * cos(4 * pi * t) - 2 * cos(6 * pi * t) - cos(8 * pi * t)", {}),
    ("figure8", tr("pattern.figure8"), "sin(t * 2 * pi)", "sin(t * 2 * pi) * cos(t * 2 * pi)", {}),
    ("slow", tr("pattern.slow_start"), "t", "t^bend", {"bend": 2.0}),
    ("fast", tr("pattern.fast_start"), "t", "1 - (1 - t)^bend", {"bend": 2.0}),
    ("scurve", tr("pattern.s_curve"), "t", "t^steep / (t^steep + (1 - t)^steep)", {"steep": 2.0}),
    ("reverse_s", tr("pattern.reverse_s"), "t^steep / (t^steep + (1 - t)^steep)", "t", {"steep": 2.0}),
    ("quarter", tr("pattern.quarter_circle"), "sin(t * pi / 2)", "1 - cos(t * pi / 2)", {}),
    ("exponential", tr("pattern.exponential"), "t", "(exp(steep * t) - 1) / (exp(steep) - 1)", {"steep": 5.0}),
    ("logarithmic", tr("pattern.logarithmic"), "t", "ln(1 + steep * t) / ln(1 + steep)", {"steep": 20.0}),
]
SHAPE_NAMES = {sid: name for sid, name, _, _, _ in SHAPE_PRESETS}


def new_shape(preset, k, old=None):
    """A shape from a preset (old: the one it replaces, its symmetric halves stay)."""
    _, _, x, y, values = next(p for p in SHAPE_PRESETS if p[0] == preset)
    return keep_sym({"preset": preset, "x": x, "y": y, "vars": dict(values), "k": k, "mirror": False}, old)


def shape_names(text_x, text_y):
    """The number names in a shape's two formulas (ValueError if one can't be read)."""
    names = formula(text_x, named=True, var="t").names
    return names + [n for n in formula(text_y, named=True, var="t").names if n not in names]


def clean_shape_formula(p):
    """A shape of the curve from a file made valid, or None."""
    if not isinstance(p, dict):
        return None
    loop = clean_loop(p["loop"]) if isinstance(p.get("loop"), dict) else None
    try:
        values = {str(a): float(b) for a, b in dict(p.get("vars", {})).items()}
        try:
            tx, ty = str(p.get("x", "")), str(p.get("y", ""))
            span = t_span(p)
            if span:
                try:
                    span_numbers(span)
                except ValueError:
                    span = None  # (a broken span: the usual 0 -> 1)
            if isinstance(p.get("polar"), str):  # (its x / y always come from the polar line)
                if not polar_ok(p["polar"]):
                    raise ValueError
                tx, ty = polar_xy(p["polar"], span)
            names = shape_names(tx, ty)
        except ValueError:
            if not loop:
                return None
            tx, ty, names, span = "", "", [], None
        out = {"preset": str(p.get("preset", "")), "x": tx, "y": ty, "vars": {n: values.get(n, 1.0) for n in names},
               "k": float(p.get("k", 1.0)), "mirror": bool(p.get("mirror"))}
    except (KeyError, TypeError, ValueError):
        return None
    if not out["k"] > 0:
        return None
    if p.get("name"):
        out["name"] = str(p["name"])
    if isinstance(p.get("polar"), str) and tx:
        out["polar"] = p["polar"]
    if span:
        out["t_span"] = span
    if loop:
        out["loop"] = loop
    return keep_sym(out, p)


def shape_name(p):
    if p.get("name"):
        return p["name"]
    name = SHAPE_NAMES.get(p.get("preset"), tr("pattern.custom"))
    return tr("pattern.edited", name=name) if p.get("loop") else name


@functools.lru_cache(maxsize=256)
def _shape(text_x, text_y, values, sym, lo=0.0, hi=1.0):
    fx, fy = formula(text_x, named=True, var="t"), formula(text_y, named=True, var="t")
    vals = dict(values)
    pts = []
    for t in np.linspace(lo, hi, SHAPE_SAMPLES + 1).tolist():
        try:
            x, y = fx(t, vals), fy(t, vals)
        except (ValueError, ArithmeticError, TypeError, KeyError):
            raise ValueError(tr("pattern.can_t_work_it_out_at_t", t=round(t, 3)))
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(tr("pattern.can_t_work_it_out_at_t", t=round(t, 3)))
        pts.append((x, y))
    a = np.array(pts)
    return symmetric_shape(a, sym) if sym else fitted_shape(a)


def symmetric_shape(a, sym):
    """A drawing (x, y rows, SHAPE_SAMPLES + 1 of them) with its second half made from its first (see the top),
    fitted. A closed drawing stays closed: its first half is mirrored across the line from its start to its middle
    point, or turned round the point halfway between them. An open one is fitted first (from A to B), then mirrored
    across the up-and-down line through its middle point, or turned round that point, and fitted again."""
    half = SHAPE_SAMPLES // 2
    size = math.hypot(*(a.max(axis=0) - a.min(axis=0)))
    if size > 1e-12 and math.dist(a[0], a[-1]) < CLOSED_SHARE * size:
        first, (p, m) = a[:half + 1], (a[0], a[half])
        if sym == "turn":
            second = p + m - first[1:]
        else:
            d = m - p
            ln = math.hypot(*d)
            if ln < 1e-9 * size:  # (its middle is its start: nothing to mirror across)
                return fitted_shape(a)
            d = d / ln
            q = first[half - 1::-1] - p
            second = p + 2 * np.outer(q @ d, d) - q
        return fitted_shape(np.vstack([first, second]))
    u, v = fitted_shape(a)
    u, v = u[:half + 1], v[:half + 1]
    mu, mv = u[-1], v[-1]
    if sym == "turn":
        u2, v2 = 2 * mu - u[-2::-1], 2 * mv - v[-2::-1]
    else:
        u2, v2 = 2 * mu - u[-2::-1], v[-2::-1]
    return fitted_shape(np.column_stack([np.concatenate([u, u2]), np.concatenate([v, v2])]))


def fitted_shape(a):
    """A drawing (x, y rows) fitted to a curve from (0, 0) to (1, 0) (see the top): (along, sideways) arrays."""
    lo, hi = a.min(axis=0), a.max(axis=0)
    size = math.hypot(*(hi - lo))
    if size < 1e-12:
        raise ValueError(tr("pattern.it_s_just_a_dot"))
    if math.dist(a[0], a[-1]) < CLOSED_SHARE * size:  # closed: from its leftmost point, as wide as the curve is long
        at = int(a[:-1, 0].argmin())
        a = np.vstack([a[at:-1], a[:at + 1]])
        width = hi[0] - lo[0]
        if width < 1e-9 * size:
            raise ValueError(tr("pattern.it_s_just_a_dot"))
        return (a[:, 0] - lo[0]) / width, (a[:, 1] - (lo[1] + hi[1]) / 2) / width
    d = a[-1] - a[0]  # open: turned and sized so it starts at (0, 0) and ends at (1, 0)
    length = math.hypot(*d)
    c, s = d / length / length
    q = a - a[0]
    return q[:, 0] * c + q[:, 1] * s, q[:, 1] * c - q[:, 0] * s


def formula_shape(p):
    """The shape's formulas fitted (see fitted_shape), not mirrored. ValueError if it can't be worked out."""
    span = t_span(p)
    lo, hi = span_numbers(span) if span else (0.0, 1.0)
    return _shape(p["x"], p["y"], tuple(sorted(p["vars"].items())), p.get("sym") if p.get("sym") in SYMS else None,
                  lo, hi)


def shape_points(p):
    """The shape as (along, sideways) arrays in the curve's length: edited by hand, else the formulas'."""
    if p.get("loop"):
        from notes.bezier import sample
        a = np.asarray(sample([tuple(q) for q in p["loop"]["pts"]], 64), float)
        u, v = a[:, 0], a[:, 1]
    else:
        u, v = formula_shape(p)
    return u, -v if p["mirror"] else v


def shape_paths(paths, p):
    """The shape laid along each path (each piece of a joined curve gets it); unchanged if it can't be worked
    out."""
    try:
        u, v = shape_points(p)
    except ValueError:
        return paths
    k = p["k"]
    out = []
    for path in paths:
        a = np.asarray(path, float).reshape(-1, 2) / [k, 1.0]
        if len(a) < 2:
            out.append(path)
            continue
        ln = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(a, axis=0).T))])
        if ln[-1] <= 1e-12:
            out.append(path)
            continue
        got = _lay(a, ln, u * ln[-1], v * ln[-1]) * [k, 1.0]
        out.append([tuple(q) for q in got.tolist()])
    return out


def formed_paths(paths, sh):
    """The curve's pieces (paths) with its shape, then its pattern, on them."""
    if sh.get("shape"):
        paths = shape_paths(paths, sh["shape"])
    if sh.get("pattern"):
        paths = pattern_paths(paths, sh["pattern"])
    return paths


def formed_path(path, holder):
    """One path (a drawer stroke's, a funnel curve's) with the shape / pattern of holder (the stroke / curve dict)
    on it. holder["rev"]: they run from the path's end (a funnel curve turned end to end)."""
    if not (holder.get("shape") or holder.get("pattern")):
        return path
    if holder.get("rev"):
        return formed_paths([path[::-1]], holder)[0][::-1]
    return formed_paths([path], holder)[0]


def has_formula(holder):
    return bool(holder.get("shape") or holder.get("pattern"))


def moved_formulas(holder, fn):
    """After holder's points were all moved by fn(x, y) -> (x, y) (straight lines staying straight: moved,
    stretched, flipped, turned, or into another box), its formulas' k / scale / mirror changed to match, so they
    look the same on it (exactly, unless it was slanted). k: how many x one y is where they look round (for the
    piano roll: beats per key on screen)."""
    if not has_formula(holder):
        return
    o = np.asarray(fn(0.0, 0.0), float)
    a = np.column_stack([np.asarray(fn(1.0, 0.0), float) - o, np.asarray(fn(0.0, 1.0), float) - o])
    for layer in ("shape", "pattern", "pattern2"):  # (pattern2: a polygon stroke's second pattern)
        p = holder.get(layer)
        if not p:
            continue
        b = a @ np.diag([p["k"], 1.0])  # from where it's round now to the new points
        r1, r2 = math.hypot(*b[0]), math.hypot(*b[1])
        if r1 < 1e-12 or r2 < 1e-12:
            continue
        k = r1 / r2
        det = float(np.linalg.det(np.diag([1 / k, 1.0]) @ b))  # ... and on to where it's round there
        new = dict(p, k=k, mirror=p["mirror"] != (det < 0))
        if layer != "shape":
            new["scale"] = p["scale"] * math.sqrt(abs(det))
        holder[layer] = new


BAKE_TOLERANCE = 0.05  # how closely "Turn into plain curve" follows the pattern, in keys (as the piano roll looked)
BAKE_SHARE = 0.001  # the same for a drawer stroke / funnel curve, as a share of its size
# a polygon's sides: closer (dense tall stairs / spikes lost whole keys at BAKE_SHARE; measured with
# dev/scratch/bake_count.py + bake_tight.py: 40 tall spikes a side = at most ~1,400 anchors, under 1 s)
POLYGON_BAKE_SHARE = 0.0001


BAKE_SYM = ("mirror", "turn")  # symmetric halves a baked curve gets when it has them (bezier.fit_symmetric)


def baked_path(holder, pts, modes=BAKE_SYM, formed=None, share=BAKE_SHARE):
    """A drawer stroke's / funnel curve's formulas made into ordinary anchors and handles: (pts, sharp, sym).
    pts: its curve's points; or formed: the path it makes with them (a line / polyline / arc stroke). share: how
    closely it follows, as a share of its size."""
    from notes.bezier import fit_symmetric, sample
    path = formed if formed is not None else formed_path(sample([tuple(q) for q in pts], 240), holder)
    k = (holder.get("pattern") or holder["shape"])["k"]
    a = np.asarray(path, float) / [k, 1.0]
    size = max(1e-9, float(np.hypot(*(a.max(axis=0) - a.min(axis=0)))))
    corners = []
    got, sym = fit_symmetric([tuple(q) for q in a.tolist()], share * size, corners, modes)
    return [[x * k, y] for x, y in got], corners, sym


def baked(sh):
    """The curve's shape / pattern made into ordinary anchors and handles: {"pts", "sharp", "gaps", "sym"} (tumours
    aren't baked in: they stay a setting of the curve). One piece with symmetric halves keeps them ("sym")."""
    from notes.arc import line_bezier
    from notes.bezier import anchor_count, fit_symmetric
    from notes.joined import is_joined, joined_paths
    plain = dict(sh, tumour=None, tumours=None, splits=None)
    if sh["kind"] == "curve" and is_joined(plain):
        paths = joined_paths(plain, None)
    else:
        paths = formed_paths(origin_paths(dict(sh, gaps=[])), sh)
    k = (sh.get("pattern") or sh["shape"])["k"]
    pts, sharp, gaps, sym = None, [], [], None
    for path in paths:
        corners = []
        got, sym = fit_symmetric([(b / k, q) for b, q in path], BAKE_TOLERANCE, corners,
                                 BAKE_SYM if len(paths) == 1 else ())
        got = [[b * k, q] for b, q in got]
        if pts is None:
            pts, sharp = got, corners
            continue
        last = anchor_count(pts) - 1
        gaps.append(last)
        pts += line_bezier(pts[-1], got[0])[1:3] + got
        sharp += [a + last + 1 for a in corners]
    return {"pts": pts, "sharp": sharp, "gaps": gaps, "sym": sym}


def bake_shape(sh):
    """Turn into plain curve, in place: a line / arc / curve shape's formulas become ordinary anchors and handles (a
    line / an arc becomes a curve; a joined curve's tumours one setting, as the tumour window shows them)."""
    from notes.joined import shown_tumour
    got = baked(sh)
    tm = shown_tumour(sh)
    for key in ("shape", "pattern", "sym", "tumours", "splits", "gaps", "sharp", "k"):
        sh.pop(key, None)
    sh["kind"] = "curve"
    sh["pts"] = got["pts"]
    if got["sharp"]:
        sh["sharp"] = got["sharp"]
    if got["gaps"]:
        sh["gaps"] = got["gaps"]
    if got["sym"]:
        sh["sym"] = got["sym"]
    if tm:
        sh["tumour"] = tm


WARP_STEPS = 8192  # the Loops graph's table along the line (plus the graph's own points)


def loop_warp(p):
    """The Loops graph (p["graphs"]["loops"]: how crowded the loops are along the line, 0 = none there) as a table
    (u along the line 0 -> 1, share of the loops before u 0 -> 1), or None: no graph, or 0 everywhere (ignored)."""
    g = (p.get("graphs") or {}).get("loops")
    if not g:
        return None
    gu, gf = np.array([q[0] for q in g], float), np.array([q[1] for q in g], float)
    u = np.unique(np.concatenate([np.linspace(0.0, 1.0, WARP_STEPS + 1), gu]))
    d = np.maximum(np.interp(u, gu, gf), 0.0)  # (below 0 = none)
    share = np.concatenate([[0.0], np.cumsum((d[1:] + d[:-1]) / 2 * np.diff(u))])
    if share[-1] <= 1e-12:
        return None
    return u, share / share[-1]


def warp_share(warp, u):
    """The share of the loops before u along the line."""
    return u if warp is None else float(np.interp(u, warp[0], warp[1]))


def warp_place(warp, q):
    """Where along the line (0 -> 1) the loops reach share q: where none are (0 %), a share lands at the far end of
    that stretch (a loop starts after it) or just before it (the last one ends there). Past 0 / 1 (an along formula
    running off the ends): straight on."""
    us, sh = warp
    q = np.asarray(q, float)
    inner = np.clip(q, 0.0, 1.0 - 1e-12)
    i = np.clip(np.searchsorted(sh, inner, side="right"), 1, len(sh) - 1)
    d = sh[i] - sh[i - 1]
    f = np.where(d > 0, (inner - sh[i - 1]) / np.where(d > 0, d, 1.0), 1.0)
    out = us[i - 1] + f * (us[i] - us[i - 1])
    out = np.where(q >= 1.0, out + (q - 1.0), out)
    return np.where(q < 0.0, out + q, out)


def number_graphs(p):
    """The pattern's numbers that follow a graph: {name: (u, f) arrays} (none on a loop edited by hand: it has no
    numbers)."""
    if p.get("loop"):
        return {}
    gs = p.get("graphs") or {}
    return {n: (np.array([q[0] for q in gs[n]], float), np.array([q[1] for q in gs[n]], float))
            for n in p["vars"] if gs.get(n)}


@functools.lru_cache(maxsize=256)
def _height_scales(text, values, sym, along):
    """Does height only scale the loop sideways (height * ..., like every preset)? Then its graph changes it
    smoothly along the line; else (a formula of your own) once per loop, like the other numbers."""
    vals = dict(values)
    try:
        u1, v1 = _loop(text, tuple(sorted(dict(vals, height=1.0).items())), sym, along)
        u2, v2 = _loop(text, tuple(sorted(dict(vals, height=2.0).items())), sym, along)
    except ValueError:
        return False
    return bool(np.allclose(u1, u2, rtol=0, atol=1e-9) and np.allclose(v2, 2 * v1, rtol=1e-9, atol=1e-9))


def smooth_height(p, graphs):
    """The Height graph changes the side sizes smoothly (see _height_scales)."""
    return "height" in graphs and _height_scales(p["formula"], tuple(sorted(p["vars"].items())),
                                                 p.get("sym") if p.get("sym") in SYMS else None, p.get("along", ""))


def loops_with_values(p, x, values, keep_bad=False):
    """Many loops at once, each with its own numbers: x (loops, samples) along each loop 0 -> 1, values {name:
    number or (loops, 1) array}. (along, side) arrays like loop_points' (symmetric halves, mirrored / scaled).
    ValueError if the formula can't be worked out (keep_bad: NaN at those places instead)."""
    sym = p.get("sym") if p.get("sym") in SYMS else None
    second = x > 0.5
    xh = np.where(second, 1.0 - x, x) if sym else x  # (symmetric: the first half makes the second)
    v = formula(p["formula"], named=True).arrays(xh, values, keep_bad)
    if sym == "turn":
        mid = formula(p["formula"], named=True).arrays(np.full((x.shape[0], 1), 0.5), values, keep_bad)
        v = np.where(second, 2 * mid - v, v)
    u = x
    if p.get("along", "").strip():
        a = formula(p["along"], named=True).arrays(xh, values, keep_bad)
        u = x + (np.where(second, -a, a) if sym else a)
    return u, v * (-p["scale"] if p["mirror"] else p["scale"])


def bad_loops(p, x, values):
    """loops_with_values where loops the formula can't work out with their numbers (a graph took one somewhere it
    can't go) are a plain stretch of line: (along, side, how many were left plain)."""
    uu, vv = loops_with_values(p, x, values, keep_bad=True)
    bad = ~(np.isfinite(uu).all(axis=1) & np.isfinite(vv).all(axis=1))
    if bad.any():
        uu, vv = np.array(uu, float), np.array(vv, float)
        uu[bad], vv[bad] = x[bad], 0.0
    return uu, vv, int(bad.sum())


def graph_trouble(p, key, pts):
    """Would the number key following graph pts (or the pattern's own graphs, pts None) leave loops plain somewhere
    (the formula can't be worked out with the number there)? Checked at 401 places along the line."""
    gs = dict(p.get("graphs") or {})
    if pts is not None:
        gs[key] = pts
    p = dict(p, graphs=gs)
    graphs = number_graphs(p)
    if not graphs:
        return False
    at = np.linspace(0.0, 1.0, 401)
    values = dict(p["vars"])
    for name, (gu, gf) in graphs.items():
        values[name] = (p["vars"][name] * np.interp(at, gu, gf))[:, None]
    x = np.broadcast_to(np.linspace(0.0, 1.0, LOOP_SAMPLES + 1), (len(at), LOOP_SAMPLES + 1))
    try:
        return bad_loops(p, x, values)[2] > 0
    except ValueError:
        return False  # (no good with any numbers: the box says so)


def _plain_stretches(s_run, v_run, ln, size):
    """With a Loops graph: the line runs plain from the piece's start / to its end where no loops are (0 % there),
    and the path's own points go in (a long plain or spread-out stretch still follows the path's bends)."""
    if s_run[0] > 0:
        s_run, v_run = [0.0] + s_run, [v_run[0]] + v_run
    if s_run[-1] < size:
        s_run, v_run = s_run + [size], v_run + [v_run[-1]]
    s, v = np.array(s_run), np.array(v_run)
    if len(s) < 2:
        return s, v
    if np.any(np.diff(s) < 0):  # (an along formula running back, Curls: only where nothing else runs over it)
        return _clear_stretches(s, v, ln)
    extra = ln[(ln > s[0]) & (ln < s[-1])]
    extra = extra[~np.isin(extra, s)]
    if not len(extra):
        return s, v
    s_all = np.concatenate([s, extra])
    v_all = np.concatenate([v, np.interp(extra, s, v)])
    order = np.argsort(s_all, kind="stable")
    return s_all[order], v_all[order]


def _clear_stretches(s, v, ln):
    """_plain_stretches when the line runs back over itself somewhere: the path's own points go in only between two
    neighbours that nothing else reaches between (everything before is behind, everything after ahead: a plain
    stretch where the loops are 0 %), so the line's order stays."""
    d = np.diff(s)
    behind = np.maximum.accumulate(s)[:-1] <= s[:-1]
    ahead = np.minimum.accumulate(s[::-1])[::-1][1:] >= s[1:]
    clear = np.nonzero((d > 0) & behind & ahead)[0]
    lo, hi = np.searchsorted(ln, s[clear], "right"), np.searchsorted(ln, s[clear + 1], "left")
    at, add_s, add_v = [], [], []
    for i, a, b in zip(clear[hi > lo], lo[hi > lo], hi[hi > lo]):  # (at most one a path point)
        extra = ln[a:b]
        at.append(np.full(len(extra), i + 1))
        add_s.append(extra)
        add_v.append(v[i] + (v[i + 1] - v[i]) * (extra - s[i]) / d[i])
    if not at:
        return s, v
    at = np.concatenate(at)
    return np.insert(s, at, np.concatenate(add_s)), np.insert(v, at, np.concatenate(add_v))


def pattern_paths(paths, p, arrays=False):
    """The pattern laid along each path ((beat, pitch) point lists: a curve's pieces), as (beat, pitch) lists.
    Unchanged if the formula can't be worked out. A Loops graph moves the loops along the line (their count stays):
    over each piece with Each piece, else over the whole line. arrays: each piece as an (n, 2) array instead (a
    second pattern laid on top next: no lists of tuples in between)."""
    try:
        u, v = loop_points(p)
    except ValueError:
        return paths
    warp = loop_warp(p)
    peak = float(np.max(np.abs(v))) if len(v) else 0.0
    graphs = number_graphs(p)
    height = graphs.pop("height") if smooth_height(p, graphs) else None  # (the rest: one value per loop)
    k = p["k"]
    screens = [np.asarray(path, float).reshape(-1, 2) / [k, 1.0] for path in paths]
    lengths = [np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(a, axis=0).T))]) if len(a) > 1 else np.zeros(1)
               for a in screens]
    if p["each"]:  # (MAX_LOOPS shared by the pieces, see most_loops: from a file / pieces added since)
        loops = min(p["loops"], max(1, MAX_LOOPS // max(1, len(paths))))
        spans = [(0.0, ln[-1], loops) for ln in lengths]
    else:
        total = sum(ln[-1] for ln in lengths)
        starts = np.concatenate([[0.0], np.cumsum([ln[-1] for ln in lengths])])
        spans = [(starts[j], total, p["loops"]) for j in range(len(lengths))]
    # MAX_POINTS shared by every piece: the loops they all touch (each piece had MAX_POINTS of its own)
    touched = sum(math.ceil(warp_share(warp, (o + ln[-1]) / t) * n) - math.floor(warp_share(warp, o / t) * n)
                  for ln, (o, t, n) in zip(lengths, spans) if t > 1e-12)
    out = []
    for a, ln, (offset, total, loops) in zip(screens, lengths, spans):
        size = ln[-1]
        if len(a) < 2 or size <= 1e-12 or total <= 1e-12:
            out.append(a * [k, 1.0] if arrays else [tuple(q) for q in (a * [k, 1.0]).tolist()])
            continue
        # the loops that reach this piece, with enough points per loop to follow the path's own bends too
        first = math.floor(warp_share(warp, offset / total) * loops)
        last = math.ceil(warp_share(warp, (offset + size) / total) * loops)
        need = len(a) / max(1e-9, loops * size / total)  # the path's own points per loop
        per = max(LOOP_SAMPLES, math.ceil(need / LOOP_SAMPLES) * LOOP_SAMPLES)
        per = min(per, max(8, MAX_POINTS // max(1, touched)))
        t = np.linspace(0.0, 1.0, per + 1)
        uu, vv = np.interp(t, np.linspace(0.0, 1.0, len(u)), u), np.interp(t, np.linspace(0.0, 1.0, len(v)), v)
        count = last - first
        if count * per > MAX_POINTS + per:  # (a piece can touch one loop more than its share)
            count = MAX_POINTS // per + 1
        if count <= 0:  # (no loops on this piece at all: a Loops graph at 0 % all over it)
            out.append(a * [k, 1.0] if arrays else [tuple(q) for q in (a * [k, 1.0]).tolist()])
            continue
        if graphs:  # each loop with its numbers where its middle is
            nos = np.arange(first, first + count, dtype=float)
            mids = (nos + 0.5) / loops
            at = mids if warp is None else warp_place(warp, mids)
            values = dict(p["vars"])
            for name, (gu, gf) in graphs.items():
                values[name] = (p["vars"][name] * np.interp(at, gu, gf))[:, None]
            try:
                uu2, vv2, _ = bad_loops(p, np.broadcast_to(t, (count, per + 1)), values)
            except ValueError:
                return paths
            along = (uu2[:, :-1] + nos[:, None]).ravel()
            side = vv2[:, :-1].ravel()
            along = np.append(along, nos[-1] + uu2[-1, -1])
            side = np.append(side, vv2[-1, -1])
        else:
            loop_no = np.repeat(np.arange(first, first + count), per)
            along = np.tile(uu[:-1], count) + loop_no
            side = np.tile(vv[:-1], count)
            along = np.append(along, first + count - 1 + uu[-1])
            side = np.append(side, vv[-1])
        q = along / loops
        s = (q if warp is None else warp_place(warp, q)) * total - offset  # distance along this piece
        if height is not None:  # (smoothly, where each point is along the line)
            side = side * np.interp((s + offset) / total, *height)
        if p.get("flatten"):  # (the wall stays where the plain loop's peak puts it: taller graphed loops press more)
            side = flattened(side, peak, p["flatten"], p.get("soft", SOFT_DEFAULT))
        inside = np.nonzero((s >= 0) & (s <= size))[0]
        if not len(inside):
            out.append(a * [k, 1.0] if arrays else [tuple(q) for q in (a * [k, 1.0]).tolist()])
            continue
        i0, i1 = inside[0], inside[-1]
        s_run, v_run = s[i0:i1 + 1], side[i0:i1 + 1]
        if i0 > 0 and s[i0] > 0:  # exactly from the piece's start ...
            f = (0 - s[i0 - 1]) / (s[i0] - s[i0 - 1])
            s_run = np.concatenate(([0.0], s_run))
            v_run = np.concatenate(([side[i0 - 1] + (side[i0] - side[i0 - 1]) * f], v_run))
        if i1 + 1 < len(s) and s[i1] < size:  # ... to its end
            f = (size - s[i1]) / (s[i1 + 1] - s[i1])
            s_run = np.append(s_run, size)
            v_run = np.append(v_run, side[i1] + (side[i1 + 1] - side[i1]) * f)
        if warp is not None:
            s_run, v_run = _plain_stretches(list(s_run), list(v_run), ln, size)
        got = _lay(a, ln, np.asarray(s_run, float), np.asarray(v_run, float)) * [k, 1.0]
        out.append(got if arrays else [tuple(q) for q in got.tolist()])
    return out
