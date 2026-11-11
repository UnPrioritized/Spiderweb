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

import hashlib
import json
import math

import numpy as np

from notes.custom import CENTRE_KEYS, frame_to_bp, frame_to_uv, moved_at
from notes.paths import dedupe, dot_segment_notes, path_notes

# what makes a line kind's path (besides its points): changed = no longer the piece it was (end_dot too: "Last note"
# changed on a piece is about its own last note)
OUTLINE = ("kind", "pts", "k", "smooth", "tumour", "tumours", "gaps", "splits", "sharp", "sym", "shape", "pattern",
           "end_dot")
CUSTOM_OUTLINE = ("kind", "pts", "strokes", "areas", "round")  # a custom shape's (its strokes are in its frame)
FUNNEL_OUTLINE = ("kind", "pts", "starts")  # a funnel's (its curves are in "starts"; cut through its notes, knife_in_two)
VELOCITY = ("vel0", "vel1", "vel_env")
GATE = ("gate", "range")  # a custom piece's spam gate: the whole's until it's changed (slice shares out a Range)
BIG = 1 << 30  # a joined curve's piece k has the spots k * BIG + ...
WHOLE_TOOLS = ("glue", "fx")  # the whole's glue and note tool pages: done on the whole, then the pieces cut (user:
# slicing keeps the look; the piece's own glue / pages added later go on its part)


def keys_of(sh):
    return {"custom": CUSTOM_OUTLINE, "funnel": FUNNEL_OUTLINE}.get(sh["kind"], OUTLINE)


def snap_piece(sh):
    """A line cut through its notes while it had Notes by snap (knife_in_two marks it "sided"): drawn and picked on
    its side of the knife only, like a funnel piece, also once unticked (hunt 2026-10-28: it drew the whole line over
    the other piece); pages pieces keep the whole drawing (user 2026-10-06)."""
    from notes.tumour import LINE_KINDS
    cut = sh.get("cut") or {}
    return sh["kind"] in LINE_KINDS and bool(cut.get("knife")) and bool(cut.get("sided"))


def piece_knives(sh):
    """A funnel piece's Slice cuts where they are now [[b0, k0, db, dk, side]] (it keeps the whole funnel's notes
    on its side of them, knife_cut), else None (not a funnel piece, or its outline changed). Flipped / turned (a
    flip / turn step, steps_kept): the cuts flipped / turned along with its drawing."""
    cut = sh.get("cut")
    if not cut or not (sh["kind"] == "funnel" or snap_piece(sh)):
        return None
    made = sh
    if sh.get("fx"):
        from notes.engine import as_made
        made = as_made(sh)
    d = moved_by(made)
    if d is None:
        return None
    knives = [[b0 + d[0], k0 + d[1], db, dk, sd] for b0, k0, db, dk, sd in cut.get("knife") or []]
    return knives if close(made["pts"], sh["pts"]) else carried_knives(knives, made["pts"], sh["pts"])


def change_of(frm, to):
    """The change (matrix m, shift t: p -> m p + t) taking the points frm to the points to (a flip / turn moves every
    point the same way), or None when it can't be told (points all on one line)."""
    p = np.asarray(frm, float).reshape(-1, 2)
    q = np.asarray(to, float).reshape(-1, 2)
    a = np.column_stack([p, np.ones(len(p))])
    if len(p) != len(q) or len(p) < 3 or np.linalg.matrix_rank(a) < 3:
        return None
    x = np.linalg.lstsq(a, q, rcond=None)[0]
    return x[:2].T, x[2]


def carried_knives(knives, frm, to):
    """Knives on a drawing with the points frm, on the same drawing flipped / turned to the points to."""
    ch = change_of(frm, to)
    if ch is None:
        return knives
    m, t = ch
    flip = 1 if np.linalg.det(m) > 0 else -1  # (a flip swaps the sides)
    out = []
    for b0, k0, db, dk, sd in knives:
        o = m @ [b0, k0] + t
        v = m @ [db, dk]
        out.append([float(o[0]), float(o[1]), float(v[0]), float(v[1]), sd * flip])
    return out


def carried_mark(m, ch):
    """A cut mark (where it is now) flipped / turned by the change ch (change_of)."""
    mat, t = ch

    def pt(p):
        q = mat @ [p[0], p[1]] + t
        return [float(q[0]), float(q[1])]
    out = dict(m, at=pt(m["at"]))
    if m.get("dir"):
        v = mat @ m["dir"]
        out["dir"] = [float(v[0]), float(v[1])]
    if m.get("segs"):
        out["segs"] = [[pt(a), pt(b)] for a, b in m["segs"]]
    if m.get("hits"):
        out["hits"] = [pt(p) for p in m["hits"]]
    return out


def turned_piece(sh, frm, change):
    """A funnel piece flipped / turned as a piece (user: a flip made it the whole funnel again, two of them on top of
    each other): sh (its drawing changed already, from the points frm) gets the funnel it was cut from changed the same
    way (change(shape) does it to a copy), its cuts and marks along. Changes sh; False when it can't (not such a
    piece: it becomes a shape of its own as before)."""
    cut = sh.get("cut")
    if not cut or sh["kind"] != "funnel" or whole_made(cut):  # (pages / glue: a flip / turn step, steps_kept)
        return False
    d = moved_by(dict(sh, pts=frm))
    ch = change_of(frm, sh["pts"]) if d is not None else None
    if ch is None:
        return False
    old = dict(sh, pts=frm)
    same_vel = cut.get("vel") is not None and close(velocity(old), cut["vel"])
    whole = json.loads(json.dumps(moved(cut["whole"], d)))
    change(whole)
    cut["whole"] = whole
    cut["knife"] = carried_knives(piece_knives(old) or [], frm, sh["pts"])
    cut["marks"] = [carried_mark(moved_mark(m, d), ch) for m in cut.get("marks", [])]
    cut["was"] = outline(sh)
    if same_vel:
        cut["vel"] = velocity(sh)
    return True


def on_side(knives, b, p):
    """Is the spot (beat, key) on the kept side of every knife (knife_cut's sides; right on one counts)?"""
    return all(sd * (db * (p - k0) - dk * (b - b0)) >= -1e-9 for b0, k0, db, dk, sd in knives)


