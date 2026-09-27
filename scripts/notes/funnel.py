"""Funnels: lines leading to a wall, opening along curves, filled with spam or long notes."""

import bisect
import math

import numpy as np

from notes.bezier import anchor_count, fit, handle_anchor, sample
from notes.custom import row_spans
from notes.paths import EDGE, TOP_KEY, pitch_of

# ---------------------------------------------------------------- funnels
# sh["pts"] = [line start, line end, wall end 1, wall end 2, (line 2 start, line 2 end, ...)]: straight lines,
# drawn any way you like (while the funnel is being drawn it has only the line). Extra lines lead to the same
# wall. The grid of notes runs from the first line's start towards the wall; when the wall comes first in time
# it's a reverse funnel.
# sh["starts"] = [{"line": which line, "at": 0..1 along it, "ends": [the curve to wall end 1 or None,
# ... end 2]}]: each start opens a curve to each wall end (a half funnel has just one; a wall end on the line
# gets none).
# A curve lives in its own slanted box: from its start S, U runs along the line and V along the wall, so
# S + U + V = the wall end and S + U is where the line meets the wall. A point [u, f] in the box is S + u*U + f*V
# (u = 0 at the start, 1 at the wall; f = how far open), so moving, stretching, flipping and turning the two
# lines takes the curves along.
# A curve = {"pts": a Bézier curve in its box (bezier.py: anchors with handles) from
# [0, 0] (the start) to [1, 1] (the wall end), "sharp": anchors whose two handles move separately (a corner),
# "link": group number, "flip": bool}. Linked curves change together; "flip" = turned end to end compared to the
# rest of its group (same "flip" = the same [u, f], which for the two curves of one start looks mirrored).
# (The first versions had "bends" the curve went through; old projects are converted when they load.)
# Every key plays while it's inside a curve's area (between the curve, the line and the wall) or on the line,
# the wall or a curve.
# fill: "spam" = back-to-back notes whose gate goes from gate0 (at the line start) to gate1 (at the wall), the
# same note grid on every key so the columns line up; "long" = one note per key per stretch.
# vary: False = one gate (gate1 is kept equal to gate0), True = a different start and wall gate.
# change: "smooth" = every note its own gate, "steps" = gates only halve/double (gate0, gate0/2, ...).
# follow: the gate changes evenly over "time", or with the "curve" (as the funnel opens: how many keys play).
# wall: "in" = the notes stop at the wall, "past" = one more column on the other side of it (a normal funnel:
# the wall notes start on the wall; a reverse funnel: they end on it).

FUNNEL_FILLS = ("spam", "long")
GATE_CHANGES = ("steps", "smooth")
GATE_FOLLOWS = ("time", "curve")
WALL_MODES = ("in", "past")
FUNNEL_DEFAULTS = {"fill": "spam", "gate0": 0.0625, "gate1": 0.0625, "vary": False, "change": "steps",
                   "follow": "time", "wall": "in"}
FUNNEL_BEND = [0.75, 0.2]  # the first versions' default curve
MAX_BENDS = 32


def clean_funnel(sh):
    """The funnel settings of sh (not its lines or curves), made valid."""
    out = {}
    for key, choices in (("fill", FUNNEL_FILLS), ("change", GATE_CHANGES), ("follow", GATE_FOLLOWS),
                         ("wall", WALL_MODES)):
        out[key] = sh.get(key) if sh.get(key) in choices else FUNNEL_DEFAULTS[key]
    for key in ("gate0", "gate1"):
        out[key] = max(1e-6, float(sh.get(key, FUNNEL_DEFAULTS[key])))
    # older files have no "vary": two different gates there were meant to vary
    out["vary"] = bool(sh["vary"]) if "vary" in sh else abs(out["gate0"] - out["gate1"]) > 1e-9
    if not out["vary"]:
        out["gate1"] = out["gate0"]
    return out


