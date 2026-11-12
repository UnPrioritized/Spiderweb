"""Text shapes: typed text in an installed font, turned into a custom shape (custom.py) of the letters' outlines.

A text shape is a custom shape with sh["text"] = its settings (TEXT_DEFAULTS plus "text", "bbox", "cap", "k",
"holes", maybe "anchor": the click it was typed at, in the box's 0..1; the part of the text Align picks sits
there, e.g. Left = its first note starts there and its lowest note is on that key, onto_anchor). The letters are laid out in em units (fonts.py: 1.0 = font size, y up from the first line's baseline);
"bbox" = where their outlines are in em units, and sh["pts"] (the custom shape's box) is where that bbox is on
the roll. So em units -> beats / pitch is known from the shape itself (text_axes), and retyping or changing a
setting keeps the text where it is, however it was moved, resized, turned or skewed.

How the letters make notes: Fill / Spam use the nonzero rule (letter parts that overlap stay filled, holes in
O / A / B stay empty) and the threshold (how much of a key's height has to be inside for that key to play there).
"grow" makes the strokes thicker (keys, can be negative) as the text looked on screen when it was typed ("k" =
beats per key then).
"""

import copy
import hashlib
import itertools
import math
import os
import weakref
from collections import deque

import numpy as np

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
                way = [float(a) for a in tx["anchor_way"]]
                if len(way) == 4 and all(math.isfinite(a) for a in way):
                    out["anchor_way"] = way
        except (KeyError, TypeError, ValueError):
            pass
        if tx.get("joined") is True:  # (laid out with joined letters: an old text without it is joined on open)
            out["joined"] = True
        try:  # (line starts on whole ticks / keys, points on ticks: with_grid)
            grid = float(tx["grid"])
            if math.isfinite(grid) and grid > 0:
                out["grid"] = grid
        except (KeyError, TypeError, ValueError):
            pass
        try:
            ppq = int(tx["ppq"])
            if 1 <= ppq <= 65535:
                out["ppq"] = ppq
        except (KeyError, TypeError, ValueError):
            pass
        form = _clean_form(tx.get("form"))
        if form:  # (a letter group split out of a joined line: the glyphs it keeps)
            out["form"] = form
        if len(out["bbox"]) != 4:
            return None
        return out
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _clean_form(form):
    """A split letter group's kept glyphs (shaping.keep_form) from a file, or None."""
    try:
        out = {"of": str(form["of"]), "font": [str(form["font"][0]), int(form["font"][1]), bool(form["font"][2])],
               "glyphs": [[str(g[0]), int(g[1])] + [float(v) for v in g[2:6]] for g in form["glyphs"]],
               "cells": [[float(a), float(b), bool(r)] for a, b, r in form["cells"]], "width": float(form["width"]),
               "rtl": bool(form["rtl"]), "pre": str(form.get("pre", "")), "post": str(form.get("post", ""))}
        nums = [v for g in out["glyphs"] for v in g[2:]] + [v for c in out["cells"] for v in c[:2]] + [out["width"]]
        if len(out["cells"]) != len(out["of"]) or not out["glyphs"] or not all(math.isfinite(v) for v in nums):
            return None
        return out
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
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


def layout(tx, info=None):
    """The text laid out in em units: (contours, places). contours = [(glyph number, Bezier points)]; places = one
    for every spot the caret can be (before each character; a line's end), in text order: {"o": where the letter's
    own (0, 0) went (em = o + letter_matrix x its point), "cell": its room as 4 em points (a line's end: none
    wide), "caret": the caret line there (2 em points)}.
    info (a dict, optional) gets "joined" (a line was laid out with joined letters) and "glyphs": per glyph number
    (line, its letter group's first character in the line or None for a letter-by-letter line, its piece: the
    letters turned as one).
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
    form = kept_form(tx)
    grid = tx.get("grid")
    if info is not None:
        info["glyphs"] = []
    for n, line in enumerate(lines):
        if way != "down" and (form or joined(line)):  # (joined letters / right to left: shaping.py; "down" stacks
            if info is not None:  # the letters one by one, as typed)
                info["joined"] = True
            g = _joined_line(font, line, n, step, track, mid, desc, asc, (a, b, c, d), way, tx["align"],
                             contours, places, g, form, info, grid)
            continue
        xs, x, prev = [], 0.0, None
        for ch in line:
            if prev is not None:
                x += font.kerning.get((prev, ch), 0.0) + track
            xs.append(x)
            x += font.glyph(ch)[0]
            prev = ch
        shift = line_shift(tx["align"], x, False, grid) if way != "down" else 0.0
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
                if info is not None:
                    info["glyphs"].append((n, None, ("g", g)))
                g += 1
    return contours, places


def kept_form(tx):
    """A split letter group's kept glyphs (tx["form"], shaping.keep_form) while they still fit its text and the
    packages load, else None (retyped: its own letters again)."""
    form = tx.get("form")
    if not form or form.get("of") != tx["text"] or "\n" in tx["text"]:
        return None
    from notes import shaping
    return form if shaping.available() else None


def joined(line):
    """This line is laid out with joined letters / right to left (shaping.py): it has such letters and both
    packages load. Every other line keeps the letter-by-letter layout exactly."""
    from notes import shaping
    return shaping.needs_shaping(line) and shaping.available()


def no_joining(text):
    """The text has letters that join / read right to left but the packages aren't here (drawn one by one)."""
    from notes import shaping
    return any(shaping.needs_shaping(line) for line in text.split("\n")) and not shaping.available()


