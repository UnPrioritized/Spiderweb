"""Custom shapes: drawings from the drawer placed on the roll, as outlines or filled with notes."""

import base64
import json
import math
import zlib

import numpy as np

from files.lang import tr
from notes.arc import arc_k, arc_points, ellipse_bezier
from notes.areas import COLOURS, _maps, area_map, inside_loops  # (areas coloured by hand)
from notes.faces import faces  # (what's filled when it isn't plain even-odd: fill_test)
from notes.bezier import sample
from notes.pattern import (baked_path, clean_pattern, clean_shape_formula, formed_path, formed_paths, has_formula,
                           moved_formulas)
from notes.polygon import side_paths
from notes.smooth import clean_level, smooth_path
from notes.paths import (TOP_KEY, dedupe, keep_longest, line_notes, loop_from_left, parts_notes, pitch_of,
                         stretch_ends)
from notes.hzbass import (HZ_DEFAULTS, KeyGrid, clean_hz, hz_gate, hz_of, off_cents, squares,  # (Hz bass: hzbass.py)
                          threshold)
from notes.shrink import (SAMPLES, inner_lines, inner_rows, merge as shrink_merge, minus as shrink_minus,
                          near_rows as shrink_near, proportion as shrink_proportion,
                          segments as shrink_segments)  # (the outline gate's even band)
from notes.text import text_polys, threshold_spans

# Custom shapes: how the inside is filled, and the gate of "spam" in beats (1/64 = 60 ticks at PPQ 960).
# "outline_spam" = the outline's notes (like "empty") chopped into notes of the spam gate, the inside stays empty
FILLS = ("empty", "fill", "spam", "outline_spam")
SPAM_FILLS = ("spam", "outline_spam")  # the ones that use the gate and spam start
# Spam start: "auto" = each stretch of a key starts at its own left edge, "aligned" = every note sits on the
# gate grid counted from tick 0 (straight columns, lined up with bar lines and other shapes), "centred" = what
# doesn't fit a whole gate is shared between both ends (a peak comes out the same on both sides)
ALIGNS = ("auto", "aligned", "centred")
# Spam ends (what happens to the bit of a stretch that doesn't fit a whole gate), in the dropdown's order:
# "round" (the default for new shapes) = a whole gate if it's at least half a gate, else dropped (every stretch gets
# at least one gate); "keep" = kept as a shorter note; "drop" = dropped, but a stretch too short for even one gate
# stays one note as it is (shapes saved before Ends existed have no "ends" and load as "drop", so their notes stay);
# "min" = like "drop", but no note shorter than a quarter gate (it grows, centred); "stretch" = the gates in the
# stretch are stretched or squeezed so a whole number fits exactly (the spam start doesn't matter then)
ENDS = ("round", "keep", "drop", "min", "stretch")
CUSTOM_DEFAULTS = {"fill": "empty", "gate": 0.0625, "align": "auto", "ends": "round", "union": False,
                   "apart": False, "borders": False}
# on / off settings a custom shape only has when they're on: "union" = where outlines overlap it's filled too (off:
# overlaps cancel out, even-odd); "apart" = Fill / Spam "Outline": the outline's notes on a channel of their own
# (with Multi channel), the inside's on another; "borders" = with "Outline" and areas coloured by hand (areas.py),
# where two colours meet is outline too (else only where the filled part ends)
CUSTOM_FLAGS = ("union", "apart", "borders")
# "Colours" (any shape but pasted notes): sh["cycle"] = {"by", "n", "every"} (only when on) = the notes take turns
# over n channels (with Multi channel each turn gets a channel, so a colour, of its own). "step" = by the step the
# note starts on (spam: its gate columns; others: each start time in turn), "key" = by key (rows), "time" = by a
# note length from tick 0. every = how many steps / keys one channel lasts; for "time" [a, b] = a/b of a whole note
# (like the snap: 1/4 = a beat).
CYCLES = ("step", "key", "time")
CYCLE_MAX = 15  # (15 note colours)


def clean_cycle(c):
    """A saved sh["cycle"] -> a valid one, or None (off)."""
    if not isinstance(c, dict) or c.get("by") not in CYCLES:
        return None
    try:
        n = max(2, min(CYCLE_MAX, int(c.get("n", 2))))
        every = c.get("every", 1)
        if c["by"] == "time":
            a, b = every if isinstance(every, (list, tuple)) and len(every) == 2 else (1, 4)
            every = [max(1, min(10 ** 4, int(a))), max(1, min(10 ** 4, int(b)))]
        else:
            every = max(1, min(10 ** 4, int(every)))
    except (TypeError, ValueError):
        return None
    return {"by": c["by"], "n": n, "every": every}


def custom_settings(cd):
    """The fill settings a new custom shape gets from cd (the settings for new ones). Never Hz bass (user: new
    shapes start without it; the Hz bass tool adds its own, from cd["hz"])."""
    out = {k: cd[k] for k in ("fill", "gate", "align", "ends")}
    out.update({k: True for k in CUSTOM_FLAGS if cd.get(k)})
    if cd.get("edge"):
        out["edge"] = cd["edge"]
    if cd.get("edge_mode") == "sideways":
        out["edge_mode"] = "sideways"
    return out


# ---------------------------------------------------------------- custom shapes
# A custom shape is a drawing from the drawer: strokes in its own box, u = 0..1 left to right, v = 0..1 bottom to
# top. A stroke is {"kind": "poly", "pts": [[u, v], ...]}, {"kind": "curve", "pts": [anchor, handle, handle,
# anchor, ...]} (bezier.py; optional "sharp" / "sym" like the roll's Curve shape, "shape" / "pattern" formulas like its
# too: pattern.py, their k = how many u one v is where they look round), {"kind": "arc", "pts": [start,
# through, end], "k": ...} (arc.py; k = how many u one v is, for it to be round) or
# {"kind": "ellipse", "box": [u0, v0, u1, v1]}. A polygon's stroke (polygon.py) is a closed poly with "sides" and
# maybe a "shape" / "pattern" laid along each side.
# On the roll, sh["pts"] = three corners of that box: [u=0 v=0, u=1 v=0, u=0 v=1]. Moving, flipping and turning
# the shape just moves these three points, and the drawing follows.

ELLIPSE_STEPS = 360
CURVE_STEPS = 240
# A stroke's "role" (none = outline and fill: its notes, and it makes the inside like always): "edge" = outline
# only (its notes, but Fill / Spam don't see it: no hole, no gap closed, drawn over what's filled), "cut" = fill
# line (no notes of its own, and it doesn't change what's filled; it splits the inside into areas to colour)
ROLES = ("edge", "cut")


def role_of(st):
    return st.get("role") if st.get("role") in ROLES else None


def colour_of(st):
    """A stroke's outline colour (1 .. COLOURS, the same numbers as the areas': areas.py), 0 = the shape's own."""
    return st.get("colour", 0) if st.get("role") != "cut" else 0


def clean_strokes(strokes):
    out = []
    for st in strokes or []:
        try:
            if st.get("kind") == "ellipse":
                box = [float(x) for x in st["box"]]
                if len(box) == 4:
                    out.append({"kind": "ellipse", "box": box})
            else:
                pts = [[float(u), float(v)] for u, v in st["pts"]]
                if st.get("kind") == "curve":
                    if len(pts) >= 4:
                        out.append(clean_curve(st, pts[:len(pts) - (len(pts) - 1) % 3]))
                elif st.get("kind") == "arc":
                    if len(pts) == 3:
                        out.append(clean_formulas(st, {"kind": "arc", "pts": pts, "k": arc_k(st)}))
                elif pts:
                    out.append({"kind": "poly", "pts": pts})
                    if st.get("free"):  # drawn freehand: can be made perfect (smooth.py)
                        out[-1].update(free=True, smooth=clean_level(st.get("smooth", 0)), k=arc_k(st))
                    else:  # a line / polyline, or a polygon's (polygon.py: the shape / pattern on each side)
                        if st.get("sides"):
                            out[-1]["sides"] = True
                        clean_formulas(st, out[-1])
            if isinstance(st.get("src"), int) and out:  # which shape it came from (convert.py)
                out[-1]["src"] = st["src"]
            if st.get("role") in ROLES and out:
                out[-1]["role"] = st["role"]
            if isinstance(st.get("colour"), int) and 1 <= st["colour"] <= COLOURS and out:
                out[-1]["colour"] = st["colour"]
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return out


def clean_curve(st, pts):
    """A curve stroke from a file: its points, corners and symmetry checked."""
    out = {"kind": "curve", "pts": pts}
    last = (len(pts) - 1) // 3
    try:
        sharp = sorted({int(a) for a in st.get("sharp", ()) if 0 < int(a) < last})
    except (TypeError, ValueError):
        sharp = []
    if sharp:
        out["sharp"] = sharp
    if st.get("sym") in ("mirror", "turn") and last % 2 == 0:
        out["sym"] = st["sym"]
    return clean_formulas(st, out)


def clean_formulas(st, out):
    """out (a stroke being read from a file) with st's shape / pattern formulas, checked (pattern.py)."""
    pat, form = clean_pattern(st.get("pattern")), clean_shape_formula(st.get("shape"))
    if pat:
        out["pattern"] = pat
    if form:
        out["shape"] = form
    return out


def takes_formula(st):
    """The strokes that can have formulas (like the piano roll's lines, polylines, curves and arcs): not freehand
    ones or ellipses."""
    return st["kind"] in ("curve", "arc") or st["kind"] == "poly" and not st.get("free")


def stroke_points(st):
    """A stroke as (u, v) points. An ellipse starts at its leftmost point and ends exactly where it started."""
    if st["kind"] == "ellipse":
        u0, v0, u1, v1 = st["box"]
        cu, cv, ru, rv = (u0 + u1) / 2, (v0 + v1) / 2, (u1 - u0) / 2, (v1 - v0) / 2
        pts = [(cu - ru * math.cos(a), cv + rv * math.sin(a))
               for a in (2 * math.pi * i / ELLIPSE_STEPS for i in range(ELLIPSE_STEPS))]
        return pts + [pts[0]]
    if st["kind"] == "curve":
        return formed_path(sample([tuple(p) for p in st["pts"]], CURVE_STEPS), st)
    if st["kind"] == "arc":
        return formed_path(arc_points(st["pts"], st.get("k", 1.0)), st)
    if st.get("smooth"):  # a freehand stroke made perfect (its drawn points stay)
        return smooth_path(st["pts"], st["smooth"], st.get("k", 1.0))
    if st.get("sides") and has_formula(st):  # a polygon's sides, each with the shape / pattern (polygon.py)
        out = []
        for side in formed_paths(side_paths(st["pts"]), st):
            out += side[1:] if out else side
        return out
    if not st.get("free") and has_formula(st):  # a line / polyline: along all of it, round its corners
        return formed_path([tuple(p) for p in st["pts"]], st)
    return [tuple(p) for p in st["pts"]]


def plain_stroke(st):
    """The stroke without its formulas (the dashed origin path under them)."""
    return {key: v for key, v in st.items() if key not in ("shape", "pattern", "rev")}


def baked_stroke(st, modes):
    """Turn into plain curve: the stroke as it looks with its formulas, as a curve stroke without them (a line /
    polyline / arc becomes a curve; its role, colour and shape it came from stay). modes: the symmetric halves
    it may get."""
    pts, sharp, sym = baked_path(st, None, modes, stroke_points(st))
    out = {key: v for key, v in st.items()
           if key not in ("kind", "pts", "shape", "pattern", "sym", "rev", "k", "sides", "sharp")}
    out.update(kind="curve", pts=pts)
    if sharp:
        out["sharp"] = sharp
    if sym:
        out["sym"] = sym
    return out


def path_closed(path):
    return len(path) >= 3 and math.dist(path[0], path[-1]) < 1e-6


def stroke_closed(st):
    return st["kind"] == "ellipse" or path_closed(st["pts"])


def join_paths(paths):
    """Point lists that meet end to end become one list (e.g. a curve and a line closing it)."""
    def meet(a, b):
        return math.dist(a, b) < 1e-6

    done = [list(p) for p in paths if path_closed(p)]
    lines = [list(p) for p in paths if not path_closed(p)]
    joined = True
    while joined:
        joined = False
        heads = np.array([a[0] for a in lines], float).reshape(-1, 2)  # (all ends at once: big drawings)
        tails = np.array([a[-1] for a in lines], float).reshape(-1, 2)
        for i, a in enumerate(lines):
            can = ((np.hypot(*(heads - tails[i]).T) < 2e-6) | (np.hypot(*(tails - tails[i]).T) < 2e-6) |
                   (np.hypot(*(heads - heads[i]).T) < 2e-6))
            can[i] = False
            for j in np.flatnonzero(can).tolist():  # (the first that really meets, as checked one by one)
                b = lines[j]
                if meet(a[-1], b[0]):
                    new = a + b[1:]
                elif meet(a[-1], b[-1]):
                    new = a + b[-2::-1]
                elif meet(a[0], b[0]):
                    new = a[::-1] + b[1:]
                else:
                    continue
                lines[i] = new
                del lines[j]
                joined = True
                break
            if joined:
                break
    return done + lines


def stroke_span(st):
    """A stroke's two ends [start, end], or None if it's closed (curves by their points: sampling is slow)."""
    if st["kind"] == "curve" and not has_formula(st):
        pts = st["pts"]
        closed = math.dist(pts[0], pts[-1]) < 1e-6
    else:
        pts = stroke_points(st)
        closed = path_closed(pts)
    return None if closed else [tuple(pts[0]), tuple(pts[-1])]


