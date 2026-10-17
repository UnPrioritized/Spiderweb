"""Text shapes: typed text in an installed font, turned into a custom shape (custom.py) of the letters' outlines.

A text shape is a custom shape with sh["text"] = its settings (TEXT_DEFAULTS plus "text", "bbox", "cap", "k",
"holes", maybe "anchor": the click it was typed at, in the box's 0..1; the text's first note starts there and its
lowest note is on that key, onto_anchor). The letters are laid out in em units (fonts.py: 1.0 = font size, y up from the first line's baseline);
"bbox" = where their outlines are in em units, and sh["pts"] (the custom shape's box) is where that bbox is on
the roll. So em units -> beats / pitch is known from the shape itself (text_axes), and retyping or changing a
setting keeps the text where it is, however it was moved, resized, turned or skewed.

How the letters make notes: Fill / Spam use the nonzero rule (letter parts that overlap stay filled, holes in
O / A / B stay empty) and the threshold (how much of a key's height has to be inside for that key to play there).
"grow" makes the strokes thicker (keys, can be negative) as the text looked on screen when it was typed ("k" =
beats per key then).
"""

import copy
import math

from files.lang import tr
from notes.bezier import segments
from notes.fonts import DEFAULT_FONT, get_font

UNITS = ("font", "rows")  # "font" = size is the font size (em) in keys, "rows" = capital letters are that many keys
TEXT_ALIGNS = ("left", "center", "right")
DIRECTIONS = ("roll", "across", "down")  # (layout)
TEXT_DEFAULTS = {"font": DEFAULT_FONT,"size": 24.0, "unit": "font", "weight": 400, "italic": False, "tracking": 0.0,
                 "leading": 100.0, "align": "left", "threshold": 50.0, "grow": 0.0, "direction": "roll", "turn": 0.0,
                 "flip": False}
SUB_ROWS = 20  # each key is looked at on this many lines for the threshold (5% steps)


def clean_text(tx):
    """Text settings from a file (or None if they're broken)."""
    try:
        out = {"text": str(tx.get("text", "")), "font": str(tx.get("font") or TEXT_DEFAULTS["font"])}
        for key in ("size", "tracking", "leading", "threshold", "grow", "turn"):
            out[key] = float(tx.get(key, TEXT_DEFAULTS[key]))
        if not math.isfinite(out["turn"]):
            out["turn"] = 0.0
        out["unit"] = tx.get("unit") if tx.get("unit") in UNITS else "font"
        out["align"] = tx.get("align") if tx.get("align") in TEXT_ALIGNS else "left"
        out["direction"] = tx.get("direction") if tx.get("direction") in DIRECTIONS else "roll"
        out["flip"] = bool(tx.get("flip", False))
        out["weight"] = min(1000, max(1, int(tx.get("weight", 400))))
        out["italic"] = bool(tx.get("italic", False))
        out["threshold"] = min(100.0, max(0.0, out["threshold"]))
        out["bbox"] = [float(a) for a in tx["bbox"]]
        out["cap"] = float(tx.get("cap", 0.7)) or 0.7
        out["k"] = float(tx.get("k", 1.0)) or 1.0
        out["holes"] = sorted({int(i) for i in tx.get("holes", ())})
        try:  # (a broken anchor only loses the anchor)
            anchor = [float(a) for a in tx["anchor"]]
            if len(anchor) == 2 and all(math.isfinite(a) for a in anchor):
                out["anchor"] = anchor
        except (KeyError, TypeError, ValueError):
            pass
        if len(out["bbox"]) != 4:
            return None
        return out
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def text_font(tx):
    return get_font(tx["font"], tx["weight"], tx["italic"])


def missing_letters(tx):
    """The letters of the text that no installed font has (fonts.py: they show as boxes or "?"), as one string."""
    font = text_font(tx)
    return "".join(sorted({ch for ch in tx["text"] if font.missing(ch)}))


def with_arial(tx, changes):
    """Setting changes for a text, plus the default font (Arial on Windows) if its font isn't installed here: a
    shared text keeps its letters until it's edited, then all of it is redrawn in that font (the user is asked
    first, missing_font_ok)."""
    return changes if "font" in changes or text_font(tx).found else dict(changes, font=DEFAULT_FONT)


def em_keys(tx, size=None):
    """How many keys one em is at this size (a number in the size box)."""
    size = tx["size"] if size is None else size
    return size if tx["unit"] == "font" else size / (tx.get("cap") or text_font(tx).cap)


# ---------------------------------------------------------------- layout

