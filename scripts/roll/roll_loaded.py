"""A loaded MIDI's notes on the piano roll (Load MIDI), painted under Spiderweb's own notes in the notes' picture.
They never go through note_rects: 100 M - 2 B notes would take seconds a frame. Two pictures, each a key x pixel
table of colour numbers (roll_draw's note colours: the channel's, shaded by velocity):
- the OVERVIEW, at once: from the loading pass's grid (which track sounds highest in each key x 1/16 beat), a few
  ms at any size;
- the EXACT one, in a background thread once the view rests: every track read from its checkpoint before the
  view (from memory or from the file), the top track first, each pixel painted once (midi_index.cover).
The overview shows until the exact one for that very view is ready; zoomed out past a grid bucket per pixel the
overview is already right, so no exact one is made."""

import threading
import time

import numpy as np

from files import midi_index, speed
from files.midi_index import C_SIZE, C_STOP, C_TICK, C_RUN, QS
from roll.roll_draw import GHOST_BASE, TRACK_FADE, note_tables
from roll.roll_shared import COLOURS_16

REST_MS = 150  # the view resting this long = the exact picture is made
POLL_MS = 30  # how often a running job is looked at
SPANS = 1 << 15  # spans painted at a time (cover's buffer)
SHADE = 25  # the overview's shade (no velocity there): a note of velocity 100's
EXACT_FROM = 2  # grid buckets per pixel or fewer = the exact picture is worth making
PICKED_BYTES = 64 << 20  # tracks picked in the track list: read exactly when they hold this little in the view


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
        exact = getattr(self, "_loaded_exact", None)
        if exact is not None and exact[0] == key:
            tab, marks = exact[1], exact[2]
        else:
            over = getattr(self, "_loaded_over", None)
            if over is None or over[0] != key:
                made = (overview_tab(lm, ax, bx, W, p_lo, nk) if order is None else
                        pictures_tab(lm, order, faint, ax, bx, W, p_lo, nk))
                over = self._loaded_over = (key, *made)
            tab, marks = over[1], over[2]
            # (zoomed in: the exact picture, once the view rests; with tracks picked in the track list also zoomed
            # out unless they hold a lot there: their own small pictures are coarse)
            if 1 / ax < lm.G * EXACT_FROM or order is not None and view_bytes(lm, order, ax, bx, W) <= PICKED_BYTES:
                self.want_exact(lm, key, (ax, bx, W, p_lo, nk, order, faint))
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

    # ------------------------------------------------------------------ the exact picture

    def want_exact(self, lm, key, geo):
        job = getattr(self, "_loaded_job", None)
        if job is not None and job.key == key:
            return
        if job is not None:
            job.stop = True
        self._loaded_job = None
        timer = getattr(self, "_loaded_timer", None)
        if timer:
            self.after_cancel(timer)
        self._loaded_timer = self.after(REST_MS, lambda: self._start_exact(lm, key, geo))

    def _start_exact(self, lm, key, geo):
        self._loaded_timer = None
        got = self.loaded_view(None)
        if got is None or got[1] != key:  # (the view moved on meanwhile: that redraw asked for its own)
            return
        job = self._loaded_job = ExactJob(lm, key, geo)
        threading.Thread(target=job.run, daemon=True, name="loaded notes").start()
        self.after(POLL_MS, self._poll_exact)

    def _poll_exact(self):
        job = getattr(self, "_loaded_job", None)
        if job is None:
            return
        if not job.done:
            self.after(POLL_MS, self._poll_exact)
            return
        self._loaded_job = None
        if job.tab is not None and not job.stop:
            self._loaded_exact = (job.key, job.tab, job.marks)
            self.loaded_version = getattr(self, "loaded_version", 0) + 1
            self.request_redraw()

    def forget_loaded(self):
        """A new loaded MIDI (or none): the pictures made for the old one go."""
        job = getattr(self, "_loaded_job", None)
        if job is not None:
            job.stop = True
        self._loaded_job = self._loaded_exact = self._loaded_over = None
        self.loaded_version = getattr(self, "loaded_version", 0) + 1


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
    """The exact picture of one view, made on a background thread (stop = give up)."""

    def __init__(self, lm, key, geo):
        self.lm, self.key, self.geo = lm, key, geo
        self.stop = False
        self.done = False
        self.tab = self.marks = None
        self.seconds = 0.0

    def run(self):
        began = time.perf_counter()
        try:
            ax, bx, W, k_lo, nk, order, faint = self.geo
            self.tab, self.marks = exact_tab(self.lm, ax, bx, W, k_lo, nk, stop=lambda: self.stop, tracks=order,
                                             faint=faint)
        except Exception:  # (a file that changed / went: the overview stays; told when exporting)
            from files.errors import write_log
            import sys
            write_log(*sys.exc_info(), "(drawing a loaded MIDI's notes)")
            self.tab = None
        self.seconds = time.perf_counter() - began
        self.done = True


def exact_tab(lm, ax, bx, W, k_lo, nk, stop=lambda: False, tracks=None, faint=()):
    """(tab, marks) of every note on screen: tracks (numbers, the one drawn on top first; default all, the last
    track on top), read from their checkpoints before the view. None if stopped."""
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
            vel_q[qs] = 100
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