def clean_starts(starts, lines):
    out = []
    old_twins = []
    for st in starts or []:
        raw = (list(st.get("ends") or []) + [None, None])[:2]
        ends = [clean_curve(c) for c in raw]
        line = int(st.get("line", 0))
        if any(ends) and 0 <= line < lines:
            out.append({"line": line, "at": min(1.0, max(0.0, float(st.get("at", 0)))), "ends": ends})
            if all(ends) and any(isinstance(c, list) for c in raw):
                old_twins.append(ends)  # the old versions always kept a start's two curves alike
    for ends in old_twins:
        link = next_link({"starts": out})
        for c in ends:
            c["link"], c["flip"] = link, False
    return sorted(out, key=lambda st: (st.get("line", 0), st["at"]))


def clean_curve(c):
    """A curve from a file made valid (None stays None); old versions' bends become anchors + handles."""
    if not c:
        return None
    if isinstance(c, list):
        return new_curve(fit(old_curve_points(clean_bends(c))))
    pts = [[float(u), float(f)] for u, f in c.get("pts", [])]
    if len(pts) < 4 or (len(pts) - 1) % 3:
        return new_curve()
    pts[0], pts[-1] = [0.0, 0.0], [1.0, 1.0]
    n = anchor_count(pts)
    out = {"pts": pts, "sharp": sorted({int(a) for a in c.get("sharp", ()) if 0 < int(a) < n - 1})}
    if c.get("link") is not None:
        out["link"], out["flip"] = int(c["link"]), bool(c.get("flip"))
    return out


def new_curve(pts=None):
    return {"pts": [list(p) for p in (pts or DEFAULT_CURVE)], "sharp": []}


def all_curves(sh):
    """(start number, wall end, curve) of every curve."""
    return [(k, end, c) for k, st in enumerate(sh.get("starts", ())) for end, c in enumerate(st["ends"]) if c]


def next_link(sh):
    return 1 + max((c["link"] for _, _, c in all_curves(sh) if c.get("link") is not None), default=0)


def partners(sh, k, end):
    """[(start, wall end, flipped)] of the curves linked to this one (flipped: turned end to end compared to it)."""
    c = sh["starts"][k]["ends"][end]
    if not c or c.get("link") is None:
        return []
    return [(k2, e2, c2.get("flip", False) != c.get("flip", False)) for k2, e2, c2 in all_curves(sh)
            if (k2, e2) != (k, end) and c2.get("link") == c["link"]]


def turned(pts):
    """A curve's points turned end to end (its steep part moves to the other end)."""
    return [[1 - u, 1 - f] for u, f in reversed(pts)]


def turned_curve(c, flip=True):
    """The curve as its partner gets it: turned end to end when flip, otherwise the same."""
    if not flip:
        return {"pts": [list(p) for p in c["pts"]], "sharp": list(c["sharp"])}
    n = anchor_count(c["pts"])
    return {"pts": turned(c["pts"]), "sharp": sorted(n - 1 - a for a in c["sharp"])}


def inside_out(c):
    """The curve flipped inside out: bulging the other way (slow start <-> fast start, S <-> reverse S)."""
    return {"pts": [[f, u] for u, f in c["pts"]], "sharp": list(c["sharp"])}


def set_shape(c, shape):
    """Give curve c the points / corners of shape (a curve dict), keeping its link."""
    c["pts"] = [list(p) for p in shape["pts"]]
    c["sharp"] = list(shape["sharp"])


# ---------------------------------------------------------------- the first version's bends (for old projects)

def clean_bends(bends):
    out = []
    for u, f in sorted(clamp_bend(u, f) for u, f in bends)[:MAX_BENDS]:
        if not out or u > out[-1][0] + 1e-4:
            out.append([u, f])
    return out


def old_curve_points(bends):
    """[(u, f)] along a curve of the old bends: one bend = a 1/x curve through it, more = a monotone cubic."""
    us = {i / 400 for i in range(401)}
    if len(bends) == 1:
        us |= {funnel_u(bends[0], i / 400) for i in range(401)}
        return [(u, funnel_f(bends[0], u)) for u in sorted(us)]
    fn = _smooth_curve([0.0] + [u for u, _ in bends] + [1.0], [0.0] + [f for _, f in bends] + [1.0])
    return [(u, fn(u)) for u in sorted(us)]


