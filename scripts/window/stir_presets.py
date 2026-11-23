"""Stir presets (user 2026-11-16; a requester: a stir couldn't be reused in another project): named stirs kept one
file each in spiderweb/user/presets/Stir/ (window/preset_files.py), picked / saved / deleted in a row above the Stir window's tabs (window/
stir_window.py). A preset = one stir (the shown tab) or every tab ("Save all tabs"); picking one puts its stirs IN
PLACE OF the shown tab only (1 tab -> n), the other tabs untouched. Centre / Area numbers are % of the colour box, so
they carry over to any shape."""

import math

import numpy as np

from files.lang import tr
from notes.recolour import MOST_STIRS, clean_stir, full_stir, one_stir, order_of, stirs_of, with_order
from window import preset_files


def clean_stirs(stirs):
    """A preset's saved stirs -> [one_stir or {}] (1..MOST_STIRS), or None when nothing in it stirs (an empty
    preset would match every unstirred shape)."""
    if not isinstance(stirs, list) or not stirs:
        return None
    out = [one_stir(s) or {} for s in stirs[:MOST_STIRS] if isinstance(s, dict)]
    return out if clean_stir(out) else None


def load_presets():
    """The user's presets [{"name", "stirs"}], newest last (files / entries that can't be read left out)."""
    def clean(p):
        stirs = clean_stirs(p.get("stirs"))
        return stirs and {"name": p["name"].strip(), "stirs": stirs}
    return preset_files.STIR.entries(clean)


def same_name(p, name):
    """A preset with this name (capitals or not: one preset)."""
    return p["name"].strip().casefold() == name.strip().casefold()


def change_presets(name, stirs, parent):
    """The preset `name` saved as `stirs` (the newest now), or None: deleted (only the one with exactly this name when
    there is one; to the Recycle Bin). What went wrong is said. True when done."""
    title = tr("stir.preset_save_title" if stirs is not None else "stir.preset_delete_title")
    return preset_files.change(preset_files.STIR, name, None if stirs is None else {"stirs": stirs}, parent, title)


# FLIPPED SHAPES (hunt 11-17): a stir's numbers count where the shape was when its Recolour was set (rc "frame",
# recolour.py FRAME); a Flip / Turn 90 since shows them mirrored / turned. A preset keeps the stir AS SEEN (to_seen),
# and goes onto a flipped shape turned back into its frame (from_seen), so it looks the same everywhere. Every frame
# is made of three moves on the colour box: "x" (mirror sideways), "y" (upside down), "swap" (time <-> keys).

MOVES = {"x": ((-1.0, 0.0), (0.0, 1.0)), "y": ((1.0, 0.0), (0.0, -1.0)), "swap": ((0.0, 1.0), (1.0, 0.0))}


def _line_angle(a, move):
    """A direction (degrees, counted from "across" toward "up") after move, kept within -180..180."""
    a = 180.0 - a if move == "x" else -a if move == "y" else 90.0 - a
    return (a + 180.0) % 360.0 - 180.0 if abs(a) > 180 else a


def _area_moved(st, move):
    """The area's Angle / slant / Stretch after move: recolour.area_local's matrix (Stretch, slant, Angle undone)
    mirrored, then taken apart again into the same three (a swap turns a slant across into one up / down: the three
    worked out anew)."""
    a = math.radians(st["tilt"])
    w, h, s = st["stretch"] / 100, st["stretch_up"] / 100, st["skew"]
    rot = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
    m = np.array([[1 / w, -s / w], [0.0, 1 / h]]) @ rot
    p = np.array(MOVES[move])
    m = p @ m @ p
    # (RQ: m = [[1/w, -s/w], [0, 1/h]] @ rot(t), rot's second row from m's second row)
    h2 = 1 / math.hypot(*m[1])
    q2 = m[1] * h2
    u12 = float(m[0] @ q2)
    r1 = m[0] - u12 * q2
    w2 = 1 / math.hypot(*r1)
    t2 = math.degrees(math.atan2(-q2[0], q2[1]))
    st["stretch"], st["stretch_up"] = round(w2 * 100, 6), round(h2 * 100, 6)
    st["skew"] = -u12 * w2
    st["tilt"] = t2 if abs(t2) > 1e-9 else 0.0


def _moved(st, move):
    """One stir (filled in) with move done to its look (every number counted on the colour box: see MOVES)."""
    st = dict(st)
    if move == "swap":
        for k in ("centre", "spot"):
            if st[k]:
                st[k] = [st[k][1], st[k][0]]
        if st["ripple_kind"] in ("side", "up"):
            st["ripple_kind"] = "up" if st["ripple_kind"] == "side" else "side"
        if st["seen"]:  # (beats per key: across and up change places)
            st["seen"] = 1 / st["seen"]
    else:
        i = 0 if move == "x" else 1
        for k in ("centre", "spot"):
            if st[k]:
                c = list(st[k])
                c[i] = 100.0 - c[i]
                st[k] = c
        if st["ripple_kind"] in ("side", "up"):  # (one mirror: the wave starts the other way)
            st["ripple"] = -st["ripple"]
    for k in ("turn", "twist", "wring"):  # (each move is a mirror: every turn goes the other way)
        st[k] = -st[k] if st[k] else st[k]
    if st["wring"]:
        st["wring_angle"] = _line_angle(st["wring_angle"], move)
    if st["kaleido"] >= 2:  # (the slice's edges mirrored; the slice kept is the half before them)
        t = _line_angle(st["kaleido_turn"], move) - 180.0 / st["kaleido"]
        st["kaleido_turn"] = (t + 180.0) % 360.0 - 180.0
    if st["reach"] is not None and (st["tilt"] or st["skew"] or move == "swap"):
        _area_moved(st, move)
    if st["shape"]:
        if move == "swap" and st["reach"] is not None:  # (Size = the drawing's width: its height before)
            st["reach"] = st["reach"] / st["shape"]["aspect"]
        st["shape"] = _shape_moved(st["shape"], move)
    return st


