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
from files.own_tracks import FILE0, HAND, key_track
from notes.custom import notes_shape
from notes.engine import slot_track_channel, track_ids
from notes.loaded import (EXTRACT_MOST, box_groups, box_key, boxes_meet, drop_extract, extract_groups, gather_box,
                          held_extract)
from window.load_extract import hidden_boxes, sync_extract


def row_entry(app, s):
    """A track list row's track (its id there) as a placed_on entry: yours = its id, Spiderweb's = -1 - slot, a
    loaded MIDI's track n = FILE0 + n (None: a damaged one, only ever copied as it is)."""
    if s >= HAND:
        return s - HAND
    if s < 0:
        n, lm = key_track(-1 - s), app.loaded  # (a part: its track, PLAN #11 D)
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
    from window.app import keep_home
    tl = app.track_list
    ids = tl.tracks()
    entries = [row_entry(app, int(ids[r])) for r in rows]
    keep_home(sh)  # (an Extract's colours on other tracks: those tracks' channels, laid_slots_of)
    for key in ("cols", "into_gap", "into_rest", "into_new", "slots10"):
        sh.pop(key, None)
    sh["into"] = entries
    use10 = app.picture_use10
    if any(v < 0 for v in entries):  # (fit_into's channel rule for Spiderweb's tracks it's pinned on)
        sh["slots10"] = bool(use10) and any(slot_track_channel(-1 - v, use10)[1] == 9 for v in entries if v < 0)




def move_boxed(app, boxes, dt, drow, copying=False, tempo=None):
    """The notes in the boxes moved dt beats and drow rows (copying: copies moved, the notes stay): one undo step."""
    return box_job(app, boxes, "copy" if copying else "move", dt, drow, tempo=tempo)


def has_notes(app, boxes):
    """Do the boxes hold any notes (Spiderweb's, or a loaded MIDI's tracks with notes on their rows)?"""
    return bool(boxes) and (bool(boxed(app, boxes).any()) or bool(loaded_boxes(app, boxes)))


def tempo_only(app, kind, tempo, dt=0.0):
    """box_job for boxes holding the Conductor's changes but no notes (tempo = {"spans", "clip"}, track_tempo)."""
    from window import track_tempo
    spans, clip = tempo["spans"], tempo["clip"]
    if kind in ("clip", "cut"):
        if clip is None:
            app.status.config(text=tr("tracks.tempo_empty"))
            return False
        clip["n"] = app.copy_stamp()  # (the tempo copy from now: newer than the shapes copied)
        app.tempo_clip = clip
        n = track_tempo.count(clip)
        app.status.config(text=trn("tracks.tempo_copied", n, n=n))
        app.drop_own_copy()  # (Spiderweb's older copy off the clipboard, as the Conductor pane's copy)
        if app.copy_domino.get():
            app.tempo_to_domino(clip, quiet=True)
        if kind == "clip":
            return True
    if clip is None or not track_tempo.changes(app, spans):  # (a held stretch: nothing to delete / move)
        app.status.config(text=tr("tracks.tempo_empty"))
        return False
    names = {"cut": "app.cut", "delete": "tracks.tempo_delete_step", "move": "tracks.tempo_move_step",
             "copy": "tracks.tempo_copy_step"}
    name = tr(names[kind])
    app.push_undo(name=name)
    if kind in ("cut", "delete"):
        track_tempo.empty(app, spans, name)
    else:
        track_tempo.move(app, spans, dt, kind == "copy", name)
    tl = app.track_list
    if kind in ("cut", "delete"):
        tl.boxes = []
    else:
        tl.boxes = [dict(b, t0=b["t0"] + dt, t1=b["t1"] + dt) for b in tl.boxes]
    tl.request_redraw()
    return True


