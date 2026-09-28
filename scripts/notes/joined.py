"""Joining lines, polylines, freehand strokes, curves and arcs into one Curve shape.

Each shape becomes a Bézier curve (bezier.py); they're chained end to end, each next one the nearest to the chain's
ends (turned round when that's nearer). Ends that touch (or nearly: `touch`) are joined into one continuous piece
with a corner there; shapes that don't touch stay separate pieces of the same curve, with a gap between them that
isn't drawn and makes no notes: sh["gaps"] = the segment numbers of those gaps (bezier.piece_ends).

Tumours: if every joined shape had the same tumours (or none), the curve gets them (each piece gets its own row of
bumps, like separate shapes). Otherwise every shape keeps its own: sh["tumours"] = one setting (or None) per joined
shape in chain order, sh["splits"] = the anchors where a new one starts inside a piece. Changing any tumour setting
of the joined curve gives it one setting for all (tumour_window.py drops "tumours" / "splits")."""

import json
import math

from notes.arc import arc_bezier, line_bezier
from notes.bezier import anchor_count, fit, sample
from notes.smooth import smooth_path
from notes.tumour import LINE_KINDS

FREE_TOLERANCE = 0.08  # how closely a freehand stroke's curve follows it, in keys (as the piano roll looks)


def to_bezier(sh, k):
    """The shape as (curve points, sharp anchors). k = beats per key on screen (for fitting freehand strokes)."""
    pts = [tuple(p) for p in sh["pts"]]
    kind = sh["kind"]
    if kind == "curve":
        return [list(p) for p in pts], list(sh.get("sharp", []))
    if kind == "arc":
        return arc_bezier(pts, sh.get("k", 1.0)), []
    if kind == "free":
        if sh.get("smooth"):
            pts = smooth_path(pts, sh["smooth"], sh.get("k", 1.0))
        if len(pts) < 2:
            pts = [pts[0], pts[0]]
        got = fit([(b / k, p) for b, p in pts], FREE_TOLERANCE)
        return [[b * k, p] for b, p in got], []
    # line / polyline: straight pieces, corners at every point
    out = line_bezier(pts[0], pts[1])
    for a, b in zip(pts[1:], pts[2:]):
        out += line_bezier(a, b)[1:]
    return out, list(range(1, len(pts) - 1))


def reversed_bezier(pts, sharp):
    last = anchor_count(pts) - 1
    return [list(p) for p in reversed(pts)], sorted(last - a for a in sharp)


def reversed_tumour(tm):
    """Tumour settings for the line run the other way round: same bumps (sides, range and graphs turned round)."""
    if not tm:
        return tm
    tm = json.loads(json.dumps(tm))
    tm["mirror"] = not tm.get("mirror", False)
    tm["start"], tm["end"] = 1 - tm.get("end", 1.0), 1 - tm.get("start", 0.0)
    for key, g in (tm.get("graphs") or {}).items():
        tm["graphs"][key] = [[1 - u, f] for u, f in reversed(g)]
    return tm


def join_shapes(shapes, k, touch):
    """One Curve shape from the shapes (in the order they're listed; the first one's velocity and last-note
    settings). touch(p, q): whether two ends count as touching. None if fewer than two can be joined."""
    shapes = [sh for sh in shapes if sh["kind"] in LINE_KINDS]
    if len(shapes) < 2:
        return None
    parts = []
    for sh in shapes:
        pts, sharp = to_bezier(sh, k)
        tm = sh.get("tumour")
        parts.append({"pts": pts, "sharp": sharp, "tm": json.loads(json.dumps(tm)) if tm else None})
    chain, rest = [parts[0]], parts[1:]
    while rest:
        # the nearest end to either end of the chain (turned round if that's nearer)
        head, tail = chain[0]["pts"][0], chain[-1]["pts"][-1]
        best = None
        for i, p in enumerate(rest):
            a, b = p["pts"][0], p["pts"][-1]
            for d, at_end, flip in ((math.dist(tail, a), True, False), (math.dist(tail, b), True, True),
                                    (math.dist(head, b), False, False), (math.dist(head, a), False, True)):
                if best is None or d < best[0] - 1e-12:
                    best = (d, i, at_end, flip)
        _, i, at_end, flip = best
        p = rest.pop(i)
        if flip:
            p["pts"], p["sharp"] = reversed_bezier(p["pts"], p["sharp"])
            p["tm"] = reversed_tumour(p["tm"])
        if at_end:
            chain.append(p)
        else:
            chain.insert(0, p)
    # put the chain together: touching ends become one point (the later piece moves onto it), others get a gap
    pts, sharp, gaps, splits = [list(p) for p in chain[0]["pts"]], list(chain[0]["sharp"]), [], []
    for p in chain[1:]:
        last = anchor_count(pts) - 1
        q, s = p["pts"], p["sharp"]
        if touch(pts[-1], q[0]):
            d = (pts[-1][0] - q[0][0], pts[-1][1] - q[0][1])
            q = [[x + d[0], y + d[1]] for x, y in q[:2]] + [list(pt) for pt in q[2:]]  # (its start + its handle)
            pts += q[1:]
            sharp.append(last)
            splits.append(last)
            sharp += [a + last for a in s]
        else:
            gaps.append(last)
            pts += line_bezier(pts[-1], q[0])[1:3] + [list(pt) for pt in q]
            sharp += [a + last + 1 for a in s]
    first = shapes[0]
    out = {key: first[key] for key in ("vel0", "vel1", "end_dot", "vel_env") if key in first}
    out.update(kind="curve", pts=pts)
    ends = {0, anchor_count(pts) - 1} | set(gaps) | {g + 1 for g in gaps}
    sharp = sorted(set(a for a in sharp if a not in ends))
    if sharp:
        out["sharp"] = sharp
    if gaps:
        out["gaps"] = gaps
    tms = [p["tm"] for p in chain]
    if all(json.dumps(t, sort_keys=True) == json.dumps(tms[0], sort_keys=True) for t in tms):
        if tms[0]:
            out["tumour"] = tms[0]
    else:
        out["tumours"] = tms
        if splits:
            out["splits"] = splits
    return out