def letter_matrix(tx):
    """How each letter is turned on its own spot: Flip letters (upside down) first, then Letter turn (degrees,
    anticlockwise as the piano roll shows it), as (a, b, c, d): em point (x, y) -> (a x + b y, c x + d y).
    Turned 90 and flipped = the letter's sideways and up swapped: upright in a falling-notes player."""
    t = math.radians(tx.get("turn", 0.0))
    cos, sin = (0.0 if abs(v) < 1e-12 else math.copysign(1.0, v) if abs(abs(v) - 1) < 1e-12 else v
                for v in (math.cos(t), math.sin(t)))
    f = -1.0 if tx.get("flip") else 1.0
    return cos, -sin * f, sin, cos * f


def layout(tx):
    """The text laid out in em units: (contours, places). contours = [(glyph number, Bezier points)]; places = one
    for every spot the caret can be (before each character; a line's end), in text order: {"o": where the letter's
    own (0, 0) went (em = o + letter_matrix x its point), "cell": its room as 4 em points (a line's end: none
    wide), "caret": the caret line there (2 em points)}.
    Direction: "roll" = letters along time, lines downwards (as typed in many programs); "across" = letters up the
    keys, each next line earlier (in a player: reads across, lines fall in turn); "down" = letters along time in
    steady steps, first letter first, each line a column next to the last (higher keys) at the same time; Align
    there = letters lined up on the column's low keys / middle / high keys."""
    font = text_font(tx)
    step = font.line_height * tx["leading"] / 100
    track = tx["tracking"] / 1000
    a, b, c, d = letter_matrix(tx)
    turn = lambda x, y: (a * x + b * y, c * x + d * y)
    asc, desc = font.ascent, font.descent
    mid = (asc - desc) / 2
    way = tx.get("direction", "roll")

    def room(adv):  # (the letter's room turned, round its middle: half its size along time / keys)
        pts = [turn(x - adv / 2, y - mid) for x, y in ((0, -desc), (adv, -desc), (adv, asc), (0, asc))]
        return max(abs(x) for x, _ in pts), max(abs(y) for _, y in pts)

    lines = tx["text"].split("\n")
    if way == "down":  # every letter the same time (the longest), the column as wide as the widest letter
        sizes = [room(font.glyph(ch)[0]) for line in lines for ch in line] or [room(0.5)]
        slot, band = 2 * max(w for w, _ in sizes), 2 * max(h for _, h in sizes)
        step = max(font.line_height, band) * tx["leading"] / 100  # (columns never overlap at 100%)
    contours, places = [], []
    g = 0
    for n, line in enumerate(lines):
        xs, x, prev = [], 0.0, None
        for ch in line:
            if prev is not None:
                x += font.kerning.get((prev, ch), 0.0) + track
            xs.append(x)
            x += font.glyph(ch)[0]
            prev = ch
        shift = {"left": 0.0, "center": -x / 2, "right": -x}[tx["align"]]
        for k in range(len(line) + 1):
            adv = font.glyph(line[k])[0] if k < len(line) else 0.0
            cx, cy = turn(adv / 2, mid)  # (where the letter's middle goes when it's turned)
            edge = None
            if way == "down":
                s0 = k * (slot + track)
                h = room(adv)[1]
                at = (s0 + slot / 2, n * step + {"left": h, "center": band / 2, "right": band - h}[tx["align"]])
                o = (at[0] - cx, at[1] - cy)
                edge = [(s0, n * step), (s0 + slot, n * step), (s0 + slot, n * step + band), (s0, n * step + band)]
            elif way == "across":
                pos = (xs[k] if k < len(line) else x) + shift
                o = (-n * step + mid - cx, pos + adv / 2 - cy)
            else:  # (unturned: exactly where the letter starts, no rounding on the way)
                o = ((xs[k] if k < len(line) else x) + shift + adv / 2 - cx, -n * step + mid - cy)
                if (a, b, c, d) == (1.0, 0.0, 0.0, 1.0):
                    o = ((xs[k] if k < len(line) else x) + shift, -n * step)
            put = lambda x, y: (o[0] + a * x + b * y, o[1] + c * x + d * y)
            cell = edge or [put(x, y) for x, y in ((0, -desc), (adv, -desc), (adv, asc), (0, asc))]
            caret = [edge[0], edge[3]] if edge else [put(0, -desc), put(0, asc)]
            places.append({"o": o, "cell": cell, "caret": caret})
            if k < len(line):
                for cont in font.glyph(line[k])[1]:
                    contours.append((g, [list(put(px, py)) for px, py in cont]))
                g += 1
    return contours, places


