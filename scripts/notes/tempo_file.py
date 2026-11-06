"""The tempo line written into a MIDI file together with the notes (the line itself: notes/tempo.py).

Keep timing (user 2026-10-21, per part): the part's tempo is the drawn line, but its notes keep the real time they
have at the tempo heard just before the part, so they move in ticks. Exact (user's rule: tick and microsecond
perfect): every note start / end (and CC message) inside the part is an anchor; between two anchors the file gets
as many ticks as the drawn tempo needs for that time, and one or two Domino-safe tempos adding up to exactly that
time. After the part everything sits that many ticks earlier / later at the same real time. Catch-ups never move
notes.

Bar compensation (tempo "bars", default on): a bar that a Keep timing part made an odd number of ticks long gets
the last gap it has there nudged a few ticks (the tempo there a hair faster / slower, the time the same) so a time
signature fits it: top 1-255, bottom a power of two up to 128 (Domino refuses finer: measured 2026-10-21). Players
ignore time signatures; they only put the bar lines of MIDI programs on the bars heard.

Warnings for the export message: notes starting inside a jump (they play off time), Hz basses where the tempo
really heard isn't the project's BPM (their tone is worked out from it)."""

import bisect

import numpy as np

from files import speed
from notes.meter import bar_line as meter_bar_line, bar_lines as meter_bar_lines, sigs_of
from notes.tempo import (EVERY, PAIR_REACH, SAFE_REACH, _fit_blocks, _fit_exact, _flat_pair, _put_all, _put_many, _runs, base_us, beat_tick, domino_safe,
                         typed_bpms,
                         file_segments, has_tempo, keep_heard, layout, tempo_text)

MAX_US = 0xFFFFFF  # the slowest tempo a file holds, in microseconds per beat
PAIR_CACHE = 4096  # gaps up to this many ticks have their tempo pair remembered (black MIDI repeats the same gaps)


def _cut(segs, lo, hi, starts=None):
    """segs (in order, not overlapping) inside ticks lo..hi. starts: their start ticks, to begin at the right one
    (cut once per Keep timing part, a walk through all of them each time was slow with thousands, PLAN #12 B)."""
    out = []
    first = max(0, bisect.bisect_right(starts, lo) - 1) if starts is not None else 0
    for k in range(first, len(segs)):
        a, b, u = segs[k]
        if a >= hi:
            break
        x, y = max(a, lo), min(b, hi)
        if y > x:
            out.append((x, y, u if isinstance(u, int) else u[x - a:y - a]))
    return out


def _per_tick(segs, lo, hi, starts=None):
    """The tempo on every tick lo..hi (int64 array). starts: as _cut's."""
    out = np.empty(hi - lo, np.int64)
    for a, b, u in _cut(segs, lo, hi, starts):
        out[a - lo:b - lo] = u
    return out


