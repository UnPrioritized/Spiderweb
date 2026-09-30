"""Custom shapes: drawings from the drawer placed on the roll, as outlines or filled with notes."""

import base64
import json
import math
import zlib

import numpy as np

from files.lang import tr
from notes.arc import arc_k, arc_points, ellipse_bezier
from notes.bezier import sample
from notes.pattern import (clean_pattern, clean_shape_formula, formed_path, formed_paths, has_formula,
                           moved_formulas)
from notes.polygon import side_paths
from notes.smooth import clean_level, smooth_path
from notes.paths import (TOP_KEY, dedupe, keep_longest, line_notes, loop_from_left, parts_notes, pitch_of,
                         stretch_ends)
from notes.hzbass import HZ_DEFAULTS, KeyGrid, clean_hz, hz_gate, hz_of, squares  # (Hz bass: hzbass.py)
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
                   "apart": False}
# on / off settings a custom shape only has when they're on: "union" = where outlines overlap it's filled too (off:
# overlaps cancel out, even-odd); "apart" = Fill / Spam "Outline": the outline's notes on a channel of their own
# (with Multi channel), the inside's on another
CUSTOM_FLAGS = ("union", "apart")


def custom_settings(cd):
    """The fill settings a new custom shape gets from cd (the settings for new ones)."""
    out = {k: cd[k] for k in ("fill", "gate", "align", "ends")}
    out.update({k: True for k in CUSTOM_FLAGS if cd.get(k)})
    if cd.get("hz"):
        out["hz"] = dict(cd["hz"])
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
                        out.append({"kind": "arc", "pts": pts, "k": arc_k(st)})
                elif pts:
                    out.append({"kind": "poly", "pts": pts})
                    if st.get("free"):  # drawn freehand: can be made perfect (smooth.py)
                        out[-1].update(free=True, smooth=clean_level(st.get("smooth", 0)), k=arc_k(st))
                    elif st.get("sides"):  # a polygon's (polygon.py), maybe with a shape / pattern on the sides
                        out[-1]["sides"] = True
                        pat, form = clean_pattern(st.get("pattern")), clean_shape_formula(st.get("shape"))
                        if pat:
                            out[-1]["pattern"] = pat
                        if form:
                            out[-1]["shape"] = form
            if isinstance(st.get("src"), int) and out:  # which shape it came from (convert.py)
                out[-1]["src"] = st["src"]
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
    pat, form = clean_pattern(st.get("pattern")), clean_shape_formula(st.get("shape"))
    if pat:
        out["pattern"] = pat
    if form:
        out["shape"] = form
    return out


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
        return arc_points(st["pts"], st.get("k", 1.0))
    if st.get("smooth"):  # a freehand stroke made perfect (its drawn points stay)
        return smooth_path(st["pts"], st["smooth"], st.get("k", 1.0))
    if st.get("sides") and has_formula(st):  # a polygon's sides, each with the shape / pattern (polygon.py)
        out = []
        for side in formed_paths(side_paths(st["pts"]), st):
            out += side[1:] if out else side
        return out
    return [tuple(p) for p in st["pts"]]


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
        for i, a in enumerate(lines):
            for j, b in enumerate(lines):
                if i == j:
                    continue
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
    is a gap in the outline."""
    spans = [s for s in map(stroke_span, strokes) if s]
    return [path for path in join_paths(spans) if not path_closed(path)]


def open_ends(strokes):
    """The loose ends of the drawing once touching strokes are joined."""
    return [end for path in open_paths(strokes) for end in (path[0], path[-1])]


def strokes_closed(strokes):
    """Every outline ends where it starts."""
    return bool(strokes) and not open_paths(strokes)


def fillable(strokes):
    """Fill and Spam work (any drawing: gaps are closed with straight lines, see fill_plan)."""
    return bool(strokes)


# Gaps in the outline, for Fill / Spam (in beats / keys, not on screen, so zooming never changes the notes):
TOUCH_BEATS, TOUCH_KEYS = 1 / 64, 1.0  # loose ends at most this far apart count as touching (joined straight)
FLAT_KEYS, FLAT_BEATS = 0.5, 1 / 64  # an open part never further than this from its closing line is left unfilled
_plans = {}
_spans = {}  # inside_spans, remembered


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
    "flat": open parts too flat to have an inside (they just keep their outline notes)."""
    key = (json.dumps(sh["strokes"]), json.dumps(sh["pts"]))
    got = _plans.get(key)
    if got is not None:
        return got
    if len(_plans) > 300:
        _plans.clear()
    paths = join_paths(custom_strokes(sh))
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
    flat = []
    for path in opens:
        if flat_path(path):
            flat.append(path)
        else:
            polys.append(path + [path[0]])
            closers.append([path[-1], path[0]])
    got = _plans[key] = {"polys": polys, "closers": closers, "flat": flat}
    return got


