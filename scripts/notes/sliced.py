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
VELOCITY = ("vel0", "vel1", "vel_env")
BIG = 1 << 30  # a joined curve's piece k has the spots k * BIG + ...


def outline(sh):
    return {k: json.loads(json.dumps(sh[k])) for k in OUTLINE if k in sh}


def velocity(sh):
    return {k: json.loads(json.dumps(sh[k])) for k in VELOCITY if k in sh}


def moved_by(sh):
    """How far a sliced piece was moved since it was cut, (beats, keys); None when it isn't a piece any more (its
    outline changed) or never was."""
    cut = sh.get("cut")
    if not cut:
        return None
    was = cut["was"]
    if len(sh["pts"]) != len(was["pts"]) or any(sh.get(k) != was.get(k) for k in OUTLINE if k != "pts"):
        return None
    d = np.asarray(sh["pts"], float) - np.asarray(was["pts"], float)
    if not len(d) or np.abs(d - d[0]).max() > 1e-9:
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
    src = {k: v for k, v in sh.items() if k not in OUTLINE and k not in VELOCITY and k != "cut"}
    src.update(outline(moved(cut["whole"], d)))
    src.update(velocity(cut["whole"] if same_vel else sh))
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
        marks = [dict(m, at=[m["at"][0] + d[0], m["at"][1] + d[1]]) for m in cut.get("marks", [])]
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


def keep_velocity(sh):
    """After a new piece got its velocities (joined.piece_velocity): they're the whole's, so its notes take the
    whole's own (exact) until they're changed (source)."""
    cut = sh.get("cut")
    if cut and not cut.get("own_vel"):
        cut["vel"] = velocity(sh)


def clean_cut(c):
    """sh["cut"] from a file, made valid (None: dropped, the piece is a shape of its own)."""
    from notes.engine import clean_shape
    from notes.tumour import LINE_KINDS
    try:
        whole, was = clean_shape(dict(c["whole"])), clean_shape(dict(c["was"]))
        if not whole or not was or whole["kind"] not in LINE_KINDS or was["kind"] not in LINE_KINDS:
            return None
        part = [float(c["part"][0]), None if c["part"][1] is None else float(c["part"][1])]
        vel = c.get("vel")
        marks = []
        for m in c.get("marks") or []:
            mk = {"id": str(m["id"]), "at": [float(m["at"][0]), float(m["at"][1])], "u": float(m["u"]),
                  "kind": "slice" if m.get("kind") == "slice" else "split"}
            if m.get("dir"):
                mk["dir"] = [float(m["dir"][0]), float(m["dir"][1])]
            marks.append(mk)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None
    out = {"whole": dict(outline(whole), **velocity(whole)), "was": outline(was), "part": part,
           "vel": velocity(clean_shape(dict(was, **vel)) or {}) if isinstance(vel, dict) else None, "marks": marks}
    if c.get("own_vel"):
        out["own_vel"] = True
    return out
