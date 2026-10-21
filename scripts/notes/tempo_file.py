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

import numpy as np

from notes.tempo import (EVERY, _fit_blocks, _fit_exact, _flat_pair, _put, _runs, base_us, beat_tick, domino_safe,
                         file_segments, has_tempo, layout)

MAX_US = 0xFFFFFF  # the slowest tempo a file holds, in microseconds per beat
PAIR_CACHE = 4096  # gaps up to this many ticks have their tempo pair remembered (black MIDI repeats the same gaps)


def _cut(segs, lo, hi):
    """segs inside ticks lo..hi."""
    out = []
    for a, b, u in segs:
        x, y = max(a, lo), min(b, hi)
        if y > x:
            out.append((x, y, u if isinstance(u, int) else u[x - a:y - a]))
    return out


def _per_tick(segs, lo, hi):
    """The tempo on every tick lo..hi (int64 array)."""
    out = np.empty(hi - lo, np.int64)
    for a, b, u in _cut(segs, lo, hi):
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


def bar_line(sigs, tick, ppq, after=False):
    """The file's bar line at or before tick (after: at or after it), by the time signatures [(tick, top, bottom
    power)] (file_plan's "sigs")."""
    sigs = sorted(sigs)
    i = max(0, [s[0] <= tick for s in sigs].count(True) - 1)
    at, top, power = sigs[i]
    unit = top * 4 * ppq // 2 ** power
    line = at + (tick - at) // unit * unit
    if after and line < tick:
        line += unit
        if i + 1 < len(sigs):
            line = min(line, sigs[i + 1][0])
    return line


def signature(length, ppq):
    """(top, bottom as a power of two) for a bar this many ticks long; quarter notes first, as most programs show
    them. None when no time signature fits."""
    for k in (2, 3, 4, 5, 6, 7, 1, 0):
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


def _spans_off(segs, keeps, start_u):
    """[(a, b)] ticks where the tempo really heard isn't the project's: the file's tempo (catch-ups in place), Keep
    timing parts at their heard tempo."""
    lo, hi = [], []
    for a, b, u in segs:
        inside = [(max(a, ka), min(b, ke), ku) for ka, ke, ku in keeps if min(b, ke) > max(a, ka)]
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


