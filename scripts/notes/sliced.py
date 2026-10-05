"""Sliced pieces: a line kind cut by Split here or the Slice tool keeps every note where it was (user, 2026-10-06).

Each piece remembers the shape it was cut from, sh["cut"]:
  whole  the shape it was cut from (its outline keys + velocities), where it was when this piece was made
  was    the piece's own outline keys then (moved since = all its points moved by the same amount)
  part   [from, to]: the spots on the whole's path (path_notes where) its notes start on; to None = the end
  vel    the piece's velocities then (unchanged: the whole's velocities are used, so they're exact), or None = its own
  marks  its cut ends, drawn while it's selected: {id, at (beat, key, where it was then), u (spot), kind "split" /
         "slice", dir (the Slice line's (beats, keys) direction)}; the other piece at that cut has the same id
A piece makes its notes as the whole did and keeps the ones starting on its part, so the pieces' notes together are
the whole's exactly (a note across a cut stays whole, in the piece it starts in); Colours count as in the whole.
Moving it (or a copy) keeps that; changing its outline (points, size, turn, flip, Straighten, tumours, a formula,
Last note) makes it a shape of its own (App.shapes_changed drops "cut"), and so does "Turn into a complete shape".
"""

import json

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


def keys_of(sh):
    return CUSTOM_OUTLINE if sh["kind"] == "custom" else OUTLINE


def outline(sh):
    return {k: json.loads(json.dumps(sh[k])) for k in keys_of(sh) if k in sh}


def velocity(sh, keys=VELOCITY):
    return {k: json.loads(json.dumps(sh[k])) for k in keys if k in sh}


def moved_by(sh):
    """How far a sliced piece was moved since it was cut, (beats, keys); None when it isn't a piece any more (its
    outline changed) or never was."""
    cut = sh.get("cut")
    if not cut:
        return None
    was = cut["was"]
    if (len(sh["pts"]) != len(was["pts"]) or was.get("kind") != sh["kind"]
            or any(sh.get(k) != was.get(k) for k in keys_of(sh) if k != "pts")):
        return None
    d = np.asarray(sh["pts"], float) - np.asarray(was["pts"], float)
    if not len(d) or np.abs(d - d[0]).max() > 1e-6:  # (a save file trims the last digits)
        return None
    return float(d[0, 0]), float(d[0, 1])


def moved(sh, d):
    """sh's points moved by d (beats, keys)."""
    return dict(sh, pts=[[b + d[0], p + d[1]] for b, p in sh["pts"]])