def flatten(pts, tol=0.004):
    """A Bezier curve as points: straight pieces stay one step, bends get enough steps to look smooth."""
    out = [tuple(pts[0])]
    for p0, p1, p2, p3 in segments(pts):
        bend = max(_line_dist(p1, p0, p3), _line_dist(p2, p0, p3))
        n = 1 if bend < 1e-9 else max(2, min(24, math.ceil(math.sqrt(bend / tol) * 2)))
        for i in range(1, n + 1):
            t = i / n
            a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
            out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _line_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = math.hypot(dx, dy)
    if ll < 1e-12:
        return math.dist(p, a)
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / ll


def area(poly):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:])) / 2


def winding(poly, x, y):
    w = 0
    for (xa, ya), (xb, yb) in zip(poly, poly[1:]):
        if (ya <= y) != (yb <= y) and x < xa + (xb - xa) * (y - ya) / (yb - ya):
            w += 1 if yb > ya else -1
    return w


def find_holes(contours):
    """Which contours are holes (inside an odd number of the same letter's other contours), e.g. the middle of O."""
    flat = [flatten(c) for _, c in contours]
    holes = []
    for i, (g, _) in enumerate(contours):
        x, y = flat[i][0]
        depth = sum(1 for j, (h, _) in enumerate(contours) if j != i and h == g and winding(flat[j], x, y))
        if depth % 2:
            holes.append(i)
    return holes


# ---------------------------------------------------------------- em units <-> the roll

def text_axes(sh):
    """(O, X, Y): where em point (x, y) is on the roll = O + x X + y Y (beats, pitch)."""
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    x0, y0, x1, y1 = sh["text"]["bbox"]
    w, h = x1 - x0, y1 - y0
    U, V = ((b1 - b0) / w, (p1 - p0) / w), ((b2 - b0) / h, (p2 - p0) / h)
    O = (b0 - x0 * U[0] - y0 * V[0], p0 - x0 * U[1] - y0 * V[1])
    return O, U, V


def new_axes(tx, b, p, k):
    """Axes for new text whose first line starts at (b, p): its baseline half a key below p, so capital letters
    sit on the key clicked and upwards."""
    e = em_keys(tx)
    return (b, p - 0.5), (e * k, 0.0), (0.0, e)


def axes_em(axes, k):
    """How many keys one em is along the axes (the height of the letters, as the text looked when typed)."""
    _, X, Y = axes
    return math.hypot(Y[0] / k, Y[1])


def scale_axes(axes, f):
    O, X, Y = axes
    return O, (X[0] * f, X[1] * f), (Y[0] * f, Y[1] * f)


def to_roll(axes, x, y):
    O, X, Y = axes
    return (O[0] + x * X[0] + y * Y[0], O[1] + x * X[1] + y * Y[1])


def from_roll(axes, b, p):
    """(beat, pitch) -> em units, or None if the axes are flat."""
    O, X, Y = axes
    det = X[0] * Y[1] - X[1] * Y[0]
    if abs(det) < 1e-15:
        return None
    db, dp = b - O[0], p - O[1]
    return ((db * Y[1] - dp * Y[0]) / det, (X[0] * dp - X[1] * db) / det)


def build(sh, tx, axes):
    """Lay out tx at axes into shape sh (its strokes, box and sh["text"]). False (sh unchanged) if there's
    nothing to see (no text, or only spaces)."""
    contours, _ = layout(tx)
    if not contours:
        return False
    pts = [p for _, c in contours for p in flatten(c)]
    x0, x1 = min(x for x, _ in pts), max(x for x, _ in pts)
    y0, y1 = min(y for _, y in pts), max(y for _, y in pts)
    if x1 - x0 < 1e-6:
        x0, x1 = x0 - 0.01, x1 + 0.01
    if y1 - y0 < 1e-6:
        y0, y1 = y0 - 0.01, y1 + 0.01
    w, h = x1 - x0, y1 - y0
    # (not rounded: text_polys rounds where the letters land, the same for a split letter as in the whole text)
    sh["strokes"] = [{"kind": "curve", "pts": [[(x - x0) / w, (y - y0) / h] for x, y in c]} for _, c in contours]
    sh["pts"] = [list(to_roll(axes, x0, y0)), list(to_roll(axes, x1, y0)), list(to_roll(axes, x0, y1))]
    font = text_font(tx)
    sh["text"] = dict(tx, bbox=[x0, y0, x1, y1], cap=font.cap, holes=find_holes(contours))
    sh["name"] = text_name(tx["text"])
    return True


