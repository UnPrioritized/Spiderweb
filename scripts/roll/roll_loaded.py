"""A loaded MIDI's notes on the piano roll (Load MIDI), painted under Spiderweb's own notes in the notes' picture.
They never go through note_rects: 100 M - 2 B notes would take seconds a frame. Each picture is a key x pixel table
of colour numbers (roll_draw's note colours: the channel's, shaded by velocity):
- EXACT pictures: every track read from its checkpoint before the picture's start (from memory or from the file),
  the top track first, each pixel painted once (midi_index.cover); covered tracks are read too, their notes'
  starts / ends shading the notes above (marks 4, user 2026-10-25: see the activity under covering tracks). Made a screen WIDER than the view on each side
  (MARGIN) for every key, so scrolling sideways / up and down only moves along them (the user saw notes switch
  while scrolling, 2026-10-24: never show two looks of the same notes). Made in the frame itself when that's quick
  (INLINE_S, guessed from the bytes to read), else on a background thread, and again further on before the view
  reaches an edge;
- while a slow one is being made (zoomed on a giant song): the last exact picture STRETCHED to the new zoom where
  it reaches (stand_in), the OVERVIEW elsewhere: from the loading pass's grid (which track sounds highest in each
  key x 1/16 beat), a few ms at any size. Far out on a giant song (EXACT_MOST_S) the overview alone."""

import math
import os
import threading
import time

import numpy as np

from files import midi_index, speed
from files.midi_index import C_SIZE, C_STOP, C_TICK, C_RUN, QS
from files.midi_read import MidiError
from roll.roll_draw import GHOST_BASE, TRACK_FADE, note_tables
from roll.roll_shared import COLOURS_16

REST_MS = 120  # the view resting this long = a slow exact picture is started (a quick one needs no rest)
POLL_MS = 30  # how often a running job is looked at
SPANS = 1 << 15  # spans painted at a time (cover's buffer)
SHADE = 25  # the overview's shade (no velocity there): a note of velocity 100's
MARGIN = 1.0  # exact pictures reach this many screens past each side of the view
INLINE_S = 0.03  # an exact picture guessed this quick is made at once, in the frame
STRIP_S = 0.05  # ... a strip scrolled into past one's edge (a slower frame now and then beats notes switching)
EXACT_MOST_S = 8.0  # ... this slow at most, on the background thread; slower = the overview alone
KEEP = 3  # exact pictures kept (newest first)
RATE = {"memory": 1.5e9, "file": 0.8e9}  # bytes a second read + painted, until pictures have been timed
RATE_PLAIN = 0.5e6  # ... without the speed-ups (plain Python)
TIMED_LEAST = 4 << 20  # pictures reading fewer bytes don't time the rate (their fixed costs would)

class Exact:
    """One exact picture: rows = every key of the file (0 .. keys - 1) x W columns, pixel x of tick t =
    round(t * ax + bx); style = what else it depends on (the MIDI, tracks shown, faint ones, fade)."""
    __slots__ = ("style", "ax", "bx", "W", "tab", "marks")

    def __init__(self, style, ax, bx, W, tab, marks):
        self.style, self.ax, self.bx, self.W, self.tab, self.marks = style, ax, bx, W, tab, marks