def clip_strokes(strokes, knives):
    """(beat, key) polylines with the parts past any knife left out (user: a funnel piece shows only its side of the
    funnel). Nothing left = the strokes as they were (the piece still has to be seen and picked)."""
    out = []
    for st in strokes:
        pieces = [[tuple(p) for p in st]]
        for b0, k0, db, dk, sd in knives:
            def f(p):
                return sd * (db * (p[1] - k0) - dk * (p[0] - b0))
            nxt = []
            for pc in pieces:
                run, w = [], None
                for p in pc:
                    v = f(p)
                    if w is not None and (v >= 0) != (w >= 0):  # (crossing it: the spot where)
                        t = w / (w - v)
                        x = (q[0] + (p[0] - q[0]) * t, q[1] + (p[1] - q[1]) * t)
                        if v >= 0:
                            run = [x]
                        else:
                            run.append(x)
                            if len(run) > 1:
                                nxt.append(run)
                            run = []
                    if v >= 0:
                        run.append(p)
                    q, w = p, v
                if len(run) > 1 or (len(run) == 1 and len(pc) == 1):
                    nxt.append(run)
            pieces = nxt
        out += pieces
    return [[list(p) for p in pc] for pc in out] if out else strokes


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
    Changes sh; its "cut" is dropped (hunt 2026-11-07: its cut group kept as "cut_of", so the other pieces keep their
    channels; a stirred / Per chord / run piece keeps the cut as "colour_cut": flipped only, it still takes the
    whole's colours, flipped_back)."""
    key = cut_group(sh)
    cut = sh.pop("cut", None)
    if key:
        sh["cut_of"] = key
    rc = sh.get("recolour") or {}
    if cut and (rc.get("stir") or rc.get("liquify") or rc.get("warp") or rc.get("by")):
        sh["colour_cut"] = cut
    whole = (cut or {}).get("whole") or {}
    if whole.get("cut"):  # (cut from a piece: its own whole's pages / glue first, then its own)
        whole = json.loads(json.dumps(whole))
        completed(whole)
    from notes.fx import chained, glue_groups, regrouped
    wfx = json.loads(json.dumps(whole.get("fx") or []))
    gl, own = whole.get("glue"), sh.get("glue")
    if gl is True and isinstance(own, list):  # (glue on all of it, done at its own place: as a box, hunt 11-07)
        from notes.glue import ALL
        gl = [list(ALL)]
    if gl and own is not True:  # (the boxes inside it as it was when cut, as shares of its own box)
        from notes.engine import cached_arrays
        from notes.glue import for_part, glue_box
        a, b = glue_box(np.concatenate(cached_arrays(whole))), glue_box(np.concatenate(cached_arrays(cut["was"])))
        groups = glue_groups(gl, wfx)
        if groups is None:  # (True: glues all of it)
            gl = for_part(gl, a, b)
        else:  # (each glue place keeps its boxes: hunt 2026-11-07, the whole's glue moved after its pages)
            gl, wfx = regrouped([for_part(g, a, b) or [] for g in groups], wfx)
            wfx = wfx or []
    elif own is True:
        gl = None
    if wfx:
        n = len(gl) if isinstance(gl, list) else 0
        fx = chained(wfx, sh.get("fx"), bool(own), n if isinstance(own, list) else 0)
        if fx:
            sh["fx"] = fx
    if gl:
        sh["glue"] = True if gl is True or own is True else gl + (own or [])
        if sh["glue"] is True and sh.get("fx"):  # (glue on everything: one place, the last (the piece's), glues all)
            first = [k for k, st in enumerate(sh["fx"]) if st["tool"] == "glue"][-1:]
            sh["fx"] = [{"tool": "glue"} if [k] == first else st for k, st in enumerate(sh["fx"])
                        if st["tool"] != "glue" or [k] == first]


def whole_made(cut):
    """Do a piece's notes come from the glue / note tool pages of the shape it was cut from (or that one is a piece
    itself)?"""
    whole = cut.get("whole") or {}
    return bool(whole.get("fx") or whole.get("glue") or whole.get("cut"))


def steps_kept(sh):
    """sh is a piece whose notes come from its whole's glue / pages or its Ripples rings (the piece keeps the
    whole's fill): flipping / turning it is a flip / turn step on its notes, like on a shape with pages (user,
    2026-10-07: it became a shape of its own, all the whole's notes back), and it stays a piece (its notes are made
    from its drawing turned / flipped back: engine.as_made)."""
    return bool(sh.get("cut")) and (whole_made(sh["cut"]) or bool(sh.get("ripples")))


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
    if sh.get("range") and cut.get("gate") is not None and not close(velocity(sh, GATE), cut["gate"]):
        return None  # (its gate Range changed: a Range runs across the shape, so over the whole's outline it'd give
    # the wrong gates at each spot; made from the piece itself, like an outline change; hunt 2026-11-01)
    d = np.asarray(sh["pts"], float) - np.asarray(was["pts"], float)
    if not len(d) or np.abs(d - d[0]).max() > 1e-6:  # (a save file trims the last digits)
        return None
    return float(d[0, 0]), float(d[0, 1])


def moved(sh, d):
    """sh's points moved by d (beats, keys)."""
    return dict(sh, pts=[[b + d[0], p + d[1]] for b, p in sh["pts"]])


def made_at_cut(sh, ppq, keys, make):
    """A moved piece's (notes, tracks) (user, 2026-10-18: a sliced shape keeps its notes however it's moved): made
    where it was cut, then moved along (Strum, Claw, Chop, Colours by time, the spam grid go by where the notes sit
    in the song, and a Strum by the notes the piano roll's edges leave), cut off only at the edges where it is now.
    make = engine's notes of a shape as it is. None: not a moved piece."""
    d = moved_by(sh)
    if d is None or abs(d[0]) < 1e-9 and abs(d[1]) < 1e-9:
        return None
    from notes.engine import cached_arrays, later_unit
    from notes.paths import rows_up_to
    pts = np.concatenate(cached_arrays(sh["cut"]["whole"]))  # (the whole where it was cut: lifted onto the keys
    ks = pts[:, 1]                                             # and after beat 0, so none of its notes go missing
    up = max(0, math.ceil(-float(ks.min()) - 1e-9))            # there, and note tools never see a time before 0)
    t0 = float(pts[:, 0].min())
    unit = later_unit(sh["cut"]["whole"], ppq) if t0 < 0 else 0
    later = float((math.ceil(-t0 / unit - 1e-9) + 1) * unit) if t0 < 0 else 0.0
    room = max(keys, math.ceil(float(ks.max()) + 1e-9) + up + 1)
    if abs(d[0] - later) < 1e-9 and abs(d[1] - up) < 1e-9:  # (lifted already: made as it is)
        return None
    with rows_up_to(room - 1):
        notes, tracks = make(moved(sh, (-d[0] + later, -d[1] + up)), ppq, room)
    notes = notes.copy()
    notes[:, :2] += round((d[0] - later) * ppq)
    notes[:, 2] += round(d[1]) - up
    if sh.get("_free"):  # (not cut at the edges: Recolour's box, engine.free_box)
        return notes, tracks
    keep = (notes[:, 2] >= 0) & (notes[:, 2] < keys) & (notes[:, 1] > 0)
    notes = notes[keep]
    notes[:, 0] = np.maximum(notes[:, 0], 0)
    return notes, None if tracks is None else np.asarray(tracks)[keep]


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
    if "_whole" in src:  # (the piece's Sustain / Gate extend / subtract ordered before the whole's pages: run on the
        # whole, in that order, by its own runner; the piece leaves them out: steps.in_whole)
        src.update(json.loads(json.dumps(src.pop("_whole"))))
        src.pop("_run", None)
        src.pop("_noglue0", None)
    if sh["kind"] == "custom":
        same_gate = cut.get("gate") is not None and close(velocity(sh, GATE), cut["gate"])
        src.update(velocity(cut["whole"] if same_gate else sh, GATE))
        for key in CENTRE_KEYS:  # (Nested's / Tiles' / Ripples' centre points kept in the piece's box: the same
            to_uv = frame_to_uv(src["pts"]) if (src.get(key) or {}).get("at") else None  # spots in the whole's,
            if to_uv:  # which the notes are made from)
                to_bp = frame_to_bp(sh["pts"])
                src[key] = moved_at(src[key], lambda u, v: to_uv(*to_bp(u, v)))
    return src, cut["part"], same_vel


def flipped_back(sh, side, up):
    """A recoloured piece made a shape of its own by a flip (sh["colour_cut"] = its cut, completed): sh as the piece
    it was, unflipped where it sits now (side / up: flipped sideways / upside down since the cut), so
    engine.stirred_piece can still look its colours up in the whole (hunt 2026-11-07). None: its outline changed in
    another way."""
    cut = sh.get("colour_cut")
    if not cut or not side and not up:
        return None
    was = np.asarray(cut["was"]["pts"], float)
    pts = np.asarray(sh["pts"], float)
    if was.shape != pts.shape or not len(pts):
        return None
    back = was.copy()
    for ax, flip in ((0, side), (1, up)):
        rel = pts[:, ax] + was[:, ax] if flip else pts[:, ax] - was[:, ax]
        if np.abs(rel - rel[0]).max() > 1e-6:
            return None
        back[:, ax] = was[:, ax] + (pts[:, ax].min() - was[:, ax].min())  # (unflipped on the same spot)
    out = dict(sh, cut=cut, pts=back.tolist())
    out.pop("colour_cut", None)
    return out if moved_by(out) is not None else None


def cut_group(sh):
    """A sliced piece -> a key its fellow pieces share (cut from one whole), else None: with "Overlapping shapes
    keep their colours" the pieces move to other channels as one shape, like a paste's pieces. A piece turned into
    plain notes keeps its key ("cut_of"), so the pieces left stay on its channels (sweep 2026-11-06: a Lasso turning
    one piece plain sent the other back to its first channels)."""
    cut = sh.get("cut")
    if not cut or not cut.get("whole"):
        return sh.get("cut_of")
    if cut.get("id"):  # (the cut's own key: copies of one drawing cut together stay apart, sweep 69994088)
        return cut["id"]
    whole = cut["whole"]
    got = _groups.get(id(whole))  # (pieces share one whole: worked out once, not per piece per redraw)
    if got is None or got[0] is not whole:
        if len(_groups) > 256:
            _groups.clear()
        key = hashlib.sha1(json.dumps(whole, sort_keys=True, default=str).encode()).hexdigest()[:16]
        got = _groups[id(whole)] = (whole, key)
    return got[1]


_groups = {}  # id(whole) -> (whole, its key): cut_group's


def new_cut_id():
    import uuid
    return uuid.uuid4().hex[:16]


def keyed_pieces(old, parts):
    """Pieces cut from shape old share ONE key (cut["id"]): old's own group when it's a piece already (its other
    pieces keep theirs), else a new one. Keyed by the drawing alone, exact copies of one shape (Ctrl+D) cut together
    became one shape: their stacked notes merged into one (sweep 2026-11-12, seed 69994088)."""
    key = cut_group(old) or new_cut_id()
    for p in parts:
        if isinstance(p.get("cut"), dict):
            p["cut"] = dict(p["cut"], id=key)


def fresh_cut_ids(copies):
    """Copied / duplicated pieces: a new key per group copied (pieces copied together stay together), so a copy is a
    shape of its own, not one with the pieces it was copied from (keep colours: editing one changed the other's note
    lengths, sweep 2026-11-11)."""
    fresh = {}
    for sh in copies:
        if isinstance(sh.get("cut"), dict) and sh["cut"].get("whole"):
            key = cut_group(sh)
            sh["cut"] = dict(sh["cut"], id=fresh.setdefault(key, new_cut_id()))
        elif sh.get("cut_of"):
            sh["cut_of"] = fresh.setdefault(sh["cut_of"], new_cut_id())


def paths_of(sh):
    """The paths a line kind's notes are made from, each first to last point in time (engine.kind_notes_tracks)."""
    from notes.engine import cached_arrays
    arrs = cached_arrays(sh)
    arrs = [dedupe(np.concatenate(arrs))] if len(arrs) == 1 else [dedupe(a) for a in arrs]
    return [a[::-1] if len(a) and a[-1, 0] < a[0, 0] else a for a in arrs]


def spotted_notes(sh, ppq, end_dot):
    """A line kind's (start, end, key) notes, as engine.kind_notes_tracks makes them, and each one's spot."""
    paths = paths_of(sh)
    if len(paths) > 1:  # a joined curve's pieces: each like a line
        got = [path_notes(a * [ppq, 1], end_dot, where=True) for a in paths]
        return (np.concatenate([np.asarray(n, np.int64).reshape(-1, 3) for n, _ in got]),
                np.concatenate([u + k * BIG for k, (_, u) in enumerate(got)]))
    path = paths[0] * [ppq, 1]
    if end_dot and sh["kind"] == "poly" and len(path) > 2 and not (sh.get("shape") or sh.get("pattern")):
        return dot_segment_notes(path, where=True)
    return path_notes(path, end_dot, where=True)


def with_whole_velocity(src, sh):
    """source()'s src for piece sh with the velocities the piece had when it was cut (the whole's)."""
    out = {k: v for k, v in src.items() if k not in VELOCITY}
    out.update(velocity(sh["cut"]["whole"]))
    return out


def own_velocities(sh, made, frac):
    """A piece with velocities of its own: made = its notes' velocities as the whole's glue / pages left them (made
    from the piece's own line), frac = each note's start over the piece's own time (0..1). The plain line is used
    only where it differs from the line the piece had when cut (drawn over in the velocity pane, typed in the panel);
    elsewhere the pages' velocities stay (user 2026-11-02: a velocity line over part of a sliced Chop piece reset
    every Chop piece's velocity)."""
    from notes.envelope import env_values, velocity_env
    env = velocity_env(sh)
    vals = env_values(env, frac)
    was = sh["cut"].get("vel")
    if not was or "vel0" not in was:
        return np.clip(np.round(np.round(vals, 6)), 1, 127).astype(np.int64)
    changed = np.abs(vals - env_values(velocity_env(was), frac)) > 1e-6
    return np.where(changed, np.clip(np.round(np.round(vals, 6)), 1, 127), made).astype(np.int64)


def piece_notes(sh, ppq, keys, make):
    """A custom piece's (notes, tracks): the whole's (make = engine.kind_notes_tracks), cut at every cut like a knife went
    through them (user: a spam note across it leaves a sliver; spam keeps the whole's grid, an Empty shape's cut side
    makes no notes), the parts on its side kept; velocities over its own time when they were changed. None: not a
    piece any more. (Also a line piece cut from a piece with pages of its own: its whole's notes, its knife cuts.)"""
    got = source(sh)
    if got is None:
        return None
    src, halves, same_vel = got
    if sh["kind"] != "custom":
        halves = sh["cut"].get("knife") or []
    # (a plain drawn piece with a velocity of its own: the whole made with the velocities it had when cut, shared by
    # every piece. Its own line only sets the last column, which own_velocities replaces below anyway; made with each
    # piece's own line, 180 pieces under one velocity line made the whole 180 times: 4.7 s to open, 2026-11-08)
    plain_vel = not same_vel and sh["kind"] == "custom" and not any(src.get(k) for k in VELOCITY_LATE)
    if plain_vel:
        src = with_whole_velocity(src, sh)
    key = (json.dumps(src, sort_keys=True), ppq, keys)
    whole = _wholes.get(key)  # (got once: the notes may be made in a background thread too, see App.notes_made)
    if whole is None:
        if len(_wholes) > 8:
            _wholes.clear()
        whole = _wholes[key] = make(src, ppq, keys)
    notes, tracks = whole_cut(whole, halves, moved_by(sh), ppq)  # (the cuts went along with it)
    if sh["cut"].get("mould"):  # (cut with a shape, mould.py)
        from notes.mould import mould_cut
        notes, tracks = mould_cut(notes, tracks, sh["cut"]["mould"], moved_by(sh), ppq)
    if not same_vel and len(notes):  # (its own velocities: over its own time, as a shape of its own)
        from notes.engine import cached_arrays
        t = np.concatenate(cached_arrays(sh))[:, 0] * ppq
        frac = np.clip((notes[:, 0] - t.min()) / max(t.max() - t.min(), 1e-9), 0, 1)
        made = notes[:, 3]
        if plain_vel:  # (made from the whole as it was when cut already)
            notes[:, 3] = own_velocities(sh, made, frac)
            return notes, tracks
        # (its notes as made with the velocities it had when cut, the whole's (with pages: as they left them); src
        # holds the piece's own line, stretched over the whole's time: partial sweep 11-02)
        was_src = with_whole_velocity(src, sh)
        key = (json.dumps(was_src, sort_keys=True), ppq, keys)
        if key not in _wholes:
            if len(_wholes) > 8:
                _wholes.clear()
            _wholes[key] = make(was_src, ppq, keys)
        was = whole_cut(_wholes[key], halves, moved_by(sh), ppq)[0]
        if sh["cut"].get("mould"):
            from notes.mould import mould_cut
            was = mould_cut(was, None, sh["cut"]["mould"], moved_by(sh), ppq)[0]
        if was.shape == notes.shape and (was[:, :3] == notes[:, :3]).all():
            made = was[:, 3]
        notes[:, 3] = own_velocities(sh, made, frac)
    return notes, tracks


_wholes = {}  # the whole shape's notes, shared by its pieces
# (settings that use a note's velocity after it's made: a piece with these makes its whole with its own line)
VELOCITY_LATE = ("fx", "glue", "hz", "sustain", "subtract", "extend", "strips", "recolour")
_by_start = {}  # id(a whole's notes) -> (them, order by start, starts in that order, longest note, lowest, top key)
WINDOW_LEAST = 4096  # (fewer notes: knife_cut on them all)


def whole_cut(whole, halves, d, ppq):
    """knife_cut of a whole's (notes, tracks) for one piece, looking only at the notes that can be on its side of its
    cuts: the whole's notes sorted by start once, each cut's tick range over the whole's keys bounds the starts (a
    piece's notes made in time ~ its own notes, not the whole's: 720 pieces of a 640k-note drawing). Exactly
    knife_cut's notes (a superset of rows goes in, in their order)."""
    notes, tracks = whole
    if len(notes) < WINDOW_LEAST or not halves:
        return knife_cut(notes, tracks, halves, d, ppq)
    got = _by_start.get(id(notes))
    if got is None or got[0] is not notes:
        if len(_by_start) > 16:
            _by_start.clear()
        order = np.argsort(notes[:, 0], kind="stable")
        starts = notes[order, 0]
        got = _by_start[id(notes)] = (notes, order, starts, int((notes[:, 1] - notes[:, 0]).max()),
                                     int(notes[:, 2].min()), int(notes[:, 2].max()))
    _, order, starts, longest, k_lo, k_hi = got
    left, right = -np.inf, np.inf  # (kept notes end after left, start before right; ticks)
    mb, mk = d
    for b0, k0, db, dk, sd in halves:
        slope = -dk / ppq
        if abs(slope) < 1e-15:
            continue
        b0, k0 = b0 + mb, k0 + mk
        xs = [-(db * (kk - k0) + dk * b0) / slope for kk in (k_lo, k_hi)]  # (the cut's tick on the lowest / top row)
        if (slope > 0) == (sd > 0):
            left = max(left, min(xs))
        else:
            right = min(right, max(xs))
    if left == -np.inf and right == np.inf:
        return knife_cut(notes, tracks, halves, d, ppq)
    # (whole ticks searched: a float made NumPy copy every start to floats each time)
    if not (math.isfinite(left) or left == -np.inf) or not (math.isfinite(right) or right == np.inf):
        return knife_cut(notes, tracks, halves, d, ppq)  # (a NaN: no window)
    far = 2 ** 62
    a = 0 if left == -np.inf else int(np.searchsorted(
        starts, min(far, max(-far, math.floor(left) - longest - 2)), side="left"))
    b = len(starts) if right == np.inf else int(np.searchsorted(
        starts, min(far, max(-far, math.ceil(right) + 2)), side="right"))
    if b - a > len(notes) // 2:
        return knife_cut(notes, tracks, halves, d, ppq)
    rows = np.sort(order[a:b])
    return knife_cut(notes[rows], None if tracks is None else np.asarray(tracks)[rows], halves, d, ppq)


KNIFE_SAMPLE = 256  # (notes looked at to put a piece's cuts tightest first)


def knife_order(k, lo, hi, halves, d, ppq):
    """The halves' order for knife_cut: those keeping fewest of a spread-out sample of the notes first (the result
    doesn't depend on the order; a piece of a shape sliced 180 times carries ~180 cuts, and only its own last two
    keep it small: cutting all notes 180 times made opening such a project take 17 s, 2026-11-08)."""
    step = max(1, len(k) // KNIFE_SAMPLE)
    ks, ls, hs = k[::step], lo[::step], hi[::step]
    h = np.asarray(halves, float).reshape(-1, 5)
    b0, k0, db, dk, sd = h[:, 0] + d[0], h[:, 1] + d[1], h[:, 2], h[:, 3], h[:, 4]
    c0 = db[:, None] * (ks[None, :] - k0[:, None]) + (dk * b0)[:, None]
    slope = -dk / ppq
    flat = np.abs(slope) < 1e-15
    with np.errstate(divide="ignore", invalid="ignore"):
        x = -c0 / np.where(flat, 1.0, slope)[:, None]
    up = ((slope > 0) == (sd > 0))[:, None]
    kept = np.where(flat[:, None], np.where((sd > 0)[:, None], c0 >= 0, c0 < 0),
                    np.where(up, hs[None, :] > np.maximum(ls[None, :], x), np.minimum(hs[None, :], x) > ls[None, :]))
    return np.argsort(kept.sum(axis=1), kind="stable")


def knife_free(k, lo, hi, halves, d, ppq):
    """Which halves change none of these notes (knife_cut can leave them out: same result): a cut whose tick on the
    notes' lowest and top rows is clearly before every start (keeping the later side) / after every end (keeping the
    earlier side); a flat one with every row on its kept side. A piece of a shape sliced 720 times carries ~720 cuts
    of which ~2 touch its own notes."""
    h = np.asarray(halves, float).reshape(-1, 5)
    b0, k0, db, dk, sd = h[:, 0] + d[0], h[:, 1] + d[1], h[:, 2], h[:, 3], h[:, 4]
    k_lo, k_hi, lo_min, hi_max = k.min(), k.max(), lo.min(), hi.max()
    c_lo = db * (k_lo - k0) + dk * b0  # (c0 on the lowest / top row)
    c_hi = db * (k_hi - k0) + dk * b0
    slope = -dk / ppq
    flat = np.abs(slope) < 1e-15
    room = 1e-6 * (1 + np.abs(c_lo) + np.abs(c_hi))
    flat_free = np.where(sd > 0, np.minimum(c_lo, c_hi) >= room, np.maximum(c_lo, c_hi) < -room)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(flat, 1.0, slope)
        x_lo, x_hi = -c_lo / s, -c_hi / s
    up = (slope > 0) == (sd > 0)
    edge = 1e-6 * (1 + abs(lo_min) + abs(hi_max))
    cut_free = np.where(up, np.maximum(x_lo, x_hi) < lo_min - edge, np.minimum(x_lo, x_hi) > hi_max + edge)
    return np.where(flat, flat_free, cut_free)


REACH_KEYS = 8192  # (needed_halves compares cuts over keys -REACH_KEYS .. REACH_KEYS: far past any note row)
LEAST_GAP = 1e-6  # beats (x (1 + how far out)): a cut counts as inside another only this far inside on every key


def needed_halves(halves):
    """A custom piece's cuts [b0, k0, db, dk, side] without the ones another cut of the piece makes useless: on every
    key row (any key a note can have) a slanted cut keeps the notes after / before the tick where it crosses the row;
    one keeping strictly less on every row (the cut to its right keeping the later side, say) leaves nothing for it
    to do: knife_cut gives exactly the same notes without it (it only ever takes the largest start / smallest end).
    Slicing a drawing piece by piece kept every earlier cut on every piece (720 pieces = 720 cuts each: slow to
    slice / open / undo, files growing with pieces x pieces, 2026-11-09); now ~2. Flat cuts are always kept. Order
    kept."""
    if len(halves) < 2:
        return halves
    h = np.asarray(halves, float).reshape(-1, 5)
    b0, k0, db, dk, sd = h.T
    slanted = np.abs(dk) > 1e-6
    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.where(slanted, db / np.where(slanted, dk, 1.0), 0.0)
    ends = [b0 + tilt * (k - k0) for k in (-REACH_KEYS, REACH_KEYS)]  # (each cut's beat on the far rows)
    later = (dk < 0) == (sd > 0)  # (keeps the later side: knife_cut raises starts)
    drop = np.zeros(len(h), bool)
    for want in (True, False):
        idx = np.flatnonzero(slanted & (later == want))
        if len(idx) < 2:
            continue
        x, y = (ends[0][idx], ends[1][idx]) if want else (-ends[0][idx], -ends[1][idx])  # (earlier side: mirrored)
        # (useless: another one at least LEAST_GAP further in on both far rows, so on every row between)
        gap = LEAST_GAP * (1 + np.abs(x) + np.abs(y))  # (far past float noise however far out the rows reach)
        o = np.argsort(x, kind="stable")
        xs, ys = x[o], y[o]
        best = np.maximum.accumulate(ys[::-1])[::-1]  # (the furthest in on the top row among those from here on)
        at = np.searchsorted(xs, x + gap, side="left")
        has = at < len(xs)
        drop[idx[has]] = best[np.minimum(at[has], len(xs) - 1)] >= y[has] + gap[has]
    if not drop.any():
        return halves
    return [c for c, gone in zip(halves, drop) if not gone]


def knife_cut(notes, tracks, halves, d, ppq):
    """Notes (start, end, key, any other columns) and tracks cut like a knife went through them: each half
    [b0, k0, db, dk, side] keeps what's on its side of the line through (b0, k0) going (db, dk) (beats, keys; moved
    by d since), a note across it cut in two there. Many halves: tightest first, notes already gone dropped as it
    goes (a note with end <= start never comes back: later halves only raise starts / lower ends; same result)."""
    k = notes[:, 2].astype(float)
    lo, hi = notes[:, 0].astype(float), notes[:, 1].astype(float)
    keep = np.ones(len(notes), bool)
    mb, mk = d
    order = range(len(halves))
    where = None  # (rows still in play, when dropped as it goes)
    if len(halves) > 4 and len(notes):
        if len(notes) > 4 * KNIFE_SAMPLE:
            order = knife_order(k, lo, hi, halves, d, ppq)
            where = np.arange(len(notes))
        free = knife_free(k, lo, hi, halves, d, ppq)
        order = [j for j in order if not free[j]]
    for j in order:
        b0, k0, db, dk, sd = halves[j]
        b0, k0 = b0 + mb, k0 + mk
        # which side of the cut (beat, key) is on: db * (key - k0) - dk * (beat - b0) >= 0 is side 1, < 0 side -1
        # (a spot right on it goes to side 1 only); on a key row that's c0 + slope * tick
        c0 = db * (k - k0) + dk * b0
        slope = -dk / ppq
        if abs(slope) < 1e-15:  # (a flat cut: whole rows on one side)
            keep &= (c0 >= 0) if sd > 0 else (c0 < 0)
        else:
            x = -c0 / slope  # the tick where the cut crosses the row's middle
            if (slope > 0) == (sd > 0):
                lo = np.maximum(lo, x)
            else:
                hi = np.minimum(hi, x)
        if where is not None:
            live = keep & (hi > lo)
            if live.sum() * 4 < len(live) * 3:  # (a quarter gone: drop them before the next cuts)
                k, lo, hi, where = k[live], lo[live], hi[live], where[live]
                keep = np.ones(len(where), bool)
    lo, hi = np.round(lo), np.round(hi)
    keep &= hi > lo
    if where is not None:
        rows = where[keep]
        notes = notes[rows].copy()
        notes[:, 0], notes[:, 1] = lo[keep], hi[keep]
        return notes, None if tracks is None else np.asarray(tracks)[rows]
    notes = notes[keep].copy()
    notes[:, 0], notes[:, 1] = lo[keep], hi[keep]
    return notes, None if tracks is None else np.asarray(tracks)[keep]


def tooled(sh):
    """Do sh's notes come from glue / note tool pages or a Ripples fill (its own, or its whole's for a piece)? Then
    the Slice tool cuts its notes, not its drawing (user, 2026-10-06: they can sit far off the drawing; Ripples: its
    rings grow past it, and half a drawn heart would grow other rings). Notes by snap too (user 2026-10-28: a knife
    through a staircase cuts its notes where it goes, slanted or flat; each piece drawn on its side only)."""
    def notes_apart(s):
        return bool(s.get("fx") or s.get("glue") or s.get("ripples") or s.get("by_snap"))
    if notes_apart(sh):
        return True
    got = source(sh) if sh.get("cut") else None
    return bool(got and notes_apart(got[0]))


def notes_across(notes, a, b, ppq):
    """Does the knife a-b (beats, keys) cut the notes: it touches them (on some key row inside a-b it goes through a
    note or between the row's notes), every note the endless line through it goes through is inside a-b (user: only
    the notes it really cuts; a row it passes between notes elsewhere just ends up on its side), and notes are left on
    both sides? -> the stretch of it over the notes it touches [[a', b']] (its mark), else None. "crossed": it
    touched some notes (if it didn't cut)."""
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
        on = (s >= -1e-9) & (s <= 1 + 1e-9)
        between = (x > first) & (x < last)  # (notes of the row on both sides)
        tick = np.round(x * ppq)[at]
        through = np.zeros(len(keys), bool)  # (rows where it goes through a note: knife_cut cuts it in two)
        through[at[(notes[:, 0] < tick) & (notes[:, 1] > tick)]] = True
        crossed = bool((between & on).any())
        if not crossed or (through & ~on).any():
            return None, crossed
        s = s[between & on]
        half = 0.5 / abs(dk)  # (half a key row either side)
        s0, s1 = max(0.0, s.min() - half), min(1.0, s.max() + half)
    if not all(len(knife_cut(notes, None, [[a[0], a[1], db, dk, sd]], (0, 0), ppq)[0]) for sd in (1, -1)):
        return None, crossed
    return [[list(a + (b - a) * s0), list(a + (b - a) * s1)]], True


def knife_hits(notes, a, b, ppq):
    """Where the knife a-b goes across a line's notes (beats, keys): the middle of each run of key rows next to each
    other where it goes through a note, none: the row where it passes nearest one (a flat knife: where the notes of the
    rows beside it meet). Its marks go there, short like a cut line's (user, 2026-10-07: the stretch over every row it
    passed between notes reached far past them)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    db, dk = b - a
    if not len(notes) or abs(db) + abs(dk) < 1e-12:
        return []
    t0, t1 = notes[:, 0] / ppq, notes[:, 1] / ppq
    if abs(dk) < 1e-12:
        lo, hi = sorted((a[0], b[0]))
        near = (np.abs(notes[:, 2] - a[1]) <= 1) & (t1 > lo) & (t0 < hi)
        below, above = near & (notes[:, 2] < a[1]), near & (notes[:, 2] > a[1])
        if not below.any() or not above.any():
            return []
        ends = np.r_[t0[below], t1[below]]
        other = np.sort(np.r_[t0[above], t1[above]])
        j = np.clip(np.searchsorted(other, ends), 1, len(other) - 1)
        pick = np.where(np.abs(other[j] - ends) < np.abs(other[j - 1] - ends), other[j], other[j - 1])
        n = int(np.argmin(np.abs(pick - ends)))
        x = min(max((ends[n] + pick[n]) / 2, lo), hi)
        return [[float(x), float(a[1])]]
    keys, at = np.unique(notes[:, 2], return_inverse=True)
    s = (keys - a[1]) / dk
    x = a[0] + db * s
    on = (s >= -1e-9) & (s <= 1 + 1e-9)
    far = np.full(len(keys), np.inf)  # (how far it passes from the row's nearest note, in beats; 0 = through one)
    np.minimum.at(far, at, np.maximum(np.maximum(t0 - x[at], x[at] - t1), 0))
    far[~on] = np.inf
    if not np.isfinite(far).any():
        return []
    hit = far <= 1e-9
    if not hit.any():
        hit = far == far.min()
    rows = np.flatnonzero(hit)
    runs = np.split(rows, np.flatnonzero(np.diff(keys[rows]) > 1) + 1)
    return [[float(a[0] + db * s[r].mean()), float(a[1] + dk * s[r].mean())] for r in runs]


def knife_in_two(sh, halves, a, b, segs, hits=()):
    """sh (a line kind or custom shape with pages / glue, maybe a piece already) cut by the Slice tool through its
    NOTES along a-b: halves = two copies of it, each made a piece keeping the whole's notes on its side; their drawing
    stays the whole's. segs: the knife's stretch over the notes (the mark; a line's: drawn short at each of hits,
    knife_hits)."""
    import uuid
    custom = sh["kind"] == "custom"
    got = source(sh)
    mould = None  # (cut with a shape before, kept: mould.py)
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
        if cut.get("mould"):
            from notes.mould import moved_mould
            mould = moved_mould(cut["mould"], d)
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
    if hits and not custom:
        new.update(at=list(hits[0]), hits=[list(p) for p in hits])
    for h, sd in zip(halves, (1, -1)):
        mine = [m for m in marks if knife_side(m["at"], a, b) in (0, sd)]
        knife = knives + [[float(a[0]), float(a[1]), float(b[0] - a[0]), float(b[1] - a[1]), sd]]
        h["cut"] = {"whole": whole, "was": outline(h), "part": needed_halves(knife) if custom else part, "vel": None,
                    "marks": mine + [new]}
        if custom:
            h["cut"]["gate"] = velocity(h, GATE)
        else:
            h["cut"]["knife"] = knife
            if sh.get("by_snap") or (sh.get("cut") or {}).get("sided"):
                h["cut"]["sided"] = True  # (drawn on its side only: snap_piece)
        if mould:
            h["cut"]["mould"] = json.loads(json.dumps(mould))
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
    from notes.engine import kind_notes_tracks
    got = source(sh)
    if got is None:
        return None
    text = json.dumps(got[0], sort_keys=True)
    # (a copy of the list: the background notes thread may add one meanwhile, audit)
    whole = next((v[0] for k, v in list(_wholes.items()) if k[0] == text and k[1] == ppq), None)
    if whole is None:
        whole = kind_notes_tracks(got[0], ppq, 256)[0]
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


def beat_on(a, r):
    """The beat at spot r (point index + share) along path a."""
    if len(a) < 2:
        return a[0][0] if len(a) else 0.0
    i = max(0, min(int(r), len(a) - 2))
    return a[i][0] + (a[i + 1][0] - a[i][0]) * (r - i)


def cut_through(whole, raw, spots, tracks, part, ppq, came=None):
    """A line piece's notes (the whole's, before its part is kept): the note sounding at each of the piece's cuts is
    cut in two there (user: slicing adds a note at the cut), the second half starting on the cut's spot, so the piece
    before keeps the first half and the piece after starts with the second (same key and colour).
    came: "Notes by snap" steps, the line note each came from (start, end): the steps of the line note sounding at
    the cut that start after it go to the piece after (they all share that note's spot); a step held on its own is
    cut in two where the knife goes through it (user 2026-10-28: "a knife sliced through"), a stack of keys goes
    by key row."""
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
        if came is not None:
            over = (spots < c) & (came[:, 0] < t) & (came[:, 1] > t) & (np.abs(raw[:, 2] - key) <= 1)
            if over.any():
                spots = spots.copy()
                spots[over & (spots == spots[over].max()) & (raw[:, 0] >= t)] = c
            # a stacked step goes by key row (each key to the piece where the line reaches it: user 2026-10-28, a flat
            # knife across a steep stack separates the keys above from those below); only a key held on its own
            # (a flat part, one key a step) is cut in two in time where the knife goes through it
            row = key + 0.5 - np.floor(key + 0.5)
            if min(row, 1 - row) < 1e-6:
                continue  # (the knife on the edge between two rows: no note under it)
            q = int(np.floor(key + 0.5))
            idx = np.flatnonzero((raw[:, 0] < t) & (raw[:, 1] > t) & (raw[:, 2] == q))
            if len(idx) != 1:
                continue
            same = (raw[:, 0] == raw[idx[0], 0]) & (np.abs(raw[:, 2] - q) == 1)
            if same.any():
                continue  # (a stack: by key row)
            second = raw[idx].copy()
            second[:, 0] = t
            raw[idx, 1] = t
            was = spots[idx]
            spots = spots.copy()
            spots[idx] = np.minimum(was, np.nextafter(c, -np.inf))
            raw = np.concatenate([raw, second])
            spots = np.append(spots, np.maximum(was, c))
            came = np.concatenate([came, came[idx]])
            if tracks is not None:
                tracks = np.append(tracks, tracks[idx])
            continue
        near = np.abs(raw[:, 2] - key) <= 1
        late = np.flatnonzero((spots < c) & (raw[:, 0] == t) & (raw[:, 1] > t) & near)
        if len(late):  # (starting under half a tick before the cut, rounded onto it: nothing of it is before the cut,
            # it goes to the piece after whole, partial sweep 11-02: it stayed with the piece before, uncut)
            ks = (spots[late] // BIG).astype(np.int64)
            rs = spots[late] - ks * BIG
            exact = np.array([float(beat_on(paths[kk], rr)) for kk, rr in zip(ks.tolist(), rs.tolist())]) * ppq
            late = late[exact < t - 1e-9]  # (an upright part: its keys start right on it, they stay)
            if len(late):
                spots = spots.copy()
                spots[late] = c
        hit = (spots < c) & (raw[:, 0] < t) & (raw[:, 1] > t) & near
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
        if got and cut.get("mould"):  # (cut with a shape before: both halves keep that, mould.py)
            from notes.mould import moved_mould
            h["cut"]["mould"] = moved_mould(cut["mould"], d)
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
    fail = CANT if any((sh.get("cut") or {}).get("knife") or (sh.get("cut") or {}).get("mould") for sh in olds) else None
    if not all(got):
        return fail
    wholes = [json.dumps(dict(outline(g[0]), **tools(g[0]), cut=g[0].get("cut")), sort_keys=True) for g in got]
    if len(set(wholes)) > 1:
        return fail
    keep = None  # cut with a shape (mould.py): pieces of one side stay that side; its two sides = the shape back
    two = False
    if fail and any(sh["cut"].get("mould") for sh in olds):
        from notes.mould import moved_mould
        ms = [moved_mould(sh["cut"]["mould"], moved_by(sh)) if sh["cut"].get("mould") else None for sh in olds]
        if not all(ms) or any(not close([m["rows"], m["loops"]], [ms[0]["rows"], ms[0]["loops"]]) for m in ms):
            return CANT
        if len({m["side"] for m in ms}) == 1:
            keep = ms[0]
        elif not (len(olds) == 2 and all(g[1] == [0.0, None] and not sh["cut"].get("knife")
                                         for sh, g in zip(olds, got))):
            return CANT
        else:
            two = True
    items = []
    for sh, g in zip(olds, got):
        d = moved_by(sh)
        items.append([g[1], [[b0 + d[0], k0 + d[1], db, dk, sd] for b0, k0, db, dk, sd in sh["cut"].get("knife", [])]])
    items = [[[0.0, None], []]] if two else merged_parts(items)
    if len(items) > 1:
        return fail
    part, knives = items[0]
    same_vel = all(g[2] for g in got)
    src = got[0][0]
    if part[0] == 0 and part[1] is None and not knives and not keep:  # all of it: the shape it was
        out = {k: v for k, v in new.items() if k not in OUTLINE and k != "cut" and not (same_vel and k in VELOCITY)}
        out.update(outline(src))
        if same_vel:
            out.update(velocity(src))
        if src.get("fx"):  # (its pages first, then the ones the pieces had alike; its glue's place)
            from notes.fx import chained
            out["fx"] = chained(json.loads(json.dumps(src["fx"])), out.get("fx"), False) or []
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
    if olds[0]["cut"].get("id"):  # (still one of those pieces: their key, keyed_pieces)
        out["cut"]["id"] = olds[0]["cut"]["id"]
    if knives:
        out["cut"]["knife"] = knives
    if keep:
        out["cut"]["mould"] = keep
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
    their marks get new ids (the same among the copies, so pieces copied together still pair up). Pieces sliced from
    pasted notes the same: copies get a paste id of their own (they get channels apart from the originals)."""
    import uuid
    new = {}
    for sh in copies:
        from notes.loaded import drop_extract
        drop_extract(sh)  # (a copy of an Extract shape hides no loaded notes: the plan's choice; merges' parts too)
        for m in (sh.get("cut") or {}).get("marks", []):
            m["id"] = new.setdefault(m["id"], uuid.uuid4().hex[:12])
        if sh.get("made") == "leftovers" and isinstance(sh.get("own_slots"), list):
            sh.pop("own_slots")  # (a copy of Merge leftovers takes its own channels, like a paste's copy: user
            # 2026-11-12; pinned to the original's slots, copy and original changed each other)
        for t in _with_parts(sh):  # (a merged shape's pasted parts too: user 2026-11-12, a copy takes its own
            if "paste" in t:      # channels; sharing the id made copy + original one paste, sweep 177535815)
                t["paste"] = new.setdefault(("paste", t["paste"]), uuid.uuid4().hex[:12])
    fresh_cut_ids(copies)


def _with_parts(sh):
    """sh and every part merged into it (parts of parts too)."""
    out, todo = [], [sh]
    while todo:
        t = todo.pop()
        out.append(t)
        todo.extend(p for p in (t.get("merge") or {}).get("parts", []) if isinstance(p, dict))
    return out  # (pieces of a drawing too: the copies a shape of their own)


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
        h["cut"] = {"whole": whole, "was": outline(h),
                    "part": needed_halves(part + [[float(a[0]), float(a[1]), float(b[0] - a[0]), float(b[1] - a[1]),
                                                   sd]]),
                    "vel": None, "gate": velocity(h, GATE), "marks": mine + [new]}
        if own:
            h["cut"]["own_vel"] = True


def moved_mark(m, d):
    """A mark as it is now, its piece moved by d since it was cut."""
    out = dict(m, at=[m["at"][0] + d[0], m["at"][1] + d[1]])
    if m.get("segs"):
        out["segs"] = [[[x + d[0], y + d[1]] for x, y in sg] for sg in m["segs"]]
    if m.get("hits"):
        out["hits"] = [[x + d[0], y + d[1]] for x, y in m["hits"]]
    return out


def keep_velocity(sh):
    """After a new piece got its velocities (joined.piece_velocity): they're the whole's, so its notes take the
    whole's own (exact) until they're changed (source)."""
    cut = sh.get("cut")
    if cut and not cut.get("own_vel"):
        cut["vel"] = velocity(sh)


def keep_was(sh, old, new_span, old_span):
    """A new piece of old, a piece with a velocity of its own: it keeps the line old had when cut (over its own
    time), so own_velocities still finds only the stretches drawn over and the rest keeps the pages' velocities
    (sweep 2026-11-06: slicing such a piece again gave every note of both halves the plain line)."""
    from notes.joined import piece_velocity
    cut, was = sh.get("cut"), (old.get("cut") or {}).get("vel")
    if not (cut and cut.get("own_vel") and cut.get("vel") is None and was and "vel0" in was):
        return
    got = {}
    piece_velocity(got, dict(was), new_span, old_span)
    cut["vel"] = got if "vel0" in got else dict(was)


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
        if not whole or not was or (whole["kind"] == "custom") != (was["kind"] == "custom") or (
                (whole["kind"] == "funnel") != (was["kind"] == "funnel")) or not (
                whole["kind"] in LINE_KINDS or whole["kind"] == "funnel" or whole["kind"] == "custom"
                and "notes" not in whole):
            return None
        if whole["kind"] == "custom":
            part = [[float(x) for x in h[:4]] + [1 if h[4] > 0 else -1] for h in c["part"]]
            if not part or any(len(h) != 5 for h in c["part"]):
                return None
            part = needed_halves(part)  # (older files kept every earlier cut on every piece: same notes without)
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
            if m.get("hits"):
                mk["hits"] = [pt(p) for p in m["hits"]]
            marks.append(mk)
        snaps = {}
        for key, keys in (("vel", VELOCITY), ("gate", GATE)):  # (as the piece's own would come out of a file)
            v = c.get(key)
            snaps[key] = velocity(clean_shape(dict(c["was"], **v)) or {}, keys) if isinstance(v, dict) else None
        if (snaps["vel"] or {}).get("vel_env"):  # (older files: every earlier cut's point before its start, as the
            from notes.joined import thin_lead  # piece's own line: engine.clean_shape)
            snaps["vel"]["vel_env"] = thin_lead(snaps["vel"]["vel_env"])
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None
    out = {"whole": dict(outline(whole), **velocity(whole), **(velocity(whole, GATE) if whole["kind"] == "custom"
                                                               else {}), **tools(whole), **inner(whole)),
           "was": outline(was), "part": part, "vel": snaps["vel"], "marks": marks}
    if whole["kind"] == "custom":
        out["gate"] = snaps["gate"]
    elif knife:
        out["knife"] = knife
        if c.get("sided") is True:
            out["sided"] = True
    if c.get("mould"):
        from notes.mould import clean_mould
        try:
            out["mould"] = clean_mould(c["mould"])
        except (KeyError, TypeError, ValueError, IndexError):
            return None
    if c.get("own_vel"):
        out["own_vel"] = True
    if isinstance(c.get("id"), str) and c["id"]:  # (its cut's key: keyed_pieces)
        out["id"] = c["id"][:32]
    return out