# ---------------------------------------------------------------- the caret in joined lines (as Windows' own boxes)

def caret_line(text, c):
    """(start, end) of the line the typed position c is in."""
    end = text.find("\n", c)
    return text.rfind("\n", 0, c) + 1, len(text) if end < 0 else end


def line_stops(text, c, down=False):
    """(stops, rtl) of c's line: a joined line gives where its caret may stop (typed positions, shaping.caret_stops)
    and whether it reads right to left; any other line None, False (every position, as before). down = the text's
    Direction is Down: never joined (letters one by one in typed order), so the arrows act as for Latin."""
    s, e = caret_line(text, c)
    line = text[s:e]
    if down or not joined(line):
        return None, False
    from notes import shaping
    return [s + k for k in shaping.caret_stops(line)], shaping.line_rtl(line)


def caret_step(text, c, forward, down=False):
    """The typed position one step on (forward) or back from c. Joined lines step by their caret stops; past a
    line's end / start the caret goes to the next / last line, one character, as before."""
    stops, _ = line_stops(text, c, down)
    if stops:
        nxt = [k for k in stops if k > c] if forward else [k for k in stops if k < c]
        if nxt:
            return nxt[0] if forward else nxt[-1]
    return max(0, min(len(text), c + (1 if forward else -1)))


def caret_snap(text, c):
    """c moved onto the nearest caret stop of its line (a click inside a joined letter group)."""
    stops, _ = line_stops(text, c)
    return min(stops, key=lambda k: (abs(k - c), k)) if stops else c


def line_shift(align, width, rtl, grid):
    """Where a line of this width starts (its left end, em) for Align. grid (tx["grid"], with_grid): one tick (or
    key, Across) in em along the line: the line's START (left end; right end for a right-to-left line, where its
    first letters are) lands on a whole one, so typing on moves the letters already typed by whole ticks only and
    their notes stay the same (user 2026-11-12: centred / right-aligned / right-to-left lines wobbled)."""
    shift = {"left": 0.0, "center": -width / 2, "right": -width}[align]
    if not grid:
        return shift
    start = shift + width if rtl else shift
    start = round(start / grid) * grid
    return start - width if rtl else start


def with_grid(tx, axes, ppq):
    """tx with "grid" (line_shift) for these axes: a tick in em when the lines run along time unturned by the box,
    a key in em when they run up the keys (Across); none otherwise (a turned / slanted box, Down). And "ppq": the
    ticks text_polys lands the letters' points on (tick_round)."""
    _, X, Y = axes
    way = tx.get("direction", "roll")
    grid = None
    if way == "roll" and abs(X[1]) < 1e-12 and abs(X[0]) > 1e-12:
        grid = 1.0 / abs(X[0] * ppq)
    elif way == "across" and abs(Y[0]) < 1e-12 and abs(Y[1]) > 1e-12:
        grid = 1.0 / abs(Y[1])
    out = {k: v for k, v in tx.items() if k != "grid"}
    if grid:
        out["grid"] = grid
    out["ppq"] = int(ppq)
    return out


TICK_STEPS = 1024  # (tick_round: points land on 1/1024 ticks ...)
TICK_LIFT = 1 / 8192  # (... and a little past, so none sits exactly half way between two ticks)


def tick_round(b, ppq):
    """A letter point's beat on 1/1024 ticks, lifted 1/8192 tick: a point half way between two ticks always goes
    to the later one, wherever the text is (rounding beats to 9 places picked by the beat's decimals, so a text
    moved by whole ticks could change its notes: user 2026-11-12, letters typed before changed as typing went on)."""
    return (math.floor(b * ppq * TICK_STEPS + 0.5) / TICK_STEPS + TICK_LIFT) / ppq


