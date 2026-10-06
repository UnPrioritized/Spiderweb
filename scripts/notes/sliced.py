"""Sliced pieces: a line kind cut by Split here or the Slice tool keeps every note where it was (user, 2026-10-06).

Each piece remembers the shape it was cut from, sh["cut"]:
  whole  the shape it was cut from (its outline keys + velocities), where it was when this piece was made
  was    the piece's own outline keys then (moved since = all its points moved by the same amount)
  part   [from, to]: the spots on the whole's path (path_notes where) its notes start on; to None = the end
         (a custom shape's: the Slice cuts [beat, key, d beats, d keys, side], its side of each kept)
  knife  a line kind's Slice cuts through its notes (a shape with pages / glue: knife_in_two), like a custom part
  vel    the piece's velocities then (unchanged: the whole's velocities are used, so they're exact), or None = its own
  marks  its cut ends, drawn while it's selected: {id, at (beat, key, where it was then), u (spot), kind "split" /
         "slice", dir (the Slice line's (beats, keys) direction)}; the other piece at that cut has the same id
A piece makes its notes as the whole did and keeps the ones starting on its part, so the pieces' notes together are
the whole's exactly, except the note sounding at a cut: it's cut in two there, so the next piece starts with a note
right at the cut (user; cut_through); Colours count as in the whole.
Moving it (or a copy) keeps that; changing its outline (points, size, turn, flip, Straighten, tumours, a formula,
Last note) makes it a shape of its own (App.shapes_changed drops "cut"), and so does "Turn into a complete shape".
"""

import json
import math

import numpy as np

from notes.paths import dedupe, dot_segment_notes, path_notes

# what makes a line kind's path (besides its points): changed = no longer the piece it was (end_dot too: "Last note"
# changed on a piece is about its own last note)
OUTLINE = ("kind", "pts", "k", "smooth", "tumour", "tumours", "gaps", "splits", "sharp", "sym", "shape", "pattern",
           "end_dot")
CUSTOM_OUTLINE = ("kind", "pts", "strokes", "areas", "round")  # a custom shape's (its strokes are in its frame)
VELOCITY = ("vel0", "vel1", "vel_env")
GATE = ("gate", "range")  # a custom piece's spam gate: the whole's until it's changed (slice shares out a Range)
BIG = 1 << 30  # a joined curve's piece k has the spots k * BIG + ...
WHOLE_TOOLS = ("glue", "fx")  # the whole's glue and note tool pages: done on the whole, then the pieces cut (user:
# slicing keeps the look; the piece's own glue / pages added later go on its part)


def keys_of(sh):
    return CUSTOM_OUTLINE if sh["kind"] == "custom" else OUTLINE


def outline(sh):
    return {k: json.loads(json.dumps(sh[k])) for k in keys_of(sh) if k in sh}


def velocity(sh, keys=VELOCITY):
    return {k: json.loads(json.dumps(sh[k])) for k in keys if k in sh}


def tools(sh):
    """sh's glue and note tool pages (for a whole: done before its pieces are cut)."""
    return velocity(sh, WHOLE_TOOLS)


def inner(sh):
    """A whole that is a piece itself (a piece with its own pages sliced again, knife_in_two): its cut, else {}."""
    return {"cut": json.loads(json.dumps(sh["cut"]))} if sh.get("cut") else {}


def completed(sh):
    """A piece that's becoming a shape of its own (its outline changed, Turn into a complete shape): it takes the
    whole's glue (the boxes inside it) and note tool pages, before its own, so they now work on its own notes.
    Changes sh; its "cut" is dropped."""
    cut = sh.pop("cut", None)
    whole = (cut or {}).get("whole") or {}
    if whole.get("cut"):  # (cut from a piece: its own whole's pages / glue first, then its own)
        whole = json.loads(json.dumps(whole))
        completed(whole)
    if whole.get("fx"):
        sh["fx"] = json.loads(json.dumps(whole["fx"])) + (sh.get("fx") or [])
    gl = whole.get("glue")
    if gl and sh.get("glue") is not True:  # (the boxes inside it as it was when cut, as shares of its own box)
        from notes.engine import cached_arrays
        from notes.glue import added, for_part, glue_box
        part = for_part(gl, glue_box(np.concatenate(cached_arrays(whole))),
                        glue_box(np.concatenate(cached_arrays(cut["was"]))))
        for b in ([True] if part is True else part or ()):
            sh["glue"] = added(sh.get("glue"), b)


def whole_made(cut):
    """Do a piece's notes come from the glue / note tool pages of the shape it was cut from (or that one is a piece
    itself)?"""
    whole = cut.get("whole") or {}
    return bool(whole.get("fx") or whole.get("glue") or whole.get("cut"))