def old_funnel(sh):
    """A funnel from the first version ([start, wall top] + "sides") -> its line + wall, and its start."""
    (b0, p0), (b1, p1) = sh["pts"]
    bend = clamp_bend(*(sh.get("bend") or FUNNEL_BEND))
    if sh.get("sides") == "one":
        return [[b0, p0], [b1, p0], [b1, p1], [b1, p0]], [{"line": 0, "at": 0.0, "ends": [[bend], None]}]
    h = abs(p1 - p0)
    return [[b0, p0], [b1, p0], [b1, p0 + h], [b1, p0 - h]], [{"line": 0, "at": 0.0, "ends": [[bend], [bend]]}]


def clamp_bend(u, f):
    return [min(0.99, max(0.01, float(u))), min(0.999, max(0.001, float(f)))]


def _bend_curve(bend):
    """(a, mirrored) of the 1/x curve through the bend point, or None when it's a straight line."""
    u, f = clamp_bend(*bend)
    if abs(u - f) < 1e-4:
        return None
    if f < u:
        return f * (1 - u) / (u - f), False
    return (1 - f) * u / (f - u), True


def funnel_f(bend, u):
    """How far open (0..1) a one-bend curve is at u (0 = start, 1 = wall)."""
    c = _bend_curve(bend)
    if c is None:
        return u
    a, mirrored = c
    if mirrored:
        u = 1 - u
    y = a * u / (1 + a - u)
    return 1 - y if mirrored else y


def funnel_u(bend, y):
    """Where (0..1) a one-bend curve is y open (the other way round from funnel_f)."""
    c = _bend_curve(bend)
    if c is None:
        return y
    a, mirrored = c
    if mirrored:
        y = 1 - y
    u = y * (1 + a) / (a + y) if a + y > 0 else 0.0
    return 1 - u if mirrored else u


def _smooth_curve(xs, ys):
    """Monotone cubic through the points (Fritsch-Carlson): smooth, and never overshoots them."""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def end_slope(h0, h1, d0, d1):
        s = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if s * d0 <= 0:
            return 0.0
        if d0 * d1 <= 0 and abs(s) > 3 * abs(d0):
            return 3 * d0
        return s

    m[0] = end_slope(h[0], h[1], d[0], d[1])
    m[-1] = end_slope(h[-1], h[-2], d[-1], d[-2])

    def fn(u):
        i = min(max(bisect.bisect_right(xs, u) - 1, 0), n - 2)
        t = (u - xs[i]) / h[i]
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
                + (3 * t2 - 2 * t3) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    return fn


# ---------------------------------------------------------------- curve shapes (presets, formulas)
# A formula is y of x, x going 0 -> 1 from the curve's start (A) to its wall end (B); it's stretched so it starts
# at 0 and ends at 1, and becomes anchors + handles that follow it (so it can still be dragged afterwards).
CURVE_PRESETS = [  # (name, formula; None = the default curve)
    ("Default", None),
    ("Straight", "x"),
    ("Slow start (x²)", "x^2"),
    ("Slower start (x³)", "x^3"),
    ("Very slow start (x⁵)", "x^5"),
    ("Fast start (x² flipped)", "1-(1-x)^2"),
    ("Faster start (x³ flipped)", "1-(1-x)^3"),
    ("S-curve (slow, fast, slow)", "x*x*(3-2*x)"),
    ("Steep S-curve", "x^3*(x*(6*x-15)+10)"),
    ("Reverse S (fast, slow, fast)", "0.5-sin(asin(1-2*x)/3)"),
    ("Quarter circle (slow start)", "1-sqrt(1-x^2)"),
    ("Quarter circle (fast start)", "sqrt(1-(1-x)^2)"),
    ("Exponential", "exp(5*x)"),
    ("Logarithmic", "ln(1+20*x)"),
]
FIT_TOLERANCE = 0.003  # how close (part of the curve's size) the anchors + handles follow a formula


def formula_curve(fn, n=400):
    """[(x, y)] of a formula at n+1 even steps, stretched so y goes 0 -> 1 (ValueError if it can't be)."""
    pts = []
    for i in range(n + 1):
        x = i / n
        try:
            y = float(fn(x))
        except (ValueError, ArithmeticError, TypeError):
            raise ValueError(f"it can't be worked out at x = {x:g}")
        if not math.isfinite(y):
            raise ValueError(f"it can't be worked out at x = {x:g}")
        pts.append((x, y))
    y0, y1 = pts[0][1], pts[-1][1]
    if abs(y1 - y0) < 1e-12:
        raise ValueError("it has to end at a different height than it starts (x = 0 and x = 1)")
    return [(x, (y - y0) / (y1 - y0)) for x, y in pts]