def open_paths(strokes):
    """The drawing's open outlines once touching strokes are joined: [[start, (joins), end]] in u, v. Each one
    is a gap in the outline (outline-only strokes and fill lines don't count: Fill / Spam don't close them). One
    whose ends both land on a line (another stroke's, or partway along its own) isn't (user: no red dots there)."""
    plain = [st for st in strokes if not role_of(st)]
    spans = [s for s in map(stroke_span, plain) if s]
    paths = [path for path in join_paths(spans) if not path_closed(path)]
    if not paths:
        return paths
    pts = [np.asarray(stroke_points(st), float).reshape(-1, 2) for st in plain]
    pts = [p for p in pts if len(p) > 1]
    if not pts:
        return paths
    every = np.concatenate(pts)
    tol = max(np.ptp(every[:, 0]), np.ptp(every[:, 1]), 1e-12) * LAND_SHARE
    seg = np.concatenate([np.column_stack([p[:-1], p[1:]]) for p in pts])
    first = np.cumsum([0] + [len(p) - 1 for p in pts])
    loose = np.array([not path_closed(p) for p in pts])
    starts, ends = np.array([p[0] for p in pts]), np.array([p[-1] for p in pts])
    lo = np.minimum(seg[:, :2], seg[:, 2:]) - tol  # each segment's box, tol bigger (only those can be in reach)
    hi = np.maximum(seg[:, :2], seg[:, 2:]) + tol
    lox, loy, hix, hiy = (np.ascontiguousarray(a) for a in (lo[:, 0], lo[:, 1], hi[:, 0], hi[:, 1]))

    def lands(end):  # (the segment a stroke's own end is on doesn't count)
        e = np.asarray(end, float)
        x, y = end
        near = (lox <= x) & (hix >= x) & (loy <= y) & (hiy >= y)
        near[first[:-1][loose & (np.hypot(*(starts - e).T) < 1e-9)]] = False
        near[first[1:][loose & (np.hypot(*(ends - e).T) < 1e-9)] - 1] = False
        return nearest_on(end, seg[near], (1.0, 1.0))[0] <= tol

    return [path for path in paths if not (lands(path[0]) and lands(path[-1]))]


def nearest_on(p, seg, scale):
    """How far p is from the nearest of these segments ((m, 4): ax, ay, bx, by), counted in scale units (x, y), and
    the spot there (None if there are none)."""
    if not len(seg):
        return math.inf, None
    k = np.asarray(scale, float)
    a, b, q = seg[:, :2] * k, seg[:, 2:] * k, np.asarray(p, float) * k
    d = b - a
    ll = (d * d).sum(1)
    t = np.clip(((q - a) * d).sum(1) / np.where(ll == 0, 1, ll), 0, 1)
    at = a + t[:, None] * d
    dist = np.hypot(*(q - at).T)
    i = int(np.argmin(dist))
    return float(dist[i]), tuple((at[i] / k).tolist())


def open_ends(strokes):
    """The loose ends of the drawing once touching strokes are joined."""
    return [end for path in open_paths(strokes) for end in (path[0], path[-1])]


def strokes_closed(strokes):
    """Every outline ends where it starts."""
    return any(not role_of(st) for st in strokes) and not open_paths(strokes)


def fillable(strokes):
    """Fill and Spam work (any drawing: gaps are closed with straight lines, see fill_plan)."""
    return bool(strokes)


# Gaps in the outline, for Fill / Spam (in beats / keys, not on screen, so zooming never changes the notes):
TOUCH_BEATS, TOUCH_KEYS = 1 / 64, 1.0  # loose ends at most this far apart count as touching (joined straight)
FLAT_KEYS, FLAT_BEATS = 0.5, 1 / 64  # an open part never further than this from its closing line is left unfilled
LAND_SHARE = 1e-4  # in the drawer (u, v): an end this share of the drawing's size from a line lands on it
ON_LINE = 0.01  # placed: an end landing this close (in TOUCH_BEATS / TOUCH_KEYS) needs no line closing it
_plans = {}
_spans = {}  # inside_spans, remembered
_inner = {}  # inner_ticks, remembered


def near_ends(p, q):
    return abs(p[0] - q[0]) <= TOUCH_BEATS + 1e-9 and abs(p[1] - q[1]) <= TOUCH_KEYS + 1e-9


def flat_path(path):
    """The open path never gets further than half a key (up / down) or 1/64 beat (sideways) from the straight line
    between its ends: it has no inside worth filling (a straight line, a very gentle curve)."""
    a = np.asarray(path, float).reshape(-1, 2)
    (b0, p0), (b1, p1) = a[0], a[-1]
    db, dp = b1 - b0, p1 - p0
    for along, across, lim, d_along, d_across, s in ((0, 1, FLAT_KEYS, db, dp, b0), (1, 0, FLAT_BEATS, dp, db, p0)):
        if abs(d_along) < 1e-12:
            continue
        t = (a[:, along] - s) / d_along
        if ((t < -1e-9) | (t > 1 + 1e-9)).any():  # (it goes back past its ends)
            continue
        line = a[0, across] + t * d_across
        if (np.abs(a[:, across] - line) <= lim + 1e-9).all():
            return True
    return abs(db) < 1e-12 and abs(dp) < 1e-12


def fill_plan(sh):
    """How Fill / Spam see a custom shape's outline (beats / pitch), remembered:
    "polys": the closed loops that make the inside, "closers": the straight lines added to close gaps
    (loose ends that nearly touch are joined; every open part left is closed from its end back to its start),
    "flat": open parts too flat to have an inside (they just keep their outline notes), "attached": open parts
    whose ends both land on a line (another one's, or partway along their own; user): not closed, they're walls
    between areas and keep their outline notes (fill_test; an end landing near a line, not on it, gets a short
    closer to it)."""
    key = (json.dumps(sh["strokes"]), json.dumps(sh["pts"]))
    got = _plans.get(key)
    if got is not None:
        return got
    if len(_plans) > 300:
        _plans.clear()
    paths = join_paths(role_paths(sh)[None])
    polys = [p for p in paths if path_closed(p)]
    opens = [list(map(tuple, p)) for p in paths if not path_closed(p)]
    closers = []
    while True:  # the nearest two loose ends that count as touching, joined, until there are none
        best = None
        for i, a in enumerate(opens):
            for j in range(i, len(opens)):
                b = opens[j]
                pairs = [(a[-1], a[0], 0)] if i == j else [(a[-1], b[0], 1), (a[-1], b[-1], 2), (a[0], b[0], 3),
                                                            (a[0], b[-1], 4)]
                for p, q, how in pairs:
                    if i == j and len(a) < 3:
                        continue
                    if near_ends(p, q):
                        d = max(abs(p[0] - q[0]) / TOUCH_BEATS, abs(p[1] - q[1]) / TOUCH_KEYS)
                        if best is None or d < best[0]:
                            best = (d, i, j, how, p, q)
        if best is None:
            break
        _, i, j, how, p, q = best
        closers.append([p, q])
        a, b = opens[i], opens[j]
        if how == 0:
            polys.append(a + [a[0]])
            del opens[i]
            continue
        a = a if how in (1, 2) else a[::-1]
        b = b if how in (1, 3) else b[::-1]
        opens[i] = a + b
        del opens[j]
    lines = [np.asarray(p, float).reshape(-1, 2) for p in polys + opens]
    seg = np.concatenate([np.column_stack([p[:-1], p[1:]]) for p in lines if len(p) > 1] or [np.zeros((0, 4))])
    first = np.cumsum([0] + [max(len(p) - 1, 0) for p in lines])
    scale = (1 / TOUCH_BEATS, 1 / TOUCH_KEYS)
    reach = np.array([TOUCH_BEATS, TOUCH_KEYS]) * (1 + 1e-9)  # (only segments whose box is this near can count)
    lox, loy = (np.minimum(seg[:, :2], seg[:, 2:]) - reach).T
    hix, hiy = (np.maximum(seg[:, :2], seg[:, 2:]) + reach).T
    flat, attached, n_closed = [], [], len(polys)
    for i, path in enumerate(opens):
        k = n_closed + i
        ends = []
        for end, own in ((path[0], first[k]), (path[-1], first[k + 1] - 1)):  # (not the segment it ends)
            keep = (lox <= end[0]) & (hix >= end[0]) & (loy <= end[1]) & (hiy >= end[1])
            if first[k + 1] > first[k]:
                keep[own] = False
            ends.append(nearest_on(end, seg[keep], scale))
        if all(d <= 1 for d, _ in ends):
            (d0, p0), (d1, p1) = ends
            for d, a, b in ((d0, p0, path[0]), (d1, path[-1], p1)):
                if d > ON_LINE:
                    closers.append([a, b])
            attached.append(([p0] if d0 > ON_LINE else []) + path + ([p1] if d1 > ON_LINE else []))
        elif flat_path(path):
            flat.append(path)
        else:
            polys.append(path + [path[0]])
            closers.append([path[-1], path[0]])
    got = _plans[key] = {"polys": polys, "closers": closers, "flat": flat, "attached": attached}
    return got


def gap_lines(sh):
    """The straight lines closing gaps in a custom shape's outline for Fill / Spam (drawn dashed)."""
    return [] if sh.get("text") else fill_plan(sh)["closers"]


def join_strokes(strokes):
    """Open lines that meet end to end become one line, so e.g. three lines drawn as a triangle make one
    closed triangle (only lines with the same role and outline colour, which they keep). Curves and circles stay as
    they are (they still count as joined, see strokes_closed), and so do lines with a formula."""
    def plain_line(st):
        return st["kind"] == "poly" and not stroke_closed(st) and not has_formula(st)

    others = [st for st in strokes if not plain_line(st)]
    order = ("",) + ROLES
    kinds = sorted({(role_of(st) or "", colour_of(st)) for st in strokes if plain_line(st)},
                   key=lambda k: (order.index(k[0]), k[1]))
    for role, colour in kinds:
        lines = [[list(p) for p in st["pts"]] for st in strokes
                 if plain_line(st) and (role_of(st) or "") == role and colour_of(st) == colour]
        keep = dict(({"role": role} if role else {}), **({"colour": colour} if colour else {}))
        others += [dict({"kind": "poly", "pts": pts}, **keep) for pts in join_paths(lines)]
    return others


def custom_strokes(sh):
    if sh.get("text"):
        return text_polys(sh)
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    return [[(b0 + u * ub + v * vb, p0 + u * up + v * vp) for u, v in stroke_points(st)] for st in sh["strokes"]]


def role_paths(sh):
    """custom_strokes by role: {None: outline and fill, "edge": outline only, "cut": fill lines} (text: all None)."""
    paths = custom_strokes(sh)
    out = {None: [], "edge": [], "cut": []}
    if sh.get("text"):
        out[None] = paths
        return out
    for st, path in zip(sh["strokes"], paths):
        out[role_of(st)].append(path)
    return out


def box_frame(b0, p0, b1, p1):
    """The three corner points of a custom shape filling the box from (b0, p0) to (b1, p1)."""
    bl, bh, pl, ph = min(b0, b1), max(b0, b1), min(p0, p1), max(p0, p1)
    return [[bl, pl], [bh, pl], [bl, ph]]


def normalize_strokes(strokes):
    """Strokes stretched so the drawing fills the whole 0..1 box, and its width / height (None if flat)."""
    pts = [p for st in strokes for p in stroke_points(st)]
    us, vs = [u for u, _ in pts], [v for _, v in pts]
    ul, vl = min(us), min(vs)
    w, h = max(us) - ul, max(vs) - vl

    def fix(u, v):
        return [round((u - ul) / w, 6) if w > 1e-9 else 0.5, round((v - vl) / h, 6) if h > 1e-9 else 0.5]

    out = []
    for st in strokes:
        if st["kind"] == "ellipse":
            u0, v0, u1, v1 = st["box"]
            out.append(dict(st, box=fix(u0, v0) + fix(u1, v1)))
        else:
            new = dict(st, pts=[fix(u, v) for u, v in st["pts"]])
            if (st["kind"] == "arc" or st.get("free")) and w > 1e-9 and h > 1e-9:
                new["k"] = st.get("k", 1.0) * h / w  # still round when stretched back to the drawn proportions
            if w > 1e-9 and h > 1e-9:
                moved_formulas(new, lambda u, v: (u / w, v / h))
            out.append(new)
    return out, (w / h if w > 1e-9 and h > 1e-9 else None)


def normalize_areas(strokes, areas):
    """Areas (areas.py) moved like normalize_strokes moves the strokes."""
    pts = [p for st in strokes for p in stroke_points(st)]
    if not pts or not areas:
        return []
    us, vs = [u for u, _ in pts], [v for _, v in pts]
    ul, vl = min(us), min(vs)
    w, h = max(us) - ul, max(vs) - vl
    return [[round((u - ul) / w, 6) if w > 1e-9 else 0.5, round((v - vl) / h, 6) if h > 1e-9 else 0.5, c]
            for u, v, c in areas]


# ---------------------------------------------------------------- live drawing (strokes drawn on the roll)
# With "Live shape" on, lines / curves / arcs / squares / circles drawn on the roll become strokes of one custom shape.
# A stroke drawn in beats / pitch is put into the shape's own box (u, v), then the box is fitted around the drawing.

def frame_to_bp(pts):
    """(u, v) -> (beat, pitch) for a custom shape's frame points."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    return lambda u, v: (b0 + u * ub + v * vb, p0 + u * up + v * vp)


def frame_to_uv(pts):
    """(beat, pitch) -> (u, v), or None if the box is flat."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    det = ub * vp - up * vb
    if abs(det) < 1e-12:
        return None
    return lambda b, p: (((b - b0) * vp - (p - p0) * vb) / det, (ub * (p - p0) - up * (b - b0)) / det)


def uv_k(pts, k):
    """How many u one v of a custom shape's box is on screen, when the screen shows k beats per key."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    lu, lv = math.hypot((b1 - b0) / k, p1 - p0), math.hypot((b2 - b0) / k, p2 - p0)
    return lv / lu if lu > 1e-12 and lv > 1e-12 else 1.0


def frame_upright(pts):
    """The box isn't turned (u runs along time, v along pitch), so an ellipse stays an ellipse in it."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    return abs(p1 - p0) < 1e-12 and abs(b2 - b0) < 1e-12