def steps_kept(sh):
    """sh is a piece whose notes come from its whole's glue / pages: flipping / turning it is a flip / turn step on
    its notes, like on a shape with pages (user, 2026-10-07: it became a shape of its own, all the whole's notes
    back), and it stays a piece (its notes are made from its drawing turned / flipped back: engine.as_made)."""
    return bool(sh.get("cut")) and whole_made(sh["cut"])


def split_here_ok(sh):
    """Can Split here cut sh? Not a piece flipped / turned as a shape with pages, or one cut through its notes after
    its own pages (its notes don't follow a spot on the drawing any more: the Slice tool cuts it)."""
    cut = sh.get("cut")
    return not cut or moved_by(sh) is not None and not (cut.get("whole") or {}).get("cut")


def close(a, b):
    """a == b, numbers within a little (a drawing turned / flipped back isn't exact to the last digit)."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    return a == b


def moved_by(sh):
    """How far a sliced piece was moved since it was cut, (beats, keys); None when it isn't a piece any more (its
    outline changed) or never was. (Flipped / turned with steps: moved_by(engine.as_made(sh)).)"""
    cut = sh.get("cut")
    if not cut:
        return None
    was = cut["was"]
    if (len(sh["pts"]) != len(was["pts"]) or was.get("kind") != sh["kind"]
            or any(not close(sh.get(k), was.get(k)) for k in keys_of(sh) if k != "pts")):
        return None
    d = np.asarray(sh["pts"], float) - np.asarray(was["pts"], float)
    if not len(d) or np.abs(d - d[0]).max() > 1e-6:  # (a save file trims the last digits)
        return None
    return float(d[0, 0]), float(d[0, 1])


def moved(sh, d):
    """sh's points moved by d (beats, keys)."""
    return dict(sh, pts=[[b + d[0], p + d[1]] for b, p in sh["pts"]])


def source(sh):
    """A sliced piece -> (the shape its notes are made from: the whole, moved as far as the piece was, with its
    glue and note tool pages and the piece's other settings; its part; whether its velocities are the whole's), else
    None. (The piece's own glue / pages go on its part afterwards.)"""
    d = moved_by(sh)
    if d is None:
        return None
    cut = sh["cut"]
    same_vel = cut.get("vel") is not None and close(velocity(sh), cut["vel"])
    own = keys_of(sh) + VELOCITY + (GATE if sh["kind"] == "custom" else ()) + WHOLE_TOOLS
    src = {k: v for k, v in sh.items() if k not in own and k != "cut"}
    src.update(outline(moved(cut["whole"], d)))
    src.update(tools(cut["whole"]))
    src.update(inner(cut["whole"]))  # (cut from a piece: that one's own cut; moved along with it)
    src.update(velocity(cut["whole"] if same_vel else sh))
    if sh["kind"] == "custom":
        same_gate = cut.get("gate") is not None and close(velocity(sh, GATE), cut["gate"])
        src.update(velocity(cut["whole"] if same_gate else sh, GATE))
    return src, cut["part"], same_vel


def paths_of(sh):
    """The paths a line kind's notes are made from, each first to last point in time (engine._notes_tracks)."""
    from notes.engine import cached_arrays
    arrs = cached_arrays(sh)
    arrs = [dedupe(np.concatenate(arrs))] if len(arrs) == 1 else [dedupe(a) for a in arrs]
    return [a[::-1] if len(a) and a[-1, 0] < a[0, 0] else a for a in arrs]


def spotted_notes(sh, ppq, end_dot):
    """A line kind's (start, end, key) notes, as engine._notes_tracks makes them, and each one's spot."""
    paths = paths_of(sh)
    if len(paths) > 1:  # a joined curve's pieces: each like a line
        got = [path_notes(a * [ppq, 1], end_dot, where=True) for a in paths]
        return (np.concatenate([np.asarray(n, np.int64).reshape(-1, 3) for n, _ in got]),
                np.concatenate([u + k * BIG for k, (_, u) in enumerate(got)]))
    path = paths[0] * [ppq, 1]
    if end_dot and sh["kind"] == "poly" and len(path) > 2 and not (sh.get("shape") or sh.get("pattern")):
        return dot_segment_notes(path, where=True)
    return path_notes(path, end_dot, where=True)