def preset_curve(formula_fn):
    """A curve (dict, no link) for a formula function (None = the default curve)."""
    if formula_fn is None:
        return new_curve()
    pts = fit(formula_curve(formula_fn), FIT_TOLERANCE)
    pts[0], pts[-1] = [0.0, 0.0], [1.0, 1.0]
    return new_curve(pts)


def remove_funnel_parts(sh, lines, curves):
    """Take lines (numbers) and curves ((start, wall end)) out of a funnel. Curves starting on a removed line go
    with it; the next line takes over as the first. False if no line would be left (remove the funnel)."""
    old = funnel_lines(sh)
    keep = [n for n in range(len(old)) if n not in lines]
    if not keep:
        return False
    for k, end in curves:
        sh["starts"][k]["ends"][end] = None
    new_number = {n: i for i, n in enumerate(keep)}
    sh["pts"] = ([list(p) for p in old[keep[0]]] + [list(p) for p in sh["pts"][2:4]]
                 + [list(p) for n in keep[1:] for p in old[n]])
    sh["starts"] = [dict(st, line=new_number[st.get("line", 0)]) for st in sh["starts"]
                    if st.get("line", 0) in new_number and any(st["ends"])]
    return True


def funnel_lines(sh):
    """[(start, end)] of every line of the funnel (not the wall)."""
    pts = [tuple(p) for p in sh["pts"]]
    return [(pts[0], pts[1])] + [(pts[i], pts[i + 1]) for i in range(4, len(pts) - 1, 2)]


def line_index(line):
    """Where line number `line` starts in sh["pts"]."""
    return 0 if line == 0 else 2 + 2 * line


def start_point(sh, at, line=0):
    i = line_index(line)
    (b0, p0), (b1, p1) = sh["pts"][i:i + 2]
    return b0 + (b1 - b0) * at, p0 + (p1 - p0) * at


def curve_box(sh, at, end, line=0):
    """(S, U, V) of the curve from the start at `at` along line `line` to wall end `end` (0 or 1), or None when
    that wall end is on the line (nothing to open). S + u*U + f*V is the curve point at (u, f)."""
    if len(sh["pts"]) < 4:
        return None
    s = start_point(sh, at, line)
    i = line_index(line)
    (b0, p0), (b1, p1) = sh["pts"][i:i + 2]
    w0, w1 = sh["pts"][2:4]
    e = sh["pts"][2 + end]
    d1, d2 = (b1 - b0, p1 - p0), (w1[0] - w0[0], w1[1] - w0[1])
    ex, ey = e[0] - s[0], e[1] - s[1]
    det = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(det) > 1e-6 * math.hypot(*d1) * math.hypot(*d2) > 0:
        x, y = (ex * d2[1] - ey * d2[0]) / det, (d1[0] * ey - d1[1] * ex) / det
        u, v = (x * d1[0], x * d1[1]), (y * d2[0], y * d2[1])
    else:  # the line and the wall are parallel (or a point): plain time / pitch box
        u, v = (ex, 0.0), (0.0, ey)
    if abs(u[0] * v[1] - u[1] * v[0]) < 1e-9:
        return None
    return s, u, v


def box_point(box, u, f):
    (sb, sp), (ub, up), (vb, vp) = box
    return sb + u * ub + f * vb, sp + u * up + f * vp


def box_uf(box, b, p):
    """(u, f) of the point (beat, pitch) in a curve's box."""
    (sb, sp), (ub, up), (vb, vp) = box
    det = ub * vp - up * vb
    x, y = b - sb, p - sp
    return (x * vp - y * vb) / det, (ub * y - up * x) / det


def funnel_curves(sh, short=False):
    """[(start index, wall end, [(beat, pitch) from the start to the wall end], where the line meets the wall)]
    short: each curve stops just inside its last key, so that key is only reached on the wall (for notes that
    start on the wall, like a line's "starts exactly on the last point")."""
    out = []
    for k, st in enumerate(sh.get("starts", ())):
        for end, c in enumerate(st["ends"]):
            box = curve_box(sh, st["at"], end, st.get("line", 0)) if c else None
            if box and short and abs(box[2][1]) > 2 * EDGE:
                s_, u_, v_ = box
                k_ = 1 - EDGE / abs(v_[1])
                box = s_, u_, (v_[0] * k_, v_[1] * k_)
            if box:
                corner = box_point(box, 1, 0)
                out.append((k, end, [box_point(box, u, f) for u, f in sample(c["pts"], 64)], corner))
    return out