def box_job(app, boxes, kind, dt=0.0, drow=0, extra=None, tempo=None):
    """What's in the list's boxes: "move" / "copy" (dragged, Ctrl: copies), "clip" (Ctrl+C), "cut" (Ctrl+X),
    "delete", "remove" (Remove Track: deleted + extra = {"ids": the list's track ids, "own": your tracks' ids,
    "files": a loaded MIDI's track numbers} gone with them in the same step). A loaded MIDI's notes in them are read
    first (on a thread, the status line says how far): moved = an Extract (user 2026-10-26), copied = a plain copy
    (nothing hidden), deleted = Delete loaded notes'. False when nothing was done (the status line says why)."""
    from window.app import can_go_in
    r = app.rendered
    inside = boxed(app, boxes)
    owners = np.unique(r[inside, 5]).tolist()
    order = np.argsort(r[:, 5], kind="stable")
    whose = r[order, 5]
    for i in owners:  # (laid on tracks / cut into plain notes: not every kind of shape can)
        a, z = np.searchsorted(whose, [i, i + 1])
        if not can_go_in(app.shapes[i]) and (kind in ("move", "copy") or not inside[order[a:z]].all()):
            app.status.config(text=tr("tracks.box_cant"))
            return False
    # (Remove Track: a loaded MIDI's tracks go whole, nothing read: left out of drawing / playing / the export by
    # app.file_gone, hunt 2026-10-26: a 10 M-note track was refused)
    files = loaded_boxes(app, boxes) if kind != "remove" else []
    if not files:
        if not inside.any() and kind != "remove":
            if tempo is not None:  # (only the Conductor's changes in them)
                return tempo_only(app, kind, tempo, dt)
            app.status.config(text=tr("tracks.box_empty"))
            return False
        return finish_job(app, boxes, kind, dt, drow, extra=extra, tempo=tempo)
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
        if not n and not boxed(app, boxes).any() and kind != "remove":
            if tempo is not None:
                tempo_only(app, kind, tempo, dt)
                return
            app.status.config(text=tr("tracks.box_empty"))
            return
        finish_job(app, boxes, kind, dt, drow, lm, files, got if n else [], extra, tempo)
    threading.Thread(target=run, daemon=True, name="loaded notes in the track list's box").start()
    poll()
    return True


def finish_job(app, boxes, kind, dt, drow, lm=None, files=(), got=(), extra=None, tempo=None):
    """box_job once a loaded MIDI's notes in the boxes are read (got: per box of files; none = no loaded notes).
    tempo: {"spans", "clip"} of the Conductor's changes going along (track_tempo; the same undo step)."""
    if kind in ("clip", "cut"):
        clip_boxed(app, boxes, loaded_shape(app, lm, files, got, True) if got else None,
                   tempo["clip"] if tempo is not None else None)
        if kind == "clip":
            return True
    if kind == "remove":
        name = tr("tracks.remove_step" if len(extra["ids"]) == 1 else "tracks.remove_many_step")
        return delete_boxed(app, boxes, lm, files, got, name, extra)
    if kind in ("cut", "delete"):
        return delete_boxed(app, boxes, lm, files, got, tr("app.cut" if kind == "cut" else "tracks.box_delete_step"),
                            tempo=tempo)
    app.roll.end_typing()
    name = tr("tracks.box_copy_step" if kind == "copy" else "tracks.box_move_step")
    app.push_undo(name=name)
    fresh = []
    if got:
        app.shapes.append(loaded_shape(app, lm, files, got, kind == "copy"))
        fresh.append(len(app.shapes) - 1)
        if kind == "move":
            sync_extract(app, {box_key(b): g for b, g in zip(files, got)})
        app.shapes_changed()
    done = move_now(app, boxes, dt, drow, kind == "copy", fresh)
    if done and tempo is not None and tempo["clip"] is not None:
        from window import track_tempo
        track_tempo.move(app, tempo["spans"], dt, kind == "copy", name)
    return done


