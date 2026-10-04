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
pitch is exact; only its end moves, by a fraction of a millisecond in a Hz bass). Kept at half precision (-76 dB of
hiss).
Chords: notes starting together with the same step, length and loudness (every key of a Hz bass wave, mostly) are
recorded together, the synth playing them all at once ("chord"), and laid as one: a 128-key Hz bass then costs one
recording per kind of wave (not 128) and one copy a wave to mix. Past the memory limit the ones used least recently
are thrown away (recorded again when needed)."""

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

    def plan(self, starts, keys, vels, gates):
        """For notes (arrays in start order: start frame, key, velocity 1..127, length in frames): the chords to lay
        ([(start frame, (keys, step velocity, gate), gain)]: notes starting together with the same step, length and
        loudness are one). Needs ready()."""
        steps, gains = self.curve
        vels = np.clip(vels, 1, 127)
        out, group, last = [], [], None
        for s, k, v, g in zip(starts.tolist(), keys.tolist(), vels.tolist(), gates.tolist()):
            what = (s, int(steps[v]), self.gate_of(g), float(gains[v]))
            if what != last and group:
                out.append((last[0], (tuple(group), last[1], last[2]), last[3]))
                group = []
            group.append(int(k))
            last = what
        if group:
            out.append((last[0], (tuple(group), last[1], last[2]), last[3]))
        return out

    def _keep(self, key, got):
        with self.lock:
            self.asked.discard(key)
            if got is None or key in self.kept:
                return
            self.kept[key] = got
            self.size += got.nbytes
            while self.size > self.budget and len(self.kept) > 1:
                _, old = self.kept.popitem(last=False)
                self.size -= old.nbytes

    def get(self, chord):
        """A chord's sound (float16 rows), or None when it isn't recorded yet."""
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
            got = self._play(*chord).astype(np.float16)
        except SynthError as e:
            self.error = str(e)
            got = None
        self._keep(chord, got if font == self.font else None)

    def set_budget(self, mb):
        with self.lock:
            self.budget = mb * 1e6
            while self.size > self.budget and self.kept:
                _, old = self.kept.popitem(last=False)
                self.size -= old.nbytes

    def used_mb(self):
        return self.size / 1e6

    def busy(self):
        return bool(self.asked) or bool(self.measuring and self.measuring.is_alive())
