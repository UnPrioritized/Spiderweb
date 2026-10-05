"""Quick sound: Hz bass notes made from the soundfont's own notes instead of the synth playing them all. Each
different note (key, loudness step, length) is played ONCE by the synth (files/synth.py) and kept; a copy of it is
laid at every note's start (a synth adds its voices up the same way). Used by the live keys (window/hz_live.py):
the synth can't play a 128-key Hz bass in real time, this can.

Measured on 7 soundfonts (2026-10-05, dev/scratch/soundfont_check.py): 0.1-0.4 dB from the synth playing every note
(band loudness); a soundfont that picks its sounds at random (an SFZ's pitch_random) can't be copied exactly, and
one where the same key hit again cuts the old note isn't either.
Loudness: STEPS levels are recorded, evenly spaced in how loud they sound (the soundfont's own curve, measured once
on CURVE_KEY), and a note takes the nearest one turned up or down to its own loudness (the user heard it as natural
as 64 plain steps). Lengths are rounded to GATE_STEP apart (a note still starts exactly where it should, so its
pitch is exact; only its end moves, by a fraction of a millisecond in a Hz bass). Kept as the spectra the Mixer
uses (below).
Chords: notes starting together with the same step, length and loudness (every key of a Hz bass wave, mostly) are
recorded together, the synth playing them all at once ("chord"), and laid as one: a 128-key Hz bass then costs one
recording per kind of wave (not 128) and one copy a wave to mix. Past the memory limit the ones used least recently
are thrown away (recorded again when needed).
Rough copy (user 2026-10-05): effects that make every key different (Sweep, Wah: its own loudness; Slant, Off pitch,
Noisy: its own timing) break the chords up, thousands of kinds (measured: Noisy 8 000 for one held note, ~2 MB each),
past the memory and what the Mixer can add up in time (about 100 kinds sounding at once). Then neighbouring keys
are played together in bands, each as its middle key does (plan_rough: 2, 4, 8... keys a band, the finest that
needs at most MOST_KINDS); anything that fits stays exact.
Mixing (Mixer): the copies are added up in the frequency domain, block by block (a high key lays thousands of waves a
second, each ringing seconds: added one by one, the sound device got a quarter of the sound at key 100)."""

import collections
import concurrent.futures
import math
import os
import threading

import numpy as np

from files.synth import RATE, SynthError, events

STEPS = 16
GATE_STEP = 1.05
RING = 8.0  # seconds a recording may ring after its note at most (it ends where the sound has died away)
CURVE_KEY = 60
WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))
VOICES = 100000  # (one note: never cut)
PART = 2048  # frames in a block of the mixing
MOST_KINDS = 100  # different recordings a made stretch of live notes may need before its keys go in bands


