"""The Hz bass live keys (Preview on): press a key on the synth window's keyboard (hz_synth.py; user: not the Hz bass
window's keys on the left) and hold it to hear the Hz bass of that key, as a note placed there would sound; let go and the falls of the sustain points
play (hzbass "sustain"). Several keys can be held together, as on a synth (user 2026-10-09: a chord for the
Arpeggio); keys pressed within CHORD of each other start together (a chord, as notes placed together).

The notes are the engine's own: a copy of the Hz bass holding the notes played since the sound was last silent (the
"take": each from its press, as long as it was held; held ones made up to FIRST beats on and longer while held), made
again at every press and let-go (its falls), from there on (what was heard before stays).
The sound changed while a key is held (a knob turned): made again from the notes not heard yet (remake).
The sound is the quick sound (files/quicksound.py), NOT the synth: a quick copy made from the soundfont's own notes,
and not the MIDI (the user wanted a big warning: the Help tip "hz_live" the first time, and the words by the
Preview toggle while it plays).
Timing: the sound is mixed when the sound device asks for it (pull, BASS's own thread). A note starts LEAD after
the press (the sound already handed over plays first), a let-go's fall LEAD after the let-go. Until the notes and the
recordings they need are made, the sound waits (silence) rather than skip notes. Effects that make every key
different: a rough copy (keys in bands, quicksound.plan_rough), as coarse as the press's roughest so far."""

import concurrent.futures
import copy
import json
import math
import threading

import numpy as np

from files.lang import tr
from files.quicksound import Mixer, QuickSound
from files.synth import RATE, Player, SynthError
from notes.custom import BOX_STROKE, box_frame, custom_settings
from notes.engine import shape_notes_tracks
from notes.hzbass import EXTRAS, MIN_LEN, key_range, sound_span
from window import look

LEAD = 0.05  # seconds from a press / let-go to its sound (time for its notes to be made)
CHORD = 0.05  # seconds: keys pressed this close after the one before (still held) start with it, as a chord
BUFFER = 0.06  # seconds of sound the sound device keeps ready (short: a press is heard soon)
AHEAD = 1.0  # seconds of notes whose recordings are asked for ahead
FIRST = 8.0  # beats of a held note made at first (doubled when it's held near the end of them)
TICK_MS = 40
FREE_MS = 60000  # the recordings are freed this long after the synth window closes (user)
HZ_KEYS = ("tones", "fx", "loop", "off", "amount", "from", "fit", "sustain", "lfo", "grow", "own") + EXTRAS


def quick_sound(app):
    """The quick sound's recordings (app.quick), kept while the synth window is open and FREE_MS after it closes
    (keep_sound / free_sound_later)."""
    cfg = app.hz_preview
    q = getattr(app, "quick", None)
    if q is None or q.synth is not app.synth or q.nofx != cfg["nofx"]:  # (No reverb or chorus changed: made anew)
        app.quick = QuickSound(app.synth, cfg["live_mb"], cfg["nofx"])
    return app.quick


def keep_sound(app):
    """The synth window opened: its recordings stay (a wait to free them called off)."""
    job = getattr(app, "quick_free", None)
    if job:
        app.after_cancel(job)
    app.quick_free = None


def free_sound_later(app):
    """The synth window closed: its recordings are freed FREE_MS later (user: reopened before that, they're kept)."""
    keep_sound(app)
    app.quick_free = app.after(FREE_MS, lambda: free_sound(app))


def free_sound(app):
    """The live keys' recordings thrown away (the memory goes back to Windows; the soundfont's loudness curve stays),
    unless a live note still sounds."""
    app.quick_free = None
    hz = app.hz_window
    if hz is not None and hz.live.active():  # (its fall still playing: a bit later)
        app.quick_free = app.after(1000, lambda: free_sound(app))
        return
    q = getattr(app, "quick", None)
    if q is not None:
        q.forget()


