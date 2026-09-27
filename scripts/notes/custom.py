"""Custom shapes: drawings from the drawer placed on the roll, as outlines or filled with notes."""

import math

import numpy as np

from notes.arc import arc_k, arc_points, ellipse_bezier
from notes.bezier import sample
from notes.smooth import clean_level, smooth_path
from notes.paths import dedupe, keep_longest, line_notes, loop_from_left, pitch_of, stretch_ends
from notes.text import text_polys, threshold_spans

# Custom shapes: how the inside is filled, and the gate of "spam" in beats (1/64 = 60 ticks at PPQ 960).
# "outline_spam" = the outline's notes (like "empty") chopped into notes of the spam gate, the inside stays empty
FILLS = ("empty", "fill", "spam", "outline_spam")
SPAM_FILLS = ("spam", "outline_spam")  # the ones that use the gate and spam start
# Spam start: "auto" = each stretch of a key starts at its own left edge, "aligned" = every note sits on the
# gate grid counted from tick 0 (straight columns, lined up with bar lines and other shapes)
ALIGNS = ("auto", "aligned")
CUSTOM_DEFAULTS = {"fill": "empty", "gate": 0.0625, "align": "auto"}


# ---------------------------------------------------------------- custom shapes
# A custom shape is a drawing from the drawer: strokes in its own box, u = 0..1 left to right, v = 0..1 bottom to
# top. A stroke is {"kind": "poly", "pts": [[u, v], ...]}, {"kind": "curve", "pts": [anchor, handle, handle,
# anchor, ...]} (bezier.py; optional "sharp" / "sym" like the roll's Curve shape), {"kind": "arc", "pts": [start,
# through, end], "k": ...} (arc.py; k = how many u one v is, for it to be round) or
# {"kind": "ellipse", "box": [u0, v0, u1, v1]}.
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
        return sample([tuple(p) for p in st["pts"]], CURVE_STEPS)
    if st["kind"] == "arc":
        return arc_points(st["pts"], st.get("k", 1.0))
    if st.get("smooth"):  # a freehand stroke made perfect (its drawn points stay)
        return smooth_path(st["pts"], st["smooth"], st.get("k", 1.0))
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
    if st["kind"] == "curve":
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
    """Fill and Spam work: the outline is closed, or has just one gap (closed with a straight line, see
    gap_line). With more gaps it's unclear what's inside."""
    return bool(strokes) and len(open_paths(strokes)) <= 1


def gap_line(sh):
    """A custom shape with one gap in its outline: the straight line that closes it for Fill / Spam, as two
    (beat, pitch) points (from the open outline's end back to its start). None otherwise."""
    if sh.get("text"):
        return None
    paths = open_paths(sh["strokes"])
    if len(paths) != 1:
        return None
    to_bp = frame_to_bp(sh["pts"])
    return [to_bp(*paths[0][-1]), to_bp(*paths[0][0])]


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
        elif st["kind"] in ("curve", "arc"):
            out += [st["pts"][0], st["pts"][-1]]
    return out


def add_stroke(sh, st):
    """Put stroke st (drawn in beats / pitch: poly / curve points, or an ellipse box) into custom shape sh and fit its
    box around the drawing again. Its ends that land on another stroke's point are made exactly the same (so the
    outline counts as joined). Returns the new stroke's number."""
    to_uv = frame_to_uv(sh["pts"])
    if st["kind"] == "ellipse" and not frame_upright(sh["pts"]):  # turned: an ellipse only fits as a curve
        st = {"kind": "curve", "pts": ellipse_bezier(st["box"])}
    new = map_stroke(st, to_uv)
    if new.get("free"):  # its k: beats per key on screen -> how many u one v is on screen
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
    sh["strokes"].append(new)
    refit(sh)
    return len(sh["strokes"]) - 1


def new_live_shape(defaults, custom_defaults):
    """An empty custom shape to draw into (its box: 1 beat by 1 key at 0, fitted once something is drawn)."""
    return dict(defaults, kind="custom", name="Live drawing", strokes=[], fill=custom_defaults["fill"],
                gate=custom_defaults["gate"], align=custom_defaults["align"], pts=[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])


def outline_notes(sh, ppq):
    """(start, end, pitch) notes along every stroke of a custom shape, like lines."""
    raw = []
    for path in join_paths(custom_strokes(sh)):
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


