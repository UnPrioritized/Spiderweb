"""Extract (Load MIDI group E step 11; plan dev/data/load-midi-plan.md, user's choices in planned-features.md "Load
MIDI"): right-click inside a kept Select box -> the loaded notes whose NOTE-ON is in the box (keys + time), from the
highlighted + shown tracks (all tracks when none is highlighted), taken whole, become one plain notes shape, and
those notes are hidden in the file (drawn, heard and exported without them). The box belongs to the shape
(sh["extract"]): delete it or undo and the notes come back; a copy of it hides nothing (sliced.fresh_marks).

The shape's notes are "as loaded": channels kept (pinned slots, own_slots), overlaps kept (held), velocities their
own; boxes of two Extracts never overlap (a note can only be in one)."""

import threading
from tkinter import messagebox

import numpy as np

from files.lang import tr, trn
from files.midi_read import Cancelled, MidiError
from notes.custom import notes_shape
from notes.loaded import EXTRACT_ASK, EXTRACT_MOST, box_key, boxes_meet, gather_box, set_boxes, shape_boxes
from roll.roll_loaded import shown_tracks
from roll.roll_shared import RULER_TOP


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


def sync_extract(app):
    """The loaded notes hidden = what the shapes' Extract boxes took (after any change to the shapes: undo, delete,
    a project opened). Drawn again, and heard again from where it plays."""
    lm = app.loaded
    if lm is None:
        return
    try:
        changed = set_boxes(lm, shape_boxes(app.shapes))
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


def extract_loaded(app):
    """Right-click → Extract loaded notes: read on a thread (the status line says how far), then asked."""
    if not can_extract(app) or getattr(app, "_extracting", None):
        return
    lm = app.loaded
    order, _ = shown_tracks(app, lm)
    tracks = sorted(order) if order is not None else [n for n, t in enumerate(lm.tracks) if t.notes]
    boxes = boxes_now(app)
    if not boxes or not tracks:
        return
    new = [{"t0": a, "t1": b, "k0": c, "k1": d, "tracks": tracks} for a, b, c, d in boxes]
    if any(boxes_meet(a, b) for a in new for b in shape_boxes(app.shapes)):
        messagebox.showerror(tr("app.spiderweb_2"), tr("extract.overlap"), parent=app)
        return
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
        if e is not None:
            raise e
        finish(app, lm, new, job["got"])
    threading.Thread(target=run, daemon=True, name="extract loaded notes").start()
    poll()


def finish(app, lm, new, got):
    """The notes read: asked, then the shape made (one undo step)."""
    n = sum(len(g["s"]) for g in got)
    if not n:
        app.status.config(text=tr("extract.none"))
        return
    if n > EXTRACT_MOST:
        messagebox.showerror(tr("app.spiderweb_2"), tr("extract.too_many", n=f"{n:,}", most=f"{EXTRACT_MOST:,}"),
                             parent=app)
        return
    text = trn("extract.ask", n, n=f"{n:,}") + (tr("extract.ask_many") if n > EXTRACT_ASK else "")
    if not messagebox.askokcancel(tr("app.spiderweb_2"), text, parent=app, icon="warning" if n > EXTRACT_ASK
                                  else "question"):
        return
    s, e, lane, vel = (np.concatenate([g[k] for g in got]) for k in ("s", "e", "lane", "vel"))
    ch = lane >> 8
    use10 = bool((ch == 9).any())  # (channel 10 kept as channel 10: the shape's slots count it, mask.slot_rule)
    slot = ch if use10 else np.where(ch > 9, ch - 1, ch)
    scale = app.ppq / lm.ppq  # (the project uses the file's PPQ: 1)
    rows = np.column_stack([np.rint(s * scale), np.maximum(1, np.rint((e - s) * scale)), lane & 255, vel,
                            slot]).astype(np.int64)
    sh = notes_shape(rows, app.ppq, tr("extract.name"))
    sh.update(own_slots=True, slots10=use10, held=True)
    sh["extract"] = {"boxes": [[b["t0"], b["t1"], b["k0"], b["k1"]] for b in new], "tracks": new[0]["tracks"]}
    found = {box_key(b): g for b, g in zip(new, got)}
    app.push_undo(name=tr("extract.step"))
    app.shapes.append(sh)
    set_boxes(lm, shape_boxes(app.shapes), found)  # (read already: hidden at once)
    app.select(len(app.shapes) - 1)
    app.shapes_changed()
    app.status.config(text=trn("extract.done", n, n=f"{n:,}"))
