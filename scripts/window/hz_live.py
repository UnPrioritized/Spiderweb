"""The Hz bass window's live keys (Preview on): press a key on the window's keyboard (the keys on the left) and hold
it to hear the Hz bass of that key, as a note placed there would sound; let go and the falls of the sustain points
play (hzbass "sustain"). Dragging onto another key plays that one instead (the one before stops there, no fall: a
note starting cuts it, as with placed notes).

The notes are the engine's own: a copy of the Hz bass holding just that one note (its keys, effects, gates), made
FIRST beats long and longer while it's held, and made again with its real length when it's let go (for the fall).
The sound is the quick sound (files/quicksound.py), NOT the synth: a quick copy made from the soundfont's own notes,
and not the MIDI (the user wanted a big warning: the Help tip "hz_live" the first time, and the words by the
Preview toggle while it plays).
Timing: the sound is mixed when the sound device asks for it (pull, BASS's own thread). A note starts LEAD after
the press (the sound already handed over plays first), a let-go's fall LEAD after the let-go. Until the notes and the
recordings they need are made, the sound waits (silence) rather than skip notes."""

import concurrent.futures
import copy
import math
import threading

import numpy as np

from files.lang import tr
from files.quicksound import QuickSound
from files.synth import RATE, Player, SynthError
from notes.custom import BOX_STROKE, box_frame, custom_settings
from notes.engine import shape_notes_tracks
from notes.hzbass import MIN_LEN, key_range, sound_span

LEAD = 0.05  # seconds from a press / let-go to its sound (time for its notes to be made)
BUFFER = 0.06  # seconds of sound the sound device keeps ready (short: a press is heard soon)
AHEAD = 1.0  # seconds of notes whose recordings are asked for ahead
FIRST = 8.0  # beats of a held note made at first (doubled when it's held near the end of them)
TICK_MS = 40
HZ_KEYS = ("tones", "fx", "loop", "off", "amount", "from", "fit", "sustain", "grow", "own")


def quick_sound(app):
    """The quick sound's recordings, kept for as long as Spiderweb is open (app.quick)."""
    cfg = app.hz_preview
    q = getattr(app, "quick", None)
    if q is None or q.synth is not app.synth or q.nofx != cfg["nofx"]:  # (No reverb or chorus changed: made anew)
        app.quick = QuickSound(app.synth, cfg["live_mb"], cfg["nofx"])
    return app.quick