def file_plan(tempo, base, ppq, beats=4, ticks=(), starts=(), hz=None):
    """What a MIDI file gets: {"events": [(tick, us)] tick 0 first, "bad": beats of catch-up lines that can't work,
    "sigs": [(tick, top, bottom power)] time signatures, "map": Spiderweb tick(s) -> file tick(s), "jumps": beats of
    the first note starting inside each jump, "hz": beats of Hz bass notes where the heard tempo isn't the project's,
    "dense": where notes in a Keep timing part are closer than one tick at its tempo (it stays faster there)}.
    ticks = every tick that must keep its time (note starts / ends, CC messages); starts = the note starts; hz =
    (starts, ends) of the Hz basses' notes. Spiderweb's own ticks, the project's PPQ."""
    out = {"events": [(0, round(60_000_000 / base))], "bad": [], "sigs": [(0, beats, 2)],
           "map": lambda t: np.asarray(t, np.int64), "jumps": [], "hz": [], "dense": []}
    if not has_tempo(tempo):
        return out
    start_u = base_us(base)
    out["events"] = [(0, start_u)]
    ticks = np.asarray(ticks, np.int64).ravel()
    lay = layout(tempo, base, ppq)
    segs, out["bad"] = file_segments(tempo, base, ppq, int(ticks.max()) + 1 if len(ticks) else 0)
    end = segs[-1][1]
    last_tick = int(ticks.max()) if len(ticks) else 0
    # a part on to the song's end stops at the last note (not a tick past it: no gap there to count)
    keeps = [(a, last_tick if e is None else e, u) for _, a, e, u in lay["keeps"]]
    keeps = [(a, e, u) for a, e, u in keeps if e > a]
    every = tempo.get("every", EVERY)
    bar = beats * ppq
    fit_bars = tempo.get("bars", True) and bar > 0
    # gaps between anchors, part by part: (first tick, end tick, time = ticks x heard us, file ticks)
    gaps = []
    for a, e, u in keeps:
        inside = ticks[(ticks > a) & (ticks < e)]
        marks = [np.array([a, e], np.int64), inside]
        if fit_bars:
            marks.append(np.arange((a // bar + 1) * bar, e, bar, dtype=np.int64))
        n = np.unique(np.concatenate(marks))
        drawn = _per_tick(segs, a, e)
        sums = np.concatenate([[0], np.cumsum(drawn, dtype=np.float64)])
        d = np.diff(n)
        total = d * u
        exact = total / ((sums[n[1:] - a] - sums[n[:-1] - a]) / d)  # (file ticks each gap needs at the drawn tempo)
        real = np.isin(n, ticks)
        close = real[:-1] & real[1:] & (exact < 1)  # (two notes under a tick apart at that tempo)
        if close.any() and not out["dense"]:
            out["dense"] = [int(n[:-1][close][0]) / ppq]
        # rounded on a running total, so the part keeps the drawn tempo on average (gap by gap, regular spam
        # rounded the same way every time: 60 BPM came out 48)
        k = np.diff(np.concatenate([[0], np.rint(np.cumsum(exact))])).astype(np.int64)
        k = np.clip(k, _least(total), total)
        gaps.append([n, total, k, drawn])
    if fit_bars and gaps:
        lengths = bar_lengths(ppq)
        _fit_bar_lines(gaps, keeps, bar, lengths)
    # the file's tempo: plain stretches shifted, Keep timing parts made of their gaps
    pairs = _Pairs()
    file_segs, bp_n, bp_f = [], [0], [0]
    offset, at = 0, 0
    for (a, e, u), (n, total, k, drawn) in zip(keeps, gaps):
        file_segs += [(x + offset, y + offset, v) for x, y, v in _cut(segs, at, a)]
        parts, last = [], None
        changes = np.flatnonzero(drawn[1:] != drawn[:-1]) + 1 + a  # (ticks where the drawn tempo changes)
        for j in range(len(k)):
            kj, tj = int(k[j]), int(total[j])
            n0, n1 = int(n[j]), int(n[j + 1])
            curved = kj > every and np.searchsorted(changes, n0, "right") < np.searchsorted(changes, n1, "left")
            got = None
            if curved:  # (a long gap over a curve: steps every "every" ticks, as drawn)
                ideal = drawn[n0 - a + (np.arange(kj) * (n1 - n0)) // kj].astype(np.float64)
                got = _fit_blocks(ideal, tj, every)
            if got is None:  # (no Domino-safe values for that many ticks: a tick more or less, rare)
                for kk in [kj] + [kj + s * w for s in range(1, 9) for w in (1, -1)]:
                    if int(_least(np.array([tj]))[0]) <= kk <= tj:
                        got = pairs.get(kk, tj)
                        if got is not None:
                            kj = k[j] = kk
                            break
                if got is None:  # (nothing near: the nearest values, not Domino-safe)
                    got = np.full(kj, tj // kj, np.int64)
                    got[:tj - int(got.sum())] += 1
                if last is not None and got[0] != last and got[-1] == last:
                    got = got[::-1]  # (the run goes on from the gap before: one change fewer)
            parts.append(got)
            last = got[-1]
        fa = a + offset
        vals = np.concatenate(parts)
        file_segs.append((fa, fa + len(vals), vals))
        f = fa + np.concatenate([[0], np.cumsum(k)])
        bp_n += n.tolist()
        bp_f += f.tolist()
        offset = int(f[-1]) - e
        at = e
    file_segs += [(x + offset, y + offset, v) for x, y, v in _cut(segs, at, end)]
    events = _runs(file_segs)
    if events[0][0] != 0:
        events.insert(0, (0, start_u))
    out["events"] = events
    bp_n, bp_f = np.array(bp_n, np.int64), np.array(bp_f, np.int64)

    def to_file(t):
        t = np.asarray(t, np.int64)
        i = np.searchsorted(bp_n, t, "right") - 1
        return bp_f[i] + (t - bp_n[i])

    if keeps:
        out["map"] = to_file
    if fit_bars and keeps:
        out["sigs"] = _signatures(keeps, gaps, bar, beats, ppq, to_file)
    # warnings
    starts = np.asarray(starts, np.int64).ravel()
    for st, plan in lay["worked"]:  # (a note in a jump plays off time where the time so far isn't the heard time)
        if not plan["ok"]:
            continue
        s0, e = beat_tick(st["from"], ppq), beat_tick(st["end"], ppq)
        on = starts[(starts > s0) & (starts < e)]
        if not len(on):
            continue
        real = _per_tick(segs, s0, e)
        for a, ke, ku in keeps:
            lo, hi = max(a, s0), min(ke, e)
            if hi > lo:
                real[lo - s0:hi - s0] = ku
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


def heard_tempo(tempo, base, ppq, end=0):
    """[(tick, us)] on Spiderweb's ticks for built-in playback: what a player of the MIDI file really plays where
    (catch-ups in place, Keep timing parts at the tempo heard before them, so their notes sound when the file's do).
    None without a tempo line."""
    if not has_tempo(tempo):
        return None
    segs, _ = file_segments(tempo, base, ppq, end)
    for _, a, e, u in layout(tempo, base, ppq)["keeps"]:
        segs = _put(segs, a, segs[-1][1] if e is None else e, u)
    return _runs(segs)


NUDGE = 0.05  # bar compensation: a gap's ticks change by at most this share (or one tick), so its tempo a hair


def _fit_bar_lines(gaps, keeps, bar, lengths):
    """Bar compensation: in each bar a Keep timing part changed, the longest gaps there get a tick more / fewer
    each so the bar's length fits a time signature (the gaps' time stays: only their tempo changes a hair). A bar
    that can't be fitted gently stays as it is (no spikes: user's "a hair")."""
    owed = {}  # bar -> (ticks the bar has, [(part, gap)] in it)
    for p, ((a, e, _), (n, total, k, _)) in enumerate(zip(keeps, gaps)):
        for j in range(len(k)):
            m = int(n[j]) // bar
            have, where = owed.get(m, (bar, []))
            owed[m] = (have - int(n[j + 1] - n[j]) + int(k[j]), where + [(p, j)])
    for m, (have, where) in owed.items():
        if not len(where):
            continue
        ks = np.array([int(gaps[p][2][j]) for p, j in where], np.int64)
        tots = np.array([int(gaps[p][1][j]) for p, j in where], np.int64)
        room = np.maximum(1, (ks * NUDGE).astype(np.int64))  # (ticks each gap may give / take)
        up, down = np.minimum(room, tots - ks), np.minimum(room, ks - _least(tots))
        order = np.argsort(-ks, kind="stable")  # (longest gaps first)
        i = int(np.searchsorted(lengths, have))
        tries = sorted(lengths[max(0, i - 3):i + 3].tolist(), key=lambda x: abs(x - have))
        for want in tries:
            delta = want - have
            if delta == 0:
                break
            cap = (up if delta > 0 else down)[order]
            if cap.sum() < abs(delta):
                continue
            share = np.zeros(len(ks), np.int64)
            left = abs(delta)
            while left:  # (one tick at a time round the longest gaps, up to each one's room)
                for o, c in zip(order.tolist(), cap.tolist()):
                    if left and abs(share[o]) < c:
                        share[o] += 1 if delta > 0 else -1
                        left -= 1
            for (p, j), s in zip(where, share.tolist()):
                gaps[p][2][j] += s
            break


def _signatures(keeps, gaps, bar, beats, ppq, to_file):
    """[(file tick, top, bottom power)]: a time signature wherever the bar length changes, up to the bar after the
    last Keep timing part (then the project's own)."""
    last = max(e for _, e, _ in keeps)
    longest = int(max(bar_lengths(ppq)))
    out, prev = [], None
    for m in range(0, last // bar + 2):
        f0, f1 = (int(x) for x in to_file(np.array([m * bar, (m + 1) * bar])))
        sig = signature(f1 - f0, ppq)
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
                continue
            f0 = at
        if sig != prev:
            out.append((f0, *sig))
            prev = sig
    if prev != (beats, 2):
        out.append((int(to_file(np.array([(last // bar + 2) * bar]))[0]), beats, 2))
    return out
