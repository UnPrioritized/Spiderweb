"""Domino-safe tempo rounding (moved out of tempo.py): which microsecond values come back unchanged after Domino
opens and saves a file, and the searches for whole, Domino-safe values adding up to an exact total."""

import functools
import math
import struct

import numpy as np

from notes.tempo_ramp import Ramp

SAFE_REACH = 8192  # the farthest a Domino-safe value is from any value (measured: 5212 at the slowest tempos)


# ------------------------------------------------------------ exact tempo changes for a file

def domino_safe(u):
    """True where u (microseconds per beat) comes back unchanged after Domino opens and saves the file."""
    u = np.asarray(u, np.int64)
    bpm = (6e7 / np.maximum(u, 1)).astype(np.float32).astype(np.float64)
    return np.trunc(6e7 / bpm).astype(np.int64) == u


_WINDOW = 256  # (one value: candidates checked this many at a time, not one by one; same order, same answer)


def _first_safe(cands):
    """The first Domino-safe value in cands (in 1..0xFFFFFF), or None."""
    cands = cands[(cands >= 1) & (cands <= 0xFFFFFF)]
    ok = np.nonzero(domino_safe(cands))[0]
    return int(cands[ok[0]]) if len(ok) else None


@functools.lru_cache(maxsize=1 << 16)
def _nearest1(x):
    """nearest_safe of one value: k = 0, then the nearer side, then the other, k = 1, 2... (remembered)"""
    u = int(np.rint(x))
    if _safe1(u):  # (k = 0: most often, no array needed)
        return u
    s = 1 if x >= u else -1
    for k in range(1, 33):  # (the next few in plain Python, the same order)
        if _safe1(u + s * k):
            return u + s * k
        if _safe1(u - s * k):
            return u - s * k
    for k0 in range(0, SAFE_REACH, _WINDOW):
        k = np.arange(max(k0, 1), min(k0 + _WINDOW, SAFE_REACH))
        cands = np.column_stack([u + s * k, u - s * k]).ravel()
        if k0 == 0:
            cands = np.concatenate([[u], cands])
        got = _first_safe(cands)
        if got is not None:
            return got
    return u


def _next1(u, way):
    """next_safe of one value."""
    for k0 in range(1, SAFE_REACH, _WINDOW):
        got = _first_safe(u + way * np.arange(k0, min(k0 + _WINDOW, SAFE_REACH)))
        if got is not None:
            return got
    return u


NEAREST_COMPILED_FROM = 256  # nearest_safe: this many values or more = the compiled loop (tempo.rs nearest_safe_arr)


def nearest_safe(x):
    """The Domino-safe whole microseconds nearest x (array of floats), kept in 1..0xFFFFFF."""
    x = np.clip(np.asarray(x, float), 1, 0xFFFFFF)
    if x.ndim == 1 and x.size <= 32:  # (one at a time in _fit_blocks, a short gap's few: thousands of calls)
        return np.array([_nearest1(v) for v in x.tolist()], np.int64)
    if x.ndim == 1 and x.size >= NEAREST_COMPILED_FROM:  # (the compiled twin: a long ramp read in pieces of 65,536, half its time)
        from files.speed import loops
        fast = getattr(loops(), "nearest_safe", None)
        got = None if fast is None else fast(x, SAFE_REACH)
        if got is not None:
            return got
    u = np.rint(x).astype(np.int64)
    out = np.where(domino_safe(u), u, 0)
    left = out == 0
    for k in range(1, SAFE_REACH):
        if not left.any():
            break
        idx = np.nonzero(left)[0]
        base = u[idx]
        # the nearer side first (x may sit just off u)
        first = np.where(x[idx] >= base, base + k, base - k)
        second = np.where(x[idx] >= base, base - k, base + k)
        for cand in (first, second):
            ok = (cand >= 1) & (cand <= 0xFFFFFF) & domino_safe(np.clip(cand, 1, 0xFFFFFF)) & (out[idx] == 0)
            out[idx[ok]] = cand[ok]
        left = out == 0
    return np.where(out == 0, u, out)


