"""A gliding tempo part's values worked out when read, never every step kept (PLAN "Tempo ramps step by step").

tempo._segments hands one back for a gliding piece with more than tempo.RAMP_LAZY steps (a huge-tick song's 1.5 s
ramp is 1.4 billion of them: as a list, 10 GB). It stands where a one-per-tick int64 array stood: len(), slicing
(another Ramp), one tick's value, sum(), and np.asarray() (one per tick: only for short stretches). Readers that
can go step by step use changes() / steps() instead."""
import numpy as np

CHUNK = 1 << 16  # steps worked out at a time


class Ramp:
    """Ticks a..b (absolute) of a part whose tempo changes every `every` ticks from tick `origin`; read(starts) =
    the int64 microseconds of the steps starting at those ticks (the part's own copy of its points: an edit to the
    line later never changes it)."""
    __slots__ = ("a", "b", "origin", "every", "read")

    def __init__(self, a, b, origin, every, read):
        self.a, self.b, self.origin, self.every, self.read = int(a), int(b), int(origin), max(1, int(every)), read

    def __len__(self):
        return self.b - self.a

    def __repr__(self):
        return f"Ramp({self.a}, {self.b}, every {self.every})"

    def __getitem__(self, k):
        n = self.b - self.a
        if isinstance(k, slice):
            x, y, st = k.indices(n)
            if st != 1:
                raise ValueError("Ramp: steps of 1 only")
            return Ramp(self.a + x, self.a + max(x, y), self.origin, self.every, self.read)
        k = int(k)
        if k < 0:
            k += n
        if not 0 <= k < n:
            raise IndexError(k)
        return int(self.read(np.array([self._start(self.a + k)], np.int64))[0])

    def _start(self, t):
        return self.origin + (t - self.origin) // self.every * self.every

    def steps(self, lo=None, hi=None, chunk=CHUNK):
        """(first ticks, end ticks, us) int64 arrays of the steps over ticks lo..hi (cut to them), chunk steps at a
        time."""
        lo = self.a if lo is None else max(lo, self.a)
        hi = self.b if hi is None else min(hi, self.b)
        s = self._start(lo)
        while s < hi:
            starts = s + np.arange(min(chunk, -(-(hi - s) // self.every)), dtype=np.int64) * self.every
            us = np.asarray(self.read(starts), np.int64)
            yield np.maximum(starts, lo), np.minimum(starts + self.every, hi), us
            s = int(starts[-1]) + self.every

    def changes(self):
        """(ticks, us) int64 arrays: the first tick, then each tick where the value changes."""
        ts, vs, last = [], [], None
        for a, _, u in self.steps():
            keep = np.ones(len(u), bool)
            keep[1:] = u[1:] != u[:-1]
            if last is not None and u[0] == last:
                keep[0] = False
            ts.append(a[keep])
            vs.append(u[keep])
            last = u[-1]
        if not ts:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        return np.concatenate(ts), np.concatenate(vs)

    def sum(self):
        """Ticks x microseconds over the whole stretch, exact (a Python int)."""
        total = 0
        chunk = max(1, min(CHUNK, (1 << 62) // ((1 << 24) * self.every)))  # (each chunk's sum inside 64 bits)
        for a, b, u in self.steps(chunk=chunk):
            total += int((u * (b - a)).sum())
        return total

    def __array__(self, dtype=None, copy=None):  # noqa: ARG002 (NumPy 2 passes copy=)
        parts = [np.repeat(u, b - a) for a, b, u in self.steps()]
        out = np.concatenate(parts) if parts else np.zeros(0, np.int64)
        return out if dtype is None else out.astype(dtype)