def frame_uv(pts, b, p):
    """(beat, pitch) -> where it is in the box sh["pts"] (0..1 both ways inside it), or None if the box is flat."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    return from_roll(((b0, p0), (b1 - b0, p1 - p0), (b2 - b0, p2 - p0)), b, p)


def anchor_at(sh):
    """Where the text's anchor is on the roll (beat, pitch), or None (a text made before anchors: it stays put).
    The anchor rides in the box, so moving / resizing / turning the text takes it along."""
    uv = sh["text"].get("anchor")
    if not uv:
        return None
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    u, v = uv
    return (b0 + u * (b1 - b0) + v * (b2 - b0), p0 + u * (p1 - p0) + v * (p2 - p0))


def onto_anchor(sh, at, notes_of, ppq):
    """Slide the text so its first note starts at the anchor `at` (beat, pitch) and its lowest note is on the
    anchor's key (a "g" tail too: the other letters sit higher), and keep the anchor in it. Whole ticks and whole
    keys only, so the notes move exactly."""
    # first the box's left / bottom onto the anchor (notes before beat 0 or under the lowest key aren't made)
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    corners = sh["pts"] + [[b1 + b2 - b0, p1 + p2 - p0]]
    db, dp = at[0] - min(b for b, _ in corners), round(at[1]) - 0.5 - min(p for _, p in corners)
    sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]
    ns = notes_of(sh)
    if len(ns):
        db = (round(at[0] * ppq) - int(ns[:, 0].min())) / ppq
        dp = round(at[1]) - int(ns[:, 2].min())
        sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]
    uv = frame_uv(sh["pts"], *at)
    if uv:
        sh["text"]["anchor"] = [round(uv[0], 9), round(uv[1], 9)]
    else:
        sh["text"].pop("anchor", None)


def rebuild(sh, tx, axes, notes_of, ppq):
    """build, keeping the text on its anchor (if it has one)."""
    at = anchor_at(sh)
    if not build(sh, tx, axes):
        return False
    if at:
        onto_anchor(sh, at, notes_of, ppq)
    return True


def set_anchor(sh, notes_of, ppq):
    """The anchor put on the text's first note and lowest key as it is now (nothing moves)."""
    ns = notes_of(sh)
    uv = frame_uv(sh["pts"], int(ns[:, 0].min()) / ppq, int(ns[:, 2].min())) if len(ns) else None
    if uv:
        sh["text"]["anchor"] = [round(uv[0], 9), round(uv[1], 9)]


def split_letters(sh, notes_of, ppq):
    """Split into letters: each letter you can see as its own one-letter text (a copy of sh: fill, colour, gate
    ... kept) exactly where it was, so the notes stay the same; spaces go. [] if there's nothing to split."""
    tx, (O, X, Y) = sh["text"], text_axes(sh)
    places = layout(tx)[1]  # (place j = where character j went)
    out = []
    for j, ch in enumerate(tx["text"]):
        if ch == "\n":
            continue
        one = copy.deepcopy({k: v for k, v in sh.items() if k not in ("strokes", "pts", "text", "name")})
        letter = dict({k: v for k, v in tx.items() if k not in ("anchor", "bbox", "holes")}, text=ch)
        own = layout(letter)[1][0]["o"]  # (where it would go alone: moved onto where it was)
        at = (places[j]["o"][0] - own[0], places[j]["o"][1] - own[1])
        if build(one, letter, (to_roll((O, X, Y), *at), X, Y)):
            set_anchor(one, notes_of, ppq)
            out.append(one)
    return out


def to_custom(sh):
    """Turn into custom shape: the letters stay as they look (Grow baked into their lines), but it's a plain custom
    shape now, filled by its rules (the notes can move a little: no threshold, overlaps cancel out)."""
    tx = sh.pop("text")
    x0, y0, x1, y1 = tx["bbox"]
    if tx.get("grow"):
        polys = text_polys(dict(sh, text=tx))
        sh["strokes"] = [{"kind": "poly", "pts": [list(frame_uv(sh["pts"], b, p)) for b, p in poly]} for poly in polys]
    sh["round"] = (y1 - y0) / (x1 - x0)  # (the letters' own look: the outline gate shrinks them the same way)
    from notes.custom import refit
    refit(sh, song_beats=True)


def shown_size(tx, axes):
    """The number in the size box for text at these axes (it follows resizing the box on the roll)."""
    e = axes_em(axes, tx.get("k", 1.0))
    return e if tx["unit"] == "font" else e * (tx.get("cap") or text_font(tx).cap)