def next_safe(u, way):
    """The next Domino-safe value past u (array) going way (+1 / -1); u itself where there's none in reach."""
    u = np.asarray(u, np.int64)
    if len(u) == 1:
        return np.array([_next1(int(u[0]), way)], np.int64)
    if len(u) <= 32:  # (a short gap's few, thousands of calls: one by one in plain Python, the same first one)
        return np.array([_next_small(v, way) for v in u.tolist()], np.int64)
    out = u.copy()
    # (only the ones still looking: one step at a time over the whole array took 15 s on a catch-up, hunt
    # 2026-10-24); most find one within a few steps
    idx = np.arange(len(u))
    for k in range(1, 9):
        if not len(idx):
            return out
        cand = u[idx] + way * k
        ok = (cand >= 1) & (cand <= 0xFFFFFF) & domino_safe(np.clip(cand, 1, 0xFFFFFF))
        out[idx[ok]] = cand[ok]
        idx = idx[~ok]
    if len(idx):  # (slow tempos: safe values far apart; walked by the BPMs a file can store instead)
        out[idx] = _next_by_bpm(u[idx], way)
    return out


@functools.lru_cache(maxsize=1 << 16)
def _next_small(u, way):
    """next_safe of one value in plain Python, _next_by_bpm's walk (remembered: one export asked the same value
    thousands of times, 92 whole numbers from the next safe one)."""
    c = u + way
    while 1 <= c <= 0xFFFFFF and (c - u) * way < SAFE_REACH:
        v = int(6e7 / struct.unpack("f", struct.pack("f", 6e7 / c))[0])
        if v == c:
            return c
        c = v if (v - c) * way > 0 else c + way
    return u


def _next_by_bpm(u, way):
    """next_safe for values whose next safe value is far: a candidate c that isn't safe comes back from Domino as
    v (6e7 / its float32 BPM, cut); no safe value lies between c and v, so when v is further on the next candidate
    is v itself (slow tempos: thousands of whole numbers skipped at once), else c + 1. The same first one as
    trying every whole number. u itself where none is within SAFE_REACH."""
    u = np.asarray(u, np.int64)
    out = u.copy()
    c = u + way
    left = np.ones(len(u), bool)
    while left.any():
        i = np.flatnonzero(left)
        ci = c[i]
        live = (ci >= 1) & (ci <= 0xFFFFFF) & ((ci - u[i]) * way < SAFE_REACH)
        left[i[~live]] = False
        i, ci = i[live], ci[live]
        if not len(i):
            break
        v = np.trunc(6e7 / (6e7 / ci).astype(np.float32).astype(np.float64)).astype(np.int64)
        safe = v == ci
        out[i[safe]] = ci[safe]
        left[i[safe]] = False
        on = ~safe
        c[i[on]] = np.where((v[on] - ci[on]) * way > 0, v[on], ci[on] + way)
    return out


def _fit_exact(ideal, target):
    """Whole, Domino-safe values near ideal (array, one per tick) adding up to exactly target. None if they can't.
    A flat catch-up comes out as a few runs of one value (few tempo changes), not a new value on every tick."""
    n = len(ideal)
    if n == 0 or not n <= target <= n * 0xFFFFFF:
        return None
    if ideal.max() - ideal.min() < 1:  # a flat catch-up: two values at most (user saw 4 that looked alike)
        got = _flat_pair(n, target)
        if got is not None:
            return got
    ideal = ideal * (target / ideal.sum())
    out = nearest_safe(ideal)
    body = out[:max(0, n - 2)]  # (the last two are picked as a pair at the end, to land exactly)
    for _ in range(8):  # rounding left over: the first ticks step to their next safe value until it's used up
        rest = target - int(body.sum()) - int(np.sum(ideal[len(body):]))
        way = 1 if rest > 0 else -1
        nxt = next_safe(body, way)
        step = np.abs(nxt - body)
        take = np.cumsum(step) <= abs(rest)
        if not take.any():
            break
        body[take] = nxt[take]
    out[:len(body)] = body
    rest = target - int(out[:max(0, n - 2)].sum())
    if n == 1:  # (only a Domino-safe value: else it can't, user's rule)
        out[0] = rest
        return out if 1 <= rest <= 0xFFFFFF and domino_safe([rest])[0] else None
    # the last two: a Domino-safe pair adding up to what's left; none near = it can't
    half = rest / 2
    a0 = int(round(half))
    for k in range(SAFE_REACH):
        for a in (a0 - k, a0 + k):
            b = rest - a
            if _safe1(a) and _safe1(b):  # (plain Python: thousands of gaps, one try each step)
                out[n - 2], out[n - 1] = a, b
                return out
    return None


