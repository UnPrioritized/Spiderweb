"""A gliding tempo part's values worked out when read, never every step kept (PLAN "Tempo ramps step by step").

tempo._segments hands one back for a gliding piece with more than tempo.RAMP_LAZY steps (a huge-tick song's 1.5 s
ramp is 1.4 billion of them: as a list, 10 GB). Its values come as RUNS (steps in a row with the same whole
microseconds): a giant ramp has only as many runs as different values (60 M -> 30 M BPM = 1 and 2 us: 2 runs), so
the tempo changes it gives (heard_tempo, the export) stay a short list.
Runs are found by reading every step (a ramp up to SCAN_MOST steps, or a formula curve: it can turn back), or past
that, on a curve going one way only (straight / bent lines, S-curves), by halving between steps that differ: the
same values at a cost growing with the runs, not the steps.
It stands where a one-per-tick int64 array stood: len(), slicing (another Ramp), one tick's value, sum(), and
np.asarray() (one per tick: only for short stretches)."""
import numpy as np

CHUNK = 1 << 16  # steps read at a time
SCAN_MOST = 1 << 24  # ramps up to this many steps are read step by step (~1 s at most; halving costs more there)
SHORT_GAP = 64  # halving stops at gaps this short: their steps are read at once
HALVE_COST = 24  # about this many reads find one change by halving (more changes than gap / this: read it whole)
GAPS_READ_MOST = 1 << 22  # steps read at once when gaps are read whole
slow_hook = None  # f(True / False) around working out a ramp past SCAN_MOST steps (the App: a status line, its thread only)
PAD = 4098  # reads padded to at least this many steps: a formula curve is worked out the same way however many are
# read (tempo._apply reads it off its samples past FORMULA_SAMPLES + 1)


class RampRule:
    """One gliding piece's steps: step k starts at tick origin + k x every; read(starts) = their int64 microseconds.
    lo..hi = the piece's ticks (steps k0..k1). monotone = its curve goes one way only (halving allowed). Its runs
    are worked out once, shared by every slice of it (Ramp)."""
    __slots__ = ("read", "origin", "every", "k0", "k1", "monotone", "_runs")

    def __init__(self, read, origin, every, lo, hi, monotone):
        self.read, self.origin, self.every = read, int(origin), max(1, int(every))
        self.k0 = (int(lo) - self.origin) // self.every
        self.k1 = (int(hi) - 1 - self.origin) // self.every
        self.monotone = bool(monotone)
        self._runs = None

    def values(self, ks):
        """The int64 microseconds of steps ks (int64 array, inside k0..k1)."""
        ks = np.asarray(ks, np.int64)
        n = len(ks)
        if n < PAD:
            ks = np.concatenate([ks, np.full(PAD - n, ks[-1] if n else self.k0, np.int64)])
        return np.asarray(self.read(self.origin + ks * self.every), np.int64)[:n]

    def runs(self):
        """(first step of each run, its us) int64 arrays; the first run starts at k0, neighbours always differ."""
        if self._runs is None:
            n = self.k1 - self.k0 + 1
            hook = slow_hook if n > SCAN_MOST else None
            if hook:
                hook(True)
            try:
                got = self._halved(n) if self.monotone and n > SCAN_MOST else None
                self._runs = self._scanned() if got is None else got
            finally:
                if hook:
                    hook(False)
        return self._runs

    def _scanned(self):
        ks, vs, last = [], [], None
        for k in range(self.k0, self.k1 + 1, CHUNK):
            idx = np.arange(k, min(k + CHUNK, self.k1 + 1), dtype=np.int64)
            v = self.values(idx)
            keep = np.ones(len(v), bool)
            keep[1:] = v[1:] != v[:-1]
            if last is not None and v[0] == last:
                keep[0] = False
            ks.append(idx[keep])
            vs.append(v[keep])
            last = v[-1]
        return np.concatenate(ks), np.concatenate(vs)

    def _halved(self, steps_most=None):
        """Runs of a one-way curve: steps read on a grid, then each gap whose ends differ halved down to the step
        where the value changes (a one-way curve can't change and come back between equal ends). None when the
        grid shows halving would read more than steps_most steps (reading them all is quicker then)."""
        grid = np.unique(np.linspace(self.k0, self.k1, CHUNK).round().astype(np.int64))
        gv = self.values(grid)
        at, val = [], []
        on = gv[1:] != gv[:-1]
        lo, hi, vlo, vhi = grid[:-1][on], grid[1:][on], gv[:-1][on], gv[1:][on]
        if steps_most is not None and int(np.minimum(np.abs(vhi - vlo) * HALVE_COST, hi - lo).sum()) > steps_most // 2:
            return None
        while len(lo):
            # (a gap read whole when short, or when it can hold so many changes that halving costs more: a one-way
            # curve changes at most |vhi - vlo| times between its ends; ~HALVE_COST reads a change by halving)
            gap = hi - lo
            whole = (gap <= SHORT_GAP) | (np.minimum(np.abs(vhi - vlo), gap) * HALVE_COST > gap)
            if whole.any():
                a, v = self._gaps_read(lo[whole], hi[whole], vlo[whole], vhi[whole])
                at += a
                val += v
            lo, hi, vlo, vhi = lo[~whole], hi[~whole], vlo[~whole], vhi[~whole]
            if not len(lo):
                break
            mid = (lo + hi) // 2
            vm = self.values(mid)
            left, right = vlo != vm, vm != vhi
            lo, hi, vlo, vhi = (np.concatenate([lo[left], mid[right]]), np.concatenate([mid[left], hi[right]]),
                                np.concatenate([vlo[left], vm[right]]), np.concatenate([vm[left], vhi[right]]))
        at = np.concatenate([np.array([self.k0], np.int64)] + at)
        val = np.concatenate([gv[:1]] + val)
        order = np.argsort(at, kind="stable")
        return at[order], val[order]

    def _gaps_read(self, lo, hi, vlo, vhi):
        """_gap_changes over many gaps, in batches of at most GAPS_READ_MOST steps (never billions at once)."""
        at, val = [], []
        cum = np.cumsum(hi - lo)
        i = 0
        while i < len(lo):
            j = max(i + 1, int(np.searchsorted(cum, (cum[i - 1] if i else 0) + GAPS_READ_MOST, "right")))
            a, v = self._gap_changes(lo[i:j], hi[i:j], vlo[i:j], vhi[i:j])
            at.append(a)
            val.append(v)
            i = j
        return at, val

    def _gap_changes(self, lo, hi, vlo, vhi):
        """The steps where the value changes inside gaps lo..hi (ends' values vlo / vhi known): every step in them
        read, (steps, values) of the changes."""
        n = hi - lo - 1  # (steps strictly inside each gap)
        tot = int(n.sum())
        if tot:
            first = np.repeat(lo + 1, n)
            idx = first + (np.arange(tot, dtype=np.int64) - np.repeat(np.cumsum(n) - n, n))
            iv = self.values(idx)
        else:
            idx = iv = np.zeros(0, np.int64)
        # each gap's steps lo, inside..., hi with their values, laid out in a row: a change = differs from the one before
        cnt = n + 2
        start = np.cumsum(cnt) - cnt
        ks = np.empty(int(cnt.sum()), np.int64)
        vs = np.empty(len(ks), np.int64)
        ks[start], vs[start] = lo, vlo
        ks[start + cnt - 1], vs[start + cnt - 1] = hi, vhi
        inner = np.ones(len(ks), bool)
        inner[start] = False
        inner[start + cnt - 1] = False
        ks[inner], vs[inner] = idx, iv
        ch = np.ones(len(ks), bool)
        ch[1:] = vs[1:] != vs[:-1]
        ch[start] = False  # (a gap's own start is never a change it found)
        return ks[ch], vs[ch]