def _joined_line(font, line, n, step, track, mid, desc, asc, m, way, align, contours, places, g, form=None,
                 info=None, grid=None):
    """layout's line n with joined letters: its letter shapes into contours, one place per character (+ the line's
    end) into places, as layout's own lines ("roll" / "across"; a place also says "rtl": the character reads right
    to left, its caret on its right side). Letter turn / Flip turn each JOINED PIECE on its own spot (user
    2026-11-12: a joined word, a letter group), so the joins stay. form: a split letter group's kept glyphs
    (kept_form). Returns the next glyph number."""
    from notes import shaping
    a, b, c, d = m
    turn = lambda x, y: (a * x + b * y, c * x + d * y)
    sl = None
    if form:
        sl = shaping.kept_line(font, form) or shaping.shape_line(font, line, track, (form["pre"], form["post"]))
    if sl is None:
        sl = shaping.shape_line(font, line, track)
    shift = line_shift(align, sl["width"], sl["rtl"], grid)
    plain = way == "roll" and m == (1.0, 0.0, 0.0, 1.0)  # (unturned: every letter exactly where it starts)
    span = {}  # (each joined piece's left and right)
    for (gx, adv, _, _), p in zip(sl["glyphs"], sl["pieces"]):
        lo, hi = span.get(p, (gx, gx + adv))
        span[p] = (min(lo, gx), max(hi, gx + adv))

    def origin(pos, adv):  # (where a letter's own (0, 0) goes, as layout does it)
        cx, cy = turn(adv / 2, mid)
        if way == "across":
            return (-n * step + mid - cx, pos + adv / 2 - cy)
        if m == (1.0, 0.0, 0.0, 1.0):
            return (pos, -n * step)
        return (pos + adv / 2 - cx, -n * step + mid - cy)

    def placed(pos, adv, p):  # (origin, but inside its joined piece turned as one)
        if plain or p is None:
            return origin(pos + shift, adv)
        lo, hi = span[p]
        o = origin(lo + shift, hi - lo)
        return (o[0] + a * (pos - lo), o[1] + c * (pos - lo))

    def putter(o):
        return lambda x, y: (o[0] + a * x + b * y, o[1] + c * x + d * y)

    char_piece = shaping.piece_of(sl, len(line)) if sl["glyphs"] else [None] * len(line)
    for (x0, x1, rtl), p in zip(sl["cells"], char_piece):
        adv = x1 - x0
        o = placed(x0, adv, p)
        put = putter(o)
        side = adv if rtl else 0.0
        places.append({"o": o, "cell": [put(x, y) for x, y in ((0, -desc), (adv, -desc), (adv, asc), (0, asc))],
                       "caret": [put(side, -desc), put(side, asc)], "rtl": rtl})
    # (the line's end: on the last typed letter's far side, as Windows' boxes put it: "سلام abc" ends after the c,
    # in the middle; one-direction lines at their left / right end)
    if sl["cells"]:
        x0, x1, rtl = sl["cells"][-1]
        end = x0 if rtl else x1
    else:
        end = 0.0 if sl["rtl"] else sl["width"]
    o = origin(end + shift, 0.0)
    put = putter(o)
    places.append({"o": o, "cell": [put(0, -desc), put(0, -desc), put(0, asc), put(0, asc)],
                   "caret": [put(0, -desc), put(0, asc)], "rtl": sl["rtl"]})
    for (gx, adv, conts, cl), p in zip(sl["glyphs"], sl["pieces"]):
        put = putter(placed(gx, adv, p))
        for cont in conts:
            contours.append((g, [list(put(px, py)) for px, py in cont]))
        if info is not None:
            info["glyphs"].append((n, cl, ("p", n, p)))
        g += 1
    return g


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


def find_holes(contours, flat=None):
    """Which contours are holes (inside an odd number of the same letter's other contours), e.g. the middle of O.
    flat: each contour cut into steps (flatten) already."""
    flat = [flatten(c) for _, c in contours] if flat is None else flat
    same = {}  # (each letter's contours: only those are looked at, not every contour of the text)
    for i, (g, _) in enumerate(contours):
        same.setdefault(g, []).append(i)
    holes = []
    for i, (g, _) in enumerate(contours):
        x, y = flat[i][0]
        depth = sum(1 for j in same[g] if j != i and winding(flat[j], x, y))
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


class Strokes(list):
    """A text's strokes as build made them, with .fp = a fingerprint of their points (a hash of the very numbers):
    engine.shape_key / _keyed use it instead of writing out every point (~38,000 points = ~28 ms, ~5 times a key
    typed). Only while it's still true (strokes_token): the same stroke dicts holding the same pts lists of the same
    length, nothing added / removed / set on the list itself (those drop the fingerprint). RULE: never change a
    text's points in place (a value inside a pts list): replace the strokes. A deepcopy (undo, Ctrl+D) keeps the
    fingerprint; slices, `+`, copy.copy, pickle, a file give a plain list (written out in full, as before)."""

    def __init__(self, items=(), fp=None):
        super().__init__(items)
        self.fp = fp
        self.guard = [(st, st.get("pts"), len(st.get("pts") or ())) for st in self]

    def __deepcopy__(self, memo):
        new = Strokes([copy.deepcopy(st, memo) for st in self], self.fp if self.valid() else None)
        memo[id(self)] = new
        return new

    def __reduce__(self):  # (pickle / copy.copy: a plain list, no fingerprint)
        return list, (list(self),)

    def valid(self):
        if self.fp is None or len(self.guard) != len(self):
            return False
        for st, (s0, p0, n0) in zip(self, self.guard):
            if st is not s0 or type(st) is not dict or len(st) != 2 or st.get("kind") != "curve":
                return False
            p = st.get("pts")
            if p is not p0 or len(p) != n0:
                return False
        return True


def _changed(name):
    def method(self, *a, **k):
        self.fp = None  # (changed as a list: the fingerprint no longer says what's in it)
        return getattr(list, name)(self, *a, **k)
    return method


for _name in ("append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse", "__setitem__",
              "__delitem__", "__iadd__", "__imul__"):
    setattr(Strokes, _name, _changed(_name))

_live = weakref.WeakValueDictionary()  # fingerprint -> the Strokes it stands for (engine.from_key puts them back)
_kept = deque(maxlen=4)  # (and the last few held for sure: from_key right after shape_key always finds them)


def fingerprint(P, off):
    """A fingerprint of curves' points: P (N, 2) all of them, off where each curve starts (textfast.points_of)."""
    return hashlib.blake2b(np.ascontiguousarray(P, float).tobytes() + np.asarray(off, np.int64).tobytes(),
                           digest_size=16).hexdigest()


def tagged(strokes):
    """A text's strokes (curves) as Strokes with their fingerprint (a project opened: keys cheap from the start).
    Strokes that aren't all plain curves stay as they are."""
    if type(strokes) is Strokes or not strokes or any(type(st) is not dict or len(st) != 2 or
                                                      st.get("kind") != "curve" for st in strokes):
        return strokes
    from notes import textfast
    try:
        return Strokes(strokes, fingerprint(*textfast.points_of([st["pts"] for st in strokes])))
    except (TypeError, ValueError):
        return strokes


