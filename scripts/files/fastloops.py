"""Compiled loops (Numba) for the slowest note work with lots of notes. Only reached through files/speed.py, which
loads this in the background and hands it out once it's ready; without Numba the NumPy code they replace is used.
Each gives exactly what that NumPy code gives (dev/tests/fast_loops.py compares them):
  paint_order    the notes kept in painting order for the next two (redone when they or the selection change)
  order_screen   roll_draw.note_rects' screen(): the notes on screen, their pixels, colours, in painting order
  order_rects    order_screen + last_on_pixels in one (the notes in between never made)
  last_on_pixels roll_draw.note_rects' end: notes on the very same pixels (only the last shows) left out
  note_top       roll_draw.note_pixels: which note is on top at every pixel (and whether it's its outline)
  paint_notes    roll_draw.paint_region: the notes' colours straight into the picture (the last painted wins)
  overlap_order  engine.resolve_overlaps' groups and sort (first_seen + overlap_order)
  overlap_sweep  engine.resolve_overlaps after its sort: cut / stretch / merge the notes on one key and slot
  midi_events    midi_out.track_events: one track's note-ons / offs as MIDI file bytes
  note_stretches engine.stretches (Multi channel): one shape's notes on a key joined into stretches
  clash_levels   engine.clash_levels: Hz bass layers' notes clashing on a key -> their levels
  note_ends      hzbass.note_lengths: a Hz bass's notes' ends by its Note length
  wave_fill      hzbass.wave_ticks: each note's wave length, one key at a time
  hz_expand ... hz_finish  hz_grid.KeyGrid.made for the plain settings (made_fast): a key's repeats
  key_order      np.lexsort by key, then up to 3 more columns (runs already in order merged: quick)
  ring_runs      roll_draw.ring_parts' runs: the selection's red outline, each key row's notes joined
  clash_pairs    engine.clash_pairs (Multi channel): which owners' stretches clash
  nearest        picture._dist2(...).argmin(1) (Image to notes): each colour's nearest palette colour
  spread_line    picture._spread: the error spreading, one slanted line at a time (its OKLab left to NumPy)
  area_spots     faces.Faces.area_at: the area each spot is in (custom shapes' Fill / Spam, area colours)
  smooth_sor     nested.smooth_values' loop (Nested fill, Blend)
  limit_dsp      synth.live_limit as a callback BASS's sound thread calls itself (Built-in BASSMIDI's limiter: it
                 never waits for Python); the same maths within float rounding
The first start compiles them (in the background); the result is kept for the next starts: in __pycache__, or in
the exe's user/compiled-loops (keep_exe_loops)."""

import math
import os
import sys

import numpy as np
from numba import carray, cfunc, njit, types
from numba.core import caching
from numba.typed import List

from files.synth import (LIM_BLOCK, LIM_CEILING, LIM_DELAY, LIM_ENV, LIM_FALL, LIM_G0, LIM_G1, LIM_HEAD, LIM_MIX,
                         LIM_N, LIM_ON, LIM_PEAK, LIM_PREV, LIM_RING, LIM_SIZE, LIM_STEP)


def keep_exe_loops():
    """The exe unpacks to a new temp folder each start, so Numba can't find its saved loops by the source file's
    place: they go to user/compiled-loops/<this build> instead (speed.loops_folder; made again when missing, the
    newest 3 builds kept: an older build's file can never pass for this one's). Numba also checks the processor
    (another PC = compiled again). False = the folder can't be written (the loops are compiled each start)."""
    from files import about, lang, safefile, speed
    folder = speed.loops_folder()
    top = os.path.dirname(folder)
    try:
        os.makedirs(folder, exist_ok=True)
        os.utime(folder)  # (last used: the newest builds are kept)
        readme = os.path.join(top, "!README.txt")  # ("!": on top when sorted by name)
        if not os.path.exists(readme):
            safefile.write_text(readme, lang.tr("speed.loops_readme"))
        test = os.path.join(folder, "x" * 80 + ".tmp")  # (longer than Numba's names: path too long / read-only = off)
        with open(test, "wb") as f:
            f.write(b"0")
        os.remove(test)
    except OSError:
        return False
    import shutil
    builds = sorted((os.path.join(top, n) for n in os.listdir(top) if os.path.isdir(os.path.join(top, n))),
                    key=os.path.getmtime, reverse=True)
    for old in builds[3:]:
        if old != folder:
            shutil.rmtree(old, ignore_errors=True)

    class ExeLocator(caching._SourceFileBackedLocatorMixin, caching._CacheLocator):
        def __init__(self, py_func, py_file):
            self._py_file = py_file
            self._lineno = py_func.__code__.co_firstlineno

        def get_cache_path(self):
            return folder

        @classmethod
        def from_function(cls, py_func, py_file):
            self = cls(py_func, py_file)
            try:
                self.ensure_cache_path()
            except OSError:
                return None
            return self

    caching.CacheImpl._locator_classes[:] = [ExeLocator]  # (no other place: Numba's own ones would pile up in AppData)
    return True


def drop_stale(folder):
    """From source Numba checks only this file, but synth.py's LIM_* numbers are built into the saved loops too:
    when they differ from the ones the saved loops were made with, those are deleted (made again)."""
    from files import safefile
    now = repr((LIM_BLOCK, LIM_CEILING, LIM_DELAY, LIM_ENV, LIM_FALL, LIM_G0, LIM_G1, LIM_HEAD, LIM_MIX, LIM_N, LIM_ON,
                LIM_PEAK, LIM_PREV, LIM_RING, LIM_SIZE, LIM_STEP))
    mark = os.path.join(folder, "fastloops-synth.txt")
    try:
        with open(mark, encoding="utf-8") as f:
            if f.read() == now:
                return
    except OSError:
        pass
    try:
        os.makedirs(folder, exist_ok=True)
        for name in os.listdir(folder):
            if name.startswith("fastloops.") and name.endswith((".nbi", ".nbc")):
                os.remove(os.path.join(folder, name))
        safefile.write_text(mark, now)
    except OSError:
        pass


def _cache():
    from files import speed
    if not speed.save_loops:  # (the saved ones failed this start: made without saving)
        return False
    if getattr(sys, "frozen", False):  # (the exe: each build's own folder, so nothing goes stale)
        return keep_exe_loops()
    drop_stale(speed.loops_folder())
    return True


CACHE = _cache()


@njit(cache=CACHE, nogil=True)
def last_on_pixels(x0, x1, key, color, left, right, keys, row0, row1):
    """The notes' rectangles (x0, y0, x1, y1, colour) with x clamped to left..right; of notes up to 3 pixels long
    on the very same pixels only the last is kept (the others are painted over)."""
    n = len(x0)
    for i in range(n):
        x0[i] = max(x0[i], left)
        x1[i] = min(x1[i], right)
    last = np.empty((right - left + 1) * keys * 4, np.int64)
    for i in range(n):
        size = x1[i] - x0[i]
        if 0 <= size < 4:
            last[((x0[i] - left) * keys + key[i]) * 4 + size] = i
    ra = np.empty(n, np.int64)
    rb = np.empty(n, np.int64)
    rc = np.empty(n, np.int64)
    rd = np.empty(n, np.int64)
    re = np.empty(n, np.int64)
    m = 0
    for i in range(n):
        size = x1[i] - x0[i]
        if 0 <= size < 4 and last[((x0[i] - left) * keys + key[i]) * 4 + size] != i:
            continue
        ra[m], rb[m], rc[m], rd[m], re[m] = x0[i], row0[key[i]], max(x1[i] - 1, x0[i]), row1[key[i]] - 1, color[i]
        m += 1
    return ra[:m].copy(), rb[:m].copy(), rc[:m].copy(), rd[:m].copy(), re[:m].copy()