class Ramp:
    """Ticks a..b (absolute) of a RampRule's steps."""
    __slots__ = ("a", "b", "rule")

    def __init__(self, a, b, rule):
        self.a, self.b, self.rule = int(a), int(b), rule

    @property
    def every(self):
        return self.rule.every

    def __len__(self):
        return self.b - self.a

    def __repr__(self):
        return f"Ramp({self.a}, {self.b}, every {self.rule.every})"

    def __getitem__(self, k):
        n = self.b - self.a
        if isinstance(k, slice):
            x, y, st = k.indices(n)
            if st != 1:
                raise ValueError("Ramp: steps of 1 only")
            return Ramp(self.a + x, self.a + max(x, y), self.rule)
        k = int(k)
        if k < 0:
            k += n
        if not 0 <= k < n:
            raise IndexError(k)
        r = self.rule
        ks, vs = r.runs()
        i = int(np.searchsorted(ks, (self.a + k - r.origin) // r.every, "right")) - 1
        return int(vs[max(0, i)])

    def step_count(self):
        r = self.rule
        return (self.b - 1 - r.origin) // r.every - (self.a - r.origin) // r.every + 1 if self.b > self.a else 0

    def spans(self, lo=None, hi=None):
        """(first ticks, end ticks, us) int64 arrays of the runs over ticks lo..hi (cut to them and to a..b)."""
        lo = self.a if lo is None else max(lo, self.a)
        hi = self.b if hi is None else min(hi, self.b)
        if hi <= lo:
            z = np.zeros(0, np.int64)
            return z, z, z
        r = self.rule
        ks, vs = r.runs()
        s = r.origin + ks * r.every
        e = np.append(s[1:], r.origin + (r.k1 + 1) * r.every)
        i = max(0, int(np.searchsorted(s, lo, "right")) - 1)
        j = int(np.searchsorted(s, hi, "left"))
        return np.maximum(s[i:j], lo), np.minimum(e[i:j], hi), vs[i:j]

    def steps(self, lo=None, hi=None):
        """spans() as one (first ticks, end ticks, us) piece (the readers' loop form)."""
        a, b, u = self.spans(lo, hi)
        if len(a):
            yield a, b, u

    def changes(self):
        """(ticks, us) int64 arrays: the first tick, then each tick where the value changes."""
        a, _, u = self.spans()
        return a, u

    def sum(self):
        """Ticks x microseconds over the whole stretch, exact (a Python int)."""
        return self.sum_to(self.b)

    def sum_to(self, t):
        """Ticks x microseconds from a up to tick t (exact)."""
        a, b, u = self.spans(self.a, t)
        if not len(a):
            return 0
        if (int(b[-1]) - int(a[0])) * int(u.max()) < 1 << 62:  # (each product and the total inside 64 bits)
            return int(((b - a) * u).sum())
        return sum(x * y for x, y in zip((b - a).tolist(), u.tolist()))

    def __array__(self, dtype=None, copy=None):  # noqa: ARG002 (NumPy 2 passes copy=)
        a, b, u = self.spans()
        out = np.repeat(u, b - a)
        return out if dtype is None else out.astype(dtype)