def restyle(tx, axes, changes):
    """Settings changed (font, size, unit, ...): the new settings and axes. The size box's number stays unless it
    was typed, so a new font or unit can make the letters bigger or smaller; the first line's start stays put."""
    size = changes.get("size", shown_size(tx, axes))
    new = dict(tx, **changes)
    new["cap"] = text_font(new).cap
    new["size"] = size
    before = axes_em(axes, tx.get("k", 1.0))
    return new, (scale_axes(axes, em_keys(new, size) / before) if before > 1e-12 else axes)


def text_name(text):
    one = " ".join(text.split())
    return tr("text.text", one=one[:24]) if len(one) > 25 else f"“{one}”"


# ---------------------------------------------------------------- outlines -> notes

def text_polys(sh):
    """The letters' outlines as closed polygons in beats / pitch, grown / shrunk by the grow setting."""
    tx = sh["text"]
    x0, y0, x1, y1 = tx["bbox"]
    w, h = x1 - x0, y1 - y0
    O, X, Y = text_axes(sh)
    polys = []
    for st in sh["strokes"]:  # (curves cut into steps in em units: a split letter gets the same steps as the whole)
        em = flatten([(x0 + u * w, y0 + v * h) for u, v in st["pts"]], 0.001)
        polys.append([(O[0] + x * X[0] + y * Y[0], O[1] + x * X[1] + y * Y[1]) for x, y in em])
    grow = tx.get("grow", 0.0)
    if grow:
        k = tx.get("k", 1.0)
        holes = set(tx.get("holes", ()))
        polys = [[(x * k, y) for x, y in offset([(b / k, p) for b, p in poly], -grow if i in holes else grow)]
                 for i, poly in enumerate(polys)]
    # float noise dropped: letter edges often sit exactly half way between two ticks (font sizes are round
    # numbers), so noise picked the tick, and a split letter (Split into letters) could land a tick off the whole
    return [[(round(b, 9), round(p, 9)) for b, p in poly] for poly in polys]


def offset(poly, d):
    """A closed polygon (first point = last) with its own inside grown by d (negative: shrunk), sharp corners
    cut off (bevel) so they don't shoot out."""
    pts = [p for i, p in enumerate(poly[:-1]) if math.dist(p, poly[i + 1]) > 1e-9]
    n = len(pts)
    if n < 3:
        return poly
    s = 1 if area(pts + [pts[0]]) > 0 else -1  # anticlockwise: its inside is on the left, so out = right
    normals = []
    for i in range(n):
        (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
        ll = math.hypot(xb - xa, yb - ya)
        normals.append((s * (yb - ya) / ll, -s * (xb - xa) / ll))
    out = []
    for i in range(n):
        (ax, ay), (bx, by) = normals[i - 1], normals[i]
        x, y = pts[i]
        dot = ax * bx + ay * by
        if dot > -0.5:  # up to ~120 degrees: one point where the two moved edges meet
            m = d / (1 + dot)
            out.append((x + (ax + bx) * m, y + (ay + by) * m))
        else:
            out += [(x + ax * d, y + ay * d), (x + bx * d, y + by * d)]
    return out + [out[0]]


def row_edges(polys, lo, hi):
    return [(a, b) for poly in polys for a, b in zip(poly, poly[1:])
            if a[1] != b[1] and min(a[1], b[1]) < hi and max(a[1], b[1]) > lo]


def line_spans(edges, y):
    """Where the line at height y is inside (nonzero rule): [(x0, x1)]."""
    cross = []
    for (xa, ya), (xb, yb) in edges:
        if (ya <= y) != (yb <= y):
            cross.append((xa + (xb - xa) * (y - ya) / (yb - ya), 1 if yb > ya else -1))
    cross.sort()
    out, w, start = [], 0, None
    for x, d in cross:
        before, w = w, w + d
        if before == 0 and w != 0:
            start = x
        elif before != 0 and w == 0:
            out.append((start, x))
    return out


def threshold_spans(polys, q, threshold):
    """Time ranges (beats) where at least `threshold` percent of key q's height is inside the letters."""
    lo = q - 0.5
    edges = row_edges(polys, lo, lo + 1)
    if not edges:
        return []
    need = max(1, math.ceil(threshold / 100 * SUB_ROWS - 1e-9))
    events = []
    for j in range(SUB_ROWS):
        for a, b in line_spans(edges, lo + (j + 0.5) / SUB_ROWS):
            events += [(a, 1), (b, -1)]
    events.sort()
    out, count, start = [], 0, None
    for x, d in events:
        before, count = count, count + d
        if before < need <= count:
            start = x
        elif count < need <= before:
            if out and start - out[-1][1] < 1e-12:
                out[-1][1] = x
            elif x > start:
                out.append([start, x])
    return out
