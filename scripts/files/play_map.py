"""A loaded MIDI's ticks as PLAYED after Change visual time signature (PLAN.md 2026-11-14): the file stays as it is,
the clock runs on the SHOWN Conductor line, so every event the readers take from the file has to be moved through
notes/loaded_map.py's links before it is turned into seconds. Shown = what Export MIDI writes (exactly: the same
rule, tick for tick). The maths: notes/loaded_shown.py (shown, first_at); here the players' small wrapper around it
(remembered answers: the reader asks for the same window ends again and again).

With no links (the usual case) every call hands its argument back at once and nothing is changed or copied."""

import numpy as np

from notes.loaded_shown import first_at, shown

_KEEP = 32  # (scalar answers remembered)
FAR = 1 << 60  # ("never": handed back as it is)


class Shown:
    """chain = loaded_map.links(app.loaded_map) ([] = nothing moved)."""

    def __init__(self, chain=()):
        self.chain = tuple(chain)
        self.on = bool(self.chain)
        self._key = hash(tuple((n.tobytes(), f.tobytes()) for n, f in self.chain)) if self.on else 0
        self._cache = {}

    def shown(self, ticks):
        """File ticks (a number or an array) -> shown ticks. No links: ticks itself, nothing made."""
        return shown(self.chain, ticks)

    def file_start(self, ticks):
        """Shown ticks -> the first file tick shown there or later (loaded_shown.first_at; scalar in = int out).
        No links: ticks itself."""
        if not self.on:
            return ticks
        if np.ndim(ticks) == 0:
            t = int(ticks)
            if t >= FAR:
                return t
            hit = self._cache.get(t)
            if hit is None:
                hit = first_at(self.chain, t)
                if len(self._cache) >= _KEEP:
                    self._cache.clear()
                self._cache[t] = hit
            return hit
        return first_at(self.chain, np.asarray(ticks, np.int64))

    def key(self):
        """Something that differs whenever the moves differ (a Kestrel song's number, caches)."""
        return self._key
