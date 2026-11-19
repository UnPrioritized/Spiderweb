"""Cut pieces share the whole's notes (PLAN.md "Cut pieces share the whole's notes", 2026-11-19).

A line piece that hasn't changed since the cut (only moved) makes no notes of its own: its whole is made ONCE,
exactly as if never cut (every step: Sustain, Gate steps, flips, Colours, pages, overlaps), cut at every cut of its
shape into parts, and each part goes to one piece, so the pieces together always hold exactly the whole's notes:
- a note goes to the piece holding the place on the line where it starts: a note the line itself made (same start
  and key as one of its plain line notes) by that note's spot; any other (moved by a step) by its start in time, or
  on a line going back in time / a joined curve by the nearest stretch of the line;
- a cut cuts the note it goes through (sounding there on the crossing key, the one starting last) and, on a
  Sustained line that never goes back in time, every note sounding there on the rows it takes in (every row for a
  Slice, the loop's rows for a Lasso: slice-2.md "SWEEP PICKS 2026-11-19"); the part after a cut goes to the piece on
  the later side of it.
The whole and its parts are made once for all its pieces (each piece only picks its own).
SHARE[0] = on (default); SPIDERWEB_SHARE=0 = the old way (each piece re-makes its notes), kept to compare; pieces
shares() says no to still go the old way.
"""
import json
import os

import numpy as np

from notes.sliced import BIG, beat_on, moved_by, source, spotted_notes, thick_of, tools

SHARE = [os.environ.get("SPIDERWEB_SHARE", "1") != "0"]  # (SPIDERWEB_SHARE=0: the old way, to compare)
_wholes = {}  # (src json, ppq, keys) -> the whole made once for all its pieces
_parts = {}  # (that key, the cuts, held, heard way) -> the whole's notes cut into parts + each part's owner spot
MOST = 8


def shares(sh):
    """Is sh a line piece that takes its notes from its whole's (only moved since the cut, nothing of its own)?"""
    from notes.tumour import LINE_KINDS
    cut = sh.get("cut")
    if not SHARE[0] or not isinstance(cut, dict) or sh.get("kind") not in LINE_KINDS:
        return False
    whole = cut.get("whole")
    part = cut.get("part")
    if not isinstance(whole, dict) or whole.get("cut") or not isinstance(part, list) or len(part) != 2:
        return False
    if isinstance(part[0], list) or cut.get("mould"):
        return False
    if (thick_of(sh) or thick_of(whole)) and not cut.get("knife"):  # (a wide pen's pieces are cut through notes)
        return False
    # (a piece from before the whole's own settings were kept at the cut: the whole is made with the piece's; a
    # Gate subtract / extend graph changing across the shape was cut per piece, so its notes can come out a tick or
    # so other than in that older version: user 2026-11-19, old projects take the new notes, "Before you update")
    return moved_by(sh) is not None


def _whole_settings(sh):
    """The settings the whole is made with for piece sh: the whole's own as when cut (whole["_set"]), with what the
    piece changed since (its settings now vs right after the cut, cut["set_at"]) on top; None: not kept (older)."""
    cut = sh.get("cut") or {}
    ws, at = (cut.get("whole") or {}).get("_set"), cut.get("set_at")
    if not isinstance(ws, dict) or not isinstance(at, dict):
        return None
    from notes.sliced import settings_of
    now = settings_of(sh)
    out = dict(ws)
    for k in set(now) | set(at):
        if k == "order":  # (the step order: the whole's; a piece's own glue / pages added since put themselves in its
            continue  # order, and they run on its share after, on_share; case 492774334)
        if now.get(k) != at.get(k):  # (changed on the piece since the cut: its own)
            if k in now:
                out[k] = now[k]
            else:
                out.pop(k, None)
    return out


# (what the whole's notes went through already, in the share: not done again on it)
STEPS = ("sustain", "extend", "subtract", "gate_flip", "recolour", "order", "off", "_run", "_noglue0", "_sus",
         "_gate", "_upto", "_before", "_whole", "_whole_colours", "_order_was")


