"""Piano roll: painting the grid, notes, shapes, handles, keyboard and ruler."""

import bisect
import itertools
import json
import math
import time
import tkinter as tk
import tkinter.font as tkfont

import numpy as np

from notes.custom import custom_note_count, edge_inner, gap_lines, role_of
from files.lang import tr, trn
from files.midi_index import CC_MOST
from files.speed import key_sort, loops
from notes.engine import cached_arrays, channels_of, same_shapes, shape_key, shape_notes_tracks, track_ids
from notes.joined import all_tumours
from notes.gliss import pen_shape
from notes.mask import mask_joined, mask_units, unit_of
from notes.merge import preview_parts, slid_preview
from notes.funnel import funnel_curves, funnel_handle_lines, funnel_lines, funnel_note_count, funnel_origins
from notes.paths import KEYS
from notes.sliced import clip_strokes, moved_by, moved_mark, on_side, piece_knives
from notes.sustain import is_sustained
from notes.meter import bar_lines, beat_lines, key_name, sig_text, sigs_of
from notes.tempo import bpm_text, heard_marks, kept_bpms, ruler_marks, short_bpm, tempo_text
from roll.roll_shared import (BLACK, DRAFT_COLOR, EDO, EDO_BLACK, EDO_PIANO, EDO_WHITE, PIANO_88, PREVIEW_LIMIT,
                              PlayFollow, SELECTED_COLOR, SLOT_COLORS, black_bands, draw_boxes, fade, note_colours,
                              note_name, slot_colours, velocity_shades)
from roll.tiles import Tiles, see_through_strips
from window import look

TIP_MARKS = 10  # a ruler tempo mark's tip lists at most this many changes, then "+N more"
CC_CROWD = 2000  # the ruler's CC row: past this many events only those on screen are laid out, and past this many
#                  on screen plain marks (one per pixel) + a count per stretch (hunt 2026-10-29: 300,000 froze each
#                  zoom 1.3 s; user 2026-10-29: "1,234 CC events" where they're too close to read)
CC_MERGE_MOST = 20000  # ... up to this many on screen the row counts them as written (one per tick + kind + value,
#                        the same setup on many tracks once) before deciding: more = counts at once
TICK_FROM, TICK_FULL = 8, 16  # px per tick: a line each tick shows faintly from here, full colour from here
HANDLE_COLOR = look.HANDLE
STROKE_POINT_COLOR = look.STROKE_POINT
PART_COLOR = look.PART
TWIN_COLOR = look.TWIN
# Note colours: number = colour * 32 + velocity // 4 (low velocity = paler fill, the outline stays); colours = the
# slot colours, then selected (unused: a selected shape's notes keep their colours, user; a red line goes round
# them, draw_ring), then the shape being drawn. Ghosts of notes a mask hides (velocity -1 in app.drawn): ghost_code
SELECTED, DRAFT = len(SLOT_COLORS), len(SLOT_COLORS) + 1
NOTE_COLORS = [(shade, b) for f, b in SLOT_COLORS + [SELECTED_COLOR, DRAFT_COLOR] for shade in velocity_shades(f)]
RING_COLOR = look.RING
RING_RGB = np.frombuffer(bytes.fromhex(RING_COLOR[1:]), np.uint8)
PREVIEW_COLOR, PREVIEW_HALO = look.PREVIEW_LINE, look.PREVIEW_HALO  # (draw_edge_preview)
RING_GAP = 4  # px: notes in a row closer than this count as touching for the ring
RING_MAX = 20000  # more ring pieces on screen than this: none drawn (canvas items are slow)
LINE_TAGS = itertools.count()  # (each shape's kept lines get their own canvas tag: keep_lines)


NO_CC = ()  # (loaded_cc with nothing loaded: always the same one, so what's worked out from it is kept)


def sorted_cc(ts, ks, vs, ss):
    """CC event parts (ticks, kinds, values, sources; each part in tick order already) joined in one tick order,
    same-tick ones as they came."""
    if not ts:
        return [np.zeros(0, np.int64), np.zeros(0, np.uint8), np.zeros(0, np.int32), np.zeros(0, np.int32)]
    arrays = [np.concatenate(a) for a in (ts, ks, vs, ss)]
    if len(ts) > 1:
        order = np.argsort(arrays[0], kind="stable")
        arrays = [a[order] for a in arrays]
    return arrays


def file_kinds(codes):
    """A loaded MIDI's CC event codes (files/midi_index: status | data 1 << 8 | data 2 << 16) -> their kind numbers
    (midi_index CC_KINDS order: controller number, then 128 instrument, 129 bend, 130 pressure, 131 key pressure)."""
    hi = codes & 0xF0
    return np.where(hi == 0xB0, codes >> 8 & 127, np.select([hi == 0xC0, hi == 0xE0, hi == 0xD0], [128, 129, 130], 131))


def kind_number(kind):
    """cc_names' kind ("cc", n) / "bend"... as file_kinds' number."""
    return kind[1] if isinstance(kind, tuple) else 128 + ("program", "bend", "pressure", "poly").index(kind)


def same_merged(items):
    """One tick's CC marks [(text, tip, end, (track, channel))]: the same text + end on several tracks = once, "Volume
    127 (2,000 tracks)" (user 2026-10-29: songs set every track up at the start), on several channels of one track =
    "Volume 127 (16 channels)", the very same one twice (one track + channel) = once (hunt 2026-10-29). Returns
    [(text, tip, end)] in first-met order."""
    groups = {}
    for text, tip, end, where in items:
        groups.setdefault((text, end), {}).setdefault(where, tip)
    out = []
    for (text, end), got in groups.items():
        tracks = len({t for t, _ in got})
        if tracks > 1:
            n = f"{tracks:,}"
            out.append((tr("cc.on_tracks", text=text, n=n), tr("cc.tip_tracks", text=text, n=n), end))
        elif len(got) > 1:
            n = f"{len(got):,}"
            out.append((tr("cc.on_channels", text=text, n=n), tr("cc.tip_channels", text=text, n=n), end))
        else:
            out.append((text, next(iter(got.values())), end))
    return out
CUT_MARK = 12  # px each side of a sliced piece's cut end: its stretch of the Slice line (draw_cut_marks)
FAINT_CUT = look.FAINT_CUT
MASK_DASH = (8, 4)  # a mask's line (mask.py)


def piece_runs(x0, y0, x1, y1):
    """Straight pieces (lists of their ends) joined where one's end is another's: runs of (piece, forward), each
    run one line (a closed loop starts and ends on the same spot)."""
    def spot(x, y):
        return round(x, 3), round(y, 3)
    at = {}
    for i in range(len(x0)):
        at.setdefault(spot(x0[i], y0[i]), []).append(i)
        at.setdefault(spot(x1[i], y1[i]), []).append(i)
    used = [False] * len(x0)

    def follow(i, fwd):
        """From piece i's far end onward (fwd: going from its start to its end)."""
        out = []
        while True:
            end = spot(x1[i], y1[i]) if fwd else spot(x0[i], y0[i])
            nxt = next((j for j in at[end] if not used[j]), None)
            if nxt is None:
                return out
            used[nxt] = True
            fwd = spot(x0[nxt], y0[nxt]) == end
            out.append((nxt, fwd))
            i = nxt
    runs = []
    for i in range(len(x0)):
        if not used[i]:
            used[i] = True
            ahead = follow(i, True)
            back = follow(i, False)
            runs.append([(j, not f) for j, f in reversed(back)] + [(i, True)] + ahead)
    return runs


def ring_parts(notes, gap, cols=None):
    """
    The outline round the selected shapes' notes (start, end, key), small gaps in a row (< gap ticks) closed:
    (sides, tops). sides = (tick, key0, key1, right): one side line over keys key0..key1. tops = (t0, t1, key, up):
    the top (up = 1) or bottom (0) edge of a key row's notes where the row beside it has none.
    cols = (starts, ends, keys) instead of notes (no copy of millions of notes).
    """
    s, e, k = ((np.asarray(c, np.int64) for c in cols) if cols is not None else
               (notes[:, i].astype(np.int64) for i in (0, 1, 2)))
    big = int(e.max()) + int(gap) + 2
    fast = loops()
    if fast:  # (the compiled loop: the same runs; a 5M-note shape zoomed out ~350 -> ~70 ms a zoom step)
        s, e, k = (np.ascontiguousarray(v) for v in (s, e, k))
        rk, ra, rb = fast.ring_runs(s, e, k, key_sort(k, s), float(gap), big)
    else:
        order = np.lexsort((s, k))
        s, e = s[order] + k[order] * big, e[order] + k[order] * big
        reach = np.maximum.accumulate(e)
        new = np.ones(len(s), bool)
        new[1:] = s[1:] > reach[:-1] + gap
        first = np.flatnonzero(new)
        last = np.append(first[1:], len(s)) - 1
        rk = s[first] // big
        ra, rb = s[first] - rk * big, reach[last] - rk * big  # runs: key, start, end
    # sides: one per run end, joined over neighbouring keys at the same tick
    sides = []
    for t, right in ((ra, 0), (rb, 1)):
        o = np.lexsort((rk, t))
        tt, kk = t[o], rk[o]
        start = np.ones(len(tt), bool)
        start[1:] = (tt[1:] != tt[:-1]) | (kk[1:] != kk[:-1] + 1)
        f = np.flatnonzero(start)
        l = np.append(f[1:], len(tt)) - 1
        sides.append(np.column_stack([tt[f], kk[f], kk[l], np.full(len(f), right)]))
    # tops / bottoms: a row's runs minus the next row's (events +1 / -1 for this row, +2 / -2 for the other)
    tops = []
    for up, dk in ((1, 1), (0, -1)):
        kk = np.concatenate([rk, rk, rk - dk, rk - dk])
        t = np.concatenate([ra, rb, ra, rb])
        v = np.concatenate([np.ones_like(ra), -np.ones_like(ra), np.full_like(ra, 2), np.full_like(ra, -2)])
        o = np.lexsort((t, kk))
        kk, t, v = kk[o], t[o], np.cumsum(v[o])
        on = np.flatnonzero((v[:-1] == 1) & (t[1:] > t[:-1]) & (kk[1:] == kk[:-1]))
        tops.append(np.column_stack([t[on], t[on + 1], kk[on], np.full(len(on), up)]))
    return np.concatenate(sides), np.concatenate(tops)


