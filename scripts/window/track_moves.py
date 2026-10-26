"""Moving notes in the track list (Draw into a track group E, like Domino's): the notes STARTING inside the list's
Select boxes (rows x time) dragged to other tracks and / or in time, or copied (Ctrl). A shape wholly inside moves
as it is (its colours laid on the tracks as far down as it moved); a shape only partly inside is cut there: both
parts become plain notes (like Lasso's cut drawn art), the part inside moves. Every moved shape is laid on the
tracks it lands on, whatever they are (yours = inside it, Spiderweb's = pinned there, a loaded MIDI's = drawn into
it), so it never bounces to other tracks afterwards."""

import copy
import threading
from tkinter import messagebox

import numpy as np

from files.lang import tr, trn
from files.midi_read import Cancelled, MidiError
from files.own_tracks import FILE0, HAND
from notes.custom import notes_shape
from notes.engine import slot_track_channel, track_ids
from notes.loaded import EXTRACT_MOST, box_key, boxes_meet, drop_extract, gather_box, held_extract
from window.load_extract import hidden_boxes, sync_extract


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
    A loaded MIDI's notes in them are read first (on a thread, the status line says how far) and become an Extract
    (user 2026-10-26; copying: a plain copy of them, nothing hidden), moved with the rest."""
    from window.app import can_go_in
    r = app.rendered
    inside = boxed(app, boxes)
    owners = np.unique(r[inside, 5]).tolist()
    if any(not can_go_in(app.shapes[i]) for i in owners if i < len(app.shapes)):
        app.status.config(text=tr("tracks.box_cant"))
        return False
    files = loaded_boxes(app, boxes)
    if not files:
        if not inside.any():
            app.status.config(text=tr("tracks.box_empty"))
            return False
        return move_now(app, boxes, dt, drow, copying)
    if getattr(app, "_extracting", None):
        app.status.config(text=tr("extract.busy"))
        return False
    lm = app.loaded
    job = app._extracting = {"done": 0, "total": 1, "got": None, "error": None, "finished": False, "cancel": False}

    def run():
        try:
            got = job["got"] = []
            for b in files:
                got.append(gather_box(lm, b, cancel=lambda: job["cancel"], most=EXTRACT_MOST - sum(
                    len(g["s"]) for g in got), progress=lambda d, n: job.update(done=d, total=max(n, 1))))
                if sum(len(g["s"]) for g in got) > EXTRACT_MOST:
                    break
        except BaseException as e:  # (said below)
            job["error"] = e
        job["finished"] = True

    def poll():
        if not job["finished"]:
            app.busy(tr("extract.reading", pct=int(100 * job["done"] / job["total"])))
            app.after(100, poll)
            return
        app._extracting = None
        app.busy(None)
        e = job["error"]
        if isinstance(e, Cancelled) or app.loaded is not lm:
            return
        if isinstance(e, (OSError, MidiError)):
            messagebox.showerror(tr("app.spiderweb_2"), tr("load.export_gone", path=lm.path), parent=app)
            return
        if isinstance(e, MemoryError):
            messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=app)
            return
        if e is not None:
            raise e
        got = job["got"]
        n = sum(len(g["s"]) for g in got)
        if n > EXTRACT_MOST:
            messagebox.showerror(tr("app.spiderweb_2"), tr("extract.too_many", n=f"{n:,}",
                                                          most=f"{EXTRACT_MOST:,}"), parent=app)
            return
        if any(boxes_meet(a, b) for a in files for b in hidden_boxes(app)):  # (an undo while it read brought back
            return                                                             # an Extract of these notes)
        if not n and not boxed(app, boxes).any():
            app.status.config(text=tr("tracks.box_empty"))
            return
        app.roll.end_typing()
        app.push_undo(name=tr("tracks.box_copy_step" if copying else "tracks.box_move_step"))
        fresh = []
        if n:
            sh = loaded_shape(app, lm, files, got, copying)
            app.shapes.append(sh)
            fresh.append(len(app.shapes) - 1)
            if not copying:
                sync_extract(app, {box_key(b): g for b, g in zip(files, got)})
            app.shapes_changed()
        move_now(app, boxes, dt, drow, copying, pushed=True, fresh=fresh)
    threading.Thread(target=run, daemon=True, name="move loaded notes").start()
    poll()
    return True


def loaded_boxes(app, boxes):
    """The loaded MIDI's notes in the list's boxes as Extract boxes (file ticks, every key, the loaded tracks on
    their rows that have notes), less what's hidden already (another Extract's / deleted notes: a note is only ever
    in one box)."""
    lm = app.loaded
    if lm is None or getattr(lm, "changed", False):
        return []
    ids = app.track_list.tracks()
    out = []
    for b in boxes:
        rows = range(len(ids)) if b["rows"] is None else range(b["rows"][0], b["rows"][1] + 1)
        tracks = sorted(-1 - int(ids[k]) for k in rows if int(ids[k]) < 0)
        tracks = [n for n in tracks if n < len(lm.tracks) and lm.tracks[n].notes and n < len(lm.mf.tracks)
                  and lm.mf.tracks[n].whole]
        t0, t1 = max(0, round(b["t0"] * lm.ppq)), round(b["t1"] * lm.ppq)
        if tracks and t1 > t0:
            out.append({"t0": t0, "t1": t1, "k0": 0, "k1": lm.keys - 1, "tracks": tracks})
    for h in hidden_boxes(app):
        out = [p for x in out for p in carve(x, h)]
    return out


def carve(box, hole):
    """box less hole (Extract boxes: note-on ticks t0 .. t1 - 1, keys k0..k1, tracks): the boxes left, none
    overlapping hole (loaded.boxes_meet)."""
    if not boxes_meet(box, hole):
        return [box]
    out = []
    both = [t for t in box["tracks"] if t in hole["tracks"]]
    others = [t for t in box["tracks"] if t not in hole["tracks"]]
    if others:
        out.append(dict(box, tracks=others))
    if box["t0"] < hole["t0"]:
        out.append(dict(box, tracks=both, t1=hole["t0"]))
    if hole["t1"] < box["t1"]:
        out.append(dict(box, tracks=both, t0=hole["t1"]))
    mid = dict(box, tracks=both, t0=max(box["t0"], hole["t0"]), t1=min(box["t1"], hole["t1"]))
    if box["k0"] < hole["k0"]:
        out.append(dict(mid, k1=hole["k0"] - 1))
    if hole["k1"] < box["k1"]:
        out.append(dict(mid, k0=hole["k1"] + 1))
    return out


def loaded_shape(app, lm, files, got, copying):
    """The loaded notes read (got, per box of files) as one shape in the file's tracks they came from, each on its
    own channel (load_extract.finish's): an Extract holding them (copying: a plain copy, nothing hidden)."""
    trk, s, e, lane, vel, on = (np.concatenate([g[k] for g in got]) for k in ("trk", "s", "e", "lane", "vel", "on"))
    if len(got) > 1:  # (each note once)
        _, first = np.unique(np.column_stack([trk, on]), axis=0, return_index=True)
        first.sort()
        trk, s, e, lane, vel = (a[first] for a in (trk, s, e, lane, vel))
    pairs, colour = np.unique(trk * 16 + (lane >> 8), return_inverse=True)
    scale = app.ppq / lm.ppq
    rows = np.column_stack([np.rint(s * scale), np.maximum(1, np.rint((e - s) * scale)), lane & 255, vel,
                            colour.ravel()]).astype(np.int64)
    sh = notes_shape(rows, app.ppq, tr("extract.name"))
    sh.update(own_slots=True, held=True, into=[FILE0 + int(p) // 16 for p in pairs],
              into_ch=[int(p) % 16 for p in pairs])
    if not copying:
        sh["extract"] = [{"boxes": [[b["t0"], b["t1"], b["k0"], b["k1"]]], "tracks": b["tracks"]} for b in files]
    return sh


def move_now(app, boxes, dt, drow, copying=False, pushed=False, fresh=()):
    """move_boxed once the notes are all Spiderweb's (pushed: its undo step made already; fresh: shapes just made of
    loaded notes: moved, never copied)."""
    tl = app.track_list
    r = app.rendered
    rows = tl.note_rows()
    inside = boxed(app, boxes)
    owners = np.unique(r[inside, 5]).tolist()
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
            if pushed:  # (the loaded notes made into a shape already: taken back, as before)
                app.undo()
                app.redo_stack.clear()
            app.status.config(text=tr("tracks.box_cant"))
            return False
        plans.append((i, whole, m, [g + drow for g in got]))
    if not pushed:
        app.roll.end_typing()
        app.push_undo(name=tr("tracks.box_copy_step" if copying else "tracks.box_move_step"))
    cut, moved, extra = 0, [], []
    for i, whole, m, to in plans:
        sh = app.shapes[i]
        if whole:
            copied = copying and i not in fresh
            piece = copy.deepcopy(sh) if copied else sh
            if copied:
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