def gap_lines(sh):
    """The straight lines closing gaps in a custom shape's outline for Fill / Spam (drawn dashed)."""
    return [] if sh.get("text") else fill_plan(sh)["closers"]


def join_strokes(strokes):
    """Open lines that meet end to end become one line, so e.g. three lines drawn as a triangle make one
    closed triangle. Curves and circles stay as they are (they still count as joined, see strokes_closed)."""
    others = [st for st in strokes if st["kind"] != "poly" or stroke_closed(st)]
    lines = [[list(p) for p in st["pts"]] for st in strokes if st["kind"] == "poly" and not stroke_closed(st)]
    return others + [{"kind": "poly", "pts": pts} for pts in join_paths(lines)]


def custom_strokes(sh):
    if sh.get("text"):
        return text_polys(sh)
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    return [[(b0 + u * ub + v * vb, p0 + u * up + v * vp) for u, v in stroke_points(st)] for st in sh["strokes"]]


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
            out.append({"kind": "ellipse", "box": fix(u0, v0) + fix(u1, v1)})
        else:
            new = dict(st, pts=[fix(u, v) for u, v in st["pts"]])
            if (st["kind"] == "arc" or st.get("free")) and w > 1e-9 and h > 1e-9:
                new["k"] = st.get("k", 1.0) * h / w  # still round when stretched back to the drawn proportions
            if w > 1e-9 and h > 1e-9:
                moved_formulas(new, lambda u, v: (u / w, v / h))
            out.append(new)
    return out, (w / h if w > 1e-9 and h > 1e-9 else None)


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
        return {"kind": "ellipse", "box": [min(a, c), min(b, d), max(a, c), max(b, d)]}
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
    sh["strokes"] = [map_stroke(st, lambda u, v: ((u - ul) / w, (v - vl) / h), 1 / w, 1 / h) for st in sh["strokes"]]