def ring_chains(xa, ya, xb, yb, up, extra):
    """Upright (up) / flat pieces from (xa, ya) to (xb, yb), both ends drawn, joined where an end is another
    piece's end or one pixel diagonally beside it (an inner corner of a staircase; a one-pixel diagonal step draws
    no pixel of its own): coordinate lists for create_line, the last point pushed on by extra (a line leaves out
    its last pixel), so the same pixels as one line per piece reaching extra past its end."""
    ends = {}
    for i, (p, q) in enumerate(zip(zip(xa, ya), zip(xb, yb))):
        ends.setdefault(p, []).append(i)
        ends.setdefault(q, []).append(i)
    used = [False] * len(xa)

    def onward(at):  # an unused piece with an end at this point, else at a diagonal neighbour: (piece, its end)
        for dx, dy in ((0, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            p = (at[0] + dx, at[1] + dy)
            for j in ends.get(p, ()):
                if not used[j]:
                    return j, p
        return None, None

    def walk(i, at):  # [(piece, from, to)]: from piece i's end at, on while another piece goes on from where it ends
        out = []
        while True:
            used[i] = True
            p, q = (xa[i], ya[i]), (xb[i], yb[i])
            nxt = q if at == p else p
            out.append((i, at, nxt))
            i, at = onward(nxt)
            if i is None:
                return out

    lines = []
    for i in range(len(xa)):
        if used[i]:
            continue
        p = (xa[i], ya[i])
        ahead = walk(i, p)
        j, at = onward(p)
        back = walk(j, at) if j is not None else []
        pts = []
        for _, a, b in reversed(back):
            pts += (b, a)
        for _, a, b in ahead:
            pts += (a, b)
        last, a, (lx, ly) = ahead[-1]
        # the last piece's direction (a one-pixel piece: along its own kind)
        dx, dy = (lx > a[0]) - (lx < a[0]), (ly > a[1]) - (ly < a[1])
        if (dx, dy) == (0, 0):
            dx, dy = (0, 1) if up[last] else (1, 0)
        pts[-1] = (lx + dx * extra, ly + dy * extra)
        # (points repeated where pieces meet: dropped)
        pts = [pt for n, pt in enumerate(pts) if not n or pt != pts[n - 1]]
        lines.append([v for pt in pts for v in pt])
    return lines


NOTE_RGB = np.array([[[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pair] for pair in NOTE_COLORS], np.uint8)
# placed pictures' notes: 16 more colour groups after these, the picture's own colours (no paler shades: the colour IS
# the picture; the outline a darker shade of it). note_tables() adds them for the colours in use.
PICTURE_GROUP = len(NOTE_COLORS) // 32
# then the ghosts (notes a mask hides): one more group per group above, its full colour (fill + outline) faded toward
# the background to GHOST_FADE[0] % (the toolbar's box by Mask preview; 0 = not drawn at all: mask_view)
GHOST_BASE = PICTURE_GROUP + 16
GHOST_FADE = [20]
# (shades 0..30 of each ghost group: tracks shown behind the highlighted one, velocity -2 - v, each velocity's
# shade faded to TRACK_FADE[0] %: the track list's Faint box; user 2026-10-23: they kept their velocity look)
TRACK_FADE = [25]
_tables = {}


def ghost_code(group, vel=-1):
    """The colour number a ghost of a note in this colour group is painted with (vel: its -1 mask ghost or
    -2 - velocity of a track behind)."""
    shade = np.where(vel == -1, 31, np.minimum(30, (-np.asarray(vel) - 2) // 4))
    return (GHOST_BASE + group) * 32 + shade


def note_tables(pal):
    """(NOTE_COLORS, NOTE_RGB) with the picture colours pal ("RRGGBB" list or None) added as groups
    PICTURE_GROUP.. (grey where there's none), then every group's ghost colours (GHOST_BASE..)."""
    key = tuple(pal or ()), GHOST_FADE[0], TRACK_FADE[0]
    got = _tables.get(key)
    if got is None:
        extra = []
        for k in range(16):
            h = pal[k] if pal and k < len(pal) else "C0C0C0"
            rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
            dark = "#%02x%02x%02x" % tuple(int(c * 0.6) for c in rgb)
            extra += [("#" + h.lower(), dark)] * 32
        away, behind = 1 - GHOST_FADE[0] / 100, 1 - TRACK_FADE[0] / 100
        groups = NOTE_COLORS + extra
        extra += [(fade(f, a), fade(b, a)) for g in range(0, len(groups), 32)
                  for (f, b), a in zip(groups[g:g + 31] + [groups[g + 31]], [behind] * 31 + [away])]
        colors = NOTE_COLORS + extra
        rgb = np.concatenate([NOTE_RGB, np.array([[[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pair]
                                                  for pair in extra], np.uint8)])
        got = _tables[key] = (colors, rgb)
        if len(_tables) > 20:
            _tables.pop(next(iter(_tables)))
    return got


SHORT_PATH = 32  # paths of at most this many points: screen_lines in plain Python (NumPy's fixed cost per call
# was most of a redraw with thousands of small shapes, speed hunt 2026-10-29)


def _pixel(v):
    """np.round(v) as screen_lines does it (halves to even, -0.0 kept), as a float."""
    r = float(round(v))
    return -0.0 if r == 0 and math.copysign(1.0, v) < 0 else r


def short_screen_lines(pts, ax, bx, ay, by, view):
    """screen_lines for a short [[beat, pitch]] list: the same steps one point at a time, the same answer."""
    x0, y0, x1, y1 = view
    xs, ys = [b * ax + bx for b, _ in pts], [p * ay + by for _, p in pts]
    out, n, k = [], len(pts), 0
    while k < n - 1:
        xa, xb, ya, yb = xs[k], xs[k + 1], ys[k], ys[k + 1]
        if (xa < x0 and xb < x0) or (xa > x1 and xb > x1) or (ya < y0 and yb < y0) or (ya > y1 and yb > y1):
            k += 1
            continue
        first = k  # (a piece: segments first .. k on screen, points first .. k + 1)
        while k + 1 < n - 1:
            xa, xb, ya, yb = xs[k + 1], xs[k + 2], ys[k + 1], ys[k + 2]
            if (xa < x0 and xb < x0) or (xa > x1 and xb > x1) or (ya < y0 and yb < y0) or (ya > y1 and yb > y1):
                break
            k += 1
        idx = range(first, k + 2)
        k += 1
        keep, run = [], []
        for i in idx:  # (each pixel column's run: its first, first lowest, first highest and last point)
            if run and math.trunc(xs[i]) != math.trunc(xs[run[0]]):
                keep.append(run)
                run = []
            run.append(i)
        keep.append(run)
        kept = []
        for run in keep:
            lo = min(run, key=lambda i: (ys[i], i))
            hi = min(run, key=lambda i: (-ys[i], i))
            kept += sorted({run[0], lo, hi, run[-1]})
        far = any(xs[i] < -999 or xs[i] > 30999 or ys[i] < -999 or ys[i] > 30999 for i in kept)
        coords, last = [], None
        for m, i in enumerate(kept):
            rx, ry = ((v if far or abs(abs(v - math.trunc(v)) - 0.5) < 1e-6 else _pixel(v)) for v in (xs[i], ys[i]))
            if last == (rx, ry) and m < len(kept) - 1:
                continue
            coords += (rx, ry)
            last = (rx, ry)
        if len(kept) >= 2:
            out.append((coords, len(kept)))
    return out


def screen_lines(path, ax, bx, ay, by, view):
    """
    A (beat, pitch) path as screen coordinate lists for create_line (x = beat * ax + bx, y = pitch * ay + by).
    Only what can show goes to the canvas, which paints tens of thousands of points very slowly (a line with
    tumours a tick apart has that many): parts outside view = (x0, y0, x1, y1) are left out (the line is split
    there), and of the points in one pixel column only the first, lowest, highest and last are kept (a dense
    zigzag looks just the same). Returns (coords, how many points the piece had before repeats were dropped).
    The points are whole pixels where the canvas would round them so anyway (it does: round to nearest, before
    drawing), then a point on the same pixel as the one before is left out: the same pixels, much less to send
    (user's example: 3x quicker). Kept as they are: a point within a hair of half a pixel (the canvas rounds those
    by where it's repainting) and pieces reaching past the canvas's own cut-off box (-1000 .. 31000 px: it cuts
    the line there from the exact points).
    """
    x0, y0, x1, y1 = view
    pts = np.asarray(path, float).reshape(-1, 2)
    if len(pts) <= SHORT_PATH:
        return short_screen_lines(pts.tolist(), ax, bx, ay, by, view)
    x, y = pts[:, 0] * ax + bx, pts[:, 1] * ay + by
    xa, xb, ya, yb = x[:-1], x[1:], y[:-1], y[1:]
    on = ~(((xa < x0) & (xb < x0)) | ((xa > x1) & (xb > x1)) | ((ya < y0) & (yb < y0)) | ((ya > y1) & (yb > y1)))
    # pieces: runs of on-screen segments (segment k runs from point k to k + 1)
    edge = np.diff(np.concatenate([[False], on, [False]]).astype(np.int8))
    first, last = np.nonzero(edge == 1)[0], np.nonzero(edge == -1)[0]  # points first .. last of each piece
    if not len(first):
        return []
    size = last - first + 1
    idx = np.repeat(first, size) + np.arange(int(size.sum())) - np.repeat(np.cumsum(size) - size, size)
    piece = np.repeat(np.arange(len(first)), size)
    x, y = x[idx], y[idx]
    col = np.trunc(x)
    new = np.ones(len(idx), bool)
    new[1:] = (piece[1:] != piece[:-1]) | (col[1:] != col[:-1])
    at = np.nonzero(new)[0]  # each pixel column's run of points
    group = np.cumsum(new) - 1
    n = np.arange(len(idx))
    keep = np.zeros(len(idx), bool)
    keep[at] = True
    keep[np.append(at[1:], len(idx)) - 1] = True
    for pick in (np.minimum, np.maximum):  # the first lowest and first highest point of each run
        best = pick.reduceat(y, at)
        keep[np.minimum.reduceat(np.where(y == best[group], n, len(idx)), at)] = True
    x, y, piece = x[keep], y[keep], piece[keep]
    sizes = np.bincount(piece, minlength=len(first))
    far = np.bincount(piece, (x < -999) | (x > 30999) | (y < -999) | (y > 30999), len(first))[piece] > 0
    rx, ry = (np.where(far | (np.abs(np.abs(v - np.trunc(v)) - 0.5) < 1e-6), v, np.round(v)) for v in (x, y))
    same = np.zeros(len(rx), bool)
    same[1:] = (rx[1:] == rx[:-1]) & (ry[1:] == ry[:-1]) & (piece[1:] == piece[:-1])
    same[np.cumsum(sizes) - 1] = False  # (a piece's last point stays: at least 2 points)
    counts = np.bincount(piece[~same], minlength=len(first)) * 2
    coords = np.column_stack([rx, ry])[~same].ravel().tolist()
    out, k = [], 0
    for c, size in zip(counts.tolist(), sizes.tolist()):
        if size >= 2:
            out.append((coords[k:k + c], size))
        k += c
    return out


def cover_next(s, e, key, group):
    """For each note, the next one painted after it on its key in its group (an owner): its start and end (-1 = none).
    Notes come in painting order within each group."""
    n = len(s)
    nxt_s, nxt_e = np.zeros(n, np.int64), np.full(n, -1, np.int64)
    if n > 1:
        order = np.lexsort((np.arange(n), key, group))
        same = (group[order][1:] == group[order][:-1]) & (key[order][1:] == key[order][:-1])
        a, b = order[:-1][same], order[1:][same]
        nxt_s[a], nxt_e[a] = s[b], e[b]
    return nxt_s, nxt_e


NO_END = 1 << 30  # (a note's colour number + this: its end was cut short by covered_ends, not a real end;
# fastloops.NO_END)


def row_overlap(rects):
    """Two notes on one key row overlapping on screen (note_rects' rectangles)? Then a note's start / end can be
    under another's fill, shaded (user 2026-10-25): only the picture does that, canvas rectangles can't."""
    x0, y0, x1 = rects[0], rects[1], rects[2]
    if len(x0) < 2:
        return False
    o = np.lexsort((x0, y0))
    return bool(((y0[o][1:] == y0[o][:-1]) & (x0[o][1:] <= x1[o][:-1])).any())


def covered_ends(e, nxt, room):
    """Ends to PAINT (Sustain's stacked notes: sustain.py): a note the next one on its key covers to its end (painted
    after it, ending no earlier) only needs painting to `room` ticks past that one's start, where its own right edge
    is hidden under it: the same pixels, a fraction of the work (a long held bar costs every pixel it's long)."""
    nxt_s, nxt_e = nxt
    return np.where(nxt_e >= e, np.minimum(e, nxt_s + room), e)


STACK_MODES = ("track", "loud", "shape")  # Settings → Look "Stacked notes" (user 2026-10-31; first = default)


def stack_rule(app):
    """(mode, own tracks' (id, after)): what decides which of the notes lying on top of each other shows. "track" =
    the later track (like Domino: lower in the track list = on top), "loud" = the louder note (equal: the later
    track), "shape" = the later shape. Selected shapes on top in all; tracks shown faint behind the highlighted one
    under everything."""
    var = getattr(app, "stack_order", None)
    mode = var.get() if var is not None else STACK_MODES[0]
    return (mode if mode in STACK_MODES else STACK_MODES[0],) + list_order(app)


def list_order(app):
    """(own, files, emptied): what the track list's order goes by, as track_ranks takes it (your tracks' (id,
    after, file), the loaded MIDI's tracks as listed, the automatic tracks Remove overlaps emptied)."""
    own = tuple((t["id"], t["after"], t.get("file")) for t in getattr(app, "own_tracks", None) or ())
    rows = getattr(app, "file_rows", None)
    return own, tuple(rows()) if rows else (), tuple(sorted(getattr(app, "cut_emptied", ()) or ()))


def track_ranks(slots, own, files=(), emptied=()):
    """Each note's track place in the track list / export and how many places there are. A track's channels share
    its place. own = (id, after) or (id, after, file) of your tracks (stack_rule's; file = its place among a loaded
    MIDI's tracks, None = after them); files = the loaded MIDI's track numbers as listed (App.file_rows: own_tracks.
    full_plan's order, so shapes drawn into its tracks rank where the list shows them, hunt 2026-11-06); emptied =
    automatic tracks counted as having notes (App.cut_emptied: Remove overlaps took theirs, nothing moves)."""
    from files.own_tracks import FILE0, full_plan, key_track
    from notes.engine import OWN0
    slots = np.asarray(slots, np.int64)
    auto = slots < OWN0
    autos = int(slots[auto].max()) + 1 if auto.any() else 0
    autos = max([autos] + [int(s) + 1 for s in emptied])
    filled = np.flatnonzero(np.bincount(slots[auto], minlength=autos)) if autos else np.zeros(0, np.int64)
    if len(emptied):
        filled = np.union1d(filled, np.asarray(sorted(emptied), np.int64))
    ids = np.unique((slots[~auto] - OWN0) >> 4) if not auto.all() else np.zeros(0, np.int64)
    mine = [dict({"id": t[0], "after": t[1]}, **({"file": t[2]} if len(t) > 2 and t[2] is not None else {}))
            for t in own]
    rows = full_plan(list(files), range(autos), mine, filled)
    at_auto, at_own = np.zeros(autos, np.int64), {}
    for k, r in enumerate(rows):
        if isinstance(r, dict):
            at_own.setdefault(r["id"], k)
        elif r < 0:  # (a loaded MIDI's track (or its part): shapes drawn into it have id FILE0 + its number)
            at_own.setdefault(FILE0 + key_track(-1 - r), k)
        else:
            at_auto[r] = k
    out = np.empty(len(slots), np.int64)
    out[auto] = at_auto[slots[auto]]
    if len(ids):
        place = np.array([at_own.get(int(i), len(rows) + j) for j, i in enumerate(ids)], np.int64)
        out[~auto] = place[np.searchsorted(ids, (slots[~auto] - OWN0) >> 4)]
    return out, len(rows) + len(ids)


def stack_keys(notes, sels, back, rule):
    """Each note's painting key (bigger = painted later = on top) by stack_rule's rule, and the key where the
    selected shapes' notes start (all below it are the rest)."""
    owner = notes[:, 5]
    group = np.ones(len(notes), np.int64)  # 0 = faint tracks behind the highlighted one, 1 = the rest, 2 = selected
    if sels:
        group[np.isin(owner, list(sels))] = 2
    if back is not None:
        group[(owner == back) & (group == 1)] = 0
    mode = rule[0]
    if mode == "shape":
        span = int(owner.max()) + 1 if len(owner) else 1
        return group * span + owner, 2 * span
    place, span = track_ranks(notes[:, 4], *rule[1:])
    if mode == "loud":  # (-1 = a mask's ghost: quietest; -2 - velocity = a faint track's note)
        v = notes[:, 3]
        v = np.where(v <= -2, -2 - v, np.clip(v, 0, 127))
        place = v * span + place
        span *= 128
    return group * span + place, 2 * span


def painting_order(fast, rendered, start, sels, pics, keys, use10, back=None, rule=(STACK_MODES[0], ())):
    """RollDrawing.paint_order's work (start = the notes' rows by start; use10: channel 10 used, slot_colours).
    back: the owner number of the tracks shown behind the highlighted one (App.track_view), painted first.
    rule: stack_rule's. Touches no Tk: also done in a background thread for notes made there (App.notes_rested)."""
    if not (len(rendered) and rendered[:, 2].min() >= 0 and rendered[:, 2].max() < keys and rendered[:, 5].min() >= 0):
        return None
    key, sel_from = stack_keys(rendered, sels, back, rule)
    used = np.zeros(int(key.max()) + 1, bool)  # (keys made into ranks 0, 1, 2 ... in the same order)
    used[key] = True
    rank = np.cumsum(used) - 1
    others = int(used[:sel_from].sum())
    made = fast.paint_order(np.ascontiguousarray(rendered, np.int64), start, rank[key], int(used.sum()),
                            pics if pics.any() else np.zeros(0, bool), slot_colours(use10), PICTURE_GROUP, GHOST_BASE)
    return made, others


class RollDrawing:
    """Mixed into PianoRoll."""

    def request_redraw(self, delay=None):
        """Redraw once the program is idle; delay (ms): not sooner than that (while drawing freehand, so the
        mouse keeps being read instead of the whole roll being painted after every move)."""
        if delay:
            if not self._redraw_pending and not self._late_redraw:
                self._late_redraw = self.after(delay, self._redraw_late)
            return
        if self._late_redraw:
            self.after_cancel(self._late_redraw)
            self._late_redraw = None
        if not self._redraw_pending:
            self._redraw_pending = True
            self.after_idle(self.redraw)
        for name in ("vel", "tempo_pane"):
            pane = getattr(self.app, name, None)
            if pane:
                pane.request_redraw()  # same notes, same time axis

    def _redraw_late(self):
        self._late_redraw = None
        self.request_redraw()

    def draft_moved(self):
        """The shape being drawn / placed changed with the mouse: redrawn. When making and painting its notes is slow,
        only its lines follow the mouse (the other notes' picture stays): its notes show once the mouse rests."""
        if self._draft_rest:
            self.after_cancel(self._draft_rest)
            self._draft_rest = None
        self.draft_moving = self._draft_time + self.paint_time > 0.15  # (as for other drags: app.shapes_changed)
        if self.draft_moving:
            self._draft_rest = self.after(250, self._draft_rested)
        self.request_redraw()

    def _draft_rested(self):
        self._draft_rest = None
        self.draft_moving = False
        self.request_redraw()
        if self.draft and self.draft["kind"] == "custom":
            self.after_idle(self.show_draft)  # (its note count)

    def draft_notes(self):
        """The notes of the shape being drawn / placed (made once for each spot it's at), or None: none, too many to
        preview (only its lines show), or slow to make while the mouse moves (draft_moved)."""
        d, app = self.draft, self.app
        if not d or self.draft_moving:
            return None
        key = (shape_key(d), app.ppq, app.keys)  # (app.notes_tracks' key: kept for it, keep_draft)
        if not self._draft_made or self._draft_made[0] != key:
            # (the count is timed too: for a filled shape it does most of the work, the notes then reuse it)
            started = time.perf_counter()
            n = (custom_note_count(d, app.ppq) if d["kind"] == "custom" else
                 funnel_note_count(d, app.ppq) if d["kind"] == "funnel" else None)
            made = None if (n or 0) > PREVIEW_LIMIT else shape_notes_tracks(d, app.ppq, app.keys)
            self._draft_made = (key, made, n)
            self._draft_time = time.perf_counter() - started
        made = self._draft_made[1]
        return None if made is None else made[0]

    def draft_count(self):
        """How many notes the shape being drawn / placed makes, or None while they're still to be made (also while
        only its lines follow the mouse: the count shows when it rests, draft_moved)."""
        if self.draft_moving:
            return None
        notes = self.draft_notes()  # (made for the draft as it is now)
        if not self._draft_made:
            return None
        n = self._draft_made[2]
        return len(notes) if n is None and notes is not None else n

    def show_draft(self, text=None):
        """The status line while placing: where the mouse is (text; None: as last time) and how many notes the new
        shape makes, once they're known."""
        text = self._draft_text = self._draft_text if text is None else text
        n = self.draft_count() if self.draft else None
        self.app.show_position(text if n is None else tr("pianoroll.new_shape_notes", text=text,
                                                                   notes=trn("n.notes", n, n=n)))

    def keep_draft(self):
        """The shape being drawn / placed is added: the app takes the notes already made for it."""
        app = self.app
        if (self.draft and self._draft_made and self._draft_made[1] is not None
                and self._draft_made[0] == (shape_key(self.draft), app.ppq, app.keys)):
            app._notes_cache[self._draft_made[0]] = self._draft_made[1]
        self._draft_made = None

    def redraw(self):
        with same_shapes():  # (each shape's lines looked up once a redraw)
            self.redraw_all()

    def redraw_all(self):
        self._redraw_pending = False
        for bar in self.bars:
            bar.refresh()
        # the grid, keyboard + ruler and play line stay when nothing they show changed (e.g. dragging a shape): Tk
        # puts back on screen only the part around what's made again, not the whole piano roll (measured on the
        # user's screen: ~43 ms a mouse move for the whole); the shapes' lines too (keep_lines)
        self.delete("!(grid||frame||playhead||kept)")
        w, h = self.winfo_width(), self.winfo_height()
        if self.sx is None or w < 50:
            self.delete("all")
            self._grid_key = self._frame_key = self._kb_key = None
            self._lines = {}
            return
        # (keyboard and ruler kept apart: a sideways scroll draws only the ruler again)
        picks = self.app.gliss_picks()
        kb_key = (h, self.kb_w, self.ruler_h, self.sy, self.view_top, self.scale, self.app.keys, self.app.edo(),
                  getattr(self, "room_keys", lambda: 0)(), None if picks is None else frozenset(picks), self.held_key)
        frame_key = (w, self.kb_w, self.ruler_h, self.sx, self.view_t, self.scale, json.dumps(sigs_of(self.app.tempo)),
                     json.dumps(self.app.tempo.get("keysigs")), self.track_mark())
        if kb_key != self._kb_key:
            self.delete("kbd", "playhead")
            self._kb_key = None
        if frame_key != self._frame_key:
            self.delete("ruler", "playhead")
            self._frame_key = None
        app = self.app
        rows, cols = self.grid_parts(w, h)
        # the picture below shows exactly this: when nothing here changed (e.g. dragging a shape whose notes catch up
        # later), it's shown again as it is instead of being painted again
        # (the loaded notes a kept box outlines: come in later, a thread; asked now, user 2026-10-26: the box gone
        # while playing, the old job still keyed the picture and its red outline stayed)
        from window.load_extract import box_notes  # (it imports this file)
        ring = box_notes(app)
        boxed = getattr(app, "_box_notes", None) if ring is not None else None
        fixed = (frozenset(app.sels), self.sx, self.sy, self.kb_w, self.ruler_h, w, h,
                 self.loaded_mark(), boxed and (boxed["key"], id(boxed["cols"])))
        pic = (app.drawn, (fixed, self.view_t, self.view_top, tuple(rows), tuple(cols), self.hidden_shade()))
        # (a shape being drawn / placed: its notes are in the picture, unless only its lines show)
        drafted = self.draft_notes() is not None
        if not drafted:
            self._draft_base = None
        old =None if drafted or not app.show_notes.get() or self._tiles is None else self._note_pic
        if self._exact:
            self.after_cancel(self._exact)
            self._exact = None
        moved = self.pan_pixels(old, pic, w, h)
        last, self._last_pic = self._last_pic, pic  # (only the view moved since the last redraw: scrolling)
        scrolled = last is not None and last[0] is pic[0] and last[1][0] == pic[1][0] and last[1][1:3] != pic[1][1:3]
        carried = self.carried()
        if carried is None:
            self._carry = None
            if not (self.drag and self.drag[0] == "move"):  # (a rest: the same picture goes on after it)
                self._carry_pic = None
        # (a drag called off: the notes catch up; a held knob / number box isn't one)
        if app.notes_late and not (self.drag or app.scrubbing) and not app._late_notes:
            app.notes_rested()
        if carried is not None and w > self.kb_w and h > self.ruler_h:
            self.paint_carried(w, h, rows, cols, fixed, *carried)
            self._note_pic = None
        elif old is not None and old[0] is pic[0] and old[1] == pic[1]:
            self._tiles.place(self, int(self.kb_w), int(self.ruler_h))
        elif moved:
            # only the view moved, by whole pixels: the picture is moved along and just the new edge is painted.
            # (A pixel here and there can round the other way, so it's painted whole once the view rests.)
            self.shift_image(w, h, rows, cols, *moved)
            self._note_pic = pic
            self._exact = self.after(300, self.paint_exact)
        else:
            started = time.perf_counter()
            if drafted and app.show_notes.get() and self.paint_drafted(w, h, rows, cols):
                self._note_pic = None
            else:
                rects = self.note_rects(w, h) if app.show_notes.get() else None
                if rects is not None and (len(rects[0]) > w * h // 2000 or self._panning or scrolled
                                          or app.loaded is not None
                                          or ((rects[0] == rects[2]) | (rects[1] == rects[3])).any()
                                          or (self.hidden_shade() and row_overlap(rects))):
                    # Lots of notes: paint grid + notes as one picture (thousands of canvas items redraw slowly).
                    # Panning / scrolling (scrollbar, wheel): always (the picture is moved along, ~12 ms a move;
                    # canvas items drawn again at every move took 35-80 ms on the user's screen with ~1000 notes).
                    # A note 1 px wide or tall: the picture too (a canvas rectangle makes it 2 px; user: 1 px everywhere)
                    # Notes overlapping on a row: the picture (shades where a note under another starts / ends)
                    self.paint_image(w, h, rows, cols, rects)
                    self._note_pic = None if drafted else pic
                else:
                    self._tiles = self._note_pic = self._img = None
                    self.keep_grid(w, h, rows, cols)
                    colors = note_tables(app.picture_pal)[0]
                    for x0, y0, x1, y1, color in zip(*(v.tolist() for v in rects or ())):
                        fill, edge = colors[color & (NO_END - 1)]
                        self.create_rectangle(x0, y0, x1, y1, fill=fill, outline=edge)
            self.paint_time = time.perf_counter() - started
        if self._tiles is not None and self._grid_key:  # (the grid is in the notes' picture)
            self.delete("grid")
            self._grid_key = None
        if carried is None:  # (while shapes are dragged their notes are stamped along: no ring)
            self.draw_ring(w, h)
            self.draw_edge_preview(w, h)
        # the shapes' lines stay on the canvas while they'd be drawn the same (keep_lines): dragging / selecting one
        # shape remade every shape's lines (200 shapes: ~77 ms a drag step, redraw hunt 2026-10-31)
        self.addtag_withtag("under", "!(grid||frame||playhead||kept)")  # (made so far: the notes, ring...: under them)
        lines = self.__dict__.get("_lines")
        if lines is None:  # (none known: any left from before go)
            self.delete("kept")
            lines = {}
        self._lines, order = {}, []
        at = (self.sx, self.sy, self.view_t, self.view_top, self.kb_w, self.ruler_h, w, h)

        def keep(key, sh, draw):
            order.append(self.keep_lines(lines, (key, at, "notes" in sh, sh.get("fill"), bool(sh.get("text"))),
                                         cached_arrays(sh), draw))

        def path(sh, *how, dash=None):
            keep(("path", i) + how + (dash,), sh, lambda tags: self.draw_path(sh, *how, dash=dash, tags=tags))

        # a line with tumours / a curve with a pattern: the line as drawn (the origin path), faint and dashed under it
        hidden = app.hidden_shapes - app.sels  # (on tracks the track list hides: no lines either, user 2026-10-23)
        for i, sh in enumerate(app.shapes):
            if i in hidden:
                continue
            if ((sh.get("pattern") or sh.get("shape") or any(tm["on"] for tm in all_tumours(sh)))
                    and (i in app.sels or app.show_lines.get())):
                path(dict(sh, tumour=None, tumours=None, pattern=None, shape=None),
                     look.ORIGIN_PICKED if i in app.sels else look.ORIGIN, 1, dash=(6, 4))
            if sh["kind"] in ("funnel", "custom") and (i in app.sels or app.show_lines.get()):  # its curves' / strokes'
                keep(("origins", i, look.ORIGIN_PICKED if i in app.sels else look.ORIGIN), sh,
                     lambda tags, i=i, sh=sh: self.draw_origins(sh, i in app.sels, tags))
        covered = app.mask_covered
        units = mask_units(app.shapes)

        def mask_colour(i, picked):  # (a group's own colour, user; ungrouped = the plain mask colour)
            u = unit_of(units, i)
            if len(u) > 1:
                return look.MASK_GROUPS[app.shapes[i]["mask"].get("colour", 0) % len(look.MASK_GROUPS)][picked]
            return look.MASK_LINE_PICKED if picked else look.MASK_LINE

        # holes worked out together with a selected one (mask.worked_units): in their picked colour (user 2026-10-16)
        partners = {i for u in (mask_joined() if app.mask_preview.get() else ()) if set(u) & set(app.sels)
                    for i in u if i < len(app.shapes) and app.shapes[i].get("mask")}
        for i, sh in enumerate(app.shapes):  # (Settings → Look's Shape lines: their thickness, a selected one's a pixel more)
            if i in app.sels or i in hidden:
                continue
            if sh.get("mask"):  # a mask: always shown, dashed (so it isn't forgotten, mask.py)
                path(sh, mask_colour(i, i in partners), look.LINES + (i in partners), dash=MASK_DASH)
            elif app.show_lines.get():
                path(sh, look.MASKED_LINE if i in covered else look.SHAPE_LINE, look.LINES)
        for i in app.sels:
            sh = app.shapes[i]
            if sh.get("mask"):  # (two pixels more: a selected mask was hard to see, user)
                path(sh, mask_colour(i, True), look.LINES + 2, dash=MASK_DASH)
            else:
                path(sh, look.SHAPE_LINE_PICKED, look.LINES + 1)
        for _, tag, _ in lines.values():  # (lines no longer shown / drawn otherwise now)
            self.delete(tag)
        self.stack_lines(order)
        for i in app.sels:
            if i < len(app.shapes) and "picture" in app.shapes[i]:
                self.draw_picture_label(app.shapes[i])
        self.draw_cut_marks()
        sel = self.point_shape()
        if sel and app.parts:
            self.draw_parts()
        if sel and sel["kind"] == "custom":
            self.draw_picked_stroke(sel)
        if sel:
            if sel["kind"] == "custom" and app.tool.get() != "text":
                self.draw_custom_box(sel)
            self.draw_handles(sel)  # on top: a stroke's point wins over the box's squares
            if sel["kind"] == "gliss":
                self.draw_gliss(sel)  # (its thickness handle + outermost copy: roll_gliss)
        if self.draft:
            self.draw_path(self.draft, look.DRAFT_LINE, look.LINES + 1)
            self.draw_draft_points()
        self.draw_hz_start()
        self.draw_above_marks()
        self.draw_select_box()
        self.draw_slice()
        self.draw_lasso()
        self.draw_copy_path()
        self.draw_centre_dots()
        self.draw_melody_lit()
        self.draw_sustain_stops()
        if self._kb_key is None or self._frame_key is None:
            if self._kb_key is None:
                self.draw_keyboard(h)
                self._kb_key = kb_key
            if self._frame_key is None:
                self.draw_ruler(w)
                self._frame_key = frame_key
            elif self.find_withtag("ruler"):  # (the keys redrawn alone: under the kept ruler, as in a full redraw)
                self.tag_lower("kbd", "ruler")
            self.addtag_withtag("made", "!(grid||frame||playhead||kept)")  # (the shapes made now: under the kept part)
            self.tag_lower("made", "frame")
            self.dtag("made")
            self.draw_playhead()
        else:  # (what was made now goes under the kept keyboard + ruler, in its own order)
            self.addtag_withtag("made", "!(grid||frame||playhead||kept)")
            self.tag_lower("made", "frame")
            self.dtag("made")
            self.draw_playhead(keep=True)
        if self._line_order:  # (the notes' picture, ring...: under the shapes' kept lines)
            self.tag_lower("under", "kept")
        self.dtag("under")
        self.tag_raise("rulerpick")  # (over the ruler, under the play line)
        self.tag_raise("tempomark")
        self.tag_raise("ccmark")  # (the CC row: a whole ruler drawn again went over it, hunt 2026-10-29)
        self.tag_raise("trackmark")  # (the highlighted track's name: over the corner's patch the tempo marks bring)
        self.tag_raise("playhead")
        if self.typing:
            self.show_caret()

    def keep_grid(self, w, h, rows, cols):
        """The grid as canvas items: the ones already there stay when it's the same grid (redraw)."""
        grid_key = (w, h, self.kb_w, self.ruler_h, tuple(rows), tuple(cols), getattr(self, "dark_x", None))
        if grid_key != self._grid_key:
            self.delete("grid")
            self.draw_grid(w, h, rows, cols)
            self.tag_lower("grid")  # (under the keyboard + ruler kept above)
            self._grid_key = grid_key

    def draw_picture_label(self, sh):
        """A selected placed picture: its name, keys and colours above its box's top left corner (only while
        selected, user)."""
        (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
        xs = [self.t2x(b) for b in (b0, b1, b2, b1 + b2 - b0)]
        ys = [self.p2y(p) for p in (p0, p1, p2, p1 + p2 - p0)]
        p = sh["picture"]
        keys, colours = p["grid"][1], len(p["set"].get("pal", ()))
        text = tr("image.label", name=sh.get("name") or "?", keys=trn("n.keys", keys, n=keys),
                  colours=trn("n.colours", colours, n=colours))
        x, y = min(xs), min(ys) - 4
        t = self.create_text(x + 5, y - 9, text=text, anchor="w", font=look.font(9, family=look.TK))
        bx = self.bbox(t)
        if bx:
            # kept inside the piano roll (user): not under the ruler / keyboard when the box is at the edge
            dx = max(0, self.kb_w + 8 - bx[0])
            dy = max(0, self.ruler_h + 4 - bx[1])
            if dx or dy:
                self.move(t, dx, dy)
                bx = (bx[0] + dx, bx[1] + dy, bx[2] + dx, bx[3] + dy)
            r = self.create_rectangle(bx[0] - 4, bx[1] - 2, bx[2] + 4, bx[3] + 2, fill=look.LABEL_BG, outline=look.LABEL_EDGE)
            self.tag_lower(r, t)

    def ring_now(self):
        """The red line round the selected shapes' notes (user: the notes keep their colours, even with short
        gates): ring_parts, worked out once per selection / notes / zoom, or None. Only for the notes from a screen
        before the view to a screen after it (a shape of millions of notes zoomed in: millions of pieces, ~1 s each
        zoom step and every pan step slow), made again when the view gets near that stretch's ends."""
        app = self.app
        from window.load_extract import box_notes  # (it imports this file)
        boxed = box_notes(app)  # (a kept box over a loaded MIDI: the loaded notes Delete would take, group D)
        if not (app.sels and len(app.drawn) or boxed is not None) or not app.show_notes.get():
            return None
        ppq, ax = app.ppq, self.sx / app.ppq
        key = (frozenset(app.sels), self.sx, ppq, id(boxed))
        span = max(1.0, self.winfo_width() - self.kb_w) / ax  # (the view's width in ticks)
        va = self.view_t * ppq
        pad = 16 / ax  # (the ring's pixels reach a little past its notes' ends)
        got = self._ring
        if (got is None or got[0] is not app.drawn or got[1] != key or va - pad < got[3][0]
                or va + span + pad > got[3][1]):
            fast = loops()
            kept = self.paint_order(fast) if fast and app.sels else None
            if kept is not None:  # (the selected shapes' notes are already together there, last)
                (s, e, k, _, first, _), others = kept
                s, e, k = s[first[others]:], e[first[others]:], k[first[others]:]
            else:
                notes = app.drawn[np.isin(app.drawn[:, 5], list(app.sels))] if app.sels else app.drawn[:0]
                s, e, k = notes[:, 0], notes[:, 1], notes[:, 2]
            if boxed is not None:
                s, e, k = (np.concatenate([a, b]) for a, b in zip((s, e, k), boxed))
            wa, wb = math.floor(va - span), math.ceil(va + 2 * span)
            near = (e > wa) & (s < wb)
            if near.all():  # (all of it: good wherever the view goes)
                cols, wa, wb = (s, e, k), -math.inf, math.inf
            else:  # (cut at the stretch's ends: the side lines there are a screen away from the view)
                cols = (np.maximum(s[near], wa), np.minimum(e[near], wb), k[near])
            parts = ring_parts(None, RING_GAP / ax, cols) if len(cols[0]) else None
            got = self._ring = (app.drawn, key, parts, (wa, wb))
        return got[2]

    def draw_ring(self, w, h):
        """The ring as lines on the canvas, when the notes are canvas rectangles (in the notes' picture it's painted
        with them: ring_pixels)."""
        parts = self.ring_now()
        if parts is None or self._tiles is not None:
            return
        # on the notes' edge pixels, growing inward (user); a wider line as 1 px lines side by side (joined lines
        # are drawn with whole pixels only that way)
        for d in range(max(1, round(self.scale))):  # (user: 2 px was too thick)
            self.draw_ring_lines(parts, (self.kb_w, self.ruler_h, w, h), -d, 1, fill=RING_COLOR, width=1)

    def ring_pixels(self, region):
        """The ring's pixels inside region = (x0, y0, x1, y1) (ends not included): where = row * the region's width
        + column, the same pixels draw_ring's lines cover. None if there's no ring."""
        parts = self.ring_now()
        if parts is None:
            return None
        kb, top, w, h = region
        iw = w - kb
        at = []
        for d in range(max(1, round(self.scale))):
            sx, sy0, sy1, tx0, tx1, ty = self.ring_screen(parts, -d, 0)  # (ends included)
            for x0, x1, y0, y1 in ((sx, sx, np.maximum(sy0, top), np.minimum(sy1, h - 1)),
                                   (np.maximum(tx0, kb), np.minimum(tx1, w - 1), ty, ty)):
                on = (x1 >= x0) & (y1 >= y0) & (x0 >= kb) & (x1 < w) & (y0 >= top) & (y1 < h)
                x0, x1, y0, y1 = (v[on].astype(np.int64) for v in (x0, x1, y0, y1))
                n = (x1 - x0) + (y1 - y0) + 1  # (each piece is upright or flat)
                step = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
                flat = np.repeat(x1 > x0, n)
                x, y = np.repeat(x0, n) + step * flat, np.repeat(y0, n) + step * ~flat
                at.append((y - top) * iw + (x - kb))
        return np.concatenate(at)

    def ring_screen(self, parts, d, extra):
        """ring_parts' pieces on screen, d pixels outside the notes' edge: sides x, y0, y1 and tops x0, x1, y
        (extra: how far past each corner)."""
        sides, tops = parts
        ax = self.sx / self.app.ppq
        bx = self.kb_w - self.view_t * self.sx + self.note_x_shift
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy

        def row(k, lower):  # a key row's top / bottom pixel, as note_rects has it
            y0 = np.round((k + 0.5) * ay + by)
            return np.maximum(np.round((k - 0.5) * ay + by), y0 + 1) - 1 if lower else y0

        sx = np.round(sides[:, 0] * ax + bx) + np.where(sides[:, 3] == 1, d - 1, -d)  # (a note's last pixel: end - 1)
        sy0, sy1 = row(sides[:, 2], False) - d, row(sides[:, 1], True) + d + extra
        tx0, tx1 = np.round(tops[:, 0] * ax + bx) - d, np.round(tops[:, 1] * ax + bx) - 1 + d + extra
        ty = np.where(tops[:, 3] == 1, row(tops[:, 2], False) - d, row(tops[:, 2], True) + d)
        return sx, sy0, sy1, tx0, tx1, ty

    def draw_edge_preview(self, w, h):
        """While the outline gate box is being used (app.edge_preview = its gate in beats): a red line where the
        selected shapes' outline would reach inside, worked out from what's typed (custom.edge_inner): with the
        even band the shape shrunk inward, drawn like a shape's line."""
        app = self.app
        g = getattr(app, "edge_preview", None)
        if not g:
            return
        bx = self.kb_w - self.view_t * self.sx  # (x = beat * sx + bx, y = key * ay + by)
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        todo = []
        for i in app.sels:
            sh = app.shapes[i] if i < len(app.shapes) else None
            if sh and sh.get("merge"):  # (Gate sensitive merge: its shapes where their notes are, merge.py)
                todo += preview_parts(sh, app.ppq, app.keys)
            elif sh and sh["kind"] == "custom" and "notes" not in sh:
                todo.append((sh, None))
        for sh, slid in todo:
            if sh["kind"] == "custom" and "notes" not in sh:
                got = edge_inner(dict(sh, edge=g), app.ppq)
                if got is None or not len(got[1]):
                    continue
                if slid:  # (each key row of the sliding shape as far as it slid)
                    got = slid_preview(got, slid, app.ppq)
                # (user: red like a shape's line, a bit thicker than 1 px but never as thick as the selected
                # shape's 2 px: a red pixel with a light red one beside it)
                if got[0] == "rows":
                    parts, view = ring_parts(got[1], 0), (self.kb_w, self.ruler_h, w, h)
                    self.draw_ring_lines(parts, view, -1, 1, fill=PREVIEW_HALO, width=1)
                    self.draw_ring_lines(parts, view, 0, 1, fill=PREVIEW_COLOR, width=1)
                    continue
                segs = got[1]
                x0, y0 = segs[:, 0] * self.sx + bx, segs[:, 1] * ay + by
                x1, y1 = segs[:, 2] * self.sx + bx, segs[:, 3] * ay + by
                on = ~((np.maximum(x0, x1) < self.kb_w) | (np.minimum(x0, x1) > w) |
                       (np.maximum(y0, y1) < self.ruler_h) | (np.minimum(y0, y1) > h))
                ln = np.maximum(np.hypot(x1 - x0, y1 - y0), 1e-9)
                nx, ny = -(y1 - y0) / ln, (x1 - x0) / ln  # (the halo one pixel to the side: the inner one)
                xi, yi = segs[:, 4] * self.sx + bx, segs[:, 5] * ay + by
                flip = np.where(nx * (xi - (x0 + x1) / 2) + ny * (yi - (y0 + y1) / 2) < 0, -1, 1)
                nx, ny = nx * flip, ny * flip
                # (pieces joined end to end into a few long lines: one canvas item per piece made every step of
                # the box slow)
                x0, y0, x1, y1, nx, ny = (v[on].tolist() for v in (x0, y0, x1, y1, nx, ny))
                for run in piece_runs(x0, y0, x1, y1):
                    line, halo = [], []
                    for n, (i, fwd) in enumerate(run):
                        pts = [(x0[i], y0[i]), (x1[i], y1[i])][::1 if fwd else -1]
                        if not n:
                            line += pts[0]
                            halo += (pts[0][0] + nx[i], pts[0][1] + ny[i])
                        # (the halo's corner: the two pieces' sides averaged)
                        j = run[n + 1][0] if n + 1 < len(run) else i
                        hx, hy = nx[i] + nx[j], ny[i] + ny[j]
                        d = math.hypot(hx, hy)
                        hx, hy = (hx / d, hy / d) if d > 1e-6 else (nx[i], ny[i])
                        line += pts[1]
                        halo += (pts[1][0] + hx, pts[1][1] + hy)
                    self.create_line(*halo, fill=PREVIEW_HALO, width=1)
                    self.create_line(*line, fill=PREVIEW_COLOR, width=1, capstyle="round")

    def draw_ring_lines(self, parts, view, d, extra, **kw):
        """ring_parts' (sides, tops) as lines on screen, d pixels outside the notes' edge (extra: how far the
        lines reach past each corner), those in view = (x0, y0, x1, y1)."""
        kb, top, w, h = view
        sx, sy0, sy1, tx0, tx1, ty = self.ring_screen(parts, d, extra)
        on_s = ~((sx < kb) | (sx > w) | (sy1 < top) | (sy0 > h))
        on_t = ~((tx1 < kb) | (tx0 > w) | (ty < top) | (ty > h))
        if on_s.sum() + on_t.sum() > RING_MAX:
            return
        # (pieces meeting at a corner joined into one line: one canvas item per piece made panning slow, ~3000
        # pieces cost 90 ms a step)
        on_t &= tx1 > tx0  # (a note under a pixel wide: nothing, as a line of no length)
        xa = np.concatenate([sx[on_s], tx0[on_t]])
        ya = np.concatenate([sy0[on_s], ty[on_t]])
        up = np.arange(len(xa)) < on_s.sum()
        xb = np.where(up, xa, np.concatenate([sx[on_s], tx1[on_t]]) - extra)
        yb = np.where(up, np.concatenate([sy1[on_s], ty[on_t]]) - extra, ya)
        for line in ring_chains(xa.tolist(), ya.tolist(), xb.tolist(), yb.tolist(), up.tolist(), extra):
            self.create_line(*line, **kw)

    def draw_above_marks(self):
        """A small arrow pointing up at the top of the piano roll for each shape put away above its highest key
        (pianoroll.above_marks; user: one, in the middle of the shape). Selected: red like its line."""
        s, y = self.scale, self.ruler_h + 3 * self.scale
        for i, x in self.above_marks():
            self.create_polygon(x, y, x + 6 * s, y + 9 * s, x - 6 * s, y + 9 * s, outline=look.ABOVE_EDGE,
                                fill=look.SHAPE_LINE_PICKED if i in self.app.sels else look.SHAPE_LINE)

    def draw_cut_marks(self):
        """A selected piece's cut ends (notes/sliced.py; user, 2026-10-06): a Slice cut = a short stretch of the Slice
        tool's dashed line, the way it was drawn; a Split here cut = small scissors beside it. From each, a really
        faint line to where the other piece's end is now (none while they touch, or once that piece is gone; a copy's
        marks pair only with the pieces copied with it)."""
        app = self.app
        if not app.sels:
            return
        spots = {}  # mark id -> [(shape, mark, x, y)]
        for i, sh in enumerate(app.shapes):
            d = moved_by(sh) if sh.get("cut") else None
            if d is None:
                continue
            for m in sh["cut"]["marks"]:
                m = moved_mark(m, d)
                spots.setdefault(m["id"], []).append((i, m, self.t2x(m["at"][0]), self.p2y(m["at"][1])))
        s = self.scale
        for pair in spots.values():
            for i, m, x, y in pair:
                if i not in app.sels:
                    continue
                for j, _, x2, y2 in pair:
                    if j != i and math.hypot(x2 - x, y2 - y) > 3 * s:
                        self.create_line(x, y, x2, y2, fill=FAINT_CUT, width=1, dash=(2, 4))
                if m.get("segs") and not m.get("hits"):  # (a custom shape's: along its cut edge, over its line there)
                    for (b0, k0), (b1, k1) in m["segs"]:
                        xy = self.t2x(b0), self.p2y(k0), self.t2x(b1), self.p2y(k1)
                        self.create_line(*xy, fill=look.CUT_UNDER, width=max(1, round(2 * s)) + 1)
                        self.create_line(*xy, fill=look.CUT, width=max(1, round(2 * s)), dash=(6, 3))
                elif m["kind"] == "slice":  # (a line's cut through its notes: one where it crosses each run, user)
                    dx, dy = m.get("dir", (0, 1))
                    dx, dy = dx * self.sx, -dy * self.sy
                    ln = math.hypot(dx, dy) or 1
                    dx, dy = dx / ln * CUT_MARK * s, dy / ln * CUT_MARK * s
                    for hx, hy in ([(self.t2x(b), self.p2y(k)) for b, k in m["hits"]] if m.get("hits") else
                                   [(x, y)]):
                        self.create_line(hx - dx, hy - dy, hx + dx, hy + dy, fill=look.CUT,
                                         width=max(1, round(2 * s)), dash=(6, 3))
                else:
                    self.draw_scissors(x + 9 * s, y - 9 * s, s)

    def draw_scissors(self, x, y, s):
        """Small scissors (two rings, two crossed blades) centred on x, y, drawn with lines so every system shows
        them."""
        r, w = 2.5 * s, max(1, round(1.5 * s))
        for side in (-1, 1):
            cx, cy = x + side * 3 * s, y + 4.5 * s
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=look.CUT, width=w)
            self.create_line(cx - side * 1 * s, cy - r, x - side * 3 * s, y - 6 * s, fill=look.CUT, width=w)

    def draw_select_box(self):
        """The box being dragged with Select (with the ones kept when Ctrl+drag adds it), or the last ones
        (kept_box): one outline, boxes that touch or overlap joined."""
        self.delete("selbox")
        if self.drag and self.drag[0] == "box":
            boxes = self.box_more + [b for b in (self.box_area(),) if b]
        else:
            boxes = self.kept_box() or []
        whole = self.ruler_boxes(boxes)  # (over every key: only the ruler's black band shows it, like Domino)
        draw_boxes(self, [self.box_rect(b) for b in boxes if b not in whole], self.kb_w, self.ruler_h, self.scale,
                   tags="selbox")
        self.draw_ruler_pick(boxes)
        vel = getattr(self.app, "vel", None)
        if vel and (whole or vel.find_withtag("band")):  # (the velocity pane shows the bands too)
            vel.request_redraw()

    def draw_slice(self):
        """The Slice tool's line while it's dragged: dashed, a dot at each end."""
        self.delete("slice")
        if not (self.drag and self.drag[0] == "slice"):
            return
        (xa, ya), (xb, yb) = (self.to_xy(p) for p in self.drag[1:3])
        s = self.scale
        self.create_line(xa, ya, xb, yb, fill=look.CUT, width=max(1, round(2 * s)), dash=(6, 3), tags="slice")
        r = 3 * s
        for x, y in ((xa, ya), (xb, yb)):
            self.create_oval(x - r, y - r, x + r, y + r, fill=look.CUT, outline="", tags="slice")

    def draw_playhead(self, keep=False):
        """keep: left as it is when it's already there (redraw with the keyboard + ruler kept)."""
        self.draw_ahead_grey()
        x = round(self.t2x(self.app.playhead))
        if keep and self._playhead_x == x and self.find_withtag("playhead&&!ahead"):
            return
        self.delete("playhead&&!ahead")
        self._playhead_x = x
        w, h = self.winfo_width(), self.winfo_height()
        if self.kb_w <= x <= w:
            s, top = self.scale, self.ruler_h
            self.create_line(x, top, x, h, fill=look.PLAY_LINE, width=max(1, round(s)), tags="playhead")
            self.create_polygon(x - 5 * s, top - 8 * s, x + 5 * s, top - 8 * s, x, top, fill=look.PLAY_LINE,
                                tags="playhead")
        x = None if self.scrub_t is None else round(self.t2x(self.scrub_t))
        if x is not None and self.kb_w <= x <= w:  # right-drag listening: a line of its own, no arrow (the play
            self.create_line(x, self.ruler_h, x, h, fill=look.PLAY_LINE,  # line stays put; user)
                             width=max(1, round(self.scale)), tags="playhead")

    def draw_ahead_grey(self):
        """Rendered ahead (PLAN R4): a REALLY faint grey (user: must not disturb the piano roll; fainter than the Hz
        preview's) over the part of the song not made yet, while playing. Tagged "playhead" too, so a redraw keeps
        it like the play line; the canvas is only changed when the grey moved by a pixel."""
        ahead = getattr(self.app, "ahead_play", None)
        w, h = self.winfo_width(), self.winfo_height()
        spans = ()
        if ahead is not None and self.sx is not None and w > self.kb_w:
            spans = tuple((max(a, self.kb_w), min(b, w)) for a, b in
                          ((round(self.t2x(a)), round(self.t2x(b)))
                           for a, b in ahead.grey_beats(self.x2t(self.kb_w), self.x2t(w))) if b > a)
            spans = tuple((a, b) for a, b in spans if b > a)
        key = (spans, self.ruler_h, h)
        had = self.find_withtag("ahead")
        if key == getattr(self, "_ahead_key", None) and (bool(had) or not spans):
            return
        self._ahead_key = key
        self.delete("ahead")
        for a, b in spans:
            self.create_rectangle(a, self.ruler_h, b, h, fill=look.AHEAD_UNMADE, outline="", stipple="gray12",
                                  tags=("playhead", "ahead"))
        if spans and self.find_withtag("playhead&&!ahead"):  # (under the play line)
            self.tag_lower("ahead", "playhead&&!ahead")

    def show_playhead(self, start=False):
        """Move the play line; while playing, turn the page when it gets near the right edge
        or back to it when it's left of the view). Not while the mouse holds something or a shape is being drawn
        (user: what's held jumped a page): the line goes on off screen, the page turns once it's let go. Not while
        the user scrolls / zooms either (PlayFollow): once they stop, the view snaps back to the line."""
        tracks = getattr(self.app, "track_list", None)
        if tracks is not None:  # (the track list's own line and pages)
            tracks.show_playhead(start)
        if self.sx is None:
            return
        x, w = self.t2x(self.app.playhead), self.winfo_width()
        busy = (self.drag or self.draft or self.follow or self.app.vel.edit or self.scrub_t is not None
                or self.lasso and not self.lasso["closed"])  # (a lasso loop being clicked: like a polyline, hunt;
        # right-drag listening: the notes stay under the mouse)
        if self.play_follow is None:
            self.play_follow = PlayFollow(self.app)
        free = self.play_follow.free((self.view_t, self.sx), start)
        if self.app.player.running and not busy and free and (x > w - 6 * self.scale or x < self.kb_w):
            # (on a whole pixel, like every view move: the pictures made ahead fit only then, else the loaded
            # notes' overview showed first and the notes shifted when the exact one came, user 2026-10-25)
            self.view_t = math.floor(self.app.playhead * self.sx) / self.sx
            self.clamp_view()
            self.play_follow.turned((self.view_t, self.sx))
            self.request_redraw()  # the full redraw puts the line back too
            return
        self.draw_playhead()
        for name in ("vel", "tempo_pane"):
            pane = getattr(self.app, name, None)
            if pane:
                pane.draw_playhead()

    def visible_pitches(self, h):
        return max(0, math.ceil(self.y2p(h) - 0.5)), min(self.app.keys - 1, math.floor(self.y2p(self.ruler_h) + 0.5))

    def grid_parts(self, w, h):
        """
        The grid as rows = [(y0, y1, color)] bands (y1 None = 1-pixel line), painted in order,
        and cols = [(x, color)] vertical lines painted over them.
        All in whole pixels, rounded like the notes: the canvas and the picture (paint_image) then draw the very
        same rows, whichever is used.
        """
        top = self.ruler_h
        rows = [(top, h, look.ROLL_BG)]
        keys = self.app.keys
        # Space past the keys (Settings → General): the key rows go on there, darker (look.space_shade); past it
        # the flat OUTSIDE_KEYS as before
        room = int(getattr(self, "room_keys", lambda: 0)())
        lo_key, hi_key = -room, keys - 1 + room
        # (one more key each side, the ones off the picture dropped below: a key just past the edge can still have
        # its line rounded onto it, and panning moved that edge into view without the line until painted whole)
        p_lo = max(lo_key, math.ceil(self.y2p(h) - 0.5) - 1)
        p_hi = min(hi_key, math.floor(self.y2p(self.ruler_h) + 0.5) + 1)
        edo = self.app.edo()
        size, black = (EDO, EDO_BLACK) if edo else (12, BLACK)

        def shade(p, colour):
            return colour if 0 <= p < keys else look.space_shade(colour)
        if room:
            if p_lo < 0:
                rows.append((self.row_y(-1)[0], self.row_y(p_lo)[1], look.space_shade(look.ROLL_BG)))
            if p_hi >= keys:
                rows.append((self.row_y(p_hi)[0], self.row_y(keys)[1], look.space_shade(look.ROLL_BG)))
        for p in range(p_lo, p_hi + 1):
            y0, y1 = self.row_y(p)
            if p % size in black:
                rows.append((y0, y1, shade(p, look.ROW_BLACK_EDO if edo else look.ROW_BLACK)))
            elif edo and p % size not in EDO_WHITE:
                rows.append((y0, y1, shade(p, look.ROW_HALF)))
            elif self.sy >= 4:
                rows.append((y1, None, shade(p, look.ROW_LINE)))
        for p in range(p_lo, p_hi + 1):
            if p % size == 0:
                rows.append((self.row_y(p)[1], None, shade(p, look.OCTAVE_LINE)))
        if self.p2y(lo_key - 0.5) < h:
            rows.append((self.row_y(lo_key)[1], h, look.OUTSIDE_KEYS))
        if self.p2y(hi_key + 0.5) > top:
            rows.append((top, self.row_y(hi_key)[0], look.OUTSIDE_KEYS))
        # Space before the start: everything left of beat 0 darker (draw_grid / paint_region read it)
        x0 = round(self.t2x(0.0))
        self.dark_x = x0 if x0 > self.kb_w else None
        top = int(top)  # (the picture's first pixel row, under the ruler)
        rows = [r for r in rows if (top <= r[0] < h if r[1] is None else r[1] > top and r[0] < h)]
        return rows, self.grid_cols(w)

    def row_y(self, p):
        """Top and bottom of key p's row, in whole pixels (the same rounding as the notes)."""
        return round(self.p2y(p + 0.5)), round(self.p2y(p - 0.5))

    def grid_cols(self, w):
        """Vertical grid lines [(x, color)]: snap lines, beats, bars (the velocity pane uses them too)."""
        cols = []
        # (a pixel more each side, the ones off the picture dropped at the end: a line just past the edge can still
        # be rounded onto it, and panning moved that edge into view without the line until painted whole)
        b_lo, b_hi = self.x2t(self.kb_w - 1), self.x2t(w + 1)
        sb = self.app.snap_beats()
        if sb and sb < 1 and sb * self.sx >= 8:
            i = math.ceil(b_lo / sb)
            while i * sb <= b_hi:
                b = i * sb
                if abs(b - round(b)) > 1e-9:
                    cols.append((round(self.t2x(b)), look.GRID_SNAP))
                i += 1
        sigs = sigs_of(self.app.tempo)  # (bars and beats by the time signatures: 6/8 = a line each eighth)
        cols += [(round(self.t2x(b)), look.GRID_BEAT) for b in beat_lines(sigs, max(0.0, b_lo), b_hi, 5 / self.sx)]
        cols += [(round(self.t2x(b)), look.GRID_BAR) for b, _ in bar_lines(sigs, max(0.0, b_lo), b_hi, 6 / self.sx)]
        if b_lo < 0:  # (Space before the start: the first bar's beats and bars go on backwards)
            first = sigs[0] if sigs else (0, 4, 4)
            beat, bar = 4 / first[2], first[1] * 4 / first[2]
            k = 1
            while -k * beat >= b_lo and (beat * self.sx >= 5 or bar * self.sx >= 6):
                b = -k * beat
                on_bar = abs(b / bar - round(b / bar)) < 1e-9
                if on_bar and bar * self.sx >= 6:
                    cols.append((round(self.t2x(b)), look.GRID_BAR))
                elif beat * self.sx >= 5 and b <= b_hi:
                    cols.append((round(self.t2x(b)), look.GRID_BEAT))
                k += 1
        ppq = self.app.ppq
        by = self.tick_by()
        if by is not None:  # zoomed far in: a line each tick, fading in (first: a snap / beat / bar line wins)
            color, taken = fade(look.GRID_TICK, 1 - by), {x for x, _ in cols}
            ticks = (round(self.t2x(i / ppq)) for i in range(max(0, math.ceil(b_lo * ppq)), math.floor(b_hi * ppq) + 1))
            cols = [(x, color) for x in ticks if x not in taken] + cols
        return [c for c in cols if int(self.kb_w) <= c[0] < w]

    def tick_by(self):
        """How strong the tick lines show (0.4 .. 1), None while zoomed out too far for them."""
        tick_px = self.sx / self.app.ppq
        if tick_px < TICK_FROM:
            return None
        return 0.4 + 0.6 * min(1.0, (tick_px - TICK_FROM) / (TICK_FULL - TICK_FROM))

    def draw_grid(self, w, h, rows, cols):
        x0, top = self.kb_w, self.ruler_h
        dark = getattr(self, "dark_x", None)  # (Space before the start: left of it every colour darker)
        parts = ((x0, dark, look.space_shade), (dark, w, None)) if dark is not None else ((x0, w, None),)
        for a, b, shade in parts:
            for y0, y1, color in rows:
                color = shade(color) if shade else color
                if y1 is None:
                    self.create_line(a, y0, b, y0, fill=color, tags="grid")
                else:
                    self.create_rectangle(a, y0, b, y1, fill=color, outline="", tags="grid")
        for x, color in cols:
            self.create_line(x, top, x, h, fill=look.space_shade(color) if dark is not None and x < dark else color,
                             tags="grid")

    def start_order(self):
        """The rendered notes' row numbers by start (stable: the same start keeps their order), kept until the
        notes change."""
        rendered = self.app.drawn
        if self._start_order is None or self._start_order[0] is not rendered:
            self._start_order = (rendered, np.argsort(np.ascontiguousarray(rendered[:, 0]), kind="stable"))
        return self._start_order[1]

    def sorted_notes(self):
        """The rendered notes sorted by start (kept until the notes change)."""
        rendered = self.app.drawn
        if self._note_index is None or self._note_index[0] is not rendered:
            order = np.take(rendered, self.start_order(), axis=0)
            self._note_index = (rendered, order, int((order[:, 1] - order[:, 0]).max()) if len(order) else 0,
                                np.ascontiguousarray(order[:, 0]))
        return self._note_index[1]

    def visible_range(self, t_lo, t_hi):
        """(first, end): the sorted_notes() rows that can overlap ticks t_lo..t_hi."""
        self.sorted_notes()
        longest, starts = self._note_index[2:]
        # (whole ticks looked for: a fraction made NumPy turn every start into a fraction first, the same rows)
        lo, hi = (min(max(v, -1 << 62), 1 << 62) for v in (math.ceil(t_lo - longest), math.floor(t_hi)))
        return int(np.searchsorted(starts, lo, "left")), int(np.searchsorted(starts, hi, "right"))

    def starting_in(self, t_lo, t_hi):
        """The sorted_notes() rows (an array view) whose notes start inside ticks t_lo..t_hi."""
        notes = self.sorted_notes()
        starts = self._note_index[3]
        lo, hi = (min(max(v, -1 << 62), 1 << 62) for v in (math.ceil(t_lo), math.floor(t_hi)))
        return notes[int(np.searchsorted(starts, lo, "left")):int(np.searchsorted(starts, hi, "right"))]

    def paint_order(self, fast):
        """The compiled loops' painting order of the notes (fastloops.paint_order: s, e, key, colour, first,
        longest) + the number of unselected owners, kept until the notes, selection or picture colours change; None
        when there are no notes or keys / owners outside the tables (the other way then)."""
        app = self.app
        rendered = app.drawn
        pics = np.asarray(getattr(app, "picture_owners", ()), bool)
        back = getattr(app, "behind_owner", None)
        rule = stack_rule(app)
        key = (frozenset(app.sels), pics.tobytes(), len(pics), bool(app.picture_use10), back, rule)
        got = self._paint_order
        if got is not None and got[0] is rendered and got[1] == key:
            return got[2]
        made = painting_order(fast, rendered, self.start_order(), app.sels, pics, app.keys, app.picture_use10, back,
                              rule)
        self._paint_order = (rendered, key, made)
        return made

    def ready_order(self, rendered, start, order):
        """Notes made in the background (App.notes_made) came with their start order and painting order (made for
        the selection and pictures then): kept, so painting them doesn't work them out again."""
        self._start_order = (rendered, start)
        if order is not None:
            self._paint_order = (rendered,) + order

    def paint_ends(self, which, made, s, e, key, group, tag=None):
        """covered_ends for these notes at this zoom (None: no sustained shape, nothing to save), remembered per notes
        (which = "fast" / "plain": the two ways' arrays, made = the array they came from), tag (what else group()
        depends on) and zoom. group(): each note's painting group."""
        if not any(is_sustained(sh) for sh in self.app.shapes):
            return None
        room = math.ceil(3 * self.app.ppq / self.sx) + 1  # (3 px: its edge well inside the note over it)
        got = self._cover.get(which)
        if got is None or got[0] is not made or got[3] != tag:
            got = self._cover[which] = [made, cover_next(s, e, key, group()), None, tag]
        if got[2] is None or got[2][0] != room:
            ends = covered_ends(e, got[1], room)
            got[2] = (room, ends, int((ends - s).max()) if len(s) else 0, ends < e)
        return got[2][1:]

    def cut_colours(self, c, cut):
        """Colour numbers with NO_END on the notes whose ends covered_ends cut short (no shade for those ends),
        remembered while the same."""
        got = getattr(self, "_cut_colours", None)
        if got is None or got[0] is not c or got[1] is not cut:
            got = self._cut_colours = (c, cut, np.where(cut, c | c.dtype.type(NO_END), c))
        return got[2]

    def visible_notes(self, t_lo, t_hi):
        """Rendered notes (array rows) that can overlap ticks t_lo..t_hi."""
        lo, hi = self.visible_range(t_lo, t_hi)
        return self.sorted_notes()[lo:hi]

    def note_rects(self, w, h, clip=None, area=None, only=None, draft="with"):
        """On-screen notes as whole-pixel rectangles, skipping exact repeats: NumPy arrays (x0, y0, x1, y1, colour),
        colour = a number in NOTE_COLORS. Painted in this order (selected shapes' notes, then the draft, on top).
        clip = (x0, y0, x1, y1): only the notes that touch this part of the screen (the same rectangles).
        area = (x0, y0, x1, y1): as if the note area were there (it can reach past the screen).
        only: True = just the selected shapes' notes, False = all but theirs (no draft either way).
        draft: "with" = the draft's notes too, "without" = all but them, "alone" = only them (paint_drafted)."""
        app, ppq = self.app, self.app.ppq
        ax, bx = self.sx / ppq, self.kb_w - self.view_t * self.sx + self.note_x_shift  # x = tick * ax + bx
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy  # y = pitch * ay + by
        kb, top, w, h = area or (self.kb_w, self.ruler_h, w, h)
        keys = np.arange(app.keys)
        row0, row1 = np.round((keys + 0.5) * ay + by).astype(np.int64), np.round((keys - 0.5) * ay + by).astype(np.int64)
        shown = ~((row1 < top) | (row0 > h))  # each key's row on screen?
        row1 = np.maximum(row1, row0 + 1)
        if clip:
            shown &= ~((row1 < clip[1]) | (row0 >= clip[3]))
        t_lo, t_hi = (self.x2t(clip[0] - 2) * ppq, self.x2t(clip[2] + 2) * ppq) if clip else (self.x2t(kb) * ppq,
                                                                                               self.x2t(w) * ppq)
        fast = loops()
        drafted = self.draft_notes() if only is None and draft != "without" else None
        kept = self.paint_order(fast) if fast and draft != "alone" else None
        main = None
        if draft == "alone":
            if drafted is None:
                return tuple(np.zeros(0, np.int64) for _ in range(5))
            main = tuple(np.zeros(0, np.int64) for _ in range(4))
        if kept is not None:  # (the compiled loops, the notes kept in painting order: the same notes)
            (s, e, k, c, first, longest), others = kept
            held = self.paint_ends("fast", s, s, e, k, lambda: np.repeat(np.arange(len(first) - 1), np.diff(first)))
            if held is not None:  # (Sustain: held notes painted only where they show; same pixels)
                e, longest, cut = held
                c = self.cut_colours(c, cut)
            b0, b1 = 0, len(first) - 1
            if only is not None and app.sels:
                b0, b1 = (others, b1) if only else (0, others)
            # (the same notes as visible_notes(t_lo, t_hi) gives, then screen())
            args = (s, e, k, c, first, b0, b1, t_lo - longest, float(t_hi), float(ax), float(bx), float(kb), float(w),
                    np.ascontiguousarray(shown, bool), bool(clip), float(clip[0]) if clip else 0.0,
                    float(clip[2]) if clip else 0.0)
            if drafted is None:
                left, right = int(kb) - 2, int(w) + 2
                return fast.order_rects(*args, left, right, app.keys, row0, row1)
            main = fast.order_screen(*args)
        sels = list(app.sels)

        def screen(notes, color, order, cut=None):
            """These notes' x0, x1, key and colour number, the ones on screen only. order: put in painting order
            (shapes drawn later go on top, clicks pick that one too; then the selected shapes' notes on top).
            cut: the notes whose ends were cut short (their colour numbers get NO_END)."""
            key = notes[:, 2]
            # (the same sums as one note at a time, so the same pixels)
            x0, x1 = np.round(notes[:, 0] * ax + bx), np.round(notes[:, 1] * ax + bx)
            on = ~((x1 < kb) | (x0 > w)) & shown[key]
            if clip:
                on &= ~((x1 < clip[0]) | (x0 >= clip[2]))
            on = np.flatnonzero(on)
            if order and len(on):
                # (stacked notes: stack_rule's order, the selected shapes' on top, faint tracks under all; the
                # same order as the compiled painting_order: by key, then by start)
                key_of = stack_keys(notes[on], sels, getattr(app, "behind_owner", None), stack_rule(app))[0]
                on = on[np.argsort(key_of, kind="stable")]
                owner = notes[on, 5]
                cols = slot_colours(getattr(app, "picture_use10", False))
                color = note_colours(notes[on, 4], cols)
                pics = getattr(app, "picture_owners", ())
                if len(pics) and pics.any():  # a placed picture's notes: its own colours (PICTURE_GROUP on)
                    mine = pics[np.clip(owner, 0, len(pics) - 1)]
                    color = np.where(mine, PICTURE_GROUP + np.minimum(notes[on, 4], 15), color)
            elif order:
                color = np.zeros(0, np.int64)
            # low velocity = paler fill (outline stays); 32 shades is plenty. Velocity -1 = a ghost (mask.py)
            vel = notes[on, 3]
            code = color * 32 + np.clip(vel, 0, 127) // 4
            if len(vel) and vel.min() < 0:
                code = np.where(vel < 0, ghost_code(color, vel), code)
            if cut is not None:
                code = np.where(cut[on], code | NO_END, code)
            return x0[on].astype(np.int64), x1[on].astype(np.int64), key[on], code

        if main is None:
            lo, hi = self.visible_range(t_lo, t_hi)
            notes = self.sorted_notes()[lo:hi]
            every = self.sorted_notes()
            back, rule = getattr(app, "behind_owner", None), stack_rule(app)
            held = self.paint_ends("plain", every, every[:, 0], every[:, 1], every[:, 2],
                                   lambda: stack_keys(every, sels, back, rule)[0], (frozenset(sels), back, rule))
            cut = None
            if held is not None:  # (Sustain: held notes painted only where they show; same pixels)
                notes = notes.copy()
                notes[:, 1] = held[0][lo:hi]
                cut = held[2][lo:hi]
            if clip:
                keep = shown[np.clip(notes[:, 2], 0, app.keys - 1)]
                notes, cut = notes[keep], None if cut is None else cut[keep]
            if only is not None and app.sels:
                keep = np.isin(notes[:, 5], list(app.sels)) == only
                notes, cut = notes[keep], None if cut is None else cut[keep]
            main = screen(notes, None, True, cut)
        parts = [main]
        if drafted is not None:
            parts.append(screen(drafted, DRAFT, False))
        x0, x1, key, color = (np.concatenate(v) if len(v) > 1 else v[0] for v in zip(*parts))
        left, right = int(kb) - 2, int(w) + 2
        if fast and (not len(key) or (key.min() >= 0 and key.max() < app.keys)):
            return fast.last_on_pixels(*(np.ascontiguousarray(v, np.int64) for v in (x0, x1, key, color)),
                                       left, right, app.keys, row0, row1)
        x0, x1 = np.maximum(x0, left), np.minimum(x1, right)
        # zoomed out, lots of notes land on the very same pixels: only the last of them shows (it's painted over
        # the others), so the others are left out. Found with a table, for notes up to 3 pixels long (the many).
        n = len(x0)
        if n > 1:
            size = x1 - x0
            small = np.flatnonzero((size < 4) & (size >= 0))
            spot = ((x0[small] - left) * app.keys + key[small]) * 4 + size[small]
            last = np.empty((right - left + 1) * app.keys * 4, np.int64)
            last[spot] = small  # (a repeated index keeps the last one written)
            keep = np.ones(n, bool)
            keep[small] = last[spot] == small
            if not keep.all():
                x0, x1, key, color = x0[keep], x1[keep], key[keep], color[keep]
        # the outline goes inside the note (user): its last pixel is the one before the note's end / next row
        return x0, row0[key], np.maximum(x1 - 1, x0), row1[key] - 1, color

    def paint_image(self, w, h, rows, cols, rects):
        """Grid and notes as one picture over the note area."""
        kb, top = int(self.kb_w), int(self.ruler_h)
        if w - kb < 1 or h - top < 1:
            return
        self._img = self.paint_region(rows, cols, rects, (kb, top, w, h))
        self.show_image()

    def show_image(self, moved=None, strips=()):
        """The picture's pixels go on screen. moved = (dx, dy) with strips: the picture shown is the same one moved
        that far, but for these strips (x0, y0, x1, y1 inside the picture), so only they are sent."""
        img = self._img
        ih, iw = img.shape[:2]
        if self._tiles is None or self._tiles.size != (iw, ih):
            self._tiles = Tiles(self, iw, ih)
            moved = self._shown = None
        if moved:
            self._tiles.move(*moved)
        elif self._shown is not None and self._shown.shape == img.shape:
            # only the part that differs from what's shown is sent (sending pixels is the slow part)
            was = self._shown.reshape(ih, -1)
            rows = np.flatnonzero((was != img.reshape(ih, -1)).any(axis=1))
            strips = []
            if len(rows):
                y0, y1 = int(rows[0]), int(rows[-1]) + 1
                cols = np.flatnonzero((was[y0:y1] != img.reshape(ih, -1)[y0:y1]).any(axis=0)) // 3
                strips = [(int(cols[0]), y0, int(cols[-1]) + 1, y1)]
        else:
            strips = [(0, 0, iw, ih)]
        for x0, y0, x1, y1 in strips:
            self._tiles.put(img, x0, y0, x1, y1)
        self._shown = img
        self._tiles.place(self, int(self.kb_w), int(self.ruler_h))

    def pan_pixels(self, old, pic, w, h):
        """(dx, dy): how far the picture painted for old has to move to show pic, if the view just moved by whole
        pixels (less than half the picture) and nothing else changed; else None."""
        if old is None or self._img is None or old[0] is not pic[0] or old[1][0] != pic[1][0]:
            return None
        dx, dy = (old[1][1] - pic[1][1]) * self.sx, (pic[1][2] - old[1][2]) * self.sy
        if abs(dx - round(dx)) > 1e-6 or abs(dy - round(dy)) > 1e-6:
            return None
        dx, dy = round(dx), round(dy)
        ih, iw = self._img.shape[:2]
        if (dx, dy) == (0, 0) or abs(dx) > iw // 2 or abs(dy) > ih // 2:
            return None
        return dx, dy

    def shift_image(self, w, h, rows, cols, dx, dy):
        """The picture moved by (dx, dy) pixels; the strips that come into view are painted."""
        kb, top = int(self.kb_w), int(self.ruler_h)
        old = self._img
        ih, iw = old.shape[:2]
        img = np.empty_like(old)
        img[max(dy, 0):ih + min(dy, 0), max(dx, 0):iw + min(dx, 0)] = (
            old[max(-dy, 0):ih + min(-dy, 0), max(-dx, 0):iw + min(-dx, 0)])
        strips = []
        # (moved right / down: one pixel more, the picture's old first column / row: it was under the keyboard /
        # ruler (they end part way into a pixel) and not painted like the rest; moved into view it was a wrong line
        # until painted whole)
        if dx:
            strips.append((kb, top, kb + dx + 1, h) if dx > 0 else (w + dx, top, w, h))
        if dy:
            strips.append((kb, top, w, top + dy + 1) if dy > 0 else (kb, h + dy, w, h))
        for x0, y0, x1, y1 in strips:
            rects = self.note_rects(w, h, clip=(x0, y0, x1, y1))
            img[y0 - top:y1 - top, x0 - kb:x1 - kb] = self.paint_region(rows, cols, rects, (x0, y0, x1, y1))
        self._img = img
        self.show_image((dx, dy), [(x0 - kb, y0 - top, x1 - kb, y1 - top) for x0, y0, x1, y1 in strips])

    def carried(self):
        """While shapes are dragged to another place and their notes are left for later (app.shapes_changed):
        (dx, dy, apart), how far they are from where the notes on screen have them, in pixels; apart: they didn't all
        go as far (one held back at an edge). Else None."""
        app = self.app
        if not (self.drag and self.drag[0] == "move" and app.notes_late and app.show_notes.get()) or self.draft:
            return None
        got = set()
        for j in self.drag[2]:
            if j >= len(app.shapes) or j >= len(app.rendered_pts):
                return None
            (b, p), (b0, p0) = app.shapes[j]["pts"][0], app.rendered_pts[j]
            got.add((round((b - b0) * self.sx), round((p0 - p) * self.sy)))
        if not got:
            return None
        return (*min(got), len(got) > 1)

    def paint_carried(self, w, h, rows, cols, fixed, dx, dy, apart=False):
        """While shapes are dragged and their notes are left for later (slow to make): the other shapes' notes shown
        as usual, made once, and the dragged shapes' notes as they were, as ONE see-through picture slid (dx, dy)
        pixels further every time (Tk only moves it: painting the notes again at every move took 25-35 ms on the
        user's screen). What they really turn into at the new place, overlaps and all, is worked out when the mouse
        is let go. apart: the dragged shapes didn't all go as far: their lines only, until the notes catch up (one
        slid picture would show some notes in the wrong place, user)."""
        app = self.app
        kb, top = int(self.kb_w), int(self.ruler_h)
        iw, ih = w - kb, h - top
        key = (fixed, self.view_t, self.view_top, tuple(rows), tuple(cols))
        c = self._carry
        if c is None or c["rendered"] is not app.drawn or c["key"] != key:
            c = self._carry = {"rendered": app.drawn, "key": key, "area": None, "base": None}
            rects = self.note_rects(w, h, only=False)
            if (len(app.drawn) > w * h // 2000 or app.loaded is not None
                    or (self.hidden_shade() and row_overlap(rects))):  # (a picture, as redraw paints them: a loaded
                # MIDI's notes are only in the picture, else they went every other frame)
                c["base"] = self.paint_region(rows, cols, rects, (kb, top, w, h), ring=False)
        if c["base"] is not None:
            if self._shown is c["base"] and self._tiles is not None:
                self._tiles.place(self, kb, top)  # (nothing new to send)
            else:
                self._img = c["base"]
                self.show_image()
        else:
            self._tiles = self._img = None
            self.keep_grid(w, h, rows, cols)
            colors = note_tables(app.picture_pal)[0]
            for x0, y0, x1, y1, color in zip(*(v.tolist() for v in self.note_rects(w, h, only=False))):
                fill, edge = colors[color & (NO_END - 1)]
                self.create_rectangle(x0, y0, x1, y1, fill=fill, outline=edge)
        if apart:
            return
        # The dragged notes' picture holds the part of the screen they came from, and around it. KEPT FOR THE WHOLE
        # DRAG at this view: after a rest their real notes are the same, moved along (making it again took ~0.2 s
        # at every next move with a zoomed-in spam shape), so it's slid from where it was made (ref)
        orig = self.drag[2]
        view = (fixed[:7], self.view_t, self.view_top)
        d = self._carry_pic
        if d is not None and (d["orig"] is not orig or d["view"] != view):
            d = None
        if d is not None:
            got = {(round((app.shapes[j]["pts"][0][0] - b0) * self.sx), round((p0 - app.shapes[j]["pts"][0][1]) * self.sy))
                   for j, (b0, p0) in d["ref"].items()}
            dx, dy = min(got)
            a = d["area"]
            if len(got) > 1 or a[0] > kb - dx or a[1] > top - dy or a[2] < w - dx or a[3] < h - dy:
                d = None
        if d is None:
            a = (kb - dx - iw // 2, top - dy - ih // 2, w - dx + iw // 2, h - dy + ih // 2)
            d = self._carry_pic = {"orig": orig, "view": view, "area": a, "photo": [],
                                   "ref": {j: app.rendered_pts[j] for j in orig}}
            rects = self.note_rects(w, h, area=a, only=True)
            if len(rects[0]):
                x0, y0 = max(int(rects[0].min()), a[0]), max(int(rects[1].min()), a[1])
                x1, y1 = min(int(rects[2].max()) + 1, a[2]), min(int(rects[3].max()) + 1, a[3])
                if x1 > x0 and y1 > y0:
                    at, rgb = self.note_pixels(rects, (x0, y0, x1, y1))
                    px = np.zeros(((y1 - y0) * (x1 - x0), 4), np.uint8)
                    px[at, :3] = rgb
                    px[at, 3] = 255
                    d["photo"] = [(photo, x0 + x, y0 + y) for photo, x, y
                                  in see_through_strips(self, px.reshape(y1 - y0, x1 - x0, 4))]
        for photo, x0, y0 in d["photo"]:
            self.create_image(x0 + dx, y0 + dy, image=photo, anchor="nw")

    def paint_drafted(self, w, h, rows, cols):
        """The picture while a shape is drawn / placed over lots of notes: the other notes painted once, the new
        shape's notes (painted last anyway) and the ring put over them at every move. The same pixels as painting
        them all, minus the work. False with few notes (canvas rectangles, painted the normal way)."""
        app = self.app
        kb, top = int(self.kb_w), int(self.ruler_h)
        if w - kb < 1 or h - top < 1:
            return False
        region = (kb, top, w, h)
        key = (frozenset(app.sels), self.sx, self.sy, kb, top, w, h, self.view_t, self.view_top, tuple(rows),
               tuple(cols), app.keys, app.ppq)
        c = self._draft_base
        if c is None or c[0] is not app.drawn or c[1] != key:
            rects = self.note_rects(w, h, draft="without")
            # (more than this: a picture either way, see redraw)
            base = (self.paint_region(rows, cols, rects, region, ring=False)
                    if len(rects[0]) > w * h // 2000 or app.loaded is not None else None)
            c = self._draft_base = (app.drawn, key, base)
        if c[2] is None:
            return False
        img = c[2].copy()
        self.paint_rects(img, self.note_rects(w, h, draft="alone"), region)
        self._img = img
        self.show_image()
        return True

    def paint_exact(self):
        """The picture painted whole again (after it was moved along with the view)."""
        if self._exact:
            self.after_cancel(self._exact)
        self._exact = None
        self._note_pic = None
        self.request_redraw()

    def paint_region(self, rows, cols, rects, region, ring=True):
        """Grid and notes of region = (x0, y0, x1, y1) of the screen (the ends not included) as an array of pixels
        (rows, columns, RGB), with the selected notes' red line over them (ring; draw_ring's lines were slow
        canvas items: ~1000 of them took 25 ms of every step when panning)."""
        kb, top, w, h = region
        iw, ih = w - kb, h - top
        rgb = {}

        def px(color):
            if color not in rgb:
                rgb[color] = bytes.fromhex(color[1:])
            return rgb[color]

        # Every pixel row of the grid is one of a few patterns: its colour with the vertical lines on top
        row_color = [look.ROLL_BG] * ih
        for y0, y1, color in rows:
            a = int(y0) - top
            b = a + 1 if y1 is None else int(y1) - top
            for y in range(max(a, 0), min(b, ih)):
                row_color[y] = color
        patterns = {}
        dark = getattr(self, "dark_x", None)  # (Space before the start: pixels left of it darker)
        dark = None if dark is None else min(iw, max(0, int(dark) - kb))
        for color in set(row_color):
            line = bytearray(px(color) * iw)
            for x, c in cols:
                x = int(x) - kb
                if 0 <= x < iw:
                    line[x * 3:x * 3 + 3] = px(c)
            if dark:
                shaded = bytearray(px(look.space_shade(color)) * dark)
                for x, c in cols:
                    x = int(x) - kb
                    if 0 <= x < dark:
                        shaded[x * 3:x * 3 + 3] = px(look.space_shade(c))
                line[:dark * 3] = shaded
            patterns[color] = bytes(line)
        img = np.frombuffer(b"".join(patterns[c] for c in row_color), np.uint8).reshape(ih, iw, 3).copy()
        loaded = getattr(self.app, "loaded", None) is not None and self.app.show_notes.get()
        tl = getattr(self.app, "track_list", None)
        if loaded and tl is not None and tl.current is not None and tl.current < 0:
            # (a loaded track highlighted: Spiderweb's notes shown are only faint ones picked behind it, so they go
            # under it, hunt 2026-10-24; the red line over both: its loaded notes in a kept box have it, group D)
            self.paint_rects(img, rects, region, False)
            self.paint_loaded(img, region)
            at = self.ring_pixels(region) if ring else None
            if at is not None:
                img.reshape(-1, 3)[at] = RING_RGB
            return img
        if loaded:
            self.paint_loaded(img, region)  # (a loaded MIDI's notes: under Spiderweb's own)
        self.paint_rects(img, rects, region, ring)
        return img

    def paint_rects(self, img, rects, region, ring=True):
        """note_rects' notes painted over img, the pixels of region = (x0, y0, x1, y1), then the ring (paint_region)."""
        fast = loops()
        if fast:  # (the compiled loops: the colours go straight in, no list of pixels in between)
            pal = getattr(getattr(self, "app", None), "picture_pal", None)
            fast.paint_notes(img.reshape(-1, 3), *(int(v) for v in region),
                             *(np.ascontiguousarray(v, np.int64) for v in rects), note_tables(pal)[1],
                             self.hidden_shade())
        else:
            at, colors = self.note_pixels(rects, region)
            img.reshape(-1, 3)[at] = colors
        at = self.ring_pixels(region) if ring else None
        if at is not None:
            img.reshape(-1, 3)[at] = RING_RGB

    def hidden_shade(self):
        """The Look option "Show hidden note starts / ends" (off by default, user 2026-10-31): a note's fill shaded
        where a note under it starts / ends. Off in the Hz window (no app.hidden_shade)."""
        var = getattr(getattr(self, "app", None), "hidden_shade", None)
        return bool(var.get()) if var is not None else False

    def note_pixels(self, rects, region):
        """The pixels of note_rects' notes inside region = (x0, y0, x1, y1): (where, colours), where = row * the
        region's width + column."""
        kb, top, w, h = region
        iw, ih = w - kb, h - top
        fast, shade = loops(), self.hidden_shade()
        if fast:
            (top_px, half), idx = self.top_pixels(fast, rects, region, shade), np.arange(len(rects[0]))
        else:
            (top_px, half), idx = self.top_pixels_numpy(rects, region, shade)
        at = np.flatnonzero(top_px >= 0)
        k = top_px[at]
        pal = getattr(getattr(self, "app", None), "picture_pal", None)  # (the Hz window shares this, no pictures)
        rgb = note_tables(pal)[1]
        code = rects[4][idx][k >> 1] & (NO_END - 1)
        colors = rgb[code, k & 1]
        shade = np.flatnonzero(half[at] & ~k & 1)  # (a fill pixel where a note under it starts / ends)
        if len(shade):
            colors[shade] = (rgb[code[shade], 0].astype(np.int64) + rgb[code[shade], 1]) // 2
        return at, colors

    @staticmethod
    def top_pixels(fast, rects, region, shade=False):
        """note_pixels' (top_px, half) by the compiled loop (every note numbered, also the ones off the region)."""
        kb, top, w, h = (int(v) for v in region)
        top_px = np.full((h - top) * (w - kb), -1, np.int64)
        half = np.zeros(len(top_px), np.uint8)
        fast.note_top(top_px, half, kb, top, w, h, *(np.ascontiguousarray(v, np.int64) for v in rects), bool(shade))
        return top_px, half

    def top_pixels_numpy(self, rects, region, shade=False):
        """((top_px, half), idx): which note is on top at every pixel of region (the last one painted) as its number
        in idx (the notes that reach the region) * 2 + 1 if that pixel is its outline; -1 = no note. half = 1 where
        a note under the top one has its start / end column (not one cut short: NO_END)."""
        kb, top, w, h = region
        iw, ih = w - kb, h - top
        # Same pixels as a canvas rectangle with a 1-pixel outline: x0..x1 and y0..y1 inclusive.
        x0, y0, x1, y1, color = rects
        cx0, cx1 = np.maximum(x0, kb), np.minimum(x1 + 1, w)
        cy0, cy1 = np.maximum(y0, top), np.minimum(y1 + 1, h)
        n, rh = cx1 - cx0, cy1 - cy0
        seen = (n > 0) & (rh > 0)
        idx = np.nonzero(seen)[0]
        x0, y0, x1, y1, cx0, cx1, cy0, cy1, n, rh = (v[seen] for v in (x0, y0, x1, y1, cx0, cx1, cy0, cy1, n, rh))
        real_end = (color[seen] & NO_END) == 0
        solid = (x1 - x0 < 2) | (y1 - y0 < 2)  # too small to have a fill: all outline
        top_px = np.full(ih * iw, -1, np.int64)  # note number * 2 + (1 = outline), the highest wins
        ends = np.full(ih * iw, -1, np.int64)  # the highest note number with its start / end column there
        small = n * rh < 256
        # small notes: pixel by pixel
        i = np.nonzero(small)[0]
        if len(i):
            r = np.repeat(i, rh[i])  # one entry per pixel row of each note
            ry = cy0[r] + np.arange(len(r)) - np.repeat(np.cumsum(rh[i]) - rh[i], rh[i])
            p = np.repeat(np.arange(len(r)), n[r])  # then one per pixel
            ri = r[p]
            x = cx0[ri] + np.arange(len(p)) - np.repeat(np.cumsum(n[r]) - n[r], n[r])
            y = ry[p]
            outline = (solid[ri] | (y == y0[ri]) | (y == y1[ri]) | ((x == x0[ri]) & (cx0[ri] == x0[ri]))
                       | ((x == x1[ri]) & (cx1[ri] == x1[ri] + 1)))
            at = (y - top) * iw + (x - kb)
            np.maximum.at(top_px, at, ri * 2 + outline)
            if shade:
                col = (((x == x0[ri]) & (cx0[ri] == x0[ri])) | ((x == x1[ri]) & (cx1[ri] == x1[ri] + 1)
                                                                & real_end[ri]))
                np.maximum.at(ends, at[col], ri[col])
        # big ones: as blocks (fill first, the outline over it: the same note, so the outline wins)
        grid, egrid = top_px.reshape(ih, iw), ends.reshape(ih, iw)
        big = np.nonzero(~small)[0]
        for k, a, b, c, d, ya, yb, xa, xb, full, real in zip(big.tolist(), *(v[big].tolist() for v in (
                cy0 - top, cy1 - top, cx0 - kb, cx1 - kb, y0 - top, y1 - top, x0 - kb, x1 - kb, solid, real_end))):
            np.maximum(grid[a:b, c:d], k * 2 + full, out=grid[a:b, c:d])
            if shade and c == xa:
                np.maximum(egrid[a:b, c], k, out=egrid[a:b, c])
            if shade and d == xb + 1 and real:
                np.maximum(egrid[a:b, d - 1], k, out=egrid[a:b, d - 1])
            if not full:
                edge = k * 2 + 1
                for yy in (ya, yb):
                    if a <= yy < b:
                        np.maximum(grid[yy, c:d], edge, out=grid[yy, c:d])
                if c == xa:
                    np.maximum(grid[a:b, c], edge, out=grid[a:b, c])
                if d == xb + 1:
                    np.maximum(grid[a:b, d - 1], edge, out=grid[a:b, d - 1])
        # (a fill pixel with a start / end column there: a note under it has it, the top one's would be its outline)
        half = ((top_px >= 0) & ((top_px & 1) == 0) & (ends >= 0)).astype(np.uint8)
        return (top_px, half), idx

    def keep_lines(self, old, key, arrays, draw):
        """The lines draw(tags) makes, kept on the canvas from the last redraw (old: {key: (arrays, tag, filled)})
        when key (which lines, how, the view) and the shape's lines are the same (arrays: its cached_arrays, held so
        their id stays theirs). Returns (tag, made now), None when it drew nothing."""
        key = (key, id(arrays))
        got = old.pop(key, None)
        if key in self._lines:  # (twice the same: drawn, not kept)
            draw(())
            return None
        made = got is None or got[0] is not arrays
        if made:
            if got:
                self.delete(got[1])
            tag = "lines%d" % next(LINE_TAGS)
            draw(("kept", tag))
            got = (arrays, tag, bool(self.find_withtag(tag)))
        self._lines[key] = got
        return (got[1], made) if got[2] else None

    def stack_lines(self, order):
        """Puts the lines keep_lines made now in their place among the kept ones (order: its answers, bottom to
        top): each just above the one before it. Kept ones still in order aren't moved (Tk paints a moved item
        again)."""
        order = [o for o in order if o]
        old, self._line_order = self.__dict__.get("_line_order", []), [tag for tag, _ in order]
        kept = [tag for tag, made in order if not made]
        if not kept:
            if order and self.find_withtag("frame"):  # (all new: under the keyboard + ruler)
                self.tag_lower("kept", "frame")
            return
        was = set(kept)
        same = kept == [tag for tag in old if tag in was]
        for k, (tag, made) in enumerate(order):
            if k and (made or not same):
                self.tag_raise(tag, order[k - 1][0])
            elif made:
                self.tag_lower(tag, kept[0])

    def draw_origins(self, sh, picked, tags=()):
        """A funnel's / custom shape's curves / strokes with a formula as drawn: faint and dashed."""
        knives = piece_knives(sh) if sh["kind"] == "funnel" else None  # (a funnel piece: its side only)
        for path in ((clip_strokes(funnel_origins(sh), knives) if knives else funnel_origins(sh))
                     if sh["kind"] == "funnel" else (p for _, p in self.origin_strokes(sh))):
            self.create_line(*[v for b, p in path for v in (self.t2x(b), self.p2y(p))], width=1,
                             fill=look.ORIGIN_PICKED if picked else look.ORIGIN, dash=(6, 4), tags=tags)

    def draw_path(self, sh, color, width, dash=None, tags=()):
        if "notes" in sh:  # pasted notes: their box, thin and dashed (the notes are the shape)
            width, dash = 1, (4, 3)
        ax, bx = self.sx, self.kb_w - self.view_t * self.sx
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        view = (self.kb_w - 20, self.ruler_h - 20, self.winfo_width() + 20, self.winfo_height() + 20)
        cuts = ([role_of(st) == "cut" for st in sh["strokes"]] if sh["kind"] == "custom" and not sh.get("text")
                else ())
        for k, path in enumerate(cached_arrays(sh)):
            if k < len(cuts) and cuts[k]:  # a fill line (no notes of its own): thin, dashed
                for coords, _ in screen_lines(path, ax, bx, ay, by, view):
                    self.create_line(*coords, fill=color, width=1, dash=(2, 3), tags=tags)
                continue
            for coords, size in screen_lines(path, ax, bx, ay, by, view):
                # (dashed: never one thick line, a thick dashed one reaching far off screen froze Windows)
                if width == 1 or size < 1000 and not dash:
                    self.create_line(*coords, fill=color, width=width, dash=dash, tags=tags)
                    continue
                # a long thick line: Windows paints thick lines with thousands of points very slowly (seconds),
                # thin ones quickly, so it's the thin line a few times, a pixel apart (2 px wide: three times)
                self.create_line(*coords, fill=color, width=1, dash=dash, tags=tags)
                # (spread to both sides, centred like a real thick line: 4 px = -1 .. +2)
                shifts = [k for k in range(-((width - 1) // 2), width // 2 + 1) if k]
                for dx, dy in [(k, 0) for k in shifts] + [(0, k) for k in shifts]:
                    self.create_line(*[v + (dx if n % 2 == 0 else dy) for n, v in enumerate(coords)], fill=color,
                                     width=1, dash=dash, tags=tags)
        if sh["kind"] == "custom" and sh.get("fill") in ("fill", "spam"):
            for (b0, p0), (b1, p1) in gap_lines(sh):  # the straight lines closing gaps in its outline: dashed
                self.create_line(b0 * ax + bx, p0 * ay + by, b1 * ax + bx, p1 * ay + by, fill=color, width=1,
                                 dash=(4, 3), tags=tags)

    def part_colors(self):
        """{(start, wall end): colour} for the highlighted curves: the one clicked purple, the curves linked to it
        teal (so you can tell them apart)."""
        got = self.funnel_parts()
        if not got:
            return {}
        main = self.app.part_main
        main = main[1:] if main and main[0] == "curve" and main[1:] in got[2] else None
        return {c: PART_COLOR if main in (None, c) else TWIN_COLOR for c in got[2]}

    def draw_parts(self):
        """The selected funnel's highlighted lines / curves: thick purple / teal (stands out on the orange selected
        notes), under the handles."""
        got = self.funnel_parts()
        if not got:
            return
        sh, lines, _ = got
        colors = self.part_colors()
        paths = [(funnel_lines(sh)[n], PART_COLOR) for n in lines]
        paths += [(c, colors[k, end]) for k, end, c, _ in funnel_curves(sh) if (k, end) in colors]
        for path, color in paths:
            self.create_line(*[v for b, p in path for v in (self.t2x(b), self.p2y(p))], fill=color,
                             width=max(4, round(4 * self.scale)), capstyle="round", joinstyle="round")

    def centre_mark(self, line=None):
        """The Nested / Tiles / Ripples centre point's picture: a ring with a cross on a white edge, built pixel by
        pixel so it is the same on every side (Tk's lines and ovals land a pixel off from each other). Kept per scale +
        colours. line: its colour (the picked Ripples source's: PART_COLOR)."""
        s, fill, line = self.scale, look.HANDLE_FILL, line or look.HANDLE
        marks = self.__dict__.setdefault("_centre_marks", {})
        if (s, fill, line) in marks:
            return marks[s, fill, line]
        q, c = 6 * s, round(10 * s)  # ring radius, arm length
        half = (max(1, round(1.5 * s)) - 1) // 2  # the arms: 2 * half + 1 px wide (an odd width stays centred)
        ring = half + 0.65  # the ring as thick as the arms
        e = max(1, round(s))  # the white edge
        m = c + e
        n = 2 * m + 1
        on = {(px, py) for py in range(n) for px in range(n)
              if (abs(py - m) <= half or abs(px - m) <= half) and max(abs(px - m), abs(py - m)) <= c
              or abs(math.hypot(px - m, py - m) - q) < ring}
        img = tk.PhotoImage(master=self, width=n, height=n)  # (pixels left unset stay see-through)
        for py in range(n):
            for px in range(n):
                if (px, py) in on:
                    img.put(line, (px, py))
                elif any((px + a, py + b) in on for a in range(-e, e + 1) for b in range(-e, e + 1)):
                    img.put(fill, (px, py))
        marks[s, fill, line] = img
        return img

    def draw_handles(self, sh):
        s = self.scale
        # the handle lines of the anchors: blue on a white edge, so they show on notes
        # a highlighted funnel curve's handles take its colour (purple / teal), so you see which ones shape it
        colors = self.part_colors() if sh["kind"] == "funnel" else {}
        knives = (piece_knives(sh) if sh["kind"] == "funnel" else None) or []
        lines = ([(a, h, HANDLE_COLOR) for a, h in self.curve_handle_lines(sh)] if pen_shape(sh) else
                 [(a, h, colors.get(c, HANDLE_COLOR)) for a, h, c in funnel_handle_lines(sh)
                  if on_side(knives, *a)]  # (a funnel piece: its side's only)
                 if sh["kind"] == "funnel" else
                 [(a, h, HANDLE_COLOR) for a, h in self.stroke_handle_lines(sh)] if sh["kind"] == "custom" else [])
        lines.sort(key=lambda line: line[2] != HANDLE_COLOR)  # the coloured ones on top
        # only what can show (a plain curve made from a pattern has tens of thousands of anchors: seconds a redraw)
        x0, y0 = self.kb_w - 20 * s, self.ruler_h - 20 * s
        x1, y1 = self.winfo_width() + 20 * s, self.winfo_height() + 20 * s
        shown = []
        for a, h, color in lines:
            ax, ay, hx, hy = self.t2x(a[0]), self.p2y(a[1]), self.t2x(h[0]), self.p2y(h[1])
            if not (ax < x0 and hx < x0 or ax > x1 and hx > x1 or ay < y0 and hy < y0 or ay > y1 and hy > y1):
                shown.append((ax, ay, hx, hy, color))
        for width, edge in ((max(3, round(3.5 * s)), True), (max(1, round(1.5 * s)), False)):
            for ax, ay, hx, hy, color in shown:
                self.create_line(ax, ay, hx, hy, fill=look.HANDLE_FILL if edge else color, width=width)
        r = 4 * s
        handles = [hd for hd in self.handles(sh) if x0 <= self.t2x(hd[0]) <= x1 and y0 <= self.p2y(hd[1]) <= y1]
        if colors:
            handles.sort(key=lambda hd: isinstance(hd[2], tuple) and hd[2][0] != "start" and hd[2][1:3] in colors)
        for b, p, i, free in handles:
            x, y = self.t2x(b), self.p2y(p)
            color = HANDLE_COLOR
            if isinstance(i, tuple):
                kind = i[0]
                if kind == "pt":  # a point of a custom shape's stroke (Select tool): a small square
                    q = r - s
                    self.create_rectangle(x - q, y - q, x + q, y + q, fill=look.HANDLE_FILL, outline=STROKE_POINT_COLOR,
                                          width=2)
                    continue
                if kind == "centre":  # Nested's centre point: a ring with a cross, on a white edge
                    self.create_image(round(x), round(y), image=self.centre_mark())
                    continue
                if kind == "ripple":  # a Ripples source's centre point: the same, the picked one coloured
                    picked = i[1] == self.app.ripple_pick
                    self.create_image(round(x), round(y), image=self.centre_mark(PART_COLOR if picked else None))
                    continue
                if kind != "start":
                    color = colors.get(i[1:3], HANDLE_COLOR)
            elif pen_shape(sh):  # a curve's (a bent run's) ends stay squares, like a line's
                kind = "ctrl" if i % 3 else "anchor" if free else None
            elif sh["kind"] == "arc":  # the point an arc passes through: round, like an anchor
                kind = "anchor" if free else None
            else:
                kind = None
            if kind == "anchor":  # an anchor between a curve's ends: round
                q = r + 1.5 * s
                self.create_oval(x - q, y - q, x + q, y + q, fill=look.HANDLE_FILL, outline=color,
                                 width=max(2, round(2 * s)))
                continue
            if kind == "ctrl":  # the end of a handle line: a solid dot with a white edge
                q = r + 0.5 * s
                self.create_oval(x - q, y - q, x + q, y + q, fill=color, outline=look.HANDLE_FILL,
                                 width=max(1, round(s)))
                continue
            if isinstance(i, tuple):  # where a funnel's curves start: a diamond
                d = r + 2
                self.create_polygon(x, y - d, x + d, y, x, y + d, x - d, y, fill=look.HANDLE_FILL, outline=look.HANDLE,
                                    width=2)
                continue
            self.create_rectangle(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL,
                                  outline=look.HANDLE if free else look.HANDLE_FIXED, width=2)

    def draw_draft_points(self):
        """The points of the shape being drawn, like a selected shape's: its ends, a polyline's corners, an arc's
        three points (the middle one round), a box's corners. (A curve's handles come once it's drawn.)"""
        d = self.draft
        kind = d.get("draw") or d["kind"]
        if kind == "free":
            return
        pts = d["pts"]
        if kind == "curve":
            pts = [pts[0], pts[-1]]
        elif d["kind"] == "custom":  # circle / polygon / custom shape: its box's four corners
            (b0, p0), (b1, p1), (b2, p2) = pts
            pts = [pts[0], pts[1], [b1 + b2 - b0, p1 + p2 - p0], pts[2]]
        s = self.scale
        r = 4 * s
        for i, (b, p) in enumerate(pts):
            x, y = self.t2x(b), self.p2y(p)
            if kind == "arc" and i == 1 and len(pts) == 3:
                q = r + 1.5 * s
                self.create_oval(x - q, y - q, x + q, y + q, fill=look.HANDLE_FILL, outline=HANDLE_COLOR,
                                 width=max(2, round(2 * s)))
            else:
                self.create_rectangle(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.HANDLE_FIXED, width=2)

    def draw_keyboard(self, h):
        """Piano keys follow the pitch zoom: black keys on their rows, white-key edges between B/C and E/F."""
        kb, top = self.kb_w, self.ruler_h
        self.create_rectangle(0, top, kb, h, fill=look.KEY_WHITE, outline="", tags=("frame", "kbd"))
        if getattr(self, "room_keys", lambda: 0)():  # (Space past the keys: no keys there, plain like past the roll)
            y_lo, y_hi = round(self.p2y(-0.5)), round(self.p2y(self.app.keys - 0.5))
            if y_lo < h:
                self.create_rectangle(0, y_lo, kb, h, fill=look.OUTSIDE_KEYS, outline="", tags=("frame", "kbd"))
            if y_hi > top:
                self.create_rectangle(0, top, kb, y_hi, fill=look.OUTSIDE_KEYS, outline="", tags=("frame", "kbd"))
        p_lo, p_hi = self.visible_pitches(h)
        font_size = max(7, min(11, int(self.sy * 0.6 / self.scale)))
        # faintly greyed: outside a real 88-key piano; with 256 keys, above the standard 128 instead (31-EDO: the
        # piano's range again, A0 .. C8)
        edo = self.app.edo()
        usual = EDO_PIANO if edo else PIANO_88 if self.app.keys == KEYS[0] else range(KEYS[0])
        size, black, f = (EDO, EDO_BLACK, 13) if edo else (12, BLACK, 5)
        # (31-EDO names every row: shorter keys and smaller in-between names leave them room)
        black_w, half_w = (0.45, 0.35) if edo else (0.6, 0)
        lit = self.app.gliss_picks() or ()  # (Glissando's picked keys, lit while picking: window/panel_gliss.py)
        held = self.held_key  # (a piano key held while picking: its own colour, roll_gliss)
        for p in range(p_lo, p_hi + 1):
            y0, y1 = self.row_y(p)
            n = p % size
            half = edo and n not in EDO_WHITE and n not in black  # (31-EDO's half sharps / flats: narrower, grey)
            if p == held:
                self.create_rectangle(0, y0, kb, y1, fill=look.KEY_HELD, outline="", tags=("frame", "kbd"))
            elif p in lit and n not in black:
                self.create_rectangle(0, y0, kb, y1, fill=look.KEY_PICKED, outline="", tags=("frame", "kbd"))
            elif p not in usual:
                self.create_rectangle(0, y0, kb, y1, fill=look.KEY_OUTSIDE, outline="", tags=("frame", "kbd"))
            if n in black:
                self.create_rectangle(0, y0, kb * black_w, y1, fill=look.KEY_PICKED_BLACK if p in lit else
                                      look.KEY_BLACK if p in usual else look.KEY_BLACK_OUTSIDE, outline="", tags=("frame", "kbd"))
            elif half:
                self.create_rectangle(0, y0, kb * half_w, y1, fill=look.KEY_HALF if p in usual else look.KEY_HALF_OUTSIDE,
                                      outline="", tags=("frame", "kbd"))
            if n in (0, f):  # bottom edge of C and F = white key border
                self.create_line(0, y1, kb, y1, fill=look.KEY_EDGE_C if n == 0 else look.KEY_EDGE_F, tags=("frame", "kbd"))
            white = n not in black and not half  # (31-EDO: every row named once there's room)
            label = note_name(p, edo) if n == 0 and self.sy >= 6 else (
                note_name(p, edo) if (white or edo) and self.sy >= 16 else None)
            if label:
                self.create_text(kb - 3, (y0 + y1) / 2, text=label, anchor="e", fill=look.KEY_TEXT,
                                 font=look.font(font_size if white else min(font_size, 8), "bold" if n == 0 else "normal",
                                                 fit=True),
                                 tags=("frame", "kbd"))
        self.create_line(kb, top, kb, h, fill=look.ROLL_EDGE, tags=("frame", "kbd"))

    def draw_ruler(self, w):
        kb, top = self.kb_w, self.ruler_h
        beats = self.app.beats
        self.create_rectangle(0, 0, w, top, fill=look.RULER_BG, outline="", tags=("frame", "ruler"))
        self.create_line(0, top, w, top, fill=look.ROLL_EDGE, tags=("frame", "ruler"))
        for x, label in self.ruler_marks(w):
            self.create_line(x, top - 6, x, top, fill=look.RULER_TICK, tags=("frame", "ruler"))
            self.create_text(x + 3, self.bar_y(), text=label, anchor="w", fill=look.RULER_TEXT,
                             font=look.font(8, fit=True), tags=("frame", "ruler"))
        self.create_rectangle(0, 0, kb, top, fill=look.RULER_BG, outline="", tags=("frame", "ruler"))
        mark = self.track_mark()
        self.mark_full = ""
        if mark:  # (only one track shows: its name in the corner, so hidden notes never look lost)
            font = look.font(8, "bold", fit=True)
            measure, room = tkfont.Font(font=font).measure, kb - 6 * self.scale
            shown = mark
            if measure(mark) > room:  # (a long name: cut with "…", never over the ruler; the whole name in a tip)
                while len(shown) > 1 and measure(shown + "…") > room:
                    shown = shown[:-1]
                shown += "…"
                self.mark_full = mark
            self.create_text(kb / 2, top / 2, text=shown, fill=look.TRACK_PICK, font=font,
                             tags=("frame", "ruler", "trackmark"))

    def track_mark(self):
        """The corner's text: the track the track list highlights ("Track 3"), else "All tracks" (user 2026-10-26:
        always saying where new shapes go)."""
        tl = getattr(self.app, "track_list", None)
        if tl is None or tl.current is None:
            return tr("tracks.all")
        row = tl.row_of_id(tl.current)
        if row is None:
            return ""
        ch = tl.shown_ch()  # (one channel of it: a folder's sub-row clicked)
        if ch is None:
            return tl.name_of(row)
        pairs = tl.shown_pairs() or set()  # (several Ch rows picked, E3: this track's channels + how many others)
        mine = sorted(c for s, c in pairs if s == tl.current) or [ch]
        mark = tr("tracks.mark_ch", name=tl.name_of(row), ch=", ".join(str(c + 1) for c in mine))
        others = len({s for s, _ in pairs} - {tl.current})
        return trn("tracks.mark_more", others, mark=mark, n=others) if others else mark

    def bar_y(self):
        """The bar numbers' middle: the ruler's bottom half, by their ticks (the tempo changes go above them)."""
        return self.ruler_h - 9 * self.scale

    def tempo_layout(self):
        """[(x, text, [tip lines], colour, x the faint line runs to or None, where the purple part of the text
        starts or None)] of the tempo changes on the ruler (user 2026-10-22): a mark at each Tempo pane point /
        catch-up line / run of stairs (off screen too: their room is worked out from the song's start, so scrolling
        never moves them), the text BPMs to one decimal (tip: the full ones); a line only where the tempo moves over
        a stretch (glides, catch-ups, stairs), none after a jump. Inside a Keep timing part the tempo heard there
        follows in purple brackets when it differs (user 2026-10-29: "120 → 60 (120)", never a second arrow)."""
        pane = getattr(self.app, "tempo_pane", None)
        if pane is None:
            return []
        out = []
        args = (self.app.tempo, pane.base(), self.app.ppq, self.app.beats)
        marks = ruler_marks(*args)
        beats = [m[0] for m in marks if m[1] == "point"]
        kept = dict(zip(beats, kept_bpms(*args[:3], beats)))
        # (the heard tempo lines' own points too, in purple: user 2026-10-24)
        for b, kind, v, until, *steps in marks + heard_marks(*args):
            x = b * self.sx  # (from the song's start: scrolling only slides them)
            ux = None if until is None else until * self.sx
            if kind == "catch":
                tip = tr("tempo.mark_catch", bpm=bpm_text(v)) if v is not None else tr("tempo.not_possible")
                out.append((x, tr("tempo.catch"), [tip], look.TEMPO_CATCH if v is not None else look.ERROR, ux, None))
                continue
            colour, heard = (look.TEMPO_KEEP, tr("tempo.mark_heard") + " ") if kind == "heard" else (look.RULER_TEMPO, "")
            before, at, then = v
            if steps:  # (stairs: before -> first step, then the climb; tip = the whole, then each step)
                steps, way = steps[0], "↗" if then > before else "↘"
                text = f"{short_bpm(before)} {way} {short_bpm(then)}"
                tips = [heard + trn("tempo.mark_steps", len(steps), bpm=f"{bpm_text(before)} {way} {bpm_text(then)}",
                                    n=f"{len(steps):,}")]
                tips += [f"{bpm_text(a)} → {bpm_text(c)} BPM" for _, a, c in steps]
            else:
                short, full = [], []
                if before is not None and abs(before - at) > 1e-6 * at:
                    short.append(f"{short_bpm(before)} → {short_bpm(at)}")
                    full.append(f"{bpm_text(before)} → {bpm_text(at)}")
                if then is not None:
                    way = "↗" if then > at else "↘"
                    short.append(f"{short_bpm(at)} {way} {short_bpm(then)}" if not short else f"{way} {short_bpm(then)}")
                    full.append(f"{bpm_text(at)} {way} {bpm_text(then)}" if not full else f"{way} {bpm_text(then)}")
                text, tips = " ".join(short), [heard + (" ".join(full) or bpm_text(at)) + " BPM"]
            h, tail = kept.get(b) if kind == "point" else None, None
            if h is not None and not (abs(h - at) <= 1e-4 * at and (then is None or abs(h - then) <= 1e-4 * then)):
                tail = len(text) + 1 if text else 0  # (the heard BPM the same as the visual: no brackets)
                text = f"{text} ({short_bpm(h)})" if text else f"({short_bpm(h)})"
                tips.append(tr("tempo.mark_kept", bpm=bpm_text(h)))
            out.append((x, text, tips, colour, ux, tail))
        return sorted(out, key=lambda m: m[0])

    def tempo_shown(self, key, measure):
        """The ruler's tempo marks laid out from the song's start: [[x, text, [tip lines], colour, text's right end,
        x its faint line runs to or None, where its purple part starts or None]]; a change landing on the text
        before isn't shown (its tip joins that one's, the line runs on to the later end); one landing on a mark with
        no text takes its place. Kept while only the view scrolls."""
        if getattr(self, "_tempo_laid", (None,))[0] == key:
            return self._tempo_laid[1]
        s = self.scale
        later = lambda a, b: b if a is None else a if b is None else max(a, b)  # noqa: E731
        shown, end = [], -math.inf
        for x, text, tip, colour, ux, tail in self.tempo_layout():
            tips = list(tip)
            if shown and x < end:
                if shown[-1][1]:  # (on the text before: joins its tip)
                    shown[-1][2].extend(tip)
                    shown[-1][5] = later(shown[-1][5], ux)
                    continue
                gone = shown.pop()  # (on a mark with no text: this one, saying more, takes its place)
                tips, ux = gone[2] + tips, later(gone[5], ux)
            right = x + 3 * s + measure(text) if text else x
            shown.append([x, text, tips, colour, right, ux, tail])
            end = right + 4 * s if text else x + 2 * s
        self._tempo_laid = (key, shown)
        return shown

    def draw_tempo_marks(self, boxes):
        """The tempo changes above the bar numbers: a light line on the exact tick, its text right of it, then a
        fainter line while the tempo moves (a glide / catch-up / stairs; user 2026-10-22: none after a jump, it just
        stays) up to its end or the next change. A change landing on the one
        before's text isn't drawn (zoomed out: its BPMs join that one's tip). Ones scrolled past the keyboard slide
        under it. White over a ruler band. Kept while nothing they show changed."""
        if self.sx is None:
            return
        w, top, s, kb = self.winfo_width(), self.ruler_h, self.scale, self.kb_w
        bands = tuple(sorted(sorted((self.t2x(a[0]), self.t2x(a[2]))) for a in black_bands(boxes)))
        pane = getattr(self.app, "tempo_pane", None)
        key = (tempo_text(self.app.tempo), pane and pane.base(), self.app.ppq, w, top, kb, self.sx,
               self.view_t, s, bands)
        if key == self._tempo_key and self.find_withtag("tempomark"):
            return
        if pane is not None and pane.drag and getattr(self, "_tempo_slow", False) and self.find_withtag("tempomark") \
                and self._tempo_key and key[1:] == self._tempo_key[1:]:
            return  # (a Conductor pane drag over a big tempo map: the marks as they were until let go, hunt
            #         2026-10-24: ~0.9 s a mouse move at 20,000 points; on_release draws them)
        self.delete("tempomark")
        self._tempo_key, self.tempo_tips = key, []
        font = look.font(7, fit=True)
        measure = self.cc_measure(font)
        t0 = time.perf_counter()
        shown = self.tempo_shown(key[:3] + (self.sx, s, font), measure)
        if self._tempo_laid[0] != getattr(self, "_tempo_timed", None):  # (laid out just now, not kept: timed)
            self._tempo_timed, self._tempo_slow = self._tempo_laid[0], time.perf_counter() - t0 > 0.05
        at = self.t2x(0)  # (the layout is from the song's start: scrolling slides it, hunt 2026-10-22: 0.12 s a
        y, tags = 8 * s, ("frame", "tempomark")  # step at 2000 points laid out again)
        first = max(0, bisect.bisect_left([m[4] for m in shown], kb - at) - 1)  # (skip what's far left)
        for i in range(first, len(shown)):
            u, text, tips, colour, right, ux, tail = shown[i]
            tint = lambda k: colour if tail is None or k < tail else look.TEMPO_KEEP  # noqa: E731 (purple brackets)
            x, right = u + at, right + at
            if x > w:
                break
            nxt = shown[i + 1][0] + at if i + 1 < len(shown) else None
            if right < kb and (nxt is None or nxt < kb):
                continue
            pick = lambda px: any(x0 <= px < x1 for x0, x1 in bands)  # noqa: E731
            if x >= kb - 1:  # (on the exact tick, down to the bar line)
                self.create_line(x, 0, x, top, fill=look.RULER_PICK_TEXT if pick(x) else colour, tags=tags)
            if text and any(x0 < right and x1 > x for x0, x1 in bands):
                # (part under a ruler band: each letter white where it is, hunt 2026-10-22: all white past the band
                # couldn't be read; letter by letter = Tk has no clipped text)
                left = x + 3 * s
                for k, ch in enumerate(text):
                    cx = left + measure(text[:k])
                    mid = cx + measure(ch) / 2
                    self.create_text(cx, y, text=ch, anchor="w", font=font, tags=tags,
                                     fill=look.RULER_PICK_TEXT if pick(mid) else tint(k))
            elif text:
                head = text if tail is None else text[:tail]
                if head.strip():
                    self.create_text(x + 3 * s, y, text=head, anchor="w", fill=colour, font=font, tags=tags)
                if tail is not None:
                    self.create_text(x + 3 * s + measure(head), y, text=text[tail:], anchor="w",
                                     fill=look.TEMPO_KEEP, font=font, tags=tags)
            tip = "\n".join(tips[:TIP_MARKS])
            if len(tips) > TIP_MARKS:  # (zoomed far out: a short tip, hunt 2026-10-22: 478 lines)
                tip += "\n" + trn("tempo.more_changes", len(tips) - TIP_MARKS, n=f"{len(tips) - TIP_MARKS:,}")
            self.tempo_tips.append((max(kb, x - 4 * s), max(right, x + 4 * s), tip))
            if ux is not None:  # (how long the tempo moves)
                x1 = min(w, ux + at if nxt is None or ux + at < nxt else nxt - 3 * s)
                x0 = max(kb - 2, (right + 3 * s) if text else x + 1)
                if x1 > x0:
                    self.create_line(x0, round(y), x1, round(y), fill=look.RULER_TEMPO_LINE, tags=tags)
        if shown:  # the keyboard's corner covers what slid past it
            self.create_rectangle(0, 0, kb, top, fill=look.RULER_BG, outline="", tags=tags)
            self.tag_raise("trackmark")
        self.tag_raise("playhead")

    def ruler_marks(self, w):
        """(x, label) of each bar number on the ruler, from the keyboard to w (by the time signatures); where a
        time / key signature starts its name follows: "5 (3/4, Ab Maj)" (user 2026-10-22, like many MIDI programs).
        A number that would run into the label before is left out."""
        tempo = self.app.tempo
        sigs, keys = sigs_of(tempo), tempo.get("keysigs") or []
        lines = bar_lines(sigs, max(0.0, self.x2t(self.kb_w)), self.x2t(w), 40 / self.sx)
        times = {round(s[0], 6): sig_text(s) for s in sigs}
        names = {round(k[0], 6): key_name(k[1], k[2]) for k in keys}
        measure = tkfont.Font(font=look.font(8, fit=True)).measure
        out, end = [], -math.inf
        for b, n in lines:
            x = self.t2x(b)
            extra = [t for t in (times.get(round(b, 6)), names.get(round(b, 6))) if t]
            label = f"{n + 1} ({', '.join(extra)})" if extra else str(n + 1)
            if x < self.kb_w or x < end:
                continue
            out.append((x, label))
            end = x + 3 + measure(label) + 4
        return out

    def draw_ruler_pick(self, boxes):
        """The ruler black under the Select boxes that hold every key (a drag along the ruler: like many MIDI
        programs), its numbers white there: each letter by where it is (user 2026-10-22: a band starting right of a
        bar line hid its number; Tk has no clipped text)."""
        w, top, tags = self.winfo_width(), self.ruler_h, ("selbox", "rulerpick")
        bands = []
        for x0, x1 in sorted(sorted((self.t2x(a[0]), self.t2x(a[2]))) for a in black_bands(boxes)):
            x0, x1 = max(x0, self.kb_w), min(x1, w)
            if x0 < x1:
                bands.append((x0, x1))
                self.create_rectangle(x0, 0, x1, top, fill=look.RULER_PICK, outline="", tags=tags)
        if bands:
            font = look.font(8, fit=True)
            measure = tkfont.Font(font=font).measure
            pick = lambda px: any(x0 <= px < x1 for x0, x1 in bands)  # noqa: E731
            for x, label in self.ruler_marks(w):
                left, right = x + 3, x + 3 + measure(label)
                if not any(x0 < right and x1 > x for x0, x1 in bands):
                    continue
                if pick(x):
                    self.create_line(x, top - 6, x, top, fill=look.RULER_PICK_TEXT, tags=tags)
                for k, ch in enumerate(label):
                    cx = left + measure(label[:k])
                    if any(x0 < cx + measure(ch) and x1 > cx for x0, x1 in bands):
                        self.create_text(cx, self.bar_y(), text=ch, anchor="w", font=font, tags=tags,
                                         fill=look.RULER_PICK_TEXT if pick(cx + measure(ch) / 2) else look.RULER_TEXT)
        self.draw_tempo_marks(boxes)
        self.draw_cc_marks(boxes)
        self.tag_raise("tempomark")
        self.tag_raise("ccmark")
        self.tag_raise("trackmark")  # (over the corner's patch: a Select box drag hid the track's name, user 10-26)
        self.tag_raise("playhead")

    def cc_y(self):
        """The ruler's CC row's middle: under the tempo changes, over the bar numbers (user 2026-10-29)."""
        return 18 * self.scale

    def cc_events(self):
        """The CC events the CC row shows, worked out once per notes change: {slot: {kind: [(tick, value)]}} of
        App.shown_cc, + each slot's channel."""
        got = self.app.shown_cc()
        memo = getattr(self, "_cc_events", None)
        if memo is not None and memo[0] is got:
            return memo[1], memo[2]
        from notes.cc_names import kind_of, value_of
        by, slots = {}, [s for s, ms in got.items() if ms]
        for s in slots:
            runs = by[s] = {}
            for m in got[s]:
                k = kind_of(m[1], m[2]) if m[1] is not None else None
                if k is not None:
                    runs.setdefault(k, []).append((m[0], value_of(m[1], m[2], m[3])))
        chans = dict(zip(slots, channels_of(slots, self.app.picture_use10).tolist())) if slots else {}
        self._cc_events = (got, by, chans)
        return by, chans

    def cc_shown_slots(self, slots):
        """The slots whose CC events the row shows: all with All tracks highlighted, else the highlighted track's
        (a Ch row: that channel's), like the piano roll's notes (user 2026-10-29)."""
        tl = getattr(self.app, "track_list", None)
        if tl is None or tl.current is None or not slots:
            return list(slots)
        ids = track_ids(slots).tolist()
        ch = tl.shown_ch()
        chans = channels_of(slots, self.app.picture_use10).tolist()
        return [s for s, i, c in zip(slots, ids, chans) if i == tl.current and (ch is None or c == ch)]

    def loaded_cc(self):
        """[(file track number, ticks, codes, the channel it shows on or None = each event's own)] of the loaded MIDI's
        own CC events the row shows (user 2026-10-29; kept by its loading read, files/midi_index): All tracks = every
        listed track, a loaded track / part / Ch row highlighted = its own (that channel's), any other = none."""
        lm, tl = self.app.loaded, getattr(self.app, "track_list", None)
        if lm is None or tl is None:
            self._loaded_cc = None  # (never holding a MIDI let go of: New, Open; hunt 2026-10-29)
            return NO_CC
        rows = tl.tracks()
        key = (lm, rows, tl.current, tl.shown_ch(), lm.hide_ver, id(lm.rechannel))
        memo = getattr(self, "_loaded_cc", None)
        if memo is not None and memo[0][0] is lm and memo[0][1] is rows and memo[0][2:] == key[2:]:
            return memo[1]
        ids = [tl.current] if tl.current is not None else [s for s in rows.tolist() if s < 0]
        ch = tl.shown_ch() if tl.current is not None else None
        moved, out = lm.rechannel, []
        for s in ids:
            if s >= 0:
                continue
            k, only = tl.file_part(int(s))
            if k >= len(lm.tracks) or not len(lm.tracks[k].c_tick):
                continue
            to = int(moved[k]) if moved is not None and k < len(moved) and moved[k] >= 0 else None
            ticks, codes = lm.tracks[k].c_tick, lm.tracks[k].c_code
            keep = np.isin(codes & 15, list(only)) if only is not None else None
            if ch is not None:
                on = np.full(len(codes), ch == to) if to is not None else (codes & 15) == ch
                keep = on if keep is None else keep & on
            if keep is not None:
                ticks, codes = ticks[keep], codes[keep]
            if len(ticks):
                out.append((k, ticks, codes, to))
        self._loaded_cc = (key, out)
        return out

    def loaded_cc_over(self):
        """The loaded MIDI has more CC events than are kept (files/midi_index CC_MOST) and the row would show its."""
        lm, tl = self.app.loaded, getattr(self.app, "track_list", None)
        return bool(lm is not None and lm.cc_over and (tl is None or tl.current is None or tl.current < 0))

    def cc_data(self):
        """Every CC event the row shows, Spiderweb's own (cc_shown_slots) and the loaded MIDI's (loaded_cc), as one list
        sorted by tick: (ticks, kind numbers (file_kinds'), values, source numbers, [(track, channel)] per source
        number; track = a track id, or -1 - a loaded track's number). Worked out once per change of them or the
        highlight and kept (hunt 2026-10-29: worked out at each pan step it took 100-190 ms with busy MIDIs)."""
        got, loaded = self.app.shown_cc(), self.loaded_cc()
        tl = getattr(self.app, "track_list", None)
        lit = (tl.current, tl.shown_ch()) if tl is not None else None
        memo = getattr(self, "_cc_data", None)
        if memo is not None and memo[0] is got and memo[1] is loaded and memo[2] == lit:
            return memo[3]
        by, chans = self.cc_events()
        srcs, ts, ks, vs, ss = [], [], [], [], []
        slots = self.cc_shown_slots(list(by))
        for sl, tid in zip(slots, track_ids(slots).tolist() if slots else []):
            srcs.append((tid, chans[sl]))
            for k, evs in by[sl].items():
                ts.append(np.array([t for t, _ in evs], np.int64))
                vs.append(np.array([v for _, v in evs], np.int32))
                ks.append(np.full(len(evs), kind_number(k), np.uint8))
                ss.append(np.full(len(evs), len(srcs) - 1, np.int32))
        own = sorted_cc(ts, ks, vs, ss)
        lt, lk, lv, ls, lsrcs = self.loaded_cc_data(loaded)  # (kept apart: an edit of the shapes never sorts the
        # loaded MIDI's again, speed hunt 2026-10-29: 0.15 s an edit on a MIDI with many CC events)
        if not len(lt):
            arrays = own
        elif not len(own[0]):
            arrays = [lt, lk, lv, ls + len(srcs)]
        else:  # (one tick order for all, own ones first on a tick: as one stable sort of own + loaded gave)
            at = np.searchsorted(lt, own[0], side="left") + np.arange(len(own[0]))
            mine = np.zeros(len(lt) + len(own[0]), bool)
            mine[at] = True
            arrays = []
            for o, l in zip(own, (lt, lk, lv, ls + len(srcs))):
                a = np.empty(len(mine), o.dtype)
                a[mine], a[~mine] = o, l
                arrays.append(a)
        data = (*arrays, srcs + lsrcs)
        self._cc_data = (got, loaded, lit, data)
        return data

    def loaded_cc_data(self, loaded):
        """cc_data's part for the loaded MIDI's events (loaded_cc), sorted, its source numbers from 0, kept while
        loaded_cc gives the same list."""
        memo = getattr(self, "_loaded_cc_data", None)
        if memo is not None and memo[0] is loaded:
            return memo[1]
        srcs, ts, ks, vs, ss = [], [], [], [], []
        for n, ticks, codes, to in loaded:
            c = codes.astype(np.int64)
            d1, d2, hi = c >> 8 & 127, c >> 16 & 127, c & 0xF0
            ts.append(ticks)
            vs.append(np.select([hi == 0xE0, hi == 0xC0, hi == 0xD0], [(d1 | d2 << 7) - 8192, d1 + 1, d1], d2)
                      .astype(np.int32))
            ks.append(file_kinds(c).astype(np.uint8))
            ss.append((len(srcs) + (c & 15)).astype(np.int32))
            srcs += [(-1 - n, ch if to is None else to) for ch in range(16)]
        got = (*sorted_cc(ts, ks, vs, ss), srcs)
        self._loaded_cc_data = (loaded, got)
        return got

    def cc_span(self, lo, hi):
        """(first, end) of the CC events (cc_data) on ticks lo..hi (decimal ticks: whole bounds for the search, so it
        never turns the whole list into decimals)."""
        t = self.cc_data()[0]
        return (int(np.searchsorted(t, np.int64(math.ceil(lo)), "left")),
                int(np.searchsorted(t, np.int64(math.floor(hi)), "right")))

    def cc_entries(self, span):
        """How many different events (tick + kind + value) the CC events first..end (cc_data) hold: the same one on
        many tracks / channels is written once (same_merged), so 2,000 tracks setting up on tick 0 are 3 entries, not
        6,000 (hunt 2026-10-29). Kept while the same ones are on screen."""
        t, k, v, _, _ = self.cc_data()
        memo = getattr(self, "_cc_entries", None)
        if memo is not None and memo[0] is t and memo[1] == span:
            return memo[2]
        a, b = span
        n = len(np.unique(t[a:b] << 22 | k[a:b].astype(np.int64) << 14 | (v[a:b].astype(np.int64) + 8192)))
        self._cc_entries = (t, span, n)
        return n

    def cc_sources(self, lo=None, hi=None):
        """[(track, channel, kind, [(tick, value)])] the CC row shows (cc_data), ticks lo..hi only when given."""
        from notes.cc_names import file_kind
        t, k, v, s, srcs = self.cc_data()
        a, b = (0, len(t)) if lo is None else self.cc_span(lo, hi)
        runs = {}
        for tt, kk, vv, ss in zip(t[a:b].tolist(), k[a:b].tolist(), v[a:b].tolist(), s[a:b].tolist()):
            runs.setdefault((ss, kk), []).append((tt, vv))
        return [(srcs[ss][0], srcs[ss][1], file_kind(kk), evs) for (ss, kk), evs in runs.items()]

    def cc_layout(self, lo=None, hi=None):
        """[(x from the song's start, text, [tip lines], x its faint line runs to or None)] of the CC events Spiderweb
        sends (App.shown_cc; user 2026-10-29, CC events never silent) and a loaded MIDI's own (cc_sources), ticks
        lo..hi only when given: one mark per tick, the 2nd and later events listed after the first ("Volume 100, Pan
        64"); a run of 3+ events of one kind on one track going one way, each within a beat of the one before = ONE
        mark "Volume 100 ↘ 40" (like the tempo stairs); the same one on several tracks = once, "Volume 127 (2,000
        tracks)" (user 2026-10-29: songs set every track up at the start)."""
        from notes.cc_names import kind_name
        ppq = self.app.ppq
        at = {}  # tick -> [(text, tip, end tick or None, (track, channel))]
        for tid, ch, k, evs in self.cc_sources(lo, hi):
            name, ch = kind_name(k), ch + 1
            i = 0
            while i < len(evs):
                j = i + 1  # (a run: one way, each within a beat of the one before)
                while (j < len(evs) and evs[j][0] - evs[j - 1][0] <= ppq and evs[j][1] != evs[j - 1][1]
                       and (j == i + 1 or (evs[j][1] > evs[j - 1][1]) == (evs[i + 1][1] > evs[i][1]))):
                    j += 1
                if j - i >= 3:
                    a, b = evs[i][1], evs[j - 1][1]
                    way = "↗" if b > a else "↘"
                    at.setdefault(evs[i][0], []).append(
                        (f"{name} {a} {way} {b}", tr("cc.tip_run", ch=ch, name=name, a=a, way=way, b=b, n=j - i),
                         evs[j - 1][0], (tid, ch)))
                    i = j
                    continue
                t, v = evs[i]
                at.setdefault(t, []).append((f"{name} {v}", tr("cc.tip_one", ch=ch, name=name, v=v), None, (tid, ch)))
                i += 1
        out = []
        for t in sorted(at):
            items = same_merged(at[t])
            ends = [e for _, _, e in items if e is not None]
            out.append((t / ppq * self.sx, ", ".join(x for x, _, _ in items), [tip for _, tip, _ in items],
                        max(ends) / ppq * self.sx if ends else None))
        return out

    def cc_pixels(self):
        """Each CC event's pixel from the song's start at this zoom (cc_data's order: sorted too), worked out once per
        zoom (hunt 2026-10-29: per pan step it was most of the lag)."""
        t = self.cc_data()[0]
        memo = getattr(self, "_cc_px", None)
        if memo is not None and memo[0] is t and memo[1] == (self.sx, self.app.ppq):
            return memo[2]
        u = np.floor(t * (self.sx / self.app.ppq)).astype(np.int64)
        self._cc_px = (t, (self.sx, self.app.ppq), u)
        return u

    def cc_measure(self, font):
        """font's text width, each text measured once per font (hunt 2026-10-29: ~0.2 ms a measure, dozens a pan
        step; the tempo marks measured ~24,000 a Drawn catch-up graph step at 8,000 points, speed hunt)."""
        fonts = self.__dict__.setdefault("_measures", {})
        got = fonts.get(font)
        if got is None:
            measured, widths = tkfont.Font(font=font).measure, {}

            def measure(text):
                got = widths.get(text)
                if got is None:
                    if len(widths) > 20000:
                        widths.clear()
                    got = widths[text] = measured(text)
                return got
            got = fonts[font] = measure
        return got

    def draw_cc_crowd(self, y, half, tags, font, measure, bands):
        """Too many CC events on screen to write: a mark on each pixel holding some (pixels side by side = one
        stretch: a few canvas items, not thousands, hunt 2026-10-29), and per stretch as wide as its text one "1,234
        CC events" + a tip naming their kinds (user 2026-10-29). Stretches counted from the song's start, so scrolling
        only slides them; inside a ruler band they're white on its black, like the written marks."""
        from notes.cc_names import file_kind, kinds_text
        from files.midi_index import CC_KINDS
        w, s, kb = self.winfo_width(), self.scale, self.kb_w
        u, kinds = self.cc_pixels(), self.cc_data()[1]
        off = int(round(kb - self.view_t * self.sx))  # (screen x = u + off)
        # (from one pixel left of the keyboard's edge, like the written marks: an edge ending early in a pixel dropped
        # the song's first tick, hunt 2026-10-29)
        a = int(np.searchsorted(u, math.floor(kb) - 1 - off, "left"))
        b = int(np.searchsorted(u, w - off, "right"))
        us = u[a:b]
        if not len(us):
            return
        pick = lambda px: any(x0 <= px < x1 for x0, x1 in bands)  # noqa: E731
        edges = sorted({math.ceil(e) for band in bands for e in band})
        cut = np.flatnonzero(np.diff(us) > 1) + 1
        for x0, x1 in zip((us[np.r_[0, cut]] + off).tolist(), (us[np.r_[cut - 1, len(us) - 1]] + off + 1).tolist()):
            stops = [x0] + [e for e in edges if x0 < e < x1] + [x1]
            for p0, p1 in zip(stops, stops[1:]):
                self.create_rectangle(p0, y - half, p1, y + half, outline="", tags=tags,
                                      fill=look.RULER_PICK_TEXT if pick(p0) else look.CC_MARK)
        width = max(1, int(measure(trn("cc.packed", 88888, n="88,888")) + 8 * s))
        shown, end = [], -math.inf  # [[x, count, {kind: n}]]
        for c in range(int(us[0]) // width, int(us[-1]) // width + 1):
            ca, cb = (int(i) for i in np.searchsorted(us, [c * width, (c + 1) * width], "left"))
            if cb <= ca:
                continue
            x0 = int(us[ca]) + off
            row = np.bincount(kinds[a + ca:a + cb], minlength=CC_KINDS)
            counts = {file_kind(k): int(row[k]) for k in np.flatnonzero(row).tolist()}
            n = cb - ca
            if shown and x0 < end:  # (onto the text before: counted there)
                shown[-1][1] += n
                for k, m in counts.items():
                    shown[-1][2][k] = shown[-1][2].get(k, 0) + m
                continue
            shown.append([x0, n, counts])
            end = x0 + 3 * s + measure(trn("cc.packed", n, n=f"{n:,}")) + 4 * s
        for i, (x0, n, counts) in enumerate(shown):
            text = trn("cc.packed", n, n=f"{n:,}")
            inside = pick(x0 + 3 * s)
            # (on the ruler's own colour, or the band's: the marks under it run together into a bar)
            self.create_rectangle(x0 + 2 * s, y - half, x0 + 4 * s + measure(text), y + half, outline="", tags=tags,
                                  fill=look.RULER_PICK if inside else look.RULER_BG)
            self.create_text(x0 + 3 * s, y, text=text, anchor="w", font=font, tags=tags,
                             fill=look.RULER_PICK_TEXT if inside else look.CC_MARK)
            x1 = shown[i + 1][0] if i + 1 < len(shown) else w
            self.cc_tips.append((max(kb, x0 - 4 * s), max(x1, x0 + 4 * s),
                                 trn("cc.crowded", n, n=f"{n:,}") + "\n" + kinds_text(counts)))

    def draw_cc_marks(self, boxes):
        """The CC row of the ruler: a short line on each CC event's tick, its name + value right of it, a faint line
        while a run goes on; one landing on the text before isn't drawn (its tip joins that one's). Kept while
        nothing they show changed (laid out from the song's start: scrolling only slides them)."""
        if self.sx is None:
            return
        w, top, s, kb = self.winfo_width(), self.ruler_h, self.scale, self.kb_w
        bands = tuple(sorted(sorted((self.t2x(a[0]), self.t2x(a[2]))) for a in black_bands(boxes)))
        got = self.app.shown_cc()
        tl = getattr(self.app, "track_list", None)
        lit = (tl.current, tl.shown_ch()) if tl is not None else None
        loaded, over = self.loaded_cc(), self.loaded_cc_over()
        key = (id(got), id(loaded), over, self.app.ppq, w, top, kb, self.sx, self.view_t, s, bands, lit)
        if key == getattr(self, "_cc_key", None) and (not (got or loaded or over) or self.find_withtag("ccmark")):
            return  # (the frame redrawn deletes them: drawn again then)
        self.delete("ccmark")
        self._cc_key, self.cc_tips = key, []
        if not (got or loaded or over):
            return
        y, half, tags = self.cc_y(), 5 * s, ("frame", "ccmark")
        font = look.font(7, fit=True)
        measure = self.cc_measure(font)
        if over:  # (more than are kept: said, user 2026-10-29; the track list still counts them)
            text = tr("cc.too_many")
            self.create_text(kb + 3 * s, y, text=text, anchor="w", fill=look.CC_MARK, font=font, tags=tags)
            self.cc_tips.append((kb, kb + 3 * s + measure(text), tr("cc.too_many_tip", n=f"{CC_MOST:,}")))
        ppq = self.app.ppq
        lo, hi = self.x2t(kb) * ppq, self.x2t(w) * ppq
        total = len(self.cc_data()[0])
        # (many events: only the ones on screen laid out, and past CC_CROWD of those plain marks + counts, hunt
        # 2026-10-29: 300,000 events froze every zoom step 1.3 s)
        near = span = None
        if total > CC_CROWD:
            span = self.cc_span(lo, hi)
            near = span[1] - span[0]
            if CC_CROWD < near <= CC_MERGE_MOST:  # (the same setup on many tracks writes as one: count those)
                near = self.cc_entries(span)
        if near is not None and near > CC_CROWD:
            self.draw_cc_crowd(y, half, tags, font, measure, bands)
            self.create_rectangle(0, y - half - 1, kb, y + half + 1, fill=look.RULER_BG, outline="", tags=tags)
            self.tag_raise("trackmark")
            return
        laid = getattr(self, "_cc_laid", (None,))
        # (laid out from the song's start: kept while the same events are on screen)
        lay_key = key[:4] + (self.sx, s, lit, id(self.cc_data()[0])) + ((span,) if near is not None else ())
        if laid[0] != lay_key:  # (few events: laid out from the song's start, kept while only scrolling)
            shown, end = [], -math.inf
            for x, text, tips, ux in (self.cc_layout() if near is None else self.cc_layout(lo, hi)):
                if shown and x < end:  # (on the text before: joins its tip, its line runs on)
                    shown[-1][2].extend(tips)
                    if ux is not None:
                        shown[-1][4] = ux if shown[-1][4] is None else max(shown[-1][4], ux)
                    continue
                right = x + 3 * s + measure(text)
                shown.append([x, text, list(tips), right, ux])
                end = right + 4 * s
            laid = self._cc_laid = (lay_key, shown)
        shown, at = laid[1], self.t2x(0)
        pick = lambda px: any(x0 <= px < x1 for x0, x1 in bands)  # noqa: E731
        first = max(0, bisect.bisect_left([m[3] for m in shown], kb - at) - 1)
        for i in range(first, len(shown)):
            u, text, tips, right, ux = shown[i]
            x, right = u + at, right + at
            if x > w:
                break
            if x >= kb - 1:
                self.create_line(x, y - half, x, y + half, fill=look.RULER_PICK_TEXT if pick(x) else look.CC_MARK,
                                 tags=tags)
            if any(x0 < right and x1 > x for x0, x1 in bands):
                # (part under a ruler band: each letter white where it is, like the tempo marks; hunt 2026-10-29)
                left = x + 3 * s
                for k, ch in enumerate(text):
                    cx = left + measure(text[:k])
                    self.create_text(cx, y, text=ch, anchor="w", font=font, tags=tags,
                                     fill=look.RULER_PICK_TEXT if pick(cx + measure(ch) / 2) else look.CC_MARK)
            else:
                self.create_text(x + 3 * s, y, text=text, anchor="w", fill=look.CC_MARK, font=font, tags=tags)
            counted = {}  # (the same line once, with how many times: marks joined onto one repeat it)
            for line in tips:
                counted[line] = counted.get(line, 0) + 1
            lines = [line if n == 1 else tr("cc.times", text=line, n=f"{n:,}") for line, n in counted.items()]
            tip = "\n".join(lines[:TIP_MARKS])
            more = sum(list(counted.values())[TIP_MARKS:])
            if more:
                tip += "\n" + trn("cc.more", more, n=f"{more:,}")
            self.cc_tips.append((max(kb, x - 4 * s), max(right, x + 4 * s), tip))
            if ux is not None:
                nxt = shown[i + 1][0] + at if i + 1 < len(shown) else None
                x1 = min(w, ux + at if nxt is None or ux + at < nxt else nxt - 3 * s)
                if x1 > right + 3 * s:
                    self.create_line(right + 3 * s, round(y), x1, round(y), fill=look.RULER_TEMPO_LINE, tags=tags)
        self.create_rectangle(0, y - half - 1, kb, y + half + 1, fill=look.RULER_BG, outline="", tags=tags)
        self.tag_raise("trackmark")
