"""Warp own bends (QoL K, user 2026-11-23): named Warp grids kept in spiderweb/user/warp_presets.json, at the TOP of
the Warp window's Preset list under a line, Save… / Delete beside it (like Stir's presets, window/stir_presets.py).
Saved = the BEND only: grid size, points, curve handles (+ corner points), split lines, a split's inner controls and
Cells (Smooth / Straight); never Strength / Moves / Show / Colour edges / Folds / At the edges (each shape keeps its
own). Split lines are places in the box (shares of the shape), so a bend fits any shape. Kept AS SEEN: a flipped /
turned shape's grid is turned back first (to_seen) and laid onto another the way it looks there (from_seen), with
Stir presets' moves ("x" mirror sideways, "y" upside down, "swap" across <-> up)."""

import json
import os
from tkinter import messagebox

from files.about import USER
from files.lang import tr
from files.safefile import write_text
from notes import warp as wp_
from window.stir_presets import frame_moves

PRESETS_FILE = os.path.join(USER, "warp_presets.json")
BEND_KEYS = ("cols", "rows", "pts", "h", "corners", "in", "xs", "ys", "smooth")  # (what an own bend keeps)
SAME_PLACES = 7  # (two bends are the same when every number agrees to this many places: flips round to DIGITS)


def bend_of(w):
    """The bend part of a Warp (clean, BEND_KEYS only), or None (no Warp)."""
    w = wp_.clean_warp(w, keep=True)
    return {k: w[k] for k in BEND_KEYS if k in w} if w else None


def clean_bend(b):
    """A saved bend -> its clean form, or None (unreadable, or nothing bent: an own bend that bends nothing would
    match every fresh grid)."""
    if not isinstance(b, dict):
        return None
    w = wp_.clean_warp({k: b[k] for k in BEND_KEYS if k in b})  # (None: nothing moved and even lines)
    return {k: w[k] for k in BEND_KEYS if k in w} if w else None


def _point(k, c, r, move):
    """Point k of a c x r grid after move -> (its new number, the new grid's (c, r))."""
    j, i = divmod(k, c + 1)
    if move == "x":
        return j * (c + 1) + (c - i), (c, r)
    if move == "y":
        return (r - j) * (c + 1) + i, (c, r)
    return i * (r + 1) + j, (r, c)  # (swap: point (i, j) -> (j, i) of the r x c grid)


SIDE_MOVED = {"x": (1, 0, 2, 3), "y": (0, 1, 3, 2), "swap": (2, 3, 0, 1)}  # (a handle's side after the move)


def _vec(dx, dy, move):
    return [-dx, dy] if move == "x" else [dx, -dy] if move == "y" else [dy, dx]


def moved(b, move):
    """A bend (bend_of's form) with move done to how it looks: the grid mirrored / turned over, every offset, handle,
    inner control and split line along with it."""
    c, r = b["cols"], b["rows"]
    out = {k: b[k] for k in ("smooth",) if k in b}
    n = (c + 1) * (r + 1)
    pts = [None] * n
    for k, (dx, dy) in enumerate(b["pts"]):
        pts[_point(k, c, r, move)[0]] = _vec(dx, dy, move)
    nc, nr = (r, c) if move == "swap" else (c, r)
    out.update(cols=nc, rows=nr, pts=pts)
    if b.get("h"):
        out["h"] = sorted([_point(k, c, r, move)[0], SIDE_MOVED[move][s]] + _vec(dx, dy, move)
                          for k, s, dx, dy in b["h"])
    if b.get("corners"):
        out["corners"] = sorted(_point(k, c, r, move)[0] for k in b["corners"])
    if b.get("in"):
        got = []
        for k, s, dx, dy in b["in"]:
            j, i = divmod(k, c)
            bb, a = divmod(s, 2)
            if move == "x":
                k2, s2 = j * c + (c - 1 - i), (1 - a) + 2 * bb
            elif move == "y":
                k2, s2 = (r - 1 - j) * c + i, a + 2 * (1 - bb)
            else:
                k2, s2 = i * r + j, bb + 2 * a  # (the cell (i, j) -> (j, i) of the r x c grid; its slots too)
            got.append([k2, s2] + _vec(dx, dy, move))
        out["in"] = sorted(got)
    xs, ys = b.get("xs"), b.get("ys")
    if move == "x" and xs:
        xs = [1.0 - x for x in reversed(xs)]
    if move == "y" and ys:
        ys = [1.0 - y for y in reversed(ys)]
    if move == "swap":
        xs, ys = ys, xs
    if xs:
        out["xs"] = xs
    if ys:
        out["ys"] = ys
    w = wp_.clean_warp(out, keep=True)
    return {k: w[k] for k in BEND_KEYS if k in w} if w else out