def piece_notes(sh, ppq, keys, make):
    """A custom piece's (notes, tracks): the whole's (make = engine._notes_tracks), cut at every cut like a knife went
    through them (user: a spam note across it leaves a sliver; spam keeps the whole's grid, an Empty shape's cut side
    makes no notes), the parts on its side kept; velocities over its own time when they were changed. None: not a
    piece any more. (Also a line piece cut from a piece with pages of its own: its whole's notes, its knife cuts.)"""
    got = source(sh)
    if got is None:
        return None
    src, halves, same_vel = got
    if sh["kind"] != "custom":
        halves = sh["cut"].get("knife") or []
    key = (json.dumps(src, sort_keys=True), ppq, keys)
    if key not in _wholes:
        if len(_wholes) > 8:
            _wholes.clear()
        _wholes[key] = make(src, ppq, keys)
    notes, tracks = knife_cut(*_wholes[key], halves, moved_by(sh), ppq)  # (the cuts went along with it)
    if not same_vel and len(notes):  # (its own velocities: over its own time, as a shape of its own)
        from notes.engine import cached_arrays
        from notes.envelope import env_values, velocity_env
        t = np.concatenate(cached_arrays(sh))[:, 0] * ppq
        env = velocity_env(sh)
        if len({v for _, v in env}) == 1:
            notes[:, 3] = max(1, min(127, round(env[0][1])))
        else:
            frac = np.clip((notes[:, 0] - t.min()) / max(t.max() - t.min(), 1e-9), 0, 1)
            notes[:, 3] = np.clip(np.round(env_values(env, frac)), 1, 127).astype(np.int64)
    return notes, tracks


_wholes = {}  # the whole shape's notes, shared by its pieces


def knife_cut(notes, tracks, halves, d, ppq):
    """Notes (start, end, key, any other columns) and tracks cut like a knife went through them: each half
    [b0, k0, db, dk, side] keeps what's on its side of the line through (b0, k0) going (db, dk) (beats, keys; moved
    by d since), a note across it cut in two there."""
    k = notes[:, 2].astype(float)
    lo, hi = notes[:, 0].astype(float), notes[:, 1].astype(float)
    keep = np.ones(len(notes), bool)
    mb, mk = d
    for b0, k0, db, dk, sd in halves:
        b0, k0 = b0 + mb, k0 + mk
        # which side of the cut (beat, key) is on: db * (key - k0) - dk * (beat - b0) >= 0 is side 1, < 0 side -1
        # (a spot right on it goes to side 1 only); on a key row that's c0 + slope * tick
        c0 = db * (k - k0) + dk * b0
        slope = -dk / ppq
        if abs(slope) < 1e-15:  # (a flat cut: whole rows on one side)
            keep &= (c0 >= 0) if sd > 0 else (c0 < 0)
            continue
        x = -c0 / slope  # the tick where the cut crosses the row's middle
        if (slope > 0) == (sd > 0):
            lo = np.maximum(lo, x)
        else:
            hi = np.minimum(hi, x)
    lo, hi = np.round(lo), np.round(hi)
    keep &= hi > lo
    notes = notes[keep].copy()
    notes[:, 0], notes[:, 1] = lo[keep], hi[keep]
    return notes, None if tracks is None else np.asarray(tracks)[keep]


def tooled(sh):
    """Do sh's notes come from glue / note tool pages (its own, or its whole's for a piece)? Then the Slice tool cuts
    its notes, not its drawing (user, 2026-10-06: they can sit far off the drawing)."""
    if sh.get("fx") or sh.get("glue"):
        return True
    got = source(sh) if sh.get("cut") else None
    return bool(got and (got[0].get("fx") or got[0].get("glue")))


def notes_across(notes, a, b, ppq):
    """Does the knife a-b (beats, keys) go all the way across the notes (on each key row the line through it passes
    between the row's first note's start and last note's end, inside a-b), leaving notes on both sides? -> the
    stretch of it over the notes [[a', b']] (its mark), else None. "crossed": it passed through some notes (if not
    all the way)."""
    if not len(notes):
        return None, False
    a, b = np.asarray(a, float), np.asarray(b, float)
    db, dk = b - a
    rows = notes[:, 2]
    keys, at = np.unique(rows, return_inverse=True)
    first = np.full(len(keys), np.inf)
    last = np.full(len(keys), -np.inf)
    np.minimum.at(first, at, notes[:, 0] / ppq)
    np.maximum.at(last, at, notes[:, 1] / ppq)
    if abs(dk) < 1e-12:  # (flat: between key rows, it has to go past the notes of the rows beside it)
        if abs(db) < 1e-12:
            return None, False
        near = np.abs(keys - a[1]) <= 1
        lo, hi = (first[near].min(), last[near].max()) if near.any() else (first.min(), last.max())
        s0, s1 = sorted(((lo - a[0]) / db, (hi - a[0]) / db))
        crossed = s1 > 0 and s0 < 1 and (keys.min() < a[1] < keys.max())
        if s0 < -1e-9 or s1 > 1 + 1e-9:
            return None, crossed
    else:
        s = (keys - a[1]) / dk  # (where along a-b it crosses each row's middle)
        x = a[0] + db * s
        inside = (x > first) & (x < last)
        if not inside.any():
            return None, False
        s = s[inside]
        crossed = bool(((s >= 0) & (s <= 1)).any())
        if s.min() < -1e-9 or s.max() > 1 + 1e-9:
            return None, crossed
        half = 0.5 / abs(dk)  # (half a key row either side)
        s0, s1 = max(0.0, s.min() - half), min(1.0, s.max() + half)
    if not all(len(knife_cut(notes, None, [[a[0], a[1], db, dk, sd]], (0, 0), ppq)[0]) for sd in (1, -1)):
        return None, crossed
    return [[list(a + (b - a) * s0), list(a + (b - a) * s1)]], True