def stroke_ends(strokes):
    """Points other strokes can join onto: every polyline point, the ends of curves and arcs."""
    out = []
    for st in strokes:
        if st["kind"] == "poly":
            out += st["pts"]
        elif st["kind"] == "curve" and has_formula(st):
            path = stroke_points(st)
            out += [list(path[0]), list(path[-1])]
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
        st = {"kind": "curve", "pts": ellipse_bezier(st["box"])}
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
        st = {"kind": "curve", "pts": ellipse_bezier(st["box"])}
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
    lines."""
    paths = custom_strokes(sh)
    return paths_outline(join_paths(paths if only is None else [paths[k] for k in only]), ppq)


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
    # order)
    first = np.searchsorted(levels, np.maximum(np.minimum(ya, yb), lo))
    n = np.searchsorted(levels, np.minimum(np.maximum(ya, yb), hi)) - first
    e = np.repeat(np.arange(len(here)), n)
    gap = np.repeat(first, n) + np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
    y0, y1 = levels[gap], levels[gap + 1]
    mid = (y0 + y1) / 2
    cross = (ya[e] <= mid) != (yb[e] <= mid)
    if not cross.all():
        e, gap, y0, y1, mid = e[cross], gap[cross], y0[cross], y1[cross], mid[cross]
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


def union_spans(polys, q, edges=None):
    """row_spans, but inside ANY of the loops counts (where they overlap it's filled, holes too). edges:
    poly_edges of each loop."""
    spans = [s for i, poly in enumerate(polys) for s in row_spans([poly], q, edges and edges[i])]
    return merge_spans(np.array([s[0] for s in spans]), np.array([s[1] for s in spans]))


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
    polys = custom_strokes(sh) if tx else fill_plan(sh)["polys"]
    ps = [p for poly in polys for _, p in poly]
    if not ps:
        return []
    union = sh.get("union") and not tx
    edges = None if tx else [poly_edges([poly]) for poly in polys] if union else poly_edges(polys)
    out = []
    for q in range(max(0, pitch_of(min(ps))), min(TOP_KEY, pitch_of(max(ps))) + 1):
        for a, b in (threshold_spans(polys, q, tx["threshold"]) if tx else
                     union_spans(polys, q, edges) if union else row_spans(polys, q, edges)):
            s = math.floor(a * ppq + 0.5)
            out.append((q, s, max(math.floor(b * ppq + 0.5), s + 1)))
    return out


def spam_gate(sh, ppq):
    """The spam gate in ticks: a whole number; for Hz bass the exact one as a float (chop_even), or with placed
    tones the array of its repeats (chop_grid), or a KeyGrid when they have effects (chop_keys)."""
    if sh.get("hz"):
        if sh["hz"].get("tones"):
            return squares(sh, ppq)
        gate = max(1.0, float(sh["gate"] * ppq))
        return float(math.floor(gate + 0.5)) if sh["hz"].get("fixed") else gate
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
    shape's strokes make their notes on their own, like the shapes did), or None if they're all one."""
    groups = {}
    for k, st in enumerate(sh["strokes"]):
        groups.setdefault(st.get("src", -1), []).append(k)
    return groups if len(groups) > 1 else None


def outline_groups(sh, ppq, spam=False):
    """The outline's notes (spam: chopped like Outline spam) and which stroke group each belongs to (None if the
    strokes are all one group, see stroke_groups)."""
    groups = stroke_groups(sh)
    if groups is None:
        notes = outline_notes(sh, ppq)
        return (chop_outline(sh, notes, ppq) if spam else notes), None
    parts, ids = [], []
    for n, (_, strokes) in enumerate(sorted(groups.items())):
        notes = outline_notes(sh, ppq, strokes)
        if spam:
            notes = chop_outline(sh, notes, ppq)
        parts.append(notes)
        ids.append(np.full(len(notes), n, np.int64))
    return np.concatenate(parts), np.concatenate(ids)


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
    flat = flat_notes(sh, ppq)
    if sh["fill"] == "fill":
        return len(inside_spans(sh, ppq)) + len(flat)
    g = spam_gate(sh, ppq)
    spans = np.asarray(inside_spans(sh, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]
    return chop_count(sh, spans, g) + chop_count(sh, flat, g)


def flat_notes(sh, ppq):
    """The outline notes of a filled shape's parts too flat to fill (fill_plan), so they don't vanish."""
    flat = [] if sh.get("text") else fill_plan(sh)["flat"]
    return paths_outline(flat, ppq) if flat else np.zeros((0, 3), np.int64)


def custom_notes(sh, ppq):
    """Empty = the outline; Fill = one note per stretch of each key inside; Spam = each stretch filled with
    back-to-back notes of the spam gate (chop: where they start and what happens to the bit that doesn't fit a
    whole gate). Outline spam = the outline chopped the same way (open ends are fine)."""
    return custom_notes_groups(sh, ppq)[0]


def custom_notes_groups(sh, ppq):
    """custom_notes, and which group each note belongs to (None = all one: see outline_groups)."""
    if "notes" in sh:
        return block_notes(sh, ppq)[:, :3], None
    if sh["fill"] == "outline_spam":
        return outline_groups(sh, ppq, spam=True)
    if sh["fill"] == "empty" or not fillable(sh["strokes"]):
        return outline_groups(sh, ppq)
    spans = np.asarray(inside_spans(sh, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]  # (start, end, key)
    flat = flat_notes(sh, ppq)  # (like Outline spam in Spam)
    apart = sh.get("apart")
    if sh["fill"] == "fill":
        notes = np.concatenate([spans, flat])
        if apart:  # the edge's notes, and the inside's long notes between them
            outline = edge_parts(notes)
            inside = cut_out(notes, outline)
            return (np.concatenate([outline, inside]),
                    np.concatenate([np.zeros(len(outline), np.int64), np.ones(len(inside), np.int64)]))
        return notes, None
    notes = np.concatenate([chop(sh, spans, spam_gate(sh, ppq)), chop_outline(sh, flat, ppq)])
    if apart:  # the same spam; the notes on the edge of what's filled are the outline's
        return notes, np.where(on_edge(notes), 0, 1).astype(np.int64)
    return notes, None


def tracks_apart(sh):
    """True if the shape's tracks must get channels of their own with Multi channel: pasted notes (each copied
    track keeps its own channel even when the tracks don't overlap), and Fill / Spam with "Outline" (the outline
    and the inside)."""
    if sh["kind"] != "custom":
        return False
    return "notes" in sh or bool(sh.get("apart") and sh.get("fill") in ("fill", "spam"))


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