def kept_tones(tones, now, tail):
    """The take's notes still needed at beat `now` (tail = beats a note sounds on after its end): those held or
    still sounding, the ones joined to them back in time (touching or overlapping: an Arpeggio run, a Legato chain,
    hunt 2026-10-09), and the last one let go before them (a Glide from it)."""
    keep = [n["len"] is None or n["t"] + n["len"] >= now - tail - 1.0 for n in tones]
    lo = min((n["t"] for n, k in zip(tones, keep) if k), default=math.inf)
    changed = True
    while changed:
        changed = False
        for i, n in enumerate(tones):
            if not keep[i] and n["t"] + n["len"] >= lo - 1e-9:
                keep[i], lo, changed = True, min(lo, n["t"]), True
    ended = [n for n, k in zip(tones, keep) if not k]
    last = max(ended, key=lambda n: n["t"] + n["len"], default=None)
    return [n for n, k in zip(tones, keep) if k or n is last]


class LiveKeys:
    def __init__(self, win):
        self.win, self.app = win, win.app
        self.lock = threading.Lock()
        self.jobs = concurrent.futures.ThreadPoolExecutor(1)  # (notes made one job after the other)
        self.gen = 0  # the latest press / let-go: a job for an older one is thrown away
        self.player = None
        self.key = None  # the key held last (the newest held one)
        self.tones = []  # the take: {"t": beats from p0, "len": beats (None while held), "key", "id"}
        self.p0 = 0  # the frame the take starts at
        self.last = None  # the newest note, and the frame it starts at (the moving dots follow it)
        self.p_last = 0
        self.made = 0.0  # beats from p0 the held notes are made up to
        self.extending = False
        self.at = 0  # frames handed to the sound device
        self.mix = Mixer()  # the chords laid so far, added up
        self.starts, self.recs, self.gains, self.i = np.zeros(0, np.int64), [], np.zeros(0, np.float32), 0
        self.cut = math.inf  # no notes of the list from this frame on (a new list for there is being made)
        self.waits = False  # pull waited for a recording last time
        self.bpm = 120.0
        self.gone = None  # the frame the newest note was let go at (None: held)
        self.band = 1  # keys a band in the rough copy of the note sounding (1 = exact)
        self.bands = {}  # the Hz bass's settings -> the band its last note needed (the next press starts there)
        self.asked_fx = None  # the effects' settings the notes asked for last were made with (JSON)
        self.job = None  # the notes being made (a future)
        self._tick = None

    # ------------------------------------------------------------ pressing and letting go

    def ready(self):
        """None when the live keys can play, else why not (a text for the status line)."""
        p = self.win.preview
        if not self.win.preview_on.get():
            return tr("hz.live_needs_preview")
        if p.loading() or self.app.synth is None or not self.app.synth.font_path:
            return tr("hz.preview_loading")
        if not self.app.synth.can_play:
            return tr("synth.no_device")
        return None

    def warm(self):
        """The preview's soundfont is open: its loudness curve measured now (about a second, in the background), so
        the first press doesn't wait for it."""
        if self.app.synth is not None and self.app.synth.font_path:
            quick_sound(self.app).prepare()

    def held_keys(self):
        """The keys held now."""
        return [n["key"] for n in self.tones if n["len"] is None]

    def beats(self, frame):
        """Beats from the take's start at a frame."""
        return (frame - self.p0) / RATE * self.bpm / 60.0

    def press(self, key, parent=None):
        """A key pressed on the synth window's keyboard (or the mouse dragged onto it): its note starts, the keys
        held already sounding on (parent: the window the warning tip shows over). Returns why it can't, or None."""
        why = self.ready()
        if why:
            return why
        if key in self.held_keys():
            return None
        try:
            ppq, self.bpm, _ = self.app.read_project()
        except ValueError as e:
            return str(e)
        qs = quick_sound(self.app)
        qs.prepare()
        if self.player is None:
            try:
                self.player = Player(self.app.synth, self.pull, self.app.hz_preview["volume"], BUFFER)
            except SynthError as e:
                return str(e)
            with self.lock:
                self.at, self.mix = 0, Mixer()
                self.starts, self.recs, self.gains, self.i = np.zeros(0, np.int64), [], np.zeros(0, np.float32), 0
            self.player.play(0)
        if "hz_live" not in self.app.tips.seen:  # the warning: once, even with tips off (user: a big warning)
            self.app.tips.show("hz_live", parent=parent or self.win, force=True)
        with self.lock:
            self.gen += 1
            now = self.at + int(LEAD * RATE)
            if not self.tones or self.finished(locked=True):  # (all silent: a new take, from here)
                self.p0, self.tones, self.made, self.band = now, [], 0.0, 1
            last = self.last if self.tones else None
            if last is not None and last["len"] is None and now - self.p_last < CHORD * RATE:
                start = self.p_last  # (pressed with the one before: a chord, starting together)
            else:
                start = now
            tone = {"t": self.beats(start), "len": None, "key": int(key),
                    "id": max((n["id"] for n in self.tones), default=0) + 1}
            self.tones.append(tone)
            self.made, self.extending = max(self.made, tone["t"] + FIRST), False
            self.key, self.last, self.p_last, self.gone = key, tone, start, None
            self.cut = min(self.cut, start)  # (what sounds from here is made again, with this one)
            gen = self.gen
        self.ask(gen, ppq)
        if not self._tick:
            self._tick = self.win.after(TICK_MS, self.tick)
        self.win.draw_preview()
        return None

    def release(self, key=None):
        """A key let go (None: every key held): its note gets its real length, and its falls play."""
        try:
            ppq, _, _ = self.app.read_project()
        except ValueError:
            ppq = self.app.ppq
        with self.lock:
            gone = [n for n in self.tones if n["len"] is None and (key is None or n["key"] == key)]
            if not gone:
                return
            self.gen += 1
            end = self.at + int(LEAD * RATE)
            for n in gone:
                n["len"] = max(MIN_LEN, self.beats(end) - n["t"])
                if n is self.last:
                    self.gone = end
            held = self.held_keys()
            self.key = held[-1] if held else None
            self.cut = min(self.cut, end)
            gen = self.gen
        self.ask(gen, ppq)

    def stop(self):
        """Everything stops (the window closing, the preview going off)."""
        with self.lock:
            self.gen += 1
            self.key, self.tones = None, []
        if self.player is not None:
            self.player.stop()
            self.player = None
        if self._tick:
            self.win.after_cancel(self._tick)
            self._tick = None

    def active(self):
        return self.player is not None

    def position(self):
        """For what's heard now: (beats since the newest note started, beats after its start it was let go at, or
        None while held); None when no live note is heard."""
        if self.player is None:
            return None
        f = self.player.position()
        if f is None or f < self.p_last:
            return None
        beats = self.bpm / 60.0 / RATE
        return (f - self.p_last) * beats, None if self.gone is None else (self.gone - self.p_last) * beats

    # ------------------------------------------------------------ the notes

    def held_shape(self, key, beats):
        """A copy of the Hz bass with just one note of key, `beats` long, from beat 0 (its own box, the shape's keys)."""
        return self.take_shape([{"t": 0.0, "len": float(beats), "key": int(key), "id": 1}])

    def take_shape(self, tones):
        """A copy of the Hz bass holding these notes ({"t", "len", "key", "id"}; beats from 0)."""
        win, app = self.win, self.app
        sh = win.target()
        tones = [{"t": float(n["t"]), "len": float(n["len"]), "key": int(n["key"]), "cents": 0.0, "id": n["id"],
                  "to": []} for n in tones]
        if sh is not None and sh.get("hz"):
            new = copy.deepcopy(sh)
            hz = {k: v for k, v in sh["hz"].items() if k not in HZ_KEYS}
            lo, hi = key_range(sh)
        else:
            new = dict(app.defaults, kind="custom", name=tr("hz.name"), strokes=[copy.deepcopy(BOX_STROKE)],
                       **custom_settings(app.custom_defaults))
            new["fill"] = "spam"
            hz = win.new_hz(self.bpm)
            lo, hi = app.hz_defaults["lo"], app.hz_defaults["hi"]
        for k in ("fx", "glue", "range"):
            new.pop(k, None)
        new["hz"] = dict(hz, tones=tones, **copy.deepcopy(win.fx_settings()))
        new["pts"] = box_frame(0.0, lo, max(MIN_LEN, sound_span(new["hz"])), hi)
        return new

    def ask(self, gen, ppq):
        """The take's notes made (in the background; held ones up to self.made beats), put in from where the list
        was cut (or, a held note made longer, from the first note not heard yet). Notes long gone are left out
        (their sound over: no slower making as the take goes on); the last one let go stays (a Glide from it)."""
        self.asked_fx = self.fx_now()
        with self.lock:
            p0 = self.p0
            tones = [dict(n, len=n["len"] if n["len"] is not None else max(MIN_LEN, self.made - n["t"]))
                     for n in self.tones]
            now = self.beats(self.at)
        tail = sound_span(self.held_shape(60, MIN_LEN)["hz"])  # (how long one note sounds on after it ends)
        sh = self.take_shape(kept_tones(tones, now, tail))
        qs = quick_sound(self.app)
        self.job = self.jobs.submit(self._make, sh, ppq, self.bpm, gen, p0, None, qs)

    def fx_now(self):
        return json.dumps(self.win.fx_settings(), sort_keys=True)

    def remake(self):
        """(While a key is held) the sound changed (a knob turned, a line moved): the held note made again with it,
        from the first of its notes not heard yet (the ones heard ring on), so the change is heard at once (user).
        One at a time: a change while one is being made waits for it."""
        if self.job is not None and not self.job.done():
            return
        with self.lock:
            if not self.held_keys():
                return
            self.gen += 1  # (one being made longer with the old sound: thrown away, this one is as long)
            gen = self.gen
        try:
            ppq = self.app.read_project()[0]
        except ValueError:
            ppq = self.app.ppq
        self.ask(gen, ppq)

    def _make(self, sh, ppq, bpm, gen, p0, cut, qs):
        try:
            notes, _ = shape_notes_tracks(sh, ppq, 128)
            notes = np.asarray(notes)[:, :4] if len(notes) else np.zeros((0, 4))
            notes = notes[notes[:, 2] <= 127]
            to_frames = RATE * 60.0 / (ppq * bpm)
            starts = p0 + np.rint(notes[:, 0] * to_frames).astype(np.int64)
            gates = np.maximum(1, np.rint((notes[:, 1] - notes[:, 0]) * to_frames)).astype(np.int64)
            order = np.argsort(starts, kind="stable")
            starts, gates, keys, vels = starts[order], gates[order], notes[order, 2], notes[order, 3].astype(np.int64)
            if qs.measuring is not None:
                qs.measuring.join()
            if not qs.ready():
                return
            sig = json.dumps([{k: v for k, v in sh["hz"].items() if k != "tones"}, key_range(sh)], sort_keys=True)
            laid, band = qs.plan_rough(starts, keys, vels, gates, max(self.band, self.bands.get(sig, 1)))
            # (chords: the keys of a wave together)
            starts = np.array([s for s, _, _ in laid], np.int64)
            recs = [c for _, c, _ in laid]
            gains = np.array([g for _, _, g in laid], np.float32)
        except Exception:
            from files.errors import write_log
            import sys
            write_log(*sys.exc_info(), "live keys")
            return
        with self.lock:
            if gen != self.gen:
                return
            self.band = max(self.band, band)  # (made longer or let go: never finer than before)
            if len(self.bands) > 50:
                self.bands.clear()
            self.bands[sig] = band
            frm = self.cut if cut is None else cut
            if not math.isfinite(frm):  # (a longer held note: from the first note not mixed yet)
                frm = int(self.starts[self.i]) if self.i < len(self.starts) else self.at
            frm = max(frm, self.at)  # (a chord's later key: what's heard already stays as it was)
            keep = int(np.searchsorted(self.starts, frm))  # (the list before frm stays, then the new notes)
            j = int(np.searchsorted(starts, frm))
            self.starts = np.concatenate([self.starts[:keep], starts[j:]])
            self.recs = self.recs[:keep] + recs[j:]
            self.gains = np.concatenate([self.gains[:keep], gains[j:]])
            self.i = min(self.i, keep)
            self.cut = math.inf
            self.extending = False
        self.want()

    def want(self):
        """The recordings the next AHEAD of notes need, asked for."""
        with self.lock:
            j = int(np.searchsorted(self.starts, self.at + int(AHEAD * RATE)))
            recs = self.recs[self.i:j]
        if recs:
            quick_sound(self.app).want(recs)

    # ------------------------------------------------------------ the sound

    def pull(self, n):
        """(BASS's thread) n frames of sound, None = not ready (silence, waiting), empty = the end."""
        qs = self.app.quick
        with self.lock:
            # (the notes being made again (self.cut): the old ones play on meanwhile, the new ones take over from
            # where the sound has got to once they're there (a long take held: no silence, hunt 2026-10-09); with
            # no old ones past the cut (a first press) the sound waits for them, so none is missed)
            at, end = self.at, self.at + n
            if math.isfinite(self.cut) and not (len(self.starts) and self.starts[-1] >= self.cut):
                end = min(end, max(at, self.cut))
            self.waits = False
            got, j = {}, self.i
            while j < len(self.starts) and self.starts[j] < end:
                chord = self.recs[j]
                if chord not in got:
                    got[chord] = qs.get(chord)
                if got[chord] is None:  # (not recorded yet: up to this note only, then wait)
                    end, self.waits = max(at, int(self.starts[j])), True
                    break
                j += 1
            self.mix.lay(self.starts[self.i:j], self.recs[self.i:j], self.gains[self.i:j], got)
            self.i = j
            if end <= at:
                return None
            self.at = end
            return self.mix.take(at, end)

    def finished(self, locked=False):
        """True once the last fall has died away (nothing held, nothing coming). locked: the lock is held already."""
        if not locked:
            with self.lock:
                return self.finished(True)
        return (not self.held_keys() and self.cut == math.inf and self.i >= len(self.starts) and
                self.at >= self.mix.end)

    def tick(self):
        """Every TICK_MS while a note sounds: recordings asked for ahead, a held note made longer, the words by the
        Preview toggle; the player stopped once all has died away."""
        self._tick = None
        if not self.win.winfo_exists() or self.player is None:
            return
        with self.lock:
            first = min((n["t"] for n in self.tones if n["len"] is None), default=None)  # (the longest held)
            held = first is not None
            near = held and not self.extending and self.beats(self.at) - first > (self.made - first) / 2
            if near:  # (half of what's made heard: twice as long, from the longest held note's start)
                self.extending, self.made = True, first + 2 * (self.made - first)
            gen = self.gen
        if near:
            try:
                ppq = self.app.read_project()[0]
            except ValueError:
                ppq = self.app.ppq
            self.ask(gen, ppq)
        elif held and self.fx_now() != self.asked_fx:
            self.remake()
        self.want()
        if not held and self.finished():
            self.player.stop()
            self.player = None
            self.win.draw_preview()
            return
        self.win.draw_preview()
        self._tick = self.win.after(TICK_MS, self.tick)

    def says(self):
        """(text, colour) for the words by the Preview toggle while it plays."""
        qs = self.app.quick
        if qs is None or not qs.ready() or self.waits:
            return tr("hz.live_recording"), look.WARN
        if self.band > 1:
            return tr("hz.live_rough", n=self.band, mb=f"{qs.used_mb():,.0f}"), look.INFO
        return tr("hz.live_playing", mb=f"{qs.used_mb():,.0f}"), look.INFO