@njit(cache=CACHE, nogil=True)
def paint_order(notes, by_start, rank, nranks, pics, cols, picgroup, ghost):
    """The notes kept in painting order between pictures (redone when the notes or the selection change): by owner
    rank, each owner's in by_start's order (np.argsort of the starts, stable). cols = roll_shared.slot_colours. ->
    start, end, key, colour (as roll_draw.note_rects' screen() works it out; velocity -1 = ghost, a mask hides it),
    first (rank r's notes = first[r] .. first[r + 1]), the longest note."""
    nslots = len(cols)
    n = len(by_start)
    first = np.zeros(nranks + 1, np.int64)
    for i in range(n):
        first[rank[notes[i, 5]] + 1] += 1
    for r in range(nranks):
        first[r + 1] += first[r]
    at = first[:-1].copy()
    s = np.empty(n, np.int64)
    e = np.empty(n, np.int64)
    key = np.empty(n, np.int16)
    color = np.empty(n, np.int32)
    longest = 0
    for j in range(n):
        i = by_start[j]
        r = rank[notes[i, 5]]
        o = at[r]
        at[r] += 1
        s[o], e[o], key[o] = notes[i, 0], notes[i, 1], notes[i, 2]
        longest = max(longest, notes[i, 1] - notes[i, 0])
        c = cols[notes[i, 4] % nslots]
        if len(pics) and pics[min(max(notes[i, 5], 0), len(pics) - 1)]:
            c = picgroup + min(notes[i, 4], 15)
        color[o] = ghost if notes[i, 3] < 0 else c * 32 + min(notes[i, 3], 127) // 4
    return s, e, key, color, first, longest


@njit(cache=CACHE, nogil=True)
def _block_range(s, lo, hi, t_lo, t_hi):
    """In s[lo:hi] (sorted): the first with s >= t_lo, the first with s > t_hi (np.searchsorted left / right)."""
    a, b = lo, hi
    while a < b:
        m = (a + b) // 2
        if s[m] < t_lo:
            a = m + 1
        else:
            b = m
    c, d = a, hi
    while c < d:
        m = (c + d) // 2
        if s[m] <= t_hi:
            c = m + 1
        else:
            d = m
    return a, c


@njit(cache=CACHE, nogil=True)
def order_screen(s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2):
    """note_rects' screen() from paint_order's notes (ranks b0 .. b1, starts t_lo .. t_hi): x0, x1, key, colour of the ones
    on screen, already in painting order."""
    m = 0
    for r in range(b0, b1):
        lo, hi = _block_range(s, first[r], first[r + 1], t_lo, t_hi)
        m += hi - lo
    x0 = np.empty(m, np.int64)
    x1 = np.empty(m, np.int64)
    k = np.empty(m, np.int64)
    c = np.empty(m, np.int64)
    m = 0
    for r in range(b0, b1):
        lo, hi = _block_range(s, first[r], first[r + 1], t_lo, t_hi)
        for i in range(lo, hi):
            a = np.rint(s[i] * ax + bx)
            b = np.rint(e[i] * ax + bx)
            if b < kb or a > w or not shown[key[i]]:
                continue
            if clip and (b < c0 or a >= c2):
                continue
            x0[m], x1[m], k[m], c[m] = int(a), int(b), key[i], color[i]
            m += 1
    return x0[:m], x1[:m], k[:m], c[:m]


@njit(cache=CACHE, nogil=True)
def order_rects(s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2, left, right, keys,
                row0, row1):
    """order_screen and last_on_pixels in one: the rectangles, without the notes in between."""
    ranges = np.empty((b1 - b0, 2), np.int64)
    for r in range(b0, b1):
        ranges[r - b0, 0], ranges[r - b0, 1] = _block_range(s, first[r], first[r + 1], t_lo, t_hi)
    last = np.empty((right - left + 1) * keys * 4, np.int64)
    j = 0  # (the j-th note on screen)
    for r in range(b1 - b0):
        for i in range(ranges[r, 0], ranges[r, 1]):
            a = np.rint(s[i] * ax + bx)
            b = np.rint(e[i] * ax + bx)
            if b < kb or a > w or not shown[key[i]]:
                continue
            if clip and (b < c0 or a >= c2):
                continue
            x0, x1 = max(int(a), left), min(int(b), right)
            size = x1 - x0
            if 0 <= size < 4:
                last[((x0 - left) * keys + key[i]) * 4 + size] = j
            j += 1
    ra = np.empty(j, np.int64)
    rb = np.empty(j, np.int64)
    rc = np.empty(j, np.int64)
    rd = np.empty(j, np.int64)
    re = np.empty(j, np.int64)
    j = m = 0
    for r in range(b1 - b0):
        for i in range(ranges[r, 0], ranges[r, 1]):
            a = np.rint(s[i] * ax + bx)
            b = np.rint(e[i] * ax + bx)
            if b < kb or a > w or not shown[key[i]]:
                continue
            if clip and (b < c0 or a >= c2):
                continue
            x0, x1 = max(int(a), left), min(int(b), right)
            size = x1 - x0
            j += 1
            if 0 <= size < 4 and last[((x0 - left) * keys + key[i]) * 4 + size] != j - 1:
                continue
            ra[m], rb[m], rc[m], rd[m], re[m] = x0, row0[key[i]], max(x1 - 1, x0), row1[key[i]] - 1, color[i]
            m += 1
    return ra[:m].copy(), rb[:m].copy(), rc[:m].copy(), rd[:m].copy(), re[:m].copy()


@njit(cache=CACHE, nogil=True)
def note_top(top_px, kb, top, w, h, x0, y0, x1, y1):
    """top_px (the region's pixels, row by row, -1 filled) = note number * 2 + (1 = outline) of the note painted
    last on each pixel. Same pixels as a canvas rectangle with a 1-pixel outline: x0..x1 and y0..y1 inclusive."""
    iw = w - kb
    for k in range(len(x0)):
        a, b = max(y0[k], top), min(y1[k] + 1, h)
        c, d = max(x0[k], kb), min(x1[k] + 1, w)
        if b <= a or d <= c:
            continue
        solid = x1[k] - x0[k] < 2 or y1[k] - y0[k] < 2  # too small to have a fill: all outline
        left, right = c == x0[k], d == x1[k] + 1  # (its left / right outline on screen?)
        fill, edge = 2 * k, 2 * k + 1
        for y in range(a, b):
            row = (y - top) * iw - kb
            v = edge if solid or y == y0[k] or y == y1[k] else fill
            for x in range(c, d):
                top_px[row + x] = v
            if v == fill:
                if left:
                    top_px[row + c] = edge
                if right:
                    top_px[row + d - 1] = edge


@njit(cache=CACHE, nogil=True)
def paint_notes(img, kb, top, w, h, x0, y0, x1, y1, color, rgb):
    """Each note's colours painted straight into img (pixels x 3), the last painted winning: the same pixels as
    note_top's notes on top coloured in, without that list."""
    iw = w - kb
    for k in range(len(x0)):
        a, b = max(y0[k], top), min(y1[k] + 1, h)
        c, d = max(x0[k], kb), min(x1[k] + 1, w)
        if b <= a or d <= c:
            continue
        solid = x1[k] - x0[k] < 2 or y1[k] - y0[k] < 2
        left, right = c == x0[k], d == x1[k] + 1
        f0, f1, f2 = rgb[color[k], 0, 0], rgb[color[k], 0, 1], rgb[color[k], 0, 2]
        e0, e1, e2 = rgb[color[k], 1, 0], rgb[color[k], 1, 1], rgb[color[k], 1, 2]
        for y in range(a, b):
            row = (y - top) * iw - kb
            if solid or y == y0[k] or y == y1[k]:
                for x in range(c, d):
                    img[row + x, 0], img[row + x, 1], img[row + x, 2] = e0, e1, e2
            else:
                for x in range(c, d):
                    img[row + x, 0], img[row + x, 1], img[row + x, 2] = f0, f1, f2
                if left:
                    img[row + c, 0], img[row + c, 1], img[row + c, 2] = e0, e1, e2
                if right:
                    img[row + d - 1, 0], img[row + d - 1, 1], img[row + d - 1, 2] = e0, e1, e2