def loaded_boxes(app, boxes):
    """The loaded MIDI's notes in the list's boxes as Extract boxes (file ticks, every key, the loaded tracks on
    their rows that have notes), less what's hidden already (another Extract's / deleted notes: a note is only ever
    in one box)."""
    lm = app.loaded
    if lm is None or getattr(lm, "changed", False):
        return []
    tl = app.track_list
    ids = tl.tracks()
    out = []
    for b in boxes:
        rows = range(len(ids)) if b["rows"] is None else range(b["rows"][0], b["rows"][1] + 1)
        # (a part's row / a track with parts: only its channels, PLAN #11 D hunt; one box per channel set)
        by = {}
        for n, chans in tl.rows_chans(rows).items():
            if n < len(lm.tracks) and lm.tracks[n].notes and n < len(lm.mf.tracks) and lm.mf.tracks[n].whole:
                by.setdefault(tuple(chans), []).append(n)
        t0, t1 = max(0, round(b["t0"] * lm.ppq)), round(b["t1"] * lm.ppq)
        for chans, tracks in by.items():
            if t1 > t0:
                out.append({"t0": t0, "t1": t1, "k0": 0, "k1": lm.keys - 1, "tracks": sorted(tracks),
                            **({"chans": list(chans)} if chans else {})})
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
    # (channels: the box's ones the hole hasn't stay whole; the carving below is of the shared ones)
    mine, its = set(box.get("chans") or range(16)), set(hole.get("chans") or range(16))
    if mine - its:
        out.append(dict(box, tracks=both, chans=sorted(mine - its)))
    if mine & its != set(range(16)):
        box = dict(box, chans=sorted(mine & its))
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
              into_ch=[int(p) % 16 for p in pairs], into_home=[FILE0 + int(p) // 16 for p in pairs])
    if not copying:
        sh["extract"] = box_groups(files)
    return sh


def plans_of(app, boxes, drow):
    """Per shape with notes in the boxes: (its number, wholly inside, which of its notes (by_owner's order), each
    colour's row + drow). None when a row isn't there (or is a damaged loaded track's)."""
    tl = app.track_list
    r = app.rendered
    rows = tl.note_rows()
    inside = boxed(app, boxes)
    order = np.argsort(r[:, 5], kind="stable")
    whose = r[order, 5]
    entries = [row_entry(app, int(s)) for s in tl.tracks()]
    plans = []
    for i in np.unique(r[inside, 5]).tolist():
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
            _, first = np.unique(key, return_index=True)
            got = rows[at][m][first].tolist()
        if any(g is None or not 0 <= g + drow < len(entries) or entries[g + drow] is None for g in got):
            return None
        plans.append((i, whole, m, [g + drow for g in got]))
    return plans


def parts_of(app, i, m):
    """Shape i cut by m (its notes in the box, by_owner's order): (the part inside, the rest) as plain notes
    (either None when empty); a shape holding loaded notes (an Extract) = both keep holding them."""
    ex = held_extract(app.shapes[i])
    # (the rest keeps the notes kept past the keys / before beat 0: the box holds only heard notes)
    piece, rest = (app.plain_copies({i}, moved=lambda rr, keep=keep: rr[keep], kept_after=after).get(i)
                   for keep, after in ((m, False), (~m, True)))
    for p in (piece, rest):
        if ex and p is not None:
            p["extract"] = copy.deepcopy(ex)
    return piece, rest


def move_now(app, boxes, dt, drow, copying=False, fresh=()):
    """box_job's move / copy once the notes are all Spiderweb's (its undo step made already; fresh: shapes just made
    of loaded notes: moved, never copied)."""
    tl = app.track_list
    plans = plans_of(app, boxes, drow)
    if plans is None:  # (the step taken back: as before)
        app.undo()
        app.redo_stack.clear()
        app.status.config(text=tr("tracks.box_cant"))
        return False
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
            piece, rest = parts_of(app, i, m)
            if piece is None:
                continue
            if copying:
                drop_extract(piece)
            elif rest is not None:
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

    def end_id(r):  # (the Conductor row (-1) stays itself: a box holding it moves in time only)
        return r if r < 0 else int(ids[r + drow])
    ends = [None if b["rows"] is None else (end_id(b["rows"][0]), end_id(b["rows"][1])) for b in tl.boxes]
    app.shapes_changed()
    boxes = []
    for b, e in zip(tl.boxes, ends):
        rows = None
        if e is not None:
            got = [r if r < 0 else tl.row_of_id(s) for r, s in zip(b["rows"], e)]  # (r < 0: the Conductor row)
            rows = tuple(sorted(got)) if None not in got else (b["rows"][0] + drow, b["rows"][1] + drow)
        boxes.append(dict(b, t0=b["t0"] + dt, t1=b["t1"] + dt, rows=rows))
    tl.boxes = boxes
    tl.tracks()  # (kept inside the rows there are)
    tl.request_redraw()
    if cut:
        app.status.config(text=trn("tracks.box_cut", cut, n=cut))
    return True


def clip_boxed(app, boxes, loaded=None, tempo=None):
    """Ctrl+C in the list: the notes in the boxes copied (shapes wholly inside as they are, parts of others as plain
    notes, loaded: a plain copy of the loaded MIDI's), each colour's row remembered from the copy's first row, so
    Ctrl+V lays them out the same from the highlighted track (Domino's paste: App.fit_into's columns). tempo: the
    Conductor's changes copied with them (track_tempo.clip_of): pasted along with these notes."""
    tl = app.track_list
    plans = plans_of(app, boxes, 0) or []
    pieces, rows = [], []
    for i, whole, m, to in plans:
        if whole:
            piece = copy.deepcopy(app.shapes[i])
        else:
            piece = parts_of(app, i, m)[0]
            if piece is None:
                continue
        drop_extract(piece)
        pieces.append(piece)
        rows.append(to)
    if loaded is not None:
        pieces.append(loaded)
        rows.append([tl.row_of_id(FILE0 - 1 - v) for v in loaded["into"]])
    if not pieces:
        if tempo is not None:
            return tempo_only(app, "clip", {"spans": [], "clip": tempo})
        app.status.config(text=tr("tracks.box_empty"))
        return False
    first = min(min(r) for r in rows)
    for piece, r in zip(pieces, rows):
        for key in ("into_gap", "into_rest", "into_new"):
            piece.pop(key, None)
        piece["cols"] = [v - first for v in r]
    app.put_clipboard(pieces, loaded=loaded is not None, tempo=tempo)
    return True


def delete_boxed(app, boxes, lm, files, got, name, extra=None, tempo=None):
    """Delete (Ctrl+X's second half) in the list: the notes in the boxes go (shapes wholly inside deleted, the rest
    of others kept as plain notes, a loaded MIDI's = Delete loaded notes'), one undo step; the boxes go too."""
    tl = app.track_list
    plans = plans_of(app, boxes, 0) or []
    parts = {i: parts_of(app, i, m)[1] for i, whole, m, _ in plans if not whole}
    gone = {i for i, whole, _, _ in plans if whole} | {i for i, rest in parts.items() if rest is None}
    if not gone and not parts and not got and extra is None:
        if tempo is not None:
            return tempo_only(app, "delete", tempo)
        app.status.config(text=tr("tracks.box_empty"))
        return False
    app.roll.end_typing()
    app.push_undo(name=name)
    if tempo is not None and tempo["clip"] is not None:  # (the Conductor's changes go in the same step)
        from window import track_tempo
        track_tempo.empty(app, tempo["spans"], name)
    for i, rest in parts.items():
        if rest is not None:
            app.shapes[i] = rest
    # (an Extract deleted here: the notes it hid stay gone, user 2026-10-26 (hunt): moved loaded notes deleted in
    # the list came back at their old place; the piano roll's Delete still gives them back)
    taken = [{"boxes": [list(b) for b in g["boxes"]], "tracks": list(g["tracks"]),
              **({"chans": list(g["chans"])} if g.get("chans") else {})}
             for i in gone for g in extract_groups(held_extract(app.shapes[i]))]
    for i in sorted(gone, reverse=True):
        del app.shapes[i]
    if taken:
        app.deleted = app.deleted + taken
    if got:
        app.deleted = app.deleted + box_groups(files)
        sync_extract(app, {box_key(b): g for b, g in zip(files, got)})
    if extra is not None:  # (Remove Track: the tracks go too)
        if extra["own"]:
            app.own_tracks = [t for t in app.own_tracks if t["id"] not in extra["own"]]
        if extra["files"]:
            app.file_gone = sorted(set(app.file_gone) | set(extra["files"]))
        if 0 in extra["ids"]:  # (Track 1 removed: a new project's empty one doesn't come back, user 2026-10-26)
            app.start_track = False
        if tl.current is not None and tl.current in extra["ids"]:
            tl.current = None
        tl.selected -= set(extra["ids"])
    app.select_many([], None)
    tl.boxes = []
    app.shapes_changed()
    tl.request_redraw()
    return True