def _runs(segs):
    """[(a, b, us)] -> [(tick, us)] only where the value changes."""
    arr = getattr(segs, "arr", None)  # (ds5: tempo.Segs: the pieces' starts + values as arrays already)
    if arr is not None and len(segs) > 64:
        a, u = arr
        keep = np.ones(len(u), bool)
        keep[1:] = u[1:] != u[:-1]
        return list(zip(a[keep].tolist(), u[keep].tolist()))
    if len(segs) > 64 and all(type(u) is int for _, _, u in segs):  # (all held: at once, 2026-11-14; the same)
        a = np.fromiter((s[0] for s in segs), np.int64, len(segs))
        u = np.fromiter((s[2] for s in segs), np.int64, len(segs))
        keep = np.ones(len(u), bool)
        keep[1:] = u[1:] != u[:-1]
        return list(zip(a[keep].tolist(), u[keep].tolist()))
    out = []
    for a, b, u in segs:
        if isinstance(u, int):
            vals, ticks = [u], [a]
        elif isinstance(u, Ramp):  # (a long ramp: its changes step by step, never one value a tick)
            t, v = u.changes()
            ticks, vals = (t + (a - u.a)).tolist(), v.tolist()  # (the piece's own ticks, even if shifted)
        else:
            keep = np.concatenate([[True], u[1:] != u[:-1]])
            vals, ticks = u[keep].tolist(), np.arange(a, b)[keep].tolist()
        for t, v in zip(ticks, vals):
            if not out or out[-1][1] != v:
                out.append((t, v))
    return out


def _fit_blocks(ideal, target, every):
    """_fit_exact with a new value only every `every` ticks (user: some programs don't like one per tick): each
    block takes the Domino-safe value nearest what keeps the sum on track (what's off carried on to the next), the
    last block lands exactly with one or two values (_flat_pair). A flat catch-up: two values at most anyway."""
    n = len(ideal)
    if n <= every and every > 1:  # (one block, drawn as one step: one or two values like a flat catch-up)
        got = _flat_pair(n, target) if n <= target <= n * 0xFFFFFF else None
        return got if got is not None else _fit_exact(ideal, target)
    if every <= 1 or ideal.max() - ideal.min() < 1:
        return _fit_exact(ideal, target)
    if not n <= target <= n * 0xFFFFFF:
        return None
    starts = np.arange(0, n, every)
    lens = np.diff(np.append(starts, n))
    want = ideal[starts] * lens
    want = want * (target / want.sum())  # (each block's share of the target)
    out = np.empty(n, np.int64)
    done, owed = 0, 0.0
    # (plain Python per block, _nearest1 straight = nearest_safe of one value: its NumPy wrapping cost more than the
    # search, thousands of gaps an export; the same values)
    wl, ll, sl = want.tolist(), lens.tolist(), starts.tolist()
    top = float(0xFFFFFF)
    for k in range(len(sl) - 1):
        u = _nearest1(min(max((wl[k] + owed) / ll[k], 1.0), top))
        out[sl[k]:sl[k] + ll[k]] = u
        owed += wl[k] - u * ll[k]
        done += u * ll[k]
    last, rest = lens[-1], target - done
    if not last <= rest <= last * 0xFFFFFF:
        return _fit_exact(ideal, target)
    tail = _flat_pair(int(last), int(rest))
    if tail is None:
        tail = _fit_exact(np.full(int(last), rest / last), int(rest))
        if tail is None:
            return _fit_exact(ideal, target)
    out[starts[-1]:] = tail
    return out


