"""The plain-Python twins of our own compiled loops (files/native.py, built from native/loops in Rust): the
slowest note work with lots of notes. They were compiled by Numba until 2026-11 (PLAN.md B); now they're the
last resort when the library refuses a call (files/loop_marks.py: their marks do nothing), and the very source
the library copies. Without the library: when Numba is installed (from source: Mac, an older Linux) this very
file is compiled by it once more as its own module (files/numba_loops.py) and used like the library; else the
NumPy code they replace is used, not these.
Each gives exactly what that NumPy code gives (dev/tests/fast_loops.py compares them):
  paint_order    the notes kept in painting order for the next two (redone when they or the selection change)
  order_screen   roll_draw.note_rects' screen(): the notes on screen, their pixels, colours, in painting order
  order_rects    order_screen + last_on_pixels in one (the notes in between never made)
  last_on_pixels roll_draw.note_rects' end: notes on the very same pixels (only the last shows) left out
  liquify_rects  roll_liquify.liquify_paint: a shape's notes on screen in painting order (by their slot's rank) + colours
  note_top       roll_draw.note_pixels: which note is on top at every pixel (and whether it's its outline)
  paint_notes    roll_draw.paint_region: the notes' colours straight into the picture (the last painted wins)
  overlap_order  engine.resolve_overlaps' groups and sort (first_seen + overlap_order)
  overlap_sweep  engine.resolve_overlaps after its sort: cut / stretch / merge the notes on one key and slot
  winner_pieces  engine.winner_stretches (Remove overlaps, split around): the winning note at every moment, a heap
  recolour_apart recolour.kept_apart's walk through bursts of 3+ overlapping notes (Recolour)
  dice_mix       recolour.dice: each note's number from its place, colour and the seed (splitmix64)
  stir_spots     recolour.stir_from's middle (Bulge, the more bends, Turn / Twist, edges, Squeeze), note by note
  stir_nearest   recolour.nearest after its sort: each spot's nearest note
  stir_wrap      recolour.Rows.wrapped (Stir's Wrap around)   (all three on half the cores: on_cores)
  liquify_sample liquify.sample: Liquify's map at spots of the colour box (on half the cores)
  warp_newton    warp._newton: the Warp's bend turned round, Newton steps each spot (a fold's pieces kept inside)
  warp_exact     warp.exact_offsets: each note's spot fetched exactly (Newton from the map's answer)
  warp_soft      liquify.found's Edges: Smooth (the Warp's): each note's pick, its looks + vote (both on half the cores)
  midi_events    midi_out.track_events: one track's note-ons / offs as MIDI file bytes
  midi_track     midi_out's tracks made side by side: one track's notes sorted (radix_order) + midi_events
  note_stretches engine.stretches (Multi channel): one shape's notes on a key joined into stretches
  clash_levels   engine.clash_levels: Hz bass layers' notes clashing on a key -> their levels
  any_overlap    engine.heard_notes: does a notes shape overlap itself on a track + key (one pass, no sort)
  turn_sample    merge._sampled: a turned / slanted / taller paste's grid of spots, a key row at a time
  turn_layers    merge.overlap_layers' heap loop: each note's layer (the first free one)
  note_ends      hzbass.note_lengths: a Hz bass's notes' ends by its Note length
  wave_fill      hzbass.wave_ticks: each note's wave length, one key at a time
  hz_expand ... hz_finish  hz_grid.KeyGrid.made for the plain settings (made_fast): a key's repeats
  key_order      np.lexsort by key, then up to 3 more columns (runs already in order merged: quick; key_order_cores =
                 the key groups on half the cores: on_cores)
  radix_order    np.argsort(kind="stable") of whole numbers (Copy / Export to Domino: domino_clip.start_key_order)
  ring_runs      roll_draw.ring_parts' runs: the selection's red outline, each key row's notes joined
  clash_pairs    engine.clash_arrays (Multi channel): which owners' stretches clash
  nearest        picture._dist2(...).argmin(1) (Image to notes): each colour's nearest palette colour
  spread_line    picture._spread: the error spreading, one slanted line at a time (its OKLab left to NumPy)
  area_spots     faces.Faces.area_at: the area each spot is in (custom shapes' Fill / Spam, area colours)
  smooth_sor     nested.smooth_values' loop (Nested fill, Blend)
  gap_ticks      tempo_file.keep_gaps' work on one Keep timing part (export, Conductor pane checks)
  gap_plan       tempo_file.file_plan's gaps of one Keep timing part as file tempo runs (with tempo._fit_blocks,
                 _fit_exact, _flat_pair (+ its full search), _near_pair, _nearest1, next_safe as *_n)
  limit_live     synth.live_limit, a frame at a time (the library's limit_dsp is its C callback for BASS)
  midi_walk      midi_scan.walk (Load MIDI: a track's events read + checked), the very same function compiled
  midi_rewrite   midi_scan.rewrite (a track written again with its ticks moved), the very same function compiled
  midi_note_ticks  midi_scan.note_ticks (Keep timing's anchors on a loaded MIDI), the very same function compiled
  midi_busy_hits   midi_scan.busy_hits (own notes on a loaded note's channel + key), the very same function compiled
  midi_gather    midi_scan.gather (Extract: a loaded track's notes in a box), the very same function compiled
  midi_index, midi_close, midi_grid, midi_halve   midi_index's functions (Load MIDI's pass: notes paired, the index
                 to read a track from anywhere, the overview picture), the very same functions compiled
  midi_cover_many  roll_loaded's exact pictures: many small tracks painted in one call (TrackReader.read each)
  play_read, play_order, play_order_count, play_order_place, play_hist, play_emit, play_programs, play_stamp,
  play_condense, play_stamp_pre   midi_play's functions (playing a loaded MIDI), compiled
  piece_track    midi_piece.piece_track (Rendered ahead: a loaded track's piece copied from its bytes), compiled
  piece_held_ons, piece_held_head, piece_note_ons   midi_piece's (a piece's held copies, each at its own note-on)
  piece_runs_sort, piece_runs_merge, piece_counted_track, piece_order_ok, piece_open_at   midi_piece's (Merlin pieces' counted
                 notes, PLAN D2)
  all_tops       track_list.loaded_tops: a loaded MIDI's tracks in the track list's All tracks row
  dc_block       synth.dc_block (Rendered ahead's DC blocker, ahead of its limiter)"""

import math
import threading

import numpy as np

from files import midi_index as _index, midi_piece, midi_play, midi_scan
from files.loop_marks import List, njit, prange, set_threads
from files.synth import dc_block as _dc_block
from files.synth import (LIM_BLOCK, LIM_CEILING, LIM_DELAY, LIM_ENV, LIM_FALL, LIM_G0, LIM_G1, LIM_HEAD, LIM_MIX,
                         LIM_N, LIM_ON, LIM_PEAK, LIM_PREV, LIM_RING, LIM_STEP)


OWN0 = 1 << 32  # (engine.OWN0)


@njit(nogil=True)
def last_on_pixels(x0, x1, key, color, left, right, keys, row0, row1, out=None):
    """The notes' rectangles (x0, y0, x1, y1, colour) with x clamped to left..right; of notes up to 3 pixels long
    on the very same pixels only the last is kept (the others are painted over).
    out: the library's room (unused here)."""
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


@njit(nogil=True)
def liquify_rects(on, slot, rank, colour, x0, x1, key, vq, out=None):
    """One shape's notes on screen (rows on, start order) in painting order: by their slot's rank, notes of a rank
    as they come (np.argsort(kind="stable")); no ranks (empty) = as they are. -> (x0, x1, key, colour number).
    out: the library's room to use again (not used here)."""
    n = len(on)
    most = 0
    if len(rank):
        for i in range(n):
            most = max(most, rank[slot[on[i]]])
    at = np.zeros(most + 2, np.int64)
    if len(rank):
        for i in range(n):
            at[rank[slot[on[i]]] + 1] += 1
        for k in range(1, len(at)):
            at[k] += at[k - 1]
    ox0 = np.empty(n, np.int64)
    ox1 = np.empty(n, np.int64)
    okey = np.empty(n, np.int64)
    ocode = np.empty(n, np.int64)
    for i in range(n):
        s = slot[on[i]]
        k = rank[s] if len(rank) else 0
        p = at[k]
        at[k] += 1
        ox0[p], ox1[p], okey[p], ocode[p] = x0[i], x1[i], key[i], colour[s] * 32 + vq[i]
    return ox0, ox1, okey, ocode


