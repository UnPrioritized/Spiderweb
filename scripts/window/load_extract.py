"""Extract (Load MIDI group E step 11; plan dev/data/load-midi-plan.md, user's choices in planned-features.md "Load
MIDI"): right-click inside a kept Select box -> the loaded notes whose NOTE-ON is in the box (keys + time), from the
highlighted + shown tracks (all tracks when none is highlighted), taken whole, become one plain notes shape, and
those notes are hidden in the file (drawn, heard and exported without them). The box belongs to the shape
(sh["extract"]): delete it or undo and the notes come back; a copy of it hides nothing (sliced.fresh_marks).

The shape's notes are "as loaded": in the file's track they came from on their own channel (group D: laid there,
"into" + "into_ch"), overlaps kept (held), velocities their own; boxes of two Extracts never overlap (a note can
only be in one). One left as it was = the export keeps the file's own bytes (untouched)."""

import hashlib
import threading
from tkinter import messagebox

import numpy as np

from files.lang import tr, trn
from files.midi_read import Cancelled, MidiError
from files.own_tracks import FILE0
from notes.custom import notes_shape
from notes.engine import own_slot
from notes.loaded import (EXTRACT_ASK, EXTRACT_MOST, box_key, boxes_meet, gather_box, group_boxes, set_boxes,
                          shape_boxes)
from roll.roll_draw import TRACK_FADE
from roll.roll_loaded import shown_tracks
from roll.roll_shared import RULER_TOP


def notes_sig(rows):
    """A fingerprint of notes (start, end, key, velocity, slot), in any order."""
    rows = np.ascontiguousarray(np.asarray(rows, np.int64).reshape(-1, 5))
    rows = rows[np.lexsort(rows.T[::-1])] if len(rows) else rows
    return hashlib.blake2b(rows.tobytes(), digest_size=16).hexdigest()