def flat_pair_runs(n, target):
    """_flat_pair for a stretch too long to list (a huge-tick song's catch-up): (A, B, ticks of A) in plain
    Python whole numbers (n x B passes 64 bits there), the same order of trying (the two whole numbers round the
    average first, then the nearest safe pair); None if none."""
    n, target = int(n), int(target)
    if n <= 0 or not n <= target <= n * 0xFFFFFF:
        return None
    if n < 1 << 39 and target < 1 << 53:  # (the compiled twin: every product inside 64 bits, target / n the same)
        from files.speed import loops
        fast = getattr(loops(), "pair_runs", None)
        if fast is not None:
            out = np.empty(3, np.int64)
            got = fast(n, target, PAIR_REACH, SAFE_REACH, out)
            if got == 0:
                return tuple(out.tolist())
            if got == 1:
                return None
    a, r = divmod(target, n)
    if r == 0 and _safe1(a):
        return a, a, n
    if r and _safe1(a) and _safe1(a + 1):
        return a, a + 1, n * (a + 1) - target
    q = target / n
    downs, ups = _safe_side(math.floor(q), -1), _safe_side(math.ceil(q), 1)
    best = None
    for x in downs:
        for y in ups:
            if x == y:
                if x * n == target:
                    return x, x, n
                continue
            k, rest = divmod(n * y - target, y - x)
            if rest == 0 and 0 <= k <= n:
                far = (q - x) + (y - q)
                if best is None or far < best[0]:
                    best = (far, x, y, k)
    return None if best is None else best[1:]


def fit_block_runs(ideal, lens, target):
    """_fit_blocks for a stretch too long to list: ideal = the wanted us at each block's first tick, lens = the
    blocks' ticks (int64 array or Python ints); each block the Domino-safe value nearest what keeps the sum on
    track, the last block landing exactly with a flat pair. (block values, (A, B, ticks of A) of the last) or None.
    (Arrays all the way when the compiled loop does the carrying: millions of blocks as lists cost ~0.5 s.)"""
    la = np.asarray(lens)
    if la.dtype != np.int64:
        la = np.array([int(x) for x in lens], object)
    wa = (np.asarray(ideal, np.float64) * la.astype(np.float64))
    wa = wa * (target / wa.sum())
    top = float(0xFFFFFF)
    vals, done = _carried_at_once(wa, la)
    if vals is None and len(la) > CARRY_COMPILED_FROM:
        got = _carry_compiled(wa, la)
        if got is not None:
            vals, done = got
    if vals is None:  # (the compiled loop's twin: the same values)
        ll, wl = la.tolist(), wa.tolist()  # (Python ints and floats)
        vals, done, owed = [], 0, 0.0
        for k in range(len(ll) - 1):
            u = _nearest1(min(max((wl[k] + owed) / ll[k], 1.0), top))
            vals.append(u)
            owed += wl[k] - u * ll[k]
            done += u * ll[k]
    # (no Domino-safe pair lands the last block: the last few blocks together as one pair instead; the per-tick
    # way _fit_blocks falls back to can't be listed here)
    ll = la[-TAIL_BLOCKS - 1:].tolist()  # (the last blocks, Python ints: ll[i - first] = block i)
    first = len(la) - len(ll)
    for m in range(1, min(TAIL_BLOCKS, len(la)) + 1):
        if m > 1:
            u = vals.pop()
            done -= u * ll[len(vals) - first]
        tail = flat_pair_runs(sum(ll[len(vals) - first:]), target - done)
        if tail is not None:
            return vals, tail
    return None


