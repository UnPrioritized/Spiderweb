"""A loaded MIDI's notes on the piano roll (Load MIDI), painted under Spiderweb's own notes in the notes' picture.
They never go through note_rects: 100 M - 2 B notes would take seconds a frame. Each picture is a key x pixel table
of colour numbers (roll_draw's note colours: the channel's, shaded by velocity):
- EXACT pictures: every track read from its checkpoint before the picture's start (from memory or from the file),
  the top track first, each pixel painted once (midi_index.cover). Made a screen WIDER than the view on each side
  (MARGIN) for every key, so scrolling sideways / up and down only moves along them (the user saw notes switch
  while scrolling, 2026-10-24: never show two looks of the same notes). Made in the frame itself when that's quick
  (INLINE_S, guessed from the bytes to read), else on a background thread, and again further on before the view
  reaches an edge;
- while a slow one is being made (zoomed on a giant song): the last exact picture STRETCHED to the new zoom where
  it reaches (stand_in), the OVERVIEW elsewhere: from the loading pass's grid (which track sounds highest in each
  key x 1/16 beat), a few ms at any size. Far out on a giant song (EXACT_MOST_S) the overview alone."""

import threading
import time

import numpy as np

from files import midi_index, speed
from files.midi_index import C_SIZE, C_STOP, C_TICK, C_RUN, QS
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
        key = (id(lm), W, kb, top, h, round(ax, 12), round(bx, 6), p_lo, p_hi, tuple(pal or ()), order,
               frozenset(faint), TRACK_FADE[0])
        return lm, key, (ax, bx, W, p_lo, p_hi, kb, top, order, faint)

    def loaded_mark(self):
        """What the loaded notes' part of the picture depends on besides the view (roll_draw.redraw's key)."""
        lm = self.app.loaded
        if lm is None:
            return None
        order, faint = shown_tracks(self.app, lm)
        return id(lm), getattr(self, "loaded_version", 0), order, faint, TRACK_FADE[0]

    def paint_loaded(self, img, region):
        """The loaded notes into img, the picture of region = (x0, y0, x1, y1) of the screen (paint_region)."""
        got = self.loaded_view(region)
        if got is None:
            return
        lm, key, (ax, bx, W, p_lo, p_hi, kb, top, order, faint) = got
        nk = p_hi - p_lo + 1
        if nk < 1 or order == ():
            return
        mark = (key, getattr(self, "loaded_version", 0))
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

    def loaded_tabs(self, lm, key, ax, bx, W, p_lo, nk, order, faint):
        """(tab, marks) of the view: keys p_lo .. + nk x W columns."""
        style = (id(lm), order, frozenset(faint), TRACK_FADE[0])
        rows = slice(p_lo, p_lo + nk)
        got = self._exact_for(style, ax, bx, W)
        if got is not None:
            e, d = got
            self._keep_ahead(lm, style, e, d, ax, bx, W, order, faint)
            return cut(e, rows, d, W)
        M = int(W * MARGIN)
        rate = getattr(lm, "draw_rate", None) or RATE[lm.mode]
        got = self._extend(lm, style, ax, bx, W, M, rate, order, faint)
        if got is not None:
            e, d = got
            self._keep_ahead(lm, style, e, d, ax, bx, W, order, faint)
            return cut(e, rows, d, W)
        if guess_bytes(lm, order, ax, bx + M, W + 2 * M) / rate <= INLINE_S:
            e = self._make_now(lm, style, ax, bx + M, W + 2 * M, order, faint)
            if e is not None:
                return cut(e, rows, M, W)
        slow = guess_bytes(lm, order, ax, bx, W) / rate
        if slow <= INLINE_S:
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
                tab, marks = exact_tab(lm, ax, e.bx - s0, s1 - s0, 0, lm.keys, tracks=order, faint=faint, took=took)
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
            tab, marks = exact_tab(lm, ax, bx, W, 0, lm.keys, tracks=order, faint=faint, took=took)
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


def learn_rate(lm, n, seconds):
    if n >= TIMED_LEAST and seconds > 0:
        r = n / seconds
        was = getattr(lm, "draw_rate", None)
        lm.draw_rate = r if was is None else (was + r) / 2


def write_log_now():
    from files.errors import write_log
    import sys
    write_log(*sys.exc_info(), "(drawing a loaded MIDI's notes)")


def colours(faint=False):
    """[channel, velocity // 4] -> colour number (roll_draw's NOTE_COLORS: channel colour group x 32 + shade;
    faint: a track shown behind the highlighted one, roll_draw.ghost_code's shades)."""
    groups = COLOURS_16[np.arange(16)]
    if faint:
        return (GHOST_BASE + groups[:, None]) * 32 + np.minimum(30, np.arange(32))[None, :]
    return groups[:, None] * 32 + np.arange(32)[None, :]


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
        if not t.notes or not t.pic_cols:
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
        code = COLOURS_16[t.channel or 0] + (GHOST_BASE if n in faint else 0)
        code = code * 32 + (min(30, SHADE) if n in faint else SHADE)
        part = tab[kk0 - k_lo:kk1 - k_lo + 1]
        sub = part[:, idx]
        sub[(sub < 0) & got] = code
        part[:, idx] = sub
    return tab, np.zeros((nk, W), np.uint8)