def map_stroke(st, fn, su=1.0, sv=1.0):
    """The stroke with every point moved by fn(u, v) -> (u, v). su, sv: how many times wider / taller that makes
    it (only for keeping arcs round and ellipse boxes the right way round)."""
    if st["kind"] == "ellipse":
        (a, b), (c, d) = fn(*st["box"][:2]), fn(*st["box"][2:])
        return dict(st, box=[min(a, c), min(b, d), max(a, c), max(b, d)])
    new = dict(st, pts=[list(fn(u, v)) for u, v in st["pts"]])
    if st["kind"] == "arc" or st.get("free"):  # (a freehand stroke's k works like an arc's)
        new["k"] = st.get("k", 1.0) * abs(su / sv)
    moved_formulas(new, fn)  # (a curve's)
    return new


def refit(sh):
    """Fit a custom shape's box around its drawing again (after a stroke was added or changed): the strokes go back
    to filling 0..1, the frame points move so nothing changes on the roll. A flat drawing keeps its box size that
    way and sits in the middle."""
    pts = [p for st in sh["strokes"] for p in stroke_points(st)]
    if not pts:
        return
    us, vs = [u for u, _ in pts], [v for _, v in pts]
    ul, vl = min(us), min(vs)
    w, h = max(us) - ul, max(vs) - vl
    if w < 1e-9:
        ul, w = ul - 0.5, 1.0
    if h < 1e-9:
        vl, h = vl - 0.5, 1.0
    if abs(ul) < 1e-12 and abs(vl) < 1e-12 and abs(w - 1) < 1e-12 and abs(h - 1) < 1e-12:
        return
    to_bp = frame_to_bp(sh["pts"])
    sh["pts"] = [list(to_bp(ul, vl)), list(to_bp(ul + w, vl)), list(to_bp(ul, vl + h))]
    for st in sh["strokes"]:  # (each stroke stays the same dict: a formula window may be holding it)
        new = map_stroke(st, lambda u, v: ((u - ul) / w, (v - vl) / h), 1 / w, 1 / h)
        st.clear()
        st.update(new)
    if sh.get("areas"):
        sh["areas"] = [[(u - ul) / w, (v - vl) / h, c] for u, v, c in sh["areas"]]


def stroke_ends(strokes):
    """Points other strokes can join onto: every polyline point, the ends of curves and arcs (with a formula: the
    ends it has then, as seen)."""
    out = []
    for st in strokes:
        if st["kind"] != "ellipse" and has_formula(st) and not st.get("sides"):  # (a polygon's corners stay)
            path = stroke_points(st)
            out += [list(path[0]), list(path[-1])]
        elif st["kind"] == "poly":
            out += st["pts"]
        elif st["kind"] in ("curve", "arc"):
            out += [st["pts"][0], st["pts"][-1]]
    return out


def bp_k(pts, k):
    """uv_k the other way round: the beats per key on screen at which one v of the box is k u (for a stroke's k
    taken out of the box). 1 if there's none."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    # uv_k = hypot(vb / K, vp) / hypot(ub / K, up) = k, solved for x = 1 / K²
    num, den = k * k * up * up - vp * vp, vb * vb - k * k * ub * ub
    x = num / den if abs(den) > 1e-18 else -1
    return 1 / math.sqrt(x) if x > 1e-18 else 1.0


def stroke_bp(sh, k):
    """Stroke k of custom shape sh in beats / pitch (like a stroke drawn on the roll, for add_stroke): an ellipse
    in a turned box becomes a curve; an arc's / freehand stroke's k becomes beats per key."""
    st = sh["strokes"][k]
    to_bp = frame_to_bp(sh["pts"])
    if st["kind"] == "ellipse" and not frame_upright(sh["pts"]):
        st = dict({k: v for k, v in st.items() if k != "box"}, kind="curve", pts=ellipse_bezier(st["box"]))
    new = map_stroke(st, to_bp)
    if "k" in st:
        new["k"] = bp_k(sh["pts"], st["k"])
    return new


def add_stroke(sh, st, at=None):
    """Put stroke st (drawn in beats / pitch: poly / curve points, or an ellipse box) into custom shape sh and fit its
    box around the drawing again. Its ends that land on another stroke's point are made exactly the same (so the
    outline counts as joined). at: its number (default: after the others). Returns the new stroke's number."""
    to_uv = frame_to_uv(sh["pts"])
    if st["kind"] == "ellipse" and not frame_upright(sh["pts"]):  # turned: an ellipse only fits as a curve
        st = dict({k: v for k, v in st.items() if k != "box"}, kind="curve", pts=ellipse_bezier(st["box"]))
    new = map_stroke(st, to_uv)
    if new.get("free") or new["kind"] == "arc":  # its k: beats per key on screen -> how many u one v is on screen
        new["k"] = uv_k(sh["pts"], st.get("k", 1.0))
    if new["kind"] != "ellipse":
        near = stroke_ends(sh["strokes"])
        for i in (0, len(new["pts"]) - 1) if new["kind"] != "poly" else range(len(new["pts"])):
            p = new["pts"][i]
            q = min(near, key=lambda q: math.dist(p, q), default=None)
            if q is not None and math.dist(p, q) < 1e-7:
                new["pts"][i] = list(q)
        if new["kind"] == "poly" and len(new["pts"]) >= 3 and math.dist(new["pts"][0], new["pts"][-1]) < 1e-7:
            new["pts"][-1] = list(new["pts"][0])
    at = len(sh["strokes"]) if at is None else at
    sh["strokes"].insert(at, new)
    refit(sh)
    return at


def new_live_shape(defaults, custom_defaults):
    """An empty custom shape to draw into (its box: 1 beat by 1 key at 0, fitted once something is drawn)."""
    return dict(defaults, kind="custom", name=tr("custom.live_drawing"), strokes=[], **custom_settings(custom_defaults),
                pts=[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])


def outline_notes(sh, ppq, only=None):
    """(start, end, pitch) notes along every stroke of a custom shape (only: just these stroke numbers), like
    lines. Fill lines make none."""
    paths = custom_strokes(sh)
    ks = range(len(paths)) if only is None else only
    if not sh.get("text"):
        ks = [k for k in ks if role_of(sh["strokes"][k]) != "cut"]
    return paths_outline(join_paths([paths[k] for k in ks]), ppq)


def paths_outline(paths, ppq):
    """(start, end, pitch) notes along these (joined) paths, like lines."""
    raw = []
    for path in paths:
        closed = path_closed(path)
        path = dedupe(path)
        if len(path) < 2:
            t = math.floor(path[0, 0] * ppq + 0.5)
            raw.append(np.array([[t, t + 1, pitch_of(path[0, 1])]], np.int64))
            continue
        if closed:
            path = loop_from_left(path if (path[-1] == path[0]).all() else np.vstack([path, path[:1]]))
        else:
            if path[-1, 0] < path[0, 0]:
                path = path[::-1]
            path = stretch_ends(path)
        raw.append(line_notes(path * [ppq, 1]))
    return keep_longest(np.concatenate(raw) if raw else np.zeros((0, 3), np.int64))


def poly_edges(polys):
    """The closed polygons' edges as arrays (x and y where each starts, x and y where it ends), without the level
    ones (row_spans works on these)."""
    parts = [np.asarray(poly, float).reshape(-1, 2) for poly in polys]
    parts = [p for p in parts if len(p) > 1]
    if not parts:
        return (np.zeros(0),) * 4
    a, b = np.concatenate([p[:-1] for p in parts]), np.concatenate([p[1:] for p in parts])
    keep = a[:, 1] != b[:, 1]
    return tuple(np.ascontiguousarray(v[keep]) for v in (a[:, 0], a[:, 1], b[:, 0], b[:, 1]))


def row_spans(polys, q, edges=None):
    """Time ranges (in beats) where the closed polygons' inside (even-odd, so holes stay empty) touches pitch
    row q anywhere between its bottom and top edge. edges: poly_edges(polys), when it's at hand (many rows)."""
    lo, hi = q - 0.5, q + 0.5
    xa, ya, xb, yb = poly_edges(polys) if edges is None else edges
    here = np.flatnonzero((np.minimum(ya, yb) < hi) & (np.maximum(ya, yb) > lo))
    if not len(here):
        return []
    xa, ya, xb, yb = xa[here], ya[here], xb[here], yb[here]
    # Between two neighbouring levels no corner lies, so every edge is a straight piece and each pair of
    # crossings is a trapezoid: its time range is the widest of its two sides.
    ys = np.concatenate([ya, yb])
    levels = np.unique(np.concatenate([[lo, hi], ys[(ys > lo) & (ys < hi)]]))
    # every edge with every gap between two levels it runs through (edge by edge, so in each gap they keep their
    # order). No "does it cross the middle" test: two levels a hair apart have a middle equal to one of them, and
    # the test then lost an edge there, so the rest of the row swapped inside and outside.
    first = np.searchsorted(levels, np.maximum(np.minimum(ya, yb), lo))
    n = np.searchsorted(levels, np.minimum(np.maximum(ya, yb), hi)) - first
    e = np.repeat(np.arange(len(here)), n)
    gap = np.repeat(first, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    y0, y1 = levels[gap], levels[gap + 1]
    mid = (y0 + y1) / 2
    xa, ya, xb, yb = xa[e], ya[e], xb[e], yb[e]

    def x_at(y):
        return xa + (xb - xa) * (y - ya) / (yb - ya)

    order = np.lexsort((x_at(mid), gap))  # in each gap from left to right (the same spot: in the edges' order)
    gap = gap[order]
    left, right = np.minimum(x_at(y0), x_at(y1))[order], np.maximum(x_at(y0), x_at(y1))[order]
    new = np.ones(len(gap), bool)
    new[1:] = gap[1:] != gap[:-1]
    at = np.arange(len(gap))
    rank = at - np.maximum.accumulate(np.where(new, at, 0))
    pair = np.flatnonzero((rank % 2 == 0) & np.append(~new[1:], False))  # the 1st with the 2nd, the 3rd with ...
    return merge_spans(left[pair], right[pair + 1])


def merge_spans(a, b):
    """Ranges from a[i] to b[i] -> [[start, end]] in order, the ones that touch or overlap made one."""
    if not len(a):
        return []
    order = np.lexsort((b, a))
    a, b = a[order], b[order]
    new = np.ones(len(a), bool)
    new[1:] = a[1:] > np.maximum.accumulate(b)[:-1]
    at = np.flatnonzero(new)
    return np.column_stack([a[at], np.maximum.reduceat(b, at)]).tolist()


def inside_spans(sh, ppq):
    """[(pitch, start tick, end tick)] for every stretch of every key inside the shape. Text: the nonzero rule and
    its threshold (text.py). Gaps in the outline are closed with straight lines (fill_plan). Remembered: a shape
    with many corners takes a while, and it's asked for again and again (note counts, every change of the gate)."""
    key = (json.dumps([sh["strokes"], sh["pts"], sh.get("text")]), bool(sh.get("union")), ppq)
    got = _spans.get(key)
    if got is None:
        if len(_spans) > 100:
            _spans.clear()
        got = _spans[key] = tuple(find_spans(sh, ppq))
    return got


def find_spans(sh, ppq):
    tx = sh.get("text")
    walled = fill_test(sh)
    polys = custom_strokes(sh) if tx else walls(sh) if walled else fill_plan(sh)["polys"]
    ps = [p for poly in polys for _, p in poly]
    if not ps:
        return []
    edges = None if tx else poly_edges(polys)
    if walled:  # (every piece of a row between two lines: filled as fill_test says)
        edges += (np.zeros(len(edges[0]), np.int64),)
    out = []
    for q in range(max(0, pitch_of(min(ps))), min(TOP_KEY, pitch_of(max(ps))) + 1):
        row = [(math.floor(a * ppq + 0.5), math.floor(b * ppq + 0.5)) for a, b, *_ in (
            threshold_spans(polys, q, tx["threshold"]) if tx else
            row_pieces(edges, q, walled, as_normal) if walled else row_spans(polys, q, edges))]
        # a bit under a tick only if the row has nothing else (like find_area_spans): two shapes sharing a stretch
        # of the same side made a column of 1-tick notes along it where they overlap
        some = any(e > s for s, e in row)
        out += [(q, s, max(e, s + 1)) for s, e in row if e > s or not some]
    return out


# ---------------------------------------------------------------- areas coloured by hand (areas.py)

def has_areas(sh):
    """Fill / Spam with areas given a colour of their own or emptied (sh["areas"]; not text or pasted notes)."""
    return bool(sh.get("areas")) and sh.get("fill") in ("fill", "spam") and not sh.get("text") and "notes" not in sh


def uv_points(pts, path):
    """(beat, pitch) points -> (u, v) array in the box of frame pts (None if the box is flat)."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    det = ub * vp - up * vb
    if abs(det) < 1e-12:
        return None
    a = np.asarray(path, float).reshape(-1, 2)
    db, dp = a[:, 0] - b0, a[:, 1] - p0
    return np.column_stack([(db * vp - dp * vb) / det, (ub * dp - up * db) / det])


def shape_areas(sh):
    """The AreaMap of a custom shape (its Fill / Spam loops and fill lines) in its own box's u, v, so it stays the
    same wherever the shape is put (None if the box is flat)."""
    plan = fill_plan(sh)
    closers = [uv_points(sh["pts"], c) for c in plan["closers"]]
    if any(c is None for c in closers) or uv_points(sh["pts"], [[0, 0]]) is None:
        return None
    key = (json.dumps(sh["strokes"]), json.dumps([np.round(c, 6).tolist() for c in closers]))
    if key in _maps:
        return _maps[key]
    loops = [uv_points(sh["pts"], p) for p in plan["polys"] + plan["attached"]]
    cuts = [uv_points(sh["pts"], p) for p in area_cuts(sh)]
    return area_map(loops, cuts, key)


def walls(sh):
    """Every line that walls areas in for Fill / Spam (not fill lines: they don't change what's filled), in beats /
    keys: the loops, the lines ending on lines and the ones too flat to fill."""
    plan = fill_plan(sh)
    return plan["polys"] + plan["attached"] + plan["flat"]


def shape_faces(sh):
    """faces.Faces of walls(sh) in the shape's own box (u, v), so it stays the same wherever the shape is put (None
    if the box is flat)."""
    plan = fill_plan(sh)
    closers = [uv_points(sh["pts"], c) for c in plan["closers"]]
    if any(c is None for c in closers) or uv_points(sh["pts"], [[0, 0]]) is None:
        return None
    return faces([uv_points(sh["pts"], p) for p in walls(sh)])


def spot_area(amap, u, v):
    """For each row of spots (u, v: (n, m) arrays, spread over one piece), the area most of them are in, as the
    exact areas say (as the drawer shows them: in a tip thinner than the cells, e.g. where a curve touches a line or
    many lines start at one point, the cells are on lines or in little pockets with no colour, user)."""
    labs = amap.fine_at(u.ravel(), v.ravel()).reshape(u.shape)
    return np.asarray([max(set(row), key=row.count) for row in labs.tolist()], np.int64).reshape(-1)


def filled_uv(sh, u, v):
    """fill_test for spots in the shape's box (u, v arrays)."""
    fc = shape_faces(sh)
    if fc is None:
        return np.zeros(np.size(u), bool)
    d = fc.depth_at(u, v)
    return d >= 1 if sh.get("union") else d % 2 == 1