def _apply(b, moves):
    for m in moves:
        b = moved(b, m)
    return b


def to_seen(b, frame):
    """A bend as a shape with this Recolour frame holds it -> as it looks (the frame undone)."""
    from notes.recolour import SAME
    if not b or not frame or tuple(frame) == SAME:
        return b
    a, bb, c, d = frame  # (the way back: a frame's inverse is its transpose, as stir_presets.to_seen)
    return _apply(b, frame_moves((a, c, bb, d)))


def from_seen(b, frame):
    """to_seen's way back: a bend as it looks -> as a shape with this frame holds it."""
    from notes.recolour import SAME
    if not b or not frame or tuple(frame) == SAME:
        return b
    return _apply(b, frame_moves(frame))


def same(a, b):
    """Two bends alike (numbers to SAME_PLACES places)."""
    def key(v):
        if isinstance(v, float):
            return round(v, SAME_PLACES) + 0.0
        if isinstance(v, list):
            return [key(x) for x in v]
        if isinstance(v, dict):
            return {k: key(x) for k, x in v.items()}
        return v
    return a is not None and b is not None and key(a) == key(b)


def read_file():
    """The file's entries as they are ([] without a file), None when it can't be read (broken)."""
    if not os.path.exists(PRESETS_FILE):
        return []
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            items = json.load(f).get("presets", [])
        return items if isinstance(items, list) else None
    except (OSError, ValueError, AttributeError):
        return None


def load_presets():
    """The user's own bends [{"name", "bend"}] in the file's order (newest last; broken entries left out)."""
    out = []
    for p in read_file() or ():
        if isinstance(p, dict) and str(p.get("name", "")).strip():
            b = clean_bend(p.get("bend"))
            if b:
                out.append({"name": str(p["name"]).strip(), "bend": b})
    return out


def same_name(p, name):
    return isinstance(p, dict) and str(p.get("name", "")).strip().casefold() == name.casefold()


def change_presets(name, bend, parent):
    """The own bend `name` replaced by `bend` (None: deleted; a new one goes last = newest), the other entries kept as
    they are. A file that can't be read is kept aside first (warp_presets-broken.json, said). False (and said) when
    the file can't be written."""
    items = read_file()
    try:
        if items is None:
            broken = os.path.splitext(PRESETS_FILE)[0] + "-broken.json"
            os.replace(PRESETS_FILE, broken)
            messagebox.showwarning(tr("warp.own_save_title"), tr("warp.own_file_broken", path=broken), parent=parent)
            items = []
        if bend is None and any(isinstance(p, dict) and str(p.get("name", "")).strip() == name for p in items):
            items = [p for p in items if not (isinstance(p, dict) and str(p.get("name", "")).strip() == name)]
        else:
            items = [p for p in items if not same_name(p, name)]
        if bend is not None:
            items.append({"name": name, "bend": bend})
        write_text(PRESETS_FILE, json.dumps({"presets": items}, indent=1))
    except OSError as e:
        messagebox.showerror(tr("warp.own_save_title"), tr("warp.own_save_failed", path=PRESETS_FILE, e=e),
                             parent=parent)
        return False
    return True


def laid(old_w, bend, frame, base):
    """A shape's new Warp with an own bend laid on it as it looks there: the bend's parts in place of the grid's,
    the shape's other settings (Strength, Moves, Colour edges, Folds, At the edges) kept; base = the window's picks
    for a shape without a Warp. Shown again (like a preset laid)."""
    own = from_seen(bend, frame)
    w = dict(wp_.full(old_w) if old_w else base)
    for k in BEND_KEYS + ("preset", "on"):
        w.pop(k, None)
    w.update(own)
    return wp_.clean_warp(w)