def new_start(sh, at, line=0):
    """A start at `at` along a line, curving to both wall ends (or None if neither can open). Its two curves come
    linked (mirrored on the two sides of the line)."""
    ends = [new_curve() if curve_box(sh, at, end, line) else None for end in (0, 1)]
    if all(ends):
        link = next_link(sh)
        for c in ends:
            c["link"], c["flip"] = link, False
    return {"line": line, "at": at, "ends": ends} if any(ends) else None


def funnel_handles(sh):
    """[(beat, pitch, id)]: ("start", k) for every start, ("ctrl", k, wall end, i) for every handle point that's
    pulled out of its anchor, ("anchor", k, wall end, i) for the anchors between the ends (i = point number)."""
    out, ctrls, anchors = [], [], []
    for k, st in enumerate(sh.get("starts", ())):
        out.append((*start_point(sh, st["at"], st.get("line", 0)), ("start", k)))
        for end, c in enumerate(st["ends"]):
            box = curve_box(sh, st["at"], end, st.get("line", 0)) if c else None
            if not box:
                continue
            pts = c["pts"]
            for i, (u, f) in enumerate(pts):
                if i % 3 and pts[i] != pts[handle_anchor(i)]:
                    ctrls.append((*box_point(box, u, f), ("ctrl", k, end, i)))
                elif i % 3 == 0 and 0 < i < len(pts) - 1:
                    anchors.append((*box_point(box, u, f), ("anchor", k, end, i)))
    return out + ctrls + anchors


def funnel_handle_lines(sh):
    """[(anchor, handle point, (start, wall end))], points as (beat, pitch): the handle lines to draw, and whose
    curve they are."""
    out = []
    for k, end, c in all_curves(sh):
        st = sh["starts"][k]
        box = curve_box(sh, st["at"], end, st.get("line", 0))
        if box:
            pts = c["pts"]
            out += [(box_point(box, *pts[handle_anchor(i)]), box_point(box, *pts[i]), (k, end))
                    for i in range(len(pts)) if i % 3 and pts[i] != pts[handle_anchor(i)]]
    return out


def funnel_strokes(sh):
    """The lines, the wall and every curve, as (beat, pitch) polylines."""
    return [list(seg) for seg in funnel_segments(sh)] + [c for _, _, c, _ in funnel_curves(sh)]


def funnel_segments(sh):
    """The straight parts: every line, then the wall."""
    pts = [tuple(p) for p in sh["pts"]]
    return funnel_lines(sh)[:1] + ([(pts[2], pts[3])] if len(pts) >= 4 else []) + funnel_lines(sh)[1:]


def funnel_polys(sh, short=False):
    """The area of every curve: the curve, then back along the wall and the line."""
    return [curve + [corner, curve[0]] for _, _, curve, corner in funnel_curves(sh, short)]


def funnel_contains(sh, b, p):
    """Is (beat, pitch) inside one of the funnel's curve areas?"""
    for poly in funnel_polys(sh):
        inside = False
        for (xa, ya), (xb, yb) in zip(poly, poly[1:]):
            if (ya <= p) != (yb <= p) and b < xa + (xb - xa) * (p - ya) / (yb - ya):
                inside = not inside
        if inside:
            return True
    return False


def line_band(a, b, q):
    """(first beat, last beat) where the straight line a -> b is on key q, or None."""
    (ta, ya), (tb, yb) = a, b
    if ya == yb:
        return (min(ta, tb), max(ta, tb)) if pitch_of(ya) == q else None
    u0, u1 = sorted(((q - 0.5 - ya) / (yb - ya), (q + 0.5 - ya) / (yb - ya)))
    u0, u1 = max(u0, 0.0), min(u1, 1.0)
    if u0 > u1:
        return None
    return tuple(sorted((ta + (tb - ta) * u0, ta + (tb - ta) * u1)))