@njit(nogil=True)
def paint_order(notes, by_start, rank, nranks, pics, cols, picgroup, ghost):
    """The notes kept in painting order between pictures (redone when the notes or the selection change): by each
    NOTE's rank (roll_draw.stack_keys: track / loudness / shape, the selected ones last), each rank's in by_start's
    order (np.argsort of the starts, stable). cols = roll_shared.slot_colours. ->
    start, end, key, colour (as roll_draw.note_rects' screen() works it out; velocity -1 = ghost, a mask hides it;
    -2 - velocity = a track the track list shows faintly behind the highlighted one),
    first (rank r's notes = first[r] .. first[r + 1]), the longest note."""
    nslots = len(cols)
    n = len(by_start)
    first = np.zeros(nranks + 1, np.int64)
    for i in range(n):
        first[rank[i] + 1] += 1
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
        r = rank[i]
        o = at[r]
        at[r] += 1
        s[o], e[o], key[o] = notes[i, 0], notes[i, 1], notes[i, 2]
        longest = max(longest, notes[i, 1] - notes[i, 0])
        sl = notes[i, 4]
        if sl >= OWN0:  # (a track added by hand: its channel's colour, roll_shared.note_colours)
            ch = sl & 15
            c = ch if ch < 9 else 15 if ch == 9 else ch - 1
        else:
            c = cols[sl % nslots]
        if len(pics) and pics[min(max(notes[i, 5], 0), len(pics) - 1)]:
            c = picgroup + min(notes[i, 4], 15)
        if notes[i, 3] < 0:  # (-1: a mask's ghost, shade 31; -2 - velocity: a track shown behind the highlighted
            v = notes[i, 3]  # one, its velocity's shade 0..30 faded)
            color[o] = (ghost + c) * 32 + (31 if v == -1 else min(30, (-v - 2) // 4))
        else:
            color[o] = c * 32 + min(notes[i, 3], 127) // 4
    return s, e, key, color, first, longest


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


NO_END = 1 << 30  # (a colour number's flag: the note's end was cut short for painting, roll_draw.covered_ends)


@njit(nogil=True)
def note_top(top_px, half, kb, top, w, h, x0, y0, x1, y1, color, shade):
    """top_px (the region's pixels, row by row, -1 filled) = note number * 2 + (1 = outline) of the note painted
    last on each pixel. Same pixels as a canvas rectangle with a 1-pixel outline: x0..x1 and y0..y1 inclusive.
    half = 1 where a note under the top one starts / ends (its fill shaded there), only with shade (the Look
    option "Show hidden note starts / ends", off by default: user 2026-10-31)."""
    iw = w - kb
    for k in range(len(x0)):
        a, b = max(y0[k], top), min(y1[k] + 1, h)
        c, d = max(x0[k], kb), min(x1[k] + 1, w)
        if b <= a or d <= c:
            continue
        solid = x1[k] - x0[k] < 2 or y1[k] - y0[k] < 2  # too small to have a fill: all outline
        left, right = c == x0[k], d == x1[k] + 1  # (its left / right outline on screen?)
        end = right and (color[k] & NO_END) == 0  # (its end column counts as an end)
        fill, edge = 2 * k, 2 * k + 1
        for y in range(a, b):
            row = (y - top) * iw - kb
            v = edge if solid or y == y0[k] or y == y1[k] else fill
            for x in range(c, d):
                p = row + x
                if shade:
                    mine = 1 if (left and x == c) or (end and x == d - 1) else 0
                    half[p] = (2 if half[p] != 0 else 0) | mine  # (1 = its start / end here, 2 = one under it)
                top_px[p] = v
            if v == fill:
                if left:
                    top_px[row + c] = edge
                if right:
                    top_px[row + d - 1] = edge
    for p in range(len(half)):
        half[p] = 1 if half[p] >= 2 else 0


@njit(nogil=True)
def paint_notes(img, kb, top, w, h, x0, y0, x1, y1, color, rgb, shade):
    """Each note's colours painted straight into img (pixels x 3), the last painted winning: the same pixels as
    note_top's notes on top coloured in, without that list. With shade, where a note under it starts / ends, a
    note's fill is shaded halfway to its edge colour."""
    iw = w - kb
    # (a key row's notes all cover the same pixel rows, so its first row stands for it: 1 = the top note's start /
    # end in that column, 2 = one under it)
    flags = np.zeros(max(h - top, 0) * max(iw, 0) if shade else 0, np.uint8)
    under = np.zeros(max(iw, 0), np.int64)
    for k in range(len(x0)):
        a, b = max(y0[k], top), min(y1[k] + 1, h)
        c, d = max(x0[k], kb), min(x1[k] + 1, w)
        if b <= a or d <= c:
            continue
        solid = x1[k] - x0[k] < 2 or y1[k] - y0[k] < 2
        left, right = c == x0[k], d == x1[k] + 1
        end = right and (color[k] & NO_END) == 0
        q = color[k] & (NO_END - 1)
        f0, f1, f2 = rgb[q, 0, 0], rgb[q, 0, 1], rgb[q, 0, 2]
        e0, e1, e2 = rgb[q, 1, 0], rgb[q, 1, 1], rgb[q, 1, 2]
        first = (a - top) * iw - kb
        m = 0  # (columns with a note under it starting / ending: under[:m])
        if shade:
            for x in range(first + c, first + d):
                if flags[x] != 0:
                    under[m] = x - first
                    m += 1
                    flags[x] = 2
            if left:
                flags[first + c] |= 1
            if end:
                flags[first + d - 1] |= 1
        s0 = s1 = s2 = 0
        if m:
            s0, s1, s2 = (np.int64(f0) + e0) // 2, (np.int64(f1) + e1) // 2, (np.int64(f2) + e2) // 2
        for y in range(a, b):
            row = (y - top) * iw - kb
            if solid or y == y0[k] or y == y1[k]:
                for x in range(c, d):
                    img[row + x, 0], img[row + x, 1], img[row + x, 2] = e0, e1, e2
            else:
                for x in range(c, d):
                    img[row + x, 0], img[row + x, 1], img[row + x, 2] = f0, f1, f2
                for i in range(m):
                    x = under[i]
                    img[row + x, 0], img[row + x, 1], img[row + x, 2] = s0, s1, s2
                if left:
                    img[row + c, 0], img[row + c, 1], img[row + c, 2] = e0, e1, e2
                if right:
                    img[row + d - 1, 0], img[row + d - 1, 1], img[row + d - 1, 2] = e0, e1, e2


@njit(nogil=True)
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


@njit(nogil=True)
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
            if k - j > 1:
                _tie_order(notes, order, j, k)
            j = k
    return group, order


TIE_SORT_MOST = 32  # (a same-start run longer than this: merge sorted; insertion went n * n on stacked copies)


@njit(nogil=True)
def _tie_order(notes, order, j, k):
    """order[j:k] (one start) by velocity, quietest first, then the longest first; alike ones kept in their order.
    Short runs: insertion; long ones: a merge sort (the same order: 80,000 copies on one tick took 2.9 s)."""
    if k - j <= TIE_SORT_MOST:
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
        return
    n = k - j
    src = order[j:k].copy()
    dst = np.empty(n, np.int64)
    width = 1
    while width < n:
        for lo in range(0, n, 2 * width):
            mid = min(lo + width, n)
            hi = min(lo + 2 * width, n)
            p, q, o = lo, mid, lo
            while p < mid and q < hi:
                u, v = src[p], src[q]
                # (v first only when strictly before u: quieter, or as loud and longer)
                if notes[v, 3] < notes[u, 3] or (notes[v, 3] == notes[u, 3] and notes[v, 1] > notes[u, 1]):
                    dst[o] = v
                    q += 1
                else:
                    dst[o] = u
                    p += 1
                o += 1
            while p < mid:
                dst[o] = src[p]
                p += 1
                o += 1
            while q < hi:
                dst[o] = src[q]
                q += 1
                o += 1
        src, dst = dst, src
        width *= 2
    order[j:k] = src


@njit(nogil=True)
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


@njit(nogil=True)
def _heap_push(hr, hi, size, r, i):
    """A note (rank r, place i) into winner_pieces' heap (lowest rank, then place, on top) -> its new size."""
    k = size
    hr[k], hi[k] = r, i
    while k > 0:
        p = (k - 1) // 2
        if hr[p] < hr[k] or (hr[p] == hr[k] and hi[p] <= hi[k]):
            break
        hr[p], hr[k] = hr[k], hr[p]
        hi[p], hi[k] = hi[k], hi[p]
        k = p
    return size + 1


@njit(nogil=True)
def _heap_pop(hr, hi, size):
    """The top of winner_pieces' heap taken off -> its new size."""
    size -= 1
    hr[0], hi[0] = hr[size], hi[size]
    k = 0
    while True:
        a, b, m = 2 * k + 1, 2 * k + 2, k
        if a < size and (hr[a] < hr[m] or (hr[a] == hr[m] and hi[a] < hi[m])):
            m = a
        if b < size and (hr[b] < hr[m] or (hr[b] == hr[m] and hi[b] < hi[m])):
            m = b
        if m == k:
            return size
        hr[m], hr[k] = hr[k], hr[m]
        hi[m], hi[k] = hi[k], hi[m]
        k = m


@njit(nogil=True)
def winner_pieces(group, s, e, rank, order):
    """engine.winner_stretches' pieces (Remove overlaps, split around): in each group, at every moment the sounding
    note with the lowest rank wins; walked once in time (order: by group, then start) with the sounding notes in a
    heap, so piling notes never makes it slower. -> (each piece's note, start, end, how many), by group, then time."""
    n = len(order)
    hr, hi = np.empty(n, np.int64), np.empty(n, np.int64)
    note, ps, pe = np.empty(2 * n, np.int64), np.empty(2 * n, np.int64), np.empty(2 * n, np.int64)
    m, i = 0, 0
    while i < n:
        g = group[order[i]]
        size, cur, since = 0, -1, 0
        t = s[order[i]]
        while True:
            while i < n and group[order[i]] == g and s[order[i]] <= t:  # (every note starting by now)
                size = _heap_push(hr, hi, size, rank[order[i]], order[i])
                i += 1
            while size and e[hi[0]] <= t:  # (ended ones off the top)
                size = _heap_pop(hr, hi, size)
            top = hi[0] if size else -1
            if top != cur:
                if cur >= 0 and t > since:
                    if m == len(note):  # (never: a note's pieces end at a start or an end, 2 per note at most)
                        break
                    note[m], ps[m], pe[m] = cur, since, t
                    m += 1
                cur, since = top, t
            nxt = s[order[i]] if i < n and group[order[i]] == g else -1
            if cur < 0 and nxt < 0:
                break
            if cur < 0:
                t = nxt
            elif nxt >= 0 and nxt < e[cur]:
                t = nxt
            else:
                t = e[cur]
    return note, ps, pe, m


@njit(nogil=True)
def stir_nearest_one(keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own, out):
    """recolour.nearest after its sort (_sorted_notes): for each spot (mid, key) the note nearest it, on the nearest
    key holding notes (lower on a tie), the note there whose middle is nearest (the earlier one on a tie); own: spot
    i is note i's own spot moved, the note itself as near wins. One core."""
    for s in range(len(mid)):
        out[s] = _near_one(s, keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own)


@njit(nogil=True, parallel=True)
def _stir_nearest_cores(keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own, out):
    for s in prange(len(mid)):
        out[s] = _near_one(s, keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own)


def stir_nearest(keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own, out):
    """stir_nearest_one on half the cores (on_cores)."""
    on_cores(_stir_nearest_cores, stir_nearest_one, len(mid), keys, first, last, line, ms, nk, nm, o, base, top,
             gap, mid, key, own, out)


@njit(nogil=True)
def _wrap_one(i, keys, a, b, mid, key):
    """recolour.Rows.wrapped for spot i: past its key row's notes = back in on that row's other side."""
    nkeys = len(keys)
    q = np.rint(key[i])
    lo_, hi_ = 0, nkeys
    while lo_ < hi_:
        h = (lo_ + hi_) >> 1
        if keys[h] < q:
            lo_ = h + 1
        else:
            hi_ = h
    r = min(max(lo_, 0), nkeys - 1)
    j = max(r - 1, 0)
    row = j if abs(keys[j] - q) <= abs(keys[r] - q) else r
    m, s, e = mid[i], a[row], b[row]
    if m < s or m > e:
        return s + np.mod(m - s, max(e - s, 1.0))
    return m


@njit(nogil=True)
def stir_wrap_one(keys, a, b, mid, key, out):
    """recolour.Rows.wrapped (Stir's Wrap around) for every spot. One core."""
    for i in range(len(mid)):
        out[i] = _wrap_one(i, keys, a, b, mid, key)


@njit(nogil=True, parallel=True)
def _stir_wrap_cores(keys, a, b, mid, key, out):
    for i in prange(len(mid)):
        out[i] = _wrap_one(i, keys, a, b, mid, key)


def stir_wrap(keys, a, b, mid, key, out):
    """stir_wrap_one on half the cores (on_cores)."""
    on_cores(_stir_wrap_cores, stir_wrap_one, len(mid), keys, a, b, mid, key, out)


@njit(nogil=True)
def _sample_at(i, a, x, y, out):
    """liquify.sample for spot i: the map a blended between its 4 grid points (the same sums, the same order)."""
    gy, gx = a.shape[0], a.shape[1]
    fx = min(max(x[i], 0.0), 1.0) * (gx - 1)
    fy = min(max(y[i], 0.0), 1.0) * (gy - 1)
    i0 = min(np.int64(fx), gx - 2)
    j0 = min(np.int64(fy), gy - 2)
    tx, ty = fx - i0, fy - j0
    for c in range(2):
        out[i, c] = ((a[j0, i0, c] * (1 - tx) + a[j0, i0 + 1, c] * tx) * (1 - ty)
                     + (a[j0 + 1, i0, c] * (1 - tx) + a[j0 + 1, i0 + 1, c] * tx) * ty)


@njit(nogil=True)
def liquify_sample_one(a, x, y, out):
    """liquify.sample (Liquify's map at spots of the colour box), one core."""
    for i in range(len(x)):
        _sample_at(i, a, x, y, out)


@njit(nogil=True, parallel=True)
def _liquify_sample_cores(a, x, y, out):
    for i in prange(len(x)):
        _sample_at(i, a, x, y, out)


def liquify_sample(a, x, y, out):
    """liquify_sample_one on half the cores (on_cores)."""
    on_cores(_liquify_sample_cores, liquify_sample_one, len(x), a, x, y, out)


@njit(nogil=True)
def _sample2(a, x, y):
    """liquify.sample of the map a (gy, gx, 2) at one spot: (across, up), the same sums in the same order."""
    gy, gx = a.shape[0], a.shape[1]
    fx = min(max(x, 0.0), 1.0) * (gx - 1)
    fy = min(max(y, 0.0), 1.0) * (gy - 1)
    i0 = min(np.int64(fx), gx - 2)
    j0 = min(np.int64(fy), gy - 2)
    tx, ty = fx - i0, fy - j0
    v0 = (a[j0, i0, 0] * (1 - tx) + a[j0, i0 + 1, 0] * tx) * (1 - ty) + (a[j0 + 1, i0, 0] * (1 - tx)
                                                                        + a[j0 + 1, i0 + 1, 0] * tx) * ty
    v1 = (a[j0, i0, 1] * (1 - tx) + a[j0, i0 + 1, 1] * tx) * (1 - ty) + (a[j0 + 1, i0, 1] * (1 - tx)
                                                                        + a[j0 + 1, i0 + 1, 1] * tx) * ty
    return v0, v1


@njit(nogil=True)
def _straight_at(P, x, y):
    """warp._straight at one spot: (value across, up, d/dx across, up, d/dy across, up), the same sums in the same
    order (value = _sample2's)."""
    gy, gx = P.shape[0], P.shape[1]
    fx = min(max(x, 0.0), 1.0) * (gx - 1)
    fy = min(max(y, 0.0), 1.0) * (gy - 1)
    i0 = min(np.int64(fx), gx - 2)
    j0 = min(np.int64(fy), gy - 2)
    tx, ty = fx - i0, fy - j0
    mx = (1.0 if (x >= 0.0 and x <= 1.0) else 0.0) * float(gx - 1)
    my = (1.0 if (y >= 0.0 and y <= 1.0) else 0.0) * float(gy - 1)
    out = np.empty(6)
    for c in range(2):
        a, b, cc, d = P[j0, i0, c], P[j0, i0 + 1, c], P[j0 + 1, i0, c], P[j0 + 1, i0 + 1, c]
        lo, hi = a * (1 - tx) + b * tx, cc * (1 - tx) + d * tx
        out[c] = lo * (1 - ty) + hi * ty
        out[2 + c] = ((b - a) * (1 - ty) + (d - cc) * ty) * mx
        out[4 + c] = (hi - lo) * my
    return out[0], out[1], out[2], out[3], out[4], out[5]


@njit(nogil=True)
def _eased_part(v, g):
    """warp._eased's parts(v, g) with slopes for one number: (cell, eased share, its slope)."""
    f = min(max(v, 0.0), 1.0) * (g - 1)
    i = min(np.int64(f), g - 2)
    t = f - i
    s = t * t * (3.0 - 2.0 * t)
    m = 1.0 if (v >= 0.0 and v <= 1.0) else 0.0
    return i, s, 6.0 * t * (1.0 - t) * m * float(g - 1)


@njit(nogil=True)
def _eased_at(P, x, y):
    """warp._eased(P, x, y, slopes=True) at one spot: (value across, up, d/dx across, up, d/dy across, up)."""
    i, sx, dx = _eased_part(x, P.shape[1])
    j, sy, dy = _eased_part(y, P.shape[0])
    out = np.empty(6)
    for c in range(2):
        a, b, cc, d = P[j, i, c], P[j, i + 1, c], P[j + 1, i, c], P[j + 1, i + 1, c]
        lo, hi = a + (b - a) * sx, cc + (d - cc) * sx
        out[c] = lo + (hi - lo) * sy
        out[2 + c] = ((b - a) + ((d - cc) - (b - a)) * sy) * dx
        out[4 + c] = (hi - lo) * dy
    return out[0], out[1], out[2], out[3], out[4], out[5]


@njit(nogil=True)
def _bern_part(v, n):
    """warp._bern with slopes for one number along n cells: (cell, 4 weights, 4 slopes)."""
    f = min(max(v, 0.0), 1.0) * float(n)
    i = min(np.int64(f), n - 1)
    t = f - i
    u = 1.0 - t
    m = (1.0 if (v >= 0.0 and v <= 1.0) else 0.0) * float(n)
    w = np.empty(4)
    d = np.empty(4)
    w[0], w[1], w[2], w[3] = u * u * u, 3.0 * t * u * u, 3.0 * t * t * u, t * t * t
    d[0], d[1] = -3.0 * u * u * m, 3.0 * u * (u - 2.0 * t) * m
    d[2], d[3] = 3.0 * t * (2.0 * u - t) * m, 3.0 * t * t * m
    return i, w, d


@njit(nogil=True)
def _net_at(C, x, y):
    """warp._net(C, x, y, slopes=True) at one spot: (value across, up, d/dx across, up, d/dy across, up)."""
    i, wx, dx = _bern_part(x, (C.shape[1] - 1) // 3)
    j, wy, dy = _bern_part(y, (C.shape[0] - 1) // 3)
    out = np.zeros(6)
    for b in range(4):
        r = 3 * j + b
        q = 3 * i
        for c in range(2):
            rb = ((C[r, q, c] * wx[0] + C[r, q + 1, c] * wx[1]) + C[r, q + 2, c] * wx[2]) + C[r, q + 3, c] * wx[3]
            sb = ((C[r, q, c] * dx[0] + C[r, q + 1, c] * dx[1]) + C[r, q + 2, c] * dx[2]) + C[r, q + 3, c] * dx[3]
            out[c] = out[c] + rb * wy[b]
            out[2 + c] = out[2 + c] + sb * wy[b]
            out[4 + c] = out[4 + c] + rb * dy[b]
    return out[0], out[1], out[2], out[3], out[4], out[5]


@njit(nogil=True)
def _even_at(v, L):
    """warp._to_even for one number: (its place on the even grid, the stretch du / dv); L empty = even (as it is).
    The same sums: (cell + (v - its line) / its width) / n, 1 / (n * width)."""
    n = len(L) - 1
    if n < 1 or v < 0.0 or v > 1.0:
        return v, 1.0
    lo, hi = 0, n - 1
    while lo < hi:
        m = (lo + hi + 1) >> 1
        if L[m] <= v:
            lo = m
        else:
            hi = m - 1
    w = L[lo + 1] - L[lo]
    return (lo + (v - L[lo]) / w) / n, 1.0 / (n * w)


@njit(nogil=True)
def _newton_one(i, P, px, py, smooth, lx, ly, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best):
    """warp._newton for spot i (the same steps, sums and order as its arrays). smooth = the kind: 0 Straight
    cells, 1 Smooth, 2 a control net (handles dragged). lx / ly: a split grid's lines (empty = that way even)."""
    x, y = sx[i], sy[i]
    b = np.inf
    ox, oy = x, y
    for step in range(steps):
        ex, fx = _even_at(x, lx)
        ey, fy = _even_at(y, ly)
        if smooth == 2:
            vx, vy, dxx, dxy, dyx, dyy = _net_at(P, ex, ey)
        elif smooth:
            vx, vy, dxx, dxy, dyx, dyy = _eased_at(P, ex, ey)
        else:
            vx, vy, dxx, dxy, dyx, dyy = _straight_at(P, ex, ey)
        if len(lx):
            dxx, dxy = dxx * fx, dxy * fx
        if len(ly):
            dyx, dyy = dyx * fy, dyy * fy
        rx, ry = x + vx - px[i], y + vy - py[i]
        err = math.sqrt(rx * rx + ry * ry)
        if err < b:
            b, ox, oy = err, x, y
        go = err > 1e-9
        if has_b and step == give_step:
            go = go and err < give_far
        if not go:
            break
        a, bb, c, e = 1 + dxx, dyx, dxy, 1 + dyy
        det = a * e - bb * c
        if abs(det) > 1e-6:
            ux, uy = (e * rx - bb * ry) / det, (a * ry - c * rx) / det
        else:
            ux, uy = rx, ry
        st = math.sqrt(ux * ux + uy * uy)
        cut = 0.2 / max(st, 1e-300) if st > 0.2 else 1.0
        nx, ny = x - ux * cut, y - uy * cut
        if has_b:
            nx = min(max(nx, bounds[i, 0]), bounds[i, 1])
            ny = min(max(ny, bounds[i, 2]), bounds[i, 3])
        x, y = nx, ny
    bx[i], by[i], best[i] = ox, oy, b


@njit(nogil=True)
def warp_newton_one(P, px, py, smooth, lx, ly, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best):
    """warp._newton (Newton steps toward s + bend(s) = p, the Warp's map turned round), each spot on its own: the
    best spot each reached (bx, by) and how far off (best). bounds (n, 4): kept inside them when has_b. One core."""
    for i in range(len(px)):
        _newton_one(i, P, px, py, smooth, lx, ly, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best)


@njit(nogil=True, parallel=True)
def _warp_newton_cores(P, px, py, smooth, lx, ly, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best):
    for i in prange(len(px)):
        _newton_one(i, P, px, py, smooth, lx, ly, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best)


def warp_newton(P, px, py, smooth, lx, ly, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best):
    """warp_newton_one on half the cores (on_cores)."""
    on_cores(_warp_newton_cores, warp_newton_one, len(px), P, px, py, smooth, lx, ly, sx, sy, bounds, has_b, steps,
             give_step, give_far, bx, by, best)


@njit(nogil=True)
def _landed(near, gy, gx, x, y):
    """The spot (x, y)'s map cell is one a fold lands on (near: (gy - 1) * (gx - 1), or empty)."""
    if not len(near):
        return False
    ci = min(np.int64(min(max(x, 0.0), 1.0) * (gx - 1)), gx - 2)
    cj = min(np.int64(min(max(y, 0.0), 1.0) * (gy - 1)), gy - 2)
    return near[cj * (gx - 1) + ci] != 0


@njit(nogil=True)
def _exact_one(i, P, smooth, lx, ly, a, near, x, y, steps, ox, oy, best, nob):
    """warp.exact_offsets for spot i: the map a's answer, then Newton's steps from it; (ox, oy) = the offset, or
    NaN when it doesn't settle or its map cell is one a fold lands on (near: (gy - 1) * (gx - 1), or empty; not
    solved at all: its answer would be thrown away)."""
    if _landed(near, a.shape[0], a.shape[1], x[i], y[i]):
        ox[i], oy[i] = np.nan, np.nan
        return
    d0, d1 = _sample2(a, x[i], y[i])
    ox[i], oy[i] = x[i] + d0, y[i] + d1
    _newton_one(i, P, x, y, smooth, lx, ly, ox, oy, nob, False, steps, 0, 0.0, ox, oy, best)
    if not (best[i] < 1e-7):
        ox[i], oy[i] = np.nan, np.nan
    else:
        ox[i], oy[i] = ox[i] - x[i], oy[i] - y[i]


@njit(nogil=True)
def warp_exact_one(P, smooth, lx, ly, a, near, x, y, steps, ox, oy):
    """warp.exact_offsets for every spot: each spot's exact fetch from the map's answer (NaN: the map's stands).
    One core."""
    best, nob = np.empty(len(x)), np.zeros((1, 4))
    for i in range(len(x)):
        _exact_one(i, P, smooth, lx, ly, a, near, x, y, steps, ox, oy, best, nob)


@njit(nogil=True, parallel=True)
def _warp_exact_cores(P, smooth, lx, ly, a, near, x, y, steps, ox, oy):
    best, nob = np.empty(len(x)), np.zeros((1, 4))
    for i in prange(len(x)):
        _exact_one(i, P, smooth, lx, ly, a, near, x, y, steps, ox, oy, best, nob)


def warp_exact(P, smooth, lx, ly, a, near, x, y, steps, ox, oy):
    """warp_exact_one on half the cores (on_cores)."""
    on_cores(_warp_exact_cores, warp_exact_one, len(x), P, smooth, lx, ly, a, near, x, y, steps, ox, oy)


@njit(nogil=True)
def _newton_lazy(P, px, py, smooth, lx, ly, sx, sy, steps, lazy):
    """_newton_one for one spot p from s (no bounds) + the matrix Newton inverts (a, b, c, e) at the best point (the
    bend's slopes there): (x, y, how far off, a, b, c, e). lazy > 0: a step taken from a spot already that close (and
    a matrix that can be turned round) is the answer without looking again, how far off = err * err (Newton's next
    step: within 1e-9 from there). warp._newton_lazy's sums, one spot."""
    x, y = sx, sy
    b = np.inf
    ox, oy = x, y
    ja, jb, jc, je = 1.0, 0.0, 0.0, 1.0
    for step in range(steps):
        ex, fx = _even_at(x, lx)
        ey, fy = _even_at(y, ly)
        if smooth == 2:
            vx, vy, dxx, dxy, dyx, dyy = _net_at(P, ex, ey)
        elif smooth:
            vx, vy, dxx, dxy, dyx, dyy = _eased_at(P, ex, ey)
        else:
            vx, vy, dxx, dxy, dyx, dyy = _straight_at(P, ex, ey)
        if len(lx):
            dxx, dxy = dxx * fx, dxy * fx
        if len(ly):
            dyx, dyy = dyx * fy, dyy * fy
        rx, ry = x + vx - px, y + vy - py
        err = math.sqrt(rx * rx + ry * ry)
        a, bb, c, e = 1 + dxx, dyx, dxy, 1 + dyy
        if err < b:
            b, ox, oy = err, x, y
            ja, jb, jc, je = a, bb, c, e
        if not err > 1e-9:
            break
        det = a * e - bb * c
        if abs(det) > 1e-6:
            ux, uy = (e * rx - bb * ry) / det, (a * ry - c * rx) / det
        else:
            ux, uy = rx, ry
        st = math.sqrt(ux * ux + uy * uy)
        cut = 0.2 / max(st, 1e-300) if st > 0.2 else 1.0
        nx, ny = x - ux * cut, y - uy * cut
        if lazy > 0 and err <= lazy and abs(det) > 1e-6:
            return nx, ny, err * err, ja, jb, jc, je
        x, y = nx, ny
    return ox, oy, b, ja, jb, jc, je


@njit(nogil=True)
def _exact3_one(i, P, smooth, lx, ly, a, near, x0, y0, v0, v1, v2, v3, steps, lazy, ox, oy):
    """warp.exact3_offsets for the spot (x0, y0) and its two arrows' ends (+ (v0, v1), + (v2, v3)): offsets into ox /
    oy at 3 i .. 3 i + 2 (NaN: the map's). The middle: Newton from the map's answer; each end from where the middle's
    answer + the bend's slopes there say it lands (s(p + v) ~ s(p) + J^-1 v), else from the map's answer."""
    gy, gx = a.shape[0], a.shape[1]
    for q in range(3):
        ox[3 * i + q], oy[3 * i + q] = np.nan, np.nan
    sx = sy = det = 0.0
    ja, jb, jc, je = 1.0, 0.0, 0.0, 1.0
    guess = False
    if not _landed(near, gy, gx, x0, y0):
        d0, d1 = _sample2(a, x0, y0)
        bx, by, e, ja_, jb_, jc_, je_ = _newton_lazy(P, x0, y0, smooth, lx, ly, x0 + d0, y0 + d1, steps, lazy)
        if e < 1e-7:
            ox[3 * i], oy[3 * i] = bx - x0, by - y0
            sx, sy, ja, jb, jc, je = bx, by, ja_, jb_, jc_, je_
            det = ja * je - jb * jc
            guess = abs(det) > 1e-6
    for q in range(2):
        vx, vy = (v0, v1) if q == 0 else (v2, v3)
        px, py = x0 + vx, y0 + vy
        if _landed(near, gy, gx, px, py):
            continue
        if guess:
            ux, uy = (je * vx - jb * vy) / det, (ja * vy - jc * vx) / det
            ex, ey, e, _, _, _, _ = _newton_lazy(P, px, py, smooth, lx, ly, sx + ux, sy + uy, steps, lazy)
            if e < 1e-7:
                ox[3 * i + 1 + q], oy[3 * i + 1 + q] = ex - px, ey - py
                continue
        e0, e1 = _sample2(a, px, py)
        ex, ey, e, _, _, _, _ = _newton_lazy(P, px, py, smooth, lx, ly, px + e0, py + e1, steps, lazy)
        if e < 1e-7:
            ox[3 * i + 1 + q], oy[3 * i + 1 + q] = ex - px, ey - py


@njit(nogil=True)
def _unit_back(x, y, fp):
    """liquify._placed + recolour.from_unit for one spot of the colour box (Edges: escape = clipped, else wrapped
    round then clipped): (middle in ticks, key). fp = [cut, turned, a0, a1 - a0, b0, b1 - b0, b1 > b0, f1, f2, t0,
    width, k0, height, k1 > k0] (+ Wrap around: 1, rows R, the R rows' keys, first starts, last ends =
    recolour.Rows.wrapped after it, _wrap_one's sums)."""
    if fp[0] != 0.0:
        if x < -1e-9 or x > 1 + 1e-9:
            x = np.mod(x, 1.0)
        if y < -1e-9 or y > 1 + 1e-9:
            y = np.mod(y, 1.0)
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    if fp[1] != 0.0:
        x2 = fp[2] + x * fp[3]
        y2 = fp[4] + y * fp[5] if fp[6] != 0.0 else y
        x, y = 0.5 + fp[8] * (y2 - 0.5), 0.5 + fp[7] * (x2 - 0.5)
    m, k = fp[9] + x * fp[10], (fp[11] + y * fp[12] if fp[13] != 0.0 else fp[11])
    if len(fp) > 15 and fp[14] == 1.0:  # (Wrap around: past its key row's notes = back in on that row's other side)
        nr = np.int64(fp[15])
        q = np.rint(k)
        lo_, hi_ = 0, nr
        while lo_ < hi_:
            h = (lo_ + hi_) >> 1
            if fp[16 + h] < q:
                lo_ = h + 1
            else:
                hi_ = h
        r = min(max(lo_, 0), nr - 1)
        j = max(r - 1, 0)
        row = j if abs(fp[16 + j] - q) <= abs(fp[16 + r] - q) else r
        s, e = fp[16 + nr + row], fp[16 + 2 * nr + row]
        if m < s or m > e:
            m = s + np.mod(m - s, max(e - s, 1.0))
    return m, k


@njit(nogil=True)
def _near_at(m, k, keys, first, last, line, ms, o, base, top, gap):
    """recolour.nearest (own=False) for one spot (middle m, key k): _near_one without a note of its own."""
    nkeys = len(keys)
    q = np.rint(k)
    lo_, hi_ = 0, nkeys
    while lo_ < hi_:
        h = (lo_ + hi_) >> 1
        if keys[h] < q:
            lo_ = h + 1
        else:
            hi_ = h
    i = min(max(lo_, 0), nkeys - 1)
    j = max(i - 1, 0)
    row = j if abs(keys[j] - q) <= abs(keys[i] - q) else i
    lo, hi = first[row], last[row]
    c = min(max(m, base), top)
    v = c - base + row * gap
    a, b = lo, hi
    while a < b:
        h = (a + b) >> 1
        if line[h] < v:
            a = h + 1
        else:
            b = h
    p = min(max(a, lo), hi - 1)
    p0 = max(p - 1, lo)
    if abs(ms[p0] - m) <= abs(ms[p] - m):
        p = p0
    return o[p]


ROUND_INNER = 7  # (liquify._RING: look 0 = the middle, 1 .. 6 the inner ring, 7 on the outer ring)
SOFT_TABLE = 4  # (liquify.SOFT_MOST: _cover_at's table holds (nt - 1) * 4 + nk - 1)


@njit(nogil=True)
def _round_at(sx, sy, r, ring, fp, keys, first, last, line, ms, o, base, top, gap, now):
    """liquify._round_vote for one spot fetching from (sx, sy), looks out to r: the middle + the outer ring first;
    all one colour = the middle's note; else the inner ring too and the colour with the most weight wins (a tie: the
    middle's, then the lower colour), its first look's note."""
    L = len(ring)
    got = np.empty(L, np.int64)
    for k in range(L):
        if k == 0 or k >= ROUND_INNER:
            m, kk = _unit_back(sx + ring[k, 0] * r, sy + ring[k, 1] * r, fp)
            got[k] = _near_at(m, kk, keys, first, last, line, ms, o, base, top, gap)
    c0 = now[got[0]]
    mixed = False
    for k in range(ROUND_INNER, L):
        if now[got[k]] != c0:
            mixed = True
    if not mixed:
        return got[0]
    for k in range(1, ROUND_INNER):
        m, kk = _unit_back(sx + ring[k, 0] * r, sy + ring[k, 1] * r, fp)
        got[k] = _near_at(m, kk, keys, first, last, line, ms, o, base, top, gap)
    cols = np.empty(L, np.int64)
    score = np.zeros(L)
    nc = 0
    for k in range(L):  # (each colour's weight summed in look order, as np.bincount does)
        c = now[got[k]]
        q = 0
        while q < nc and cols[q] != c:
            q += 1
        if q == nc:
            cols[q] = c
            nc += 1
        score[q] += ring[k, 2]
    score[0] += 1e-6  # (the middle's colour is the first one met)
    best = 0
    for q in range(1, nc):
        if score[q] > score[best] or (score[q] == score[best] and cols[q] < cols[best]):
            best = q
    for k in range(L):
        if now[got[k]] == cols[best]:
            return got[k]
    return got[0]


@njit(nogil=True)
def _cover_at(mid, key, vt0, vt1, vk0, vk1, nt, nk, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap,
              now):
    """liquify._covered for one note: nt x nk looks over what it covers (tstart / ta / tb: each count's looks,
    nearest the middle first) vote, the most common colour wins (a tie: the one looked at first); its look's note."""
    t = (nt - 1) * SOFT_TABLE + (nk - 1)
    a0, a1 = tstart[t], tstart[t + 1]
    s = a1 - a0
    got = np.empty(s, np.int64)
    for j in range(s):
        pm = mid + ta[a0 + j] * vt0 + tb[a0 + j] * vk0
        pk = key + ta[a0 + j] * vt1 + tb[a0 + j] * vk1
        got[j] = _near_at(pm, pk, keys, first, last, line, ms, o, base, top, gap)
    if s == 1:
        return got[0]
    cols = np.empty(s, np.int64)
    count = np.zeros(s, np.int64)
    firstj = np.zeros(s, np.int64)
    nc = 0
    for j in range(s):
        c = now[got[j]]
        q = 0
        while q < nc and cols[q] != c:
            q += 1
        if q == nc:
            cols[q], firstj[q] = c, j
            nc += 1
        count[q] += 1
    best = 0
    for q in range(1, nc):
        vq, vb = count[q] * (s + 1) - firstj[q], count[best] * (s + 1) - firstj[best]
        if vq > vb or (vq == vb and cols[q] < cols[best]):
            best = q
    return got[firstj[best]]


@njit(nogil=True)
def _soft_fetch(a, x, y, ox, oy, s100):
    """Where the spot (x, y) fetches from: its exact fetch (ox, oy), or the map a's, blended (ox NaN)."""
    if np.isnan(ox):
        d0, d1 = _sample2(a, x, y)
        return x + d0 * s100, y + d1 * s100
    return x + ox, y + oy


@njit(nogil=True)
def _soft_one(i, at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top,
              gap, now, out):
    """liquify.found's Edges: Smooth for note idx[i] at spot (x[i], y[i]) of the box (the same sums, the same
    order as its arrays). c = [tick's step across, up (box units), half a key's across, up, one key's height,
    strength / 100, SOFT_SQUASHED, SOFT_REACH, 1 = Edges: Sharp (the nearest note alone, C7)(, 1 = ox / oy hold
    three a spot)]. ox / oy: each spot's exact fetch (a Warp's, warp.exact_offsets; NaN = the map's), one a spot,
    or three a spot interleaved (c[9] = 1: also the ends of the note's length arrow and key arrow), or both empty."""
    s100 = c[5]
    x0, y0 = x[i], y[i]
    per = 0
    if len(ox):
        per = 3 if len(c) > 9 and c[9] != 0 else 1
    nan = np.nan
    if per == 3:
        o0, o1, o2, o3, o4, o5 = ox[3 * i], oy[3 * i], ox[3 * i + 1], oy[3 * i + 1], ox[3 * i + 2], oy[3 * i + 2]
    elif per == 1:
        o0, o1, o2, o3, o4, o5 = ox[i], oy[i], nan, nan, nan, nan
    else:
        o0 = o1 = o2 = o3 = o4 = o5 = nan
    sx, sy = _soft_fetch(a, x0, y0, o0, o1, s100)
    mid, key = _unit_back(sx, sy, fp)
    if c[8] != 0:  # (Edges: Sharp, the Liquify: the nearest note alone)
        out[i] = _near_at(mid, key, keys, first, last, line, ms, o, base, top, gap)
        return
    j = idx[i]
    s, e = float(at[j, 0]), float(at[j, 1])
    h = max((e - s) / 2, 0.5)
    X, Y = _soft_fetch(a, x0 + c[0] * h, y0 + c[1] * h, o2, o3, s100)
    m1, k1 = _unit_back(X, Y, fp)
    X, Y = _soft_fetch(a, x0 + c[2], y0 + c[3], o4, o5, s100)
    m3, k3 = _unit_back(X, Y, fp)
    vt0, vt1 = (m1 - mid) * 2, (k1 - key) * 2
    vk0, vk1 = (m3 - mid) * 2, (k3 - key) * 2
    ut, uk = vt0 / (2 * h), vt1
    wt, wk = vk0 / (2 * h), vk1
    area = abs(ut * wk - uk * wt)
    if area <= c[6]:
        grow = min(max(1.0 / math.sqrt(max(area, 1e-12)) - 1.0, 0.0), 1.0)
        if grow < 0.05:
            out[i] = _near_at(mid, key, keys, first, last, line, ms, o, base, top, gap)
        else:
            out[i] = _round_at(sx, sy, c[4] * c[7] * grow, ring, fp, keys, first, last, line, ms, o, base, top, gap,
                               now)
        return
    nt = np.int64(min(max(np.ceil(math.sqrt(ut * ut + uk * uk)), 1.0), float(SOFT_TABLE)))
    nk = np.int64(min(max(np.ceil(math.sqrt(wt * wt + wk * wk)), 1.0), float(SOFT_TABLE)))
    out[i] = _cover_at(mid, key, vt0, vt1, vk0, vk1, nt, nk, tstart, ta, tb, keys, first, last, line, ms, o, base,
                       top, gap, now)


@njit(nogil=True)
def warp_soft_one(at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top,
                  gap, now, out):
    """liquify.found's Edges: Smooth (the Warp's) for every note idx: the note each takes its colour from. One
    core."""
    for i in range(len(idx)):
        _soft_one(i, at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top,
                  gap, now, out)


@njit(nogil=True, parallel=True)
def _warp_soft_cores(at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top,
                     gap, now, out):
    for i in prange(len(idx)):
        _soft_one(i, at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top,
                  gap, now, out)


def warp_soft(at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap,
              now, out):
    """warp_soft_one on half the cores (on_cores)."""
    on_cores(_warp_soft_cores, warp_soft_one, len(idx), at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys,
             first, last, line, ms, o, base, top, gap, now, out)


@njit(nogil=True)
def _soft2_one(i, at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap, now,
               P, smooth, lx, ly, near, steps, lazy, ox, oy, out):
    """_soft_one for note idx[i] with its three exact fetches worked out first (_exact3_one: its middle + its two
    arrows' ends, the arrows as _soft_one reads them from c) into ox / oy (3 a note). Edges: Smooth only."""
    j = idx[i]
    s, e = float(at[j, 0]), float(at[j, 1])
    h = max((e - s) / 2, 0.5)
    _exact3_one(i, P, smooth, lx, ly, a, near, x[i], y[i], c[0] * h, c[1] * h, c[2], c[3], steps, lazy, ox, oy)
    _soft_one(i, at, idx, x, y, ox, oy, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap,
              now, out)


@njit(nogil=True)
def warp_soft2_one(at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap, now,
                   P, smooth, lx, ly, near, steps, lazy, out):
    """liquify.found's Edges: Smooth for a Warp with its exact fetches (warp.exact3_offsets) worked out note by note
    inside: the note each takes its colour from. c as warp_soft's with c[9] = 1. One core."""
    ox, oy = np.empty(3 * len(idx)), np.empty(3 * len(idx))
    for i in range(len(idx)):
        _soft2_one(i, at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap,
                   now, P, smooth, lx, ly, near, steps, lazy, ox, oy, out)


@njit(nogil=True, parallel=True)
def _warp_soft2_cores(at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap,
                      now, P, smooth, lx, ly, near, steps, lazy, out):
    ox, oy = np.empty(3 * len(idx)), np.empty(3 * len(idx))
    for i in prange(len(idx)):
        _soft2_one(i, at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap,
                   now, P, smooth, lx, ly, near, steps, lazy, ox, oy, out)


def warp_soft2(at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap, now, P,
               smooth, lx, ly, near, steps, lazy, out):
    """warp_soft2_one on half the cores (on_cores)."""
    on_cores(_warp_soft2_cores, warp_soft2_one, len(idx), at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first,
             last, line, ms, o, base, top, gap, now, P, smooth, lx, ly, near, steps, lazy, out)


@njit(nogil=True)
def _near_one(s, keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own):
    """stir_nearest's work for spot s."""
    nkeys = len(keys)
    q = np.rint(key[s])
    lo_, hi_ = 0, nkeys  # (np.searchsorted(keys, q): first key >= q)
    while lo_ < hi_:
        h = (lo_ + hi_) >> 1
        if keys[h] < q:
            lo_ = h + 1
        else:
            hi_ = h
    i = min(max(lo_, 0), nkeys - 1)
    j = max(i - 1, 0)
    row = j if abs(keys[j] - q) <= abs(keys[i] - q) else i
    lo, hi = first[row], last[row]
    m = mid[s]
    c = min(max(m, base), top)
    v = c - base + row * gap
    a, b = lo, hi  # (np.searchsorted(line, v) inside the row's own stretch: the rows never mix)
    while a < b:
        h = (a + b) >> 1
        if line[h] < v:
            a = h + 1
        else:
            b = h
    p = min(max(a, lo), hi - 1)
    p0 = max(p - 1, lo)
    if abs(ms[p0] - m) <= abs(ms[p] - m):
        p = p0
    got = o[p]
    if own and nk[s] == keys[row] and abs(nm[s] - m) <= abs(ms[p] - m) + 1e-9:
        got = s
    return got


@njit(nogil=True)
def _stir_q(x, y, fp, ip):
    """recolour.area_spot_q of one spot (a circle / square / diamond area): 0 in its middle, 1 on its outline and
    past it, rounded to 9 places."""
    X = (x - fp[9]) * fp[11] / fp[13]
    Y = (y - fp[10]) * fp[12] / fp[13]
    if ip[3]:
        X, Y = X * fp[14] + Y * fp[15], Y * fp[14] - X * fp[15]
    if ip[4]:
        X = X - fp[16] * Y
    X, Y = X / fp[17], Y / fp[18]
    if ip[2] == 1:
        d = max(abs(X), abs(Y))
    elif ip[2] == 2:
        d = abs(X) + abs(Y)
    else:
        d = np.hypot(X, Y)
    d = np.rint(d * 1e9) / 1e9
    return min(max(d, 0.0), 1.0)


@njit(nogil=True)
def _stir_one(px, py, fp, ip):
    """recolour.stir_from's middle part, note by note: (x, y) in the colour box (unit_xy) -> where the stir looks
    (before from_unit): Bulge, the more bends, Turn / Twist (+ the Area's fade), the edges' clip / tile, Squeeze.
    fp / ip = the stir's numbers in recolour.stir_numbers' order (circle / square / diamond areas only)."""
    cx, cy, rx, ry = fp[0], fp[1], fp[2], fp[3]
    tau = 2 * np.pi
    if ip[9]:  # Bulge
        if ip[0] and ip[8]:
            mx, my = fp[9], fp[10]
            q = _stir_q(px, py, fp, ip)
        else:
            mx, my = cx, cy
            dx, dy = px - mx, py - my
            q = np.hypot(dx / max(1 - mx, 1e-12) if dx > 0 else -dx / max(mx, 1e-12),
                         dy / max(1 - my, 1e-12) if dy > 0 else -dy / max(my, 1e-12))
        g = 1.0
        if q < 1 and q > 1e-12:
            g = 1 / (fp[19] - fp[20] * q)
        px, py = mx + (px - mx) * g, my + (py - my) * g
    w = 1.0
    q = 0.0
    hard = 0.0
    if ip[0]:  # (the Area's fade, measured where the note is seen)
        q = _stir_q(px, py, fp, ip)
        hard = 1.0 if q < 1 else 0.0
        w = hard if ip[1] else 1 - q * q * (3 - 2 * q)
    if ip[10]:  # Kaleidoscope
        inside = ip[0] and ip[14]
        mx, my = (fp[9], fp[10]) if inside else (cx, cy)
        X, Y = (px - mx) * rx, (py - my) * ry
        a = np.mod(np.arctan2(Y, X) - fp[21], fp[22])
        if a > fp[23]:
            a = fp[22] - a
        a = a + fp[21]
        R = np.hypot(X, Y)
        nx, ny = mx + R * np.cos(a) / rx, my + R * np.sin(a) / ry
        f = ((hard if ip[18] else w) * 1.0) if inside else 1.0
        px, py = px + (nx - px) * f, py + (ny - py) * f
    if ip[11] >= 0:  # Ripple
        inside = ip[0] and ip[15]
        mx, my = (fp[9], fp[10]) if inside else (cx, cy)
        X, Y = (px - mx) * rx, (py - my) * ry
        if ip[11] == 0:
            X = X + fp[24] * np.sin(tau * Y / fp[25])
        elif ip[11] == 1:
            Y = Y + fp[24] * np.sin(tau * X / fp[25])
        else:
            R = np.hypot(X, Y)
            g = (R + fp[24] * np.sin(tau * R / fp[25])) / max(R, 1e-12) if R > 1e-12 else 1.0
            X, Y = X * g, Y * g
        nx, ny = mx + X / rx, my + Y / ry
        f = (w * 1.0) if inside else 1.0
        px, py = px + (nx - px) * f, py + (ny - py) * f
    if ip[12]:  # Twist along a line
        inside = ip[0] and ip[16]
        mx, my = (fp[9], fp[10]) if inside else (cx, cy)
        X, Y = (px - mx) * rx, (py - my) * ry
        u, v = X * fp[26] + Y * fp[27], Y * fp[26] - X * fp[27]
        phi = fp[28] * u / (fp[31] if inside else fp[30])
        u, v = u + v * np.sin(phi), v * np.cos(phi)
        nx, ny = mx + (u * fp[26] - v * fp[27]) / rx, my + (u * fp[27] + v * fp[26]) / ry
        f = (w * 1.0) if inside else 1.0
        px, py = px + (nx - px) * f, py + (ny - py) * f
    if ip[13]:  # Straight to round
        inside = ip[0] and ip[17]
        mx, my = (fp[9], fp[10]) if inside else (cx, cy)
        X, Y = (px - mx) * rx, (py - my) * ry
        across = (np.arctan2(Y, X) + np.pi) / tau
        up = _stir_q(px, py, fp, ip) if inside else np.hypot(X, Y) / fp[30]
        f = fp[29] * ((hard if ip[18] else w) if inside else 1.0)
        px, py = px + (across - px) * f, py + (up - py) * f
    dx, dy = px - cx, py - cy
    if ip[20]:  # (a turn of its own for every note)
        turn = fp[4]
        if ip[0]:
            if ip[5]:
                turn = turn * w
            if fp[5] != 0 and ip[6]:
                turn = turn + fp[5] * np.sqrt(q) * w
        if ip[19]:
            turn = turn + fp[5] * np.sqrt(np.hypot(dx * rx, dy * ry) / max(fp[8], 1e-12))
        a = -2 * np.pi * turn
        ca, sa = np.cos(a), np.sin(a)
    else:
        ca, sa = fp[6], fp[7]
    X, Y = dx * rx, dy * ry
    px, py = cx + (X * ca - Y * sa) / rx, cy + (X * sa + Y * ca) / ry
    if ip[21]:  # Escape
        px, py = min(max(px, 0.0), 1.0), min(max(py, 0.0), 1.0)
    else:
        if px < -1e-9 or px > 1 + 1e-9:
            px = np.mod(px, 1.0)
        if py < -1e-9 or py > 1 + 1e-9:
            py = np.mod(py, 1.0)
        px, py = min(max(px, 0.0), 1.0), min(max(py, 0.0), 1.0)
    if ip[22]:  # Squeeze
        sx = 0.5 * px / max(cx, 1e-12) if px < cx else 0.5 + 0.5 * (px - cx) / max(1 - cx, 1e-12)
        sy = 0.5 * py / max(cy, 1e-12) if py < cy else 0.5 + 0.5 * (py - cy) / max(1 - cy, 1e-12)
        if ip[0] and ip[7]:
            sx, sy = px + (sx - px) * w, py + (sy - py) * w
        px, py = sx, sy
    return px, py


@njit(nogil=True)
def stir_spots_one(x, y, fp, ip, ox, oy):
    """recolour.stir_from's middle part for every spot (x, y) in the colour box (unit_xy): ox, oy = where the stir
    looks (before from_unit). One core."""
    for i in range(len(x)):
        ox[i], oy[i] = _stir_one(x[i], y[i], fp, ip)


@njit(nogil=True, parallel=True)
def _stir_spots_cores(x, y, fp, ip, ox, oy):
    for i in prange(len(x)):
        ox[i], oy[i] = _stir_one(x[i], y[i], fp, ip)


# (SEVERAL CORES, user 11-07 "any speed up is beneficial": the Stir's per-note loops shared over half the cores (all
# cores halted the PC, the synths' rule; the library does it with its own threads). ONE THREAD AT A TIME: a thread
# finding it busy takes the one-core loop instead of waiting; any failure = one core from then on. Each note on its
# own: the very same numbers either way.)
_CORES_LOCK = threading.Lock()
_CORES = {"ok": True}
CORES_LEAST = 20000  # (spots: fewer = one core, sharing out costs more than it saves)


def on_cores(many, one, n, *args):
    """many(*args) on half the cores when n is big enough and no other thread is sharing them out, else one(*args)."""
    if n >= CORES_LEAST and _CORES["ok"] and _CORES_LOCK.acquire(blocking=False):
        try:
            set_threads()
            many(*args)
            return
        except Exception:  # noqa: BLE001 (no threading layer here: one core for good)
            _CORES["ok"] = False
        finally:
            _CORES_LOCK.release()
    one(*args)


def stir_spots(x, y, fp, ip, ox, oy):
    """stir_spots_one on half the cores (on_cores)."""
    on_cores(_stir_spots_cores, stir_spots_one, len(x), x, y, fp, ip, ox, oy)


_MIX_ADD, _MIX_A, _MIX_B = np.uint64(0x9E3779B97F4A7C15), np.uint64(0xBF58476D1CE4E5B9), np.uint64(0x94D049BB133111EB)


@njit(nogil=True)
def _mix(x):
    """recolour._mixed (splitmix64) on one uint64."""
    x = x + _MIX_ADD
    x = (x ^ (x >> np.uint64(30))) * _MIX_A
    x = (x ^ (x >> np.uint64(27))) * _MIX_B
    return x ^ (x >> np.uint64(31))


@njit(nogil=True)
def dice_mix(seed, c0, c1, c2, c3, out):
    """recolour.dice's numbers: each note's 4 columns (as uint64) mixed in turn from seed -> out (0 <= u < 1)."""
    for i in range(len(out)):
        x = _mix(seed ^ c0[i])
        x = _mix(x ^ c1[i])
        x = _mix(x ^ c2[i])
        x = _mix(x ^ c3[i])
        out[i] = np.float64(x >> np.uint64(11)) / 9007199254740992.0


@njit(nogil=True)
def recolour_apart(order, ss, e, old, new, pick, bstart, bsize, nc):
    """recolour.kept_apart's walk through its bursts of 3+ notes (old / new: colour numbers 0 .. nc - 1, new changed
    in place): a note keeps no colour that a note sounding with it, of ANOTHER colour before, has now (its own one
    first, else the free one its dice pick, its own one when none is free)."""
    act = np.empty(max(1, bsize.max() if len(bsize) else 1), np.int64)
    taken = np.zeros(nc, np.bool_)
    for w in range(len(bstart)):
        na = 0
        for j in range(bstart[w], bstart[w] + bsize[w]):
            i = order[j]
            m = 0
            for q in range(na):  # (ending where this one starts: touching, fine)
                x = act[q]
                if e[x] > ss[j]:
                    act[m] = x
                    m += 1
            na = m
            for c in range(nc):
                taken[c] = False
            count = 0
            for q in range(na):
                x = act[q]
                if old[x] != old[i] and not taken[new[x]]:
                    taken[new[x]] = True
                    count += 1
            if taken[new[i]]:
                free = nc - count
                if free == 0 or not taken[old[i]]:
                    new[i] = old[i]
                else:
                    r = min(int(pick[i] * free), free - 1)
                    for c in range(nc):
                        if not taken[c]:
                            if r == 0:
                                new[i] = c
                                break
                            r -= 1
            act[na] = i
            na += 1


@njit(nogil=True)
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


@njit(nogil=True)
def _before(i, j, a, b, c):
    """Row i comes before row j: by a, then b, then c."""
    if a[i] != a[j]:
        return a[i] < a[j]
    if b[i] != b[j]:
        return b[i] < b[j]
    return c[i] < c[j]


@njit(nogil=True)
def _runs_sort(idx, a, b, c, tmp, bounds, nb):
    """idx (row numbers) sorted by (a, b, c) in place, rows alike kept in their order (as np.lexsort): the runs
    already in order are merged two by two (notes mostly come in order: a few runs, quick). bounds, nb: work space,
    len(idx) + 2 each (plain arrays: a growing list was ~1.3x slower)."""
    n = len(idx)
    if n < 2:
        return
    nbo = 1
    bounds[0] = 0
    for i in range(1, n):
        if _before(idx[i], idx[i - 1], a, b, c):
            bounds[nbo] = i
            nbo += 1
    bounds[nbo] = n
    nbo += 1
    while nbo > 2:
        nn = 1
        nb[0] = 0
        for r in range(0, nbo - 1, 2):
            lo = bounds[r]
            if r + 2 >= nbo:
                nb[nn] = bounds[r + 1]
                nn += 1
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
            for j in range(lo, hi):
                idx[j] = tmp[j]
            nb[nn] = hi
            nn += 1
        for j in range(nn):
            bounds[j] = nb[j]
        nbo = nn


@njit(nogil=True)
def _key_groups(key):
    """key_order's first part: rows grouped by key, lowest first, in their order -> (order, at: group g = order[at[g]
    .. at[g + 1]])."""
    n = len(key)
    order = np.empty(n, np.int64)
    if n == 0:
        return order, np.zeros(1, np.int64)
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
    return order, at


@njit(nogil=True)
def _key_runs(order, at, a, b, c):
    """key_order's second part: each key's group sorted by (a, b, c). One core."""
    n = len(order)
    tmp = np.empty(n, np.int64)
    bounds, nb = np.empty(n + 2, np.int64), np.empty(n + 2, np.int64)
    for g in range(len(at) - 1):
        if at[g + 1] - at[g] > 1:
            _runs_sort(order[at[g]:at[g + 1]], a, b, c, tmp[at[g]:at[g + 1]], bounds, nb)


@njit(nogil=True, parallel=True)
def _key_runs_cores(order, at, a, b, c):
    """_key_runs with the key groups shared out (each group on its own: the very same order)."""
    n, nk = len(order), len(at) - 1
    tmp = np.empty(n, np.int64)
    bounds, nb = np.empty(n + 2 * nk, np.int64), np.empty(n + 2 * nk, np.int64)
    for g in prange(nk):
        lo, hi = at[g], at[g + 1]
        if hi - lo > 1:
            _runs_sort(order[lo:hi], a, b, c, tmp[lo:hi], bounds[lo + 2 * g:hi + 2 * g + 2],
                       nb[lo + 2 * g:hi + 2 * g + 2])


@njit(nogil=True)
def key_order(key, a, b, c):
    """np.lexsort((c, b, a, key)): rows by key, then a, b, c, rows alike in their order. One core."""
    order, at = _key_groups(key)
    _key_runs(order, at, a, b, c)
    return order


def key_order_cores(key, a, b, c):
    """key_order with the key groups sorted on half the cores (on_cores; 3 M pasted notes measured ~3x quicker)."""
    order, at = _key_groups(key)
    on_cores(_key_runs_cores, _key_runs, len(order), order, at, a, b, c)
    return order


@njit(nogil=True)
def radix_order(k, bits):
    """np.argsort(k, kind="stable") for whole numbers 0 .. 2 ** bits - 1: 11 bits a pass, the keys carried along
    (a pass where every key has the same digit is skipped). k is used as work space: changed afterwards."""
    n = len(k)
    order, o2 = np.arange(n), np.empty(n, np.int64)
    k1, k2 = k, np.empty(n, np.int64)
    count = np.empty(2049, np.int64)
    shift = 0
    while shift < bits and n > 1:
        count[:] = 0
        for i in range(n):
            count[((k1[i] >> shift) & 2047) + 1] += 1
        if count[((k1[0] >> shift) & 2047) + 1] == n:
            shift += 11
            continue
        for d in range(2048):
            count[d + 1] += count[d]
        for i in range(n):
            d = (k1[i] >> shift) & 2047
            p = count[d]
            o2[p] = order[i]
            k2[p] = k1[i]
            count[d] = p + 1
        order, o2 = o2, order
        k1, k2 = k2, k1
        shift += 11
    return order


@njit(nogil=True)
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


@njit(nogil=True)
def clash_pairs(items, n, most):
    """engine.clash_arrays: (key, start, end, owner) rows, owners below n -> arrays a, b of the owner pairs a < b
    that clash, sorted by a then b, and True (False + nothing: the table would take more than `most` bytes): in (key, start, end, owner) order, a later one on the same key starting before
    an earlier one ends. Swept key by key: each owner's latest end so far, the owners still sounding at each start;
    pairs listed (repeats too) until the list gets long, then marked in an n x n table of bits (n / 64 words a row:
    the same pair on every key of thousands of stacked shapes)."""
    k = np.ascontiguousarray(items[:, 0])
    s = np.ascontiguousarray(items[:, 1])
    e = np.ascontiguousarray(items[:, 2])
    o = np.ascontiguousarray(items[:, 3])
    order = key_order(k, s, e, o)
    words = (n + 63) >> 6
    listed = np.empty(min(1 << 20, max(16, 4 * len(order))), np.int64)
    nl = 0
    hit = np.zeros((0, words), np.uint64)
    count = -1  # (pairs in the table; -1 = no table yet)
    one = np.uint64(1)
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
                        lo, hi = min(a, b), max(a, b)
                        if count < 0 and nl < len(listed):
                            listed[nl] = lo * n + hi
                            nl += 1
                        else:
                            if count < 0:  # (the list is full: the table from here on)
                                if n * words * 8 > most:  # (too big: the caller's NumPy way)
                                    return listed[:0], listed[:0], False
                                hit = np.zeros((n, words), np.uint64)
                                count = 0
                                for x2 in range(nl):
                                    p, q = listed[x2] // n, listed[x2] % n
                                    bit = one << np.uint64(q & 63)
                                    if not hit[p, q >> 6] & bit:
                                        hit[p, q >> 6] |= bit
                                        count += 1
                            bit = one << np.uint64(hi & 63)
                            if not hit[lo, hi >> 6] & bit:
                                hit[lo, hi >> 6] |= bit
                                count += 1
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
    if count < 0:
        code = np.sort(listed[:nl])
        c = 0
        for x in range(nl):
            if x == 0 or code[x] != code[x - 1]:
                code[c] = code[x]
                c += 1
        return code[:c] // n, code[:c] % n, True
    pa = np.empty(count, np.int64)
    pb = np.empty(count, np.int64)
    c = 0
    for a in range(n):
        for w in range(words):
            x = hit[a, w]
            bit = 0
            while x:
                if x & one:
                    pa[c] = a
                    pb[c] = (w << 6) + bit
                    c += 1
                x >>= one
                bit += 1
    return pa, pb, True


@njit(nogil=True)
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


@njit(nogil=True)
def any_overlap(notes, g, ng):
    """engine.heard_notes' check without a sort: notes (start, end, ...) rows, g = each note's group (0 .. ng - 1) ->
    1 = two notes of one group overlap, 0 = none, 2 = a group's starts go back somewhere (the caller sorts), -1 = a
    group out of range. In start order a note overlaps an earlier one exactly when it starts before the latest end
    so far (two notes overlapping = the sorted check's neighbours overlap somewhere between them)."""
    n = notes.shape[0]
    if n < 2 or ng <= 0:
        return 0
    if notes.shape[1] < 2 or g.shape[0] < n:
        return -1
    last = np.full(ng, np.iinfo(np.int64).min, np.int64)
    most = np.full(ng, np.iinfo(np.int64).min, np.int64)
    for i in range(n):
        k = g[i]
        if k < 0 or k >= ng:
            return -1
        s = notes[i, 0]
        if s < last[k]:
            return 2
        last[k] = s
        if s < most[k]:
            return 1
        if notes[i, 1] > most[k]:
            most[k] = notes[i, 1]
    return 0


@njit(nogil=True)
def _upper(flat, x, hint):
    """How many of flat (sorted) are <= x (np.searchsorted side="right"), looked for from hint (the last answer)."""
    n = flat.shape[0]
    h = min(hint, n)
    if h > 0 and flat[h - 1] > x:
        hi = h - 1
        step = 1
        while True:
            if hi < step:
                lo = 0
                break
            c = hi - step
            if flat[c] <= x:
                lo = c + 1
                break
            hi = c
            step *= 2
    else:
        lo = h
        step = 1
        while True:
            c = lo + step - 1
            if c >= n:
                hi = n
                break
            if flat[c] > x:
                hi = c
                break
            lo = c + 1
            step *= 2
    while lo < hi:  # (the first of lo .. hi above x)
        mid = (lo + hi) // 2
        if flat[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


@njit(nogil=True)
def turn_sample(flat, skey, send, g, lo_k, hi_k, n, many):
    """merge._sampled's grid, one key row at a time: one layer of notes sorted by key, then start (flat = key x
    (t_all + 1) + start; their keys, ends), g = (b0, p0, ub, up, vb, vp, det, t0, step, t_all, k_all, ppq, e) ->
    (start, end, key row, which note) of every piece, row after row. The same sums in the same order."""
    b0, p0, ub, up, vb, vp, det, t0, step = g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7], g[8]
    t_all, k_all, ppq, e = g[9], g[10], g[11], g[12]
    m = flat.shape[0]
    room = 1024
    out = np.empty((4, room), np.int64)
    c = 0
    if m == 0 or hi_k < lo_k or n <= 0:
        return out[0, :0].copy(), out[1, :0].copy(), out[2, :0].copy(), out[3, :0].copy()
    kmax = np.int64(k_all) - 1
    t1 = t_all + 1.0
    one_e = 1.0 - e
    for row in range(lo_k, hi_k + 1):
        dk = float(row) - p0
        hint = 0
        prev = np.int64(-1)
        frm = 0
        for j in range(n + 1):
            a = np.int64(-1)
            if j < n:
                bb = t0 + (j + 0.5) * step
                db = bb - b0
                u = (db * vp - dk * vb) / det
                v = (dk * ub - up * db) / det
                if u >= -e and u < one_e and v >= -e and v < one_e:
                    key = min(max(np.int64(v * k_all), 0), kmax)
                    tick = u * t_all
                    hint = _upper(flat, key * t1 + tick, hint)
                    if hint >= 1:
                        at = min(hint - 1, m - 1)
                        if skey[at] == key and tick < send[at]:
                            a = at
            if j == 0:
                prev = a
                continue
            if j < n and a == prev:
                continue
            if prev >= 0:
                start = np.int64(np.rint((t0 + frm * step) * ppq))
                end = np.int64(np.rint((t0 + j * step) * ppq))
                if end > start:
                    if c == room:
                        room *= 2
                        bigger = np.empty((4, room), np.int64)
                        bigger[:, :c] = out[:, :c]
                        out = bigger
                    out[0, c] = start
                    out[1, c] = end
                    out[2, c] = row
                    out[3, c] = prev
                    c += 1
            prev = a
            frm = j
    return out[0, :c].copy(), out[1, :c].copy(), out[2, :c].copy(), out[3, :c].copy()


@njit(nogil=True)
def _pair_push(h, n, x):
    """x into the min-heap h[:n] (rows compared as (first, second)) -> the new size."""
    i = n
    h[i, 0], h[i, 1] = x[0], x[1]
    while i > 0:
        p = (i - 1) // 2
        if h[p, 0] < h[i, 0] or (h[p, 0] == h[i, 0] and h[p, 1] <= h[i, 1]):
            break
        h[p, 0], h[i, 0] = h[i, 0], h[p, 0]
        h[p, 1], h[i, 1] = h[i, 1], h[p, 1]
        i = p
    return n + 1


@njit(nogil=True)
def _pair_pop(h, n):
    """The smallest row of the min-heap h[:n] taken off (read h[n - 1] after) -> the new size."""
    n -= 1
    h[0, 0], h[n, 0] = h[n, 0], h[0, 0]
    h[0, 1], h[n, 1] = h[n, 1], h[0, 1]
    i = 0
    while True:
        a = 2 * i + 1
        if a >= n:
            break
        b = a + 1
        if b < n and (h[b, 0] < h[a, 0] or (h[b, 0] == h[a, 0] and h[b, 1] < h[a, 1])):
            a = b
        if h[i, 0] < h[a, 0] or (h[i, 0] == h[a, 0] and h[i, 1] <= h[a, 1]):
            break
        h[a, 0], h[i, 0] = h[i, 0], h[a, 0]
        h[a, 1], h[i, 1] = h[i, 1], h[a, 1]
        i = a
    return n


@njit(nogil=True)
def turn_layers(s, e, k, first, over, lay):
    """merge.overlap_layers' heap loop: notes sorted by key, then start (s, e, k), first / over = its marks -> each
    note's layer into lay (zeros: notes outside the overlapping stretches stay 0; the first free layer, a new one
    when none) -> 0. One note at a time = the same layers as its blocks of notes starting together."""
    n = s.shape[0]
    ends = np.empty((n + 1, 2), np.int64)  # (end, layer)
    free = np.empty((n + 1, 2), np.int64)  # (layer, 0)
    ne = nf = 0
    key = 0
    used = 0
    started = False
    pair = np.zeros(2, np.int64)
    for i in range(n):
        if not first[i]:
            continue
        if not over[i] or not started or k[i] != key:
            ne = nf = 0
            key = k[i]
            started = True
            pair[0], pair[1] = e[i], 0
            ne = _pair_push(ends, ne, pair)
            used = 1
            lay[i] = 0
            continue
        si = s[i]
        while ne > 0 and ends[0, 0] <= si:
            ne = _pair_pop(ends, ne)
            pair[0], pair[1] = ends[ne, 1], 0
            nf = _pair_push(free, nf, pair)
        if nf > 0:
            nf = _pair_pop(free, nf)
            j = free[nf, 0]
        else:
            j = used
            used += 1
        lay[i] = j
        pair[0], pair[1] = e[i], j
        ne = _pair_push(ends, ne, pair)
    return 0


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
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


@njit(nogil=True)
def _put_vlq(out, p, d):
    """A MIDI wait (7 bits per byte, every byte but the last with the top bit set) at out[p]; returns the next p."""
    k = 1 + (d >= 1 << 7) + (d >= 1 << 14) + (d >= 1 << 21)
    for b in range(k):
        left = k - 1 - b
        out[p + b] = ((d >> (7 * left)) & 0x7F) | (0x80 if left > 0 else 0)
    return p + k


@njit(nogil=True)
def midi_track(notes, rows, ch, most, bits):
    """midi_events for the notes at rows (in their order), sorted here too: ticks * 2 (+ 1 on a note-on, so offs
    come first) below 2 ** bits. Lets go of the GIL all the way (midi_out: tracks made side by side)."""
    m = len(rows)
    own = np.empty((m, 4), np.int64)  # (taken out once: reading the rows far apart again and again was most of it)
    k = np.empty(2 * m, np.int64)
    for i in range(m):
        r = rows[i]
        for c in range(4):
            own[i, c] = notes[r, c]
        k[2 * i] = own[i, 0] * 2 + 1
        k[2 * i + 1] = own[i, 1] * 2
    return midi_events(own, np.arange(m), radix_order(k, bits), ch, most)


@njit(nogil=True)
def midi_events(notes, rows, order, ch, most):
    """midi_out.track_events' bytes (end-of-track not included): order = event numbers in time order
    (note i's on = 2i, its off = 2i + 1; note i = notes[rows[i]]); a wait over most is split by empty text events
    (FF 01 00)."""
    size, last = 0, 0
    for i in range(len(order)):
        j = order[i]
        d = notes[rows[j >> 1], j & 1] - last
        last += d
        while d > most:
            size += 7  # (most takes 4 bytes)
            d -= most
        size += 4 + (d >= 1 << 7) + (d >= 1 << 14) + (d >= 1 << 21)
    out = np.empty(size, np.uint8)
    p, last = 0, 0
    for i in range(len(order)):
        j = order[i]
        r = rows[j >> 1]
        d = notes[r, j & 1] - last
        last += d
        while d > most:
            p = _put_vlq(out, p, most)
            out[p], out[p + 1], out[p + 2] = 0xFF, 0x01, 0x00
            p += 3
            d -= most
        p = _put_vlq(out, p, d)
        on = (j & 1) == 0
        out[p] = (0x90 if on else 0x80) | ch
        out[p + 1] = notes[r, 2] & 0xFF
        out[p + 2] = notes[r, 3] & 0xFF if on else 0
        p += 3
    return out


@njit(nogil=True)
def limit_live(st, x, frames, loud, slot):
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
                loud[slot] = st[LIM_ENV]
                st[LIM_G0], st[LIM_G1] = st[LIM_G1], LIM_CEILING / max(loud.sum(), LIM_CEILING)
            st[LIM_PREV], st[LIM_PEAK] = st[LIM_PEAK], 0.0


@njit(nogil=True)
def smooth_sor(flat, red, black, wide, om, times):
    """nested.smooth_values' loop: red-black over-relaxation on a grid with a zero border (flat), red / black = the
    cells each half changes, the same sums in the same order."""
    for _ in range(times):
        for part in (red, black):
            for i in part:
                g = (flat[i - 1] + flat[i + 1] + flat[i - wide] + flat[i + wide]) / 4
                here = flat[i]
                flat[i] = here + om * (g - here)


@njit(nogil=True)
def _domino_safe1(x):
    """tempo.domino_safe of one value (the same float32 rounding)."""
    bpm = np.float64(np.float32(6e7 / max(x, 1)))
    return np.int64(np.trunc(6e7 / bpm)) == x


@njit(nogil=True)
def _safe1n(u):
    """tempo._safe1: Domino-safe and in 1..0xFFFFFF."""
    return 1 <= u <= 0xFFFFFF and _domino_safe1(u)


@njit(nogil=True)
def _nearest1n(x, reach):
    """tempo._nearest1 (x already in 1..0xFFFFFF): u, then u + s k, u - s k for k = 1, 2... (s = x's side)."""
    u = np.int64(np.rint(x))
    if _safe1n(u):
        return u
    s = 1 if x >= u else -1
    for k in range(1, reach):
        if _safe1n(u + s * k):
            return u + s * k
        if _safe1n(u - s * k):
            return u - s * k
    return u


@njit(nogil=True)
def _pw_sum(v, lo, n):
    """NumPy's float64 sum of v[lo:lo + n] (pairwise, blocks of 8: the very same additions as np.sum, which starts
    from 0.0 + this; dev/scratch/pairwise_check.py). Halves added up with a list of its own, not by calling itself
    (kept from its compiled days: a saved compiled copy of a function calling itself crashed on loading)."""
    if n <= 128:
        return _pw_leaf(v, lo, n)
    los = np.empty(64, np.int64)
    ns = np.empty(64, np.int64)
    stage = np.zeros(64, np.int64)
    sums = np.empty(64, np.float64)
    top, ns_top = 0, 0
    los[0], ns[0] = lo, n
    while top >= 0:
        a, m = los[top], ns[top]
        if m <= 128:
            sums[ns_top] = _pw_leaf(v, a, m)
            ns_top += 1
            top -= 1
            continue
        half = m // 2
        half -= half % 8
        if stage[top] == 0:
            stage[top] = 1
            top += 1
            los[top], ns[top], stage[top] = a, half, 0
        elif stage[top] == 1:
            stage[top] = 2
            top += 1
            los[top], ns[top], stage[top] = a + half, m - half, 0
        else:
            ns_top -= 1
            sums[ns_top - 1] = sums[ns_top - 1] + sums[ns_top]
            top -= 1
    return sums[0]


@njit(nogil=True)
def _pw_leaf(v, lo, n):
    """_pw_sum of 128 values or fewer."""
    if n < 8:
        res = 0.0
        for i in range(n):
            res += v[lo + i]
        return res
    r0, r1, r2, r3 = v[lo], v[lo + 1], v[lo + 2], v[lo + 3]
    r4, r5, r6, r7 = v[lo + 4], v[lo + 5], v[lo + 6], v[lo + 7]
    i = 8
    while i < n - (n % 8):
        r0 += v[lo + i]
        r1 += v[lo + i + 1]
        r2 += v[lo + i + 2]
        r3 += v[lo + i + 3]
        r4 += v[lo + i + 4]
        r5 += v[lo + i + 5]
        r6 += v[lo + i + 6]
        r7 += v[lo + i + 7]
        i += 8
    res = ((r0 + r1) + (r2 + r3)) + ((r4 + r5) + (r6 + r7))
    while i < n:
        res += v[lo + i]
        i += 1
    return res


@njit(nogil=True)
def _side_next(u, way):
    """tempo._near_pair's side(): the first safe value from u going way within 64 steps, or -1 (LookupError)."""
    for _ in range(64):
        if _safe1n(u):
            return u
        u += way
    return -1


@njit(nogil=True)
def _near_pair_n(n, target, reach):
    """tempo._near_pair: (0, a, ticks of a, b) found / (1, ...) None / (2, ...) _FAR. Every pair tried once; the
    nearest by (nearness, down place, up place), the same pick in any order."""
    q = target / n
    downs = np.empty(reach, np.int64)
    ups = np.empty(reach, np.int64)
    v = _side_next(np.int64(math.floor(q)), -1)
    if v < 0:
        return 2, 0, 0, 0
    downs[0], nd, cur_d = v, 1, v - 1
    v = _side_next(np.int64(math.ceil(q)), 1)
    if v < 0:
        return 2, 0, 0, 0
    ups[0], nu, cur_u = v, 1, v + 1
    found, b_far, b_ia, b_ib, b_a, b_b, b_k = False, 0.0, 0, 0, 0, 0, 0
    ed, eu = 0, 0  # (pairs ia < ed, ib < eu tried already)
    while True:
        for ia in range(nd):
            for ib in range(nu):
                if ia < ed and ib < eu:
                    continue
                av, bv = downs[ia], ups[ib]
                if bv == av:
                    continue
                gap = bv - av
                num = n * bv - target
                k = num // gap
                if num % gap != 0 or not 0 <= k <= n:
                    continue
                far = (q - np.float64(av)) + (np.float64(bv) - q)
                if (not found or far < b_far or (far == b_far and (ia < b_ia or (ia == b_ia and ib < b_ib)))):
                    found, b_far, b_ia, b_ib, b_a, b_b, b_k = True, far, ia, ib, av, bv, k
        ed, eu = nd, nu
        limit = np.inf if not found else b_far + 1e-6
        grow_d = nd < reach and ups[0] - (downs[nd - 1] - 1) <= limit
        grow_u = nu < reach and (ups[nu - 1] + 1) - downs[0] <= limit
        if not grow_d and not grow_u:
            break
        if grow_d:
            v = _side_next(cur_d, -1)
            if v < 0:
                return 2, 0, 0, 0
            downs[nd], cur_d = v, v - 1
            nd += 1
        if grow_u:
            v = _side_next(cur_u, 1)
            if v < 0:
                return 2, 0, 0, 0
            ups[nu], cur_u = v, v + 1
            nu += 1
    if not found:
        return (1 if nd >= reach and nu >= reach else 2), 0, 0, 0
    return 0, b_a, b_k, b_b


@njit(nogil=True)
def _next1n(u, way, reach):
    """tempo._next1: the first safe value u + way k, k = 1 .. reach - 1 (u when none)."""
    for k in range(1, reach):
        if _safe1n(u + way * k):
            return u + way * k
    return u


@njit(nogil=True)
def _next_small_n(u, way, reach):
    """tempo._next_small / _next_by_bpm: walked by the BPMs a file can store."""
    c = u + way
    while 1 <= c <= 0xFFFFFF and (c - u) * way < reach:
        v = np.int64(np.trunc(6e7 / np.float64(np.float32(6e7 / c))))
        if v == c:
            return c
        c = v if (v - c) * way > 0 else c + way
    return u


@njit(nogil=True)
def _next_safe_n(body, nb, way, reach, out):
    """tempo.next_safe of body[:nb] into out (its three ways by length, as there)."""
    if nb == 1:
        out[0] = _next1n(body[0], way, reach)
        return
    for i in range(nb):
        u = body[i]
        if nb <= 32:
            out[i] = _next_small_n(u, way, reach)
            continue
        got = -1
        for k in range(1, 9):
            c = u + way * k
            if _safe1n(c):
                got = c
                break
        out[i] = got if got != -1 else _next_small_n(u, way, reach)


@njit(nogil=True)
def _safe_side_n(start, way, reach, safe_reach, out):
    """tempo._safe_side: up to reach safe values from start (included) going way into out; how many."""
    c, u = 0, start - way
    while c < reach:
        found = 0
        last = u
        for k in range(1, 513):  # (a window of 512 candidates)
            x = u + way * k
            if 1 <= x <= 0xFFFFFF and _domino_safe1(x):
                found += 1
                if c < reach:
                    out[c] = x
                    c += 1
                    last = x
        if found == 0:
            nxt = _next1n(u, way, safe_reach)
            if nxt == u:
                break
            out[c] = nxt
            c += 1
            u = nxt
            continue
        u = last
    return c


@njit(nogil=True)
def _full_pair_n(n, target, reach, safe_reach):
    """tempo._flat_pair_search's full search (safe values far apart): (0, a, ticks of a, b) / (1, ...) None."""
    q = target / n
    downs = np.empty(reach, np.int64)
    ups = np.empty(reach, np.int64)
    nd = _safe_side_n(np.int64(math.floor(q)), -1, reach, safe_reach, downs)
    nu = _safe_side_n(np.int64(math.ceil(q)), 1, reach, safe_reach, ups)
    if nd == 0 or nu == 0:
        return 1, 0, 0, 0
    for ia in range(nd):  # (a pair of one value adding up exactly: the first, a then b)
        for ib in range(nu):
            if ups[ib] == downs[ia] and downs[ia] * n == target:
                return 0, downs[ia], n, downs[ia]
    found, b_far, b_a, b_b, b_k = False, 0.0, 0, 0, 0
    for ia in range(nd):
        for ib in range(nu):
            av, bv = downs[ia], ups[ib]
            if bv == av:
                continue
            num = n * bv - target
            k = num // (bv - av)
            if num % (bv - av) != 0 or not 0 <= k <= n:
                continue
            far = (q - np.float64(av)) + (np.float64(bv) - q)
            if not found or far < b_far:  # (the first of the nearest)
                found, b_far, b_a, b_b, b_k = True, far, av, bv, k
    if not found:
        return 1, 0, 0, 0
    return 0, b_a, b_k, b_b


@njit(nogil=True)
def _flat_pair_n(n, target, reach, safe_reach):
    """tempo._flat_pair_search: (0, a, ticks of a, b) / (1, ...) None."""
    a0 = target // n
    r = target - a0 * n
    if r == 0 and _safe1n(a0):
        return 0, a0, n, a0
    if r != 0 and _safe1n(a0) and _safe1n(a0 + 1):
        return 0, a0, n * (a0 + 1) - target, a0 + 1
    code, pa, pk, pb = _near_pair_n(n, target, reach)
    if code != 2:
        return code, pa, pk, pb
    return _full_pair_n(n, target, reach, safe_reach)


@njit(nogil=True)
def _put_pair(out, n, a, ka, b):
    for i in range(n):
        out[i] = a if i < ka else b


@njit(nogil=True)
def _fit_exact_n(ideal, n, target, safe_reach, pair_reach, out):
    """tempo._fit_exact of ideal[:n] (left as it is) into out[:n]: 0 = made, 1 = None."""
    top = 0xFFFFFF
    if n == 0 or not n <= target <= n * top:
        return 1
    lo, hi = np.inf, -np.inf
    for i in range(n):
        lo, hi = min(lo, ideal[i]), max(hi, ideal[i])
    if hi - lo < 1:
        code, pa, pk, pb = _flat_pair_n(n, target, pair_reach, safe_reach)
        if code == 0:
            _put_pair(out, n, pa, pk, pb)
            return 0
    scale = target / (0.0 + _pw_sum(ideal, 0, n))
    want = np.empty(n, np.float64)
    for i in range(n):
        want[i] = ideal[i] * scale
        out[i] = _nearest1n(min(max(want[i], 1.0), np.float64(top)), safe_reach)
    nb = max(0, n - 2)  # (the last two are picked as a pair at the end)
    nxt = np.empty(max(nb, 1), np.int64)
    for _ in range(8):
        s = 0
        for i in range(nb):
            s += out[i]
        rest = target - s - np.int64(0.0 + _pw_sum(want, nb, n - nb))
        way = 1 if rest > 0 else -1
        _next_safe_n(out, nb, way, safe_reach, nxt)
        used, took = 0, 0
        for i in range(nb):
            used += abs(nxt[i] - out[i])
            if used > abs(rest):
                break
            took += 1
        if took == 0:
            break
        for i in range(took):
            out[i] = nxt[i]
    s = 0
    for i in range(nb):
        s += out[i]
    rest = target - s
    if n == 1:
        out[0] = rest
        return 0 if 1 <= rest <= top and _domino_safe1(rest) else 1
    a0 = np.int64(np.rint(rest / 2))
    for k in range(safe_reach):
        for a in (a0 - k, a0 + k):
            b = rest - a
            if _safe1n(a) and _safe1n(b):
                out[n - 2], out[n - 1] = a, b
                return 0
    return 1


@njit(nogil=True)
def _fit_blocks_n(drawn, a, n0, n1, kj, target, every, safe_reach, pair_reach, out):
    """tempo._fit_blocks on file_plan's curved gap (ideal = the drawn tempo at kj even steps): 0 = out[:kj] made, 1 =
    None."""
    n = kj
    top = 0xFFFFFF
    ideal = np.empty(n, np.float64)
    lo, hi = np.inf, -np.inf
    for i in range(n):
        x = np.float64(drawn[n0 - a + (i * (n1 - n0)) // kj])
        ideal[i] = x
        lo, hi = min(lo, x), max(hi, x)
    if n <= every and every > 1:
        if n <= target <= n * top:
            code, pa, pk, pb = _flat_pair_n(n, target, pair_reach, safe_reach)
            if code == 0:
                _put_pair(out, n, pa, pk, pb)
                return 0
        return _fit_exact_n(ideal, n, target, safe_reach, pair_reach, out)
    if every <= 1 or hi - lo < 1:
        return _fit_exact_n(ideal, n, target, safe_reach, pair_reach, out)
    if not n <= target <= n * top:
        return 1
    nb = (n + every - 1) // every
    want = np.empty(nb, np.float64)
    for b in range(nb):
        ln = min(every, n - b * every)
        want[b] = ideal[b * every] * ln
    scale = target / (0.0 + _pw_sum(want, 0, nb))
    for b in range(nb):
        want[b] = want[b] * scale
    done, owed = 0, 0.0
    for b in range(nb - 1):
        x = (want[b] + owed) / every
        u = _nearest1n(min(max(x, 1.0), np.float64(top)), safe_reach)
        for i in range(b * every, b * every + every):
            out[i] = u
        owed += want[b] - u * every
        done += u * every
    last = n - (nb - 1) * every
    rest = target - done
    if not last <= rest <= last * top:
        return _fit_exact_n(ideal, n, target, safe_reach, pair_reach, out)
    tail = out[(nb - 1) * every:]
    code, pa, pk, pb = _flat_pair_n(last, rest, pair_reach, safe_reach)
    if code == 0:
        _put_pair(tail, last, pa, pk, pb)
        return 0
    if _fit_exact_n(np.full(last, rest / last), last, rest, safe_reach, pair_reach, tail) == 0:
        return 0
    return _fit_exact_n(ideal, n, target, safe_reach, pair_reach, out)


@njit(nogil=True)
def _least1(t, most):
    """tempo_file._least of one gap."""
    least = max(1, -(-t // most))
    if least == 1 and t >= 2 and not _domino_safe1(min(max(t, 1), most)):
        least = 2
    return least


@njit(nogil=True)
def gap_plan(j, last, a, n, total, k, drawn, pref, every, most, safe_reach, pair_reach, lens, vals, pos, got):
    """tempo_file.file_plan's loop over one Keep timing part's gaps from gap j: each gap's file tempo as runs into
    lens / vals from pos (each gap its own runs), k[j] changed where its pair needs a tick more or less, as that
    loop does. last = the value the gap before ended on (-1 = none). -> (number of gaps, last, pos); a smaller
    gap number = stopped there: lens / vals have no room for its most runs (kj + 10; the caller keeps the runs,
    makes room and goes on from there)."""
    g = len(k)
    while j < g:
        n0, n1 = n[j], n[j + 1]
        kj, tj = k[j], total[j]
        if pos + kj + 10 > len(lens):  # (no room for this gap's most runs: one per tick: the caller makes room)
            return j, last, pos
        curved = kj > every and pref[n1 - a - 1] - pref[n0 - a] > 0
        least = _least1(tj, most)
        kk = max(kj, 1)
        qa = tj // kk
        exact = tj % kk == 0
        qb = qa if exact else qa + 1
        qka = kj if exact else kj * qb - tj
        if (not curved and kj >= 1 and qa >= 1 and qb <= most and least <= kj and kj <= tj
                and _domino_safe1(min(max(qa, 1), most)) and _domino_safe1(min(max(qb, 1), most))):
            if 0 < qka < kj and last != -1 and qa != last and qb == last:
                lens[pos], vals[pos] = kj - qka, qb
                lens[pos + 1], vals[pos + 1] = qka, qa
                pos += 2
                last = qa
            else:
                if qka > 0:
                    lens[pos], vals[pos] = qka, qa
                    pos += 1
                if kj - qka > 0:
                    lens[pos], vals[pos] = kj - qka, qb
                    pos += 1
                last = qb if qka < kj else qa
            j += 1
            continue
        have = False
        if curved:
            code = _fit_blocks_n(drawn, a, n0, n1, kj, tj, every, safe_reach, pair_reach, got)
            if code == 2:
                return j, last, pos
            have = code == 0
        if not have:
            for step in range(17):  # (kj, then kj + 1, kj - 1, kj + 2, kj - 2 ... kj - 8)
                c = kj if step == 0 else kj + ((step + 1) // 2) * (1 if step % 2 else -1)
                if least <= c <= tj:  # (file_plan's _Pairs.get: the pair, else _fit_exact of the flat tempo)
                    code, pa, pk, pb = _flat_pair_n(c, tj, pair_reach, safe_reach)
                    if code == 0:
                        _put_pair(got, c, pa, pk, pb)
                    elif _fit_exact_n(np.full(c, tj / c), c, tj, safe_reach, pair_reach, got) != 0:
                        continue
                    kj = c
                    k[j] = c
                    have = True
                    break
            if not have:  # (nothing near: the nearest values, not Domino-safe)
                q = tj // kj
                extra = tj - q * kj
                for i in range(kj):
                    got[i] = q + 1 if i < extra else q
            if last != -1 and got[0] != last and got[kj - 1] == last:  # (pairs only: a curve's steps never)
                got[:kj] = got[:kj][::-1].copy()
        start = 0
        for i in range(1, kj + 1):
            if i == kj or got[i] != got[i - 1]:
                lens[pos], vals[pos] = i - start, got[start]
                pos += 1
                start = i
        last = got[kj - 1]
        j += 1
    return j, last, pos


@njit(nogil=True)
def gap_ticks(a, e, ts, bs, drawn, was, u, most):
    """tempo_file.keep_gaps' work on one Keep timing part without loose ticks: the anchors n (a, e, the note ticks
    of ts inside, the bar lines of bs inside), each gap's time (d x u, or was's running total when it has one:
    len(was) > 0), its file ticks k (rounded on a running total, clipped like _least) and the anchors where two
    notes are under a tick apart. The same sums in the same order as the NumPy code."""
    i0 = np.searchsorted(ts, a)
    i1 = np.searchsorted(ts, e, side="right")
    b0 = np.searchsorted(bs, a, side="right")
    b1 = np.searchsorted(bs, e)
    # (a, then the note ticks and bar lines strictly inside merged (both sorted already), then e; repeats once)
    n = np.empty(2 + (i1 - i0) + max(0, b1 - b0), np.int64)
    n[0] = a
    c = 1
    i, j = i0, b0
    while i < i1 or j < b1:
        if j >= b1 or (i < i1 and ts[i] <= bs[j]):
            x = ts[i]
            i += 1
        else:
            x = bs[j]
            j += 1
        if a < x < e and x != n[c - 1]:
            n[c] = x
            c += 1
    n[c] = e
    c += 1
    n = n[:c]
    note = np.zeros(c, np.bool_)  # (an anchor that is a note tick: ts[i0:i1] holds them all, sorted)
    j = i0
    for i in range(c):
        while j < i1 and ts[j] < n[i]:
            j += 1
        note[i] = j < i1 and ts[j] == n[i]
    sums = np.empty(e - a + 1, np.float64)
    s = 0.0
    sums[0] = 0.0
    for i in range(e - a):
        s += np.float64(drawn[i])
        sums[i + 1] = s
    g = c - 1
    total = np.empty(g, np.int64)
    k = np.empty(g, np.int64)
    dense = np.empty(g, np.int64)
    nd = 0
    run, before = 0.0, 0.0
    own = len(was) > 0
    for i in range(g):
        x0, x1 = n[i] - a, n[i + 1] - a
        d = n[i + 1] - n[i]
        tot = was[x1] - was[x0] if own else d * u
        exact = tot / ((sums[x1] - sums[x0]) / d)
        if note[i] and note[i + 1] and exact < 1:
            dense[nd] = n[i]
            nd += 1
        run += exact
        r = np.rint(run)
        kk = np.int64(r - before)
        before = r
        least = max(1, -(-tot // most))
        if least == 1 and tot >= 2 and not _domino_safe1(min(max(tot, 1), most)):
            least = 2
        total[i] = tot
        k[i] = min(max(kk, least), tot)
    return n, total, k, dense[:nd]


@njit(nogil=True)
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


# (Load MIDI: midi_scan's own functions, under the names the library's loops have)
midi_walk = njit(nogil=True)(midi_scan.walk)
midi_rewrite = njit(nogil=True)(midi_scan.rewrite)
midi_note_ticks = njit(nogil=True)(midi_scan.note_ticks)
midi_busy_hits = njit(nogil=True)(midi_scan.busy_hits)
midi_gather = njit(nogil=True)(midi_scan.gather)
midi_index = njit(nogil=True)(_index.index)
midi_close = njit(nogil=True)(_index.close)
midi_grid = njit(nogil=True)(_index.grid)
midi_halve = njit(nogil=True)(_index.halve)
midi_cover = njit(nogil=True)(_index.cover)
midi_merge_loud = njit(nogil=True)(_index.merge_loud)


@njit(nogil=True)
def midi_cover_many(faint, klo, khi, starts, ends, e0s, e1s, cp_tick, cp_run, limits, lasts, buf, pos, eq, ec, ev,
                    skip, t1, ax, bx, W, k_lo, normal, behind, tab, nx, filled, marks, sq, sa, sb, sv, cnt_q, first_q,
                    vel_q, st, covered):
    """roll_loaded.TrackReader.read for many small tracks in one go (roll_loaded.read_small: everything per track
    worked out before, their bytes end to end in buf from pos), in the same order with the same covered check
    (exact_tab). Returns how many tracks went into covered (read later, read_covered)."""
    nk = tab.shape[0]
    nc = 0
    for i in range(len(starts)):
        a = max(klo[i], k_lo) - k_lo
        b = min(khi[i], k_lo + nk - 1) - k_lo + 1
        full = True
        for r in range(a, b):
            if filled[r] < W:
                full = False
                break
        if full:
            covered[nc] = i
            nc += 1
            continue
        tick0 = cp_tick[i]
        for e in range(e0s[i], e1s[i]):
            q = np.int64(eq[e])
            cnt_q[q] = ec[e]
            first_q[q] = tick0
            vel_q[q] = ev[e]
        size = ends[i] - starts[i]
        b8 = buf[pos[i]:pos[i] + size]
        st[:] = 0
        st[_index.C_TICK] = tick0
        st[_index.C_RUN] = cp_run[i]
        colour = behind if faint[i] else normal
        p = midi_cover(b8, 0, size, starts[i], skip, st, cnt_q, first_q, vel_q, t1, -1, ax, bx, W, k_lo, colour, tab,
                       nx, filled, marks, sq, sa, sb, sv)
        close = t1 + 1 if st[_index.C_STOP] == 1 or ends[i] < limits[i] else max(st[_index.C_TICK], lasts[i])
        midi_cover(b8, p, p, starts[i], skip, st, cnt_q, first_q, vel_q, t1, close, ax, bx, W, k_lo, colour, tab, nx,
                   filled, marks, sq, sa, sb, sv)
    return nc
play_read = njit(nogil=True)(midi_play.read)
play_order = njit(nogil=True)(midi_play.order)
play_order_count = njit(nogil=True)(midi_play.order_count)
play_order_place = njit(nogil=True)(midi_play.order_place)
play_hist = njit(nogil=True)(midi_play.hist)
play_emit = njit(nogil=True)(midi_play.emit)
play_programs = njit(nogil=True)(midi_play.programs)
play_stamp = njit(nogil=True)(midi_play.stamp)
play_condense = njit(nogil=True)(midi_play.condense)
play_stamp_pre = njit(nogil=True)(midi_play.stamp_pre)
piece_track = njit(nogil=True)(midi_piece.piece_track)
piece_held_ons = njit(nogil=True)(midi_piece.held_ons)
piece_held_head = njit(nogil=True)(midi_piece.held_head)
piece_note_ons = njit(nogil=True)(midi_piece.note_ons)
piece_runs_sort = njit(nogil=True)(midi_piece.runs_sort_fold)
piece_runs_merge = njit(nogil=True)(midi_piece.runs_merge)
piece_counted_track = njit(nogil=True)(midi_piece.counted_track)
piece_order_ok = njit(nogil=True)(midi_piece.order_ok)
piece_open_at = njit(nogil=True)(midi_piece.open_at)
dc_block = njit(nogil=True)(_dc_block)


@njit(nogil=True)
def loaded_pixels(img, x0, y0, tab, marks, c0, c1, kb, row0, row1, rgb):
    """roll_loaded.loaded_pixels (a loaded MIDI's key x pixel table painted like notes), the same pixels."""
    ih, iw = img.shape[0], img.shape[1]
    for kk in range(tab.shape[0]):
        a, b = row0[kk], row1[kk]
        ya, yb = max(a, y0), min(b, y0 + ih)
        if yb <= ya:
            continue
        solid = b - 1 - a < 2
        for c in range(c0, c1):
            code = tab[kk, c]
            if code < 0:
                continue
            x = c + kb - x0
            if x < 0 or x >= iw:
                continue
            m = (marks[kk, c] & 3) != 0
            half = (marks[kk, c] & 4) != 0
            for y in range(ya, yb):
                if solid or y == a or y == b - 1 or m:
                    e = 1
                elif half:  # (a hidden note starts / ends: halfway to the edge colour)
                    for i in range(3):
                        img[y - y0, x, i] = (np.int64(rgb[code, 0, i]) + rgb[code, 1, i]) // 2
                    continue
                else:
                    e = 0
                img[y - y0, x, 0] = rgb[code, e, 0]
                img[y - y0, x, 1] = rgb[code, e, 1]
                img[y - y0, x, 2] = rgb[code, e, 2]


@njit(nogil=True)
def all_tops(top, data, offs, nrows, nbytes, pklo, glo, pf, cols, used, uoff, ids, x0, tpp, G):
    """track_list.loaded_tops' block for the loaded tracks ids in order (a later one over an earlier one): each key x
    pixel of top the track whose loading-pass small picture has a note there, read like track_list.small_mask with
    one pixel more past the block (each pixel's columns run up to the next pixel's first). The same tracks.
    used[uoff[r] + c] = track r's picture column c has a note on any key (pixels without one skipped at once)."""
    span, w = top.shape
    c0 = np.empty(w + 1, np.int64)
    c1 = np.empty(w + 1, np.int64)
    for q in range(len(ids)):
        r = ids[q]
        n = cols[r]
        if n == 0 or nrows[r] == 0:
            continue
        step = G * pf[r]
        base = glo[r] * G
        if math.floor((x0 * tpp - base) / step) >= n or math.ceil(((x0 + w) * tpp + tpp - base) / step) <= 0:
            continue  # (its picture ends before the block / starts after it: the columns only grow along x)
        first, last = -1, -1
        for x in range(w + 1):
            ta = (x0 + x) * tpp
            a = np.int64(math.floor((ta - base) / step))
            b = max(np.int64(math.ceil((ta + tpp - base) / step)), a + 1)
            c0[x], c1[x] = a, b
            if b > 0 and a < n:
                if first < 0:
                    first = x
                last = x
        if first < 0:
            continue
        joined = tpp >= step
        o, nb, k0 = offs[r], nbytes[r], pklo[r]
        for x in range(first, min(last + 1, w)):
            a = min(max(c0[x], 0), n - 1)
            if not joined:
                e = a + 1
            elif x < last:
                e = max(min(max(c0[x + 1], 0), n - 1), a + 1)
            else:
                e = max(min(max(c1[last], 1), n), a + 1)
            u = uoff[r]
            hit = False
            for c in range(a, e):
                if used[u + c]:
                    hit = True
                    break
            if not hit:
                continue
            for row in range(nrows[r]):
                key = k0 + row
                if key >= span:
                    break
                rb = o + row * nb
                for c in range(a, e):
                    if (data[rb + (c >> 3)] >> (7 - (c & 7))) & 1:
                        top[key, x] = r
                        break


def warm():
    """Compiles (or loads) every loop with the same kinds of values the program hands them. Only called on the
    Numba copy of this file (files/numba_loops.py: no own library, Numba installed); here it would just run them."""
    i1, f, b = np.zeros(1, np.int64), 0.0, np.zeros(1, bool)
    e0, i0e, u0 = np.zeros(0), np.zeros(0, np.int64), np.zeros(0, np.uint64)  # (no spots: made, nothing read)
    fp, ip = np.zeros(64), np.zeros(64, np.int64)
    for fn in (stir_spots_one, _stir_spots_cores):
        fn(e0, e0, fp, ip, e0.copy(), e0.copy())
    for fn in (stir_wrap_one, _stir_wrap_cores):
        fn(i0e, e0, e0, e0, e0, e0.copy())
    for fn in (stir_nearest_one, _stir_nearest_cores):
        fn(i0e, i0e, i0e, e0, e0, i0e, e0, i0e, 0.0, 1.0, 1.0, e0, e0, False, i0e.copy())
    for fn in (liquify_sample_one, _liquify_sample_cores):
        fn(np.zeros((2, 2, 2)), e0, e0, np.zeros((0, 2)))
    dice_mix(np.uint64(1), u0, u0, u0, u0, e0.copy())
    winner_pieces(i1, i1, i1 + 1, i1, i1.copy())
    s, e, key, color, first, _ = paint_order(np.zeros((1, 6), np.int64), np.zeros(1, np.int64), i1, 1,
                                             np.zeros(0, bool), np.arange(15, dtype=np.int64), 16, 0)
    order_screen(s, e, key, color, first, 0, 1, f, f, f, f, f, f, b, False, f, f)
    order_rects(s, e, key, color, first, 0, 1, f, f, f, f, f, f, b, False, f, f, 0, 0, 1, i1, i1)
    last_on_pixels(i1.copy(), i1.copy(), i1.copy(), i1.copy(), 0, 0, 1, i1, i1)
    note_top(np.full(1, -1, np.int64), np.zeros(1, np.uint8), 0, 0, 1, 1, i1, i1, i1, i1, i1, False)
    paint_notes(np.zeros((1, 3), np.uint8), 0, 0, 1, 1, i1, i1, i1, i1, i1, np.zeros((1, 2, 3), np.uint8), False)
    overlap_sweep(np.zeros((1, 6), np.int64), i1, i1)
    overlap_order(np.zeros((1, 6), np.int64))
    recolour_apart(i1, i1, i1, i1, i1.copy(), np.zeros(1), i1, i1 + 1, 1)
    midi_events(np.zeros((1, 6), np.int64), i1 * 0, np.zeros(2, np.int64), 0, 1)
    midi_track(np.zeros((1, 6), np.int64), i1 * 0, 0, 1, 1)
    note_stretches(np.zeros((1, 4), np.int64), True, 0, np.zeros(0, np.int64))
    clash_levels(np.zeros((1, 3), np.int64), np.array([0, 1, 1], np.int64), i1.copy())
    any_overlap(np.zeros((2, 4), np.int64), np.zeros(2, np.int64), 1)
    turn_sample(np.zeros(1), i1 * 0, i1 * 0, np.ones(13), 0, 0, 1, False)
    turn_layers(i1, i1, i1, np.zeros(1, np.uint8), np.zeros(1, np.uint8), i1.copy())
    key_order(i1, i1, i1, i1)
    k_order, k_at = _key_groups(i1)
    on_cores(_key_runs_cores, _key_runs, CORES_LEAST, k_order, k_at, i1, i1, i1)  # (the shared loop made now, not mid-paste)
    radix_order(i1, 1)
    ring_runs(i1, i1, i1, i1, 0.5, 1)
    clash_pairs(np.zeros((1, 4), np.int64), 1, 1 << 28)
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
    t3 = np.array([0, 1, 3], np.int64)
    gap_ticks(0, 3, t3, t3[:0], np.full(3, 500000, np.int64), t3[:0], 500000, 0xFFFFFF)
    gap_ticks(0, 3, t3, t3, np.full(3, 500000, np.int64), np.arange(4, dtype=np.int64) * 500000, 0, 0xFFFFFF)
    gap_plan(0, -1, 0, t3, np.array([500000, 1000000], np.int64), np.array([1, 2], np.int64),
             np.full(3, 500000, np.int64), np.zeros(3, np.int64), 24, 0xFFFFFF, 8192, 48, np.zeros(16, np.int64),
             np.zeros(16, np.int64), 0, np.zeros(16, np.int64))
    u8 = np.zeros(1, np.uint8)
    mu = np.zeros(1, np.uint16)  # (LoadedPlayer.muted: each track's channels muted, a bit each)
    midi_walk(u8, 0, 0, True, 0, np.zeros(midi_scan.ST_SIZE, np.int64), np.zeros(midi_scan.C_SIZE, np.int64))
    midi_rewrite(u8, 0, 0, True, i1, i1.copy(), False, np.zeros(128, np.uint8), i1[:0], np.zeros(1, np.int64),
                 u8[:0], u8.copy(), 0, np.zeros(midi_scan.RW_SIZE, np.int64), 0, i1[:0], -1, 0xFFFF, 1)
    midi_note_ticks(u8, 0, 0, True, u8.copy(), 0, np.zeros(midi_scan.NT_SIZE, np.int64))
    midi_busy_hits(u8, 0, 0, 0, i1[:0], 1, np.zeros(midi_scan.BH_SIZE, np.int64), i1.copy(), i1.copy(),
                   np.zeros(2, np.int64), i1.copy(), i1.copy(), i1.copy(), u8.copy())
    midi_gather(u8, 0, 0, 0, i1[:0], 0, 1, 0, 127, u8.copy(), np.zeros(midi_scan.GA_SIZE, np.int64), i1.copy(),
                i1.copy(), i1.copy(), i1.copy(), i1.copy(), i1.copy(), i1.copy(), i1.copy(), i1.copy(), i1.copy(),
                i1.copy(), u8.copy())
    q, z = np.zeros(_index.QS, np.int64), np.zeros(4, np.int64)
    ix, cov, i32 = _index.new_state(), np.zeros((128, 8), np.int32), np.zeros((128, 8), np.int32)
    midi_index(u8, 0, 0, 0, 60, ix, q, q.copy(), q.copy(), q.copy(), q.copy(), q.copy(), z, z.copy(), z.copy(),
               z.copy(), z.copy(), z.copy(), z.copy(), z.copy(), z.copy(), z.copy(), z.copy(), z.copy(), cov,
               np.zeros((128, 8), np.uint8), np.zeros(16, np.int64), z.copy(), z.copy(), z.copy(),
               np.zeros((4, _index.META_KEEP), np.uint8), z.copy(), np.zeros(4, np.int32),
               np.zeros(16 * _index.CC_KINDS, np.int64))
    midi_close(ix, 60, q, q.copy(), q.copy(), z, z.copy(), z.copy(), z.copy(), z.copy(), cov,
               np.zeros((128, 8), np.uint8))
    midi_grid(cov, np.zeros((128, 8), np.uint8), 0, 1, 0, 0, i32, i32.copy(), i32.copy(), 0,
              np.zeros((1, 2), np.uint8), 1)
    midi_halve(i32, i32.copy(), np.zeros((128, 4), np.int32), np.zeros((128, 4), np.int32))
    t2, m2 = np.full((1, 2), -1, np.int64), np.zeros((1, 2), np.uint8)
    midi_cover(u8, 0, 0, 0, i1[:0], np.zeros(_index.C_SIZE, np.int64), q, q.copy(), q.copy(), 1, 0, 1.0, 0.0, 2, 0,
               np.zeros((16, 32), np.int64), t2, np.zeros((1, 3), np.int64), np.zeros(1, np.int64), m2, z, z.copy(),
               z.copy(), z.copy())
    midi_cover_many(np.zeros(1, bool), i1, i1, i1, i1, i1, i1, i1, i1, i1, i1, u8, i1, np.zeros(1, np.int16), i1,
                    u8.copy(), i1[:0], 1, 1.0, 0.0, 2, 0, np.zeros((16, 32), np.int64), np.zeros((16, 32), np.int64),
                    t2, np.zeros((1, 3), np.int64), np.zeros(1, np.int64), m2, z, z.copy(), z.copy(), z.copy(), q,
                    q.copy(), q.copy(), np.zeros(_index.C_SIZE, np.int64), i1.copy())
    loaded_pixels(np.zeros((1, 1, 3), np.uint8), 0, 0, t2, m2, 0, 1, 0, i1, i1 + 1, np.zeros((1, 2, 3), np.uint8))
    all_tops(np.full((1, 1), -1, np.int32), np.zeros(1, np.uint8), i1, i1 + 1, i1 + 1, i1.copy(), i1.copy(), i1 + 1,
             i1 + 1, np.zeros(1, bool), i1.copy(), i1.copy(), 0, 1.0, 1)
    midi_merge_loud(t2, m2, t2.copy(), m2.copy(), np.zeros((1, 3), np.int64), np.zeros(1, np.int64), 1 << 20)
    i32 = np.zeros(1, np.int32)
    play_read(u8, i1, i1.copy(), 1, i1.copy(), i1.copy(), i1.copy(), i1.copy(), i32, i1.copy(), i32.copy(),
              i32.copy(), 0, i1.copy(), i1[:0], i1.copy(), i1.copy(), False, i1.copy(), i1.copy())
    play_order(i1, i32, i32, 0, 1, i1.copy(), i32.copy(), i32.copy())
    play_order_count(i1, 0, i1.copy())
    play_order_place(i1, i32, i32, 0, i1.copy(), i1.copy(), i32.copy(), i32.copy())
    h2 = np.zeros((1, 128), np.int64)
    play_hist(i32, i32, 0, 0, i1, 0, mu, i1, 1, 1, h2)
    line = midi_play.Line(4, midi_play.OPEN * 4)
    play_emit(i32, i32, 0, 0, i1, 0, mu, i1, 1, 1, i1, i1.copy(), np.zeros((1, 3), np.uint8), i1.copy(), False,
              *line.arrays())
    play_programs(u8, 0, 0, np.zeros(midi_play.PS_SIZE, np.int64))
    f1 = np.zeros(1, np.float64)
    line = midi_play.Line(4)
    play_stamp(i32, i32, 0, 0, i1, mu, i1, i1.copy(), f1, f1.copy(), 0.0, 48000.0, 0.0,
               np.zeros(1, np.uint64), np.zeros(1, np.uint32), np.zeros(1, np.uint16), np.zeros(3, np.int64),
               np.zeros(1, np.uint32), np.zeros(1, np.uint32), np.zeros(1, np.int64), np.zeros(1, np.int64),
               *line.arrays())
    play_condense(i1, i32, i32, i1.copy(), 0, i1.copy(), np.zeros(1, np.uint32),
                  np.zeros(1, np.uint16), np.zeros(1, np.uint32), np.zeros(1, np.uint32), np.zeros(1, np.int64),
                  np.zeros(1, np.int64), np.zeros(4, np.int64), np.full(1 << 17, -1, np.int64))
    play_stamp_pre(i32, np.zeros(1, np.uint32), np.zeros(1, np.uint16), np.zeros(1, np.uint32), 0, 0, 0,
                   i1.copy(), f1, f1.copy(), 0.0, 48000.0, 0.0, np.zeros(1, np.uint64),
                   np.zeros(1, np.uint32), np.zeros(1, np.uint16), np.zeros(1, np.uint32), np.zeros(3, np.int64), False,
                   *line.arrays())
    q = np.zeros(midi_piece.QS, np.int64)
    piece_track(np.array([0, 0x90, 60, 100, 0, 0xFF, 0x2F, 0], np.uint8), 0, 0, 0, 0, 10, i1[:0], q, q.copy(),
                q.copy(), 0, -1, 0, np.zeros(32, np.uint8), 5, q.copy(), 0, 0, np.zeros(1, np.int64),
                np.zeros(256, np.uint8), np.zeros(4, np.int64), np.zeros(4, np.uint8), np.zeros(2, np.int64))
    rk, rs, rc = np.array([3, 1, 3], np.int64), np.array([5, 0, 5], np.uint8), np.ones(3, np.int64)
    m = piece_runs_sort(rk, rs, rc, 3, rk.copy(), rs.copy(), rc.copy(), 2)
    m = piece_runs_merge(rk, rs, rc, m, rk.copy(), rs.copy(), rc.copy(), m, np.zeros(6, np.int64),
                         np.zeros(6, np.uint8), np.zeros(6, np.int64))
    piece_counted_track(rk, rs, rc, 2, 0, 0, np.zeros(96, np.uint8))
    piece_order_ok(rk, rs, rc, 2, np.zeros(256, np.uint8))
    piece_open_at(rk, rs, rc, 2, 1 << 40, 1 << 30, np.zeros(2, np.int64))
    dc_block(np.zeros((4, 2), np.float32), np.ones(5), np.zeros((2, 2)))
    piece_held_ons(np.array([0, 0x90, 60, 100, 0, 0xFF, 0x2F, 0], np.uint8), 0, 0, 0, 10, i1[:0], 0, q, q.copy(),
                   np.zeros(1, np.int64), np.zeros(1, np.int64), q.copy())
    piece_held_head(np.zeros(1, np.int64), np.zeros(1, np.int64), np.ones(1, np.int64), -1, 0, np.zeros(16, np.uint8))
    piece_note_ons(np.array([0, 0x90, 60, 100, 0, 0xFF, 0x2F, 0], np.uint8), 0, 0, 0, 10, i1[:0], 0,
                   np.ones(midi_piece.QS, np.int8), np.zeros(4, np.int64), np.zeros(4, np.int64), np.zeros(4, np.int64))
