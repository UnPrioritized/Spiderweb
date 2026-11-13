"""The track list's pictures and strips (moved out of track_list.py): a loaded MIDI's rows read exactly (row_mask,
small_mask, RowJob), the squares painted, shapes' tracks (shape_ids, moved_to), the main toolbar's Multiple tracks
toggle (build_onion, sync_onion), the strip under the list (build_strip), its picker (BehindWindow), cut_text."""
from collections import deque
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from roll import roll_loaded as RL
from window import look
from window.widgets import Scrub, Tooltip, good, remember_place

TILE = 512  # px: a loaded MIDI's rows are read in blocks this wide along the song, kept while the zoom stays, and
# the next ones read ahead in the background (user 2026-10-24, Shinki: each new picture read every row again,
# ~100 ms stops while scrolling)
SQUARE = 256  # px: the rows, All tracks and Conductor rows are painted in squares this wide (paint, tiled)


def row_mask(lm, r, ax, bx, W, ch=None):
    """A loaded track's notes exactly: its keys klo..khi x W pixels, the colour group (COLOURS_16) of the channel
    sounding there, -1 none (False: unreadable). ch: that channel alone (a folder's sub-row)."""
    t = lm.tracks[r]
    try:
        tab, _ = RL.exact_tab(lm, ax, bx, W, t.klo, t.khi - t.klo + 1, tracks=(r,),
                              only=None if ch is None else {r: ch})  # (ch: a channel or a part's set)
    except Exception:  # (a file that changed / went: the small picture stays)
        RL.write_log_now()
        return False
    return False if tab is None else np.where(tab >= 0, tab // 32, -1).astype(np.int8)


def squares(o, n):
    """The squares (SQUARE px) showing pixels o .. o + n - 1, and those with one more on each side (painted ahead
    so scrolling finds them ready)."""
    a, b = max(0, o) // SQUARE, max(0, o + n - 1) // SQUARE + 1
    return range(a, b), range(max(0, a - 1), b + 1)


def small_mask(lm, t, cx, aw, sx):
    """The same from the loading pass's small picture (keys klo.. x up to PIC_COLS columns), or None."""
    if not t.pic_cols:
        return None
    tpp = lm.ppq / sx  # (ticks a pixel)
    ta = (np.arange(aw) + cx) * tpp
    step = lm.G * t.pic_pf
    c0 = np.floor((ta - t.pic_glo * lm.G) / step).astype(np.int64)
    c1 = np.maximum(np.ceil((ta + tpp - t.pic_glo * lm.G) / step).astype(np.int64), c0 + 1)
    on = np.flatnonzero((c1 > 0) & (c0 < t.pic_cols))
    if not len(on):
        return None
    a = np.clip(c0[on], 0, t.pic_cols - 1)
    joined = tpp >= step
    end = int(np.clip(c1[on[-1]], 1, t.pic_cols)) if joined else int(a[-1]) + 1
    # (only the columns shown unpacked: the whole track's every redraw was most of the All tracks row's time at
    # 2788 tracks, Shinki 2026-10-28)
    a0 = int(a[0])
    b0 = a0 >> 3
    bits = np.unpackbits(t.pic[:, b0:(end + 7) >> 3], axis=1)[:, a0 - (b0 << 3):end - (b0 << 3)].astype(bool)
    a = a - a0
    got = np.logical_or.reduceat(bits, a, axis=1) if joined else bits[:, a]
    out = np.zeros((t.khi - t.klo + 1, aw), bool)
    out[t.pic_klo - t.klo:t.pic_klo - t.klo + len(got), on] = got
    return out


class RowJob:
    """A loaded MIDI's rows' blocks [(track, block, channel or None)] read exactly on a background thread, in order
    (stop = give up); shown = the ones on screen blocky."""

    def __init__(self, lm, place, todo, ax, shown):
        self.lm, self.place, self.todo, self.ax, self.shown = lm, place, todo, ax, shown
        self.stop = self.done = False
        self.out = deque()  # ((track, block, channel), mask) as read (TrackList.take_rows)

    def run(self):
        for r, j, c in self.todo:
            if self.stop:
                break
            self.out.append(((r, j, c), row_mask(self.lm, r, self.ax, -float(j * TILE), TILE, c)))
        self.done = True


def shape_ids(shapes, owners):
    """id() of the shapes numbered in owners (the shapes themselves: their numbers shift on deleting)."""
    return {id(shapes[i]) for i in np.unique(owners).tolist() if 0 <= i < len(shapes)}


def moved_to(shapes, owners, slots, ids, slot):
    """The track (slot) now holding most notes of the shapes with these ids (shape_ids): slot itself while it holds
    any of them (user 2026-10-26: a 15-colour shape's highlighted track holding fewer than its others made the
    highlight jump at every edit), None when none of them has notes. owners / slots: each note's shape / track."""
    known = [i for i, sh in enumerate(shapes) if id(sh) in ids]
    on = slots[np.isin(owners, known)] if known else slots[:0]
    if not len(on):
        return None
    if (on == slot).any():
        return slot
    where, counts = np.unique(on, return_counts=True)
    return int(where[counts.argmax()])


def build_onion(app, parent):
    """The main toolbar's Multiple tracks toggle + its ▾ list (user 2026-10-23, like Domino's button: on = the
    picked tracks show faintly behind the highlighted one, off = that track alone). Greyed while no track is
    highlighted (sync_onion)."""
    b = ttk.Checkbutton(parent, text=tr("tracks.onion"), variable=app.onion, style="Toolbutton")
    Tooltip(b, tr("tracks.onion_tip"))
    from window.tool_picker import arrow_picture  # (the drawing tools' drawn arrow: the "▾" letter sat left / low)
    pics = arrow_picture(app.scale), arrow_picture(app.scale, look.ICON_OFF)
    more = ttk.Menubutton(parent, image=(pics[0], "disabled", pics[1]), style="Toolbutton")
    more.pics = pics  # (kept, or Tk forgets the pictures)
    menu = tk.Menu(more, tearoff=False)
    tl = lambda: app.track_list  # noqa: E731  (made after the toolbar)
    # (All tracks first: the list's All tracks row, which does the same, is at its top too; user 2026-10-26)
    menu.add_command(label=tr("tracks.onion_all"), command=lambda: tl().show_all())
    menu.add_separator()
    menu.add_command(label=tr("tracks.onion_previous"), command=lambda: tl().behind_near(-1))
    menu.add_command(label=tr("tracks.onion_next"), command=lambda: tl().behind_near(1))
    menu.add_command(label=tr("tracks.onion_pick"), command=lambda: tl().open_picker())
    menu.add_command(label=tr("tracks.onion_alike"), command=lambda: tl().behind_alike())
    more.config(menu=menu)
    Tooltip(more, tr("tracks.onion_more_tip"))
    return b, more


def sync_onion(app):
    on = getattr(app, "track_list", None) is not None and app.track_list.current is not None
    # (the strip's Show multiple tracks… too: picks belong to a track, hunt 2026-10-26)
    for w in tuple(getattr(app, "onion_parts", ())) + tuple(getattr(app, "strip_parts", ())):
        w.state(["!disabled"] if on else ["disabled"])
    tl = getattr(app, "track_list", None)
    if tl is not None and tl.picker:
        tl.picker.fill()


def build_strip(app, parent, tl):
    """The strip above the track list: Show multiple tracks… + Faint [n] % (its snap is the toolbar's, user
    2026-10-23: SnapPicker.use)."""
    strip = ttk.Frame(parent, padding=(2, 2))
    b = ttk.Button(strip, text=tr("tracks.show_multiple"), command=tl.open_picker)
    b.pack(side="left")
    Tooltip(b, tr("tracks.show_multiple_tip"))
    app.strip_parts = (b,)
    sync_onion(app)
    lb = ttk.Label(strip, text=tr("tracks.faint"))
    lb.pack(side="left", padx=(10, 4))
    e = ttk.Entry(strip, textvariable=tl.faint_var, width=4, justify="center")
    e.pack(side="left")
    pc = ttk.Label(strip, text="%")
    pc.pack(side="left", padx=(2, 0))
    e.bind("<Return>", lambda ev: (tl.on_faint(e), "break")[1])
    e.bind("<FocusOut>", lambda ev: tl.on_faint(e))
    Scrub(app, [(e, tl.faint_var, lambda: tl.on_faint(e))], (5, 20, 1), 0, 100, label=lb, drag_box=True,
          no_undo=True)
    for w in (lb, e, pc):
        Tooltip(w, tr("tracks.faint_tip"))
    good(e)
    return strip


class BehindWindow(tk.Toplevel):
    """Show multiple tracks: which tracks the piano roll shows faintly behind the current one. Not modal: changes
    show at once."""

    def __init__(self, tl):
        app = tl.app
        super().__init__(app)
        self.tl = tl
        self.title(tr("tracks.picker_title"))
        self.transient(app)
        box = ttk.Frame(self, padding=8)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("tracks.picker_hint"), foreground=look.HINT, wraplength=round(260 * app.scale),
                  justify="left").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(box)
        row.pack(fill="both", expand=True)
        self.list = tk.Listbox(row, selectmode="multiple", exportselection=False, activestyle="none", height=14,
                               width=26)
        sb = ttk.Scrollbar(row, orient="vertical", command=self.list.yview)
        self.list.config(yscrollcommand=sb.set)
        self.list.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self._drag = None
        self.list.bind("<ButtonPress-1>", self.press)
        self.list.bind("<B1-Motion>", self.drag)
        self.list.bind("<ButtonRelease-1>", lambda e: (setattr(self, "_drag", None), "break")[1])
        self.list.bind("<Double-Button-1>", self.press)  # (a quick second click toggles again, not a double-click)
        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text=tr("tracks.picker_all"), command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(btns, text=tr("tracks.picker_none"), command=lambda: self.set_all(False)).pack(side="left",
                                                                                                 padx=(4, 0))
        ttk.Button(btns, text=tr("tracks.picker_close"), command=self.close).pack(side="right")
        self.bind("<Escape>", lambda e: self.close())
        # Ctrl+A = All (user 2026-10-31: Tk's own list key only highlighted the rows, the piano roll took nothing
        # until the next click); Ctrl+/ too (Tk's), Ctrl+\ = None. "break": not the main window's Select all
        for keys, on in (("<Control-a> <Control-A> <Control-slash>", True), ("<Control-backslash>", False)):
            for k in keys.split():
                self.bind(k, lambda e, on=on: (self.set_all(on), "break")[1])
                self.list.bind(k, lambda e, on=on: (self.set_all(on), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.slots = []
        self.fill()
        remember_place(self, "tracks_picker")

    def fill(self):
        """The rows again (the tracks / the current one changed), the picked ones selected."""
        tl, lb = self.tl, self.list
        slots = [int(s) for s in tl.tracks()]
        top = lb.yview()[0]
        lb.config(state="normal")
        lb.delete(0, "end")
        for row, slot in enumerate(slots):
            text = f"{tl.name_of(row)}  ·  {tl.channel_of(slot)}"
            lb.insert("end", tr("tracks.picker_current", name=text) if slot == tl.current else text)
            if slot == tl.current:
                lb.itemconfig(row, foreground=look.HINT)
            if slot in tl.behind:
                lb.selection_set(row)
        self.slots = slots
        lb.yview_moveto(top)
        lb.config(state="normal" if tl.current is not None else "disabled")  # (All tracks: nothing to pick for)

    def press(self, e):
        """A click toggles its row; dragging on gives every row the mouse crosses the same (user 2026-10-23:
        clicking each one was tedious)."""
        lb = self.list
        lb.focus_set()
        i = lb.nearest(e.y)
        if i < 0 or i >= len(self.slots):
            return "break"
        on = not lb.selection_includes(i)
        (lb.selection_set if on else lb.selection_clear)(i)
        self._drag = (on, i)
        self.picked()
        return "break"

    def drag(self, e):
        if self._drag is None:
            return "break"
        on, last = self._drag
        lb = self.list
        if e.y < 0:  # (past the top / bottom: the list scrolls along)
            lb.yview_scroll(-1, "units")
        elif e.y > lb.winfo_height():
            lb.yview_scroll(1, "units")
        i = lb.nearest(e.y)
        a, b = sorted((last, i))
        for k in range(a, b + 1):
            (lb.selection_set if on else lb.selection_clear)(k)
        self._drag = (on, i)
        self.picked()
        return "break"

    def picked(self):
        self.tl.set_behind({self.slots[i] for i in self.list.curselection() if i < len(self.slots)})

    def set_all(self, on):
        self.tl.set_behind(set(self.slots) if on else set())

    def close(self):
        self.tl.picker = None
        self.destroy()


def cut_text(text, room, measure):
    """text, or as much of it as fits in room pixels followed by "…"."""
    if measure(text) <= room:
        return text
    while text and measure(text + "…") > room:
        text = text[:-1]
    return text + "…"