def knife_in_two(sh, halves, a, b, segs):
    """sh (a line kind or custom shape with pages / glue, maybe a piece already) cut by the Slice tool through its
    NOTES along a-b: halves = two copies of it, each made a piece keeping the whole's notes on its side; their drawing
    stays the whole's. segs: the knife's stretch over the notes (the mark)."""
    import uuid
    custom = sh["kind"] == "custom"
    got = source(sh)
    if sh.get("cut") and (sh.get("fx") or sh.get("glue") or not got):
        # a piece with pages / glue of its own (or flipped / turned with steps): the halves are cut from it as it is,
        # its notes after them (user, 2026-10-07: cut before them, the halves' notes changed)
        d = moved_by(sh)
        marks = [moved_mark(m, d) for m in sh["cut"].get("marks", [])] if d else []
        part, knives, own = [0.0, None], [], False
        whole = dict(outline(sh), **velocity(sh), **tools(sh), cut=json.loads(json.dumps(sh["cut"])))
        if custom:
            whole.update(velocity(sh, GATE))
        for h in halves:
            for k in WHOLE_TOOLS:
                h.pop(k, None)
    elif got:
        src, part, same_vel = got
        cut = sh["cut"]
        d = moved_by(sh)
        marks = [moved_mark(m, d) for m in cut.get("marks", [])]
        knives = [[b0 + d[0], k0 + d[1], db, dk, sd] for b0, k0, db, dk, sd in (part if custom else
                                                                               cut.get("knife", []))]
        own = cut.get("own_vel") or cut.get("vel") is not None and not same_vel
        whole = dict(outline(src), **velocity(cut["whole"]), **tools(cut["whole"]), **inner(src))
        if custom:
            whole.update(velocity(src, GATE))
    else:
        part, marks, knives, own = [0.0, None], [], [], False
        whole = dict(outline(sh), **velocity(sh), **tools(sh))
        if custom:
            whole.update(velocity(sh, GATE))
        for h in halves:  # (the whole's glue / pages are done before the cut now)
            for k in WHOLE_TOOLS:
                h.pop(k, None)
    a, b = np.asarray(a, float), np.asarray(b, float)
    mid = np.mean([np.mean(sg, axis=0) for sg in segs], axis=0)
    new = {"id": uuid.uuid4().hex[:12], "kind": "slice", "at": [float(mid[0]), float(mid[1])], "u": 0.0,
           "dir": [float(b[0] - a[0]), float(b[1] - a[1])], "segs": segs}
    for h, sd in zip(halves, (1, -1)):
        mine = [m for m in marks if knife_side(m["at"], a, b) in (0, sd)]
        knife = knives + [[float(a[0]), float(a[1]), float(b[0] - a[0]), float(b[1] - a[1]), sd]]
        h["cut"] = {"whole": whole, "was": outline(h), "part": knife if custom else part, "vel": None,
                    "marks": mine + [new]}
        if custom:
            h["cut"]["gate"] = velocity(h, GATE)
        else:
            h["cut"]["knife"] = knife
        if own:
            h["cut"]["own_vel"] = True


def knife_side(pt, a, b):
    """Which side of the knife a-b a spot is on, as knife_cut counts it (+1 / -1; 0 = on it)."""
    c = (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0])
    return 0 if abs(c) < 1e-9 else (1 if c > 0 else -1)