class QuickSound:
    """Recordings of chords (notes played together) for one soundfont. Thread-safe: they're made in worker threads,
    the mixing asks for them from the sound device's thread."""

    def __init__(self, synth, budget_mb=1000, nofx=False):
        self.synth, self.nofx = synth, nofx  # (nofx: recorded without reverb / chorus)
        self.budget = budget_mb * 1e6
        self.font = None  # the soundfont the recordings are of
        self.kept = collections.OrderedDict()  # (keys, step velocity, gate frames) -> float16 rows, least used first
        self.size = 0  # bytes in kept
        self.lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(WORKERS)
        self.asked = set()  # being made
        self.curve = None  # (step velocity for each velocity 1..127, gain for each) once measured
        self.measuring = None
        self.error = None

    # ------------------------------------------------------------ the soundfont's loudness

    def ready(self):
        """True once the loudness curve of the synth's soundfont is known (else it's being measured: prepare)."""
        self.check_font()
        return self.curve is not None

    def check_font(self):
        """A new soundfont in the synth: everything recorded goes."""
        if self.synth.font_path != self.font:
            with self.lock:
                self.font, self.curve = self.synth.font_path, None
                self.kept.clear()
                self.size = 0
                self.asked.clear()

    def prepare(self):
        """Measures the soundfont's loudness curve in the background (once)."""
        self.check_font()
        if self.curve is None and not (self.measuring and self.measuring.is_alive()) and self.font:
            self.measuring = threading.Thread(target=self._measure, args=(self.font,), daemon=True)
            self.measuring.start()

    def _measure(self, font):
        try:
            amp = np.array([math.sqrt(float(np.mean(self._play(CURVE_KEY, v, RATE // 10)[:RATE // 4] ** 2)))
                            for v in range(1, 128)]) + 1e-12
        except SynthError as e:
            self.error = str(e)
            return
        level = 20.0 * np.log10(amp)
        steps = sorted({int(np.argmin(np.abs(level - w))) + 1 for w in np.linspace(level[0], level[-1], STEPS)})
        near = [min(steps, key=lambda s: abs(level[s - 1] - level[v - 1])) for v in range(1, 128)]
        curve = (np.array([0] + near), np.array([0.0] + [amp[v - 1] / amp[s - 1] for v, s in zip(range(1, 128), near)]))
        if self.synth.font_path == font:
            self.curve = curve

    # ------------------------------------------------------------ recordings and chords

    def _play(self, keys, vel, gate):
        """keys (one or several, together) played by the synth: their sound until it has died away."""
        keys = (keys,) if isinstance(keys, int) else keys
        st = {}
        got = self.synth.render(events(np.array([[0, gate, k, vel] for k in keys]), RATE, 60), RATE, 0,
                                gate + int(RING * RATE), VOICES, self.nofx, stats=st)
        return got[:st.get("end", len(got))]

    @staticmethod
    def gate_of(frames):
        """A note's length rounded to GATE_STEP apart (frames)."""
        return max(1, int(round(GATE_STEP ** round(math.log(max(1.0, frames)) / math.log(GATE_STEP)))))

    def plan(self, starts, keys, vels, gates, most=None):
        """For notes (arrays in start order: start frame, key, velocity 1..127, length in frames): the chords to lay
        ([(start frame, (keys, step velocity, gate), gain)]: notes starting together with the same step, length and
        loudness are one); None when they'd need more than `most` different ones. Needs ready()."""
        if not len(starts):
            return []
        steps, gains = self.curve
        vels = np.clip(vels, 1, 127)
        step, gain = steps[vels], gains[vels]
        frames = np.maximum(1.0, gates)
        gate = np.maximum(1, np.rint(GATE_STEP ** np.rint(np.log(frames) / math.log(GATE_STEP)))).astype(np.int64)
        new = np.zeros(len(starts), bool)  # (a group starts where any of them changes)
        new[0] = True
        for a in (starts, step, gate, gain):
            new[1:] |= a[1:] != a[:-1]
        firsts = np.flatnonzero(new)
        ends = np.append(firsts[1:], len(starts))
        keys = np.asarray(keys, np.int64)
        same = {}  # (the same keys again: one tuple)
        out, kinds = [], set()
        for i, j, s, v, g, n in zip(firsts.tolist(), ends.tolist(), starts[firsts].tolist(), step[firsts].tolist(),
                                    gate[firsts].tolist(), gain[firsts].tolist()):
            raw = keys[i:j].tobytes()
            group = same.get(raw)
            if group is None:
                group = same[raw] = tuple(keys[i:j].tolist())
            out.append((s, (group, int(v), g), float(n)))
            if most is not None:
                kinds.add(out[-1][1])
                if len(kinds) > most:
                    return None
        return out

    def plan_rough(self, starts, keys, vels, gates, band=1):
        """plan, with neighbouring keys played together in bands of `band` keys or more (as few as needed so that at
        most MOST_KINDS different recordings are needed; band 1 = every key as it is): (chords, keys a band)."""
        many = len(np.unique(keys))
        while True:
            got = self.plan(*banded(starts, keys, vels, gates, band), most=None if band >= many else MOST_KINDS)
            if got is not None:
                return got, band
            band *= 2

    def _keep(self, key, got):
        with self.lock:
            self.asked.discard(key)
            if got is None or key in self.kept:
                return
            self.kept[key] = got
            self.size += got[0].nbytes
            while self.size > self.budget and len(self.kept) > 1:
                _, old = self.kept.popitem(last=False)
                self.size -= old[0].nbytes

    def get(self, chord):
        """A chord's sound as the Mixer wants it ((spectra, frames)), or None when it isn't recorded yet."""
        with self.lock:
            got = self.kept.get(chord)
            if got is not None:
                self.kept.move_to_end(chord)
            return got

    def want(self, chords):
        """The chords that aren't recorded yet are recorded, in the background; how many are missing."""
        missing = 0
        for chord in set(chords):
            with self.lock:
                if chord in self.kept:
                    continue
                missing += 1
                if chord in self.asked:
                    continue
                self.asked.add(chord)
            self.pool.submit(self._record, chord, self.font)
        return missing

    def _record(self, chord, font):
        try:
            got = spectra(self._play(*chord))
        except SynthError as e:
            self.error = str(e)
            got = None
        self._keep(chord, got if font == self.font else None)

    def set_budget(self, mb):
        with self.lock:
            self.budget = mb * 1e6
            while self.size > self.budget and self.kept:
                _, old = self.kept.popitem(last=False)
                self.size -= old[0].nbytes

    def used_mb(self):
        return self.size / 1e6

    def busy(self):
        return bool(self.asked) or bool(self.measuring and self.measuring.is_alive())


def banded(starts, keys, vels, gates, band):
    """Notes (arrays in start order) with neighbouring keys in bands of `band` (counted among the keys there are):
    every key of a band plays the notes of its middle key (the others' are left out)."""
    keys = np.asarray(keys, np.int64)
    if band <= 1 or not len(keys):
        return starts, keys, vels, gates
    have = np.unique(keys)
    first = np.searchsorted(have, keys) // band * band
    size = np.minimum(band, len(have) - first)
    keep = keys == have[first + size // 2]
    first, size = first[keep], size[keep]
    pick = np.repeat(np.arange(len(first)), size)  # (each kept note once for every key of its band)
    nth = np.arange(len(pick)) - np.repeat(np.cumsum(size) - size, size)
    return starts[keep][pick], have[first[pick] + nth], vels[keep][pick], gates[keep][pick]


def spectra(rec):
    """A recording (rows) as the Mixer uses it: (its parts' spectra (frequency, part, channel), its length in frames)."""
    k = max(1, -(-len(rec) // PART))
    parts = np.zeros((k * PART, 2), np.float32)
    parts[:len(rec)] = rec
    spec = np.fft.rfft(parts.reshape(k, PART, 2), 2 * PART, axis=1)
    return np.ascontiguousarray(spec.transpose(1, 0, 2), np.complex64), len(rec)


class Mixer:
    """Adds up the chords laid at their starts (the live keys' sound), block by block (PART frames) in the
    frequency domain: a block's sound = the starts in it and in the blocks before, times the matching parts of the
    recordings. The same sum as adding a copy of each recording at each start, but its cost doesn't grow with how
    many start or how long they ring. Starts may come late (while their block is being handed over): their sound
    is added then."""

    def __init__(self):
        self.kinds = {}  # chord -> [spectra, the last blocks' starts' spectra (ring of K), the last block with a start]
        self.fresh = {}  # (chord, block) -> the starts in a block not worked out yet (PART gains)
        self.done = -1  # the last block worked out
        self.out, self.out0 = np.zeros((0, 2), np.float32), 0  # the sound worked out, from frame out0
        self.end = 0  # the frame the sound laid so far has died away at

    def lay(self, starts, chords, gains, got):
        """Chords (got: chord -> QuickSound.get) laid at frames `starts` (in order, none before what's handed over)."""
        groups = {}
        for at, chord, gain in zip(starts.tolist(), chords, gains.tolist()):
            b = at // PART
            groups.setdefault((chord, b), []).append((at - b * PART, gain))
            self.end = max(self.end, at + got[chord][1])
        for (chord, b), laid in groups.items():
            x = np.zeros(PART, np.float32)
            np.add.at(x, [o for o, _ in laid], [g for _, g in laid])
            spec = got[chord][0]
            kind = self.kinds.get(chord)
            if kind is None:
                kind = self.kinds[chord] = [spec, np.zeros((spec.shape[1], spec.shape[0]), np.complex64), b]
            if b > self.done:
                old = self.fresh.get((chord, b))
                self.fresh[(chord, b)] = x if old is None else old + x
                continue
            ring = kind[1]  # (late: its block is out already) its sound added now, and to the blocks after
            xs = np.fft.rfft(x, 2 * PART)
            ring[b % len(ring)] += xs
            kind[2] = max(kind[2], b)
            self._put(b, np.fft.irfft(xs[:, None] * spec[:, 0, :], 2 * PART, axis=0))

    def _work(self, m):
        """Block m's sound (and the start of what rings into the next one)."""
        total = np.zeros((PART + 1, 2), np.complex64)
        for chord, kind in list(self.kinds.items()):
            spec, ring, last = kind
            k = len(ring)
            x = self.fresh.pop((chord, m), None)
            if x is not None:
                ring[m % k], kind[2] = np.fft.rfft(x, 2 * PART), m
            else:
                ring[m % k] = 0
                if m < last:  # (laid ahead only: nothing sounding yet)
                    continue
                if m - last >= k:  # (died away)
                    del self.kinds[chord]
                    continue
            total += np.matmul(ring[(m - np.arange(k)) % k].T[:, None, :], spec)[:, 0]
        self._put(m, np.fft.irfft(total, 2 * PART, axis=0))
        self.done = m

    def _put(self, b, y):
        a = b * PART - self.out0
        if a + len(y) > len(self.out):
            grown = np.zeros((max(a + len(y), 2 * len(self.out)), 2), np.float32)
            grown[:len(self.out)] = self.out
            self.out = grown
        self.out[a:a + len(y)] += y

    def take(self, at, end):
        """The sound of frames at..end (everything laid before end)."""
        for m in range(self.done + 1, (end - 1) // PART + 1):
            self._work(m)
        a = at - self.out0
        got = np.zeros((end - at, 2), np.float32)
        part = self.out[a:a + (end - at)]
        got[:len(part)] = part
        keep = self.done * PART  # (what's been played goes; a late start may still add to the last block)
        if keep - self.out0 > RATE:
            self.out, self.out0 = self.out[keep - self.out0:].copy(), keep
        return got