class LoadedNotes:
    """Mixed into PianoRoll."""

    def loaded_view(self, region):
        """(lm, key, geometry) of what the loaded notes' picture shows for the note area now; None = nothing."""
        lm = self.app.loaded
        if lm is None or not lm.notes or self.sx is None:
            return None
        kb, top = int(self.kb_w), int(self.ruler_h)
        w, h = self.winfo_width(), self.winfo_height()
        W = w - kb
        if W < 1 or h - top < 1:
            return None
        p_lo, p_hi = self.visible_pitches(h)
        p_lo, p_hi = max(0, p_lo - 1), min(self.app.keys - 1, lm.keys - 1, p_hi + 1)
        ax = self.sx / lm.ppq
        bx = self.kb_w - self.view_t * self.sx - kb  # (pixel c of the table = screen x kb + c)
        pal = getattr(self.app, "picture_pal", None)
        order, faint = shown_tracks(self.app, lm)
        key = (id(lm), lm.hide_ver, W, kb, top, h, round(ax, 12), round(bx, 6), p_lo, p_hi, tuple(pal or ()), order,
               frozenset(faint), TRACK_FADE[0], loud_on(self.app))
        return lm, key, (ax, bx, W, p_lo, p_hi, kb, top, order, faint)

    def loaded_mark(self):
        """What the loaded notes' part of the picture depends on besides the view (roll_draw.redraw's key)."""
        lm = self.app.loaded
        if lm is None:
            return None
        order, faint = shown_tracks(self.app, lm)
        return (id(lm), lm.hide_ver, getattr(self, "loaded_version", 0), order, faint, TRACK_FADE[0],
                loud_on(self.app))

    def paint_loaded(self, img, region):
        """The loaded notes into img, the picture of region = (x0, y0, x1, y1) of the screen (paint_region)."""
        got = self.loaded_view(region)
        if got is None:
            return
        lm, key, (ax, bx, W, p_lo, p_hi, kb, top, order, faint) = got
        nk = p_hi - p_lo + 1
        if nk < 1 or order == ():
            return
        mark = (key, getattr(self, "loaded_version", 0), self.app.player.running)  # (playing starts: read ahead)
        now = getattr(self, "_loaded_now", None)
        if now is None or now[0] != mark:  # (one frame's strips share it)
            now = self._loaded_now = (mark, *self.loaded_tabs(lm, key, ax, bx, W, p_lo, nk, order, faint))
        tab, marks = now[1], now[2]
        x0, y0, x1, y1 = region
        keys = np.arange(p_lo, p_hi + 1)
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        row0 = np.round((keys + 0.5) * ay + by).astype(np.int64)
        row1 = np.maximum(np.round((keys - 0.5) * ay + by).astype(np.int64), row0 + 1)
        rgb = note_tables(getattr(self.app, "picture_pal", None))[1]
        c0, c1 = max(0, x0 - kb), min(W, x1 - kb)
        if c1 <= c0:
            return
        fast = speed.loops()
        if fast:
            fast.loaded_pixels(img, x0, y0, tab, marks, c0, c1, kb, row0, row1, rgb)
        else:
            loaded_pixels(img, x0, y0, tab, marks, c0, c1, kb, row0, row1, rgb)

    # ------------------------------------------------------------------ which picture

    def loaded_style(self, lm, order, faint):
        """What an exact picture depends on besides its place (Exact.style; the velocity pane looks pictures up by
        it too, hunt 2026-10-25). loud_on stays LAST (ExactJob reads style[-1])."""
        return id(lm), lm.hide_ver, order, frozenset(faint), TRACK_FADE[0], loud_on(self.app)

    def loaded_tabs(self, lm, key, ax, bx, W, p_lo, nk, order, faint):
        """(tab, marks) of the view: keys p_lo .. + nk x W columns."""
        style = self.loaded_style(lm, order, faint)
        rows = slice(p_lo, p_lo + nk)
        got = self._exact_for(style, ax, bx, W)
        if got is not None:
            e, d = got
            self._keep_ahead(lm, style, e, d, ax, bx, W, order, faint)
            return cut(e, rows, d, W)
        M = int(W * MARGIN)
        rate = rate_of(lm)
        now_ok = speed.loops() is not None and not changed(self, lm)  # (plain Python: never in the frame, hunt)
        got = self._extend(lm, style, ax, bx, W, M, rate, order, faint) if now_ok else None
        if got is not None:
            e, d = got
            self._keep_ahead(lm, style, e, d, ax, bx, W, order, faint)
            return cut(e, rows, d, W)
        if now_ok and guess_bytes(lm, order, ax, bx + M, W + 2 * M) / rate <= INLINE_S:
            e = self._make_now(lm, style, ax, bx + M, W + 2 * M, order, faint)
            if e is not None:
                return cut(e, rows, M, W)
        slow = guess_bytes(lm, order, ax, bx, W) / rate
        if changed(self, lm):  # (the file changed / went: what's made stays, nothing more is read)
            slow = EXACT_MOST_S if any(e.style == style for e in getattr(self, "_loaded_exact", ())) else math.inf
        elif now_ok and slow <= INLINE_S:
            e = self._make_now(lm, style, ax, bx, W, order, faint)
            if e is not None:
                self._want(lm, style, ax, bx, W, order, faint, rest=False)  # (the wide one next, behind)
                return cut(e, rows, 0, W)
        over = getattr(self, "_loaded_over", None)
        if over is None or over[0] != key:
            made = (overview_tab(lm, ax, bx, W, p_lo, nk) if order is None else
                    pictures_tab(lm, order, faint, ax, bx, W, p_lo, nk))
            over = self._loaded_over = (key, *made)
        if slow > EXACT_MOST_S:
            return over[1], over[2]
        if not changed(self, lm):
            self._want(lm, style, ax, bx, W, order, faint, rest=True)
        old = next((e for e in getattr(self, "_loaded_exact", ()) if e.style == style), None)
        if old is None:
            return over[1], over[2]
        tab, marks = over[1].copy(), over[2].copy()
        stand_in(old, ax, bx, W, rows, tab, marks)
        return tab, marks

    def _exact_for(self, style, ax, bx, W):
        """(an exact picture holding the whole view, its column at the view's left) or None."""
        for e in getattr(self, "_loaded_exact", ()):
            if e.style != style or e.ax != ax:
                continue
            d = e.bx - bx
            r = round(d)
            if abs(d - r) < 1e-6 and 0 <= r and r + W <= e.W:
                return e, int(r)
        return None

    def _extend(self, lm, style, ax, bx, W, M, rate, order, faint):
        """Scrolled past an exact picture's edge (faster than the next could be made): the uncovered strip made at
        once and joined on when that's quick (the pixels one whole picture has, but where one track sounds a key on
        two channels at once: either channel's colour). (picture, the view's column in it) or None."""
        for e in getattr(self, "_loaded_exact", ()):
            if e.style != style or e.ax != ax:
                continue
            d = e.bx - bx
            r = round(d)
            if abs(d - r) >= 1e-6 or not -W < r < e.W or (r < 0 and r + W > e.W):
                continue
            r = int(r)
            s0, s1 = (r - W // 8, 0) if r < 0 else (e.W, r + W + W // 8)
            if guess_bytes(lm, order, ax, e.bx - s0, s1 - s0) / rate > STRIP_S:
                return None
            began, took = time.perf_counter(), [0]
            try:
                tab, marks = exact_tab(lm, ax, e.bx - s0, s1 - s0, 0, lm.keys, tracks=order, faint=faint, took=took,
                                       loud=style[-1])
            except Exception:
                write_log_now()
                return None
            if tab is None:
                return None
            learn_rate(lm, took[0], time.perf_counter() - began)
            if r < 0:
                tab, marks, nbx, r = (np.concatenate([tab, e.tab], axis=1), np.concatenate([marks, e.marks], axis=1),
                                      e.bx - s0, r - s0)
            else:
                tab, marks, nbx = np.concatenate([e.tab, tab], axis=1), np.concatenate([e.marks, marks], axis=1), e.bx
            most = W + 4 * M  # (kept round the view, the far side let go)
            if tab.shape[1] > most:
                a = min(max(0, r + W // 2 - most // 2), tab.shape[1] - most)
                tab, marks, nbx, r = tab[:, a:a + most].copy(), marks[:, a:a + most].copy(), nbx - a, r - a
            n = Exact(style, ax, nbx, tab.shape[1], tab, marks)
            self._loaded_exact = [n] + [o for o in self._loaded_exact if o is not e][:KEEP - 1]
            return n, r
        return None

    def _remember(self, e):
        self._loaded_exact = [e] + [o for o in getattr(self, "_loaded_exact", ()) if o is not e][:KEEP - 1]

    def _make_now(self, lm, style, ax, bx, W, order, faint):
        began, took = time.perf_counter(), [0]
        try:
            tab, marks = exact_tab(lm, ax, bx, W, 0, lm.keys, tracks=order, faint=faint, took=took, loud=style[-1])
        except Exception:  # (a file that changed / went: the overview stays; told when exporting)
            write_log_now()
            return None
        if tab is None:
            return None
        learn_rate(lm, took[0], time.perf_counter() - began)
        e = Exact(style, ax, bx, W, tab, marks)
        self._remember(e)
        return e

    def _keep_ahead(self, lm, style, e, d, ax, bx, W, order, faint):
        """The next wide picture started before the view reaches this one's edge (where notes go on past it)."""
        soon = int(W * MARGIN) * 3 // 4
        short_left = d < soon and -e.bx / ax > lm.first
        short_right = e.W - d - W < soon and (e.W - e.bx) / ax < lm.end
        if self.app.player.running:  # (playing: only ahead matters, and a page turn moves the view a little over a
            # screen at once, so the picture must reach past it beforehand, else the overview shows first and the
            # notes shift when the exact one comes: user 2026-10-25, Lagtester bars ~150 / 199)
            if e.W - d - W < W + W // 4 and (e.W - e.bx) / ax < lm.end:
                self._want(lm, style, ax, bx, W, order, faint, rest=False, ahead=1)
            return
        if short_left != short_right:  # (reaching further the way the view goes)
            self._want(lm, style, ax, bx, W, order, faint, rest=False, ahead=1 if short_right else -1)
        elif short_left:
            self._want(lm, style, ax, bx, W, order, faint, rest=False)

    def _want(self, lm, style, ax, bx, W, order, faint, rest, ahead=0):
        """A wide exact picture round this view on the background thread (rest: once the view rests; ahead = +1 /
        -1: reaching further right / left)."""
        job = getattr(self, "_loaded_job", None)
        if job is not None and not job.stop and job.style == style and job.ax == ax and job.covers(bx, W):
            return
        if job is not None:
            job.stop = True
        self._loaded_job = None
        timer = getattr(self, "_loaded_timer", None)
        if timer:
            self.after_cancel(timer)
            self._loaded_timer = None
        M = int(W * MARGIN)
        parts = [(bx + (M // 2 if ahead > 0 else M * 3 // 2 if ahead < 0 else M), W + 2 * M)]
        if rest:  # (slow: this view first, so it's exact as soon as can be)
            parts.insert(0, (bx, W))
        go = lambda: self._start_exact(ExactJob(lm, style, ax, parts, order, faint))
        if rest:
            self._loaded_timer = self.after(REST_MS, go)
        else:
            go()

    def _start_exact(self, job):
        self._loaded_timer = None
        was = getattr(self, "_loaded_job", None)
        if was is not None:
            was.stop = True
        self._loaded_job = job
        threading.Thread(target=job.run, daemon=True, name="loaded notes").start()
        self.after(POLL_MS, self._poll_exact)

    def _poll_exact(self):
        job = getattr(self, "_loaded_job", None)
        if job is None:
            return
        made = []
        while job.made:
            made.append(job.made.pop(0))
        if made and not job.stop:
            for e in made:
                self._remember(e)
            self.loaded_version = getattr(self, "loaded_version", 0) + 1
            self.request_redraw()
        if job.done:
            self._loaded_job = None
            changed(self, job.lm)
            return
        self.after(POLL_MS, self._poll_exact)

    def forget_loaded(self):
        """A new loaded MIDI (or none): the pictures made for the old one go."""
        job = getattr(self, "_loaded_job", None)
        if job is not None:
            job.stop = True
        timer = getattr(self, "_loaded_timer", None)
        if timer:
            self.after_cancel(timer)
        self._loaded_job = self._loaded_timer = self._loaded_over = self._loaded_now = None
        self._loaded_exact = []
        self.loaded_version = getattr(self, "loaded_version", 0) + 1


def cut(e, rows, d, W):
    """The view's part of an exact picture (own arrays: the compiled painter takes plain ones)."""
    return np.ascontiguousarray(e.tab[rows, d:d + W]), np.ascontiguousarray(e.marks[rows, d:d + W])


def stand_in(e, ax, bx, W, rows, tab, marks):
    """An exact picture made at another zoom laid over the view's tab / marks where it reaches: each column takes
    the pixel under its middle there, or when that's empty, any note over the column's width."""
    c = np.arange(W, dtype=np.float64)
    f = e.ax / ax
    mid = np.floor((c - bx) * f + e.bx + 0.5).astype(np.int64)
    j0 = np.floor((c - 0.5 - bx) * f + e.bx + 0.5).astype(np.int64)
    j1 = np.maximum(np.floor((c + 0.5 - bx) * f + e.bx + 0.5).astype(np.int64), j0 + 1)
    mid = np.clip(mid, j0, j1 - 1)
    on = (j0 >= 0) & (j1 <= e.W)
    if not on.any():
        return
    idx = np.flatnonzero(on)
    src, sm = e.tab[rows], e.marks[rows]
    t, m = src[:, mid[idx]], sm[:, mid[idx]]
    if f > 1:  # (zoomed out from it: several of its columns a column)
        a, end = j0[idx], int(j1[idx[-1]])
        t = np.where(t >= 0, t, np.maximum.reduceat(src[:, :end], a, axis=1))
        m = m | np.bitwise_or.reduceat(sm[:, :end], a, axis=1)
    tab[:, idx] = t
    marks[:, idx] = m


def spread(lm):
    """(ticks a bucket, bytes before each bucket, per track: first, end, a checkpoint stretch's bytes) of the
    tracks with notes: how many bytes an exact picture reads, guessed without a loop over the tracks."""
    got = getattr(lm, "_spread", None)
    if got is not None:
        return got
    step = max(lm.last_tick, 1) // 65536 + 1
    nb = max(lm.last_tick, 1) // step + 2
    ticks, sizes, first, end, lead = [], [], [], [], []
    for t in lm.tracks:
        if not t.notes or not len(t.cp_off):
            continue
        size = np.diff(np.append(t.cp_off, t.limit))
        ticks.append(t.cp_tick // step)
        sizes.append(size)
        first.append(t.first)
        end.append(t.end)
        lead.append(size.mean())
    h = np.bincount(np.concatenate(ticks), weights=np.concatenate(sizes), minlength=nb) if ticks else np.zeros(nb)
    got = lm._spread = (step, np.concatenate([[0.0], np.cumsum(h)]), np.array(first, np.int64),
                        np.array(end, np.int64), np.array(lead, np.float64))
    return got


def guess_bytes(lm, order, ax, bx, W):
    """About how many bytes an exact picture of W columns reads."""
    if order is not None:
        return view_bytes(lm, order, ax, bx, W)
    step, cum, first, end, lead = spread(lm)
    t0 = int(np.floor((-0.5 - bx) / ax))
    t1 = int(np.ceil((W + 0.5 - bx) / ax))
    n = len(cum) - 1
    g0, g1 = min(max(t0 // step, 0), n), min(max(t1 // step + 1, 0), n)
    return float(cum[g1] - cum[g0]) + float(lead[(first <= t1) & (end > t0)].sum())


def rate_of(lm):
    """Bytes a second exact pictures are read + painted at: as timed, else the first guess (plain Python: far
    slower, hunt 2026-10-24: a guess made for the compiled loops froze the window for seconds a zoom step)."""
    return getattr(lm, "draw_rate", None) or (RATE[lm.mode] if speed.loops() else RATE_PLAIN)


def changed(widget, lm):
    """True once the loaded file changed / went since loading (read from the file: exact_tab found it); said once
    (hunt 2026-10-24: a file saved again elsewhere was drawn as its new notes, without a word)."""
    if not getattr(lm, "changed", False):
        return False
    if not getattr(lm, "told", False):
        lm.told = True
        from tkinter import messagebox
        from files.lang import tr
        widget.after_idle(lambda: messagebox.showwarning(tr("load.title"), tr("load.file_changed"),
                                                         parent=widget.winfo_toplevel()))
    return True


def learn_rate(lm, n, seconds):
    if (n >= TIMED_LEAST or not speed.loops()) and n and seconds > 0:
        r = n / seconds
        was = getattr(lm, "draw_rate", None)
        lm.draw_rate = r if was is None else (was + r) / 2


def write_log_now():
    from files.errors import write_log
    import sys
    write_log(*sys.exc_info(), "(drawing a loaded MIDI's notes)")


def track_channel(lm, n):
    """Loaded track n's channel as shown: Track Properties' (lm.rechannel), else its main one."""
    moved = lm.rechannel
    if moved is not None and n < len(moved) and moved[n] >= 0:
        return int(moved[n])
    return lm.tracks[n].channel or 0


def colours(faint=False):
    """[channel, velocity // 4] -> colour number (roll_draw's NOTE_COLORS: channel colour group x 32 + shade;
    faint: a track shown behind the highlighted one, roll_draw.ghost_code's shades)."""
    groups = COLOURS_16[np.arange(16)]
    if faint:
        return (GHOST_BASE + groups[:, None]) * 32 + np.minimum(30, np.arange(32))[None, :]
    return groups[:, None] * 32 + np.arange(32)[None, :]


def loud_on(app):
    """Settings → Look "Louder notes on top (loaded MIDI)" ticked?"""
    var = getattr(app, "loud_on_top", None)
    return bool(var is not None and var.get())


def shown_tracks(app, lm):
    """(order, faint) of the loaded tracks the piano roll shows: order None = every track (nothing highlighted in
    the track list), else the track numbers drawn, the highlighted one first (on top), then the ones picked behind
    it (faint: drawn faint), () = none of the loaded tracks."""
    tl = getattr(app, "track_list", None)
    if tl is None or tl.current is None:
        return None, frozenset()
    shown = tl.shown_slots()
    faint = sorted((-s - 1 for s in shown if s < 0 and s != tl.current), reverse=True)
    order = ([-tl.current - 1] if tl.current < 0 else []) + faint
    order = tuple(n for n in order if n < len(lm.tracks))
    return order, frozenset(n for n in faint if n < len(lm.tracks))


def view_bytes(lm, order, ax, bx, W):
    """About how many bytes these tracks hold in the view (checkpoint to checkpoint)."""
    t0 = int(np.floor((-0.5 - bx) / ax))
    t1 = int(np.ceil((W + 0.5 - bx) / ax))
    n = 0
    for k in order:
        t = lm.tracks[k]
        if not t.notes or not len(t.cp_off) or t.end <= t0 or t.first > t1:
            continue
        j = max(0, int(np.searchsorted(t.cp_tick, t0, "right")) - 1)
        j2 = int(np.searchsorted(t.cp_tick, t1, "right"))
        n += (int(t.cp_off[j2]) if j2 < len(t.cp_off) else t.limit) - int(t.cp_off[j])
    return n


def pictures_tab(lm, order, faint, ax, bx, W, k_lo, nk):
    """(tab, marks) from the tracks' own small pictures (the loading pass's: each track's keys x up to PIC_COLS
    columns), order = top first, a pixel taken by the first that has a note there."""
    tab = np.full((nk, W), -1, np.int64)
    tpp = 1 / ax
    c = np.arange(W)
    ta = (c - 0.5 - bx) * tpp
    for n in order:
        t = lm.tracks[n]
        if not t.notes or not t.pic_cols or n in lm.gone:  # (removed in the track list: not drawn)
            continue
        kk0, kk1 = max(t.pic_klo, k_lo), min(t.pic_klo + t.pic.shape[0] - 1, k_lo + nk - 1)
        if kk1 < kk0:
            continue
        bits = np.unpackbits(t.pic, axis=1, count=t.pic_cols).astype(bool)[kk0 - t.pic_klo:kk1 - t.pic_klo + 1]
        step = lm.G * t.pic_pf  # (ticks a picture column)
        c0 = np.floor((ta - t.pic_glo * lm.G) / step).astype(np.int64)
        c1 = np.maximum(np.ceil((ta + tpp - t.pic_glo * lm.G) / step).astype(np.int64), c0 + 1)
        on = (c1 > 0) & (c0 < t.pic_cols)
        if not on.any():
            continue
        idx = np.flatnonzero(on)
        a = np.clip(c0[idx], 0, t.pic_cols - 1)
        if tpp >= step:  # (several columns a pixel: any of them)
            got = np.logical_or.reduceat(bits[:, :int(np.clip(c1[idx[-1]], 1, t.pic_cols))], a, axis=1)
        else:
            got = bits[:, a]
        code = COLOURS_16[track_channel(lm, n)] + (GHOST_BASE if n in faint else 0)
        code = code * 32 + (min(30, SHADE) if n in faint else SHADE)
        part = tab[kk0 - k_lo:kk1 - k_lo + 1]
        sub = part[:, idx]
        sub[(sub < 0) & got] = code
        part[:, idx] = sub
    return tab, np.zeros((nk, W), np.uint8)


def overview_tab(lm, ax, bx, W, k_lo, nk):
    """(tab, marks) from the loading pass's grid: each key x pixel = the highest track sounding there, in its
    channel's colour; marks from its edges (lm.edges: 2 = the top track's note starts / ends there = edge colour,
    1 = only a track under it = the shade)."""
    tpp = 1 / ax
    L = 0
    while L + 1 < len(lm.levels) and (lm.G << (L + 1)) <= tpp:
        L += 1
    cnt, top = lm.levels[L]
    GL = lm.G << L
    ng = top.shape[1]
    c = np.arange(W)
    ta = (c - 0.5 - bx) * tpp
    b0 = np.floor(ta / GL).astype(np.int64)
    b1 = np.maximum(np.ceil((ta + tpp) / GL).astype(np.int64), b0 + 1)
    out = np.full((nk, W), -1, np.int64)
    edge = np.zeros((nk, W), np.uint8)
    for grid, got in ((top, out), (lm.edges[L], edge)):
        rows = grid[k_lo:k_lo + nk]
        if not len(rows):
            continue
        on = (b1 > 0) & (b0 < ng)
        a = np.clip(b0, 0, ng - 1)
        if tpp >= GL and on.any():  # (each column a bucket or more: the most over its buckets, starts increasing)
            idx = np.flatnonzero(on)
            starts = a[idx]
            got[:, idx] = np.maximum.reduceat(rows[:, :int(np.clip(b1[idx[-1]], 1, ng))], starts, axis=1)
        else:
            got[:, on] = rows[:, a[on]]
    tracks = np.array([track_channel(lm, n) for n in range(len(lm.tracks))], np.int64)
    code = COLOURS_16[tracks] * 32 + SHADE
    if lm.gone:  # (removed in the track list: its buckets empty, far out only the top track is known)
        out = np.where(np.isin(out, list(lm.gone)), -1, out)
    tab = np.where(out >= 0, code[np.clip(out, 0, len(code) - 1)], -1)
    marks = np.where(out < 0, 0, np.where(edge == 2, 1, np.where(edge == 1, 4, 0))).astype(np.uint8)
    return tab, marks


SCRUB_COLS = 256  # right-drag listening reads the swept ticks as this many columns at most
SCRUB_S = 0.04  # ... exactly when that's guessed this quick, else from the overview (sounding keys only)
_GROUP_CH = np.argsort(COLOURS_16)  # (channel colour group -> channel)


def scrub_notes(lm, order, lo, hi, at, loud=False):
    """Right-drag listening on a loaded MIDI (user 2026-10-25): {(channel, key): (start, velocity)} of what's drawn
    on top: the keys sounding at tick `at` (start None) and notes starting in ticks lo..hi (start = about their
    tick; the latest per key). order = the tracks shown (shown_tracks, faint ones left out), None = all; loud =
    louder notes on top (Settings → Look). Velocity
    as drawn (to within 4); the overview (where reading exactly is slow) has no starts nor velocities (100).
    Returns (that, rough): rough = from the overview (the status line says so)."""
    lo, hi = int(lo), int(hi)
    if order == () or getattr(lm, "changed", False) or hi < lo:
        return {}, False
    W = int(min(SCRUB_COLS, hi - lo + 1))
    ax = W / (hi - lo + 1)
    bx = -lo * ax
    slow = speed.loops() is None or guess_bytes(lm, order, ax, bx, W) / rate_of(lm) > SCRUB_S
    try:
        if not slow:
            tab, marks = exact_tab(lm, ax, bx, W, 0, lm.keys, tracks=order, loud=loud)
        elif order is None:
            tab, marks = overview_tab(lm, ax, bx, W, 0, lm.keys)
        else:
            tab, marks = pictures_tab(lm, order, frozenset(), ax, bx, W, 0, lm.keys)
    except Exception:  # (a file that changed / went: silent, told when drawing / exporting)
        write_log_now()
        return {}, False
    if tab is None:
        return {}, False

    def note(code):
        group, shade = divmod(int(code), 32)
        return int(_GROUP_CH[group % 16]), 100 if slow else min(127, shade * 4 + 2)
    out = {}
    c = int(min(W - 1, max(0, round(at * ax + bx))))
    for k in np.flatnonzero(tab[:, c] >= 0).tolist():
        out[note(tab[k, c])[0], k] = (None, note(tab[k, c])[1])
    if not slow:
        ks, cs = np.nonzero((marks & 1) & (tab >= 0))
        for k, col in zip(ks.tolist(), cs.tolist()):  # (columns in order: the latest start per key wins)
            ch, v = note(tab[k, col])
            out[ch, k] = (lo + int(col / ax), v)
    return out, slow


def loaded_pixels(img, x0, y0, tab, marks, c0, c1, kb, row0, row1, rgb):
    """tab's columns c0..c1 painted into img (its pixel [0, 0] = screen (x0, y0)), each key's row like a note:
    edge colour on its first / last pixel row and where marks say a note starts / ends, halfway between where a
    hidden note starts / ends (marks 4), fill inside. NumPy twin of
    fastloops.loaded_pixels (the same pixels)."""
    ih, iw = img.shape[:2]
    for kk in range(tab.shape[0]):
        a, b = int(row0[kk]), int(row1[kk])
        ya, yb = max(a, y0), min(b, y0 + ih)
        if yb <= ya:
            continue
        codes = tab[kk, c0:c1]
        on = np.flatnonzero(codes >= 0)
        if not len(on):
            continue
        cs = codes[on]
        xs = on + c0 + kb - x0
        keep = (xs >= 0) & (xs < iw)
        xs, cs = xs[keep], cs[keep]
        mk = marks[kk, c0:c1][on][keep]
        m, half = (mk & 3) != 0, (mk & 4) != 0
        fill, edge = rgb[cs, 0], rgb[cs, 1]
        solid = b - 1 - a < 2
        shade = ((fill.astype(np.int64) + edge) // 2).astype(img.dtype)  # (a hidden note starts / ends there)
        inside = np.where(m[:, None], edge, np.where(half[:, None], shade, fill))
        for y in range(ya, yb):
            img[y - y0, xs] = edge if (solid or y == a or y == b - 1) else inside


class ExactJob:
    """Exact pictures made on a background thread: parts = [(bx, W)] in order, each finished one put in .made for
    the window to take (stop = give up)."""

    def __init__(self, lm, style, ax, parts, order, faint):
        self.lm, self.style, self.ax, self.parts, self.order, self.faint = lm, style, ax, parts, order, faint
        self.stop = False
        self.done = False
        self.made = []

    def covers(self, bx, W):
        """Its widest part holds a view of W columns at bx."""
        b, w = self.parts[-1]
        d = b - bx
        return d >= -1e-6 and d + W <= w + 1e-6

    def run(self):
        try:
            for bx, W in self.parts:
                began, took = time.perf_counter(), [0]
                tab, marks = exact_tab(self.lm, self.ax, bx, W, 0, self.lm.keys, stop=lambda: self.stop,
                                       tracks=self.order, faint=self.faint, took=took, loud=self.style[-1])
                if tab is None or self.stop:
                    break
                learn_rate(self.lm, took[0], time.perf_counter() - began)
                self.made.append(Exact(self.style, self.ax, bx, W, tab, marks))
        except Exception:  # (a file that changed / went: the overview stays; told when exporting)
            write_log_now()
        self.done = True


def merge_loud(tab, marks, ttab, tmarks, tnx, tfilled, ghost):
    """NumPy twin of midi_index.merge_loud (the same tables)."""
    for k in np.flatnonzero(tfilled):
        t, g, mt, mg = ttab[k], tab[k], tmarks[k], marks[k]
        on = t >= 0
        win = on & ((g < 0) | ((t & 31) + np.where(t >= ghost, 0, 32) > (g & 31) + np.where(g >= ghost, 0, 32)))
        lose = on & ~win
        hid = (g >= 0) & (mg != 0)
        new = np.where((mt & 3) != 0, mt & 3, np.where(hid | ((mt & 4) != 0), 4, 0)).astype(np.uint8)
        mg[lose & (mt != 0) & ((mg & 3) == 0)] |= 4
        mg[win] = new[win]
        g[win] = t[win]
        t[:] = -1
        mt[:] = 0
        tnx[k] = np.arange(tnx.shape[1])
        tfilled[k] = 0


def exact_tab(lm, ax, bx, W, k_lo, nk, stop=lambda: False, tracks=None, faint=(), took=None, loud=False):
    """(tab, marks) of every note on screen: tracks (numbers, the one drawn on top first; default all, the last
    track on top), read from their checkpoints before the view. None if stopped. took = [bytes read] (added to).
    loud: louder notes on top (Settings → Look; each track painted alone, then merge_loud)."""
    if loud:
        return loud_tab(lm, ax, bx, W, k_lo, nk, stop, tracks, faint, took)
    fast = speed.loops()
    t0 = int(np.floor((-0.5 - bx) / ax))
    t1 = int(np.ceil((W + 0.5 - bx) / ax))
    tab = np.full((nk, W), -1, np.int64)
    marks = np.zeros((nk, W), np.uint8)
    nx = np.tile(np.arange(W + 1, dtype=np.int64), (nk, 1))
    filled = np.zeros(nk, np.int64)
    order = tracks if tracks is not None else range(len(lm.tracks) - 1, -1, -1)
    if lm.mode != "memory" and not still_same(lm):
        return None, None
    covered = []  # (tracks whose every row is painted already: they only shade, read on other cores)
    rd = TrackReader(lm, ax, bx, W, k_lo, t0, t1, faint)
    try:
        for n in order:
            if stop():
                return None, None
            t = lm.tracks[n]
            if not t.notes or n in lm.gone or t.end <= t0 or t.first > t1 or t.khi < k_lo or t.klo >= k_lo + nk:
                continue
            if fast and (filled[max(t.klo, k_lo) - k_lo:min(t.khi, k_lo + nk - 1) - k_lo + 1] >= W).all():
                covered.append(n)
                continue
            if not rd.read(n, tab, nx, filled, marks):
                return None, None
        if covered and not read_covered(lm, covered, rd, tab, marks, stop):
            return None, None
    finally:
        rd.close()
        if took is not None:
            took[0] += rd.took
    if lm.mode != "memory" and not still_same(lm):  # (changed while it was read: nothing read is trusted)
        return None, None
    return tab, marks


def loud_tab(lm, ax, bx, W, k_lo, nk, stop, tracks, faint, took):
    """exact_tab with louder notes on top: each track painted alone into its own tables, then merged in. Blocks of
    neighbouring tracks (about the same bytes each) merged on several cores, the blocks then merged in order (the
    same picture: merging is the same in any grouping, kept in track order)."""
    fast = speed.loops()
    merge = fast.midi_merge_loud if fast else merge_loud
    t0 = int(np.floor((-0.5 - bx) / ax))
    t1 = int(np.ceil((W + 0.5 - bx) / ax))
    order = tracks if tracks is not None else range(len(lm.tracks) - 1, -1, -1)
    busy = [n for n in order if lm.tracks[n].notes and n not in lm.gone and lm.tracks[n].end > t0
            and lm.tracks[n].first <= t1 and lm.tracks[n].khi >= k_lo and lm.tracks[n].klo < k_lo + nk]
    if lm.mode != "memory" and not still_same(lm):
        return None, None
    n_threads = min(len(busy), max(1, (os.cpu_count() or 2) // 2)) if fast else 1
    if n_threads > 1:  # (blocks of about the same bytes)
        size = np.array([view_bytes(lm, (n,), ax, bx, W) for n in busy], np.float64)
        cut = np.searchsorted(np.cumsum(size), np.arange(1, n_threads) * size.sum() / n_threads)
        blocks = [b for b in np.split(np.array(busy, np.int64), cut) if len(b)]
    else:
        blocks = [busy]
    readers = [TrackReader(lm, ax, bx, W, k_lo, t0, t1, faint) for _ in blocks]
    made = [None] * len(blocks)
    bad = []

    def work(i):
        tab = np.full((nk, W), -1, np.int64)
        marks = np.zeros((nk, W), np.uint8)
        ttab, tmarks = tab.copy(), marks.copy()
        tnx = np.tile(np.arange(W + 1, dtype=np.int64), (nk, 1))
        tfilled = np.zeros(nk, np.int64)
        for n in blocks[i]:
            if bad or stop() or not readers[i].read(int(n), ttab, tnx, tfilled, tmarks):
                bad.append(True)
                return
            merge(tab, marks, ttab, tmarks, tnx, tfilled, GHOST_BASE * 32)
        made[i] = (tab, marks)
    try:
        threads = [threading.Thread(target=work, args=(i,), daemon=True, name="loaded notes")
                   for i in range(1, len(blocks))]
        for th in threads:
            th.start()
        work(0)
        for th in threads:
            th.join()
    finally:
        for rd in readers:
            rd.close()
            if took is not None:
                took[0] += rd.took
    if bad:
        return None, None
    tab, marks = made[0]
    rows = np.ones(nk, np.int64)
    spare = np.zeros((nk, W + 1), np.int64)
    for later in made[1:]:  # (the block lower down merged in like one track)
        rows[:] = 1
        merge(tab, marks, later[0], later[1], spare, rows, GHOST_BASE * 32)
    if lm.mode != "memory" and not still_same(lm):
        return None, None
    return tab, marks


def read_covered(lm, covered, first, tab, marks, stop):
    """Covered tracks (nothing left to paint: their notes' starts / ends only add marks 4) read side by side on
    several cores, each with its own marks, joined at the end (same marks in any order). False = stopped / the
    file changed."""
    n_threads = min(len(covered), max(1, (os.cpu_count() or 2) // 2))
    nk, W = tab.shape
    queue = list(reversed(covered))
    lock = threading.Lock()
    bad = []

    def work(rd, own):
        full = np.full((nk, W + 1), W, np.int64)  # (every pixel taken: nothing gets painted)
        dummy = np.zeros(nk, np.int64)
        while not bad:
            with lock:
                if not queue:
                    return
                n = queue.pop()
            if stop() or not rd.read(n, tab, full, dummy, own):
                bad.append(True)
                return
    readers = [first] + [TrackReader(lm, first.ax, first.bx, W, first.k_lo, first.t0, first.t1, first.faint)
                         for _ in range(n_threads - 1)]
    owns = [marks] + [np.zeros_like(marks) for _ in range(n_threads - 1)]
    threads = [threading.Thread(target=work, args=(rd, own), daemon=True, name="loaded notes")
               for rd, own in zip(readers[1:], owns[1:])]
    try:
        for th in threads:
            th.start()
        work(readers[0], owns[0])
        for th in threads:
            th.join()
    finally:
        for rd in readers[1:]:
            rd.close()
            first.took += rd.took
    if bad:
        return False
    for own in owns[1:]:
        marks |= own
    return True


class TrackReader:
    """One track at a time painted into an exact picture's tables (cover from its checkpoint before t0), with its
    own scratch arrays and file handle, so several can read side by side."""

    def __init__(self, lm, ax, bx, W, k_lo, t0, t1, faint):
        self.lm, self.ax, self.bx, self.W, self.k_lo, self.t0, self.t1, self.faint = lm, ax, bx, W, k_lo, t0, t1, faint
        fast = speed.loops()
        self.cover = fast.midi_cover if fast else midi_index.cover
        self.cnt_q, self.first_q, self.vel_q = np.zeros(QS, np.int64), np.zeros(QS, np.int64), np.zeros(QS, np.int64)
        self.sq, self.sa, self.sb, self.sv = (np.zeros(SPANS, np.int64) for _ in range(4))
        self.st = np.zeros(C_SIZE, np.int64)
        self.normal, self.behind = colours(), colours(True)
        self.f = self.buf = None
        self.took = 0

    def read(self, n, tab, nx, filled, marks):
        """Track n painted in; False = the file changed / went (lm.changed)."""
        lm, t, t0, t1 = self.lm, self.lm.tracks[n], self.t0, self.t1
        if not len(t.cp_off):
            return True
        j = max(0, int(np.searchsorted(t.cp_tick, t0, "right")) - 1)
        j2 = int(np.searchsorted(t.cp_tick, t1, "right"))
        start = int(t.cp_off[j])
        end = int(t.cp_off[j2]) if j2 < len(t.cp_off) else t.limit
        if end <= start:
            return True
        cnt_q, first_q, vel_q, st = self.cnt_q, self.first_q, self.vel_q, self.st
        e0, e1 = t.cp_ent[j], t.cp_ent[j + 1]
        qs = t.e_q[e0:e1].astype(np.int64)
        cnt_q[qs] = t.e_cnt[e0:e1]
        first_q[qs] = t.cp_tick[j]
        vel_q[qs] = t.e_vel[e0:e1]
        lanes, counts = lm.hidden_open(n, start)  # (extracted: not sounding)
        cnt_q[lanes] -= counts
        skip = lm.skip_of(n)
        self.took += end - start
        if t.data is not None:
            b = t.data[start:end]
        else:
            size = end - start
            if self.buf is None or len(self.buf) < size:
                self.buf = np.empty(max(size, 1 << 20), np.uint8)
            try:
                if self.f is None:
                    self.f = open(lm.path, "rb")
                self.f.seek(lm.mf.tracks[n].start + start)
                got = self.f.readinto(memoryview(self.buf)[:size])
            except OSError:
                got = -1
            if got != size:
                lm.changed = True
                return False
            b = self.buf[:size]
        st[:] = 0
        st[C_TICK], st[C_RUN] = t.cp_tick[j], t.cp_run[j]
        colour = self.behind if n in self.faint else self.normal
        moved = lm.rechannel
        if moved is not None and n < len(moved) and moved[n] >= 0:  # (Track Properties' channel: every note on it)
            colour = colour[np.full(16, moved[n])]
        args =(self.ax, self.bx, self.W, self.k_lo, colour, tab, nx, filled, marks, self.sq, self.sa, self.sb, self.sv)
        p = self.cover(b, 0, len(b), start, skip, st, cnt_q, first_q, vel_q, t1, -1, *args)
        close = t1 + 1 if st[C_STOP] == 1 or end < t.limit else max(int(st[C_TICK]), t.last_tick)
        self.cover(b, p, p, start, skip, st, cnt_q, first_q, vel_q, t1, close, *args)
        return True

    def close(self):
        if self.f is not None:
            self.f.close()
            self.f = None


def still_same(lm):
    """The loaded file is still the one read (size + time); False (and lm.changed) once it isn't."""
    if getattr(lm, "changed", False):
        return False
    try:
        lm.mf.same()
    except MidiError:
        lm.changed = True
        return False
    return True