@njit(cache=CACHE, nogil=True)
def _merge_runs(notes, order, a, b):
    """order[a:b] sorted by start (notes[:, 0]), stable: its runs already in order merged two by two."""
    n = b - a
    idx = order[a:b].copy()
    s = np.empty(n, np.int64)
    for q in range(n):
        s[q] = notes[idx[q], 0]
    bounds = [0]
    for q in range(1, n):
        if s[q] < s[q - 1]:
            bounds.append(q)
    bounds.append(n)
    idx2, s2 = np.empty(n, np.int64), np.empty(n, np.int64)
    while len(bounds) > 2:
        new = [0]
        for r in range(0, len(bounds) - 1, 2):
            lo = bounds[r]
            if r + 2 >= len(bounds):  # (an odd run out: copied as it is)
                hi = bounds[r + 1]
                idx2[lo:hi], s2[lo:hi] = idx[lo:hi], s[lo:hi]
                new.append(hi)
                continue
            mid, hi = bounds[r + 1], bounds[r + 2]
            i, j, o = lo, mid, lo
            while i < mid and j < hi:
                if s[j] < s[i]:  # (equal: the earlier run's first)
                    idx2[o], s2[o] = idx[j], s[j]
                    j += 1
                else:
                    idx2[o], s2[o] = idx[i], s[i]
                    i += 1
                o += 1
            while i < mid:
                idx2[o], s2[o] = idx[i], s[i]
                i += 1
                o += 1
            while j < hi:
                idx2[o], s2[o] = idx[j], s[j]
                j += 1
                o += 1
            new.append(hi)
        bounds = new
        idx, idx2 = idx2, idx
        s, s2 = s2, s
    order[a:b] = idx


@njit(cache=CACHE, nogil=True)
def overlap_order(notes):
    """engine.first_seen of slot * 256 + key and engine.overlap_order in one: (group, order), or two empty arrays
    when the ids are too spread out for a table (the NumPy way then)."""
    n = len(notes)
    lo, hi = 0, 0
    for i in range(n):
        g = notes[i, 4] * 256 + notes[i, 2]
        if i == 0 or g < lo:
            lo = g
        if i == 0 or g > hi:
            hi = g
    if n == 0 or lo < 0 or hi + 1 > 4 * n + 65536:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    seen = np.full(hi + 1, -1, np.int64)
    group = np.empty(n, np.int64)
    first = np.zeros(n + 1, np.int64)
    groups = 0
    for i in range(n):  # groups numbered in the order they first show up, counted
        g = notes[i, 4] * 256 + notes[i, 2]
        if seen[g] < 0:
            seen[g] = groups
            groups += 1
        group[i] = seen[g]
        first[seen[g] + 1] += 1
    for g in range(groups):
        first[g + 1] += first[g]
    at = first[:groups].copy()
    order = np.empty(n, np.int64)
    for i in range(n):  # by group, keeping the order inside each
        g = group[i]
        order[at[g]] = i
        at[g] += 1
    for g in range(groups):
        a, b = first[g], first[g + 1]
        for j in range(a + 1, b):  # by start inside each (stable; most come in sorted already)
            if notes[order[j], 0] < notes[order[j - 1], 0]:
                _merge_runs(notes, order, a, b)
                break
        j = a
        while j < b:  # the same start: the quietest first, then the longest first (stable)
            k = j + 1
            while k < b and notes[order[k], 0] == notes[order[j], 0]:
                k += 1
            for p in range(j + 1, k):
                v = order[p]
                q = p - 1
                while q >= j:
                    u = order[q]
                    if notes[u, 3] > notes[v, 3] or (notes[u, 3] == notes[v, 3] and notes[u, 1] < notes[v, 1]):
                        order[q + 1] = u
                        q -= 1
                    else:
                        break
                order[q + 1] = v
            j = k
    return group, order


@njit(cache=CACHE, nogil=True)
def overlap_sweep(notes, order, group):
    """resolve_overlaps' work once sorted (order: by group, start, velocity, longest first): a note starting while
    earlier ones of its group still sound is stretched to where they would have ended and the one before it is
    cut there; notes left with no length are dropped (but the last of each group). -> (new rows, how many)."""
    n = len(order)
    out = np.empty((n, notes.shape[1]), np.int64)
    m = 0
    run = 0  # everything before in the group sounds until here
    for idx in range(n):
        i = order[idx]
        g, s, e = group[i], notes[i, 0], notes[i, 1]
        end = e
        if idx > 0 and group[order[idx - 1]] == g:
            if s < run:
                end = max(e, run)
            run = max(run, e)
        else:
            run = e
        last = True
        if idx + 1 < n:
            j = order[idx + 1]
            if group[j] == g:
                last = False
                if notes[j, 0] < run:
                    end = notes[j, 0]
        if last or end > s:
            for c in range(notes.shape[1]):
                out[m, c] = notes[i, c]
            out[m, 1] = end
            m += 1
    return out, m


@njit(cache=CACHE, nogil=True)
def note_stretches(notes, by_key, owner, order):
    """engine.stretches: (key, start, end, owner) rows, the notes without a length first as they come, then the rest's
    stretches (back-to-back notes on a key joined) by key and start. order: the notes by key, then start, or empty =
    as they come, if they come in that order (else -> (empty, -1): sort them first). -> (rows, how many)."""
    n = len(notes)
    out = np.empty((n, 4), np.int64)
    m = 0
    for i in range(n):
        if notes[i, 1] <= notes[i, 0]:
            out[m, 0] = notes[i, 2] if by_key else 0
            out[m, 1], out[m, 2], out[m, 3] = notes[i, 0], notes[i, 1], owner
            m += 1
    first, pk, ps = m, 0, 0  # (the note before: key, start)
    for j in range(len(order) if len(order) else n):
        i = order[j] if len(order) else j
        s, e = notes[i, 0], notes[i, 1]
        if e <= s:
            continue
        k = notes[i, 2] if by_key else 0
        if m > first and not len(order) and (k < pk or k == pk and s < ps):
            return out[:0], -1
        pk, ps = k, s
        if m > first and k == out[m - 1, 0] and s <= out[m - 1, 2]:  # (starts while the stretch sounds: it goes on)
            if e > out[m - 1, 2]:
                out[m - 1, 2] = e
        else:
            out[m, 0], out[m, 1], out[m, 2], out[m, 3] = k, s, e, owner
            m += 1
    return out, m


@njit(cache=CACHE, nogil=True)
def _before(i, j, a, b, c):
    """Row i comes before row j: by a, then b, then c."""
    if a[i] != a[j]:
        return a[i] < a[j]
    if b[i] != b[j]:
        return b[i] < b[j]
    return c[i] < c[j]


@njit(cache=CACHE, nogil=True)
def _runs_sort(idx, a, b, c, tmp):
    """idx (row numbers) sorted by (a, b, c) in place, rows alike kept in their order (as np.lexsort): the runs
    already in order are merged two by two (notes mostly come in order: a few runs, quick)."""
    n = len(idx)
    if n < 2:
        return
    bounds = [0]
    for i in range(1, n):
        if _before(idx[i], idx[i - 1], a, b, c):
            bounds.append(i)
    bounds.append(n)
    while len(bounds) > 2:
        nb = [0]
        for r in range(0, len(bounds) - 1, 2):
            lo = bounds[r]
            if r + 2 >= len(bounds):
                nb.append(bounds[r + 1])
                continue
            mid, hi = bounds[r + 1], bounds[r + 2]
            p, q, m = lo, mid, lo
            while p < mid and q < hi:
                if _before(idx[q], idx[p], a, b, c):
                    tmp[m] = idx[q]
                    q += 1
                else:
                    tmp[m] = idx[p]
                    p += 1
                m += 1
            while p < mid:
                tmp[m] = idx[p]
                p += 1
                m += 1
            while q < hi:
                tmp[m] = idx[q]
                q += 1
                m += 1
            idx[lo:hi] = tmp[lo:hi]
            nb.append(hi)
        bounds = nb