def fill_test(sh):
    """What Fill / Spam fill, when it isn't the plain even-odd of fill_plan's loops: test(x, y) = for each row of
    spots in beats / keys ((n, m) arrays spread over one piece of a key row; the first one, its middle, decides)
    whether it's filled. Overlaps cancel out off: every area closed in (user: overlaps and holes too, also where ONE
    line crosses itself). On: areas an odd number of lines in (faces.py; the same as even-odd, but a line ending on
    lines leaves both sides as they were, user). None: plain even-odd (text, or every line part of a loop)."""
    if sh.get("text"):
        return None
    plan = fill_plan(sh)
    if not (sh.get("union") or plan["attached"] or plan["flat"]):
        return None
    frame = sh["pts"]

    def test(x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        if not x.size:
            return np.zeros(len(x), bool)
        uv = uv_points(frame, np.column_stack([x.reshape(len(x), -1)[:, 0], y.reshape(len(y), -1)[:, 0]]))
        return np.zeros(len(x), bool) if uv is None else filled_uv(sh, uv[:, 0], uv[:, 1])
    test.mode = "union" if sh.get("union") else "odd"  # (remembered results differ: shrink.field)
    return test


def as_normal(x, y):
    """row_pieces' colour when nothing is coloured: every piece filled as normal."""
    return np.full(len(x), -1, np.int64)


def carry_areas(old, new):
    """new["areas"] after the lines moved (old: the shape before, its areas in the same order): each area's colour
    stays with the area it was in. Each new area is paired with the old one most of it came from (surest first,
    each used once; works for a big jump too, where overlap alone would hand a squeezed area's colour to the
    neighbour that took most of it), so an area squeezed past its colour's spot keeps the colour (the spot moves
    into it). A colour whose area found no pair (merged into another for now) keeps its spot, so it comes back when
    the areas part again; paired ones go after those, so they win the merged area. Returns the new areas list."""
    areas = new.get("areas") or []
    a0, a1 = (shape_areas(old), shape_areas(new)) if areas else (None, None)
    if a0 is None or a1 is None:
        return areas

    def grid(m):  # every fourth cell's middle, in the map's u, v
        ys, xs = np.mgrid[0:m.h:4, 0:m.w:4]
        return (np.column_stack([xs.ravel(), ys.ravel()]) + 0.5) / m.k + m.lo

    def moved(uv, src, dst):  # u, v in shape src's box -> in dst's (through beats / keys)
        (b0, p0), (b1, p1), (b2, p2) = src
        u, v = uv[:, 0], uv[:, 1]
        return uv_points(dst, np.column_stack([b0 + u * (b1 - b0) + v * (b2 - b0), p0 + u * (p1 - p0) + v * (p2 - p0)]))

    g0, g1 = grid(a0), grid(a1)
    at1 = np.concatenate([moved(g0, old["pts"], new["pts"]), g1])  # (the spots, in the new box)
    l0 = np.concatenate([a0.cell(*g0.T, off=a0.outside()), a0.cell(*moved(g1, new["pts"], old["pts"]).T,
                                                                   off=a0.outside())])
    l1 = a1.cell(*at1.T, off=a1.outside())
    ok = (l0 >= 0) & (l1 >= 0)
    l0, l1, at1 = l0[ok], l1[ok], at1[ok]
    n1 = a1.count
    both = np.bincount(l0 * n1 + l1, minlength=a0.count * n1)
    size1 = np.bincount(l1, minlength=n1)
    pairs = np.flatnonzero(both)
    i, j = pairs // n1, pairs % n1
    share = both[pairs] / size1[j]  # (how much of the new area came from the old one)
    match, taken = {}, set()
    for k in np.argsort(-share, kind="stable"):
        if i[k] not in match and j[k] not in taken:
            match[int(i[k])] = int(j[k])
            taken.add(int(j[k]))
    old_labs = a0.at(*np.asarray([a[:2] for a in old["areas"]], float).reshape(-1, 2).T)
    new_labs = a1.at(*np.asarray([a[:2] for a in areas], float).reshape(-1, 2).T)
    source = both.reshape(a0.count, n1).argmax(0)  # (the old area most of each new one came from)
    lost, out = [], []
    for a, lab0, lab1 in zip(areas, old_labs.tolist(), new_labs.tolist()):
        to = match.get(lab0) if lab0 >= 0 else None
        if to is None:
            lost.append(a)
        elif to == lab1 or lab1 >= 0 and lab1 not in taken and source[lab1] == lab0:  # (or: a piece of its area)
            out.append(a)
        else:  # into its area: the spot there nearest the middle of it
            pts = at1[l1 == to]
            mid = np.median(pts, axis=0)
            best = pts[np.argmin(np.hypot(*(pts - mid).T))]
            out.append([round(float(best[0]), 6), round(float(best[1]), 6), a[2]])
    return lost + out


def settled_areas(old, new):
    """new["areas"] after lines were drawn, erased or changed in one go (not dragged: carry_areas): each colour goes
    to the new area most of its old area became, so a new line splitting a coloured area leaves the colour on the
    bigger part and the new part starts as normal (user). A colour whose area is gone (its lines erased, or merged
    into another coloured area that kept more of it) is forgotten: it doesn't come back on lines drawn there later
    (user). old: the shape before, its areas in the same order. Returns the new areas list."""
    areas = new.get("areas") or []
    a0, a1 = (shape_areas(old), shape_areas(new)) if areas else (None, None)
    if a0 is None or a1 is None:
        return areas
    ys, xs = np.mgrid[0:a1.h:2, 0:a1.w:2]  # (every other cell of the new map, in u, v)
    pts = (np.column_stack([xs.ravel(), ys.ravel()]) + 0.5) / a1.k + a1.lo
    (b0, p0), (b1, p1), (b2, p2) = new["pts"]
    u, v = pts[:, 0], pts[:, 1]
    back = uv_points(old["pts"], np.column_stack([b0 + u * (b1 - b0) + v * (b2 - b0),
                                                  p0 + u * (p1 - p0) + v * (p2 - p0)]))
    l0, l1 = a0.cell(*back.T), a1.cell(u, v)
    ok = (l0 >= 0) & (l1 >= 0)
    l0, l1, pts = l0[ok], l1[ok], pts[ok]
    n1 = a1.count
    both = np.bincount(l0 * n1 + l1, minlength=a0.count * n1).reshape(a0.count, n1)
    labs = a0.at(*np.asarray([a[:2] for a in old["areas"]], float).reshape(-1, 2).T).tolist()
    best = {}  # new area -> (how much of the old area went there, which spot)
    for i, lab in enumerate(labs):
        if lab < 0:  # (no free cell by it: left as it is)
            best[("as is", i)] = (0, i)
            continue
        if lab == a0.outside() or not both[lab].any():
            continue
        to = int(both[lab].argmax())
        if to != a1.outside() and (to not in best or both[lab, to] >= best[to][0]):
            best[to] = (int(both[lab, to]), i)
    out = []
    for to, (_, i) in sorted(best.items(), key=lambda t: t[1][1]):
        a = areas[i]
        if isinstance(to, tuple) or int(a1.at([a[0]], [a[1]])[0]) == to:
            out.append(a)
            continue
        here = pts[l1 == to]  # (into its area: the spot there nearest the middle of it)
        mid = np.median(here, axis=0)
        spot = here[np.argmin(np.hypot(*(here - mid).T))]
        out.append([round(float(spot[0]), 6), round(float(spot[1]), 6), a[2]])
    return out


def area_cuts(sh):
    """The lines that split areas without closing a loop, in beats / keys: fill lines, and outline lines too flat to
    fill (fill_plan "flat": a box's side touching the rest only partway along its lines, user, was no wall)."""
    return role_paths(sh)["cut"] + fill_plan(sh)["flat"]


def area_spans(sh, ppq):
    """has_areas shapes: (start, end, key, colour) ticks of every stretch filled, colour 0 = the shape's own.
    Remembered like inside_spans."""
    key = ("areas", json.dumps([sh["strokes"], sh["pts"], sh["areas"]]), bool(sh.get("union")), ppq)
    got = _spans.get(key)
    if got is None:
        if len(_spans) > 100:
            _spans.clear()
        got = _spans[key] = find_area_spans(sh, ppq)
        got.flags.writeable = False
    return got


def area_paint(sh, amap):
    """Each area's colour as given by hand (-1: as normal, 0: empty, k: colour k); one more at the end for walls.
    The area around the drawing never takes one (a coloured area whose lines were erased: its spot is out there,
    and coloured the gaps between the drawing's parts)."""
    paint = np.full(amap.count + 1, -1, np.int64)
    seeds = np.asarray([a[:2] for a in sh["areas"]], float).reshape(-1, 2)
    out = amap.outside()
    for lab, a in zip(amap.at(seeds[:, 0], seeds[:, 1]).tolist(), sh["areas"]):
        if lab >= 0 and lab != out:
            paint[lab] = a[2]
    return paint


def area_state(sh, amap):
    """Each area's (filled, colour) as Fill / Spam make it (one more at the end for walls: not filled, -1)."""
    paint = area_paint(sh, amap)
    filled = np.append(np.where(paint[:-1] >= 0, paint[:-1] > 0, areas_filled(sh, amap)), False)
    return filled, np.append(np.where(paint[:-1] > 0, paint[:-1], 0), -1)


def areas_filled(sh, amap):
    """Which areas of AreaMap amap Fill / Spam fill as normal (nothing coloured)."""
    return filled_spots(sh, amap.spots[:, 0], amap.spots[:, 1])


def filled_spots(sh, u, v):
    """Which spots (u, v arrays, in the shape's box) Fill / Spam fill as normal (nothing coloured)."""
    if fill_test(sh):
        return filled_uv(sh, u, v)
    loops = [uv_points(sh["pts"], p) for p in fill_plan(sh)["polys"]]
    return inside_loops(u, v, loops)


def area_lines(sh):
    """Every line of a custom shape that cuts areas (Fill / Spam loops, lines ending on lines and fill lines), (ax,
    ay, bx, by) in beats / keys."""
    return shrink_segments(fill_plan(sh)["polys"] + fill_plan(sh)["attached"] + area_cuts(sh))


def area_edges(sh, amap, borders_only=False):
    """The lines with a filled area on one side only ("Outline between colours": or two colours meeting), as
    (ax, ay, bx, by) in beats / keys: where the outline gate's band grows in from. borders_only: just the lines
    with two different colours on their sides."""
    seg = area_lines(sh)
    if not len(seg):
        return seg
    filled, colour = area_state(sh, amap)
    a, b = uv_points(sh["pts"], seg[:, :2]), uv_points(sh["pts"], seg[:, 2:])
    # long lines cut into pieces a few map cells long, each looked at on its own: what's beside a line changes
    # where other lines cross it (one looked at in its middle only grew the band along all of it, user)
    cells = np.hypot(*((b - a) * amap.k).T)
    n = np.maximum(1, np.ceil(cells / 4).astype(np.int64))
    i = np.repeat(np.arange(len(seg)), n)
    t0 = (np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)) / np.repeat(n, n)
    t1 = t0 + 1 / np.repeat(n, n)
    seg = np.column_stack([seg[i, :2] + (seg[i, 2:] - seg[i, :2]) * t0[:, None],
                           seg[i, :2] + (seg[i, 2:] - seg[i, :2]) * t1[:, None]])
    a, b = a[i] + (b[i] - a[i]) * t0[:, None], a[i] + (b[i] - a[i]) * t1[:, None]
    d = (b - a) * amap.k  # (in map cells)
    ln = np.hypot(d[:, 0], d[:, 1])
    ok = ln > 1e-9
    n = np.column_stack([-d[:, 1], d[:, 0]]) / np.where(ok, ln, 1)[:, None] * 1.5 / amap.k
    mid = (a + b) / 2
    one, two = amap.at(*(mid + n).T), amap.at(*(mid - n).T)
    meet = filled[one] & filled[two] & (colour[one] != colour[two])
    differ = meet if borders_only else (filled[one] != filled[two]) | (meet & bool(sh.get("borders")))
    keep = np.flatnonzero(ok & differ)
    if not len(keep):
        return seg[keep]
    # pieces kept one after another on the same line: one piece again (fewer for the band's distances: quicker)
    new = np.ones(len(keep), bool)
    new[1:] = (i[keep][1:] != i[keep][:-1]) | (keep[1:] != keep[:-1] + 1)
    first = keep[new]
    last = keep[np.append(np.flatnonzero(new)[1:] - 1, len(keep) - 1)]
    return np.column_stack([seg[first, :2], seg[last, 2:]])