def _keys(ps):
    return range(max(0, pitch_of(min(ps))), min(TOP_KEY, pitch_of(max(ps))) + 1)


def funnel_key_spans(sh):
    """{key: [[first beat, last beat], ...]} where each key plays (sorted, overlapping stretches merged)."""
    pieces = {}
    for a, b in funnel_segments(sh):
        for q in _keys((a[1], b[1])):
            s = line_band(a, b, q)
            if s:
                pieces.setdefault(q, []).append(s)
    for poly in funnel_polys(sh, sh.get("wall") == "past"):
        for q in _keys([p for _, p in poly]):
            pieces.setdefault(q, []).extend(tuple(s) for s in row_spans([poly], q))
    out = {}
    for q, spans in pieces.items():
        merged = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1] + 1e-9:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        if merged:
            out[q] = merged
    return out


def funnel_axis(sh, spans):
    """(t0, sign) in beats: the note grid starts at the line start and runs towards the wall (sign -1 =
    backwards in time: reverse funnel), and how far (beats) the wall is from the start."""
    pts = sh["pts"]
    ref = (pts[2][0] + pts[3][0]) / 2 if len(pts) >= 4 else pts[1][0]
    if abs(ref - pts[0][0]) > 1e-9:
        return pts[0][0], (1 if ref > pts[0][0] else -1), abs(ref - pts[0][0])
    lo = min(a for s in spans.values() for a, _ in s)
    hi = max(b for s in spans.values() for _, b in s)
    if hi - lo < 1e-9:  # just an upright line (the wall, drawn first): a column of wall gate ending on it
        return lo, -1, sh["gate1"]
    return lo, 1, hi - lo  # the line and the wall start at the same time (turned funnel): left to right


def funnel_reversed(sh):
    """Does the wall come before the line start in time?"""
    pts = sh["pts"]
    return len(pts) >= 4 and (pts[2][0] + pts[3][0]) / 2 < pts[0][0] - 1e-9


def funnel_layout(sh, ppq):
    """Everything the notes come from: (spans in grid distance, wall ranges, grid, t0 tick, sign), where a grid
    distance is ticks from the line start towards the wall. None if the funnel makes no notes."""
    spans = funnel_key_spans(sh)
    if not spans:
        return None
    t0, sign, length = funnel_axis(sh, spans)
    t0 *= ppq

    def dist(beat):
        return sign * (beat * ppq - t0)

    dspans = {q: [sorted((dist(a), dist(b))) for a, b in s] for q, s in spans.items()}
    walls = {}
    if len(sh["pts"]) >= 4:
        w0, w1 = (tuple(p) for p in sh["pts"][2:4])
        for q in _keys((w0[1], w1[1])):
            s = line_band(w0, w1, q)
            if s:
                walls[q] = sorted((dist(s[0]), dist(s[1])))
    return dspans, walls, max(length * ppq, 1.0), t0, sign


def funnel_openness(dspans):
    """w(d): 0..1, how many keys play at grid distance d (1 key = 0, the most keys = 1)."""
    events = sorted([(a, 1) for s in dspans.values() for a, _ in s] + [(b, -1) for s in dspans.values() for _, b in s])
    xs, counts, c = [], [], 0
    for x, step in events:
        c += step
        if xs and xs[-1] == x:
            counts[-1] = c
        else:
            xs.append(x)
            counts.append(c)
    top = max(counts, default=0)

    def w(d):
        i = bisect.bisect_right(xs, d) - 1
        n = counts[i] if i >= 0 else 0
        return min(1.0, max(0.0, (n - 1) / (top - 1))) if top > 1 else 0.0
    return w


def funnel_gate(sh, g0, g1, w):
    """The gate (ticks) of a note that far (w: 0 = start gate, 1 = wall gate) through the funnel."""
    g = g0 * (g1 / g0) ** w
    if sh["change"] == "steps" and g0 != g1:
        g = g0 * 2.0 ** round(math.log2(g / g0))
        g = min(max(g, min(g0, g1)), max(g0, g1))
    return max(g, 1.0)