def tag_texts(shapes):
    """Every text's strokes tagged (tagged), a project just opened."""
    for sh in shapes:
        if isinstance(sh.get("text"), dict) and isinstance(sh.get("strokes"), list):
            sh["strokes"] = tagged(sh["strokes"])


def strokes_token(strokes):
    """The fingerprint of strokes still as build made them (Strokes.valid), else None."""
    if type(strokes) is Strokes and strokes.valid():
        if _live.get(strokes.fp) is not strokes:
            _live[strokes.fp] = strokes
            _kept.append(strokes)
        return strokes.fp
    return None


def strokes_of(fp):
    """The Strokes a fingerprint stands for (strokes_token gave it out), or None if they're gone."""
    return _live.get(fp)


def build(sh, tx, axes, ppq=None):
    """Lay out tx at axes into shape sh (its strokes, box and sh["text"]). False (sh unchanged) if there's
    nothing to see (no text, or only spaces). ppq: the lines start on whole ticks (with_grid); none = tx's own."""
    from notes import textfast
    if ppq:
        tx = with_grid(tx, axes, ppq)
    info = {}
    contours, _ = layout(tx, info)
    if not contours:
        return False
    P, off = textfast.points_of([c for _, c in contours])
    if BATCH:
        flat, counts = textfast.flatten_many(P, off, 0.004)
        x0, x1 = float(flat[:, 0].min()), float(flat[:, 0].max())
        y0, y1 = float(flat[:, 1].min()), float(flat[:, 1].max())
        flat = textfast.split_points(flat, counts)
    else:
        flat = [flatten(c) for _, c in contours]
        pts = [p for f in flat for p in f]
        x0, x1 = min(x for x, _ in pts), max(x for x, _ in pts)
        y0, y1 = min(y for _, y in pts), max(y for _, y in pts)
    if x1 - x0 < 1e-6:
        x0, x1 = x0 - 0.01, x1 + 0.01
    if y1 - y0 < 1e-6:
        y0, y1 = y0 - 0.01, y1 + 0.01
    w, h = x1 - x0, y1 - y0
    # (not rounded: text_polys rounds where the letters land, the same for a split letter as in the whole text)
    U = np.empty_like(P)
    U[:, 0] = (P[:, 0] - x0) / w
    U[:, 1] = (P[:, 1] - y0) / h
    sh["strokes"] = Strokes(({"kind": "curve", "pts": pts} for pts in textfast.split_points(U, np.diff(off))),
                            fingerprint(U, off))
    sh["pts"] = [list(to_roll(axes, x0, y0)), list(to_roll(axes, x1, y0)), list(to_roll(axes, x0, y1))]
    font = text_font(tx)
    sh["text"] = dict(tx, bbox=[x0, y0, x1, y1], cap=font.cap, holes=find_holes(contours, flat))
    sh["text"].pop("joined", None)
    if info.get("joined"):  # (an old text, saved before joined letters, has none: rejoin_old)
        sh["text"]["joined"] = True
    sh["name"] = text_name(tx["text"])
    return True


def needs_rejoin(tx):
    """A text saved before joined letters (1.7.2) that has lines needing them, the packages load here and its font
    is installed here (user 2026-11-12: one whose font is missing stays as saved, like any text in a missing font;
    it's joined when edited, after the usual missing-font question)."""
    return (tx.get("direction", "roll") != "down" and not tx.get("joined") and not tx.get("form")
            and any(joined(line) for line in tx["text"].split("\n")) and text_font(tx).found)


def rejoin_old(shapes, notes_of, ppq):
    """Opening a project (user 2026-11-12): texts saved with Arabic / Hindi / ... letters drawn one by one are laid
    out again with joined letters, kept on their anchor as when retyped. Other texts aren't touched. Sliced pieces
    keep their notes (they come from the whole text). How many changed."""
    done = 0
    for sh in shapes:
        tx = sh.get("text")
        if not isinstance(tx, dict) or sh.get("cut") or not needs_rejoin(tx):
            continue
        was = copy.deepcopy(sh)
        try:
            if rebuild(sh, dict(tx), text_axes(sh), notes_of, ppq):
                done += 1
        except Exception:  # noqa: BLE001 (a text that can't be laid out again stays as it was saved)
            sh.clear()
            sh.update(was)
    return done