class LiveKeys:
    def __init__(self, win):
        self.win, self.app = win, win.app
        self.lock = threading.Lock()
        self.jobs = concurrent.futures.ThreadPoolExecutor(1)  # (notes made one job after the other)
        self.gen = 0  # the latest press / let-go: a job for an older one is thrown away
        self.player = None
        self.key = None  # the key held
        self.p0 = 0  # the frame the note sounding starts at
        self.made = 0.0  # beats of the held note made
        self.extending = False
        self.at = 0  # frames handed to the sound device
        self.acc, self.acc0 = np.zeros((0, 2), np.float32), 0  # sound mixed ahead, from frame acc0
        self.acc_end = 0  # the frame the sound mixed so far ends at
        self.starts, self.recs, self.gains, self.i = np.zeros(0, np.int64), [], np.zeros(0, np.float32), 0
        self.cut = math.inf  # no notes of the list from this frame on (a new list for there is being made)
        self.waits = False  # pull waited for a recording last time
        self.bpm = 120.0
        self.gone = None  # the frame the note sounding was let go at (None: held)
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

    def press(self, key):
        """A key pressed on the window's keyboard (or the mouse dragged onto it): its note starts. Returns why it
        can't, or None."""
        why = self.ready()
        if why:
            return why
        if key == self.key:
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
                self.at, self.acc, self.acc0, self.acc_end = 0, np.zeros((0, 2), np.float32), 0, 0
                self.starts, self.recs, self.gains, self.i = np.zeros(0, np.int64), [], np.zeros(0, np.float32), 0
            self.player.play(0)
        if "hz_live" not in self.app.tips.seen:  # the warning: once, even with tips off (user: a big warning)
            self.app.tips.show("hz_live", parent=self.win, force=True)
        with self.lock:
            self.gen += 1
            self.key, self.p0, self.made, self.extending = key, self.at + int(LEAD * RATE), FIRST, False
            self.gone = None
            self.cut = self.p0  # (what sounds now stops where this one starts)
        self.ask(FIRST, self.gen, self.p0, ppq)
        if not self._tick:
            self._tick = self.win.after(TICK_MS, self.tick)
        self.win.draw_preview()
        return None

    def release(self):
        """The key let go: the note gets its real length, and its falls play."""
        if self.key is None:
            return
        try:
            ppq, _, _ = self.app.read_project()
        except ValueError:
            ppq = self.app.ppq
        with self.lock:
            self.gen += 1
            end = self.at + int(LEAD * RATE)
            self.cut, self.gone = end, end
            self.key, key = None, self.key
        self.ask(max(MIN_LEN, (end - self.p0) / RATE * self.bpm / 60.0), self.gen, self.p0, ppq, key, end)

    def stop(self):
        """Everything stops (the window closing, the preview going off)."""
        with self.lock:
            self.gen += 1
            self.key = None
        if self.player is not None:
            self.player.stop()
            self.player = None
        if self._tick:
            self.win.after_cancel(self._tick)
            self._tick = None

    def active(self):
        return self.player is not None

    def position(self):
        """For what's heard now: (beats since the note sounding started, beats after its start it was let go at, or
        None while held); None when no live note is heard."""
        if self.player is None:
            return None
        f = self.player.position()
        if f is None or f < self.p0:
            return None
        beats = self.bpm / 60.0 / RATE
        return (f - self.p0) * beats, None if self.gone is None else (self.gone - self.p0) * beats

    # ------------------------------------------------------------ the notes

    def held_shape(self, key, beats):
        """A copy of the Hz bass with just one note of key, `beats` long, from beat 0 (its own box, the shape's keys)."""
        win, app = self.win, self.app
        sh = win.target()
        tone = {"t": 0.0, "len": float(beats), "key": int(key), "cents": 0.0, "id": 1, "to": []}
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
        for k in ("strum", "claw", "chop", "glue", "range"):
            new.pop(k, None)
        new["hz"] = dict(hz, tones=[tone], **copy.deepcopy(win.fx_settings()))
        new["pts"] = box_frame(0.0, lo, max(MIN_LEN, sound_span(new["hz"])), hi)
        return new

    def ask(self, beats, gen, p0, ppq, key=None, cut=None):
        """The notes of the held note made `beats` long (in the background), put in from frame `cut` on (None:
        from where the list was cut / where the sound has got to)."""
        sh = self.held_shape(self.key if key is None else key, beats)
        qs = quick_sound(self.app)
        self.jobs.submit(self._make, sh, ppq, self.bpm, gen, p0, cut, qs)

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
            laid = qs.plan(starts, keys, vels, gates)  # (chords: the keys of a wave together)
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
            frm = self.cut if cut is None else cut
            if not math.isfinite(frm):  # (a longer held note: from the first note not mixed yet)
                frm = int(self.starts[self.i]) if self.i < len(self.starts) else self.at
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
            at, end = self.at, self.at + n
            end = min(end, max(at, self.cut)) if math.isfinite(self.cut) else end
            self.waits = False
            while self.i < len(self.starts) and self.starts[self.i] < end:
                rec = qs.get(self.recs[self.i])
                if rec is None:  # (not recorded yet: up to this note only, then wait)
                    end, self.waits = max(at, int(self.starts[self.i])), True
                    break
                self._add(int(self.starts[self.i]), rec, self.gains[self.i])
                self.i += 1
            if end <= at:
                return None
            a = at - self.acc0
            out = np.zeros((end - at, 2), np.float32)
            got = self.acc[a:a + (end - at)]
            out[:len(got)] = got
            self.at = end
            if self.at - self.acc0 > 2 * RATE:  # (what's been played goes)
                self.acc, self.acc0 = self.acc[self.at - self.acc0:].copy(), self.at
            return out

    def _add(self, start, rec, gain):
        a = start - self.acc0
        need = a + len(rec)
        if need > len(self.acc):
            grown = np.zeros((max(need, 2 * len(self.acc), RATE), 2), np.float32)
            grown[:len(self.acc)] = self.acc
            self.acc = grown
        self.acc[a:need] += np.multiply(rec, gain, dtype=np.float32)
        self.acc_end = max(self.acc_end, start + len(rec))

    def finished(self):
        """True once the last fall has died away (nothing held, nothing coming)."""
        with self.lock:
            return (self.key is None and self.cut == math.inf and self.i >= len(self.starts) and
                    self.at >= self.acc_end)

    def tick(self):
        """Every TICK_MS while a note sounds: recordings asked for ahead, a held note made longer, the words by the
        Preview toggle; the player stopped once all has died away."""
        self._tick = None
        if not self.win.winfo_exists() or self.player is None:
            return
        with self.lock:
            held = self.key is not None
            near = held and not self.extending and (self.at - self.p0) / RATE * self.bpm / 60.0 > self.made / 2
            if near:
                self.extending, self.made = True, self.made * 2
        if near:
            try:
                ppq = self.app.read_project()[0]
            except ValueError:
                ppq = self.app.ppq
            self.ask(self.made, self.gen, self.p0, ppq)
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
            return tr("hz.live_recording"), "#c06000"
        return tr("hz.live_playing", mb=f"{qs.used_mb():,.0f}"), "#555"