def bar_lengths(ppq):
    """Every bar length (ticks) a time signature can give: top 1-255, bottom 1..128 (powers of two dividing 4 x PPQ
    into whole ticks)."""
    out = set()
    for k in range(8):
        d = 2 ** k
        if 4 * ppq % d == 0:
            out.update(range(4 * ppq // d, 255 * 4 * ppq // d + 1, 4 * ppq // d))
    return np.array(sorted(out), np.int64)


def file_sigs(tempo, ppq):
    """The Conductor pane's time signatures as a file's: [(tick, top, bottom power)]."""
    return [(round(b * ppq), top, bottom.bit_length() - 1) for b, top, bottom in sigs_of(tempo)]


def file_keys(tempo, ppq):
    """The Conductor pane's key signatures as a file's: [(tick, sharps, minor)], each on its bar's first tick (a
    MIDI file has them at a bar's start; one left off a bar line by a time signature removed since goes back)."""
    sigs, out = sigs_of(tempo), {}
    for b, sf, mi in (tempo or {}).get("keysigs") or ():
        out[round(meter_bar_line(sigs, b) * ppq)] = (sf, mi)  # (two in one bar: the last wins, as many programs do)
    return [(t, *k) for t, k in sorted(out.items())]


def bar_line(sigs, tick, ppq, after=False):
    """The file's bar line at or before tick (after: at or after it), by the time signatures [(tick, top, bottom
    power)] (file_plan's "sigs")."""
    sigs = sorted(sigs)
    i = max(0, [s[0] <= tick for s in sigs].count(True) - 1)
    at, top, power = sigs[i]
    unit = max(1, top * 4 * ppq // 2 ** power)  # (a bar under a tick, e.g. 3/16 at PPQ 1: no crash, hunt 2026-10-24)
    line = at + (tick - at) // unit * unit
    if after and line < tick:
        line += unit
        if i + 1 < len(sigs):
            line = min(line, sigs[i + 1][0])
    return line


def bar_ticks(tempo, ppq, upto):
    """The bar lines' ticks by the Conductor pane's time signatures, from 0 to the first one past upto (and one
    more): int64 array, at least two."""
    sigs = sigs_of(tempo)
    longest = max(s[1] * 4 / s[2] for s in sigs)
    lines = [round(b * ppq) for b, _ in meter_bar_lines(sigs, 0, upto / ppq + 2 * longest + 1)]
    return np.unique(np.array(lines + [lines[-1] + 1], np.int64)) if len(lines) < 2 else np.unique(
        np.array(lines, np.int64))


def signature(length, ppq, prefer=None):
    """(top, bottom as a power of two) for a bar this many ticks long; prefer's note value first (the bar's own
    signature), then quarter notes, as most programs show them. None when no time signature fits."""
    for k in ((prefer,) if prefer is not None else ()) + (2, 3, 4, 5, 6, 7, 1, 0):
        d = 2 ** k
        if 4 * ppq % d:
            continue
        unit = 4 * ppq // d
        if length % unit == 0 and 1 <= length // unit <= 255:
            return length // unit, k
    return None


def _least(total):
    """The fewest file ticks a gap of this time (ticks x us, array) can have: each tick at most MAX_US, and two
    when one tick's value wouldn't be Domino-safe."""
    least = np.maximum(1, -(-total // MAX_US))
    return np.where((least == 1) & ~domino_safe(np.clip(total, 1, MAX_US)) & (total >= 2), 2, least)


class _Pairs:
    """Gaps' tempos, remembered by (ticks, time): a long Keep timing part of spam has thousands of alike gaps."""

    def __init__(self):
        self.known = {}

    def get(self, k, total):
        key = (k, total)
        got = self.known.get(key)
        if got is None:
            got = _flat_pair(k, total)
            if got is None:
                got = _fit_exact(np.full(k, total / k), total)
            if k <= PAIR_CACHE and len(self.known) < 65536:
                self.known[key] = got
        return got


def _seg_events(segs):
    """tempo._runs for file_plan's pieces: (a, b, us), us = a number, one per tick, or (run starts, values)."""
    ticks, vals = [], []
    for a, b, u in segs:
        if isinstance(u, tuple):
            ticks.append(u[0])
            vals.append(u[1])
        elif isinstance(u, (int, np.integer)):
            ticks.append(np.array([a], np.int64))
            vals.append(np.array([u], np.int64))
        else:
            ticks.append(np.arange(a, b, dtype=np.int64))
            vals.append(np.asarray(u, np.int64))
    if not ticks:
        return []
    t, v = np.concatenate(ticks), np.concatenate(vals)
    keep = np.concatenate([[True], v[1:] != v[:-1]])
    return list(zip(t[keep].tolist(), v[keep].tolist()))


def _quick_pairs(k, total):
    """_flat_pair for every gap at once where its answer is the two whole values round the average (or the average
    itself), both Domino-safe: (which gaps, a, b, ticks of a). The same answer _flat_pair gives (the nearest pair
    first); the others go the slow way (hunt 2026-10-24: one call per gap took 10-45 s on black MIDI)."""
    k, total = np.asarray(k, np.int64), np.asarray(total, np.int64)
    a = total // np.maximum(k, 1)
    exact = total % np.maximum(k, 1) == 0
    b = np.where(exact, a, a + 1)
    ka = np.where(exact, k, k * b - total)
    ok = (k >= 1) & (a >= 1) & (b <= MAX_US) & (_least(total) <= k) & (k <= total)
    ok &= domino_safe(np.clip(a, 1, MAX_US)) & domino_safe(np.clip(b, 1, MAX_US))
    return ok, a, b, ka


class _RunBuf:
    """A Keep timing part's file tempo as runs (ticks, us), not one number per file tick (hunt 2026-10-24: a few
    bars at 1,000,000 BPM took gigabytes)."""

    # (2026-11-06: every piece numbered in order and turned into runs all at once at the end: one piece at a time
    # = a few small NumPy calls each, 40,000 gaps in a Keep the audio export; the same runs)

    def __init__(self, size=0):
        self.ids, self.lens, self.vals = [], [], []  # (add's runs, plain Python)
        self.pend, self.pend_ids, self.waiting = [], [], 0  # (add_array's gaps waiting)
        self.chunks = []  # (ids, lens, vals) arrays of gaps already turned into runs
        self.count = 0

    def add(self, length, value):
        if length > 0:
            self.ids.append(self.count)
            self.lens.append(length)
            self.vals.append(value)
            self.count += 1

    def add_runs(self, lens, vals):
        """Runs made already (gap_plan's), in order, as one piece."""
        self.chunks.append((np.full(len(lens), self.count, np.int64), lens, vals))
        self.count += 1

    def add_array(self, got):
        """One gap's per-tick values as runs (each gap's runs its own, also where the gap before ends on the same
        value)."""
        self.pend.append(got)
        self.pend_ids.append(self.count)
        self.count += 1
        self.waiting += len(got)
        if self.waiting > 1 << 20:  # (never millions of per-tick values held: the runs are the point)
            self.flush()

    def flush(self):
        """The waiting gaps' runs: cut where the value changes and at every gap's start, as one by one."""
        sizes = np.array([len(g) for g in self.pend], np.int64)
        got = np.concatenate(self.pend) if len(self.pend) > 1 else np.asarray(self.pend[0], np.int64)
        cut = np.empty(len(got), bool)
        cut[0] = True
        cut[1:] = got[1:] != got[:-1]
        cut[np.cumsum(sizes[:-1])] = True
        starts = np.flatnonzero(cut)
        ids = np.repeat(np.array(self.pend_ids, np.int64), sizes)[starts]
        self.chunks.append((ids, np.diff(np.append(starts, len(got))), got[starts].astype(np.int64)))
        self.pend, self.pend_ids, self.waiting = [], [], 0

    def runs(self):
        if self.pend:
            self.flush()
        ids = np.concatenate([np.array(self.ids, np.int64)] + [c[0] for c in self.chunks])
        lens = np.concatenate([np.array(self.lens, np.int64)] + [c[1] for c in self.chunks])
        vals = np.concatenate([np.array(self.vals, np.int64)] + [c[2] for c in self.chunks])
        order = np.argsort(ids, kind="stable")  # (back in the order added; a gap's own runs keep theirs)
        return lens[order], vals[order]


def _spans_off(segs, keeps, start_u):
    """[(a, b)] ticks where the tempo really heard isn't the project's: the file's tempo (catch-ups in place), Keep
    timing parts at their heard tempo."""
    lo, hi = [], []
    for a, b, u in segs:
        inside = [(max(a, ka), min(b, ke), ku if isinstance(ku, int) else ku[max(a, ka) - ka:min(b, ke) - ka])
                  for ka, ke, ku in keeps if min(b, ke) > max(a, ka)]
        pieces, at = [], a
        for x, y, ku in inside:
            if x > at:
                pieces.append((at, x, u if isinstance(u, int) else u[at - a:x - a]))
            pieces.append((x, y, ku))
            at = y
        if b > at:
            pieces.append((at, b, u if isinstance(u, int) else u[at - a:b - a]))
        for x, y, v in pieces:
            if isinstance(v, (int, np.integer)):
                if v != start_u:
                    lo.append(x)
                    hi.append(y)
            else:
                off = np.concatenate([[False], np.asarray(v) != start_u, [False]])
                edges = np.flatnonzero(off[1:] != off[:-1])
                lo += (x + edges[0::2]).tolist()
                hi += (x + edges[1::2]).tolist()
    return np.array(lo, np.int64), np.array(hi, np.int64)


def heard_segs(events, end):
    """[(tick, us)] tempo changes (MIDI's 120 before the first) -> _per_tick's [(a, b, us)] up to tick end."""
    out, at, u = [], 0, 500000
    for t, v in events:
        if t > at:
            out.append((at, min(t, end), u))
        at, u = max(at, t), int(v)
        if at >= end:
            break
    if at < end:
        out.append((at, end, u))
    return [s for s in out if s[1] > s[0]]


class FileTempo:
    """A loaded MIDI's own tempo read once for every Keep timing piece of one layout (loaded_part), each piece's
    answer remembered: thousands of pieces each read the whole file's tempo and the piece before them again
    (2026-10-29: 6,500 pieces on a 13,000-tempo song = hours; the same answers)."""

    def __init__(self, heard, lay):
        self.lay = lay
        self.full = heard_segs(heard, 1 << 62)  # (cut at each piece's end: what heard_segs up to it gives)
        self.xs = [x for x, _, _ in self.full]
        self.ys = [y for _, y, _ in self.full]
        self.after = {}  # (a piece's end -> the first piece without its own line ending there)
        for k, (_, _, e, _) in enumerate(lay["keeps"]):
            if not lay["own_line"][k]:
                self.after.setdefault(e, k)
        self.known = {}

    def part(self, n, end):
        # (the pieces right before it worked out first, oldest first: one asked for in the middle of thousands
        # back to back went that deep in calls and failed)
        chain, seen, key = [], set(), (n, end)
        while key not in self.known and key not in seen:
            chain.append(key)
            seen.add(key)
            prev = self.after.get(self.lay["keeps"][key[0]][1])
            if prev is None:
                break
            key = (prev, self.lay["keeps"][key[0]][1])
        for k in reversed(chain):
            self.known[k] = self._part(*k)
        return self.known[(n, end)]

    def _part(self, n, end):
        a, before = self.lay["keeps"][n][1], self.lay["keeps"][n][3]
        prev = self.after.get(a)
        if prev is not None:  # (right after another piece: what that one plays at its end, not its own start tempo)
            got = self.part(prev, a)
            before = got[-1][2] if got else before
        i = bisect.bisect_right(self.ys, a)  # (the file's pieces ending after a ...)
        j = bisect.bisect_left(self.xs, end)  # (... and starting before end)
        segs = [(max(x, a), min(y, end), u) for x, y, u in self.full[i:j]]
        if a > 0 and segs:
            x0 = self.full[i][0]
            if x0 < a and int(before) != segs[0][2]:  # (the file's tempo runs on across the start: was it changed?)
                segs[0] = (segs[0][0], segs[0][1], int(before))
        return segs


def loaded_part(heard, lay, n, end, known=None):
    """A loaded MIDI's Keep timing piece n (layout's keeps) heard at the file's own tempo [(a, b, us)] up to tick
    end, but STARTING at the tempo heard just before it when the Conductor line there was changed (user
    2026-10-26: their 135 before the part, the file's 136 in it, was unintuitive); the file's changes inside it
    still come. A file change right on its start: the file's. known: a FileTempo of the same heard + lay, for
    many pieces."""
    return (known or FileTempo(heard, lay)).part(n, end)


def sorted_union(parts):
    """np.unique of the int64 arrays joined (np.unique hashes: ~0.15 s on a 17 GB MIDI's 2 M note ticks, where
    the Conductor pane hands them over sorted already; a sort + repeats dropped = the same array)."""
    x = np.concatenate(parts) if len(parts) > 1 else np.asarray(parts[0], np.int64)
    if len(x) < 2 or bool(np.all(x[1:] > x[:-1])):
        return x
    x = np.sort(x)
    return x[np.concatenate([[True], x[1:] != x[:-1]])]


def _member(x, s):
    """np.isin(x, s) for a sorted s."""
    if not len(s):
        return np.zeros(len(x), bool)
    at = np.minimum(np.searchsorted(s, x), len(s) - 1)
    return s[at] == x


def keep_gaps(tempo, base, ppq, beats, ticks, bars=None, loose=None, heard=None):
    """The Keep timing parts' gaps between anchors: (layout, segs, bad catch-ups, keeps [(a, e, us)], gaps [[n,
    total, file ticks, drawn]], dense = beats of EVERY gap whose two notes are under a tick apart at the part's
    tempo (it stays faster there), gave, times = each part's heard time: ticks x us from its start, one per tick
    (array, first 0) or the one us every tick takes). ticks = the ticks keeping their time (int64 array); loose = ticks keeping
    their time only where they have room (CC messages, user 2026-10-23: never the tempo staying faster for one):
    one under a tick from another is no anchor (file_plan's map puts it between its neighbours). heard = a
    loaded MIDI's own tempo [(tick, us)]: its notes keep the time they have in the file (user 2026-10-24: no
    unexpected BPM changes), not the time at the tempo heard before the part."""
    lay = layout(tempo, base, ppq)
    ts = sorted_union([ticks])
    if loose is None:
        loose = np.zeros(0, np.int64)
    else:  # (= np.setdiff1d(loose, ticks))
        loose = sorted_union([np.asarray(loose, np.int64)])
        at = np.searchsorted(ts, loose)
        loose = loose[(at >= len(ts)) | (ts[np.minimum(at, len(ts) - 1)] != loose)] if len(ts) else loose
    every_tick = np.concatenate([ticks, loose])
    segs, bad = file_segments(tempo, base, ppq, int(every_tick.max()) + 1 if len(every_tick) else 0)
    last_tick = int(every_tick.max()) if len(every_tick) else 0
    # a part on to the song's end stops at the last note (not a tick past it: no gap there to count); its heard
    # tempo: one whole number, or one per tick from its heard tempo line
    keeps = [(n, a, last_tick if e is None else e) for n, (_, a, e, _) in enumerate(lay["keeps"])]
    own = [lay["own_line"][n] for n, a, e in keeps if e > a]  # (a part with its own heard line: that, always)
    known = None if heard is None else FileTempo(heard, lay)
    files = [None if heard is None else known.part(n, e) for n, a, e in keeps if e > a]
    keeps = [(a, e, keep_heard(lay, n, a, e)) for n, a, e in keeps if e > a]
    fit_bars = tempo.get("bars", True)
    if fit_bars and bars is None:  # (bar lines by the time signatures: fitting works inside each one, user)
        bars = bar_ticks(tempo, ppq, max([last_tick] + [e for _, e, _ in keeps]))
    # gaps between anchors, part by part: (first tick, end tick, time = ticks x heard us, file ticks)
    gaps, dense, gave, times = [], [], [], []
    starts = [s[0] for s in segs]
    # (sorted once, each part takes its own slice: every part looked through every note of the song, thousands of
    # jumps x 300k notes froze for seconds, PLAN #12 hunt; the same answers)
    bs =np.sort(bars) if fit_bars else None
    ls = np.sort(loose)
    fast = speed.loops()  # (compiled: gap_ticks does a part's work below in one go, the same numbers)
    if fast is not None:
        ts_c = np.ascontiguousarray(ts, np.int64)
        bs_c = np.ascontiguousarray(bs, np.int64) if fit_bars else np.zeros(0, np.int64)
        none = np.zeros(0, np.int64)
    for (a, e, u), mine, part in zip(keeps, own, files):
        drawn = _per_tick(segs, a, e, starts)
        was = None if part is None else np.concatenate([[0], np.cumsum(_per_tick(part, a, e))])
        if mine:  # (its own heard tempo line: that, also over a loaded MIDI's own tempo, even one steady value:
            # a flat line, e.g. Keep the visual's, was left to the file's tempo, hunt 2026-10-24)
            was = None if isinstance(u, int) else np.concatenate([[0], np.cumsum(u)])
        lo = ls[np.searchsorted(ls, a, "right"):np.searchsorted(ls, e, "left")]
        if fast is not None and not len(lo):
            n, total, k, close_at = fast.gap_ticks(a, e, ts_c, bs_c, drawn, none if was is None else was,
                                                   int(u) if was is None else 0, MAX_US)
            dense += (close_at / ppq).tolist()
            gaps.append([n, total, k, drawn])
            times.append(was if was is not None else int(u))
            continue
        near = ts[np.searchsorted(ts, a, "left"):np.searchsorted(ts, e, "right")]  # (a <= tick <= e)
        inside = near[(near > a) & (near < e)]
        marks = [np.array([a, e], np.int64), inside]
        if fit_bars:
            marks.append(bs[np.searchsorted(bs, a, "right"):np.searchsorted(bs, e, "left")])
        sums =np.concatenate([[0], np.cumsum(drawn, dtype=np.float64)])
        while True:
            n = sorted_union(marks + [lo])
            d = np.diff(n)
            total = d * u if was is None else was[n[1:] - a] - was[n[:-1] - a]
            exact = total / ((sums[n[1:] - a] - sums[n[:-1] - a]) / d)  # (file ticks each gap needs at the tempo)
            note = _member(n, near)  # (n is inside a..e: the notes there are all it can meet)
            real = note | _member(n, lo)
            close = real[:-1] & real[1:] & (exact < 1)  # (two under a tick apart at that tempo)
            if not len(lo):
                break
            # a loose one too close gives way (the later of the two when both are)
            j = np.flatnonzero(close)
            drop = np.where(~note[j + 1], n[j + 1], np.where(~note[j], n[j], -1))
            drop = drop[drop >= 0]
            if not len(drop):
                break
            lo = np.setdiff1d(lo, drop)
            gave += (np.unique(drop) / ppq).tolist()
        dense += (n[:-1][close & note[:-1] & note[1:]] / ppq).tolist()
        # rounded on a running total, so the part keeps the drawn tempo on average (gap by gap, regular spam
        # rounded the same way every time: 60 BPM came out 48)
        k = np.diff(np.concatenate([[0], np.rint(np.cumsum(exact))])).astype(np.int64)
        k = np.clip(k, _least(total), total)
        gaps.append([n, total, k, drawn])
        times.append(was if was is not None else int(u))
    return lay, segs, bad, keeps, gaps, dense, sorted(gave), times


def dense_spots(tempo, base, ppq, beats, ticks, heard=None):
    """Beats where notes in a Keep timing part are under a tick apart at its tempo (the Tempo pane shows them while
    editing, user 2026-10-22: the export's message came too late). [] without Keep timing parts."""
    if not has_tempo(tempo) or not any(len(p) > 5 and p[5] is True for p in tempo.get("points", ())):
        return []
    return keep_gaps(tempo, base, ppq, beats, np.asarray(ticks, np.int64).ravel(), heard=heard)[5]


def limit_spots(tempo, base, ppq, beats, ticks, heard=None, loose=None):
    """What the Conductor pane's limit texts show (user 2026-10-24), quicker than file_plan (no tempo values):
    {"dense": beats where notes in a Keep timing part are under a tick apart, "gave": CC messages (loose) moved by
    a tick or less, "unfit": bars no time signature fits (file_plan's "unfit"), "moved": bar lines written a
    little off (BAR CARRY-OVER, file_plan's "moved")}. The same as the export's, but
    for the rare gap whose tempo pair needs a tick more or less."""
    out = {"dense": [], "gave": [], "unfit": [], "moved": []}
    if not has_tempo(tempo) or not any(len(p) > 5 and p[5] is True for p in tempo.get("points", ())):
        return out
    ticks = np.asarray(ticks, np.int64).ravel()
    loose = np.zeros(0, np.int64) if loose is None else np.asarray(loose, np.int64).ravel()
    upto = max([int(t.max()) for t in (ticks, loose) if len(t)] + [beat_tick(p[0], ppq) for p in tempo["points"]])
    bars = bar_ticks(tempo, ppq, upto)
    _, _, _, keeps, gaps, out["dense"], out["gave"], _ = keep_gaps(tempo, base, ppq, beats, ticks, bars, loose,
                                                                   heard)
    if not tempo.get("bars", True) or not gaps:
        return out
    bars = bar_ticks(tempo, ppq, max(upto, max(e for _, e, _ in keeps)))
    _fit_bar_lines(gaps, keeps, bars, bar_lengths(ppq))
    bp_n, bp_f, offset = [0], [0], 0  # (the file's ticks at the anchors, as file_plan's map)
    for (a, e, _), (n, _, k, _) in zip(keeps, gaps):
        f = a + offset + np.concatenate([[0], np.cumsum(k)])
        bp_n += n.tolist()
        bp_f += f.tolist()
        offset = int(f[-1]) - e
    bp_n, bp_f = np.array(bp_n, np.int64), np.array(bp_f, np.int64)
    stop = min(len(bars) - 2, int(np.searchsorted(bars, max(e for _, e, _ in keeps), "right")))
    lines = bars[:stop + 2]
    i = np.searchsorted(bp_n, lines, "right") - 1
    fl = bp_f[i] + (lines - bp_n[i])  # (bar lines are anchors inside the parts: exact)
    _, moved, unfit = _written_lines(fl, bar_lengths(ppq))
    out["unfit"] = [int(lines[m]) / ppq for m in unfit]
    out["moved"] = [int(lines[m + 1]) / ppq for m in moved]
    return out


FINE_MOST = 64  # a Hz bass's finer gates: made on at most this many times the project's ticks
FINE_TICKS = 1 << 22  # ... and at most this many ticks a beat


def finer_parts(tempo, base, ppq, last):
    """[(first tick, end tick, times finer)] of the Keep timing parts drawn at least as fast as the tempo heard
    before them all along (the file has more ticks a second there: a Hz bass with "Finer gates" is made on them,
    hz_fine.py); last = the last tick a note reaches (a part on to the song's end stops there)."""
    if not has_tempo(tempo) or not any(len(p) > 5 and p[5] is True for p in tempo.get("points", ())):
        return []
    segs, _ = file_segments(tempo, base, ppq, last + 1)
    out = []
    lay = layout(tempo, base, ppq)
    starts = [s[0] for s in segs]
    for n, (_, a, e, _) in enumerate(lay["keeps"]):
        e = last + 1 if e is None else e
        if e <= a:
            continue
        drawn = _per_tick(segs, a, min(e, segs[-1][1]), starts)
        u = keep_heard(lay, n, a, a + len(drawn))  # (the heard tempo: one, or per tick from its heard line)
        if len(drawn) and np.all(drawn <= u):
            times = min(int(np.max(-(-np.asarray(u) // drawn))), FINE_MOST, max(1, FINE_TICKS // ppq))
            if times > 1:
                out.append((a, e, int(times)))
    return out


def file_plan(tempo, base, ppq, beats=4, ticks=(), starts=(), hz=None, loose=(), heard=None):
    """What a MIDI file gets: {"events": [(tick, us)] tick 0 first, "bad": beats of catch-up lines that can't work,
    "sigs": [(tick, top, bottom power)] time signatures, "map": Spiderweb tick(s) -> file tick(s) ("bp": the same as
    (anchors, their file ticks) with Keep timing parts, else None), "jumps": beats of
    the first note starting inside each jump, "hz": beats of Hz bass notes where the heard tempo isn't the project's,
    "dense": where notes in a Keep timing part are closer than one tick at its tempo (it stays faster there),
    "gave": beats of the CC messages (loose) that gave way, a tick or less off their time}.
    ticks = every tick that must keep its time (note starts / ends); loose = ticks keeping it where they have room
    (CC messages: keep_gaps); starts = the note starts; hz =
    (starts, ends) of the Hz basses' notes. Spiderweb's own ticks, the project's PPQ. heard: a loaded MIDI's own tempo
    (keep_gaps)."""
    out = {"events": [(0, round(60_000_000 / base))], "bad": [], "sigs": file_sigs(tempo, ppq),
           "keys": file_keys(tempo, ppq),
           "map": lambda t: np.asarray(t, np.int64), "bp": None, "jumps": [], "hz": [], "dense": [], "gave": [],
           "unfit": [], "moved": []}
    if not has_tempo(tempo):
        return out
    start_u = base_us(base)
    out["events"] = [(0, start_u)]
    out["typed"] = typed_bpms(tempo)  # (Domino's items write these BPMs as typed: domino_clip.dms_bpm)
    ticks = np.asarray(ticks, np.int64).ravel()
    loose = np.asarray(loose, np.int64).ravel()
    upto = max([int(t.max()) for t in (ticks, loose) if len(t)] + [beat_tick(p[0], ppq) for p in tempo["points"]])
    bars = bar_ticks(tempo, ppq, upto)
    lay, segs, out["bad"], keeps, gaps, out["dense"], out["gave"], times = keep_gaps(tempo, base, ppq, beats, ticks,
                                                                                    bars, loose, heard)
    end = segs[-1][1]
    every = tempo.get("every", EVERY)
    fit_bars = tempo.get("bars", True)
    if fit_bars and gaps:
        if keeps:  # (bar lines far enough for every part)
            bars = bar_ticks(tempo, ppq, max(upto, max(e for _, e, _ in keeps)))
        _fit_bar_lines(gaps, keeps, bars, bar_lengths(ppq))
    # the file's tempo: plain stretches shifted, Keep timing parts made of their gaps
    pairs = _Pairs()
    file_segs, bp_n, bp_f, clocks = [], [0], [0], []
    offset, at = 0, 0
    seg_starts = [s[0] for s in segs]
    for (a, e, u), (n, total, k, drawn), heard_t in zip(keeps, gaps, times):
        file_segs += [(x + offset, y + offset, v) for x, y, v in _cut(segs, at, a, seg_starts)]
        buf = _RunBuf()
        pre = []

        def slow(j, last, a=a, n=n, total=total, k=k, drawn=drawn, buf=buf, pre=pre):
            """Gap j's file tempo into buf the NumPy way (no compiled loops, or a gap gap_plan leaves to Python:
            _fit_exact / the full pair search); last = the value the gap before ended on (None = none) -> its."""
            if not pre:  # (the part's gaps at once, the first time one is asked; k is only ever changed at gaps
                # already done)
                changes = np.flatnonzero(drawn[1:] != drawn[:-1]) + 1 + a  # (ticks where the drawn tempo changes)
                curved = (k > every) & (np.searchsorted(changes, n[:-1], "right") <
                                        np.searchsorted(changes, n[1:], "left"))
                quick, qa, qb, qka = _quick_pairs(k, total)
                quick &= ~curved
                pre += [quick.tolist(), qa.tolist(), qb.tolist(), qka.tolist(), curved.tolist(),
                        _least(total).tolist()]
            quick, qa, qb, qka, curved, fewest = pre
            kj, tj = int(k[j]), int(total[j])
            if quick[j]:  # (the two values round the average: as runs, never one number per tick)
                aj, bj, kaj = qa[j], qb[j], qka[j]
                if 0 < kaj < kj and last is not None and aj != last and bj == last:
                    buf.add(kj - kaj, bj)  # (the run goes on from the gap before: one change fewer)
                    buf.add(kaj, aj)
                    return aj
                buf.add(kaj, aj)
                buf.add(kj - kaj, bj)
                return bj if kaj < kj else aj
            n0, n1 = int(n[j]), int(n[j + 1])
            got = None
            if curved[j]:  # (a long gap over a curve: steps every "every" ticks, as drawn)
                ideal = drawn[n0 - a + (np.arange(kj) * (n1 - n0)) // kj].astype(np.float64)
                got = _fit_blocks(ideal, tj, every)
            if got is None:  # (no Domino-safe values for that many ticks: a tick more or less, rare)
                least = fewest[j]
                for kk in [kj] + [kj + s * w for s in range(1, 9) for w in (1, -1)]:
                    if least <= kk <= tj:
                        got = pairs.get(kk, tj)
                        if got is not None:
                            kj = k[j] = kk
                            break
                if got is None:  # (nothing near: the nearest values, not Domino-safe)
                    got = np.full(kj, tj // kj, np.int64)
                    got[:tj - int(got.sum())] += 1
                if last is not None and got[0] != last and got[-1] == last:
                    got = got[::-1]  # (the run goes on from the gap before: one change fewer)
            buf.add_array(got)
            return int(got[-1])

        loops = speed.loops()
        if loops is None:
            last = None
            for j in range(len(k)):
                last = slow(j, last)
        else:  # (compiled: gap_plan does the gaps, the same numbers)
            pref = np.concatenate([[0], np.cumsum(drawn[1:] != drawn[:-1], dtype=np.int64)])
            # (room for the usual runs: two a pair, one a curve's step; a gap whose values change on every tick
            # (_fit_exact) needs more: gap_plan stops before it and the room grows)
            room = 3 * len(k) + 16 + (int((k // every).sum()) if every > 1 else 0)
            lens_out, vals_out = np.empty(room, np.int64), np.empty(room, np.int64)
            got_out = np.empty(int(k.max()) + 9, np.int64)
            j, last = 0, -1
            while True:
                j, last, pos = loops.gap_plan(j, last, a, n, total, k, drawn, pref, int(every), MAX_US, SAFE_REACH,
                                              PAIR_REACH, lens_out, vals_out, 0, got_out)
                if pos:
                    buf.add_runs(lens_out[:pos].copy(), vals_out[:pos].copy())
                if j >= len(k):
                    break
                need = int(k[j]) + 10
                if need > len(lens_out):
                    lens_out, vals_out = np.empty(need, np.int64), np.empty(need, np.int64)
                elif not pos:  # (an empty room and still stopped: never happens; the NumPy way, to be safe)
                    last = slow(j, None if last == -1 else last)
                    j += 1
        fa = a + offset
        lens, vals = buf.runs()
        starts = fa + np.concatenate([[0], np.cumsum(lens)])
        file_segs.append((fa, int(starts[-1]), (starts[:-1], vals)))
        # (each part's heard and file clocks: a CC message that gave way goes on the file tick sounding nearest its
        # heard time, hunt 2026-10-24: the same share of ticks was up to 190 ms off where the tempo is uneven)
        clocks.append((a, e, fa, heard_t, starts, vals,
                       np.concatenate([[0], np.cumsum(lens * vals, dtype=np.float64)])))
        f = fa + np.concatenate([[0], np.cumsum(k)])
        bp_n += n.tolist()
        bp_f += f.tolist()
        offset = int(f[-1]) - e
        at = e
    file_segs += [(x + offset, y + offset, v) for x, y, v in _cut(segs, at, end)]
    events = _seg_events(file_segs)
    if events[0][0] != 0:
        events.insert(0, (0, start_u))
    out["events"] = events
    bp_n, bp_f = np.array(bp_n, np.int64), np.array(bp_f, np.int64)

    def to_file(t):
        # between two anchors (a CC message that gave way, keep_gaps): the file tick sounding nearest its heard
        # time, never onto the anchor before when there's room (one a tick after a note stays after it)
        t = np.asarray(t, np.int64)
        i = np.searchsorted(bp_n, t, "right") - 1
        out = bp_f[i] + (t - bp_n[i])
        j = np.minimum(i + 1, len(bp_n) - 1)
        d, fd = bp_n[j] - bp_n[i], bp_f[j] - bp_f[i]
        mid = (j > i) & (t > bp_n[i]) & (d > 0) & (fd != d)
        if np.any(mid):
            share = bp_f[i] + np.rint((t - bp_n[i]) * fd / np.maximum(d, 1)).astype(np.int64)
            for a, e, fa, heard_t, starts, vals, clock in clocks:
                on = mid & (t > a) & (t < e)
                if not np.any(on):
                    continue
                x = t[on] - a
                want = heard_t[x] if isinstance(heard_t, np.ndarray) else x.astype(np.float64) * heard_t
                r = np.clip(np.searchsorted(clock, want, "right") - 1, 0, len(vals) - 1)
                pos = (starts[r] - fa) + (want - clock[r]) / vals[r]  # (file ticks from the part's start)
                share[on] = fa + np.ceil(pos - 0.5).astype(np.int64)  # (the nearer tick, the earlier on a tie)
            # (on the note before = written before its note-on: a tick on, the next anchor's tick when that's all
            # the room there is (hunt 2026-10-23))
            share = np.clip(share, bp_f[i] + np.minimum(fd, 1), bp_f[j])
            out = np.where(mid, share, out)
        return out

    if keeps:
        # (the map as anchors -> file ticks: a loaded MIDI's export, midi_scan.rewrite; the CC messages that gave
        # way as anchors of their own there, so they land by time too)
        lt = np.setdiff1d(loose, bp_n)
        if len(lt):
            lf = to_file(lt)
            n_all, f_all = np.concatenate([bp_n, lt]), np.concatenate([bp_f, lf])
            order = np.argsort(n_all, kind="stable")
            out["bp"] = (n_all[order], f_all[order])
        else:
            out["bp"] = (bp_n, bp_f)
        out["map"] = to_file
        out["sigs"] = [(int(to_file(t)), top, p) for t, top, p in out["sigs"]]
        out["keys"] = [(int(to_file(t)), sf, mi) for t, sf, mi in out["keys"]]
    if fit_bars and keeps:
        out["sigs"], unfit, moved = _signatures(keeps, tempo, bars, ppq, to_file)
        out["unfit"] = [t / ppq for t in unfit]
        out["moved"] = [t / ppq for t in moved]
    # warnings
    starts = np.asarray(starts, np.int64).ravel()
    seg_starts = [s[0] for s in segs]
    for st, plan in lay["worked"]:  # (a note in a jump plays off time where the time so far isn't the heard time)
        if not plan["ok"]:
            continue
        s0, e = beat_tick(st["from"], ppq), beat_tick(st["end"], ppq)
        on = starts[(starts > s0) & (starts < e)]
        if not len(on):
            continue
        real = _per_tick(segs, s0, e, seg_starts)
        for a, ke, ku in keeps:
            lo, hi = max(a, s0), min(ke, e)
            if hi > lo:
                real[lo - s0:hi - s0] = ku if isinstance(ku, int) else ku[lo - a:hi - a]
        so_far = np.concatenate([[0], np.cumsum(real)])
        off = on[so_far[on - s0] != (on - s0) * st["base_u"]]
        if len(off):
            out["jumps"].append(int(off.min()) / ppq)
    if hz is not None and len(hz[0]):
        lo, hi = _spans_off(segs, keeps, start_u)
        if len(lo):
            s, e = (np.asarray(x, np.int64).ravel() for x in hz)
            i = np.searchsorted(lo, e, "left") - 1
            bad = (i >= 0) & (hi[np.maximum(i, 0)] > s)
            if bad.any():
                out["hz"] = [int(s[bad].min()) / ppq]
    return out


def kept_drawn(tempo, base, ppq, end=0):
    """[(tick, us)] of the DRAWN tempo inside the Keep timing parts (what the file holds there, its notes moved to
    keep their time), each part's first value at its start; [] without a part. The track list's Conductor row shows
    them purple (user 2026-11-05: heard_tempo holds the part at one tempo, the row showed nothing there)."""
    if not has_tempo(tempo):
        return []
    lay = layout(tempo, base, ppq)
    if not lay["keeps"]:
        return []
    segs = _file_segs(tempo, base, ppq, end)
    starts = [s[0] for s in segs]  # (in order, not overlapping: each part's pieces found by halving, never all)
    last = segs[-1][1]
    out = []
    for _, a, e, _ in lay["keeps"]:
        e = last if e is None else e
        if e <= a:
            continue
        part, i = [], max(0, bisect.bisect_right(starts, a) - 1)
        while i < len(segs) and segs[i][0] < e:  # (no slice of the rest: that copied it for every part)
            sa, sb, u = segs[i]
            lo, hi = max(a, sa), min(e, sb)
            if lo < hi:
                part.append((lo, hi, u if isinstance(u, int) else u[lo - sa:hi - sa]))
            i += 1
        out += _runs(part)
    return out


def _file_segs(tempo, base, ppq, end):
    """file_segments' pieces, the last few remembered: heard_tempo and kept_drawn both ask after each tempo change
    (worked out twice doubled the track list's wait on a big tempo map)."""
    key = (tempo_text(tempo), base, ppq, end)
    got = _segs.get(key)
    if got is None:
        if len(_segs) > 4:
            _segs.clear()
        got = _segs[key] = file_segments(tempo, base, ppq, end)[0]
    return got


_segs = {}


def heard_tempo(tempo, base, ppq, end=0, heard=None):
    """[(tick, us)] on Spiderweb's ticks for built-in playback: what a player of the MIDI file really plays where
    (catch-ups in place, Keep timing parts at the tempo heard before them, so their notes sound when the file's do).
    heard = a loaded MIDI's own tempo [(tick, us)]: Keep timing parts without their own heard line play at it (its
    notes keep the time they have in the file, as keep_gaps). None without a tempo line."""
    if not has_tempo(tempo):
        return None
    key = (tempo_text(tempo), base, ppq, end, id(heard))
    if key in _heard:  # (Play / each tempo edit while playing: remembered until the line changes)
        return _heard[key]
    segs = _file_segs(tempo, base, ppq, end)
    lay = layout(tempo, base, ppq)
    known = None if heard is None else FileTempo(heard, lay)
    puts = []
    for n, (_, a, e, _) in enumerate(lay["keeps"]):
        e = segs[-1][1] if e is None else e
        if heard is not None and not lay["own_line"][n]:
            puts.append((a, e, known.part(n, e)))
            continue
        puts.append((a, e, lay["keep_segs"](n, a, e)))  # (its heard tempo line, if any)
    segs = _put_many(segs, puts)
    if len(_heard) > 4:
        _heard.clear()
    _heard[key] = out = _runs(segs)
    return out


_heard = {}


def seconds_at(runs, ppq, ticks):
    """Ticks (a number or an array) -> seconds from tick 0 under a tempo [(tick, us)] (heard_tempo; first on 0)."""
    at = np.array([t for t, _ in runs], float)
    us = np.array([u for _, u in runs], float)
    sec = np.concatenate([[0.0], np.cumsum(np.diff(at) * us[:-1])]) / (ppq * 1e6)
    t = np.asarray(ticks, float)
    i = np.maximum(np.searchsorted(at, t, "right") - 1, 0)
    out = sec[i] + (t - at[i]) * us[i] / (ppq * 1e6)
    return float(out) if np.isscalar(ticks) else out


def ticks_at(runs, ppq, seconds):
    """seconds_at the other way: seconds (a number or an array) -> ticks (not rounded)."""
    at = np.array([t for t, _ in runs], float)
    us = np.array([u for _, u in runs], float)
    sec = np.concatenate([[0.0], np.cumsum(np.diff(at) * us[:-1])]) / (ppq * 1e6)
    s = np.asarray(seconds, float)
    i = np.maximum(np.searchsorted(sec, s, "right") - 1, 0)
    out = at[i] + (s - sec[i]) * (ppq * 1e6) / us[i]
    return float(out) if np.isscalar(seconds) else out


NUDGE = 0.05  # bar compensation: a gap's ticks change by at most this share (or one tick), so its tempo a hair


def _fit_length(d, lengths):
    """The bar length nearest d (file ticks) that time signatures can write: as many of the longest as needed, then
    the nearest one (the shorter on a tie); d itself when it fits."""
    longest, base = int(lengths[-1]), 0
    while d > longest and not (lengths[min(np.searchsorted(lengths, d), len(lengths) - 1)] == d):
        d, base = d - longest, base + longest
    i = int(np.searchsorted(lengths, d))
    if i < len(lengths) and lengths[i] == d:
        return base + int(d)
    near = [int(lengths[j]) for j in (i - 1, i) if 0 <= j < len(lengths)]
    return base + min(near, key=lambda x: (abs(x - d), x))


def _written_lines(fl, lengths):
    """BAR CARRY-OVER (user 2026-10-24): the file's bar lines fl (ticks, in order) as time signatures can write them:
    each bar the length nearest to reaching its true line, so a bar no signature fits is written a little off and
    the next bar puts it right (usually within 2 bars; on until it can). The last line is kept exact (the project's
    own signatures go on from there). A carry that can't settle before then (no notes left to nudge) isn't used:
    that bar is left with no signature as before and the next line is exact. (written lines, bars whose end line
    is moved, bars left with none)."""
    w = np.array(fl, np.int64)
    moved, unfit = [], []
    m, last = 0, len(fl) - 1
    while m < last:
        if _fit_length(int(fl[m + 1] - w[m]), lengths) == fl[m + 1] - w[m]:
            m += 1
            continue
        k, trial, chain = m, w.copy(), []  # (carried on bar by bar until a bar lands on its line)
        while k < last - 1:
            trial[k + 1] = trial[k] + _fit_length(int(fl[k + 1] - trial[k]), lengths)
            chain.append(k)
            k += 1
            if _fit_length(int(fl[k + 1] - trial[k]), lengths) == fl[k + 1] - trial[k]:
                break
        else:
            k = None
        if k is None or k >= last:
            unfit.append(m)  # (never settles: the old way, this bar alone without one)
            m += 1
            continue
        w, moved = trial, moved + chain
        m = k
    return w, moved, unfit


def _fit_bar_lines(gaps, keeps, bars, lengths):
    """Bar compensation: in each bar a Keep timing part changed, the longest gaps there get a tick more / fewer
    each so the bar's length fits a time signature (the gaps' time stays: only their tempo changes a hair). A bar
    that can't be fitted gently is written the nearest length a signature gives, and the next bars aim to land
    the bar line back (BAR CARRY-OVER, _written_lines, user 2026-10-24); no spikes: user's "a hair"."""
    if not gaps:
        return
    # each gap's bar (by the time signatures), then the bars' gaps together, in order (a million gaps one by one in
    # Python took seconds)
    ms = np.concatenate([np.searchsorted(bars, n[:-1], "right") - 1 for n, _, _, _ in gaps])
    ps = np.concatenate([np.full(len(k), p, np.int64) for p, (_, _, k, _) in enumerate(gaps)])
    js = np.concatenate([np.arange(len(k)) for _, _, k, _ in gaps])
    kk = np.concatenate([k for _, _, k, _ in gaps]).astype(np.int64)
    tt = np.concatenate([total for _, total, _, _ in gaps]).astype(np.int64)
    dk = kk - np.concatenate([np.diff(n) for n, _, _, _ in gaps])
    order_m = np.argsort(ms, kind="stable")
    ms, ps, js, dk, kk, tt = (x[order_m] for x in (ms, ps, js, dk, kk, tt))
    cut = np.flatnonzero(np.diff(ms)) + 1
    carry, last = 0, None  # (written bar line - true one, at the start of the bar after `last`)
    for lo, hi in zip(np.concatenate([[0], cut]).tolist(), np.concatenate([cut, [len(ms)]]).tolist()):
        m = int(ms[lo])
        if carry and last is not None:  # (bars in between, no gaps to nudge: the carry goes on through them)
            for b in range(last + 1, m):
                need = int(bars[b + 1] - bars[b]) - carry
                carry = _fit_length(need, lengths) - need
        last = m
        true = int(bars[m + 1] - bars[m]) + int(dk[lo:hi].sum())  # (the bar's file length now)
        where = list(zip(ps[lo:hi].tolist(), js[lo:hi].tolist()))
        ks, tots = kk[lo:hi], tt[lo:hi]
        room = np.maximum(1, (ks * NUDGE).astype(np.int64))  # (ticks each gap may give / take)
        up, down = np.minimum(room, tots - ks), np.minimum(room, ks - _least(tots))
        order = np.argsort(-ks, kind="stable")  # (longest gaps first)

        def nudge(have):
            """The bar's gaps nudged so `have` becomes a length a signature gives: the ticks moved, or None."""
            i = int(np.searchsorted(lengths, have))
            for want in sorted(lengths[max(0, i - 3):i + 3].tolist(), key=lambda x: abs(x - have)):
                delta = want - have
                if delta == 0:
                    return 0
                cap = (up if delta > 0 else down)[order]
                if cap.sum() < abs(delta):
                    continue
                share = np.zeros(len(ks), np.int64)
                left = abs(delta)
                while left:  # (one tick at a time round the longest gaps, up to each one's room)
                    for o, c in zip(order.tolist(), cap.tolist()):
                        if not left:
                            break
                        if abs(share[o]) < c:
                            share[o] += 1 if delta > 0 else -1
                            left -= 1
                for o in np.flatnonzero(share).tolist():
                    p, j = where[o]
                    gaps[p][2][j] += share[o]
                return delta
            return None

        # (landing on its true line from where the last line was written: the carry settled; else its own length
        # fitted as before, the carry going on; else written the nearest length and the rest carried on)
        if nudge(true - carry) is not None:
            carry = 0
            continue
        moved_by = nudge(true) if carry else None
        need = true + (moved_by or 0) - carry
        carry = _fit_length(need, lengths) - need


def _signatures(keeps, tempo, bars, ppq, to_file):
    """([(file tick, top, bottom power)], [Spiderweb ticks of the bars no time signature fits], [Spiderweb ticks
    of the bar lines written a little off]): a time signature wherever the bar length changes, up to the bar after
    the last Keep timing part (then the project's own). A bar that kept its length keeps its own signature (6/8
    stays 6/8, not 3/4); one fitted gets the nearest with its own note value first. A bar no signature fits is
    written the nearest length one gives and the next bars put the line right (BAR CARRY-OVER, _written_lines);
    only the last one, where the project's own go on, can be left with none (the sound is right)."""
    last = max(e for _, e, _ in keeps)
    longest = int(max(bar_lengths(ppq)))
    own = file_sigs(tempo, ppq)
    starts = [t for t, _, _ in own]

    def own_at(tick):
        return own[max(0, bisect.bisect_right(starts, tick) - 1)][1:]

    out, prev, unfit = [], None, []
    stop = min(len(bars) - 2, int(np.searchsorted(bars, last, "right")))
    lines, moved, _ = _written_lines(to_file(bars[:stop + 2]), bar_lengths(ppq))
    for m in range(0, stop + 1):
        f0, f1 = int(lines[m]), int(lines[m + 1])
        mine = own_at(int(bars[m]))
        sig = mine if f1 - f0 == int(bars[m + 1] - bars[m]) else signature(f1 - f0, ppq, mine[1])
        if sig is None:  # (too long for one signature: as many of the longest as fit, then the rest)
            at, rest = f0, f1 - f0
            while rest > longest and signature(rest, ppq) is None:
                s = signature(longest, ppq)
                if s != prev:
                    out.append((at, *s))
                    prev = s
                at, rest = at + longest, rest - longest
            sig = signature(rest, ppq)
            if sig is None:  # (the rest fits none: the next bar writes its own again, so its line is right)
                prev = None
                unfit.append(int(bars[m]))
                continue
            f0 = at
        if sig != prev:
            out.append((f0, *sig))
            prev = sig
    end = int(bars[stop + 1])
    for t, top, power in own:  # (then the project's own: the one in effect there, and every later one)
        if t >= end or t == own[max(0, bisect.bisect_right(starts, end) - 1)][0]:
            at = max(t, end)
            if (top, power) != prev:
                out.append((int(to_file(np.array([at]))[0]), top, power))
                prev = (top, power)
    return out, unfit, [int(bars[m + 1]) for m in moved]
