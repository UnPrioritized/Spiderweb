"""Rendered ahead's player (PLAN.md R3): plays the blocks files/ahead_pieces.py made, joined, through the sound device.

How it fits in: the MIDI out "Rendered ahead" is a Sink (takes every message, sends nothing), so the usual player
(files/playback.Player) still runs the play line, page turns and edits while playing; AheadPlay makes and plays the
sound, and the window's play tick moves the play line to what is HEARD (Player.follow), so the line waits when the
sound waits.

At the grey (a block not made yet): Slow down to keep up ON = the sound waits (stutters) until it's made; OFF
(default) = skips: silence at the song's speed, the maker jumps past the line (it makes from where the sound is).
FIRST BATCH (user 2026-11-03: the first play stuttered and skipped notes; it must sound like a replay there): the
line waits at Play until START_S of sound is made from it (less near the song's end / with a shorter Render ahead),
silence before the first note too; Lower it by itself may lower only once the first piece is back and the start
has waited START_LOWER_S (a dense start). Stop fades (synth.FADE_MS).
Loudness: OUR limiter over the joined pieces (pieces are made without one: no level jumps at the joins),
App.ahead_opts limiter 0 Off / 1 Brickwall / 2 Balanced + ceiling_db.
Lower it by itself (R1c, Kestrel + BASSMIDI): sound made ahead under LOW_SHARE of Render ahead (at least
LOW_LEAST s) = the next pieces' voice limit x LOWER_STEP; above RISE_SHARE = back up a step every RISE_EVERY s."""

import math
import threading
import time

import numpy as np

from files import errors
from files.ahead_pieces import BLOCK, Maker, Song
from files.synth import RATE, Player, Synth, SynthError

FLOOR_VOICES = {"voices": 1024, "bass_voices": 50}  # lowering never goes under these
RISE_EVERY = 2.0  # seconds between steps back up
FOLLOW_S = 0.02  # the play line moved to the heard sound when it's further off than this
START_S = 10.0  # seconds made from the play line before playing starts (the first batch)
START_LOWER_S = 3.0  # a start waiting longer than this (first piece back): Lower it by itself may lower


def merlin_gap(ccs, fonts):
    """Why Merlin can't make this song yet (a key of "ra.kestrel_*"), or None: more than one soundfont ("fonts"),
    channel volume / expression lines ("cc", CC7 / CC11: Merlin doesn't apply them yet)."""
    if len(fonts) > 1:
        return "fonts"
    if any((m & 0xF0) == 0xB0 and (m >> 8) & 0x7F in (7, 11) for _, m in ccs or ()):
        return "cc"
    return None


class AheadLimiter:
    """Keeps the joined sound under the ceiling (looking one block ahead, nothing gets through). Brickwall comes back
    up quickly (FAST s), Balanced slowly (SLOW s: less pulling at low notes). Both HOLD the gain HOLD_S first. Off =
    the sound as it is (it may clip)."""
    BLOCK = 64
    DELAY = 2 * BLOCK
    FAST, SLOW, HOLD_S = 0.05, 0.3, 0.02

    def __init__(self, mode=2, ceiling_db=0.0):
        self.mode = mode
        self.top = 10 ** (ceiling_db / 20)
        self.inp = np.zeros((self.BLOCK, 2), np.float32)
        self.out = [np.zeros((self.BLOCK, 2), np.float32)]
        self.env, self.gain, self.held = self.top, 1.0, 0
        release = self.FAST if mode == 1 else self.SLOW
        self.fall = math.exp(-self.BLOCK / (release * RATE))
        self.hold = max(1, int(self.HOLD_S * RATE / self.BLOCK))

    def process(self, x):
        if self.mode == 0:  # (Off: as it is, the same delay so the play line's sums stay right)
            inp = self.inp = np.concatenate([self.inp, x])
            out, self.inp = inp[:len(x)], inp[len(x):]
            return out
        B = self.BLOCK
        inp = self.inp = np.concatenate([self.inp, x])
        k = len(inp) // B - 1
        if k > 0:
            peaks = np.abs(inp[:(k + 1) * B]).reshape(k + 1, -1).max(axis=1)
            ahead = np.maximum(peaks[:-1], peaks[1:])
            env, fall, top, held = self.env, self.fall, self.top, self.held
            gains = np.empty(k + 1)
            gains[0] = self.gain
            for i in range(k):
                a = float(ahead[i])
                if a >= env:
                    env, held = a, self.hold
                elif held:
                    held -= 1
                else:
                    env = max(a, env * fall)
                gains[i + 1] = top / max(env, top)
            self.env, self.gain, self.held = env, gains[-1], held
            t = (np.arange(1, B + 1) / B)[None, :]
            ramp = (gains[:-1, None] * (1 - t) + gains[1:, None] * t).reshape(-1, 1)
            self.out.append(inp[:k * B] * ramp.astype(np.float32))
            self.inp = inp[k * B:]
        out = np.concatenate(self.out)
        self.out = [out[len(x):]]
        return out[:len(x)]