def frame_uv(pts, b, p):
    """(beat, pitch) -> where it is in the box sh["pts"] (0..1 both ways inside it), or None if the box is flat."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    return from_roll(((b0, p0), (b1 - b0, p1 - p0), (b2 - b0, p2 - p0)), b, p)


def anchor_at(sh):
    """Where the text's anchor is on the roll (beat, pitch), or None (a text made before anchors: it stays put).
    The anchor rides in the box, so moving / resizing the text takes it along."""
    uv = sh["text"].get("anchor")
    if not uv:
        return None
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    u, v = uv
    return (b0 + u * (b1 - b0) + v * (b2 - b0), p0 + u * (p1 - p0) + v * (p2 - p0))


def frame_way(pts):
    """Which way the box's sides point (unit steps): the same after moving / stretching, not after a turn, flip or
    skew."""
    (b0, p0), (b1, p1), (b2, p2) = pts
    out = []
    for db, dp in ((b1 - b0, p1 - p0), (b2 - b0, p2 - p0)):
        n = math.hypot(db, dp) or 1.0
        out += [round(db / n, 6), round(dp / n, 6)]
    return out


def letter_notes(sh, notes_of):
    """The notes of the letters themselves: no Hz bass tones, note tool pages, gate Range or Sustain (the anchor
    goes by the letters: a Hz tone starting later never moves the text)."""
    return notes_of({k: v for k, v in sh.items() if k not in ("hz", "fx", "range", "sustain", "subtract", "extend", "gate_flip")})


def whole_letter_notes(sh, notes_of, ppq):
    """letter_notes as if nothing were cut at beat 0 / key 0 (notes there aren't made): a box near those edges is
    measured moved later / higher by whole ticks / keys (the notes move exactly), then moved back."""
    bs, ps = zip(*box_corners(sh["pts"]))
    ns = letter_notes(sh, notes_of)
    if min(bs) >= 0 and min(ps) >= -0.5 and (not len(ns) or (ns[:, 0].min() > 0 and ns[:, 2].min() > 0)):
        return ns  # (clear of the edges: nothing cut, measured where it is)
    pad_b, pad_p = max(1.0, max(bs) - min(bs)), max(2.0, max(ps) - min(ps))  # (turned letters reach past the box)
    st = math.ceil((pad_b - min(bs)) * ppq) if min(bs) < pad_b else 0
    sk = math.ceil(pad_p - min(ps)) if min(ps) < pad_p else 0
    if not st and not sk:
        return letter_notes(sh, notes_of)
    pts = sh["pts"]
    sh["pts"] = [[b + st / ppq, p + sk] for b, p in pts]
    try:
        ns = np.array(letter_notes(sh, notes_of), dtype=np.int64)
    finally:
        sh["pts"] = pts
    if len(ns):
        ns[:, 0] -= st
        ns[:, 1] -= st
        ns[:, 2] -= sk
    return ns


def box_corners(pts):
    (b0, p0), (b1, p1), (b2, p2) = pts
    return [tuple(pts[0]), tuple(pts[1]), tuple(pts[2]), (b1 + b2 - b0, p1 + p2 - p0)]


def anchor_spot(sh, notes_of, ppq):
    """The anchor to keep the text on while it's rebuilt, or None. Turned / flipped / skewed since it was put
    there: where the text sits now instead (the letters don't line up on the old anchor any more: it jumped)."""
    at = anchor_at(sh)
    way = sh["text"].get("anchor_way")
    if at and way and frame_way(sh["pts"]) != way:
        ns = whole_letter_notes(sh, notes_of, ppq)
        if len(ns):
            t, k = anchor_ref(sh["text"], ns)
            at = (t / ppq, k)
    return at


def keep_anchor(sh, at):
    """The anchor `at` (beat, pitch) kept in the text's box, with which way the box points."""
    uv = frame_uv(sh["pts"], *at)
    if uv:
        sh["text"]["anchor"] = [round(uv[0], 9), round(uv[1], 9)]
        sh["text"]["anchor_way"] = frame_way(sh["pts"])
    else:
        sh["text"].pop("anchor", None)
        sh["text"].pop("anchor_way", None)


def align_parts(tx):
    """Which part of the text sits on its anchor, as (along time, across keys): 0 = start / lowest, 0.5 = middle,
    1 = end / highest. Piano roll: Align picks along time (Left = the first note starts there, Centre = the middle,
    Right = the last note ends there), the lowest note on the key. Player across / down: the first note in time,
    Align picks across the keys (left / low = the lowest note, centre, right / high = the highest)."""
    part = {"left": 0.0, "center": 0.5, "right": 1.0}[tx.get("align", "left")]
    return (part, 0.0) if tx.get("direction", "roll") == "roll" else (0.0, part)


def anchor_ref(tx, ns):
    """The spot of notes ns (tick, key) that goes on the anchor (align_parts); whole ticks / keys."""
    ft, fk = align_parts(tx)
    t0, t1 = int(ns[:, 0].min()), int(ns[:, 1].max())
    k0, k1 = int(ns[:, 2].min()), int(ns[:, 2].max())
    return t0 + math.floor((t1 - t0) * ft), k0 + math.floor((k1 - k0) * fk)


def onto_anchor(sh, at, notes_of, ppq, past=(False, False)):
    """Slide the text so the part Align picks (align_parts) sits on the anchor `at` (beat, pitch): Left = its first
    note starts there and its lowest note is on the anchor's key (a "g" tail too: the other letters sit higher), and
    keep the anchor in it. Whole ticks and whole keys only, so the notes move exactly."""
    # first the box roughly there (notes before beat 0 or under the lowest key aren't made: kept off those). Only
    # when it reaches there: a text that stays clear is measured where it is, so the letters typed before never
    # move when the anchor is already right (user 2026-11-12: an edge half way between two ticks let two spots
    # fit, and the rough move picked the other one at every second letter typed)
    ft, fk = align_parts(sh["text"])
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    corners = sh["pts"] + [[b1 + b2 - b0, p1 + p2 - p0]]
    bs, ps = [b for b, _ in corners], [p for _, p in corners]
    # (past = dragged before beat 0 / under key 0 already: kept there, no edge on that side)
    left = at[0] - ft * (max(bs) - min(bs))
    bottom = round(at[1]) - 0.5 - fk * (max(ps) - min(ps))
    left, bottom = left if past[0] else max(0.0, left), bottom if past[1] else max(-0.5, bottom)
    # (whole ticks / keys here too: a box moved by part of a tick can land its edges on other ticks)
    db, dp = round((left - min(bs)) * ppq) / ppq, round(bottom - min(ps))
    moved = (abs(db * ppq) > 1.5 or dp or (min(bs) < 0 and not past[0])  # (a tick or less: measured where it is)
             or (min(ps) < -0.5 and not past[1]))
    if moved:
        sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]
    tries = 0
    while tries < 4:  # (looked at again: an edge exactly half way between two ticks rounds either way, so a
        ns = whole_letter_notes(sh, notes_of, ppq)  # second try also moves it a quarter tick off that half way)
        if not len(ns) and not moved:  # (none seen where it is, e.g. past the keys: the rough move after all)
            moved = True
            sh["pts"] = [[b + db, p + dp] for b, p in sh["pts"]]
            continue
        if not len(ns):
            break
        t, k = anchor_ref(sh["text"], ns)
        dt, dk = round(at[0] * ppq) - t, round(at[1]) - k
        # typing never cuts letters (user 2026-11-12, picked A): a text that would reach before beat 0 / under key 0
        # stops there and grows the other way (measured whole, so Centre's middle isn't taken from what's left)
        dt = dt if past[0] else max(dt, -int(ns[:, 0].min()))
        dk = dk if past[1] else max(dk, -int(ns[:, 2].min()))
        if not dt and not dk:
            break
        nudge = 0.25 if tries == 1 and dt else 0.0
        sh["pts"] = [[b + (dt + nudge) / ppq, p + dk] for b, p in sh["pts"]]
        tries += 1
    keep_anchor(sh, at)


def rebuild(sh, tx, axes, notes_of, ppq):
    """build, keeping the text on its anchor (if it has one)."""
    at = anchor_spot(sh, notes_of, ppq)
    past = past_edges(sh, notes_of, ppq) if at else (False, False)
    if past[0] or past[1]:  # (kept where it sits on that side, not where it was clicked: a text stopped at beat 0
        ns = whole_letter_notes(sh, notes_of, ppq)  # then dragged mustn't jump onto its old click)
        if len(ns):
            t, k = anchor_ref(sh["text"], ns)
            at = (t / ppq if past[0] else at[0], k if past[1] else at[1])
    if not build(sh, tx, axes, ppq):
        return False
    if at:
        onto_anchor(sh, at, notes_of, ppq, past)
    return True


def past_edges(sh, notes_of, ppq):
    """(before beat 0, under key 0): the text already reaches there (dragged there with the piano roll's space past
    the edges): it stays where it was put, typing on never pulls it back (user 2026-11-12)."""
    bs, ps = zip(*box_corners(sh["pts"]))
    if min(bs) >= 0 and min(ps) >= -0.5:
        return False, False
    ns = whole_letter_notes(sh, notes_of, ppq)
    if not len(ns):
        return min(bs) < 0, min(ps) < -0.5
    return int(ns[:, 0].min()) < 0, int(ns[:, 2].min()) < 0


def set_anchor(sh, notes_of, ppq):
    """The anchor put where the text sits now (align_parts: its first note and lowest key for Left; nothing
    moves)."""
    ns = letter_notes(sh, notes_of)
    if len(ns):
        t, k = anchor_ref(sh["text"], ns)
        keep_anchor(sh, (t / ppq, k))


def split_letters(sh, notes_of, ppq):
    """Split into letters: each letter you can see as its own one-letter text (a copy of sh: fill, colour, gate
    ... kept) exactly where it was, so the notes stay the same; spaces go. [] if there's nothing to split.
    A line with joined letters splits into its LETTER GROUPS (user 2026-11-12: each keeps the joined shape it had:
    shaping.keep_form; lam-alef = one group of two letters, a Hindi conjunct one group)."""
    from notes import shaping
    tx, (O, X, Y) = sh["text"], text_axes(sh)
    info = {}
    contours, places = layout(tx, info)  # (place j = where character j went)
    first = {}  # (each glyph's first point in the whole text)
    for g, pts in contours:
        first.setdefault(g, pts[0])
    groups = {}  # ((line, letter group) -> its glyph numbers)
    for g, (n, cl, _) in enumerate(info["glyphs"]):
        if cl is not None:
            groups.setdefault((n, cl), []).append(g)
    base = {k: v for k, v in tx.items() if k not in ("anchor", "anchor_way", "bbox", "holes", "joined", "form")}
    font, out, j0 = text_font(tx), [], 0

    def put_one(letter, at):
        one = copy.deepcopy({k: v for k, v in sh.items() if k not in ("strokes", "pts", "text", "name")})
        if build(one, letter, (to_roll((O, X, Y), *at), X, Y)):
            set_anchor(one, notes_of, ppq)
            out.append(one)

    for n, line in enumerate(tx["text"].split("\n")):
        if any((n, c) in groups for c in range(len(line))):  # (laid out joined)
            sl = shaping.shape_line(font, line, tx["tracking"] / 1000)
            for c, e, ks in shaping.cluster_parts(sl, len(line)):
                was = next((first[g] for g in groups.get((n, c), ()) if g in first), None)
                if was is None:  # (a space: nothing to see)
                    continue
                form = shaping.keep_form(font, line, sl, c, e, ks)
                letter = dict(base, text=line[c:e], **({"form": form} if form else {}))
                own = layout(letter)[0]
                if own:  # (its own drawing moved onto where it was in the whole text)
                    put_one(letter, (was[0] - own[0][1][0][0], was[1] - own[0][1][0][1]))
        else:
            for k, ch in enumerate(line):
                j = j0 + k
                letter = dict(base, text=ch)
                own = layout(letter)[1][0]["o"]  # (where it would go alone: moved onto where it was)
                put_one(letter, (places[j]["o"][0] - own[0], places[j]["o"][1] - own[1]))
        j0 += len(line) + 1
    return out


def to_custom(sh):
    """Turn into custom shape: the letters stay as they look (Grow baked into their lines), but it's a plain custom
    shape now, filled by its rules (the notes can move a little: no threshold). A letter drawn from parts that
    overlap (some fonts' A, K, X) stays solid: Overlaps cancel out goes off and its real holes (inside an O) are
    emptied areas. Joined letters overlapping where they join: each joined piece becomes ONE outline (poly_union),
    so it stays filled there with the plain fill (Overlaps cancel out over a whole text = far too much memory)."""
    from notes.custom import refit, stroke_points
    from notes.hzbass import left_edge, shifted_hz
    left = left_edge(sh)  # (a Hz bass's tones count from here, Grow included: they stay on their beats)
    sh["strokes"] = list(sh["strokes"])  # (a plain custom shape now: its strokes get edited, no Strokes tag)
    tx = sh.pop("text")
    x0, y0, x1, y1 = tx["bbox"]
    info = {}
    cons = layout(tx, info)[0]
    groups = [info["glyphs"][g][2] for g, _ in cons]
    if tx.get("grow"):
        polys = text_polys(dict(sh, text=tx))
        sh["strokes"] = [{"kind": "poly", "pts": [list(frame_uv(sh["pts"], b, p)) for b, p in poly]} for poly in polys]
    if len(groups) == len(sh["strokes"]):
        groups = _merge_joined(sh, cons, groups, (x0, y0, x1 - x0, y1 - y0))
    sh["round"] = (y1 - y0) / (x1 - x0)  # (the letters' own look: the outline gate shrinks them the same way)
    refit(sh)
    if sh.get("hz"):
        sh["hz"] = shifted_hz(sh["hz"], left - left_edge(sh))
    loops = [[tuple(p) for p in stroke_points(st)] for st in sh["strokes"]]
    if len(groups) == len(loops) and parts_overlap(loops, groups):
        sh["union"] = True
        sh["areas"] = empty_areas(loops)


def _merge_joined(sh, cons, groups, box):
    """to_custom: each joined piece whose letters overlap (where they join) made one outline, in the strokes' own
    0..1 box (curves cut into steps as text_polys does). The pieces' new group names, one per stroke."""
    from notes.poly_union import union
    x0, y0, w, h = box
    pieces = {}
    for i, g in enumerate(groups):
        if g[0] == "p":
            pieces.setdefault(g, []).append(i)

    def poly(i):
        st = sh["strokes"][i]
        if st["kind"] == "poly":
            return [tuple(p) for p in st["pts"]]
        return [((x - x0) / w, (y - y0) / h) for x, y in flatten(cons[i][1], 0.001)]

    # (every piece of 2+ outlines: letters only touching where they join also count as crossing later on)
    merged = {g: union([poly(i) for i in idx]) for g, idx in pieces.items() if len(idx) > 1}
    if not merged:
        return groups
    strokes, out, done = [], [], set()
    for st, g in zip(sh["strokes"], groups):
        if g in merged:
            for k, loop in enumerate(merged.pop(g)):  # (at its first stroke's place; that piece's others go)
                strokes.append({"kind": "poly", "pts": [list(p) for p in loop]})
                out.append(("u", g, k))  # (each its own: they don't overlap any more)
            done.add(g)
        elif g not in done:
            strokes.append(st)
            out.append(g)
    sh["strokes"] = strokes
    return out


def _bunches(box):
    """The outlines (their boxes: x0, y0, x1, y1) bunched where boxes touch or overlap, a chain of them one bunch:
    lists of their numbers, each in order, bunches in order of their first one. Sorted along x, each box only
    against those still reaching it (a line of letters: a few each)."""
    n = len(box)
    head = list(range(n))

    def find(i):
        while head[i] != i:
            head[i] = head[head[i]]
            i = head[i]
        return i

    order = sorted(range(n), key=lambda i: box[i][0])
    live = []  # (boxes still reaching the sweep's x: only those can touch the next one)
    for i in order:
        x0, y0, x1, y1 = box[i]
        live = [j for j in live if box[j][2] >= x0]
        for j in live:
            if box[j][1] <= y1 and y0 <= box[j][3]:
                head[find(j)] = find(i)
        live.append(i)
    bunches = {}
    for i in range(n):
        bunches.setdefault(find(i), []).append(i)
    return list(bunches.values())


CROSS_ROWS = 64  # pieces of an outline checked at a time by parts_overlap


def parts_overlap(loops, glyphs):
    """Whether two outlines of one letter overlap with the same turn (the letter's parts drawn on top of each
    other: the text fills them, a custom shape's even-odd fill would empty them)."""
    def crosses(a, b):  # (any piece of a properly crossing a piece of b; pieces sharing an end don't count)
        a, b = np.asarray(a, float), np.asarray(b, float)
        if len(a) < 2 or len(b) < 2:
            return False
        lo, hi = np.minimum(b[:-1], b[1:]), np.maximum(b[:-1], b[1:])
        for s in range(0, len(a) - 1, CROSS_ROWS):  # (a run of a's pieces against only b's pieces near it: a
            # letter's outline has thousands of points, all against all took ~1 s a letter, speed hunt 2026-10-29)
            p = a[s:s + CROSS_ROWS + 1]
            plo, phi = p.min(0), p.max(0)
            near = np.flatnonzero((hi[:, 0] >= plo[0]) & (lo[:, 0] <= phi[0]) & (hi[:, 1] >= plo[1])
                                  & (lo[:, 1] <= phi[1]))
            if not len(near):
                continue
            ax, ay, bx, by = p[:-1, 0, None], p[:-1, 1, None], p[1:, 0, None], p[1:, 1, None]  # (pieces of a: rows)
            cx, cy, dx, dy = b[None, near, 0], b[None, near, 1], b[None, near + 1, 0], b[None, near + 1, 1]
            d1 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
            d2 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
            d3 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            d4 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
            if ((d1 * d2 < 0) & (d3 * d4 < 0)).any():
                return True
        return False

    box = [(min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p)) for p in loops]
    if any(crosses(p, p) for p in loops):  # (one outline crossing itself: some fonts' A)
        return True
    same = {}  # (each letter's outlines: only pairs within one letter are looked at)
    for i, g in enumerate(glyphs):
        same.setdefault(g, []).append(i)
    for idx in same.values():
        for i, j in itertools.combinations(idx, 2):
            a, b = box[i], box[j]
            if a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]:
                continue
            if (area(loops[i]) > 0) != (area(loops[j]) > 0):
                continue  # (a hole and its letter: fine either way)
            if crosses(loops[i], loops[j]) or winding(loops[j], *loops[i][0]) or winding(loops[i], *loops[j][0]):
                return True
    return False


