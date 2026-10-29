"""Slow down to keep the sound going (13f, user 2026-10-29; Settings → Sound, a tick for Kestrel and one for Built-in
BASSMIDI, off at first): when the synth can't make the sound as fast as the song plays, the song plays slower
instead, the pitch unchanged (fewer note starts a real second = the synth catches up), and creeps back to its own
speed once there's room. Notes are left out ("Lower it by itself") only once it's at the slowest.

SlowDown is the brain only: the window's play tick hands it the synth's load (time it takes to make the sound / the
time that sound lasts: 1 = just keeping up) and gets the speed to play at (Player.set_speed)."""

import time

SLOWEST = 0.1  # (user 2026-10-29: down to 10 %; past it, notes left out / stutters as without it)
HIGH = 0.8  # load over this = slower (under "Lower it by itself"'s 0.9: slowing comes first)
TARGET = 0.65  # slowed so the load lands about here (load ~ note starts a real second ~ speed)
LOW = 0.5  # load under this for CALM_S = faster again...
CALM_S = 0.5
RISE = 1.08  # ...by this much every RISE_S (~2.5 s from half speed back to 1x)
RISE_S = 0.2
DOWN_S = 0.15  # (another cut at most this often: each one takes a moment to show in the load)
SMOOTH = 0.3  # weight of the newest reading
WARM_S = 0.5  # (the first moments of a play: the synth's start-up is slow at any load, never judged)
LEAST_STEP = 0.03  # (changes smaller than this wait: each change sends what was sent ahead again)


def queue_load(ready, ahead):
    """A synth that makes its sound ahead (Kestrel): its load read from how many of its `ahead` blocks are still
    ready, not from how long a block takes (user 2026-10-29: at 500k voices each block took most of its time, the
    sound was fine, yet it slowed down to 10 %; slower doesn't shorten a block much, voices ring as long). All
    ready = calm (0.3), half gone = HIGH's side, none = 1.5. One block is always being handed out and the sound card
    takes about one more at a time (read at any moment, not right after a block is made): ahead - 2 = all."""
    full = max(1.0, ahead - 2.0)
    empty = 1.0 - min(1.0, max(0.0, ready) / full)
    return 0.3 + 1.2 * empty


class SlowDown:
    def __init__(self):
        self.reset()

    def reset(self):
        """A new play: at the song's own speed, nothing learned."""
        self.speed = 1.0
        self.load = None
        self.began = time.perf_counter()
        self._down = self._rise = float("-inf")
        self._calm = None  # (since when the load stayed under LOW)

    def step(self, load, now=None, behind=False):
        """One reading: load (None = none this time); behind = the sound already broke up (Kestrel handed out
        silence). Returns the new speed when it should change, else None."""
        now = time.perf_counter() if now is None else now
        if now - self.began < WARM_S:
            return None
        if load is not None:
            self.load = load if self.load is None else self.load + SMOOTH * (load - self.load)
        if behind:
            self.load = max(self.load or 0.0, 1.2)
        if self.load is None:
            return None
        v = self.speed
        if self.load > HIGH:
            self._calm = None
            if now - self._down < DOWN_S or v <= SLOWEST:
                return None
            self._down = now
            new = max(SLOWEST, v * max(0.5, TARGET / self.load))
            self.load = TARGET  # (as it will be once the slower speed shows)
            return self._set(new, force=True)
        if self.load < LOW and v < 1.0:
            if self._calm is None:
                self._calm = now
            if now - self._calm >= CALM_S and now - self._rise >= RISE_S:
                self._rise = now
                return self._set(min(1.0, v * RISE))
            return None
        self._calm = None
        return None

    def _set(self, new, force=False):
        new = round(new, 3)
        if new != 1.0 and new != SLOWEST and abs(new - self.speed) < LEAST_STEP * self.speed and not force:
            return None
        if new == self.speed:
            return None
        self.speed = new
        return new
