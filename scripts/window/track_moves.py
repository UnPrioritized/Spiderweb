"""Moving notes in the track list (Draw into a track group E, like Domino's): the notes STARTING inside the list's
Select boxes (rows x time) dragged to other tracks and / or in time, or copied (Ctrl). A shape wholly inside moves
as it is (its colours laid on the tracks as far down as it moved); a shape only partly inside is cut there: both
parts become plain notes (like Lasso's cut drawn art), the part inside moves. Every moved shape is laid on the
tracks it lands on, whatever they are (yours = inside it, Spiderweb's = pinned there, a loaded MIDI's = drawn into
it), so it never bounces to other tracks afterwards."""

import copy

import numpy as np

from files.lang import tr, trn
from files.own_tracks import FILE0, HAND
from notes.engine import slot_track_channel, track_ids
from notes.loaded import drop_extract, held_extract


def row_entry(app, s):
    """A track list row's track (its id there) as a placed_on entry: yours = its id, Spiderweb's = -1 - slot, a
    loaded MIDI's track n = FILE0 + n (None: a damaged one, only ever copied as it is)."""
    if s >= HAND:
        return s - HAND
    if s < 0:
        n, lm = -1 - s, app.loaded
        whole = lm is None or n >= len(lm.mf.tracks) or lm.mf.tracks[n].whole
        return FILE0 + n if whole else None
    return -1 - s


def boxed(app, boxes):
    """Which of app.rendered's notes START inside the boxes (track_list.boxes: rows x beats; a ruler band = every
    row)."""
    r, ppq = app.rendered, app.ppq
    rows = app.track_list.note_rows()
    out = np.zeros(len(r), bool)
    for b in boxes:
        a, z = round(b["t0"] * ppq), round(b["t1"] * ppq)
        m = (r[:, 0] >= a) & (r[:, 0] < z) & (rows >= 0)
        if b["rows"] is not None:
            m &= (rows >= b["rows"][0]) & (rows <= b["rows"][1])
        out |= m
    return out


def colour_rows(app, sh, mine):
    """Each colour's track list row of a shape with all its notes (mine) in view: laid ones by their list, others
    by where most of each colour's notes are (App.colour_slots)."""
    from window.app import placed_on
    tl = app.track_list
    if placed_on(sh, app.own_channels()):
        slots = app.laid_slots(sh)
    else:
        slots = app.colour_slots(sh, mine)
    return [tl.row_of_id(v) for v in track_ids(np.array(slots, np.int64)).tolist()]


def lay_on(app, sh, rows):
    """sh laid on the tracks of these list rows (colour k on rows[k]; Domino's paste, done: nothing left to fit)."""
    tl = app.track_list
    ids = tl.tracks()
    entries = [row_entry(app, int(ids[r])) for r in rows]
    for key in ("cols", "into_gap", "into_rest", "into_new", "slots10"):
        sh.pop(key, None)
    sh["into"] = entries
    use10 = app.picture_use10
    if any(v < 0 for v in entries):  # (fit_into's channel rule for Spiderweb's tracks it's pinned on)
        sh["slots10"] = bool(use10) and any(slot_track_channel(-1 - v, use10)[1] == 9 for v in entries if v < 0)


def move_boxed(app, boxes, dt, drow, copying=False):
    """The notes in the boxes moved dt beats and drow rows (copying: copies moved, the notes stay): one undo step.
    False when nothing was moved (status line says why)."""
    from window.app import can_go_in
    tl = app.track_list
    r = app.rendered
    rows = tl.note_rows()
    inside = boxed(app, boxes)
    if not inside.any():
        app.status.config(text=tr("tracks.box_empty"))
        return False
    owners = np.unique(r[inside, 5]).tolist()
    if any(not can_go_in(app.shapes[i]) for i in owners if i < len(app.shapes)):
        app.status.config(text=tr("tracks.box_cant"))
        return False
    order = np.argsort(r[:, 5], kind="stable")
    whose = r[order, 5]
    # (each shape: what goes where, worked out on the notes as they are before anything changes)
    plans, entries = [], [row_entry(app, int(s)) for s in tl.tracks()]
    n_rows = len(entries)
    for i in owners:
        a, z = np.searchsorted(whose, [i, i + 1])
        at = order[a:z]
        m = inside[at]
        whole = bool(m.all())
        if whole:
            got = colour_rows(app, app.shapes[i], r[at])
        else:  # (a part: each of its colours in the plain notes = its notes' tracks in order, plain_copies' columns)
            key = r[at, 4][m]
            if app.in_own_track(app.shapes[i]):  # (laid: its notes' colours = their track's place in its list)
                place = {s: k for k, s in reversed(list(enumerate(app.laid_slots(app.shapes[i]))))}
                key = np.array([place.get(int(s), 0) for s in key.tolist()], np.int64)
            vals, first = np.unique(key, return_index=True)
            got = rows[at][m][first].tolist()
        if any(g is None or not 0 <= g + drow < n_rows or entries[g + drow] is None for g in got):
            app.status.config(text=tr("tracks.box_cant"))
            return False
        plans.append((i, whole, m, [g + drow for g in got]))
    app.roll.end_typing()
    app.push_undo(name=tr("tracks.box_copy_step" if copying else "tracks.box_move_step"))
    cut, moved, extra = 0, [], []
    for i, whole, m, to in plans:
        sh = app.shapes[i]
        if whole:
            piece = copy.deepcopy(sh) if copying else sh
            if copying:
                drop_extract(piece)  # (a copy hides no loaded notes: sliced.fresh_marks' rule)
                extra.append(piece)
            else:
                moved.append(i)
        else:
            ex = held_extract(sh)
            parts = [app.plain_copies({i}, moved=lambda rr, keep=keep: rr[keep]).get(i) for keep in (m, ~m)]
            piece, rest = parts
            if piece is None:
                continue
            if ex and not copying:  # (both parts hold the loaded notes it took)
                piece["extract"] = copy.deepcopy(ex)
            if rest is not None and not copying:
                if ex:
                    rest["extract"] = ex
                app.shapes[i] = rest
            extra.append(piece)
            cut += not copying
        piece["pts"] = [[b + dt, p] for b, p in piece["pts"]]
        lay_on(app, piece, to)
    first = len(app.shapes)
    app.shapes.extend(extra)
    picked = moved + list(range(first, len(app.shapes)))
    app.select_many(picked, picked[-1] if picked else None)
    # (the boxes go along, onto the tracks they landed on: rows come and go when tracks empty / fill)
    ids = tl.tracks()
    ends = [None if b["rows"] is None else (int(ids[b["rows"][0] + drow]), int(ids[b["rows"][1] + drow]))
            for b in tl.boxes]
    app.shapes_changed()
    boxes = []
    for b, e in zip(tl.boxes, ends):
        rows = None
        if e is not None:
            got = [tl.row_of_id(s) for s in e]
            rows = tuple(sorted(got)) if None not in got else (b["rows"][0] + drow, b["rows"][1] + drow)
        boxes.append(dict(b, t0=b["t0"] + dt, t1=b["t1"] + dt, rows=rows))
    tl.boxes = boxes
    tl.tracks()  # (kept inside the rows there are)
    tl.request_redraw()
    if cut:
        app.status.config(text=trn("tracks.box_cut", cut, n=cut))
    return True