def _carried_at_once(wl, ll):
    """fit_block_runs' carry loop over every block but the last, all at once when the blocks are one length L and
    every value comes out a Domino-safe whole number in range: then "nearest to what keeps the sum on track, the rest
    carried on" = the difference of the running total's rounded counts (u_k = rint(W_k+1 / L) - rint(W_k / L)).
    (values, their ticks x us) or (None, 0) for the loop."""
    m = len(ll) - 1
    if m <= 0:
        return [], 0
    L = int(ll[0])
    if bool((ll[:m] != L).any()):
        return None, 0
    w = np.concatenate([[0.0], np.cumsum(np.asarray(wl[:m], np.float64))])
    if w[-1] >= 2.0 ** 52:  # (running totals past exact decimals: the loop)
        return None, 0
    u = np.diff(np.rint(w / L)).astype(np.int64)
    if not len(u) or u.min() < 1 or u.max() > 0xFFFFFF or not bool(domino_safe(u).all()):
        return None, 0
    return u.tolist(), int(u.sum()) * L


CARRY_COMPILED_FROM = 64  # fit_block_runs: more blocks than this = the compiled carry loop (~1.7 us a block in Python)


def carry_compiled_there():
    """True when the own loops have the compiled carry loop (long catch-ups take ~10x less time)."""
    from files.speed import loops
    return getattr(loops(), "carry_blocks", None) is not None


def _carry_compiled(wl, ll):
    """fit_block_runs' carry loop by the own loops (tempo.rs carry_blocks): (values, their ticks x us) or None (not
    there / block ticks past 64 bits: the Python loop)."""
    from files.speed import loops
    fast = loops()
    if getattr(fast, "carry_blocks", None) is None or ll.dtype != np.int64 or int(ll.max()) >= 1 << 62:
        return None
    return fast.carry_blocks(wl, ll, len(ll) - 1, SAFE_REACH)


TAIL_BLOCKS = 8  # fit_block_runs: the last blocks tried together when the last one can't land alone


PAIR_REACH = 48  # Domino-safe values tried on each side of a flat catch-up's tempo


def _safe_side(start, way):
    """Up to PAIR_REACH Domino-safe values from start (included when safe) going way (+1 / -1)."""
    out, u = [], int(start) - way
    while len(out) < PAIR_REACH:  # (a window of candidates at a time; a gap of SAFE_REACH or more ends it)
        cands = u + way * np.arange(1, 2 * _WINDOW + 1)
        cands = cands[(cands >= 1) & (cands <= 0xFFFFFF)]
        safe = cands[domino_safe(cands)] if len(cands) else cands
        if not len(safe):
            nxt = _next1(u, way)
            if nxt == u:
                break
            out.append(nxt)
            u = nxt
            continue
        out += safe[:PAIR_REACH - len(out)].tolist()
        u = out[-1]
    return out


def _safe1(u):
    """domino_safe of one whole number, in plain Python (the same float32 rounding; thousands of calls a second)."""
    if not 1 <= u <= 0xFFFFFF:
        return False
    bpm = struct.unpack("f", struct.pack("f", 6e7 / u))[0]
    return int(6e7 / bpm) == u


_FAR = object()  # (_near_pair: safe values too far apart here, the full search decides)