def overview_tab(lm, ax, bx, W, k_lo, nk):
    """(tab, marks) from the loading pass's grid: each key x pixel = the highest track sounding there, in its
    channel's colour (marks: none)."""
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
    rows = top[k_lo:k_lo + nk]
    out = np.full((nk, W), -1, np.int64)
    if len(rows):
        on = (b1 > 0) & (b0 < ng)
        a = np.clip(b0, 0, ng - 1)
        if tpp >= GL and on.any():  # (each column a bucket or more: the most over its buckets, starts increasing)
            idx = np.flatnonzero(on)
            starts = a[idx]
            out[:, idx] = np.maximum.reduceat(rows[:, :int(np.clip(b1[idx[-1]], 1, ng))], starts, axis=1)
        else:
            out[:, on] = rows[:, a[on]]
    tracks = np.array([t.channel if t.channel is not None else 0 for t in lm.tracks], np.int64)
    code = COLOURS_16[tracks] * 32 + SHADE
    tab = np.where(out >= 0, code[np.clip(out, 0, len(code) - 1)], -1)
    return tab, np.zeros((nk, W), np.uint8)


def loaded_pixels(img, x0, y0, tab, marks, c0, c1, kb, row0, row1, rgb):
    """tab's columns c0..c1 painted into img (its pixel [0, 0] = screen (x0, y0)), each key's row like a note:
    edge colour on its first / last pixel row and where marks say a note starts / ends, fill inside. NumPy twin of
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
        m = marks[kk, c0:c1][on][keep] != 0
        fill, edge = rgb[cs, 0], rgb[cs, 1]
        solid = b - 1 - a < 2
        inside = np.where(m[:, None], edge, fill)
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
                                       tracks=self.order, faint=self.faint, took=took)
                if tab is None or self.stop:
                    break
                learn_rate(self.lm, took[0], time.perf_counter() - began)
                self.made.append(Exact(self.style, self.ax, bx, W, tab, marks))
        except Exception:  # (a file that changed / went: the overview stays; told when exporting)
            write_log_now()
        self.done = True


def exact_tab(lm, ax, bx, W, k_lo, nk, stop=lambda: False, tracks=None, faint=(), took=None):
    """(tab, marks) of every note on screen: tracks (numbers, the one drawn on top first; default all, the last
    track on top), read from their checkpoints before the view. None if stopped. took = [bytes read] (added to)."""
    fast = speed.loops()
    cover = fast.midi_cover if fast else midi_index.cover
    t0 = int(np.floor((-0.5 - bx) / ax))
    t1 = int(np.ceil((W + 0.5 - bx) / ax))
    tab = np.full((nk, W), -1, np.int64)
    marks = np.zeros((nk, W), np.uint8)
    nx = np.tile(np.arange(W + 1, dtype=np.int64), (nk, 1))
    filled = np.zeros(nk, np.int64)
    cnt_q, first_q, vel_q = np.zeros(QS, np.int64), np.zeros(QS, np.int64), np.zeros(QS, np.int64)
    sq, sa, sb, sv = (np.zeros(SPANS, np.int64) for _ in range(4))
    st = np.zeros(C_SIZE, np.int64)
    normal, behind = colours(), colours(True)
    order = tracks if tracks is not None else range(len(lm.tracks) - 1, -1, -1)
    f = None if lm.mode == "memory" else open(lm.path, "rb")
    buf = None
    try:
        for n in order:
            if stop():
                return None, None
            t = lm.tracks[n]
            if not t.notes or t.end <= t0 or t.first > t1 or t.khi < k_lo or t.klo >= k_lo + nk:
                continue
            a, z = max(t.klo, k_lo) - k_lo, min(t.khi, k_lo + nk - 1) - k_lo + 1
            if (filled[a:z] >= W).all():  # (every row it could paint is full: hidden under the tracks above)
                continue
            if not len(t.cp_off):
                continue
            j = max(0, int(np.searchsorted(t.cp_tick, t0, "right")) - 1)
            j2 = int(np.searchsorted(t.cp_tick, t1, "right"))
            start = int(t.cp_off[j])
            end = int(t.cp_off[j2]) if j2 < len(t.cp_off) else t.limit
            if end <= start:
                continue
            e0, e1 = t.cp_ent[j], t.cp_ent[j + 1]
            qs = t.e_q[e0:e1].astype(np.int64)
            cnt_q[qs] = t.e_cnt[e0:e1]
            first_q[qs] = t.cp_tick[j]
            vel_q[qs] = t.e_vel[e0:e1]
            if took is not None:
                took[0] += end - start
            if t.data is not None:
                b = t.data[start:end]
            else:
                size = end - start
                if buf is None or len(buf) < size:
                    buf = np.empty(max(size, 1 << 20), np.uint8)
                f.seek(lm.mf.tracks[n].start + start)
                got = f.readinto(memoryview(buf)[:size])
                if got != size:
                    return None, None
                b = buf[:size]
            st[:] = 0
            st[C_TICK], st[C_RUN] = t.cp_tick[j], t.cp_run[j]
            colour = behind if n in faint else normal
            p = cover(b, 0, len(b), st, cnt_q, first_q, vel_q, t1, -1, ax, bx, W, k_lo, colour, tab, nx, filled,
                      marks, sq, sa, sb, sv)
            close = t1 + 1 if st[C_STOP] == 1 or end < t.limit else max(int(st[C_TICK]), t.last_tick)
            cover(b, p, p, st, cnt_q, first_q, vel_q, t1, close, ax, bx, W, k_lo, colour, tab, nx, filled, marks,
                  sq, sa, sb, sv)
    finally:
        if f is not None:
            f.close()
    return tab, marks
