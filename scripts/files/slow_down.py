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
RISE = 1.15  # ...by this much every RISE_S (~2 s from half speed back to 1x; few, bigger steps: each one sends
RISE_S = 0.4  # what was sent ahead again)
SETTLE_S = 1.0  # (after a cut, no other until it has shown: notes already ringing keep the load up a while; cut
# every 0.15 s it fell straight to 10 %, hunt 2026-10-29)
# The speed that broke up is remembered (user 2026-10-29: BASSMIDI sped up, overloaded, slowed, sped up... on a
# loop): climbed back to just UNDER it, the mark itself raised by CEIL_RISE after CEIL_S calm (tried a little faster
# now and then), forgotten once there's clearly room (the busy part is over)
UNDER = 0.95
CEIL_S = 4.0
CEIL_RISE = 1.1
NEAR = 0.85  # (breaking up again within this of the mark: the mark goes NEAR lower, the cut is a gentle NEAR too)
# Slowing that isn't helping (user 2026-10-29, after real Lagtester at 1 M voices crawled at 15 % and still
# stuttered: ringing voices don't get fewer by playing slower): the synth's work (effort: Kestrel's block time,
# BASSMIDI's CPU while that's what's maxed out) measured again at the next cut, still in trouble (within NEXT_S): it
# should have dropped by at least HELPED of the speed's share; if not, no slower from here, "Lower it by itself"
# takes over
HELPED = 0.5
FIT = 0.7  # (the synth's work known: faster only up to where it's expected to take this share of the time...)
FIT_CPU = 0.9  # (... Built-in BASSMIDI's CPU: fine to 95 %, user 2026-10-29 "fine means no stuttering")
FIT_S = 0.6  # (... once the last change has shown in it)
# (the work says "no room" but the sound has been fine this long: careful tries after all, each failed one waiting
# twice as long before the next, at most TRY_MOST_S; hunt 2026-10-29: after a wall at 500k-1 M voices Kestrel's
# blocks take most of their time even where 1x played fine, it stayed at 10-15 %)
FALLBACK_S = 3.0
TRY_MOST_S = 32.0
NEXT_S = SETTLE_S + 0.5
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
        self._down = self._rise = self._lift = float("-inf")
        self._calm = None  # (since when the load stayed under LOW)
        self.ceiling = 1.0  # (the speed that last broke up; 1 = none)
        self._lifts = 0  # (tries faster in a row that held)
        self.floor = SLOWEST  # (no slower than this: raised to the speed where slowing stopped helping)
        self.effort = None  # (the synth's work, smoothed: None = not known)
        self._ref = None  # (speed, effort) at the last cut, while still in trouble
        self._fails = 0  # (tries faster that broke up, since the last time back at 1x)

    def free_floor(self):
        """A "Lower it by itself" tick changed: slowing may go on down to 10 % again (hunt 2026-10-29: unticked
        after the hand-over, it stayed where it was and stuttered)."""
        self.floor, self._ref = SLOWEST, None

    def step(self, load, now=None, behind=False, easy=False, hold=False, effort=None, can_lower=True, fit=FIT):
        """One reading: load (None = none this time); behind = the sound already broke up (Kestrel handed out
        silence, BASSMIDI lost sound / left notes out); easy = clearly room to spare (the busy part is over: the
        speed that broke up is forgotten); hold = not faster yet ("Lower it by itself" still has notes / voices to
        give back first: undone in the reverse order); effort = how hard the synth works (None = not known: slowing
        counted as helping); can_lower = "Lower it by itself" is ticked (off: nothing to hand over to, so it goes on
        slowing to 10 % however little it helps, user 2026-10-29); fit = the share of the time the effort may take at
        the speed climbed to (FIT Kestrel's block time, FIT_CPU BASSMIDI's CPU), None = the effort doesn't say how
        much faster fits (a loaded MIDI's note budget: chords come in one go at any speed). Returns the new speed when
        it should change, else None."""
        now = time.perf_counter() if now is None else now
        if now - self.began < WARM_S:
            return None
        if load is not None:
            self.load = load if self.load is None else self.load + SMOOTH * (load - self.load)
        if effort is not None:
            self.effort = effort if self.effort is None else self.effort + SMOOTH * (effort - self.effort)
        if behind:
            self.load = max(self.load or 0.0, 1.2)
        if self.load is None:
            return None
        v = self.speed
        if self.load > HIGH:
            self._calm = None
            # (the work known: a cut shows in it at once, the next may come sooner, real 1 M voices 2026-10-29: the
            # wall's first stutter lasted the 3 s the cuts took)
            settle = SETTLE_S / 2 if self.effort is not None else SETTLE_S
            if now - self._down < settle or v <= self.floor:
                return None
            if can_lower and self._ref is not None and now - self._down <= NEXT_S and self.effort is not None:
                v0, e0 = self._ref
                if self.effort > e0 * (1 - HELPED * (1 - v / v0)):  # (slowing isn't helping: Lower it by itself's
                    self.floor = v  # turn, from this speed)
                    return None
            self._ref = (v, self.effort) if self.effort is not None else None
            self._down = now
            self._lifts = 0
            if self.ceiling < 1.0 and v >= self.ceiling * NEAR:  # (the mark was a little too fast: lower it, gently)
                self.ceiling = max(SLOWEST, min(v, self.ceiling) * NEAR)
                self._fails += 1  # (a try that broke up: the next one waits longer)
                new = max(self.floor, v * NEAR)
            else:
                self.ceiling = v
                new = max(self.floor, v * max(0.5, TARGET / self.load))
            self.load = TARGET  # (as it will be once the slower speed shows)
            return self._set(new, force=True)
        if self.load < LOW:
            if self._calm is None:
                self._calm = now
            self._ref = None  # (calm: the last cut helped)
            if fit and self.effort is not None and self.effort > 0.01:
                # (the synth's work known (Kestrel's block time): as fast as it says fits, in one go, never a try
                # that breaks up; the work taken to grow with the speed (it grew a little faster at 1 M voices), FIT
                # of the time at most. User 2026-10-29: its tries stuttered, climbing back after a wall was slow)
                top = min(1.0, v * fit / self.effort)
                if top > v * 1.03:
                    if not hold and now - self._calm >= CALM_S and now - self._rise >= FIT_S:
                        self._rise = now
                        self.ceiling = 1.0
                        return self._set(top, force=True)
                    return None
                if hold or now - self._calm < FALLBACK_S:
                    return None
                if self.ceiling >= 1.0:  # (no room by the work, yet the sound's fine: careful tries from here)
                    self.ceiling = v
            if easy:  # (the busy part is over: straight back, user 2026-10-29: climbing back was slow)
                self.ceiling = 1.0
                if v < 1.0 and not hold and now - self._calm >= CALM_S:
                    self._rise = now
                    return self._set(1.0)
                return None
            if self.ceiling < 1.0 and now - max(self._calm, self._lift) >= min(TRY_MOST_S, CEIL_S * 2 ** self._fails):
                # (tried faster: 10 %, then more each time it held, so it's back to 1x soon after the busy part; a
                # failure starts again at 10 %. Real BASSMIDI 2026-10-29: its note budget limits at 17 % CPU, nothing
                # says there's room but trying)
                self._lift = now
                self.ceiling = min(1.0, self.ceiling * CEIL_RISE ** (1 + self._lifts))
                self._lifts += 1
            top = 1.0 if self.ceiling >= 1.0 else max(SLOWEST, self.ceiling * UNDER)
            if v < top and not hold and now - self._calm >= CALM_S and now - self._rise >= RISE_S:
                self._rise = now
                return self._set(min(top, v * RISE), force=min(top, v * RISE) == top)
            return None
        self._calm = None
        return None

    @property
    def floored(self):
        """At the slowest it goes: 10 %, or where slowing stopped helping ("Lower it by itself" may work then)."""
        return self.speed <= self.floor

    def _set(self, new, force=False):
        new = round(new, 3)
        if new >= 1.0:  # (back to its own speed: slowing gets its chance again next time)
            self.floor, self._ref, self._fails = SLOWEST, None, 0
        if new != 1.0 and new != SLOWEST and abs(new - self.speed) < LEAST_STEP * self.speed and not force:
            return None
        if new == self.speed:
            return None
        self.speed = new
        return new