def area_inner_lines(sh, ppq, g, keys):
    """What's filled (areas.py) shrunk by g ticks from area_edges, on SAMPLES lines across each of these keys:
    (heights, [[(start, end)] in beats] per line)."""
    ys, on, edges, k = area_filled_lines(sh, tuple(keys))
    if edges is None:
        return ys, [[] for _ in ys]
    near = shrink_near(edges * [1, k, 1, k], [y * k for y in ys], g / ppq)
    return ys, [shrink_merge(shrink_minus(spans, cut)) for spans, cut in zip(on, near)]


def area_filled_lines(sh, keys):
    """What the areas fill on SAMPLES lines across each of these keys ([(start, end)] in beats per line), with the
    heights, area_edges and the shape's proportion (shrink.proportion). Remembered: the same for every outline gate
    tried (working it out was most of a gate step's time)."""
    key = ("filled", json.dumps([sh["strokes"], sh["pts"], sh["areas"]]), bool(sh.get("union")),
           bool(sh.get("borders")), keys)
    got = _inner.get(key)
    if got is not None:
        return got
    ys = [(q - 0.5 + (j + 0.5) / SAMPLES) for q in keys for j in range(SAMPLES)]
    amap = shape_areas(sh)
    if amap is None:
        return ys, None, None, 1.0
    filled, _ = area_state(sh, amap)
    lines = area_lines(sh)
    ay, by = lines[:, 1], lines[:, 3]
    out = []
    for y in ys:
        s = lines[(ay <= y) != (by <= y)]
        x = np.sort(s[:, 0] + (s[:, 2] - s[:, 0]) * (y - s[:, 1]) / (s[:, 3] - s[:, 1]))
        if len(x) < 2:
            out.append([])
            continue
        m = (x[:-1] + x[1:]) / 2
        uv = uv_points(sh["pts"], np.column_stack([m, np.full(len(m), y)]))
        on = filled[amap.fine_at(uv[:, 0], uv[:, 1])]  # (exact: a piece thinner than a cell is no free cell)
        out.append(list(zip(x[:-1][on].tolist(), x[1:][on].tolist())))
    if len(_inner) > 100:
        _inner.clear()
    got = _inner[key] = (ys, out, area_edges(sh, amap),
                         shrink_proportion(fill_plan(sh)["polys"] + fill_plan(sh)["attached"] + area_cuts(sh)))
    return got


def area_inner(sh, ppq, g, keys):
    """inner_ticks for coloured areas: what's filled (areas.py) shrunk by g ticks from area_edges."""
    _, per = area_inner_lines(sh, ppq, g, keys)
    out = []
    for n, q in enumerate(keys):
        got = [p for spans in per[n * SAMPLES:(n + 1) * SAMPLES] for p in spans]
        row = []
        for a, b in shrink_merge(got):
            s, e = math.floor(a * ppq + 0.5), math.floor(b * ppq + 0.5)
            if e <= s:
                continue
            # a gap narrower than the gate closed: at a sloping colour border (filled on both sides) the row's lines
            # each leave their gap somewhere else, and what was left between them made 1-tick slivers (user)
            if row and s - row[-1][1] < g:
                row[-1] = (row[-1][0], max(row[-1][1], e), q)
            else:
                row.append((s, e, q))
        out += row
    return np.asarray(out, np.int64).reshape(-1, 3)


def outline_of_lines(ys, per, gap):
    """The outline of what's on these lines (heights ys going up, [(start, end)] on each), joining each end to the
    matching end on the next line: straight pieces (b0, k0, b1, k1, bi, ki), (bi, ki) a spot on the inner side.
    Gaps narrower than gap are closed first."""
    def close(spans):
        out = []
        for a, b in spans:
            if out and a - out[-1][1] < gap:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        return out

    per = [close(p) for p in per]
    dy = ys[1] - ys[0] if len(ys) > 1 else 1.0
    e = dy * 0.25  # (how far the inner side's spot is from a piece)
    segs = []
    lines = [(ys[0] - dy, [])] + list(zip(ys, per)) + [(ys[-1] + dy, [])]
    for (y0, below), (y1, above) in zip(lines, lines[1:]):
        over_b = [[i for i, q in enumerate(above) if q[0] < p[1] and q[1] > p[0]] for p in below]
        over_a = [[i for i, p in enumerate(below) if q[0] < p[1] and q[1] > p[0]] for q in above]
        for i, (a, b) in enumerate(below):
            o = over_b[i]
            if not o:  # (ends between the lines: across at the lower one)
                segs.append((a, y0, b, y0, (a + b) / 2, y0 - e))
                continue
            first, last = above[o[0]], above[o[-1]]
            if over_a[o[0]][0] == i:
                segs.append((a, y0, first[0], y1, (a + first[0]) / 2 + e, (y0 + y1) / 2))
            if over_a[o[-1]][-1] == i:
                segs.append((b, y0, last[1], y1, (b + last[1]) / 2 - e, (y0 + y1) / 2))
            for q0, q1 in zip(o, o[1:]):  # (a gap opening above it)
                x0, x1 = above[q0][1], above[q1][0]
                segs.append((x0, y1, x1, y1, (x0 + x1) / 2, y1 - e))
        for i, (a, b) in enumerate(above):
            o = over_a[i]
            if not o:  # (starts between the lines: across at the upper one)
                segs.append((a, y1, b, y1, (a + b) / 2, y1 + e))
                continue
            for p0, p1 in zip(o, o[1:]):  # (a gap closing above it)
                x0, x1 = below[p0][1], below[p1][0]
                segs.append((x0, y0, x1, y0, (x0 + x1) / 2, y0 + e))
    return np.asarray(segs, float).reshape(-1, 6)


def find_area_spans(sh, ppq):
    none = np.zeros((0, 4), np.int64)
    amap = shape_areas(sh)
    polys = fill_plan(sh)["polys"] + fill_plan(sh)["attached"]
    cuts = area_cuts(sh)
    ps = [p for path in polys + cuts for _, p in path]
    if amap is None or not ps:
        return none
    paint = area_paint(sh, amap)
    parts = [(np.asarray(p, float).reshape(-1, 2), i) for i, p in enumerate(polys)]
    parts += [(np.asarray(p, float).reshape(-1, 2), -1) for p in cuts]
    parts = [(p, i) for p, i in parts if len(p) > 1]
    a = np.concatenate([p[:-1] for p, _ in parts])
    b = np.concatenate([p[1:] for p, _ in parts])
    lid = np.concatenate([np.full(len(p) - 1, i) for p, i in parts])
    keep = a[:, 1] != b[:, 1]
    edges = (a[keep, 0], a[keep, 1], b[keep, 0], b[keep, 1], lid[keep])
    frame = sh["pts"]

    def colour(x, y):  # (pieces, spots) spots inside each piece -> its colour: the area most of them are in
        uv = uv_points(frame, np.column_stack([x.ravel(), y.ravel()]))
        return paint[spot_area(amap, uv[:, 0].reshape(x.shape), uv[:, 1].reshape(x.shape))]

    walled = fill_test(sh)
    rows = [(q, row_cut(edges, q)) for q in range(max(0, pitch_of(min(ps))), min(TOP_KEY, pitch_of(max(ps))) + 1)]
    rows = [(q, cut) for q, cut in rows if cut]
    # (every row's spots asked at once: one row at a time, asking took most of the time)
    asked = []
    for i in (1, 2):  # the pieces' spots, the spots on the rows' middle lines
        x = np.concatenate([cut[i][0] for _, cut in rows]) if rows else np.zeros((0, 3))
        y = np.concatenate([cut[i][1] for _, cut in rows]) if rows else np.zeros((0, 3))
        got = (colour(x, y), walled(x, y) if walled else None) if len(x) else (np.zeros(0, np.int64), None)
        ends = np.cumsum([len(cut[i][0]) for _, cut in rows])[:-1]
        asked.append([np.split(a, ends) if a is not None else [None] * len(rows) for a in got])
    out = []
    for j, (q, cut) in enumerate(rows):
        row = [(math.floor(s * ppq + 0.5), math.floor(e * ppq + 0.5), g)
               for s, e, g in row_done(cut, asked[0][0][j], asked[0][1][j], asked[1][0][j], asked[1][1][j])]
        some = any(e > s for s, e, _ in row)  # (a bit under a tick, e.g. at a tip: only if the row has nothing else)
        out += [(s, max(e, s + 1), q, g) for s, e, g in row if e > s or not some]
    return np.asarray(out, np.int64).reshape(-1, 4) if out else none


def row_pieces(edges, q, walled, colour):
    """Like row_spans, but every line (fill lines too, loop number -1 in edges) cuts the row into pieces, and each
    piece is filled as normal (even-odd, or as walled(x, y) = fill_test says) or as colour(x, y)
    says (-1: as normal, 0: empty, k: colour k).
    [(start, end, colour)] in beats, merged per colour; colour 0 = the shape's own. Where two filled pieces meet,
    they meet where the line between them crosses the middle of the row's slice; on the outside edge the piece
    reaches as far as the line does in the slice (like row_spans, so with nothing coloured the notes are the
    same)."""
    cut = row_cut(edges, q)
    if not cut:
        return []
    (xs, ys), (cxs, cys) = cut[1:3]
    return row_done(cut, colour(xs, ys), walled(xs, ys) if walled else None,
                    colour(cxs, cys) if len(cxs) else np.zeros(0, np.int64),
                    walled(cxs, cys) if walled and len(cxs) else None)


