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
  midi_events    midi_out.track_data: one track's note-ons / offs as MIDI file bytes
  note_stretches engine.stretches (Multi channel): one shape's notes on a key joined into stretches
  nearest        picture._dist2(...).argmin(1) (Image to notes): each colour's nearest palette colour
  spread_line    picture._spread: the error spreading, one slanted line at a time (its OKLab left to NumPy)
The first start compiles them (about a second, in the background); the result is kept in __pycache__ for the next
starts (not in the exe: its files can't be kept, so it compiles each start)."""

import sys

import numpy as np
from numba import njit

CACHE = not getattr(sys, "frozen", False)


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
def paint_order(notes, by_start, rank, nranks, pics, nslots, picgroup):
    """The notes kept in painting order between pictures (redone when the notes or the selection change): by owner
    rank, each owner's in by_start's order (np.argsort of the starts, stable). -> start, end, key, colour (as
    roll_draw.note_rects' screen() works it out), first (rank r's notes = first[r] .. first[r + 1]), the longest note."""
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
        c = notes[i, 4] % nslots
        if len(pics) and pics[min(max(notes[i, 5], 0), len(pics) - 1)]:
            c = picgroup + min(notes[i, 4], 15)
        color[o] = c * 32 + min(notes[i, 3], 127) // 4
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
    """midi_out.track_data's events as bytes (end-of-track not included): order = event numbers in time order
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


def warm():
    """Compiles (or loads) every loop with the same kinds of values the program hands them."""
    i1, f, b = np.zeros(1, np.int64), 0.0, np.zeros(1, bool)
    s, e, key, color, first, _ = paint_order(np.zeros((1, 6), np.int64), np.zeros(1, np.int64), i1, 1,
                                             np.zeros(0, bool), 15, 16)
    order_screen(s, e, key, color, first, 0, 1, f, f, f, f, f, f, b, False, f, f)
    order_rects(s, e, key, color, first, 0, 1, f, f, f, f, f, f, b, False, f, f, 0, 0, 1, i1, i1)
    last_on_pixels(i1.copy(), i1.copy(), i1.copy(), i1.copy(), 0, 0, 1, i1, i1)
    note_top(np.full(1, -1, np.int64), 0, 0, 1, 1, i1, i1, i1, i1)
    paint_notes(np.zeros((1, 3), np.uint8), 0, 0, 1, 1, i1, i1, i1, i1, i1, np.zeros((1, 2, 3), np.uint8))
    overlap_sweep(np.zeros((1, 6), np.int64), i1, i1)
    overlap_order(np.zeros((1, 6), np.int64))
    midi_events(np.zeros((1, 6), np.int64), np.zeros(2, np.int64), 0, 1)
    note_stretches(np.zeros((1, 4), np.int64), True, 0, np.zeros(0, np.int64))
    nearest(np.zeros((1, 3)), np.zeros((1, 3)))
    m = np.zeros((1, 3))
    spread_line(np.zeros((1, 1, 3)), np.zeros((1, 1)), m, m, 1.0, np.zeros((1, 1), np.int64), np.zeros((1, 1, 3)), 0,
                m[:0], m, m.copy())