def _shape_moved(shape, move):
    """A drawn area's own copy (loops in its 0..1 box, aspect = its width / height) with move done to it."""
    def pt(p):
        x, y = p
        return [y, x] if move == "swap" else [1.0 - x, y] if move == "x" else [x, 1.0 - y]
    out = dict(shape, loops=[[pt(p) for p in loop] for loop in shape["loops"]])
    if move == "swap" and shape.get("aspect"):
        out["aspect"] = 1 / shape["aspect"]
    return out


def frame_moves(f):
    """A frame (a, b, c, d) as moves done in order: place q = (a x + b y, c x + d y) round the box's middle."""
    a, b, c, d = f
    if not b:
        return (["x"] if a < 0 else []) + (["y"] if d < 0 else [])
    return ["swap"] + (["x"] if b < 0 else []) + (["y"] if c < 0 else [])


def _apply(stirs, moves):
    out = []
    for s in stirs:
        st = full_stir(s)
        for m in moves:
            st = _moved(st, m)
        out.append(one_stir(st) or {})
    return out


def to_seen(stirs, frame):
    """Stirs as a shape with this frame holds them -> as they look (the frame undone)."""
    from notes.recolour import SAME
    if not frame or tuple(frame) == SAME:
        return [one_stir(s) or {} for s in stirs]
    a, b, c, d = frame  # (the way back: the inverse of a frame is its transpose)
    return _apply(stirs, frame_moves((a, c, b, d)))


def from_seen(stirs, frame):
    """to_seen's way back: stirs as they look -> as a shape with this frame holds them."""
    from notes.recolour import SAME
    if not frame or tuple(frame) == SAME:
        return [one_stir(s) or {} for s in stirs]
    return _apply(stirs, frame_moves(frame))


def preset_label(p):
    """The name shown in the list: "Name (n tabs)" for a preset of several tabs."""
    n = len(p["stirs"])
    return tr("stir.preset_tabs", name=p["name"], n=n) if n > 1 else p["name"]


def fitted_label(p, fits):
    """preset_label cut to what fits (fits(text) -> True when it fits): only the NAME shortened with "…", the
    "(n tabs)" kept (hunt 11-17: a long name hid the count)."""
    full = preset_label(p)
    if fits(full):
        return full
    name = p["name"]
    lo, hi = 0, len(name)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(preset_label(dict(p, name=name[:mid].rstrip() + "…"))):
            lo = mid
        else:
            hi = mid - 1
    return preset_label(dict(p, name=name[:lo].rstrip() + "…"))


def seen_tabs(rc):
    """Recolour rc's Stir tabs as they look (to_seen: a flipped / turned shape's undone), filled in."""
    frame = (rc or {}).get("frame")
    if not frame:  # (not flipped: as they are, no work per tab)
        return stirs_of(rc)
    return [full_stir(s) for s in to_seen(stirs_of(rc), frame)]


def preset_tabs(seen, i, n):
    """Tabs i .. i + n - 1 of seen_tabs' list (missing ones: plain stirs), to tell whether a preset is there."""
    return [seen[k] if k < len(seen) else full_stir(None) for k in range(i, i + n)]


def tab_count_after(rc, i, n, count=1):
    """How many Stir tabs rc has once a preset of n tabs is put in place of tabs i .. i + count - 1."""
    return max(len(stirs_of(rc)), i + count) - count + n


def with_preset(old, i, stirs, stirred_rc, count=1):
    """old's Recolour (clean or None) with its Stir tabs i .. i + count - 1 replaced by the preset's stirs (count > 1:
    the preset the list named there, hunt 11-17: trying presets one after another swaps them, no tabs piling up; the
    stack padded with plain stirs up to them); the order of the colour steps written out in full: the preset's tabs
    take tab i's place, the other replaced tabs' places go (padded tabs and tabs not there yet go at the end, like
    "+")."""
    stack = stirs_of(old)
    had = len(stack)
    stack += [full_stir(None)] * (i + count - len(stack))
    put = [full_stir(s) for s in from_seen(stirs, (old or {}).get("frame"))]  # (as seen on a flipped shape too)
    new_stack = stack[:i] + put + stack[i + count:]
    o = order_of(old) if old else []
    out, seen = [], -1
    for k in o:  # (tab i's own "stir" entry becomes n of them, tabs i + 1 .. i + count - 1's go)
        if k == "stir":
            seen += 1
            if seen == i:
                out += ["stir"] * len(put)
                continue
            if i < seen < i + count:
                continue
        out.append(k)
    o = out + ["stir"] * (len(new_stack) - out.count("stir"))  # (tabs not in the order yet: at the end)
    rc = stirred_rc(old, new_stack, held=len(new_stack) if had else 0)  # (as with_stir: plain tabs on a shape with
    return with_order(rc, o) if rc else None  # no stir before = no stir)