def clean_joined(sh, out):
    """A joined curve's gaps / splits / tumours from a file into out (a cleaned curve); anything that doesn't fit
    the curve is dropped."""
    from notes.tumour import clean_tumour
    last = anchor_count(out["pts"]) - 1
    try:
        want = sorted({int(g) for g in sh.get("gaps", ())})
        splits = sorted({int(a) for a in sh.get("splits", ()) if 0 < int(a) < last})
    except (TypeError, ValueError):
        return
    gaps = []  # (every piece needs at least one segment of its own)
    for g in want:
        if 1 <= g <= last - 2 and (not gaps or g >= gaps[-1] + 2):
            gaps.append(g)
    splits = [a for a in splits if a not in gaps and a - 1 not in gaps]
    if gaps:
        out["gaps"] = gaps
    tms = sh.get("tumours")
    if isinstance(tms, list) and len(tms) == len(splits) + len(gaps) + 1:
        out["tumours"] = [clean_tumour(t) for t in tms]
        if splits:
            out["splits"] = splits
        out.pop("tumour", None)


def is_joined(sh):
    return sh["kind"] == "curve" and bool(sh.get("gaps") or sh.get("tumours"))


def joined_paths(sh, tumour_path):
    """The joined curve as one path per piece, with its tumours (tumour_path(path, tm) puts them on)."""
    pts = sh["pts"]
    gaps = sh.get("gaps", [])
    tms = sh.get("tumours")
    splits = sh.get("splits", []) if tms else []
    pieces, a0 = [], 0
    for g in gaps:
        pieces.append((a0, g))
        a0 = g + 1
    pieces.append((a0, anchor_count(pts) - 1))
    out, section = [], 0
    for p0, p1 in pieces:
        if tms is None:
            path = sample(pts[3 * p0:3 * p1 + 1], 240)
            tm = sh.get("tumour")
            out.append(tumour_path(path, tm) if tm and tm["on"] else path)
            continue
        bounds = [p0] + [a for a in splits if p0 < a < p1] + [p1]
        path = []
        for a, b in zip(bounds, bounds[1:]):
            part = sample(pts[3 * a:3 * b + 1], 240)
            tm = tms[section] if section < len(tms) else None
            section += 1
            if tm and tm["on"]:
                part = tumour_path(part, tm)
            path += part[1:] if path and part and tuple(part[0]) == tuple(path[-1]) else part
        out.append(path)
    return out


def all_tumours(sh):
    """Every tumour setting on the shape (a joined curve can have one per joined shape), to flip / turn them."""
    return [tm for tm in [sh.get("tumour")] + list(sh.get("tumours") or []) if tm]


def shown_tumour(sh):
    """The tumour settings the tumour window shows: the shape's own, or on a joined curve whose shapes kept their own
    ones, the first one there is."""
    if sh.get("tumours"):
        return next((t for t in sh["tumours"] if t), None)
    return sh.get("tumour")


def unify_tumours(sh):
    """A joined curve whose shapes kept their own tumours: from now on the shown one is the whole curve's."""
    if "tumours" in sh:
        tm = shown_tumour(sh)
        sh.pop("tumours")
        sh.pop("splits", None)
        if tm:
            sh["tumour"] = tm
        else:
            sh.pop("tumour", None)


# ---------------------------------------------------------------- splitting

def _cut(sh, a0, a1, tm):
    """Anchors a0..a1 of a curve as a curve of their own (a copy of sh's other settings, tumours tm)."""
    out = {key: json.loads(json.dumps(v)) for key, v in sh.items()
           if key not in ("pts", "sharp", "gaps", "splits", "tumours", "tumour", "sym")}
    out["pts"] = [list(p) for p in sh["pts"][3 * a0:3 * a1 + 1]]
    gaps = [g - a0 for g in sh.get("gaps", []) if a0 <= g < a1]
    sharp = [a - a0 for a in sh.get("sharp", []) if a0 < a < a1]
    if sharp:
        out["sharp"] = sharp
    if gaps:
        out["gaps"] = gaps
    if tm:
        out["tumour"] = json.loads(json.dumps(tm))
    return out


