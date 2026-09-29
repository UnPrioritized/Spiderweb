"""Turn into live shape: lines, polylines, freehand strokes, curves and arcs (and custom shapes) become one custom
shape, each as strokes of its own kind (a curve stays a curve, an arc an arc), so it can be filled.

The notes stay the same: every stroke remembers which shape it came from ("src"), and each shape's strokes make
their outline notes on their own (custom.outline_groups), so with Multi channel the old shapes still get channels
of their own. Tumours become plain points (the bumps as drawn), "Last note: starts on it" is dropped.

The old shapes are kept in the new one (sh["from"]), so Split into separate shapes can give them back as long as the
drawing wasn't changed (moving the whole shape is fine; resizing, turning, flipping or editing strokes isn't)."""

import json
import math

from files.lang import tr
from notes.bezier import anchor_count
from notes.custom import add_stroke, custom_strokes, new_live_shape, stroke_bp
from notes.joined import is_joined, join_velocity
from notes.tumour import LINE_KINDS

CAN_TURN = LINE_KINDS + ("custom",)


def has_tumours(sh):
    return any(tm and tm.get("on") for tm in [sh.get("tumour")] + list(sh.get("tumours") or []))


def line_strokes(sh, paths):
    """A line kind as strokes in beats / pitch (paths: its engine.cached_strokes, used for tumours: the bumps
    become points)."""
    kind = sh["kind"]
    if (sh.get("shape") or sh.get("pattern")) and not has_tumours(sh):  # its formulas as ordinary curves
        from notes.pattern import baked  # (pattern.py)
        sh = dict(sh, **baked(sh), kind="curve")
        kind = "curve"
        sh.pop("sym", None)
    if has_tumours(sh):
        return [{"kind": "poly", "pts": [list(p) for p in path]} for path in paths]
    if kind == "curve":
        pts, gaps = sh["pts"], sh.get("gaps", [])
        sharp = set(sh.get("sharp", []))
        out, a0 = [], 0
        for a1 in list(gaps) + [anchor_count(pts) - 1]:  # one curve per piece of a joined curve
            st = {"kind": "curve", "pts": [list(p) for p in pts[3 * a0:3 * a1 + 1]]}
            own = sorted(a - a0 for a in sharp if a0 < a < a1)
            if own:
                st["sharp"] = own
            if sh.get("sym") and not is_joined(sh):
                st["sym"] = sh["sym"]
            out.append(st)
            a0 = a1 + 1
        return out
    if kind == "arc":
        return [{"kind": "arc", "pts": [list(p) for p in sh["pts"]], "k": sh.get("k", 1.0)}]
    if kind == "free":
        return [{"kind": "poly", "pts": [list(p) for p in sh["pts"]], "free": True, "smooth": sh.get("smooth", 0),
                 "k": sh.get("k", 1.0)}]
    return [{"kind": "poly", "pts": [list(p) for p in sh["pts"]]}]


def losses(shapes):
    """What turning these into a live shape changes (for the warning), as sentences; empty = nothing."""
    out = []
    if any(sh["kind"] in LINE_KINDS and has_tumours(sh) for sh in shapes):
        out.append(tr("convert.tumours_become_fixed_points_they_can"))
    if any(sh["kind"] in LINE_KINDS and sh.get("end_dot") for sh in shapes):
        out.append(tr("convert.last_note_starts_on_it_is"))
    return out


def to_live(shapes, paths, defaults, custom_defaults):
    """shapes (with paths[i] = engine.cached_strokes(shapes[i])) as one new custom shape. The first custom shape
    among them gives its name and fill settings."""
    first = next((sh for sh in shapes if sh["kind"] == "custom"), None)
    new = new_live_shape(defaults, custom_defaults)
    if first:
        new.update(name=first.get("name") or new["name"], fill=first["fill"], gate=first["gate"],
                   align=first.get("align", "auto"), ends=first.get("ends", "drop"))
    src = 0
    for sh, path in zip(shapes, paths):
        if sh["kind"] == "custom":
            ids = {}  # (a shape turned into a live shape before keeps its groups apart)
            for k, st in enumerate(sh["strokes"]):
                got = stroke_bp(sh, k)
                key = st.get("src", -1)
                if key not in ids:
                    ids[key], src = src, src + 1
                got["src"] = ids[key]
                add_stroke(new, got)
            continue
        for st in line_strokes(sh, path):
            st["src"] = src
            add_stroke(new, st)
        src += 1
    spans = [(min(b for p in ps for b, _ in p), max(b for p in ps for b, _ in p)) for ps in paths]
    mine = [b for p in custom_strokes(new) for b, _ in p]
    join_velocity(new, shapes, spans, (min(mine), max(mine)))  # (each keeps its velocities)
    new["from"] = {"shapes": json.loads(json.dumps(shapes)), "strokes": json.loads(json.dumps(new["strokes"])),
                   "pts": [list(p) for p in new["pts"]]}
    return new


def _same(a, b, tol=1e-7):
    """Two JSON-like values equal (numbers within tol: save files trim digits)."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=tol, abs_tol=tol)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], tol) for k in a)
    return a == b


def originals(sh):
    """The shapes a live shape was made of, moved to where it is now, or None if they can't come back (none kept,
    or the drawing was changed: only moving the whole shape keeps them)."""
    fr = sh.get("from")
    if not fr or not _same(sh["strokes"], fr["strokes"]):
        return None
    (a0, b0), (a1, b1), (a2, b2) = fr["pts"]
    (c0, d0), (c1, d1), (c2, d2) = sh["pts"]
    db, dp = c0 - a0, d0 - b0
    if not _same([c1 - a1, d1 - b1, c2 - a2, d2 - b2], [db, dp, db, dp], 1e-9):
        return None
    out = json.loads(json.dumps(fr["shapes"]))
    for old in out:
        old["pts"] = [[b + db, p + dp] for b, p in old["pts"]]
    return out