def run_origins(sh, notes, ppq):
    """A sliced Fill piece's chop (user: the rhythm goes on across the cut): where each of its notes' run started in
    the whole shape (chop.run_starts there), or None."""
    from notes.chop import run_starts
    from notes.engine import _notes_tracks
    got = source(sh)
    if got is None:
        return None
    text = json.dumps(got[0], sort_keys=True)
    whole = next((v[0] for k, v in _wholes.items() if k[0] == text and k[1] == ppq), None)
    if whole is None:
        whole = _notes_tracks(got[0], ppq, 256)[0]
    if not len(whole):
        return None
    rs = run_starts(whole)
    order = np.lexsort((whole[:, 0], whole[:, 2]))
    big = int(max(whole[:, 1].max(), notes[:, 1].max())) + 2
    keyed = whole[order, 2] * big + whole[order, 0]
    i = np.clip(np.searchsorted(keyed, notes[:, 2] * big + notes[:, 0], "right") - 1, 0, len(order) - 1)
    hit = whole[order[i], 2] == notes[:, 2]
    return np.where(hit, rs[order[i]], notes[:, 0]).astype(float)


def in_part(u, part):
    a, b = part
    return (u >= a) & (u < b) if b is not None else u >= a


def cut_through(whole, raw, spots, tracks, part, ppq):
    """A line piece's notes (the whole's, before its part is kept): the note sounding at each of the piece's cuts is
    cut in two there (user: slicing adds a note at the cut), the second half starting on the cut's spot, so the piece
    before keeps the first half and the piece after starts with the second (same key and colour)."""
    paths = paths_of(whole)
    for c in sorted(u for u in part if u):
        k = int(c // BIG)
        if k >= len(paths) or len(paths[k]) < 2:
            continue
        a = paths[k]
        r = c - k * BIG
        i = max(0, min(int(r), len(a) - 2))
        beat, key = a[i] + (a[i + 1] - a[i]) * (r - i)
        t = int(round(float(beat) * ppq))
        hit = (spots < c) & (raw[:, 0] < t) & (raw[:, 1] > t) & (np.abs(raw[:, 2] - key) <= 1)
        if not hit.any():
            continue
        j = int(np.flatnonzero(hit)[np.argmax(spots[hit])])  # (the one started last along the path)
        second = raw[j:j + 1].copy()  # (any other columns, a velocity, go along)
        second[0, 0] = t
        raw = np.concatenate([raw, second])
        raw[j, 1] = t
        spots = np.append(spots, c)
        if tracks is not None:
            tracks = np.append(tracks, tracks[j])
    return raw, spots, tracks


def spot_of(sh, at, scale, part=None):
    """The spot on sh's path nearest the point at (beat, key); scale = (px per beat, px per key) to measure in.
    part: only there."""
    best = None
    for k, a in enumerate(paths_of(sh)):
        if len(a) < 2:
            continue
        p = a * scale
        q = np.asarray(at, float) * scale
        ab = p[1:] - p[:-1]
        L = (ab ** 2).sum(1)
        f = np.clip(((q - p[:-1]) * ab).sum(1) / np.where(L > 0, L, 1), 0, 1)
        u = k * BIG + np.arange(len(ab)) + f
        d = np.hypot(*(p[:-1] + ab * f[:, None] - q).T)
        if part is not None:
            lo, hi = part
            if hi is not None:
                u = np.minimum(u, hi)
            u = np.maximum(u, lo)
            d = np.where((k * BIG + np.arange(len(ab)) + 1 > lo) &
                         ((k * BIG + np.arange(len(ab)) < hi) if hi is not None else True), d, np.inf)
        j = int(np.argmin(d))
        if best is None or d[j] < best[0]:
            best = (float(d[j]), float(u[j]))
    return best[1] if best else 0.0


def cut_in_two(sh, halves, at, scale, mark):
    """sh (a line kind, maybe a piece already) cut in two at at (beat, key): each half made a piece of the shape sh
    was cut from (or of sh). mark: {"kind", "dir"?} for the cut's marks."""
    import uuid
    from notes.engine import cached_path
    got = source(sh)
    if got:
        src, part, same_vel = got
        cut = sh["cut"]
        d = moved_by(sh)
        marks = [moved_mark(m, d) for m in cut.get("marks", [])]
        # (its velocities changed since it was cut: the halves keep their own, cut from its)
        own = cut.get("own_vel") or cut.get("vel") is not None and not same_vel
        whole = dict(outline(src), **velocity(cut["whole"]), **tools(cut["whole"]), **inner(src))
        # (cut through its notes by the Slice tool before: both halves keep that cut)
        knives = [[b0 + d[0], k0 + d[1], db, dk, sd] for b0, k0, db, dk, sd in cut.get("knife", [])]
    else:
        src, part, marks, own, knives = sh, [0.0, None], [], False, []
        whole = dict(outline(sh), **velocity(sh), **tools(sh))
        for h in halves:  # (the whole's glue / pages are done before the cut now)
            for k in WHOLE_TOOLS:
                h.pop(k, None)
    uc = spot_of(src, at, scale, part)
    mids = []
    for h in halves:
        path = cached_path(h)
        mids.append(spot_of(src, path[len(path) // 2], scale, part))
    new = dict(mark, id=uuid.uuid4().hex[:12], at=[float(at[0]), float(at[1])], u=uc)
    order = sorted(range(2), key=lambda i: mids[i])
    for i, (a, b) in zip(order, ((part[0], uc), (uc, part[1]))):
        h = halves[i]
        # (a cut through the notes, its stretch over them: both halves have notes along it)
        mine = [m for m in marks if m.get("segs") or m["u"] >= a - 1e-9 and (b is None or m["u"] <= b + 1e-9)]
        h["cut"] = {"whole": whole, "was": outline(h), "part": [a, b], "vel": None, "marks": mine + [new]}
        if knives:
            h["cut"]["knife"] = json.loads(json.dumps(knives))
        if own:
            h["cut"]["own_vel"] = True


CANT = "can't"  # rejoined: pieces cut through their notes that can't become one piece


def rejoined(olds, new, join=None):
    """Join of pieces of one shape that follow on from each other, or lie on both sides of a Slice cut through its
    notes (and were moved together, if at all): all of them = that shape back as it was (with new's other settings:
    the join's), else one bigger piece (its drawing: new, the joined curve; join(shapes) joins some of them when
    others lie on top of them). None: not such pieces (a normal join); CANT: pieces cut through their notes that
    don't make one piece (a normal join would draw the drawing twice and lose their notes)."""
    got = [source(sh) for sh in olds]
    fail = CANT if any((sh.get("cut") or {}).get("knife") for sh in olds) else None
    if not all(got):
        return fail
    wholes = [json.dumps(dict(outline(g[0]), **tools(g[0]), cut=g[0].get("cut")), sort_keys=True) for g in got]
    if len(set(wholes)) > 1:
        return fail
    items = []
    for sh, g in zip(olds, got):
        d = moved_by(sh)
        items.append([g[1], [[b0 + d[0], k0 + d[1], db, dk, sd] for b0, k0, db, dk, sd in sh["cut"].get("knife", [])]])
    items = merged_parts(items)
    if len(items) > 1:
        return fail
    part, knives = items[0]
    same_vel = all(g[2] for g in got)
    src = got[0][0]
    if part[0] == 0 and part[1] is None and not knives:  # all of it: the shape it was
        out = {k: v for k, v in new.items() if k not in OUTLINE and k != "cut" and not (same_vel and k in VELOCITY)}
        out.update(outline(src))
        if same_vel:
            out.update(velocity(src))
        if src.get("fx"):  # (its pages first, then the ones the pieces had alike)
            out["fx"] = json.loads(json.dumps(src["fx"])) + (out.get("fx") or [])
        if src.get("glue"):
            out["glue"] = json.loads(json.dumps(src["glue"]))
        if src.get("cut"):  # (a piece itself: the one these were cut from)
            out["cut"] = json.loads(json.dumps(src["cut"]))
        return out
    drawings = {}  # (pieces cut through their notes keep the same drawing: once)
    for sh in olds:
        drawings.setdefault(json.dumps(outline(sh), sort_keys=True), sh)
    if len(drawings) == 1:
        base = next(iter(drawings.values()))
        new = dict({k: v for k, v in new.items() if k not in OUTLINE and k not in VELOCITY}, **outline(base),
                   **velocity(base))
    elif len(drawings) < len(olds):
        new = join(list(drawings.values())) if join else None
        if new is None:
            return fail
    marks = {}
    for sh in olds:  # (the cuts joined up again are gone)
        d = moved_by(sh)
        for m in sh["cut"].get("marks", []):
            m = moved_mark(m, d)
            if m["id"] not in marks and (any(on_knife(m, k) for k in knives) if m.get("segs") else
                                         any(e is not None and abs(m["u"] - e) <= 1e-9 for e in part)):
                marks[m["id"]] = m
    whole = dict(outline(src), **velocity(olds[0]["cut"]["whole"]), **tools(olds[0]["cut"]["whole"]))
    if src.get("cut"):
        whole["cut"] = json.loads(json.dumps(src["cut"]))
    out = dict(new, cut={"whole": whole, "was": outline(new), "part": list(part), "vel": None,
                         "marks": list(marks.values())})
    if knives:
        out["cut"]["knife"] = knives
    if same_vel:
        out["cut"]["vel"] = velocity(new)
    else:
        out["cut"]["own_vel"] = True
    return out


def merged_parts(items):
    """Pieces of one shape, [part, knives] each (knives where they are now), joined as far as they go: two pieces
    next to each other on the path with the same cuts through their notes, or the two sides of one such cut with
    the same part, become one."""
    items = [[list(p), list(k)] for p, k in items]
    again = True
    while again:
        again = False
        for i in range(len(items)):
            for j in range(len(items)):
                got = i != j and _merged(items[i], items[j])
                if got:
                    items[i] = got
                    del items[j]
                    again = True
                    break
            if again:
                break
    return items


def _merged(x, y):
    (pa, ka), (pb, kb) = x, y
    if pa[1] is not None and abs(pa[1] - pb[0]) <= 1e-9 and _same_knives(ka, kb):
        return [[pa[0], pb[1]], ka]
    if abs(pa[0] - pb[0]) > 1e-9 or (pa[1] is None) != (pb[1] is None) or (
            pa[1] is not None and abs(pa[1] - pb[1]) > 1e-9):
        return None
    for i, k in enumerate(ka):
        for j, q in enumerate(kb):
            if _same_line(k, q) and k[4] != q[4] and _same_knives(ka[:i] + ka[i + 1:], kb[:j] + kb[j + 1:]):
                return [pa, ka[:i] + ka[i + 1:]]
    return None


def _same_line(k, q):
    return all(abs(a - b) <= 1e-6 for a, b in zip(k[:4], q[:4]))


def _same_knives(a, b):
    left = list(b)
    for k in a:
        m = next((q for q in left if _same_line(k, q) and k[4] == q[4]), None)
        if m is None:
            return False
        left.remove(m)
    return not left


def on_knife(m, k):
    """Is the mark m (where it is now) the mark of the cut through the notes k?"""
    b0, k0, db, dk, _ = k
    n = math.hypot(db, dk) or 1
    dx, dy = m.get("dir") or (db, dk)
    return (abs(db * (m["at"][1] - k0) - dk * (m["at"][0] - b0)) / n <= 1e-6 and
            abs(db * dy - dk * dx) <= 1e-6 * n * (math.hypot(dx, dy) or 1))


def fresh_marks(copies):
    """Copied pieces keep their cut marks, but no faint line goes from them to the pieces they were copied from (user):
    their marks get new ids (the same among the copies, so pieces copied together still pair up)."""
    import uuid
    new = {}
    for sh in copies:
        for m in (sh.get("cut") or {}).get("marks", []):
            m["id"] = new.setdefault(m["id"], uuid.uuid4().hex[:12])


def slice_in_two(sh, halves, a, b):
    """A custom shape (maybe a piece already) cut in two along a-b (beats, keys) by the Slice tool (slice.py made the
    halves): each half made a piece of the shape sh was cut from (or of sh), keeping the notes on its side. Its mark:
    the cut line's parts inside the shape."""
    import uuid
    from notes.engine import cached_path, cached_strokes
    from notes.slice import crossings, inside, side
    got = source(sh)
    if got:
        src, part, same_vel = got
        cut = sh["cut"]
        d = moved_by(sh)
        marks = [moved_mark(m, d) for m in cut.get("marks", [])]
        part = [[b0 + d[0], k0 + d[1], db, dk, sd] for b0, k0, db, dk, sd in part]  # (where its cuts are now)
        own = cut.get("own_vel") or cut.get("vel") is not None and not same_vel
        whole = dict(outline(src), **velocity(cut["whole"]), **velocity(src, GATE),  # (its gate as it makes notes)
                     **tools(cut["whole"]), **inner(src))
    else:
        part, marks, own = [], [], False
        whole = dict(outline(sh), **velocity(sh), **velocity(sh, GATE), **tools(sh))
        for h in halves:  # (the whole's glue / pages are done before the cut now)
            for k in WHOLE_TOOLS:
                h.pop(k, None)
    polys = [np.asarray(st, float) for st in cached_strokes(sh)]
    ss = sorted({round(s, 9) for p in polys for _, s in crossings(p, a, b, whole_line=True)})
    a, b = np.asarray(a, float), np.asarray(b, float)
    segs = [[list(a + (b - a) * s0), list(a + (b - a) * s1)] for s0, s1 in zip(ss, ss[1:])
            if s1 - s0 > 1e-9 and inside(polys, a + (b - a) * (s0 + s1) / 2)]
    if not segs:
        segs = [[list(a), list(b)]]
    mid = np.mean([np.mean(sg, axis=0) for sg in segs], axis=0)
    new = {"id": uuid.uuid4().hex[:12], "kind": "slice", "at": [float(mid[0]), float(mid[1])], "u": 0.0,
           "dir": [float(b[0] - a[0]), float(b[1] - a[1])], "segs": segs}
    for h in halves:
        sd = side([p for p in cached_path(h)], a, b) or 1
        mine = [m for m in marks if side([m["at"]], a, b) in (0, sd)]
        h["cut"] = {"whole": whole, "was": outline(h), "part": part + [[float(a[0]), float(a[1]), float(b[0] - a[0]),
                                                                       float(b[1] - a[1]), sd]],
                    "vel": None, "gate": velocity(h, GATE), "marks": mine + [new]}
        if own:
            h["cut"]["own_vel"] = True


def moved_mark(m, d):
    """A mark as it is now, its piece moved by d since it was cut."""
    out = dict(m, at=[m["at"][0] + d[0], m["at"][1] + d[1]])
    if m.get("segs"):
        out["segs"] = [[[x + d[0], y + d[1]] for x, y in sg] for sg in m["segs"]]
    return out


def keep_velocity(sh):
    """After a new piece got its velocities (joined.piece_velocity): they're the whole's, so its notes take the
    whole's own (exact) until they're changed (source)."""
    cut = sh.get("cut")
    if cut and not cut.get("own_vel"):
        cut["vel"] = velocity(sh)


def pack_wholes(shapes):
    """Shapes for a project file: the pieces of one shape point to ONE copy of it (user: the file shouldn't grow
    with every piece): cut["whole"] = {"ref": n} -> (shapes, the wholes)."""
    table, out = {}, []
    for sh in shapes:
        cut = sh.get("cut")
        if cut and isinstance(cut.get("whole"), dict):
            n = table.setdefault(json.dumps(cut["whole"], sort_keys=True), len(table))
            sh = dict(sh, cut=dict(cut, whole={"ref": n}))
        out.append(sh)
    return out, [json.loads(k) for k in table]


def unpack_wholes(sh, wholes):
    """A shape read from a project file with its piece's whole put back (pack_wholes)."""
    cut = sh.get("cut") if isinstance(sh, dict) else None
    if isinstance(cut, dict) and isinstance(cut.get("whole"), dict) and "ref" in cut["whole"]:
        n = cut["whole"]["ref"]
        whole = wholes[n] if isinstance(n, int) and 0 <= n < len(wholes) else None
        sh = dict(sh, cut=dict(cut, whole=whole)) if isinstance(whole, dict) else {k: v for k, v in sh.items()
                                                                                    if k != "cut"}
    return sh


def clean_cut(c):
    """sh["cut"] from a file, made valid (None: dropped, the piece is a shape of its own)."""
    from notes.engine import clean_shape
    from notes.tumour import LINE_KINDS

    def pt(p):
        return [float(p[0]), float(p[1])]
    try:
        whole, was = clean_shape(dict(c["whole"])), clean_shape(dict(c["was"]))
        # (a line kind's piece can be another line kind: a polyline's last two points are a line)
        if not whole or not was or (whole["kind"] == "custom") != (was["kind"] == "custom") or not (
                whole["kind"] in LINE_KINDS or whole["kind"] == "custom" and "notes" not in whole):
            return None
        if whole["kind"] == "custom":
            part = [[float(x) for x in h[:4]] + [1 if h[4] > 0 else -1] for h in c["part"]]
            if not part or any(len(h) != 5 for h in c["part"]):
                return None
        else:
            part = [float(c["part"][0]), None if c["part"][1] is None else float(c["part"][1])]
            knife = [[float(x) for x in h[:4]] + [1 if h[4] > 0 else -1] for h in c.get("knife") or []]
            if any(len(h) != 5 for h in c.get("knife") or []):
                return None
        marks = []
        for m in c.get("marks") or []:
            mk = {"id": str(m["id"]), "at": pt(m["at"]), "u": float(m.get("u", 0)),
                  "kind": "slice" if m.get("kind") == "slice" else "split"}
            if m.get("dir"):
                mk["dir"] = pt(m["dir"])
            if m.get("segs"):
                mk["segs"] = [[pt(a), pt(b)] for a, b in m["segs"]]
            marks.append(mk)
        snaps = {}
        for key, keys in (("vel", VELOCITY), ("gate", GATE)):  # (as the piece's own would come out of a file)
            v = c.get(key)
            snaps[key] = velocity(clean_shape(dict(c["was"], **v)) or {}, keys) if isinstance(v, dict) else None
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None
    out = {"whole": dict(outline(whole), **velocity(whole), **(velocity(whole, GATE) if whole["kind"] == "custom"
                                                               else {}), **tools(whole), **inner(whole)),
           "was": outline(was), "part": part, "vel": snaps["vel"], "marks": marks}
    if whole["kind"] == "custom":
        out["gate"] = snaps["gate"]
    elif knife:
        out["knife"] = knife
    if c.get("own_vel"):
        out["own_vel"] = True
    return out