def _near_pair(n, target):
    """_flat_pair's answer found from the nearest safe values out (plain Python, a few each side: the full search
    built PAIR_REACH each side and tried every pair, 62,000 times in one export), stopping once no pair further
    out can be as near; the same pair (same nearness sums, same order on a tie). _FAR when the safe values are
    far apart (the full search then)."""
    q = target / n

    def side(u, way):
        while True:
            for _ in range(64):
                if _safe1(u):
                    yield u
                    break
                u += way
            else:
                raise LookupError
            u += way

    try:
        gd, gu = side(math.floor(q), -1), side(math.ceil(q), 1)
        downs, ups = [next(gd)], [next(gu)]
        best, done = None, set()
        while True:
            for ia, av in enumerate(downs):
                for ib, bv in enumerate(ups):
                    if (ia, ib) in done:
                        continue
                    done.add((ia, ib))
                    if bv == av:
                        continue
                    gap = bv - av
                    k, rest = divmod(n * bv - target, gap)
                    if rest or not 0 <= k <= n:
                        continue
                    far = (q - float(av)) + (float(bv) - q)
                    if best is None or (far, ia, ib) < best[:3]:
                        best = (far, ia, ib, av, bv, k)
            # (a pair with a further value: at least this far; strictly further = can't win, ties included)
            limit = math.inf if best is None else best[0] + 1e-6
            grow_d = len(downs) < PAIR_REACH and ups[0] - (downs[-1] - 1) <= limit
            grow_u = len(ups) < PAIR_REACH and (ups[-1] + 1) - downs[0] <= limit
            if not grow_d and not grow_u:
                break
            if grow_d:
                downs.append(next(gd))
            if grow_u:
                ups.append(next(gu))
    except (LookupError, StopIteration):
        return _FAR
    if best is None:
        return None if len(downs) >= PAIR_REACH and len(ups) >= PAIR_REACH else _FAR
    _, ia, ib, av, bv, k = best  # (a tie: the full search's order = by the down value's place, then the up's)
    return np.concatenate([np.full(k, av, np.int64), np.full(n - k, bv, np.int64)])


def _flat_pair(n, target):
    """n Domino-safe values adding up to exactly target, all one value or a run of A then a run of B (one or two
    tempo changes); the pair nearest the average first. None if no such pair is near. Remembered as (A, B, ticks
    of A) by (n, target) (2026-10-29: a Keep the audio export searched the same gaps again, thousands of times)."""
    key = (int(n), int(target))
    got = _pairs.get(key, _FAR)
    if got is _FAR:
        made = _flat_pair_search(n, target)
        got = None if made is None else (int(made[0]), int(made[-1]), int(np.count_nonzero(made == made[0])))
        if len(_pairs) > PAIRS_KEPT:
            _pairs.clear()
        _pairs[key] = got
    if got is None:
        return None
    a, b, k = got
    return np.full(key[0], a, np.int64) if k == key[0] else \
        np.concatenate([np.full(k, a, np.int64), np.full(key[0] - k, b, np.int64)])


PAIRS_KEPT = 200_000  # _flat_pair's answers remembered (a few dozen bytes each)
_pairs = {}


def _flat_pair_search(n, target):
    a, r = divmod(int(target), int(n))  # (most often: the two whole numbers round the average, both safe; that's
    if r == 0 and _safe1(a):  # the nearest pair, as the search below finds; hunt 2026-10-24: export speed)
        return np.full(n, a, np.int64)
    if r and _safe1(a) and _safe1(a + 1):
        k = n * (a + 1) - target
        return np.concatenate([np.full(k, a, np.int64), np.full(n - k, a + 1, np.int64)])
    got = _near_pair(n, target)
    if got is not _FAR:
        return got
    q = target / n
    downs, ups = _safe_side(math.floor(q), -1), _safe_side(math.ceil(q), 1)
    if not downs or not ups:
        return None
    # (every pair at once, in the order a then b; n * b < 2^63: values < 2^24, blocks far under 2^39 ticks)
    a = np.repeat(np.array(downs, np.int64), len(ups))
    b = np.tile(np.array(ups, np.int64), len(downs))
    same = b == a
    hit = np.nonzero(same & (a * n == target))[0]
    if len(hit):
        return np.full(n, int(a[hit[0]]), np.int64)
    gap = np.where(same, 1, b - a)
    k, rest = (n * b - target) // gap, (n * b - target) % gap  # (k ticks of a, the rest of b)
    ok = np.nonzero(~same & (rest == 0) & (k >= 0) & (k <= n))[0]
    if not len(ok):
        return None
    far = (q - a[ok].astype(float)) + (b[ok].astype(float) - q)
    i = ok[int(np.argmin(far))]  # (the first of the nearest, as before)
    a, b, k = int(a[i]), int(b[i]), int(k[i])
    return np.concatenate([np.full(k, a, np.int64), np.full(n - k, b, np.int64)])