def on_share(sh):
    """A sharing piece with glue / note tool pages of its own (added since the cut): a stand-in shape making them on
    its share (the whole's finished notes, its steps all done), engine.fx_notes: its notes are asked for without the
    glue / pages ("_share_of" = the piece without them), the rest as the piece."""
    # (its step order without them: the order its own glue / pages put themselves in is not the whole's, which a
    # piece from before the whole's settings were kept would hand on; case 492774334)
    bare = {k: v for k, v in sh.items() if k not in ("fx", "glue", "order")}
    out = {k: v for k, v in sh.items() if k not in STEPS and k != "cut"}  # (a shape of its own to the pages)
    out["_share_of"] = bare
    from notes.sustain import sustain_on
    if sustain_on(sh):  # (its notes are held: glue / pages keep their overlaps, as on the Sustained piece)
        out["held"] = True
    if sh.get("gate_flip"):
        out["_gate"] = True
    return out


def piece_notes(sh, ppq, keys):
    """(notes, tracks) of a sharing piece (shares(sh)), in the frame sh is in: made where it was cut, moved along
    (sliced.made_at_cut: a moved piece keeps its notes exactly)."""
    from notes.sliced import made_at_cut
    got = made_at_cut(sh, ppq, keys, lambda here, q, room: _share(here, q, room))
    return got if got is not None else _share(sh, ppq, keys)


def _keep(cache, key, value):
    if len(cache) >= MOST:
        cache.clear()
    cache[key] = value
    return value


def _whole_of(src, ppq, keys):
    """The whole's notes as if never cut, before beat 0 / past the keys too (they may belong to a piece), its plain
    line notes with their spots (sorted by key, start), its paths, and whether it goes back in time."""
    from notes.engine import whole_notes_tracks
    key = (json.dumps(src, sort_keys=True), ppq, keys)
    got = _wholes.get(key)
    if got is None:
        notes, tracks = whole_notes_tracks(src, ppq, keys)
        notes = np.asarray(notes, np.int64).reshape(-1, 4)
        base, spots = spotted_notes(src, ppq, src.get("end_dot", False))
        base = np.asarray(base, np.int64).reshape(-1, 3)
        spots = np.asarray(spots, float)
        order = np.lexsort((-spots, base[:, 0], base[:, 2]))  # (by key, start; the latest spot first)
        paths = paths_of_src(src)
        back = len(paths) > 1 or any(len(a) > 1 and bool((np.diff(a[:, 0]) < -1e-9).any()) for a in paths)
        got = _keep(_wholes, key, (key, notes, None if tracks is None else np.asarray(tracks), base[order],
                                   spots[order], paths, back))
    return got


def paths_of_src(src):
    from notes.sliced import paths_of
    return [np.asarray(a, float) for a in paths_of(src)]