def row_spans(polys, q):
    """Time ranges (in beats) where the closed polygons' inside (even-odd, so holes stay empty) touches pitch
    row q anywhere between its bottom and top edge."""
    lo, hi = q - 0.5, q + 0.5
    edges = [(a, b) for poly in polys for a, b in zip(poly, poly[1:])
             if a[1] != b[1] and min(a[1], b[1]) < hi and max(a[1], b[1]) > lo]
    if not edges:
        return []
    # Between two neighbouring levels no corner lies, so every edge is a straight piece and each pair of
    # crossings is a trapezoid: its time range is the widest of its two sides.
    levels = sorted({lo, hi} | {p[1] for a, b in edges for p in (a, b) if lo < p[1] < hi})

    def x_at(edge, y):
        (xa, ya), (xb, yb) = edge
        return xa + (xb - xa) * (y - ya) / (yb - ya)

    spans = []
    for ya, yb in zip(levels, levels[1:]):
        mid = (ya + yb) / 2
        cross = sorted((e for e in edges if (e[0][1] <= mid) != (e[1][1] <= mid)), key=lambda e: x_at(e, mid))
        for left, right in zip(cross[::2], cross[1::2]):
            spans.append((min(x_at(left, ya), x_at(left, yb)), max(x_at(right, ya), x_at(right, yb))))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def inside_spans(sh, ppq):
    """[(pitch, start tick, end tick)] for every stretch of every key inside the shape. Text: the nonzero rule and
    its threshold (text.py). One gap in the outline is closed with a straight line."""
    polys = custom_strokes(sh)
    gap = gap_line(sh)
    if gap:
        polys = polys + [gap]
    ps = [p for poly in polys for _, p in poly]
    tx = sh.get("text")
    out = []
    for q in range(max(0, pitch_of(min(ps))), min(127, pitch_of(max(ps))) + 1):
        for a, b in threshold_spans(polys, q, tx["threshold"]) if tx else row_spans(polys, q):
            s = math.floor(a * ppq + 0.5)
            out.append((q, s, max(math.floor(b * ppq + 0.5), s + 1)))
    return out


def spam_gate(sh, ppq):
    return max(1, math.floor(sh["gate"] * ppq + 0.5))


def spam_starts(sh, s, e, g):
    """First note start and how many whole gates fit in the stretch s..e (ticks)."""
    if sh.get("align") == "aligned":
        s = -(-s // g) * g  # the first gate line at or after s
    return s, max(0, (e - s) // g)


def chop(sh, stretches, g, keep_short=False):
    """stretches: NumPy array of (start, end, key) rows in ticks -> each filled with back-to-back notes of gate g
    (spam start like spam_starts), as an array of (start, end, key) rows in the same order. What doesn't fit a
    whole gate is dropped; keep_short: a stretch too short for even one gate stays as it is."""
    s0, e0, q = stretches[:, 0], stretches[:, 1], stretches[:, 2]
    s = -(-s0 // g) * g if sh.get("align") == "aligned" else s0
    n = np.maximum(0, (e0 - s) // g)
    short = (n == 0) & keep_short
    n = np.where(short, 1, n)
    k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)  # note number inside its stretch
    starts = np.repeat(s, n) + k * g
    out = np.column_stack([starts, starts + g, np.repeat(q, n)])
    if short.any():
        whole = np.repeat(short, n)
        out[whole, 0], out[whole, 1] = np.repeat(s0, n)[whole], np.repeat(e0, n)[whole]
    return out


def outline_spam(sh, ppq):
    """The outline's notes chopped into back-to-back notes of the spam gate (spam start like Spam). What doesn't
    fit a whole gate is dropped, but a note too short for even one gate stays as it is (steep parts of the
    outline would vanish otherwise)."""
    return chop(sh, np.asarray(outline_notes(sh, ppq), np.int64).reshape(-1, 3), spam_gate(sh, ppq), True)


def custom_note_count(sh, ppq):
    """How many notes a custom shape makes, without making them (spam can be millions)."""
    if sh["fill"] == "outline_spam":
        g = spam_gate(sh, ppq)
        return sum(max(1, spam_starts(sh, s, e, g)[1]) for s, e, _ in outline_notes(sh, ppq).tolist())
    if sh["fill"] == "empty" or not fillable(sh["strokes"]):
        return None
    if sh["fill"] == "fill":
        return len(inside_spans(sh, ppq))
    g = spam_gate(sh, ppq)
    return sum(spam_starts(sh, s, e, g)[1] for _, s, e in inside_spans(sh, ppq))


def custom_notes(sh, ppq):
    """Empty = the outline; Fill = one note per stretch of each key inside; Spam = each stretch filled with
    back-to-back notes of the spam gate, starting at its left edge or on the gate grid (see ALIGNS); what doesn't
    fit a whole gate is dropped. Outline spam = the outline chopped the same way (open ends are fine)."""
    if sh["fill"] == "outline_spam":
        return outline_spam(sh, ppq)
    if sh["fill"] == "empty" or not fillable(sh["strokes"]):
        return outline_notes(sh, ppq)
    spans = np.asarray(inside_spans(sh, ppq), np.int64).reshape(-1, 3)[:, [1, 2, 0]]  # (start, end, key)
    return spans if sh["fill"] == "fill" else chop(sh, spans, spam_gate(sh, ppq))