SLIVER = 1e-9  # (an area this small, in the box's 0..1 units, is no area: empty_areas)


def empty_areas(loops):
    """Area colour spots (areas.py: [u, v, 0] = left empty) for every area the outlines close in that the text
    leaves empty (its turns add up to none, nonzero rule): inside an O, or between an A's legs and its crossbar
    when they're separate parts. With Overlaps cancel out off the rest is all filled.
    Worked out bunch by bunch (_bunches: outlines whose boxes touch): an area is closed in by one bunch's lines
    only, and the other bunches' outlines wind 0 times round it. All at once, faces.py's slabs ran across every
    letter of the line: 200 letters took 261 s and 23 GB (2026-11-12); bunch by bunch the areas are the same (a
    spot may sit elsewhere in its area)."""
    from notes.faces import Faces
    boxes = [(min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p)) for p in loops]
    out = []
    for idx in _bunches(boxes):
        part = [loops[i] for i in idx]
        xs, ys, size, lab = Faces([np.ascontiguousarray(p, float).reshape(-1, 2) for p in part]).pieces()
        for g in sorted(set(lab.tolist())):
            mine = np.flatnonzero(lab == g)
            if size[mine].sum() < SLIVER:  # (no room: between two lines lying on each other, e.g. where joined
                continue  # letters' strokes meet; its spot would land in the filled join)
            k = max(mine, key=lambda i: size[i])  # (its biggest piece's middle)
            x, y = float(xs[k]), float(ys[k])
            if not sum(winding(loops[i], x, y) for i in idx
                       if boxes[i][0] <= x <= boxes[i][2] and boxes[i][1] <= y <= boxes[i][3]):
                out.append([round(x, 9), round(y, 9), 0])
    return out


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