class Sink:
    """The MIDI out "Rendered ahead" for the usual player: messages go nowhere (AheadPlay makes the sound)."""
    lead = 0

    def send(self, msg):
        pass

    def close(self):
        pass


class AheadPlay:
    """Owned by the App (app.ahead_play). The Maker is kept between plays (an unchanged song plays at once from
    what's made) and dropped when the engine / its settings / the soundfonts change."""

    def __init__(self, app):
        self.app = app
        self.maker = None
        self.key = None  # (engine, settings, soundfonts, card) the maker was made for
        self.audio = None  # synth.Player
        self.play_at = 0  # the song frame handed to the sound next
        self.started = False  # (the first batch is made: from then on Slow down OFF skips; set on the window's thread)
        self.since = 0.0  # (when Play was pressed)
        self.gate = threading.Event()
        self.load = None  # a thread opening BASSMIDI's soundfonts
        self.factor = 1.0  # (Lower it by itself: the voice limit's share in use)
        self._rose = 0.0
        self.skipped = 0  # frames skipped (silence) since Play
        self.slow = False  # (the Slow down tick, copied each tick for the sound's thread)

    # ------------------------------------------------------------ settings

    def _want_key(self, engine):
        from window.hz_preview import font_list
        app = self.app
        opts = {k: v for k, v in app.ahead_opts.items() if k not in ("lower", "limiter", "ceiling_db")}
        return engine, tuple(sorted(opts.items())), tuple(font_list(app.hz_preview)), app.kestrel_opts.get("card", "")

    def engine_for(self, ccs):
        """(the engine the song is made with, why Kestrel stands in for Merlin or None). R6: what Merlin can't do yet
        goes to Kestrel by itself: channel volume / expression lines (CC7 / CC11: Merlin doesn't apply them yet),
        more than one soundfont (refused)."""
        from window.hz_preview import font_list
        from files.render_ahead import engine_used
        engine = engine_used(self.app.ahead_engine.get())
        if engine != "merlin":
            return engine, None
        why = merlin_gap(ccs, font_list(self.app.hz_preview))
        return ("kestrel", why) if why else (engine, None)

    def _edo_first(self, engine):
        """31-EDO's first channel for this engine while the 31-EDO + Audio ticks are on, else None: BASSMIDI's after
        the Hz bass ports like live play (96), the graphics card's the top two ports (playback.EDO_KESTREL)."""
        from files.playback import EDO_FIRST, EDO_KESTREL
        if not self.app.out.edo:
            return None
        return EDO_FIRST if engine == "bassmidi" else EDO_KESTREL

    def _say_why(self, why):
        if why:
            from files.lang import tr
            self.app.status.config(text=tr("ra.kestrel_" + why))

    def _ensure_maker(self, engine):
        """The Maker for the current settings and this engine (a new one when they changed; the sound playing goes on
        with it). Returns an error text or None."""
        key = self._want_key(engine)
        if self.maker is not None and key == self.key:
            return None
        if self.maker is not None:
            self.maker.close()
        self.maker, self.key = None, None
        engine, _, fonts, card = key
        synth = None
        if engine == "bassmidi":
            try:
                if self.app.synth is None:
                    self.app.synth = Synth()
            except SynthError as e:
                return str(e)
            synth = self.app.synth
            if synth.needs_open(list(fonts)) and not (self.load and self.load.is_alive()):
                self.load = threading.Thread(target=self._open_fonts, args=(synth, list(fonts)), daemon=True)
                self.load.error = None
                self.load.start()
        self.maker = Maker(engine, self.app.ahead_opts, fonts, card, synth)
        self.key = key
        self.factor = 1.0
        errors.note("Rendered ahead", f"{engine}, ahead {self.app.ahead_seconds} s")
        return None

    def _open_fonts(self, synth, fonts):
        try:
            synth.set_fonts(fonts)
        except SynthError as e:
            self.load.error = str(e)

    def drop(self):
        """Everything made let go (settings changed, MIDI out changed, closing)."""
        self.stop()
        if self.maker is not None:
            self.maker.close()
        self.maker, self.key = None, None

    # ------------------------------------------------------------ playing

    def start(self, rendered, ccs, ports, ppq, bpm, tempo, start_beat):
        """Play from start_beat. Returns an error text, or None."""
        engine, why = self.engine_for(ccs)
        err = self._ensure_maker(engine)
        if err:
            return err
        self.maker.update_song(Song(rendered, ccs, ports, self.app.picture_use10, ppq, bpm, tempo,
                                    self._edo_first(engine)))
        song = self.maker.song
        self._say_why(why)
        try:
            if self.app.synth is None:
                self.app.synth = Synth()
        except SynthError as e:
            return str(e)
        if not self.app.synth.can_play:
            from files.lang import tr
            return tr("synth.no_device")
        self.stop()
        self.play_at = max(0, song.frame(int(round(start_beat * ppq))))
        self.started, self.skipped, self.since = False, 0, time.monotonic()
        self.gate = threading.Event()  # (the play line's Player.ready: set with the first batch)
        from files.render_ahead import take_opts
        o = take_opts(self.app.ahead_opts)
        try:
            self.audio = Player(self.app.synth, self.pull, 1.0, limiter=AheadLimiter(o["limiter"], o["ceiling_db"]))
        except SynthError as e:
            self.audio = None
            return str(e)
        self.audio.play(self.play_at)
        self.tick()
        return None

    def changed(self, rendered, ccs, ports, ppq, bpm, tempo):
        """The notes / tempo changed while playing: only the changed stretch (+ its ring) turns grey and is made
        again (R5); playing goes on."""
        if self.maker is None:
            return
        engine, why = self.engine_for(ccs)
        if engine != self.maker.engine:  # (a CC7 / CC11 line drawn or gone: the other engine, all made again)
            if self._ensure_maker(engine):
                return
            self._say_why(why)
        if self.maker.update_song(Song(rendered, ccs, ports, self.app.picture_use10, ppq, bpm, tempo,
                                       self._edo_first(engine))):
            self.app.roll.draw_ahead_grey()

    def playing(self):
        return self.audio is not None

    def pull(self, n):
        """(sound's thread) The next frames: made ones, None = wait, silence = skipped (Slow down off)."""
        m = self.maker
        if m is None or m.song is None:
            return np.zeros((0, 2), np.float32)
        if not self.started:  # (the first batch isn't made yet: the line waits, silence before the first note too)
            return None
        got = m.take(self.play_at, n)
        if got is None:
            if self.slow:  # (read on the window's thread: Tk can't be asked from here)
                return None
            k = min(n, BLOCK - self.play_at % BLOCK)  # (to the block's end: the next may be made)
            self.play_at += k
            self.skipped += k
            return np.zeros((k, 2), np.float32)
        self.play_at += len(got)
        return got

    def first_batch(self):
        """(window's thread) True once enough is made from the play line to start: START_S (or what Render ahead /
        free memory / the rest of the song allows), counted from the first note when Play is before it."""
        m = self.maker
        lo, hi = m.span
        if hi <= lo:
            return True
        at = max(self.play_at, lo)
        if at >= hi:
            return True
        need = min(START_S, self.app.ahead_seconds, m.room(), (hi - at) / RATE)
        return m.made_ahead(at) >= need - 1e-6

    def tick(self):
        """(window's thread, every play tick) Pieces looked after; the voice limit lowered / raised. Returns an
        error text when making failed (the caller stops and says it), else None."""
        m = self.maker
        if m is None:
            return None
        self.slow = self.app.slow_ahead.get()
        if self.load is not None:
            if self.load.is_alive():
                return None
            err, self.load = self.load.error, None
            if err:
                return err
        self._lower()
        m.step(self.play_at, self.app.ahead_seconds)
        if not self.started and m.song is not None and self.first_batch():
            self.started = True
            self.gate.set()
        return m.error

    def ended(self):
        """True once the sound's end was played (the play line then runs to its own end and stops)."""
        return self.audio is not None and not self.audio.active()

    def _lower(self):
        from files.render_ahead import LOW_LEAST, LOW_SHARE, LOWER_STEP, RISE_SHARE
        m = self.maker
        if m.engine == "merlin" or not self.app.ahead_opts.get("lower") or self.audio is None:
            if self.factor != 1.0:
                self._set_factor(1.0)
            return
        made = m.made_ahead(self.play_at)
        ahead = self.app.ahead_seconds
        now = time.monotonic()
        if not self.started and (not m.took or now - self.since < START_LOWER_S):
            return  # (the first batch: never lowered while the soundfonts load / before a dense start shows)
        if made < max(LOW_LEAST, LOW_SHARE * ahead):
            if now - self._rose > RISE_EVERY / 2:  # (one step per half RISE_EVERY at most)
                self._set_factor(self.factor * LOWER_STEP)
                self._rose = now
        elif made > RISE_SHARE * ahead and self.factor < 1.0 and now - self._rose > RISE_EVERY:
            self._set_factor(min(1.0, self.factor / LOWER_STEP))
            self._rose = now
        elif made > RISE_SHARE * ahead and self.factor == 1.0 and m.low and now - self._rose > RISE_EVERY:
            # (R5: calm at the full limit: blocks made lowered and still far enough ahead are made again at the
            # full limit; the ones nearer than the lowering mark stay, so this can't lower it again by itself)
            near = self.play_at + int((max(LOW_LEAST, LOW_SHARE * ahead) + BLOCK / RATE) * RATE)
            for i in sorted(m.low):
                if i * BLOCK >= near:
                    m.clear(i * BLOCK, (i + 1) * BLOCK)
            self._rose = now

    def _set_factor(self, f):
        m = self.maker
        key = "bass_voices" if m.engine == "bassmidi" else "voices"
        full = self.app.ahead_opts[key]
        f = max(f, min(1.0, FLOOR_VOICES[key] / max(1, full)))
        if f == self.factor:
            return
        self.factor = f
        m.opts[key] = max(1, int(full * f))
        m.lowered = f < 1.0

    def grey_beats(self, b0, b1):
        """(R4) [(from, to)] beats between b0 and b1 not made yet, while playing (stopped: none; nothing is made
        then, a whole song in grey would only be in the way)."""
        m = self.maker
        song = None if m is None else m.song
        if self.audio is None or song is None or not len(song) or b1 <= b0:
            return []
        ppq = song.ppq
        f0, f1 = song.frame(max(0, int(b0 * ppq))), song.frame(max(0, int(math.ceil(b1 * ppq))))
        return [(song.map.tick(a / RATE) / ppq, song.map.tick(b / RATE) / ppq) for a, b in m.grey(f0, f1)]

    def heard_sec(self):
        """Song seconds heard right now (None: silence while waiting for the first / next block)."""
        if self.audio is None:
            return None
        f = self.audio.position()
        return None if f is None else f / RATE

    def stop(self):
        if self.audio is not None:
            self.audio.stop()
            self.audio = None
        if self.factor != 1.0 and self.maker is not None:
            self._set_factor(1.0)