def funnel_grid(sh, ppq, dspans, length):
    """The note grid every key of a spam funnel shares, as grid distances: from the line start to the wall
    (a leftover under half a gate joins the last note), then on past everything plus one more note."""
    g0, g1 = sh["gate0"] * ppq, sh["gate1"] * ppq
    opened = funnel_openness(dspans) if sh["follow"] == "curve" else None

    def gate(d):
        if d >= length:
            return funnel_gate(sh, g0, g1, 1.0)
        w = min(1.0, max(0.0, d / length)) if opened is None else opened(d)
        return funnel_gate(sh, g0, g1, w)

    lo = min(a for s in dspans.values() for a, _ in s)
    hi = max(b for s in dspans.values() for _, b in s)
    marks, d = [0.0], 0.0
    while d + 1.5 * gate(d) <= length:
        d += gate(d)
        marks.append(d)
    marks.append(length)
    while marks[-1] < hi:
        marks.append(marks[-1] + gate(marks[-1]))
    marks.append(marks[-1] + gate(marks[-1]))  # room for the wall column past the wall
    while marks[0] > lo:
        marks.insert(0, marks[0] - g0)
    return marks


def _nearest(xs, x):
    i = bisect.bisect_left(xs, x)
    if i and (i == len(xs) or x - xs[i - 1] <= xs[i] - x):
        i -= 1
    return i


def funnel_cells(sh, ppq):
    """Spam: (grid ticks, [(key, first grid line, last grid line)]), every key's notes running from grid
    line to grid line. Long: (None, [(key, start tick, end tick)])."""
    lay = funnel_layout(sh, ppq)
    if not lay:
        return None, []
    dspans, walls, length, t0, sign = lay
    past = sh["wall"] == "past"
    g1 = max(1, math.floor(sh["gate1"] * ppq + 0.5))

    def at_wall(q, d):
        w = walls.get(q)
        return w is not None and w[0] - 1 <= d <= w[1] + 1

    def tick(d):
        return math.floor(t0 + sign * d + 0.5)

    if sh["fill"] == "long":
        out = []
        for q, spans in dspans.items():
            for a, b in spans:
                far = tick(b) + (sign * g1 if past and at_wall(q, b) else 0)  # a wall gate past the wall
                s, e = sorted((tick(a), far))
                out.append((q, s, max(e, s + 1)))
        return None, out
    marks = funnel_grid(sh, ppq, dspans, length)
    ticks, ds = [], []
    for d in marks:
        t = tick(d)
        if not ticks or t != ticks[-1]:
            ticks.append(t)
            ds.append(sign * (t - t0))
    out = []
    for q, spans in dspans.items():
        ranges = []
        for a, b in spans:
            i, j = _nearest(ds, a), _nearest(ds, b)
            if past and at_wall(q, b):
                j = min(j + 1, len(ds) - 1)  # on to one note past the wall (a key reached on the wall: just that)
            elif j <= i:  # shorter than a note: the note it's in (the one ending there, if it's on a grid line)
                j = min(max(bisect.bisect_left(ds, (a + b) / 2 - 1e-9), 1), len(ds) - 1)
                i = j - 1
            if ranges and i <= ranges[-1][1]:
                ranges[-1][1] = max(ranges[-1][1], j)
            else:
                ranges.append([i, j])
        out += [(q, i, j) for i, j in ranges]
    return ticks, out


def funnel_note_count(sh, ppq):
    ticks, cells = funnel_cells(sh, ppq)
    return len(cells) if ticks is None else sum(j - i for _, i, j in cells)


def funnel_notes(sh, ppq):
    ticks, cells = funnel_cells(sh, ppq)
    cells = np.asarray(cells, np.int64).reshape(-1, 3)
    if ticks is None:
        return cells[:, [1, 2, 0]]
    # every key: a note from each grid line to the next, from its first line to its last
    q, i, j = cells.T
    n = np.maximum(j - i, 0)
    at = np.repeat(i, n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    ticks = np.asarray(ticks, np.int64)
    a, b = ticks[at], ticks[at + 1]
    return np.column_stack([np.minimum(a, b), np.maximum(a, b), np.repeat(q, n)])


# The default curve: close to the first versions' default (slow start, opening fast near the wall), with just the
# two end anchors and their handles
DEFAULT_CURVE = [[0.0, 0.0], [0.7, 0.06], [0.94, 0.3], [1.0, 1.0]]