BATCH = os.environ.get("SPIDERWEB_TEXT_BATCH", "1") != "0"  # (textfast: all letters in one pass; off = one by one)
BATCH_FROM = 2  # (one outline: one by one, ~0.1 ms quicker; 2+ already quicker all at once, 10 letters 4.3 -> 1.7 ms)
_em = {}  # (Strokes token, bbox) -> the strokes cut into steps in em units (textfast), for the last few texts


def text_polys(sh):
    """The letters' outlines as closed polygons in beats / pitch, grown / shrunk by the grow setting."""
    tx = sh["text"]
    if BATCH and not tx.get("grow", 0.0) and len(sh["strokes"]) >= BATCH_FROM:
        return _batch_polys(sh)
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
    ppq = tx.get("ppq")
    if ppq:  # (laid out since 2026-11-12: on the ticks themselves, the same wherever the text is moved by whole ticks)
        return [[(tick_round(b, ppq), round(p, 9)) for b, p in poly] for poly in polys]
    return [[(round(b, 9), round(p, 9)) for b, p in poly] for poly in polys]


def _batch_polys(sh):
    """text_polys (no grow) for all letters at once (textfast), the very same numbers. The steps in em units are
    kept for a text whose strokes are still as build made them (strokes_token): the anchor's second making and
    every move only place them again."""
    from notes import textfast
    tx = sh["text"]
    x0, y0, x1, y1 = tx["bbox"]
    w, h = x1 - x0, y1 - y0
    tok = strokes_token(sh["strokes"])
    key = (tok, x0, y0, x1, y1)
    got = _em.get(key) if tok else None
    if got is None:
        P, off = textfast.points_of([st["pts"] for st in sh["strokes"]])
        E = np.empty_like(P)
        E[:, 0] = x0 + P[:, 0] * w
        E[:, 1] = y0 + P[:, 1] * h
        got = textfast.flatten_many(E, off, 0.001)
        if tok:
            if len(_em) >= 4:
                _em.pop(next(iter(_em)))
            _em[key] = got
    em, counts = got
    O, X, Y = text_axes(sh)
    bx = O[0] + em[:, 0] * X[0] + em[:, 1] * Y[0]
    by = O[1] + em[:, 0] * X[1] + em[:, 1] * Y[1]
    ppq = tx.get("ppq")
    bx = (np.floor(bx * ppq * TICK_STEPS + 0.5) / TICK_STEPS + TICK_LIFT) / ppq if ppq else textfast.round9(bx)
    return textfast.polys_of(bx, textfast.round9(by), counts)


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