def sections(sh):
    """[(first anchor, last anchor, tumour settings)] of a curve: its pieces, and inside them the joined shapes that
    kept their own tumours."""
    gaps = sh.get("gaps", [])
    tms = sh.get("tumours")
    splits = sh.get("splits", []) if tms else []
    last = anchor_count(sh["pts"]) - 1
    bounds = sorted({0, last} | set(gaps) | {g + 1 for g in gaps} | set(splits))
    out, i = [], 0
    for a, b in zip(bounds, bounds[1:]):
        if a in gaps:  # (a gap, not a piece)
            continue
        out.append((a, b, (tms[i] if i < len(tms) else None) if tms else sh.get("tumour")))
        i += 1
    return out


def split_pieces(sh):
    """A joined curve back as one curve per piece (a piece whose shapes kept their own tumours: one per shape)."""
    return [_cut(sh, a, b, tm) for a, b, tm in sections(sh)]


def set_tumours(sh, tms, splits):
    """Give a curve one tumour setting per section (tms, in order; splits = where sections start inside a piece).
    When they're all the same and every section is a whole piece, that's one setting for the curve (each piece
    gets its own row of bumps either way, so it looks the same)."""
    for key in ("tumour", "tumours", "splits"):
        sh.pop(key, None)
    tms = [json.loads(json.dumps(tm)) if tm else None for tm in tms]
    if not splits and len(set(json.dumps(t, sort_keys=True) for t in tms)) == 1:
        if tms[0]:
            sh["tumour"] = tms[0]
        return
    sh["tumours"] = tms
    if splits:
        sh["splits"] = list(splits)


def split_at(sh, a):
    """A curve cut in two at anchor a (a joined curve's tumours go with their sections; the section that's cut is
    shared out so its bumps stay where they were, see tumour.split_tumour). None if a is an end of a piece."""
    from notes.bezier import piece_ends
    from notes.tumour import split_tumour
    if a in piece_ends(sh):
        return None
    pts = sh["pts"]
    left, right = _cut(sh, 0, a, None), _cut(sh, a, anchor_count(pts) - 1, None)
    tl, tr = [], []
    for s0, s1, tm in sections(sh):
        if s1 <= a:
            tl.append(tm)
        elif s0 >= a:
            tr.append(tm)
        else:
            l, r = split_tumour(tm, sample(pts[3 * s0:3 * a + 1], 240), sample(pts[3 * a:3 * s1 + 1], 240))
            tl.append(l)
            tr.append(r)
    splits = sh.get("splits", []) if sh.get("tumours") else []
    set_tumours(left, tl, [s for s in splits if s < a])
    set_tumours(right, tr, [s - a for s in splits if s > a])
    return left, right


def piece_velocity(new, old, new_span, old_span):
    """Give `new` (a part of `old`) the velocities it had as part of old: its own envelope over its own time span
    (spans: (earliest, latest) beat)."""
    from notes.envelope import env_values, velocity_env
    import numpy as np
    env = velocity_env(old)
    (n0, n1), (o0, o1) = new_span, old_span
    if o1 - o0 < 1e-12 or n1 - n0 < 1e-12:
        return
    ua, ub = (n0 - o0) / (o1 - o0), (n1 - o0) / (o1 - o0)
    inner = [[(u - ua) / (ub - ua), v] for u, v in env if ua < u < ub]
    va, vb = (float(x) for x in env_values(env, np.array([ua, ub])))
    new.pop("vel_env", None)
    new["vel0"], new["vel1"] = int(round(va)), int(round(vb))
    if inner:
        new["vel_env"] = [[0.0, va]] + inner + [[1.0, vb]]
    elif abs(va - round(va)) > 1e-9 or abs(vb - round(vb)) > 1e-9:
        new["vel_env"] = [[0.0, va], [1.0, vb]]


def custom_groups(sh):
    """A custom shape's strokes in groups that touch each other (end on end or on a point), as lists of stroke
    numbers; one group = nothing to split."""
    from notes.custom import stroke_points
    strokes = sh["strokes"]
    pts = [[tuple(round(c, 6) for c in p) for p in stroke_points(st)] for st in strokes]
    ends = [set([p[0], p[-1]] if st["kind"] != "poly" else p) if p else set() for st, p in zip(strokes, pts)]
    group = list(range(len(strokes)))

    def root(i):
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i
    for i in range(len(strokes)):
        for j in range(i + 1, len(strokes)):
            if ends[i] & set(pts[j]) or ends[j] & set(pts[i]):
                group[root(i)] = root(j)
    out = {}
    for i in range(len(strokes)):
        out.setdefault(root(i), []).append(i)
    return list(out.values())


def split_custom(sh):
    """A custom shape (e.g. drawn with Live shape) as one custom shape per group of touching strokes."""
    from notes.custom import refit
    out = []
    for g in custom_groups(sh):
        new = json.loads(json.dumps(sh))
        new["strokes"] = [new["strokes"][i] for i in g]
        refit(new)
        out.append(new)
    return out