def source(sh):
    """A sliced piece -> (the shape its notes are made from: the whole, moved as far as the piece was, with the
    piece's other settings; its part; whether its velocities are the whole's), else None."""
    d = moved_by(sh)
    if d is None:
        return None
    cut = sh["cut"]
    same_vel = cut.get("vel") is not None and velocity(sh) == cut["vel"]
    own = keys_of(sh) + VELOCITY + (GATE if sh["kind"] == "custom" else ())
    src = {k: v for k, v in sh.items() if k not in own and k != "cut"}
    src.update(outline(moved(cut["whole"], d)))
    src.update(velocity(cut["whole"] if same_vel else sh))
    if sh["kind"] == "custom":
        same_gate = cut.get("gate") is not None and velocity(sh, GATE) == cut["gate"]
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
    """A custom piece's (notes, tracks): the whole's (make = engine._notes_tracks), those on its side of every cut
    (rows of Fill cut there, other notes by where they start: spam keeps the whole's grid, an Empty shape's cut side
    makes no notes, user); velocities over its own time when they were changed. None: not a piece any more."""
    got = source(sh)
    if got is None:
        return None
    src, halves, same_vel = got
    key = (json.dumps(src, sort_keys=True), ppq, keys)
    if key not in _wholes:
        if len(_wholes) > 8:
            _wholes.clear()
        _wholes[key] = make(src, ppq, keys)
    notes, tracks = _wholes[key]
    s, k = notes[:, 0].astype(float), notes[:, 2].astype(float)
    lo, hi = s.copy(), notes[:, 1].astype(float)
    split = src.get("fill") == "fill"
    keep = np.ones(len(notes), bool)
    mb, mk = moved_by(sh)  # (the cuts went along with it)
    for b0, k0, db, dk, sd in halves:
        b0, k0 = b0 + mb, k0 + mk
        # which side of the cut (beat, key) is on: db * (key - k0) - dk * (beat - b0) >= 0 is side 1, < 0 side -1
        # (a spot right on it goes to side 1 only); on a key row that's c0 + slope * tick
        c0 = db * (k - k0) + dk * b0
        slope = -dk / ppq
        if abs(slope) < 1e-15:  # (a flat cut: whole rows on one side)
            keep &= (c0 >= 0) if sd > 0 else (c0 < 0)
            continue
        if split:
            x = -c0 / slope  # the tick where the cut crosses the row's middle
            if (slope > 0) == (sd > 0):
                lo = np.maximum(lo, x)
            else:
                hi = np.minimum(hi, x)
        else:
            c = c0 + slope * s
            keep &= (c >= 0) if sd > 0 else (c < 0)
    if split:
        lo, hi = np.round(lo), np.round(hi)
        keep &= hi > lo
    notes = notes[keep].copy()
    notes[:, 0], notes[:, 1] = lo[keep], hi[keep]
    tracks = None if tracks is None else np.asarray(tracks)[keep]
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
        whole = dict(outline(src), **velocity(cut["whole"]))
    else:
        src, part, marks, own = sh, [0.0, None], [], False
        whole = dict(outline(sh), **velocity(sh))
    uc = spot_of(src, at, scale, part)
    mids = []
    for h in halves:
        path = cached_path(h)
        mids.append(spot_of(src, path[len(path) // 2], scale, part))
    new = dict(mark, id=uuid.uuid4().hex[:12], at=[float(at[0]), float(at[1])], u=uc)
    order = sorted(range(2), key=lambda i: mids[i])
    for i, (a, b) in zip(order, ((part[0], uc), (uc, part[1]))):
        h = halves[i]
        mine = [m for m in marks if m["u"] >= a - 1e-9 and (b is None or m["u"] <= b + 1e-9)]
        h["cut"] = {"whole": whole, "was": outline(h), "part": [a, b], "vel": None, "marks": mine + [new]}
        if own:
            h["cut"]["own_vel"] = True


def rejoined(olds, new):
    """Join of pieces of one shape that follow on from each other (and were moved together, if at all): all of them
    = that shape back as it was (with new's other settings: the join's), else new (the joined curve) made one bigger
    piece. None: not such pieces (a normal join)."""
    got = [source(sh) for sh in olds]
    if not all(got):
        return None
    wholes = [json.dumps(outline(g[0]), sort_keys=True) for g in got]
    if len(set(wholes)) > 1:
        return None
    order = sorted(range(len(olds)), key=lambda i: got[i][1][0])
    parts = [got[i][1] for i in order]
    if any(p[1] is None or abs(p[1] - q[0]) > 1e-9 for p, q in zip(parts, parts[1:])):
        return None
    same_vel = all(g[2] for g in got)
    src = got[0][0]
    if parts[0][0] == 0 and parts[-1][1] is None:  # all of it: the shape it was
        out = {k: v for k, v in new.items() if k not in OUTLINE and k != "cut" and not (same_vel and k in VELOCITY)}
        out.update(outline(src))
        if same_vel:
            out.update(velocity(src))
        return out
    ids = [m["id"] for sh in olds for m in sh["cut"].get("marks", [])]
    marks = []
    for sh in olds:
        d = moved_by(sh)
        marks += [dict(m, at=[m["at"][0] + d[0], m["at"][1] + d[1]]) for m in sh["cut"].get("marks", [])
                  if ids.count(m["id"]) == 1]  # (the cuts joined up again are gone)
    whole = dict(outline(src), **velocity(olds[0]["cut"]["whole"]))
    out = dict(new, cut={"whole": whole, "was": outline(new), "part": [parts[0][0], parts[-1][1]], "vel": None,
                         "marks": marks})
    if same_vel:
        out["cut"]["vel"] = velocity(new)
    else:
        out["cut"]["own_vel"] = True
    return out


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
        whole = dict(outline(src), **velocity(cut["whole"]), **velocity(src, GATE))  # (its gate as it makes notes)
    else:
        part, marks, own = [], [], False
        whole = dict(outline(sh), **velocity(sh), **velocity(sh, GATE))
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
        if not whole or not was or whole["kind"] != was["kind"] or not (
                whole["kind"] in LINE_KINDS or whole["kind"] == "custom" and "notes" not in whole):
            return None
        if whole["kind"] == "custom":
            part = [[float(x) for x in h[:4]] + [1 if h[4] > 0 else -1] for h in c["part"]]
            if not part or any(len(h) != 5 for h in c["part"]):
                return None
        else:
            part = [float(c["part"][0]), None if c["part"][1] is None else float(c["part"][1])]
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
                                                               else {})),
           "was": outline(was), "part": part, "vel": snaps["vel"], "marks": marks}
    if whole["kind"] == "custom":
        out["gate"] = snaps["gate"]
    if c.get("own_vel"):
        out["own_vel"] = True
    return out