def row_cut(edges, q):
    """row_pieces up to its questions: (pieces, (x, y) spots in each piece, (x, y) spots on the row's middle line
    (centre_spots)), or None when the row has no pieces. The answers go to row_done."""
    lo, hi = q - 0.5, q + 0.5
    xa, ya, xb, yb, lid = edges
    here = np.flatnonzero((np.minimum(ya, yb) < hi) & (np.maximum(ya, yb) > lo))
    if not len(here):
        return None
    xa, ya, xb, yb, lid = xa[here], ya[here], xb[here], yb[here], lid[here]
    near = (xa, ya, xb, yb, lid)  # (for the row's middle line, centre_spots)
    ys = np.concatenate([ya, yb])
    levels = np.unique(np.concatenate([[lo, hi], ys[(ys > lo) & (ys < hi)]]))
    first = np.searchsorted(levels, np.maximum(np.minimum(ya, yb), lo))
    n = np.searchsorted(levels, np.minimum(np.maximum(ya, yb), hi)) - first
    e = np.repeat(np.arange(len(here)), n)
    gap = np.repeat(first, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    y0, y1 = levels[gap], levels[gap + 1]
    mid = (y0 + y1) / 2
    xa, ya, xb, yb, lid = xa[e], ya[e], xb[e], yb[e], lid[e]

    def x_at(y):
        return xa + (xb - xa) * (y - ya) / (yb - ya)

    xm = x_at(mid)
    order = np.lexsort((xm, gap))
    gap, lid, mid, xm = gap[order], lid[order], mid[order], xm[order]
    xl, xr = np.minimum(x_at(y0), x_at(y1))[order], np.maximum(x_at(y0), x_at(y1))[order]
    m = len(gap)
    at = np.arange(m)
    start = np.ones(m, bool)
    start[1:] = gap[1:] != gap[:-1]
    step = (lid >= 0).astype(np.int64)
    run = np.cumsum(step)
    begin = np.maximum.accumulate(np.where(start, at, 0))
    depth = run - run[begin] + step[begin]  # (counted from the slice's left end, up to and with this crossing)
    pi = np.flatnonzero(~np.append(start[1:], True))  # pieces: from crossing i to i + 1 in the same slice
    if not len(pi):
        return None
    # spots spread over each piece (its slice between the two lines), so one near a line can't decide its area
    xs, ys = [], []
    for fy in (0.5, 0.2, 0.8):
        y = y0 + (y1 - y0) * fy
        xy = x_at(y)[order]
        for fx in (0.5, 0.2, 0.8):
            xs.append(xy[pi] + (xy[pi + 1] - xy[pi]) * fx)
            ys.append(y[order][pi])
    cx, clid, cxs, cys = centre_spots(near, q)
    pieces = (pi, depth, xm, xl, xr, (y1 - y0)[order], cx, clid)
    return pieces, (np.column_stack(xs), np.column_stack(ys)), (cxs, cys)


def row_done(cut, c, wall, cpaint, cwall):
    """row_pieces from row_cut's cut and the answers: c = colour and wall = walled (None: even-odd) of the pieces'
    spots, cpaint / cwall the same of the middle line's."""
    pi, depth, xm, xl, xr, tall, cx, clid = cut[0]
    filled = np.where(c >= 0, c > 0, wall if wall is not None else depth[pi] % 2 == 1)
    group = np.where(c > 0, c, 0)
    joined = np.zeros(len(pi), bool)
    joined[1:] = pi[1:] == pi[:-1] + 1  # (the piece before it is in the same slice)
    left_full = np.zeros(len(pi), bool)
    left_full[1:] = joined[1:] & filled[:-1]
    right_full = np.zeros(len(pi), bool)
    right_full[:-1] = joined[1:] & filled[1:]
    left = np.where(left_full, xm[pi], xl[pi])
    right = np.where(right_full, xm[pi + 1], xr[pi + 1])
    lo_, hi_, k, tall = left[filled], right[filled], group[filled], tall[pi][filled]
    if len(set(k.tolist())) <= 1:
        return [(s, e, int(k[0])) for s, e in merge_spans(lo_, hi_)] if len(k) else []
    # colours overlapping in the row (pieces from different slices of it): each bit of time goes to the colour that
    # fills the most of the row's height there, all its slices added up (so a thin sliver by a line can't take the
    # whole row, and a sloping border switches where it crosses the row's middle)
    at = np.unique(np.concatenate([lo_, hi_]))
    a, b = at[:-1], at[1:]
    m = (a + b) / 2
    # (each piece covers the bits from its start to its end: added where it starts, taken off where it ends)
    ia, ib = np.searchsorted(at, lo_), np.searchsorted(at, hi_)
    on = ia < ib
    names, which = np.unique(k, return_inverse=True)
    which = which.reshape(-1)
    step = np.zeros((len(at), len(names)))
    np.add.at(step, (ia[on], which[on]), tall[on])
    np.add.at(step, (ib[on], which[on]), -tall[on])
    height = np.cumsum(step, 0)[:-1]
    best = names[height.argmax(1)]
    count = np.zeros(len(at), np.int64)
    np.add.at(count, ia[on], 1)
    np.add.at(count, ib[on], -1)
    has = np.cumsum(count)[:-1] > 0
    # where two filled colours meet, the switch goes where the line crosses the row's middle (the slices only say
    # how far the filling reaches): the colour of the piece of the middle line each bit is on
    if len(cx) >= 2:
        inside = cwall if cwall is not None else np.cumsum(clid[:-1] >= 0) % 2 == 1
        cfill, cgroup = np.where(cpaint >= 0, cpaint > 0, inside), np.where(cpaint > 0, cpaint, 0)
        j = np.searchsorted(cx, m) - 1
        ok = (j >= 0) & (j < len(cx) - 1)
        ok[ok] &= cfill[j[ok]]
        best = np.where(ok, cgroup[np.clip(j, 0, max(0, len(cgroup) - 1))], best)
    out = []
    for s, e, c in zip(a[has].tolist(), b[has].tolist(), best[has].tolist()):
        if out and out[-1][2] == c and out[-1][1] >= s:
            out[-1] = (out[-1][0], e, c)
        else:
            out.append((s, e, c))
    return out


def colour_at(notes, spans, colours, none=None):
    """For each (start, end, key) note, the colour of the (start, end, key) stretch on its key holding its middle
    (none does: the nearest one before it, else 0; or none if given)."""
    if not len(notes) or not len(spans):
        return np.zeros(len(notes), np.int64)
    order = np.lexsort((spans[:, 0], spans[:, 2]))
    sp, col = spans[order], colours[order]
    big = np.int64(1) << 40
    mid = (notes[:, 0] + notes[:, 1]) // 2
    i = np.searchsorted(sp[:, 2] * big + sp[:, 0], notes[:, 2] * big + mid, "right") - 1
    ok = (i >= 0) & (sp[np.maximum(i, 0), 2] == notes[:, 2])
    if none is None:
        return np.where(ok, col[np.maximum(i, 0)], 0).astype(np.int64)
    ok &= sp[np.maximum(i, 0), 1] > mid
    return np.where(ok, col[np.maximum(i, 0)], none).astype(np.int64)


def border_lines(sh, ppq):
    """"Outline between colours": the lines where two colours meet (area_edges) as notes, like a drawn line's: on
    every key row it crosses, from where it comes into the row to where it leaves, (start, end, key) ticks. The
    border is where its line is (user: going by colour number, the outline switched sides along a flat border
    where the colours beside it changed)."""
    amap = shape_areas(sh)
    seg = area_edges(sh, amap, borders_only=True) if amap is not None else np.zeros((0, 4))
    if not len(seg):
        return np.zeros((0, 3), np.int64)
    up = seg[:, 1] <= seg[:, 3]
    xa, ya = np.where(up, seg[:, 0], seg[:, 2]), np.where(up, seg[:, 1], seg[:, 3])  # (a = the lower end)
    xb, yb = np.where(up, seg[:, 2], seg[:, 0]), np.where(up, seg[:, 3], seg[:, 1])
    q0, q1 = np.floor(ya + 0.5).astype(np.int64), np.floor(yb + 0.5).astype(np.int64)  # (rows it passes)
    n = q1 - q0 + 1
    i = np.repeat(np.arange(len(seg)), n)
    q = q0[i] + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    flat = ya[i] == yb[i]
    lo, hi = np.maximum(ya[i], q - 0.5), np.minimum(yb[i], q + 0.5)
    keep = flat | (hi > lo)  # (in the row, not just touching its edge)
    i, q, lo, hi, flat = i[keep], q[keep], lo[keep], hi[keep], flat[keep]
    dy = np.where(flat, 1, yb[i] - ya[i])
    x0 = np.where(flat, xa[i], xa[i] + (xb[i] - xa[i]) * (lo - ya[i]) / dy)
    x1 = np.where(flat, xb[i], xa[i] + (xb[i] - xa[i]) * (hi - ya[i]) / dy)
    s = np.floor(np.minimum(x0, x1) * ppq + 0.5).astype(np.int64)
    e = np.maximum(np.floor(np.maximum(x0, x1) * ppq + 0.5).astype(np.int64), s + 1)
    return merged_rows(np.column_stack([s, e, q]))


def border_parts(spans, lines):
    """Fill "Outline between colours": the parts of the filled (start, end, key) stretches the colour border lines
    (border_lines) cross."""
    return cut_out(spans, cut_out(spans, lines))


def touching_parts(spans, colours, lines):
    """"Outline between colours": where two colours still touch with the outline (lines, (start, end, key)) taken
    out, side by side on a key or one over the other (where a line runs too near another for the map's cells, no
    border is found, e.g. a curve touching a side), the touching part of the shorter stretch, as (start, end, key)
    notes: the outline never lets two colours touch (user)."""
    if not len(spans):  # (nothing filled: every area emptied, or the shape is above the top key)
        return np.zeros((0, 3), np.int64)
    parts = [cut_out(spans[colours == k], lines) for k in np.unique(colours).tolist()]
    col = np.concatenate([np.full(len(p), k, np.int64) for p, k in zip(parts, np.unique(colours).tolist())])
    n = np.concatenate(parts) if parts else np.zeros((0, 3), np.int64)
    by = {}
    for (s, e, k), c in zip(n.tolist(), col.tolist()):
        by.setdefault(k, []).append((s, e, c))
    out = []
    for k, row in by.items():
        row.sort()
        for a, b in zip(row, row[1:]):  # (side by side)
            if a[2] != b[2] and b[0] <= a[1]:
                s, e, _ = min(a, b, key=lambda r: r[1] - r[0])
                out.append((s, e, k))
        for a in row:  # (over the next key's)
            for b in by.get(k + 1, ()):
                s, e = max(a[0], b[0]), min(a[1], b[1])
                if e > s and a[2] != b[2]:
                    out.append((s, e, k if a[1] - a[0] <= b[1] - b[0] else k + 1))
    return np.asarray(out, np.int64).reshape(-1, 3)


def colour_edges(notes, lines):
    """Spam "Outline between colours": which notes a colour border line (border_lines) crosses."""
    return ~covered(notes, cut_out(notes, lines))


def centre_spots(edges, q):
    """Where the lines cross the middle of row q (x in order), their loop numbers, and (x, y) spots on each piece
    between two of them, whose colour says if it's filled and how (like row_pieces)."""
    xa, ya, xb, yb, lid = edges
    c = (np.minimum(ya, yb) <= q) & (np.maximum(ya, yb) > q)
    x = xa[c] + (xb[c] - xa[c]) * (q - ya[c]) / (yb[c] - ya[c])
    o = np.argsort(x, kind="stable")
    x, lid = x[o], lid[c][o]
    if len(x) < 2:
        return x, lid, np.zeros((0, 3)), np.zeros((0, 3))
    spots = np.column_stack([x[:-1] + (x[1:] - x[:-1]) * f for f in (0.5, 0.2, 0.8)])
    return x, lid, spots, np.full((len(x) - 1, 3), float(q))


def merged_rows(spans):
    """(start, end, key) stretches -> the same merged per key where they touch (whatever their colour)."""
    ks, ss, es = merged_by_key(np.asarray(spans, np.int64).reshape(-1, 3))
    return np.column_stack([ss, es, ks]).astype(np.int64).reshape(-1, 3)


def spam_gate(sh, ppq):
    """The spam gate in ticks: a whole number; for Hz bass the exact one as a float (chop_even), or with placed
    tones the array of its repeats (chop_grid), or a KeyGrid when they have effects (chop_keys)."""
    if sh.get("hz"):
        if sh["hz"].get("tones"):
            return squares(sh, ppq)
        gate = max(1.0, float(sh["gate"] * ppq))
        limit = threshold(sh["hz"])
        whole = sh["hz"].get("fixed") or (limit is not None and off_cents(gate) <= limit + 1e-9)
        return float(math.floor(gate + 0.5)) if whole else gate
    return max(1, math.floor(sh["gate"] * ppq + 0.5))


def chop_even(stretches, g, count=False):
    """chop for Hz bass: g = the exact gate in ticks (a float, at least 1). One grid for every stretch, counted
    from tick 0: square n runs from round(n × g) to round((n + 1) × g). A stretch gets the squares whose middle is
    inside it; one too short for any stays one note as it is."""
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    lo = np.ceil(s0 / g - 0.5).astype(np.int64)
    n = np.ceil(e0 / g - 0.5).astype(np.int64) - lo
    short = n <= 0
    n = np.where(short, 1, n)
    if count:
        return n
    k = np.repeat(lo, n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    out = np.column_stack([np.floor(k * g + 0.5).astype(np.int64), np.floor((k + 1) * g + 0.5).astype(np.int64),
                           np.repeat(q, n)])
    if short.any():
        whole = np.repeat(short, n)
        out[whole, 0], out[whole, 1] = s0[short], e0[short]
    return out


def chop_grid(stretches, squares, count=False):
    """chop for Hz bass with placed tones: squares = its repeats, (start, end) ticks in order (hzbass.squares).
    A stretch gets the squares whose middle is inside it, whole."""
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    mids = squares[:, 0] + squares[:, 1]  # (twice the middle, so it stays whole numbers)
    lo = np.searchsorted(mids, 2 * s0, "left")
    n = np.maximum(np.searchsorted(mids, 2 * e0, "left") - lo, 0)
    if count:
        return n
    k = np.repeat(lo, n) + np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
    return np.column_stack([squares[k, 0], squares[k, 1], np.repeat(q, n)])


def chop_keys(stretches, grid, count=False):
    """chop for Hz bass whose tones have effects: every key has its own repeats (hzbass.KeyGrid)."""
    q = stretches[:, 2]
    n = np.zeros(len(stretches), np.int64)
    parts, owners = [], []
    for key in np.unique(q):
        rows = np.flatnonzero(q == key)
        sq = grid.squares(int(key))
        n[rows] = chop_grid(stretches[rows], sq, True)
        if not count:
            parts.append(chop_grid(stretches[rows], sq))
            owners.append(np.repeat(rows, n[rows]))
    if count:
        return n
    if not parts:
        return np.zeros((0, 3), np.int64)
    return np.concatenate(parts)[np.argsort(np.concatenate(owners), kind="stable")]


def chop(sh, stretches, g, count=False):
    """stretches: NumPy array of (start, end, key) rows in ticks -> each filled with back-to-back notes of gate g,
    as an array of (start, end, key) rows in the same order. Where they start: ALIGNS; what happens to the bit that
    doesn't fit a whole gate: ENDS. count: just how many notes each stretch gets."""
    if isinstance(g, KeyGrid):
        return chop_keys(stretches, g, count)
    if isinstance(g, np.ndarray):
        return chop_grid(stretches, g, count)
    if isinstance(g, float):
        return chop_even(stretches, g, count)
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    size = e0 - s0
    ends, align = sh.get("ends", "drop"), sh.get("align", "auto")

    def number(n):  # note number inside its stretch
        return np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)

    if ends in ("round", "stretch"):
        n = np.maximum(1, (2 * size + g) // (2 * g))  # whole gates, rounded (half up)
        if ends == "stretch":
            if count:
                return n
            k, a, m, length = number(n), np.repeat(s0, n), np.repeat(n, n), np.repeat(size, n)
            return np.column_stack([a + length * k // m, a + length * (k + 1) // m, np.repeat(q, n)])
        if align == "aligned":  # the gate grid's squares with at least half a gate of the stretch in them
            lo = -((g - 2 * s0) // (2 * g))
            n = (2 * e0 + g) // (2 * g) - lo
            none = n <= 0  # (none: the square the stretch's middle is in)
            first = np.where(none, (s0 + e0) // 2 // g, lo) * g
            n = np.where(none, 1, n)
        else:
            first = s0 + (size - n * g) // 2 if align == "centred" else s0
        if count:
            return n
        starts = np.repeat(first, n) + number(n) * g
        return np.column_stack([starts, starts + g, np.repeat(q, n)])
    if align == "aligned":
        s = -(-s0 // g) * g  # the first gate line at or after s0
    else:
        s = s0 + size % g // 2 if align == "centred" else s0
    n = np.maximum(0, (e0 - s) // g)
    if ends == "keep":  # a shorter note before the first whole gate (aligned / centred) and after the last
        head_end = np.minimum(s, e0)
        head = head_end > s0
        tail = np.where(s < e0, s + n * g, e0) < e0
        c = n + head + tail
        if count:
            return c
        k = number(c)
        starts = np.repeat(s, c) + (k - np.repeat(head, c)) * g
        stops = starts + g
        first, last = np.repeat(head, c) & (k == 0), np.repeat(tail, c) & (k == np.repeat(c, c) - 1)
        starts = np.where(first, np.repeat(s0, c), starts)
        stops = np.where(first, np.repeat(head_end, c), np.where(last, np.repeat(e0, c), stops))
        return np.column_stack([starts, stops, np.repeat(q, c)])
    short = n == 0  # too short for even one gate: stays one note as it is ("min": at least a quarter gate)
    n = np.where(short, 1, n)
    if count:
        return n
    starts = np.repeat(s, n) + number(n) * g
    out = np.column_stack([starts, starts + g, np.repeat(q, n)])
    if short.any():
        whole = np.repeat(short, n)
        a, b = s0[short], e0[short]
        if ends == "min":
            least = max(1, -(-g // 4))
            grow = b - a < least
            a = np.where(grow, a + (b - a - least) // 2, a)
            b = np.where(grow, a + least, b)
        out[whole, 0], out[whole, 1] = a, b
    return out


def chop_count(sh, stretches, g):
    """How many notes chop makes of stretches (a list or array of (start, end, key))."""
    st = np.asarray(stretches, np.int64).reshape(-1, 3)
    return int(chop(sh, st, g, count=True).sum()) if len(st) else 0


def stroke_groups(sh):
    """{group: [stroke numbers]} of a custom shape whose strokes came from different shapes (convert.py: each
    shape's strokes make their notes on their own, like the shapes did) or have outline colours of their own
    (colour_of), or None if they're all one."""
    groups = {}
    for k, st in enumerate(sh["strokes"]):
        groups.setdefault((st.get("src", -1), colour_of(st)), []).append(k)
    return groups if len(groups) > 1 else None


def coloured_strokes(sh):
    """Some stroke has an outline colour of its own (its notes then go on a channel of their own)."""
    return not sh.get("text") and "notes" not in sh and any(colour_of(st) for st in sh["strokes"])


def outline_groups(sh, ppq, spam=False):
    """The outline's notes (spam: chopped like Outline spam) and which stroke group each belongs to (None if the
    strokes are all one group, see stroke_groups)."""
    groups = stroke_groups(sh)
    g = edge_gate(sh, ppq)
    spans = inside_rows(sh, ppq) if g else None
    if groups is None:
        notes = thicker(sh, outline_notes(sh, ppq), spans, g, ppq)
        return (chop_outline(sh, notes, ppq) if spam else notes), None
    parts, ids = [], []
    for n, (_, strokes) in enumerate(sorted(groups.items())):
        # (the even band goes with the first group: it's the whole shape's)
        notes = outline_notes(sh, ppq, strokes)
        notes = thicker(sh, notes, spans, g, ppq) if n == 0 or sh.get("edge_mode") == "sideways" else notes
        if spam:
            notes = chop_outline(sh, notes, ppq)
        parts.append(notes)
        ids.append(np.full(len(notes), n, np.int64))
    return np.concatenate(parts), np.concatenate(ids)


def edge_gate(sh, ppq):
    """The shape's smallest outline gate in ticks (sh["edge"], in beats; 0 = off)."""
    return max(1, int(round(sh["edge"] * ppq))) if sh.get("edge") else 0


def inside_rows(sh, ppq):
    """The inside's stretches as (start, end, key) ticks (none when the shape can't be filled)."""
    if not fillable(sh["strokes"]):
        return np.zeros((0, 3), np.int64)
    if has_areas(sh):
        return merged_rows(area_spans(sh, ppq)[:, :3])
    return np.asarray(inside_spans(sh, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]


def span_of(notes, spans):
    """For each (start, end, key) note, the inside stretch on its key that holds its middle: (a, b, found)."""
    n = len(notes)
    if not n or spans is None or not len(spans):
        return np.zeros(n, np.int64), np.zeros(n, np.int64), np.zeros(n, bool)
    lo = int(min(notes[:, 0].min(), spans[:, 0].min()))
    big = int(max(notes[:, 1].max(), spans[:, 1].max())) - lo + 2
    sp = spans[np.lexsort((spans[:, 0], spans[:, 2]))]
    mid = (notes[:, 0] + notes[:, 1]) // 2
    i = np.searchsorted(sp[:, 2] * big + (sp[:, 0] - lo), notes[:, 2] * big + (mid - lo), "right") - 1
    found = i >= 0
    i = np.maximum(i, 0)
    found &= (sp[i, 2] == notes[:, 2]) & (mid <= sp[i, 1])
    return sp[i, 0], sp[i, 1], found


def grow_inward(notes, spans, g):
    """Outline notes shorter than g ticks made g long, growing into the inside (user: the shape's edge stays where
    it is): a note in the left half of its key's inside grows right, one in the right half grows left, never past
    the middle (so a gate too big for the shape is cut to what fits). Notes with no inside beside them (open
    lines) stay as they are."""
    if not g or not len(notes):
        return notes
    a, b, found = span_of(notes, spans)
    s, e = notes[:, 0].copy(), notes[:, 1].copy()
    m = (a + b) // 2
    left = found & (s + e <= a + b)
    right = found & ~left
    e = np.where(left, np.maximum(e, np.minimum(s + g, m)), e)
    s = np.where(right, np.minimum(s, np.maximum(e - g, m)), s)
    return np.column_stack([s, e, notes[:, 2]]).astype(np.int64)


def near_edge(notes, spans, g):
    """Spam "Outline" with a smallest outline gate: the spam notes that start or end within g ticks of their
    key's inside edge (so the outline is at least g thick, all of it when the shape is thinner)."""
    a, b, found = span_of(notes, spans)
    return found & ((notes[:, 0] - a < g) | (b - notes[:, 1] < g))


def edge_loops(sh):
    """The lines the inside is made of (fill_plan's loops; text: its letters), and fill_test (None: even-odd; then
    every line given is part of a loop, else it's walls(sh))."""
    tx = sh.get("text")
    walled = fill_test(sh)
    return (custom_strokes(sh) if tx else walls(sh) if walled else fill_plan(sh)["polys"]), walled


def inner_ticks(sh, ppq, g, spans):
    """The shape shrunk inward by the outline gate g (ticks) as (start, end, key) notes on the keys of its inside
    (shrink.inner_rows: a smaller copy of its own outline). Remembered."""
    keys = sorted(set(spans[:, 2].tolist()))
    key = (json.dumps([sh["strokes"], sh["pts"], sh.get("text")]), bool(sh.get("union")), ppq, g, keys[0], keys[-1])
    if has_areas(sh):  # (what the coloured areas fill, shrunk from its own edge)
        key += (json.dumps(sh["areas"]), bool(sh.get("borders")))
    got = _inner.get(key)
    if got is None:
        if len(_inner) > 100:
            _inner.clear()
        if has_areas(sh):
            got = _inner[key] = area_inner(sh, ppq, g, keys)
        else:
            loops, walled = edge_loops(sh)
            got = _inner[key] = inner_rows(loops, walled, keys, g / ppq, ppq)
    return got


def with_band(notes, spans, inner):
    """Outline notes and the band between the inside's edge and the inner shape, as notes merged per key."""
    band = cut_out(spans, inner)
    ks, ss, es = merged_by_key(np.concatenate([np.asarray(notes, np.int64).reshape(-1, 3), band]))
    return np.column_stack([ss, es, ks]).astype(np.int64).reshape(-1, 3)


def edge_inner(sh, ppq):
    """How far in sh's outline gate reaches, for the piano roll's preview line: ("lines", pieces (b0, k0, b1, k1)
    of the shrunk shape's outline) for the even band, ("rows", (start, end, key) notes of the inside left) for Grow
    sideways, or None where the outline gate does nothing."""
    if not (sh.get("fill") in ("empty", "outline_spam") or sh.get("apart") and sh.get("fill") in ("fill", "spam")):
        return None
    g = edge_gate(sh, ppq)
    spans = inside_rows(sh, ppq) if g else ()
    if not len(spans):
        return None
    if sh.get("edge_mode") == "sideways":
        return "rows", cut_out(spans, grow_inward(outline_notes(sh, ppq), spans, g))
    loops, walled = edge_loops(sh)
    if has_areas(sh):  # (worked out exactly on the notes' lines across each key and joined up: the grid over the
        # whole shape was too coarse for hairline gaps between areas, user saw it zigzag)
        keys = list(range(int(spans[:, 2].min()), int(spans[:, 2].max()) + 1))
        key = ("lines", json.dumps([sh["strokes"], sh["pts"], sh["areas"]]), bool(sh.get("union")),
               bool(sh.get("borders")), ppq, g)
        got = _inner.get(key)
        if got is None:
            if len(_inner) > 100:
                _inner.clear()
            got = _inner[key] = outline_of_lines(*area_inner_lines(sh, ppq, g, keys), g / ppq)
        return "lines", got
    return "lines", inner_lines(loops, walled, g / ppq)


def thicker(sh, notes, spans, g, ppq):
    """Outline notes with the smallest outline gate: the even band (default: down to the shape shrunk inward) or
    each note grown sideways (the first way, kept as a second option: sh["edge_mode"] = "sideways")."""
    if not g or not len(spans):
        return notes
    if sh.get("edge_mode") == "sideways":
        return grow_inward(notes, spans, g)
    return with_band(notes, spans, inner_ticks(sh, ppq, g, spans))


def chop_outline(sh, notes, ppq):
    """Outline notes chopped into back-to-back notes of the spam gate (spam start and ends like Spam; a note too
    short for even one gate never vanishes, so steep parts of the outline stay)."""
    return chop(sh, np.asarray(notes, np.int64).reshape(-1, 3), spam_gate(sh, ppq))


def outline_spam(sh, ppq):
    """The outline in notes of the spam gate (chop_outline)."""
    return outline_groups(sh, ppq, spam=True)[0]


def custom_note_count(sh, ppq):
    """How many notes a custom shape makes, without making them (spam can be millions)."""
    if "notes" in sh:
        return len(unpack_notes(sh["notes"]))
    if sh.get("apart") and sh["fill"] in ("fill", "spam"):
        return None  # (made to count them)
    if sh["fill"] == "outline_spam":
        return chop_count(sh, outline_groups(sh, ppq)[0], spam_gate(sh, ppq))
    if sh["fill"] == "empty" or not fillable(sh["strokes"]):
        return None
    flat = np.concatenate([flat_notes(sh, ppq), edge_notes(sh, ppq)])
    if has_areas(sh):
        spans = area_spans(sh, ppq)[:, :3]
        if sh["fill"] == "spam":  # (chopped as whole stretches, whatever their colours)
            spans = merged_rows(spans)
    else:
        spans = np.asarray(inside_spans(sh, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]
    if sh["fill"] == "fill":
        return len(spans) + len(flat)
    g = spam_gate(sh, ppq)
    return chop_count(sh, spans, g) + chop_count(sh, flat, g)


def flat_notes(sh, ppq):
    """The outline notes of a filled shape's parts too flat to fill and lines ending on lines (fill_plan), so they
    don't vanish (a line across a filled area: both sides stay filled, its notes show it, user). With coloured
    areas not where two colours meet: each colour fills up to the line as one note (they cut the colour beside them
    in two, and in Spam covered "Outline between colours", user)."""
    flat = [] if sh.get("text") else fill_plan(sh)["flat"] + fill_plan(sh)["attached"]
    if not flat:
        return np.zeros((0, 3), np.int64)
    notes = paths_outline(flat, ppq)
    if has_areas(sh) and len(notes):  # (a line's notes go by where it crosses the keys, the colours by the rows'
        # middles: half a key apart, so not the colours beside each note but the borders, a tick either way)
        near = border_lines(sh, ppq) + [-1, 1, 0]
        notes = notes[covered(notes, cut_out(notes, near))] if len(near) else notes
    return notes


def edge_notes(sh, ppq):
    """The notes of a filled shape's outline-only strokes (drawn over what's filled)."""
    return edge_lines(sh, ppq)[0]


def edge_lines(sh, ppq):
    """edge_notes, and each one's outline colour (colour_of; 0 = the shape's own)."""
    if sh.get("text"):
        return np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    paths = custom_strokes(sh)
    by = {}
    for st, path in zip(sh["strokes"], paths):
        if role_of(st) == "edge":
            by.setdefault(colour_of(st), []).append(path)
    parts = [(paths_outline(join_paths(ps), ppq), c) for c, ps in sorted(by.items())]
    if not parts:
        return np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    return (np.concatenate([n for n, _ in parts]),
            np.concatenate([np.full(len(n), c, np.int64) for n, c in parts]))


def custom_notes(sh, ppq):
    """Empty = the outline; Fill = one note per stretch of each key inside; Spam = each stretch filled with
    back-to-back notes of the spam gate (chop: where they start and what happens to the bit that doesn't fit a
    whole gate). Outline spam = the outline chopped the same way (open ends are fine)."""
    return custom_notes_groups(sh, ppq)[0]


def custom_notes_groups(sh, ppq):
    """custom_notes, and which group each note belongs to (None = all one: see outline_groups). "Colours"
    (sh["cycle"]): every group split into its turns (cycle_turns); with "Outline" the outline stays one group."""
    notes, groups = _notes_groups(sh, ppq)
    if not cycling(sh) or not len(notes):
        return notes, groups
    n = sh["cycle"]["n"]
    turn = cycle_turns(sh, notes, ppq)
    if groups is None:
        return notes, turn
    if sh.get("apart") and sh["fill"] in ("fill", "spam"):  # (0 = the outline, 1 = the inside, 1 + k = colour k)
        return notes, np.where(groups == 0, 0, 1 + (groups - 1) * n + turn).astype(np.int64)
    return notes, groups * n + turn


def cycling(sh):
    """The shape's notes take turns over channels ("Colours"; not pasted notes, they keep their tracks)."""
    return bool(sh.get("cycle")) and "notes" not in sh


def cycle_turns(sh, notes, ppq):
    """Which turn (0 .. n - 1) each (start, end, key) note gets (CYCLES). Spam steps are counted from the shape's
    first note (with the "aligned" start from tick 0, so they keep to the gate grid), at the shape's gate (Hz bass:
    its tone's gate), each note in the step it starts nearest to; other notes: each start time is a step."""
    c = sh["cycle"]
    s = notes[:, 0]
    if c["by"] == "key":
        k = notes[:, 2] - notes[:, 2].min()
    elif c["by"] == "time":
        a, b = c["every"]
        k = np.floor(s * b / (4 * a * ppq) + 1e-9).astype(np.int64)
        return k % c["n"]
    elif sh.get("kind") == "custom" and sh.get("fill") in SPAM_FILLS:
        g = max(1.0, sh["gate"] * ppq)
        x0 = 0 if sh.get("align") == "aligned" and not sh.get("hz") else int(s.min())
        k = np.floor((s - x0) / g + 0.5).astype(np.int64)
    else:
        k = np.unique(s, return_inverse=True)[1].reshape(-1)
    return (k // int(c["every"])) % c["n"]


def _notes_groups(sh, ppq):
    if "notes" in sh:
        return block_notes(sh, ppq)[:, :3], None
    if sh["fill"] == "outline_spam":
        return outline_groups(sh, ppq, spam=True)
    if sh["fill"] == "empty" or not fillable(sh["strokes"]):
        return outline_groups(sh, ppq)
    area = None  # (areas coloured by hand: each stretch's colour, 0 = the shape's own)
    if has_areas(sh):
        got = area_spans(sh, ppq)
        spans, area = got[:, :3], got[:, 3]
        rows = merged_rows(spans)  # (what's filled, whatever its colour)
    else:
        spans = rows = np.asarray(inside_spans(sh, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]  # (start, end, key)
    flat = flat_notes(sh, ppq)  # (like Outline spam in Spam)
    # (with coloured areas the colour filled where each is, so they blend in as without colours; not the shape's
    # own colour all along the line, showing between two colours like an outline, user)

    def flat_area(n):
        return colour_at(n, spans, area, none=0)

    # outline-only strokes: over what's filled, the outline's with "Outline"; their colours (colour_of) are the same
    # numbers as the areas'
    lines, lc = edge_lines(sh, ppq)
    coloured = area is not None or bool(lc.any())
    apart = sh.get("apart")
    g = edge_gate(sh, ppq) if apart else 0  # (the smallest outline gate)
    zeros = np.zeros
    if sh["fill"] == "fill":
        notes = np.concatenate([spans, flat])
        if apart:  # the edge's notes, and the inside's long notes between them
            outline = edge_parts(notes)
            if g:
                ks, ss, es = merged_by_key(thicker(sh, outline, rows, g, ppq))
                outline = np.column_stack([ss, es, ks]).astype(np.int64).reshape(-1, 3)
            if area is not None and sh.get("borders"):
                outline = merged_rows(np.concatenate([outline, border_parts(spans, border_lines(sh, ppq))]))
                outline = merged_rows(np.concatenate([outline, touching_parts(spans, area, outline)]))
            if area is None:
                inside = cut_out(notes, outline)
                ids = np.ones(len(inside), np.int64)
            else:  # (each colour's own stretches; 1 = the shape's own colour, 1 + k = colour k)
                parts = [(cut_out(spans[area == k], outline), 1 + k) for k in np.unique(area).tolist()]
                fl = cut_out(flat, outline)
                inside = np.concatenate([p for p, _ in parts] + [fl])
                ids = np.concatenate([np.full(len(p), k, np.int64) for p, k in parts] + [1 + flat_area(fl)])
            return (np.concatenate([outline, lines, inside]),
                    np.concatenate([zeros(len(outline), np.int64), np.where(lc > 0, 1 + lc, 0), ids]))
        if not coloured:
            return np.concatenate([notes, lines]), None
        own = zeros(len(spans), np.int64) if area is None else area
        return np.concatenate([notes, lines]), np.concatenate([own, zeros(len(flat), np.int64) if area is None
                                                               else flat_area(flat), lc])
    gate = spam_gate(sh, ppq)
    # with coloured areas the whole filled stretch is chopped as one (like without them: no gaps where colours
    # meet), then each note takes the colour where its middle is
    main = chop(sh, rows, gate)
    notes = np.concatenate([main, chop_outline(sh, flat, ppq)])
    ids = None
    if area is not None:
        ids = np.concatenate([colour_at(main, spans, area), flat_area(notes[len(main):])])
    lc = np.repeat(lc, chop(sh, lines, gate, count=True)) if len(lines) else lc
    lines = chop_outline(sh, lines, ppq)
    if apart:  # the same spam; the notes on the edge of what's filled are the outline's
        if not g:
            edge = on_edge(notes)
        elif sh.get("edge_mode") == "sideways":
            edge = on_edge(notes) | near_edge(notes, rows, g)
        else:  # (the even band: every note not wholly in the shrunk inside)
            edge = on_edge(notes) | ~covered(notes, inner_ticks(sh, ppq, g, rows))
        if area is not None and sh.get("borders"):
            border = border_lines(sh, ppq)
            edge |= colour_edges(notes, np.concatenate([border, touching_parts(spans, area, border)]))
        inner = 1 if ids is None else 1 + ids
        return (np.concatenate([notes, lines]),
                np.concatenate([np.where(edge, 0, inner), np.where(lc > 0, 1 + lc, 0)]).astype(np.int64))
    if not coloured:
        return np.concatenate([notes, lines]), None
    return np.concatenate([notes, lines]), np.concatenate([zeros(len(notes), np.int64) if ids is None else ids, lc])


def tracks_apart(sh):
    """True if the shape's tracks must get channels of their own with Multi channel: pasted notes (each copied
    track keeps its own channel even when the tracks don't overlap), Fill / Spam with "Outline" (the outline
    and the inside) and "Colours" (each turn, any shape)."""
    if sh["kind"] != "custom":
        return cycling(sh)
    return ("notes" in sh or bool(sh.get("apart") and sh.get("fill") in ("fill", "spam")) or cycling(sh)
            or has_areas(sh) and any(a[2] for a in sh["areas"]) or coloured_strokes(sh))


def capped_colours(groups):
    """A shape's groups (custom_notes_groups) kept to COLOURS colours (user: a shape never has more than 15, like a
    MIDI player's colours; the outline and the shape's own fill count too): the groups past the 15th used are
    merged into the 15th, so none wraps round onto another's colour."""
    ids = np.unique(groups)
    return groups if len(ids) <= COLOURS else np.minimum(groups, ids[COLOURS - 1])


def merged_by_key(notes):
    """(start, end, key) notes -> per key, the stretches they cover: (key, start, end) arrays sorted by key and
    start, not overlapping."""
    if not len(notes):
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64)
    a = notes[np.lexsort((notes[:, 0], notes[:, 2]))]
    s, e, k = a[:, 0], a[:, 1], a[:, 2]
    from notes.engine import running_max
    run = running_max(e, k)
    new = np.ones(len(a), bool)
    new[1:] = (k[1:] != k[:-1]) | (s[1:] > run[:-1])
    at = np.nonzero(new)[0]
    return k[at], s[at], np.maximum.reduceat(e, at)


def covered(notes, others):
    """Which notes lie wholly inside what the others cover on the same key."""
    ks, ss, es = merged_by_key(others)
    if not len(ks) or not len(notes):
        return np.zeros(len(notes), bool)
    big = np.int64(1) << 40
    i = np.searchsorted(ks * big + ss, notes[:, 2] * big + notes[:, 0], "right") - 1  # the last stretch starting at or before
    ok = i >= 0
    i = np.maximum(i, 0)
    return ok & (ks[i] == notes[:, 2]) & (es[i] >= notes[:, 1])


def shifted(notes, keys):
    out = notes.copy()
    out[:, 2] += keys
    return out


def on_edge(notes):
    """Fill / Spam "Outline": which (start, end, key) notes are on the edge of the area they fill: not wholly
    covered by the notes on the key above or below, or first / last on their key. So only the filled area's own
    edge counts: outlines inside it (overlaps filled in) are left out, and where overlaps cancel out, every side of
    every filled bit is the outline, whichever way it slants."""
    s, e = notes[:, 0:1], notes[:, 1:2]
    before = np.hstack([s - 1, s, notes[:, 2:3]])
    after = np.hstack([e, e + 1, notes[:, 2:3]])
    return ~(covered(notes, shifted(notes, -1)) & covered(notes, shifted(notes, 1)) &
             covered(before, notes) & covered(after, notes))


def edge_parts(notes):
    """Fill "Outline": the parts of the (start, end, key) long notes on the edge of the area they fill: the time
    the key above or below doesn't cover, and the first and last tick of each stretch (like a line's upright
    part), as (start, end, key) notes."""
    ks, ss, es = merged_by_key(notes)
    ends = np.column_stack([np.concatenate([ss, es - 1]), np.concatenate([ss + 1, es]), np.concatenate([ks, ks])])
    parts = np.concatenate([cut_out(notes, shifted(notes, -1)), cut_out(notes, shifted(notes, 1)), ends])
    ks, ss, es = merged_by_key(parts)
    return np.column_stack([ss, es, ks]).astype(np.int64).reshape(-1, 3)


def cut_out(spans, others):
    """(start, end, key) stretches with the time the others cover on the same key taken out."""
    ks, ss, es = merged_by_key(others)
    by_key = {}
    for k, s, e in zip(ks.tolist(), ss.tolist(), es.tolist()):
        by_key.setdefault(k, []).append((s, e))
    out = []
    for s, e, k in spans.tolist():
        for os_, oe in by_key.get(k, ()):
            if oe <= s or os_ >= e:
                continue
            if os_ > s:
                out.append((s, os_, k))
            s = max(s, oe)
            if s >= e:
                break
        if s < e:
            out.append((s, e, k))
    return np.asarray(out, np.int64).reshape(-1, 3)


# ---------------------------------------------------------------- pasted notes
# A custom shape can hold notes pasted from another program instead of a drawing: sh["notes"] = the notes packed
# (pack_notes), sh["strokes"] = just the box's outline. In the box a note runs from u = start / T to end / T at
# v = (row + 0.5) / K (T = the last note's end in ticks, K = keys from the lowest to the highest), so moving,
# stretching, flipping and turning the box moves the notes with it. sh["own_vel"]: the notes keep their own
# velocities (until the velocity is changed in Spiderweb). Each note also remembers its track (which copied track
# it came from): with Multi channel every track gets a channel of its own (engine.render, tracks_apart).

BOX_STROKE = {"kind": "poly", "pts": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]}
TRACKS = "t:"  # packed notes starting with this have the track column (the first test version didn't)
_unpacked = {}


def pack_notes(rows):
    """(start, end, row, velocity, track) rows -> text for the save file (zlib, base64)."""
    return TRACKS + base64.b64encode(zlib.compress(np.asarray(rows, "<i4").tobytes(), 1)).decode("ascii")


def unpack_notes(text):
    """pack_notes' text -> (start, end, row, velocity, track) int64 rows (remembered: it's asked for often)."""
    got = _unpacked.get(text)
    if got is None:
        if len(_unpacked) > 20:
            _unpacked.clear()
        tracks = text.startswith(TRACKS)
        got = np.frombuffer(zlib.decompress(base64.b64decode(text[len(TRACKS):] if tracks else text)), "<i4")
        got = got.reshape(-1, 5 if tracks else 4).astype(np.int64)
        if not tracks:
            got = np.column_stack([got, np.zeros(len(got), np.int64)])
        got.flags.writeable = False
        _unpacked[text] = got
    return got


def check_notes(text):
    """True if text is packed notes Spiderweb can use."""
    try:
        rows = unpack_notes(text)
    except (TypeError, ValueError, zlib.error):
        return False
    return (len(rows) > 0 and (rows[:, 0] >= 0).all() and (rows[:, 1] > rows[:, 0]).all() and (rows[:, 2] >= 0).all()
            and (rows[:, 3] >= 1).all() and (rows[:, 3] <= 127).all() and (rows[:, 4] >= 0).all())


def notes_shape(notes, ppq, name):
    """(tick, gate, key, velocity, track) rows -> the settings of a custom shape holding them (see above), its box
    starting at the first note's tick / ppq beats and the lowest key."""
    t0, k0 = int(notes[:, 0].min()), int(notes[:, 2].min())
    rows = np.column_stack([notes[:, 0] - t0, notes[:, 0] - t0 + notes[:, 1], notes[:, 2] - k0, notes[:, 3],
                            notes[:, 4]])
    b0, b1 = t0 / ppq, (t0 + int(rows[:, 1].max())) / ppq
    vel = max(1, min(127, round(float(notes[:, 3].mean()))))
    return dict(kind="custom", name=name, strokes=[dict(BOX_STROKE)], fill="empty", notes=pack_notes(rows),
                own_vel=True, vel0=vel, vel1=vel, pts=box_frame(b0, k0 - 0.5, b1, k0 + 0.5 + int(rows[:, 2].max())))


def block_notes(sh, ppq):
    """A pasted-notes shape's (start, end, pitch, velocity, track) notes where its box is now. Each note is a flat
    line in the box; a stretched / turned box makes them like any line (a note turned upright = 1-tick notes up the
    keys)."""
    rows = unpack_notes(sh["notes"])
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    t_all, k_all = float(rows[:, 1].max()), float(rows[:, 2].max() + 1)
    v = (rows[:, 2] + 0.5) / k_all
    ends = []
    for u in (rows[:, 0] / t_all, rows[:, 1] / t_all):
        ends.append(np.column_stack([(b0 + u * ub + v * vb) * ppq, p0 + u * up + v * vp]))
    a, b = ends
    swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))  # every note left to right
    a, b = np.where(swap[:, None], b, a), np.where(swap[:, None], a, b)
    n = len(rows)
    raw, per = parts_notes(np.stack([a, b], axis=1).reshape(-1, 2), np.arange(n) * 2, np.zeros(n, bool), counts=True)
    return np.column_stack([raw, np.repeat(rows[:, 3:5], per, axis=0)])