def own_slots_of(pairs):
    """The slots of (track * 16 + channel) pairs laid in the loaded MIDI's tracks (engine.own_slot)."""
    return np.array([own_slot(FILE0 + int(p) // 16, int(p) % 16) for p in pairs], np.int64)


def untouched(shapes, rendered):
    """The Extracts left exactly as they were made (their notes = the fingerprint taken then): (their boxes' keys,
    their shape numbers). The export keeps those notes in the file as they are, byte for byte (user 2026-10-26:
    extracting alone changes nothing)."""
    keys, owners = [], []
    for i, sh in enumerate(shapes):
        ex = sh.get("extract")
        if not isinstance(ex, dict) or not ex.get("sig"):
            continue
        mine = rendered[rendered[:, 5] == i, :5]
        if len(mine) and notes_sig(mine) == ex["sig"]:
            keys += [box_key({"t0": b[0], "t1": b[1], "k0": b[2], "k1": b[3], "tracks": ex["tracks"]})
                     for b in ex["boxes"]]
            owners.append(i)
    return keys, owners


def hidden_boxes(app):
    """Every box whose loaded notes are hidden: the shapes' Extract boxes, then Delete loaded notes' (app.deleted)."""
    return shape_boxes(app.shapes) + group_boxes(app.deleted)


def can_extract(app):
    """A loaded MIDI and a kept Select box (or ruler band) on the piano roll: Extract is offered."""
    return app.loaded is not None and not getattr(app.loaded, "changed", False) and bool(app.roll.kept_box())


def boxes_now(app):
    """The kept boxes as Extract boxes in the file's ticks / keys (a ruler band = every key)."""
    lm = app.loaded
    ppq = lm.ppq / app.ppq
    out = []
    for b0, p0, b1, p1 in app.roll.kept_box():
        lo, hi = sorted((p0, p1))
        k0, k1 = (0, lm.keys - 1) if hi >= RULER_TOP - 0.5 else (max(0, int(np.ceil(lo))), min(lm.keys - 1,
                                                                                               int(np.floor(hi))))
        a, b = sorted((b0, b1))
        t0, t1 = max(0, round(a * app.ppq * ppq)), round(b * app.ppq * ppq)
        if t1 > t0 and k1 >= k0:
            out.append([t0, t1, k0, k1])
    return out


def sync_extract(app, found=None):
    """The loaded notes hidden = what the shapes' Extract boxes took (after any change to the shapes: undo, delete,
    a project opened, an Extract: found = its boxes read). Drawn again, and heard again from where it plays. Boxes
    not read yet (a project opened, reads dropped to save memory) are read in the background first, the status
    line saying so (hunt 2026-10-25: a big box froze the window)."""
    lm = app.loaded
    if lm is None:
        return
    boxes = hidden_boxes(app)
    found = dict(found or {})
    lm._taken.update(found)  # (an Extract's own read: kept even while other boxes are still read)
    need = {box_key(b): b for b in boxes if box_key(b) not in lm._taken}
    if need:
        read_boxes(app, lm, list(need.values()))
        return
    try:
        changed = set_boxes(lm, boxes, found)
    except (OSError, MidiError):  # (the file changed / went: said when drawing / exporting; nothing more hidden)
        return
    if not changed:
        return
    app.roll.request_redraw()
    if hasattr(app, "track_list"):
        app.track_list.request_redraw()
    if app.player.running:  # (its reader read the old ones: started again from where it is)
        app.stop_play()
        app.start_play(keep=True)


def read_boxes(app, lm, boxes):
    """sync_extract's boxes not read yet: read on a thread (status line), then sync_extract again."""
    job = getattr(app, "_box_reading", None)
    if job is not None and job["lm"] is lm and not job["finished"]:
        job["again"] = True  # (the shapes changed meanwhile: looked at again once this read is done)
        return
    job = app._box_reading = {"lm": lm, "done": 0, "total": 1, "got": {}, "error": None, "finished": False,
                              "cancel": False, "again": False}

    def run():
        try:
            for b in boxes:
                job["got"][box_key(b)] = gather_box(lm, b, cancel=lambda: job["cancel"],
                                                    progress=lambda d, n: job.update(done=d, total=max(n, 1)))
        except BaseException as e:  # (said below: nothing more hidden)
            job["error"] = e
        job["finished"] = True

    def poll():
        if not job["finished"]:
            app.busy(tr("extract.reading", pct=int(100 * job["done"] / job["total"])))
            app.after(100, poll)
            return
        if getattr(app, "_box_reading", None) is job:  # (a newer read's record left alone)
            app._box_reading = None
        app.busy(None)
        if job["again"] and app.loaded is lm and job["error"] is None:  # (shapes changed while it read)
            lm._taken.update(job["got"])
            sync_extract(app)
            return
        if app.loaded is not lm or job["error"] is not None:
            if isinstance(job["error"], MemoryError):
                messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=app)
            return
        lm._taken.update(job["got"])
        sync_extract(app, job["got"])
    threading.Thread(target=run, daemon=True, name="read extracted boxes").start()
    poll()


def stop_extract(app):
    """Esc / another project: an Extract still reading stops (nothing made)."""
    job = getattr(app, "_extracting", None)
    if job:
        job["cancel"] = True
    return bool(job)


def extract_loaded(app):
    """Right-click → Extract loaded notes: read on a thread (the status line says how far; Esc stops it), then
    asked."""
    if not can_extract(app):
        return
    if getattr(app, "_extracting", None):
        app.status.config(text=tr("extract.busy"))
        return
    lm = app.loaded
    order, faint = shown_tracks(app, lm)
    if order is not None and TRACK_FADE[0] == 0:  # (picked-behind tracks drawn at 0 %: not seen, not taken)
        order = [n for n in order if n not in faint]
    tracks = sorted(order) if order is not None else [n for n, t in enumerate(lm.tracks) if t.notes]
    boxes = boxes_now(app)
    if not boxes:
        return
    if not tracks:
        app.status.config(text=tr("extract.no_tracks"))
        return
    new = [{"t0": a, "t1": b, "k0": c, "k1": d, "tracks": tracks} for a, b, c, d in boxes]
    if any(boxes_meet(a, b) for a in new for b in hidden_boxes(app)):
        messagebox.showerror(tr("app.spiderweb_2"), tr("extract.overlap"), parent=app)
        return
    job = app._extracting = {"done": 0, "total": 1, "got": None, "error": None, "finished": False, "cancel": False}

    def run():
        try:
            got = job["got"] = []
            for b in new:  # (all boxes together stop soon after EXTRACT_MOST: refused below)
                got.append(gather_box(lm, b, cancel=lambda: job["cancel"], most=EXTRACT_MOST - sum(
                    len(g["s"]) for g in got), progress=lambda d, n: job.update(done=d, total=max(n, 1))))
                if sum(len(g["s"]) for g in got) > EXTRACT_MOST:
                    break
        except BaseException as e:  # (said below)
            job["got"] = None
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
        finish(app, lm, new, job["got"])
    threading.Thread(target=run, daemon=True, name="extract loaded notes").start()
    poll()


def finish(app, lm, new, got):
    """The notes read: asked, then the shape made (one undo step)."""
    n = sum(len(g["s"]) for g in got)
    if n > EXTRACT_MOST:
        messagebox.showerror(tr("app.spiderweb_2"), tr("extract.too_many", n=f"{n:,}", most=f"{EXTRACT_MOST:,}"),
                             parent=app)
        return
    trk, s, e, lane, vel, on = (np.concatenate([g[k] for g in got]) for k in ("trk", "s", "e", "lane", "vel", "on"))
    if len(got) > 1:  # (Ctrl+drag boxes overlapping: each note once, hunt 2026-10-25)
        _, first = np.unique(np.column_stack([trk, on]), axis=0, return_index=True)
        first.sort()
        trk, s, e, lane, vel = (a[first] for a in (trk, s, e, lane, vel))
    n = len(s)
    if not n:
        app.status.config(text=tr("extract.none"))
        return
    text = trn("extract.ask", n, n=f"{n:,}") + (tr("extract.ask_many") if n > EXTRACT_ASK else "")
    if not messagebox.askokcancel(tr("app.spiderweb_2"), text, parent=app, icon="warning" if n > EXTRACT_ASK
                                  else "question"):
        return
    if app.loaded is not lm or any(boxes_meet(a, b) for a in new for b in hidden_boxes(app)):
        # (an undo while it read brought back an Extract of the same notes, hunt 2026-10-25)
        if app.loaded is lm:
            messagebox.showerror(tr("app.spiderweb_2"), tr("extract.overlap"), parent=app)
        return
    ch = lane >> 8
    # the notes stay IN THE FILE'S TRACK they came from, each on its own channel (group D, user 2026-10-26: they
    # came out sorted by channel, the file's order and names lost): one colour per (track, channel), laid there
    pairs, colour = np.unique(trk * 16 + ch, return_inverse=True)
    scale = app.ppq / lm.ppq  # (the project uses the file's PPQ: 1)
    rows = np.column_stack([np.rint(s * scale), np.maximum(1, np.rint((e - s) * scale)), lane & 255, vel,
                            colour.ravel()]).astype(np.int64)
    sh = notes_shape(rows, app.ppq, tr("extract.name"))
    sh.update(own_slots=True, held=True, into=[FILE0 + int(p) // 16 for p in pairs],
              into_ch=[int(p) % 16 for p in pairs])
    sh["extract"] = {"boxes": [[b["t0"], b["t1"], b["k0"], b["k1"]] for b in new], "tracks": new[0]["tracks"],
                     # (its notes as made: left as they are, the export keeps the file's own bytes, user 2026-10-26)
                     "sig": notes_sig(np.column_stack([rows[:, 0], rows[:, 0] + rows[:, 1], rows[:, 2], rows[:, 3],
                                                       own_slots_of(pairs)[colour.ravel()]]))}
    found = {box_key(b): g for b, g in zip(new, got)}
    app.push_undo(name=tr("extract.step"))
    app.shapes.append(sh)
    sync_extract(app, found)  # (read already: hidden at once; playing = heard again without them)
    app.select(len(app.shapes) - 1)
    app.shapes_changed()
    app.status.config(text=trn("extract.done", n, n=f"{n:,}"))


BOX_RING_MOST = 200_000  # loaded notes in a kept box outlined at most (measured: 200k notes' outline ~0.15 s a zoom
# step; more = the box alone shows what Delete takes, the status line counts them)


def box_notes(app):
    """The loaded notes Delete loaded notes would take now (kept box, delete_tracks), as (starts, ends, keys) in the
    project's ticks for the red outline round selected notes (user 2026-10-26, pick C: like shapes); None while
    they're read (on a thread: the piano roll is drawn again when they come), when there are none, or past
    BOX_RING_MOST (said on the status line)."""
    lm = app.loaded
    if not can_extract(app) or getattr(app.roll, "drag", None):
        return None
    tracks = delete_tracks(app, lm)
    boxes = boxes_now(app) if tracks else []
    if not boxes:
        return None
    key = (id(lm), lm.hide_ver, tuple(map(tuple, boxes)), tuple(tracks), app.ppq)
    job = getattr(app, "_box_notes", None)
    if job is not None and job["key"] == key:
        return job["cols"]
    if job is not None:
        job["cancel"] = True
    job = app._box_notes = {"key": key, "cols": None, "cancel": False, "finished": False, "many": 0}
    new = [{"t0": a, "t1": b, "k0": c, "k1": d, "tracks": tracks} for a, b, c, d in boxes]
    scale = app.ppq / lm.ppq

    def run():
        try:
            got, n = [], 0
            for b in new:
                got.append(gather_box(lm, b, cancel=lambda: job["cancel"], most=BOX_RING_MOST - n))
                n += len(got[-1]["s"])
                if n > BOX_RING_MOST:
                    break
            if n > BOX_RING_MOST:
                job["many"] = n
            elif n:
                s, e, lane = (np.concatenate([g[k] for g in got]) for k in ("s", "e", "lane"))
                job["cols"] = (np.rint(s * scale).astype(np.int64), np.rint(e * scale).astype(np.int64),
                               (lane & 255).astype(np.int64))
        except BaseException:  # (stopped, or the file changed: no outline)
            job["cols"] = None
        job["finished"] = True

    def poll():
        if not job["finished"]:
            app.after(50, poll)
            return
        if getattr(app, "_box_notes", None) is job and not job["cancel"]:
            if job["many"]:
                app.status.config(text=tr("delete_loaded.many", n=f"{BOX_RING_MOST:,}"))
            app.roll.request_redraw()
    threading.Thread(target=run, daemon=True, name="loaded notes in the box").start()
    app.after(50, poll)
    return None


def delete_tracks(app, lm):
    """The loaded tracks Delete loaded notes takes (user 2026-10-26, like Domino: the highlighted track only, the
    ones shown behind it are for looking; none highlighted = every track; one of Spiderweb's / yours = none).
    Damaged tracks never (only ever copied as they are)."""
    cur = app.track_list.current
    whole = [n for n, t in enumerate(lm.tracks) if t.notes and n < len(lm.mf.tracks) and lm.mf.tracks[n].whole]
    if cur is None:
        return whole
    return [-1 - cur] if cur < 0 and -1 - cur in whole else []


def delete_loaded(app, shapes_too=False):
    """Delete loaded notes (right-click in a kept Select box; the Delete key: the selected shapes too, one undo
    step): the loaded notes whose note-on is in the boxes (delete_tracks) are hidden like an Extract's, with no shape
    (app.deleted: saved, undone; group D). No question, like deleting shapes (user). Read on a thread first (Esc
    stops it). A box over an Extract that stays = refused (the notes are that shape's)."""
    lm = app.loaded
    gone = set(app.sels) if shapes_too else set()
    tracks = delete_tracks(app, lm) if can_extract(app) else []
    boxes = boxes_now(app) if tracks else []
    if not boxes:
        if gone:
            app.delete_selected()
        elif can_extract(app):
            app.status.config(text=tr("delete_loaded.none"))
        return
    if getattr(app, "_extracting", None):
        app.status.config(text=tr("extract.busy"))
        return
    new = [{"t0": a, "t1": b, "k0": c, "k1": d, "tracks": tracks} for a, b, c, d in boxes]
    staying = shape_boxes([sh for i, sh in enumerate(app.shapes) if i not in gone])
    if any(boxes_meet(a, b) for a in new for b in staying):
        messagebox.showerror(tr("app.spiderweb_2"), tr("delete_loaded.overlap"), parent=app)
        return
    step = None
    if gone:  # (the Delete key: the shapes go AT ONCE, the step the read's notes join; a selected Extract's notes
        # are back in the file first, so they're deleted too, like the rest of the box; Ctrl+Z mid-read = that step
        # undone and the read's notes dropped, hunt 2026-10-26)
        app.roll.end_typing()  # (first: it refreshes the panel, which must still see the old shapes)
        app.push_undo(name=tr("app.delete"))
        for i in sorted(gone, reverse=True):
            del app.shapes[i]
        app.select_many([], None)
        app.shapes_changed()
        step = app.undo_stack[-1]
    job = app._extracting = {"done": 0, "total": 1, "got": None, "error": None, "finished": False, "cancel": False}

    def run():
        try:
            job["got"] = [gather_box(lm, b, cancel=lambda: job["cancel"],
                                     progress=lambda d, n: job.update(done=d, total=max(n, 1))) for b in new]
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
        finish_delete(app, lm, new, job["got"], step)
    threading.Thread(target=run, daemon=True, name="delete loaded notes").start()
    poll()


def finish_delete(app, lm, new, got, step=None):
    """The boxes read: the notes hidden, in one undo step: step (the Delete key's, its shapes already deleted: only
    while it's still the newest step, else something changed meanwhile and nothing more happens) or a new one."""
    if step is not None and (not app.undo_stack or app.undo_stack[-1][0] is not step[0]):  # (its shapes' state:
        # a step renamed in place stays the same one)
        return
    old = {int(t) * (1 << 40) + int(o) for t, o in zip(lm.h_trk.tolist(), lm.h_on.tolist())}
    taken = {int(t) * (1 << 40) + int(o) for g in got for t, o in zip(g["trk"].tolist(), g["on"].tolist())}
    n = len(taken - old)  # (notes newly gone: ones hidden already, e.g. a box over a box deleted before, not counted)
    if not n:
        if step is None:
            app.status.config(text=tr("delete_loaded.none"))
        return
    if step is None:
        app.roll.end_typing()  # (first: it refreshes the panel, which must still see the old shapes)
        app.push_undo(name=tr("delete_loaded.step"))
    if n:
        app.deleted = app.deleted + [{"boxes": [[b["t0"], b["t1"], b["k0"], b["k1"]] for b in new],
                                      "tracks": new[0]["tracks"]}]
    sync_extract(app, {box_key(b): g for b, g in zip(new, got)})
    app.shapes_changed()
    if n:
        app.status.config(text=trn("delete_loaded.done", n, n=f"{n:,}"))