def _at(paths, u):
    """(beat, key, beats per spot along the path there) at spot u."""
    k = min(int(u // BIG), len(paths) - 1)
    a = paths[k]
    if len(a) < 2:
        return float(a[0][0]), float(a[0][1]), 0.0
    r = u - k * BIG
    i = max(0, min(int(r), len(a) - 2))
    p = a[i] + (a[i + 1] - a[i]) * (r - i)
    return float(p[0]), float(p[1]), float(a[i + 1][0] - a[i][0])


def _share(sh, ppq, keys):
    src, part, same_vel = source(sh)
    for k in ("fx", "glue", "_cut_heard", "_cut_kept"):  # (the whole's own pages / glue only; a piece's own
        src.pop(k, None)  # reading marks are not the whole's: one whole for all its pieces; "_from0" stays: where
    # beat 0 is, for notes across it reading their velocity there, made_at_cut)
    src.update(tools(sh["cut"]["whole"]))
    ws = _whole_settings(sh)
    if ws is not None:  # (the whole's own settings (a cut rewrites some per piece), the piece's changes on top)
        from notes.sliced import settings_of
        for k in settings_of(src):
            src.pop(k, None)
        src.update(json.loads(json.dumps(ws)))
    rc = src.get("recolour")
    if isinstance(rc, dict) and ("whole" in rc or "cut_frame" in rc):
        # (the piece's Recolour carries the whole's box and flips at the cut, recolour.carried: the whole counts its
        # colours in its own box, flipped as it was then, as engine.stirred_piece remakes it)
        then = rc.get("cut_frame")
        rc = {k: v for k, v in rc.items() if k not in ("whole", "frame", "cut_frame")}
        if then and tuple(then) != (1, 0, 0, 1):
            rc["frame"] = list(then)
        src["recolour"] = rc
    if not same_vel:  # (a velocity of its own: the whole made as it was when cut, its line read on top, own_velocity)
        from notes.sliced import with_whole_velocity
        src = with_whole_velocity(src, sh)
    whole = _whole_of(src, ppq, keys)
    from notes.sustain import sustain_on
    held = sustain_on(src) and not whole[6]
    cuts = sh["cut"].get("group") or [[u, None] for u in part if u]  # (every cut of its shape: sliced.sync_cuts)
    rows_of = {round(float(u), 9): rows for u, rows in sh["cut"].get("cuts") or ()}
    marks = []  # (tick, key there, rows it takes in, spot, the later side is the piece starting there)
    for u, rows in cuts:
        if not u:
            continue
        u = float(u)
        beat, key, step = _at(whole[5], u)
        rows = rows if rows is not None else rows_of.get(round(u, 9))
        marks.append((int(round(beat * ppq)), key, rows, u, step >= 0))
    marks.sort(key=lambda m: (m[0], m[3]))
    way = sh.get("_cut_heard")
    beat0 = int(round(float(src.get("_from0") or 0) * ppq))
    kept = bool(sh.get("_cut_kept"))
    pkey = (whole[0], json.dumps([[m[0], m[2], m[3]] for m in marks]), held, way, beat0, kept)
    got = _parts.get(pkey)
    if got is None:
        got = _keep(_parts, pkey, _cut_up(whole, marks, held, way, ppq, beat0, kept))
    notes, tracks, owner, start0 = got
    a, b = part
    mine = (owner >= (a if a else -np.inf)) & ((owner < b) if b is not None else True)
    out, start0 = notes[mine], start0[mine]
    tracks = None if tracks is None else tracks[mine]
    knife = sh["cut"].get("knife")
    if knife and len(out):  # (cut through its notes, a wide pen's / a shape with pages': the whole's notes on its side
        # of every knife, a note across one cut in two there; sliced.knife_cut)
        from notes.sliced import knife_cut
        got, tracks = knife_cut(np.column_stack([out, start0]), tracks, knife, moved_by(sh), ppq)
        out, start0 = np.ascontiguousarray(got[:, :4]), got[:, 4]
    if not same_vel and len(out):
        out = out.copy()
        out[:, 3] = own_velocity(sh, src, out, start0, ppq)
    # (not cut at the keys / beat 0 here: whoever asked does (engine.edge_made / sliced.made_at_cut put it back and
    # cut it), the parts past the edges are its kept notes; a wide pen's band reaches past its points, sweep 571275090)
    return np.ascontiguousarray(out), None if tracks is None else np.asarray(tracks)


def own_velocity(sh, src, notes, start0, ppq):
    """A piece with a velocity of its own (drawn in the velocity pane, typed in the panel): its share's velocities
    (the whole's, as it was when cut) where its line is as it was then, its own line's elsewhere (user 2026-11-02:
    a line over part of a piece sets only the notes under it; sliced.own_velocities). Each note reads the line where
    it started (a cut half: where the note began, joined.piece_velocity's lead; sliced.drawn_spots), a note across
    beat 0 there (engine.from0_starts)."""
    from notes.engine import cached_arrays, from0_starts, line_span
    from notes.envelope import velocity_env
    from notes.sliced import drawn_spots, own_velocities
    t = np.concatenate(cached_arrays(sh))[:, 0] * ppq
    t_lo, t_hi = line_span(sh, ppq, float(t.min()), float(t.max()))
    if t_hi - t_lo < 1e-6 * ppq:  # (shrunk to a point: its line runs over the whole's time, velocity.time_span)
        wt = np.concatenate(cached_arrays(src))[:, 0] * ppq
        t_lo, t_hi = float(wt.min()), float(wt.max())
    at = from0_starts(src, notes, np.asarray(start0, float), ppq)
    at = drawn_spots(sh, at, notes[:, 0], t_lo, t_hi)
    env = velocity_env(sh)
    lo, hi = min(0.0, float(env[0][0])), max(1.0, float(env[-1][0]))
    frac = np.clip((at - t_lo) / (t_hi - t_lo), lo, hi) if t_hi > t_lo else np.zeros(len(notes))
    return own_velocities(sh, notes[:, 3], frac)


def _takes(rows, k):
    return rows is None or any(a <= k <= b for a, b in rows)


def _through(notes, t, key, over, way, owner):
    """The note a cut at tick t (the line at key there) goes through, of those over (sounding there): on the crossing
    key (a key off when none: a curve's notes are spread evenly in time), the one last along the line (where the
    Channels mode fixes the line's own overlaps: the one starting last, then last along it; sliced.cut_through).
    -1: none."""
    q = int(np.floor(key + 0.5))
    for reach in (0, 1, 2):
        hit = np.flatnonzero(over & (np.abs(notes[:, 2] - q) <= reach))
        if len(hit):
            first = notes[hit, 0] if way else np.zeros(len(hit))
            return int(hit[np.lexsort((owner[hit], first))[-1]])
    return -1


def _heard_velocity(notes, j, t, way, beat0, kept):
    """The velocity the part after a cut through note j takes (LINES' OWN OVERLAPS, user 2026-11-19): where the
    Channels mode fixes a line's own overlaps (way: not As drawn, not held), the note heard at the cut: the one on
    its key starting last before it, when that started after j (split around: only while it still sounds). Notes
    wholly before beat 0 (tick beat0 here) take part only with "Keep notes made there" (kept; else they're gone);
    the others start on beat 0 as heard (the loudest of those starting together)."""
    if not way:
        return None
    on = (notes[:, 2] == notes[j, 2]) & (notes[:, 0] < t)
    if not kept:
        on &= notes[:, 1] > beat0
    before = np.flatnonzero(on)
    if not len(before):
        return None
    starts = notes[before, 0] if kept else np.maximum(notes[before, 0], beat0)
    last = before[starts == starts.max()]
    p = int(last[np.argmax(notes[last, 3])])
    s_j = int(notes[j, 0]) if kept else max(int(notes[j, 0]), beat0)
    if p != j and starts.max() >= s_j and (way != "split" or notes[p, 1] > t):
        return int(notes[p, 3])
    return None


def _cut_up(whole, marks, held, way, ppq, beat0=0, kept=False):
    """The whole's notes cut at every mark into parts, and each part's owner spot (the piece it goes to), + where
    each part's note started. Cut by cut along the line: a cut goes through a note of the piece just before it (the
    old rule, case 488603472: a long note of an earlier piece stays whole in its own piece); on a held line every
    note sounding there on its rows. beat0: the tick beat 0 is at here (made later, made_at_cut)."""
    _key, notes, tracks, base, spots, paths, back = whole
    owner, bi = _owners(notes, base, spots, paths, back, marks, ppq)
    start0 = notes[:, 0].copy()
    bend = np.where(bi >= 0, base[np.maximum(bi, 0), 1] if len(base) else 0, -1)  # (a line note's end as the line
    # made it, before any step: the cut goes through the line note sounding there, as the old way picked it)
    src_i = np.arange(len(notes))  # (each part's note in the whole: its heard velocity looks at the whole's notes)
    if not len(notes) or not marks:
        return notes, tracks, owner, start0
    out = notes.copy()
    trk = None if tracks is None else np.asarray(tracks).copy()
    prev = -np.inf
    for t, key, rows, u, later in sorted(marks, key=lambda m: m[3]):
        after = u if later else float(np.nextafter(u, -np.inf))
        over = (out[:, 0] < t) & (out[:, 1] > t)
        hit = np.zeros(len(out), bool)
        mine = (owner >= prev) & (owner < u)  # (the piece just before the cut along the line)
        line = mine & (bend >= 0) & (out[:, 0] < t) & (bend > t)  # (line notes sounding there, as the line made them)
        q = int(np.floor(key + 0.5))
        if (mine & (out[:, 2] == q) & (start0 == t) & (bend >= 0)).any():
            line &= out[:, 2] == q  # (the line's own note on the crossing key starts right on the cut: nothing to
            # cut there, no note a key off either: on a spiral that was another loop's note, case 872320488)
        j = _through(out, t, key, line, way, owner)
        if j >= 0 and not over[j]:
            j = -1  # (a step ended it before the cut: nothing of it to cut)
        elif j < 0 and not line.any():
            j = _through(out, t, key, mine & over & (bend < 0), way, owner)  # (notes a step made)
        if j >= 0:
            hit[j] = True
        if held:
            take = over.copy()
            if rows is not None and take.any():
                idx = np.flatnonzero(take)
                take[idx] = [_takes(rows, int(k)) for k in out[idx, 2].tolist()]
            hit |= take
        prev = u
        idx = np.flatnonzero(hit)
        if not len(idx):
            continue
        right = out[idx].copy()
        right[:, 0] = t
        if j >= 0:
            v = _heard_velocity(notes, int(src_i[j]), t, way, beat0, kept)
            if v is not None:
                right[idx == j, 3] = v
        out[idx, 1] = t
        out = np.concatenate([out, right])
        owner = np.concatenate([owner, np.full(len(idx), after)])
        start0 = np.concatenate([start0, start0[idx]])
        bend = np.concatenate([bend, bend[idx]])
        src_i = np.concatenate([src_i, src_i[idx]])
        if trk is not None:
            trk = np.concatenate([trk, trk[idx]])
    return out, trk, owner, start0


def _owners(notes, base, spots, paths, back, marks, ppq):
    """Per note the spot on the line it belongs to (where it starts)."""
    n = len(notes)
    owner = np.zeros(n, float)
    bi = np.full(n, -1, np.int64)  # (each note's line note, by start + key; -1: a note a step made)
    if not n:
        return owner, bi
    keyed = base[:, 2] * (1 << 40) + base[:, 0]
    want = notes[:, 2] * (1 << 40) + notes[:, 0]
    if len(keyed):
        i = np.clip(np.searchsorted(keyed, want), 0, len(keyed) - 1)
        hit = keyed[i] == want
        owner[hit] = spots[i[hit]]
        bi[hit] = i[hit]
    else:
        hit = np.zeros(n, bool)
    # (a line note starting under half a tick before a cut, rounded onto it, goes after it: nothing of it is before)
    ticks = {}
    for t, key, _rows, u, _later in marks:
        ticks.setdefault(t, []).append((u, key))
    for r in np.flatnonzero(hit & np.isin(notes[:, 0], list(ticks))).tolist():
        u = owner[r]
        for c, key in ticks[int(notes[r, 0])]:
            if u < c and abs(int(notes[r, 2]) - key) <= 1:
                k = min(int(u // BIG), len(paths) - 1)
                exact = float(beat_on(paths[k], u - k * BIG)) * ppq
                if exact < notes[r, 0] - 1e-9 or c - u < 1e-6:
                    u = c
        owner[r] = u
    rest = np.flatnonzero(~hit)
    if len(rest):
        beats = notes[rest, 0] / ppq
        owner[rest] = _nearest(paths, beats, notes[rest, 2] + 0.0) if back else _by_time(paths[0], beats)
    return owner, bi


def _by_time(a, beats):
    """Spots along a line that never goes back in time at these beats (before its start: its start; past its end:
    its end; an upright part: its last point at that beat)."""
    if len(a) < 2:
        return np.zeros(len(beats))
    t = a[:, 0]
    b = np.asarray(beats, float)
    j = np.clip(np.searchsorted(t, b, "right") - 1, 0, len(a) - 2)
    span = t[j + 1] - t[j]
    f = np.where(span > 0, (b - t[j]) / np.where(span > 0, span, 1), 0.0)
    u = j + np.clip(f, 0, 1)
    u = np.where(b <= t[0], 0.0, u)
    return np.where(b >= t[-1], float(len(a) - 1), u)


def _nearest(paths, beats, keys):
    """Spots of the line's stretches nearest these (beat, key) points (a beat counted as 4 keys)."""
    q = np.column_stack([np.asarray(beats, float) * 4, np.asarray(keys, float)])
    best_d = np.full(len(q), np.inf)
    best_u = np.zeros(len(q))
    for k, a in enumerate(paths):
        a = a * [4, 1]
        if len(a) < 2:
            if len(a):
                d = np.hypot(*(q - a[0]).T)
                better = d < best_d
                best_d[better], best_u[better] = d[better], k * BIG
            continue
        for j in range(len(a) - 1):  # (per stretch, all points at once)
            p, ab = a[j], a[j + 1] - a[j]
            L = float((ab ** 2).sum())
            f = np.clip(((q - p) @ ab) / L, 0, 1) if L > 0 else np.zeros(len(q))
            d = np.hypot(*(p + f[:, None] * ab - q).T)
            better = d < best_d
            best_d[better], best_u[better] = d[better], k * BIG + j + f[better]
    return best_u