@njit(cache=CACHE, nogil=True)
def key_order(key, a, b, c):
    """np.lexsort((c, b, a, key)): rows by key, then a, b, c, rows alike in their order."""
    n = len(key)
    order = np.empty(n, np.int64)
    if n == 0:
        return order
    kmin = key.min()
    nk = key.max() - kmin + 1
    at = np.zeros(nk + 1, np.int64)
    for i in range(n):
        at[key[i] - kmin + 1] += 1
    for g in range(nk):
        at[g + 1] += at[g]
    pos = at[:-1].copy()
    for i in range(n):
        g = key[i] - kmin
        order[pos[g]] = i
        pos[g] += 1
    tmp = np.empty(n, np.int64)
    for g in range(nk):
        if at[g + 1] - at[g] > 1:
            part = order[at[g]:at[g + 1]]
            _runs_sort(part, a, b, c, tmp[at[g]:at[g + 1]])
    return order


@njit(cache=CACHE, nogil=True)
def ring_runs(s, e, k, order, gap, big):
    """roll_draw.ring_parts' runs: the notes in (key, start) order joined while the next starts within gap ticks of
    the reach so far (key * big added, as there) -> each run's key, start, end."""
    n = len(order)
    rk, ra, rb = np.empty(n, np.int64), np.empty(n, np.int64), np.empty(n, np.int64)
    m = 0
    i = order[0]
    reach = e[i] + k[i] * big
    rk[0], ra[0] = k[i], s[i]
    for j in range(1, n):
        i = order[j]
        at = s[i] + k[i] * big
        if at > reach + gap:
            rb[m] = reach - rk[m] * big
            m += 1
            rk[m], ra[m] = k[i], s[i]
        reach = max(reach, e[i] + k[i] * big)
    rb[m] = reach - rk[m] * big
    return rk[:m + 1], ra[:m + 1], rb[:m + 1]


@njit(cache=CACHE, nogil=True)
def clash_pairs(items, n):
    """engine.clash_pairs: (key, start, end, owner) rows, owners below n -> an n x n table, True where owners a < b
    clash: in (key, start, end, owner) order, a later one on the same key starting before an earlier one ends.
    Swept key by key: each owner's latest end so far, the owners still sounding at each start."""
    k = np.ascontiguousarray(items[:, 0])
    s = np.ascontiguousarray(items[:, 1])
    e = np.ascontiguousarray(items[:, 2])
    o = np.ascontiguousarray(items[:, 3])
    order = key_order(k, s, e, o)
    hit = np.zeros((n, n), np.bool_)
    latest = np.full(n, np.iinfo(np.int64).min, np.int64)
    on = np.zeros(n, np.bool_)
    act = np.empty(n, np.int64)
    na = 0
    m = len(order)
    i = 0
    while i < m:
        key = k[order[i]]
        while i < m and k[order[i]] == key:
            r = order[i]
            t, b = s[r], o[r]
            j = 0
            for x in range(na):  # (owners whose notes end by now: not sounding)
                a = act[x]
                if latest[a] > t:
                    act[j] = a
                    j += 1
                    if a != b:
                        hit[min(a, b), max(a, b)] = True
                else:
                    on[a] = False
            na = j
            if e[r] > latest[b]:
                latest[b] = e[r]
            if not on[b] and latest[b] > t:
                on[b] = True
                act[na] = b
                na += 1
            i += 1
        for x in range(na):  # (the next key starts afresh)
            on[act[x]] = False
        for x in range(i - 1, -1, -1):
            r = order[x]
            if k[r] != key:
                break
            latest[o[r]] = np.iinfo(np.int64).min
        na = 0
    return hit


@njit(cache=CACHE, nogil=True)
def _first_at(arr, n, x, hint):
    """How many of arr[:n] (sorted) are below x, searched outwards from hint (the answer nearby: a step or two when
    the questions come in order), then halved down as a plain binary search."""
    if hint > n:
        hint = n
    if hint < n and arr[hint] < x:  # (further on)
        lo = hint + 1
        step = 1
        hi = lo
        while hi < n and arr[hi] < x:
            lo = hi + 1
            step *= 2
            hi = lo + step - 1
        if hi > n:
            hi = n
    else:  # (here or before)
        hi = hint
        step = 1
        lo = hi - 1
        while lo > 0 and arr[lo] >= x:
            hi = lo
            step *= 2
            lo = hi - step
        if lo < 0:
            lo = 0
    while lo < hi:
        mid = (lo + hi) >> 1
        if arr[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(cache=CACHE, nogil=True)
def clash_levels(rows, offs, out):
    """engine.clash_levels: rows = (n, 3) int64 (start, end, key) of all lists one after another; offs: where each
    list starts (+ end). out: the level of each note (filled). A note goes on the lowest level where no note of an EARLIER list on its key
    sounds at the same time; the notes of one list never clash with each other (as in the NumPy twin)."""
    n = rows.shape[0]
    nl = offs.shape[0] - 1
    if n == 0 or nl < 2:
        return
    kmin = rows[0, 2]
    kmax = rows[0, 2]
    for i in range(n):
        if rows[i, 2] < kmin:
            kmin = rows[i, 2]
        if rows[i, 2] > kmax:
            kmax = rows[i, 2]
    nk = kmax - kmin + 1
    at = np.zeros(nk + 1, np.int64)
    for i in range(n):
        at[rows[i, 2] - kmin + 1] += 1
    for g in range(nk):
        at[g + 1] += at[g]
    pos = at[:-1].copy()
    order = np.empty(n, np.int64)  # (by key; on a key the lists' order kept)
    for i in range(n):
        g = rows[i, 2] - kmin
        order[pos[g]] = i
        pos[g] += 1

    # per level: the notes placed there so far, by start, with the latest end so far (cs / ce, count cnt);
    # ss / se are spare buffers the merge writes into, then they swap places with cs / ce
    cs = List()
    ce = List()
    ss = List()
    se = List()
    cs.append(np.empty(0, np.int64))
    ce.append(np.empty(0, np.int64))
    ss.append(np.empty(0, np.int64))
    se.append(np.empty(0, np.int64))
    cnt = np.zeros(16, np.int64)
    used = 0
    for g in range(nk):
        a = at[g]
        b = at[g + 1]
        if b == a:
            continue
        used = 0
        i = a
        while i < b:
            L = 0  # the list of the note at order[i]
            while offs[L + 1] <= order[i]:
                L += 1
            end_list = offs[L + 1]
            c = i
            while c < b and order[c] < end_list:
                c += 1
            bidx = order[i:c].copy()
            m = c - i
            srt = True
            for t in range(1, m):
                if rows[bidx[t], 0] < rows[bidx[t - 1], 0]:
                    srt = False
                    break
            if not srt:  # (sorted by start, for the merge)
                keys_b = np.empty(m, np.int64)
                for t in range(m):
                    keys_b[t] = rows[bidx[t], 0]
                perm = np.argsort(keys_b, kind="mergesort")
                bidx = bidx[perm]
            # the level of each note of this list (all against the earlier lists' notes only)
            top = 0
            hint = np.zeros(len(cnt), np.int64)  # (per level: the last answer, where the next search starts)
            for t in range(m):
                x = bidx[t]
                sx = rows[x, 0]
                ex = rows[x, 1]
                lv = 0
                while lv < used:
                    lo = _first_at(cs[lv], cnt[lv], ex, hint[lv])  # how many starts are before its end
                    hint[lv] = lo
                    if lo > 0 and ce[lv][lo - 1] > sx:
                        lv += 1
                    else:
                        break
                out[x] = lv
                if lv > top:
                    top = lv
            # merge this list's notes into each level they landed on (by start, latest end so far)
            for lv in range(top + 1):
                while len(cs) <= lv:
                    cs.append(np.empty(0, np.int64))
                    ce.append(np.empty(0, np.int64))
                    ss.append(np.empty(0, np.int64))
                    se.append(np.empty(0, np.int64))
                if len(cnt) <= lv:
                    cnt2 = np.zeros(2 * lv + 16, np.int64)
                    cnt2[:len(cnt)] = cnt
                    cnt = cnt2
                add = 0
                for t in range(m):
                    if out[bidx[t]] == lv:
                        add += 1
                q = cnt[lv] if lv < used else 0
                if q == 0 and add == 0:
                    continue
                old_s = cs[lv]
                old_e = ce[lv]
                need = q + add
                ns = ss[lv]
                ne = se[lv]
                if len(ns) < need:
                    size = need + (need >> 1) + 8
                    ns = np.empty(size, np.int64)
                    ne = np.empty(size, np.int64)
                p = 0
                mm = 0
                run = -(1 << 62)  # (below any end)
                for t in range(m):
                    x = bidx[t]
                    if out[x] != lv:
                        continue
                    sx = rows[x, 0]
                    while p < q and old_s[p] <= sx:
                        if old_e[p] > run:
                            run = old_e[p]
                        ns[mm] = old_s[p]
                        ne[mm] = run
                        p += 1
                        mm += 1
                    ex = rows[x, 1]
                    if ex > run:
                        run = ex
                    ns[mm] = sx
                    ne[mm] = run
                    mm += 1
                while p < q:
                    if old_e[p] > run:
                        run = old_e[p]
                    ns[mm] = old_s[p]
                    ne[mm] = run
                    p += 1
                    mm += 1
                cs[lv] = ns
                ce[lv] = ne
                ss[lv] = old_s
                se[lv] = old_e
                cnt[lv] = need
            if top + 1 > used:
                used = top + 1  # (levels past the old count read as empty: see q above)
            i = c


@njit(cache=CACHE, nogil=True)
def wave_fill(starts, a, b, sq0, wo, out):
    """hzbass.wave_ticks / KeyGrid.wave_at for one key: out[i] = wo[the last repeat (sq0 = its start ticks) starting at or before starts[i], 0 if none] for i in a..b-1
    (np.searchsorted(sq0, starts, "right") - 1, clamped at 0: as KeyGrid.wave_at)."""
    m = len(sq0)
    lo = 0  # (lo: the first repeat starting after x; kept from the last x while the starts go up: a walk, not a search)
    prev = starts[a] if a < b else 0
    for i in range(a, b):
        x = starts[i]
        if x < prev:
            lo = 0
        prev = x
        steps = 0
        while lo < m and sq0[lo] <= x and steps < 8:
            lo += 1
            steps += 1
        if lo < m and sq0[lo] <= x:
            hi = m
            while lo < hi:
                mid = (lo + hi) // 2
                if sq0[mid] <= x:
                    lo = mid + 1
                else:
                    hi = mid
        at = lo - 1
        if at < 0:
            at = 0
        out[i] = wo[at]


@njit(cache=CACHE, nogil=True)
def note_ends(s, e, k, stick, mode, beats, pct, pct_int, wv, wf, big):
    """hzbass.note_lengths: the notes' (sorted by key, then start) new ends, the same steps as its NumPy
    code. mode 0 = beats (ticks), 1 = share wf of a wave (wv), 2 = pct (pct_int: a whole number), 3 = as made."""
    n = len(s)
    keyed = np.empty(n, np.int64)
    for i in range(n):
        keyed[i] = k[i] * big + s[i]
    # j: the first note on the same key starting later (np.searchsorted(keyed, keyed, "right") on sorted keys)
    j = np.empty(n, np.int64)
    i = 0
    while i < n:
        r = i + 1
        while r < n and keyed[r] == keyed[i]:
            r += 1
        for t in range(i, r):
            j[t] = r
        i = r
    has = np.zeros(n, np.bool_)
    room = np.empty(n, np.int64)
    now = np.empty(n, np.int64)
    last = np.empty(n, np.bool_)
    for i in range(n):
        now[i] = e[i] - s[i]
        jj = j[i]
        if jj < n and k[jj] == k[i]:
            has[i] = True
            room[i] = s[jj] - s[i]
            last[i] = not (s[jj] <= e[i])
        else:
            room[i] = big
            last[i] = True
    full = np.empty(n, np.int64)
    for i in range(n):
        full[i] = now[i]
        if stick and i > 0 and last[i] and (not last[i - 1]) and k[i - 1] == k[i]:
            full[i] = min(max(now[i], now[i - 1]), room[i])
    # runs: chain[i] = a new run starts at i (no note before it on its key reaching it)
    chain = np.empty(n, np.bool_)
    group = np.empty(n, np.int64)
    if n:
        chain[0] = True
        group[0] = 0
    for i in range(1, n):
        chain[i] = (k[i] != k[i - 1]) or (s[i] > s[i - 1] + full[i - 1])
        group[i] = group[i - 1] + (1 if chain[i] else 0)
    ng = group[n - 1] + 1 if n else 0
    gmax = np.empty(ng, np.int64)  # run_end: the furthest a run's notes reach
    for i in range(n):
        g = group[i]
        v = s[i] + full[i]
        if chain[i] or v > gmax[g]:
            gmax[g] = v
    # want: the note's end as asked (at least 1 tick, never past its run's end)
    want = np.empty(n, np.int64)
    for i in range(n):
        if mode == 0:
            ni = max(np.int64(np.floor(beats)), np.int64(1))
        elif mode == 1:
            w = wv[i]
            if w != w:
                ni = max(full[i], np.int64(1))
            else:
                if math.isinf(w):
                    w = math.copysign(1.7976931348623157e308, w)
                ni = max(np.int64(np.floor(w * wf + 0.5)), np.int64(1))
        elif mode == 2:
            if pct_int:
                ni = max(np.int64(np.floor(np.float64(full[i] * np.int64(pct)) / 100.0 + 0.5)), np.int64(1))
            else:
                ni = max(np.int64(np.floor(np.float64(full[i]) * pct / 100.0 + 0.5)), np.int64(1))
        else:
            ni = max(full[i], np.int64(1))
        want[i] = min(s[i] + ni, gmax[group[i]])
    # reach: the latest wanted end so far in the run; end: at least that (the notes before it on the run)
    end = np.empty(n, np.int64)
    reach = np.empty(n, np.int64)
    for i in range(n):
        if chain[i]:
            reach[i] = want[i]
            end[i] = want[i]
        else:
            reach[i] = max(reach[i - 1], want[i])
            end[i] = max(want[i], reach[i - 1])
    for i in range(n - 1):  # cut where the next note of its run starts
        if not chain[i + 1]:
            end[i] = min(end[i], s[i + 1])
    res = np.empty(n, np.int64)
    for i in range(n):
        res[i] = max(end[i], s[i] + 1)
    return res


@njit(cache=CACHE, nogil=True)
def hz_expand(runs_sel, copy_sel, scale_c, n_run, off_run, prev_run, moves_run, pos_len, x, off_pitch, opf, vf,
              W, S):
    """hz_grid.KeyGrid.made (the plain settings, made_fast): the parts' repeats as made (pre-expansion), the chain counts, and the expansion to the sorted repeats
    (part, number, src, waves, starts). Returns also each part's run and whether every inc is good."""
    R = len(runs_sel)
    C = len(copy_sel)
    P = R * C
    n_p = np.empty(P, np.int64)
    run_p = np.empty(P, np.int64)
    scale_p = np.empty(P)
    for p in range(P):
        run_p[p] = runs_sel[p // C]
        n_p[p] = n_run[run_p[p]]
        scale_p[p] = scale_c[p % C]
    first = np.empty(P, np.int64)
    acc = 0
    for p in range(P):
        first[p] = acc
        acc += n_p[p]
    M0 = acc
    part = np.empty(M0, np.int64)
    src = np.empty(M0, np.int64)
    for p in range(P):
        for k in range(n_p[p]):
            r = first[p] + k
            part[r] = p
            src[r] = off_run[run_p[p]] + k
    stretch = np.empty(M0)
    inc = np.empty(M0)
    ok = True
    for r in range(M0):
        s = src[r]
        stretch[r] = scale_p[part[r]] * (1.0 + off_pitch * opf[s] * (x - 0.5)) * vf[s]
    moves = np.empty(P, np.bool_)
    for p in range(P):
        scaled = scale_p[p] != 1.0 and n_p[p] > 0
        moves[p] = (moves_run[run_p[p]] or scaled) and n_p[p] > 0
    for r in range(M0):
        if moves[part[r]]:
            inc[r] = 1.0 / stretch[r]
        else:
            inc[r] = 1.0
        if not (inc[r] > 0.0 and inc[r] < np.inf):
            ok = False
    total = np.zeros(P)
    for r in range(M0):
        total[part[r]] += inc[r]
    pos = np.full(pos_len, -1, np.int64)
    for i in range(R):
        pos[runs_sel[i]] = i
    up = np.empty(P, np.int64)
    for p in range(P):
        pr = prev_run[run_p[p]]
        u = pos[pr] if pr >= 0 else -1
        up[p] = u * C + (p % C) if u >= 0 else -1
    head = np.zeros(P)
    for p in range(P):
        head[p] = total[up[p]] if up[p] >= 0 else 0.0
    while True:
        busy = False
        for p in range(P):
            if up[p] >= 0:
                busy = True
                break
        if not busy:
            break
        nh = np.empty(P)
        nu = np.empty(P, np.int64)
        for p in range(P):
            if up[p] >= 0:
                s = up[p]
                nh[p] = head[p] + head[s]
                nu[p] = up[s]
            else:
                nh[p] = head[p] + 0.0
                nu[p] = -1
        head = nh
        up = nu
    counted = np.empty(P, np.bool_)
    j0 = np.empty(P)
    size = np.empty(P, np.int64)
    for p in range(P):
        h = head[p]
        counted[p] = moves[p] or (abs(h - np.rint(h)) > 1e-9)
        j0[p] = np.ceil(h - 1e-9)
        if counted[p]:
            size[p] = int(max(np.ceil(h + total[p] - 1e-9) - j0[p], 0.0))
        else:
            size[p] = n_p[p]
    ends = np.empty(M0)
    acc = 0.0
    for r in range(M0):
        acc += inc[r]
        ends[r] = acc
    excl = np.empty(P)
    acc = 0.0
    for p in range(P):
        excl[p] = acc
        acc += total[p]
    Mo = 0
    for p in range(P):
        Mo += size[p]
    o_part = np.empty(Mo, np.int64)
    o_num = np.empty(Mo, np.int64)
    o_src = np.empty(Mo, np.int64)
    o_w = np.empty(Mo)
    o_s = np.empty(Mo)
    q = 0
    for p in range(P):
        if not counted[p]:
            hr = np.int64(np.rint(head[p]))
            for k in range(n_p[p]):
                s = src[first[p] + k]
                o_part[q] = p
                o_num[q] = hr + k
                o_src[q] = s
                o_w[q] = W[s]
                o_s[q] = S[s]
                q += 1
        else:
            jb = np.int64(j0[p])
            lo_b = first[p]
            hi_b = first[p] + n_p[p] - 1
            for t in range(size[p]):
                jj = jb + t
                here = excl[p] + (float(jj) - head[p])
                lo_i = 0
                hi_i = M0
                while lo_i < hi_i:  # (searchsorted, side right)
                    mid = (lo_i + hi_i) // 2
                    if here < ends[mid]:
                        hi_i = mid
                    else:
                        lo_i = mid + 1
                a = min(max(lo_i, lo_b), hi_b)
                ia = inc[a]
                s = src[a]
                into = (here - (ends[a] - ia)) / ia
                o_part[q] = p
                o_num[q] = jj
                o_src[q] = s
                o_w[q] = W[s] / ia
                o_s[q] = S[s] + into * W[s]
                q += 1
    return o_part, o_num, o_src, o_w, o_s, run_p, ok


@njit(cache=CACHE, nogil=True)
def hz_late(part, number, src, w, s, hits, has_trace, x, slant, groups, noisy_f, P):
    """Trace hits (each repeat once per hit when there are several), the Slant / Groups lateness, the noisy
    draws' counts (which random numbers each part takes) and their places."""
    M1 = len(part)
    h = len(hits)
    if has_trace and h != 1:
        M2 = M1 * h
        p2 = np.empty(M2, np.int64)
        n2 = np.empty(M2, np.int64)
        s2 = np.empty(M2, np.int64)
        w2 = np.empty(M2)
        st2 = np.empty(M2)
        for i in range(M1):
            for t in range(h):
                q = i * h + t
                p2[q] = part[i]
                n2[q] = number[i]
                s2[q] = src[i]
                w2[q] = w[i]
                st2[q] = s[i]
    else:
        M2 = M1
        p2, n2, s2, w2, st2 = part, number, src, w, s
    late = np.zeros(M2)
    nsrc = np.empty(M2)
    count = np.zeros(P, np.int64)
    noisy_p = np.zeros(P, np.bool_)
    for q in range(M2):
        sq_ = s2[q]
        late[q] = hits[q % h] if has_trace else 0.0
        late[q] = late[q] + slant[sq_] * x + np.floor(x * groups[sq_]) / groups[sq_]
        nsrc[q] = noisy_f[sq_]
        count[p2[q]] += 1
        if noisy_f[sq_] != 0:
            noisy_p[p2[q]] = True
    took = np.zeros(P, np.int64)
    for p in range(P):
        took[p] = count[p] if noisy_p[p] else 0
    base = np.empty(P, np.int64)
    acc = 0
    for p in range(P):
        base[p] = acc
        acc += took[p]
    total_took = acc
    excl_c = np.empty(P, np.int64)
    acc = 0
    for p in range(P):
        excl_c[p] = acc
        acc += count[p]
    place = np.empty(M2, np.int64)
    for q in range(M2):
        place[q] = base[p2[q]] + q - excl_c[p2[q]]
    return p2, n2, s2, w2, st2, late, nsrc, noisy_p, place, total_took


@njit(cache=CACHE, nogil=True)
def hz_order(wg):
    """np.argsort(wg, kind="stable"): LSD radix sort on 11-bit digits when the ticks' range is small (same order:
    each pass is stable), else the merge sort."""
    K = len(wg)
    mn, mx = wg[0], wg[0]
    for i in range(K):
        if wg[i] < mn:
            mn = wg[i]
        if wg[i] > mx:
            mx = wg[i]
    span = mx - mn
    if span >= 8589934592 or K < 256:  # (2**33: three passes at most)
        return np.argsort(wg, kind="mergesort")
    key = wg - mn
    order = np.arange(K)
    tmp = np.empty(K, np.int64)
    cnt = np.empty(2049, np.int64)
    shift = 0
    while (span >> shift) > 0:
        cnt[:] = 0
        for i in range(K):
            cnt[((key[i] >> shift) & 2047) + 1] += 1
        for d in range(2048):
            cnt[d + 1] += cnt[d]
        for i in range(K):
            d = (key[order[i]] >> shift) & 2047
            tmp[cnt[d]] = order[i]
            cnt[d] += 1
        order, tmp = tmp, order
        shift += 11
    return order


@njit(cache=CACHE, nogil=True)
def hz_finish(part, number, src, w, st0, late, nsrc, noisy_p, place, drawn, until_p, gains_sel, has_loud, has_wah,
              hv, vol_f, lev, oct_f, own_f, layer_f, lim_f, loud_sw, c1, tf, soft_thr):
    """The noise, the starts, the loudness (wah, tremolo, volume, Octave below), the repeats kept, the velocity parts,
    and the grid of notes (repeat_grid): returns (sq, factor, mine, wv, K) with the repeats heard (K = repeats kept)."""
    M = len(part)
    C = len(gains_sel)
    st = np.empty(M)
    li = np.empty(M)
    wv = np.empty(M)
    fa = np.empty(M)
    pe0 = np.empty(M)
    qu = np.empty(M, np.bool_)
    K = 0
    for q in range(M):
        p = part[q]
        s = src[q]
        l_ = late[q]
        if noisy_p[p]:
            l_ = l_ + nsrc[q] * drawn[place[q]]
        s0 = st0[q] + l_ * w[q]
        if not (s0 < until_p[p] - 1e-6):
            continue
        if has_loud:
            lo = loud_sw[s]
            if has_wah:
                lo = (lo * c1[s]) / 2.0
            lo = lo * tf[s]
            g = gains_sel[p % C]
            vol = (vol_f[s] if hv[s] else 1.0) * (g * lev[s])
            env_ = lo * vol
            quiet_ = vol < soft_thr
            soft_ = (1.0 - oct_f[s]) if number[q] % 2 == 1 else 1.0
        else:
            env_ = 1.0
            quiet_ = False
            soft_ = 1.0
        v0 = own_f[s]
        v1 = layer_f[s]
        f_ = np.sqrt(env_) * soft_ * 1.0
        f_ = f_ * (v0 if v0 > 0 else 1.0) * v1
        st[K] = s0
        li[K] = lim_f[s]
        wv[K] = w[q]
        fa[K] = f_
        pe0[K] = v0
        qu[K] = quiet_ or (v1 <= 0)
        K += 1
    if K == 0:
        return np.zeros((0, 2)), np.zeros(0), np.zeros(0, np.bool_), np.zeros(0), 0
    # (repeat_grid: the repeats on one whole tick are one group: the longest limit of them, ends at the next start)
    wg = np.empty(K, np.int64)
    for i in range(K):
        wg[i] = np.int64(np.floor(st[i] + 0.5))
    order = hz_order(wg)
    gfirst = np.empty(K, np.int64)
    gstart = np.empty(K, np.int64)
    gmax = np.empty(K)
    ng = 0
    k = 0
    while k < K:
        a = k
        i0 = order[a]
        wcur = wg[i0]
        m = li[i0]
        k += 1
        while k < K and wg[order[k]] == wcur:
            v = li[order[k]]
            if v > m:
                m = v
            k += 1
        gfirst[ng] = a
        gstart[ng] = wcur
        gmax[ng] = m
        ng += 1
    out_sq = np.empty((ng, 2))
    which = np.empty(ng, np.int64)
    cnt = 0
    for g in range(ng):
        nxt = np.float64(gstart[g + 1]) if g + 1 < ng else gmax[ng - 1]
        end = min(nxt, gmax[g])
        i = order[gfirst[g]]
        if end > np.float64(gstart[g]) and not qu[i]:
            out_sq[cnt, 0] = np.float64(gstart[g])
            out_sq[cnt, 1] = end
            which[cnt] = i
            cnt += 1
    factor = np.empty(cnt)
    mine = np.empty(cnt, np.bool_)
    wout = np.empty(cnt)
    for j in range(cnt):
        i = which[j]
        factor[j] = fa[i]
        mine[j] = pe0[i] > 0
        wout[j] = wv[i]
    return out_sq[:cnt], factor, mine, wout, K


@njit(cache=CACHE, nogil=True)
def nearest(lab, cen):
    """picture._dist2(lab, cen).argmin(1): each colour's nearest centre (squared distance summed channel by channel
    in the same order; the first of equal ones, or the first not-a-number, as argmin)."""
    out = np.empty(len(lab), np.int64)
    for i in range(len(lab)):
        best, bd = 0, 0.0
        for j in range(len(cen)):
            d0, d1, d2 = lab[i, 0] - cen[j, 0], lab[i, 1] - cen[j, 1], lab[i, 2] - cen[j, 2]
            d = d0 * d0
            d += d1 * d1
            d += d2 * d2
            if d != d:
                best = j
                break
            if j == 0 or d < bd:
                best, bd = j, d
        out[i] = best
    return out


@njit(cache=CACHE, nogil=True)
def spread_line(a0, free, pal, pal_lab, strength, idx, err, line, lab, vals, clipped):
    """One step of picture._spread: finishes slanted line `line - 1` (lab = NumPy's OKLab of its colours, vals = the
    colours themselves: nearest palette colour like nearest(), error kept in err), then works out line `line`'s
    colours (each cell the same sums in the same order as the NumPy code: up-left, up, left, up-right) into vals and
    clipped (0..1, for NumPy's OKLab) -> how many cells it has. The OKLab is left to NumPy: its matrix product goes
    to BLAS, whose last bits a loop can't promise to match."""
    rows, cols = idx.shape
    if line > 0:
        y0 = max(0, (line - 1 - cols + 2) // 2)
        for n in range(len(lab)):
            y = y0 + n
            x = line - 1 - 2 * y
            best, bd = 0, 0.0
            for j in range(len(pal_lab)):
                d0, d1, d2 = lab[n, 0] - pal_lab[j, 0], lab[n, 1] - pal_lab[j, 1], lab[n, 2] - pal_lab[j, 2]
                d = d0 * d0
                d += d1 * d1
                d += d2 * d2
                if d != d:
                    best = j
                    break
                if j == 0 or d < bd:
                    best, bd = j, d
            idx[y, x] = best
            fs = strength * free[y, x]
            for c in range(3):
                err[y, x, c] = (vals[n, c] - pal[best, c]) * fs
    y0, y1 = max(0, (line - cols + 2) // 2), min(rows - 1, line // 2)
    for n in range(max(0, y1 - y0 + 1)):
        y = y0 + n
        x = line - 2 * y
        ft = free[y, x]
        for c in range(3):
            t = a0[y, x, c]
            if y > 0 and x > 0:
                t += err[y - 1, x - 1, c] * (1 / 16) * ft
            if y > 0:
                t += err[y - 1, x, c] * (5 / 16) * ft
            if x > 0:
                t += err[y, x - 1, c] * (7 / 16) * ft
            if y > 0 and x + 1 < cols:
                t += err[y - 1, x + 1, c] * (3 / 16) * ft
            vals[n, c] = t
            clipped[n, c] = min(max(t, 0.0), 1.0)
    return max(0, y1 - y0 + 1)


@njit(cache=CACHE, nogil=True)
def _put_vlq(out, p, d):
    """A MIDI wait (7 bits per byte, every byte but the last with the top bit set) at out[p]; returns the next p."""
    k = 1 + (d >= 1 << 7) + (d >= 1 << 14) + (d >= 1 << 21)
    for b in range(k):
        left = k - 1 - b
        out[p + b] = ((d >> (7 * left)) & 0x7F) | (0x80 if left > 0 else 0)
    return p + k


@njit(cache=CACHE, nogil=True)
def midi_events(notes, order, ch, most):
    """midi_out.track_events' bytes (end-of-track not included): order = event numbers in time order
    (note i's on = 2i, its off = 2i + 1); a wait over most is split by empty text events (FF 01 00)."""
    size, last = 0, 0
    for i in range(len(order)):
        j = order[i]
        d = notes[j >> 1, j & 1] - last
        last += d
        while d > most:
            size += 7  # (most takes 4 bytes)
            d -= most
        size += 4 + (d >= 1 << 7) + (d >= 1 << 14) + (d >= 1 << 21)
    out = np.empty(size, np.uint8)
    p, last = 0, 0
    for i in range(len(order)):
        j = order[i]
        d = notes[j >> 1, j & 1] - last
        last += d
        while d > most:
            p = _put_vlq(out, p, most)
            out[p], out[p + 1], out[p + 2] = 0xFF, 0x01, 0x00
            p += 3
            d -= most
        p = _put_vlq(out, p, d)
        on = (j & 1) == 0
        out[p] = (0x90 if on else 0x80) | ch
        out[p + 1] = notes[j >> 1, 2] & 0xFF
        out[p + 2] = notes[j >> 1, 3] & 0xFF if on else 0
        p += 3
    return out


@njit(cache=CACHE, nogil=True)
def limit_live(st, x, frames):
    """synth.live_limit one frame at a time: x = the piece's floats (left, right, left...), changed in place."""
    for f in range(frames):
        n = int(st[LIM_N])
        r = LIM_HEAD + 2 * (n % LIM_RING)
        left, right = float(x[2 * f]), float(x[2 * f + 1])
        st[r], st[r + 1] = left, right
        st[LIM_PEAK] = max(st[LIM_PEAK], abs(left), abs(right))
        d = n - LIM_DELAY
        if st[LIM_ON]:
            if d >= 0:
                st[LIM_MIX] = min(1.0, st[LIM_MIX] + LIM_STEP)
        else:
            st[LIM_MIX] = max(0.0, st[LIM_MIX] - LIM_STEP)
        mix = st[LIM_MIX]
        if d >= 0:
            t = (d % LIM_BLOCK + 1) / LIM_BLOCK
            gain = st[LIM_G0] * (1 - t) + st[LIM_G1] * t
            r = LIM_HEAD + 2 * (d % LIM_RING)
            x[2 * f] = left * (1 - mix) + st[r] * gain * mix
            x[2 * f + 1] = right * (1 - mix) + st[r + 1] * gain * mix
        else:
            x[2 * f] = left * (1 - mix)
            x[2 * f + 1] = right * (1 - mix)
        n += 1
        st[LIM_N] = n
        if n % LIM_BLOCK == 0:
            if n >= 2 * LIM_BLOCK:
                st[LIM_ENV] = max(st[LIM_PREV], st[LIM_PEAK], st[LIM_ENV] * LIM_FALL)
                st[LIM_G0], st[LIM_G1] = st[LIM_G1], LIM_CEILING / max(st[LIM_ENV], LIM_CEILING)
            st[LIM_PREV], st[LIM_PEAK] = st[LIM_PEAK], 0.0


# (BASS's DSPPROC: handle, channel, the piece, its bytes, user = the limiter's row of numbers)
@cfunc(types.void(types.uint32, types.uint32, types.CPointer(types.float32), types.uint32,
                  types.CPointer(types.float64)), cache=CACHE)
def limit_dsp(dsp, channel, buf, length, user):
    limit_live(carray(user, (LIM_SIZE,)), carray(buf, (length // 4,)), length // 8)


@njit(cache=CACHE, nogil=True)
def smooth_sor(flat, red, black, wide, om, times):
    """nested.smooth_values' loop: red-black over-relaxation on a grid with a zero border (flat), red / black = the
    cells each half changes, the same sums in the same order."""
    for _ in range(times):
        for part in (red, black):
            for i in part:
                g = (flat[i - 1] + flat[i + 1] + flat[i - wide] + flat[i + wide]) / 4
                here = flat[i]
                flat[i] = here + om * (g - here)


@njit(cache=CACHE, nogil=True)
def area_spots(x, y, levels, start, off, lab, outside, edge, ex, ey, es, x0, y0, x1, y1, eps):
    """faces.Faces.area_at for every spot (x, y): the same halving over its slab's lines, and the same recount of
    every line of the slab for a spot right by one (worked out as _x does, so the very same numbers)."""
    out = np.empty(len(x), np.int64)
    nl = len(levels)
    for i in range(len(x)):
        xi, yi = x[i], y[i]
        a, b = 0, nl  # (searchsorted "right": how many levels are at or under y)
        while a < b:
            m = (a + b) >> 1
            if levels[m] <= yi:
                a = m + 1
            else:
                b = m
        s = a - 1
        if s < 0 or s >= nl - 1:
            out[i] = lab[outside]
            continue
        first, end = start[s], start[s + 1]
        lo, hi = first, end
        while lo < hi:
            mid = (lo + hi) >> 1
            if ex[mid] + es[mid] * (yi - ey[mid]) < xi:
                lo = mid + 1
            else:
                hi = mid
        margin = eps * (end - first + 1)
        near = False
        for side in (lo - 1, lo):
            if first <= side < end:
                e = edge[side]
                if abs(x0[e] + (x1[e] - x0[e]) * (yi - y0[e]) / (y1[e] - y0[e]) - xi) <= margin:
                    near = True
        if near:
            lo = first
            for j in range(first, end):
                e = edge[j]
                if x0[e] + (x1[e] - x0[e]) * (yi - y0[e]) / (y1[e] - y0[e]) < xi:
                    lo += 1
        out[i] = lab[off[s] + lo - first]
    return out


def warm():
    """Compiles (or loads) every loop with the same kinds of values the program hands them."""
    i1, f, b = np.zeros(1, np.int64), 0.0, np.zeros(1, bool)
    s, e, key, color, first, _ = paint_order(np.zeros((1, 6), np.int64), np.zeros(1, np.int64), i1, 1,
                                             np.zeros(0, bool), np.arange(15, dtype=np.int64), 16, 0)
    order_screen(s, e, key, color, first, 0, 1, f, f, f, f, f, f, b, False, f, f)
    order_rects(s, e, key, color, first, 0, 1, f, f, f, f, f, f, b, False, f, f, 0, 0, 1, i1, i1)
    last_on_pixels(i1.copy(), i1.copy(), i1.copy(), i1.copy(), 0, 0, 1, i1, i1)
    note_top(np.full(1, -1, np.int64), 0, 0, 1, 1, i1, i1, i1, i1)
    paint_notes(np.zeros((1, 3), np.uint8), 0, 0, 1, 1, i1, i1, i1, i1, i1, np.zeros((1, 2, 3), np.uint8))
    overlap_sweep(np.zeros((1, 6), np.int64), i1, i1)
    overlap_order(np.zeros((1, 6), np.int64))
    midi_events(np.zeros((1, 6), np.int64), np.zeros(2, np.int64), 0, 1)
    note_stretches(np.zeros((1, 4), np.int64), True, 0, np.zeros(0, np.int64))
    clash_levels(np.zeros((1, 3), np.int64), np.array([0, 1, 1], np.int64), i1.copy())
    key_order(i1, i1, i1, i1)
    ring_runs(i1, i1, i1, i1, 0.5, 1)
    clash_pairs(np.zeros((1, 4), np.int64), 1)
    wave_fill(i1, 0, 1, i1, np.zeros(1), np.zeros(1))
    note_ends(i1, i1 + 1, i1, True, 2, 0.0, 50.0, True, np.zeros(1), 0.0, np.int64(1) << 40)
    nearest(np.zeros((1, 3)), np.zeros((1, 3)))
    m = np.zeros((1, 3))
    spread_line(np.zeros((1, 1, 3)), np.zeros((1, 1)), m, m, 1.0, np.zeros((1, 1), np.int64), np.zeros((1, 1, 3)), 0,
                m[:0], m, m.copy())
    f1, i2 = np.zeros(1), np.zeros(2, np.int64)
    i0, f0 = np.zeros(0, np.int64), np.zeros(0)
    p, nm, sr, w, s, rp, _ = hz_expand(i1, i1, np.ones(1), i1 + 1, i1, i1 - 1, b, 1, 0.5, 0.1, f1, np.ones(1),
                                       np.ones(1), f1)
    p, nm, sr, w, s, late, ns, npp, place, took = hz_late(p, nm, sr, w, s, f1, True, 0.5, f1, np.ones(1, np.int64), f1, 1)
    hz_finish(p, nm, sr, w, s, late, ns, npp, place, np.zeros(1), np.full(1, 9.0), np.ones(1), True, True, b, f1,
              np.ones(1), f1, f1, np.ones(1), np.full(1, 9.0), np.ones(1), np.ones(1), np.ones(1), 0.01)
    hz_finish(p, nm, sr, w, s, late, ns, npp, place, np.zeros(1), np.full(1, 9.0), np.ones(1), True, True, b, f1,
              np.ones(1), f1, f1, np.ones(1), np.full(1, 9, np.int64), np.ones(1), np.ones(1), np.ones(1), 0.01)
    hz_order(np.arange(300, dtype=np.int64))
    area_spots(f1, f1, np.zeros(2), i2, i2, i2, 0, i1, f1, f1, f1, f1, f1, f1, f1, f)
    smooth_sor(np.zeros(9), np.array([4], np.int64), np.zeros(0, np.int64), 3, 1.9, 1)
