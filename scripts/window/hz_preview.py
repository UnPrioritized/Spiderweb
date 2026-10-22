"""The Hz bass preview (the Preview toggle in the Hz bass window): the Hz bass shown there, alone, played through the
built-in synth (files/synth.py) with the user's soundfont and voice limit, on program 0.

The sound is made ahead of time in CHUNK-second pieces, several at once (threads, one core each), over the AHEAD
seconds from the play line while playing, or from the left edge of the view when stopped: a window that moves,
not the whole song. What isn't made yet is greyed over the notes and clears as it's made. A change to the notes
greys (and makes again) only the stretch that changed. Playing waits at the grey; it never skips notes.

The notes are looked at every TICK_MS (the shape's notes are remembered by the main window, so an unchanged shape
costs nothing), so any change counts: this window, the main piano roll, undo, BPM / PPQ. The sound is made SETTLE
after the last change (at once while playing). Another shape picked: the play line goes back to its first note.
Settings: app.hz_preview (kept with the window settings in the autosave; the soundfont is a path on this PC)."""

import math
import os
import threading
import time

import numpy as np

from files import errors
from files.lang import tr
from files.synth import RATE, STREAM_PORTS, Player, Synth, SynthError, events
from files.synth import cc_events as synth_cc
from notes.hz_cc import cc_events, cc_key, voice_events
from notes.engine import CHANNELS, CLASH
from notes.hzbass import all_tones, left_edge, own_channels
from notes.tempo_file import heard_tempo, seconds_at, ticks_at

CHUNK = 2.0  # seconds of sound made in one piece
AHEAD = 60.0  # seconds made ahead of the play line (or the view's left edge)
BEHIND = 10.0  # seconds kept behind it (further back is thrown away, and greyed again)
TAIL = 30.0  # seconds of ring after the last note at most (it ends where the soundfont's sound has died away)
TICK_MS = 100
SETTLE = 0.5  # seconds after the last change before the sound is made (a slider dragged changes it many times a
# second: sound made for each step was thrown away at once, user). Playing (Space) makes it at once.
LOADED_SHOWN = 3.0  # seconds the green "soundfont loaded" stays
WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))  # pieces made at once
DEFAULTS = {"on": False, "font": "", "voices": 1000, "nofx": False, "volume": 0.8, "live_mb": 1000}
VOICES = (1, 100000)  # the voice limit's range
LIVE_MB = (100, 65536)  # the live keys' memory limit's range (MB)


def clean_settings(d):
    """app.hz_preview from the autosave's window settings (anything odd = the default)."""
    d = d if isinstance(d, dict) else {}
    got = dict(DEFAULTS)
    if isinstance(d.get("font"), str):
        got["font"] = d["font"]
    if isinstance(d.get("voices"), int) and VOICES[0] <= d["voices"] <= VOICES[1]:
        got["voices"] = d["voices"]
    if isinstance(d.get("live_mb"), int) and LIVE_MB[0] <= d["live_mb"] <= LIVE_MB[1]:
        got["live_mb"] = d["live_mb"]
    if isinstance(d.get("volume"), (int, float)) and 0 <= d["volume"] <= 1:
        got["volume"] = float(d["volume"])
    got["on"], got["nofx"] = d.get("on") is True, d.get("nofx") is True
    ring = d.get("ring")  # (Auto's CC run-on measured for the soundfont "ring_font": App.ring_check)
    if (isinstance(ring, (int, float)) and not isinstance(ring, bool) and math.isfinite(ring) and ring >= 0
            and isinstance(d.get("ring_font"), str)):
        got["ring"], got["ring_font"] = float(ring), d["ring_font"]
    return got


class _Job:
    """One piece being made in a thread."""

    def __init__(self, i, ver):
        self.i, self.ver = i, ver
        self.cancel = threading.Event()
        self.done = 0  # frames made so far
        self.result = self.error = None
        self.finished = False
        self.took, self.voices = 0.0, 0
        self.end = None  # the frame the ring after the last note died away at, if in this piece


class Preview:
    def __init__(self, win):
        self.win, self.app = win, win.app
        self.synth = None
        self.notes = None  # the notes the sound is made of (the main window's remembered array: same = unchanged)
        self.shape = None  # (id of the shape, its left edge): where the window's beats start
        self.ev, self.ppq, self.bpm, self.tempo = None, 0, 0.0, None
        self.span = (0, 0)  # frames with sound in them: first note .. where the ring after the last note ends
        self.last = 0  # the frame the last note ends at
        self.ends = {}  # piece -> the frame the ring died away at in it
        self.chunks, self.jobs, self.ver = {}, {}, {}  # made pieces, pieces being made, each piece's version
        self.threads = []  # the threads making pieces, thrown-away ones too (at most WORKERS running)
        self.took = []  # seconds the last few pieces with notes in them took (for the speed)
        self.voices_used = 0  # the most voices at once in the pieces made since the settings last changed
        self.player, self.play_at = None, 0  # the player, the song frame it asks for next
        self.line = None  # the play line (song beat); None = at the first note
        self.which = None  # the shown shape's place in app.shapes (another one picked: the line back to its start)
        self.shown = None  # what the grey looks like now (only drawn again when it changes)
        self._tick = None
        self.changed = 0.0  # when the notes / settings last changed (the sound waits SETTLE after it)
        self.load = None  # the thread opening the soundfont (it happens in the background)
        self.loaded_at = 0.0  # when it finished (the green "loaded" shows for a few seconds)

    @property
    def cfg(self):
        return self.app.hz_preview

    # ------------------------------------------------------------ on / off

    def start(self):
        """Preview on (a soundfont is set; a new one is opened in the background, see loading). Returns an error
        text, or None."""
        try:
            if self.app.synth is None:
                self.app.synth = Synth()
        except SynthError as e:
            return str(e)
        self.synth = self.app.synth
        errors.note("Hz preview", f"on, voice limit {self.cfg['voices']}, no FX "
                                  f"{'on' if self.cfg['nofx'] else 'off'}, {WORKERS} pieces at once")
        self.clear()
        if self.synth.needs_open(self.cfg["font"]) and not (self.load and self.load.is_alive()):
            self._load_font()  # (one still opening: tick opens the one picked once it's done)
        if not self._tick:
            self._tick = self.win.after(0, self.tick)
        return None

    def _load_font(self):
        """The soundfont picked opened in the background (see loading)."""
        font = self.cfg["font"]
        self.load = threading.Thread(target=self._open_font, args=(font,), daemon=True)
        self.load.error, self.load.path = None, font
        self.load.start()

    def _open_font(self, path):
        try:
            self.synth.set_font(path)
            self.synth.warm_up()
        except SynthError as e:
            self.load.error = str(e)

    def loading(self):
        """True until tick has seen the soundfont open (then just_loaded)."""
        return self.load is not None

    def just_loaded(self):
        return time.perf_counter() - self.loaded_at < LOADED_SHOWN

    def stop(self):
        """Preview off: nothing plays, nothing is made, no grey."""
        if self.synth is not None:
            errors.note("Hz preview", "off")
        self.stop_play()
        if self._tick:
            self.win.after_cancel(self._tick)
            self._tick = None
        self.clear()
        self.notes, self.shown = None, None
        self.win.draw_preview()

    def remake(self):
        """The settings changed (soundfont, voice limit, no FX): everything is made again (greyed until then; playing
        goes on, waiting at the grey). A new soundfont is opened first. Returns an error text, or None."""
        self.took, self.voices_used = [], 0
        return self.start()

    def clear(self, lo=None, hi=None):
        """Throws away the pieces between frames lo and hi (all without them) and stops making them. (ends too: a
        piece thrown away for being far from the view keeps its end, and a stale end would cut the sound short.)"""
        self.changed = time.perf_counter()
        for i in list(self.ver.keys() | self.chunks.keys() | self.jobs.keys() | self.ends.keys()):
            if lo is None or (i + 1) * CHUNK * RATE > lo and i * CHUNK * RATE < hi:
                self.ver[i] = self.ver.get(i, 0) + 1
                self.chunks.pop(i, None)
                self.ends.pop(i, None)
                job = self.jobs.pop(i, None)
                if job:
                    job.cancel.set()

    # ------------------------------------------------------------ the notes

    def frames(self, ticks):
        if self.tempo:  # (the Conductor pane's tempo as heard, user 2026-10-23)
            return seconds_at(self.tempo, self.ppq, ticks) * RATE
        return ticks / self.ppq * 60.0 / self.bpm * RATE

    def beat_of(self, frame):
        """Song frame -> the window's beat (counted from the shape's left edge)."""
        left = self.shape[1] if self.shape else 0.0
        if self.tempo:
            return ticks_at(self.tempo, self.ppq, frame / RATE) / self.ppq - left
        return frame / RATE * self.bpm / 60.0 - left

    def frame_of(self, beat):
        song = (self.shape[1] if self.shape else 0.0) + beat
        if self.tempo:
            return seconds_at(self.tempo, self.ppq, song * self.ppq) * RATE
        return song * 60.0 / self.bpm * RATE

    def look(self):
        """Takes in changed notes: only the stretch that changed is made again. Not while the main window leaves
        the notes for later (a slow drag going on): the sound would be thrown away at the next step."""
        if self.app.notes_late:
            return
        sh = self.win.target()
        which = next((i for i, s in enumerate(self.app.shapes) if s is sh), None)
        if which != self.which:  # (another shape: its preview starts at its first note; the old song place could
            # be far before it, user. By place, not id: undo makes new copies of the same shape)
            self.which, self.line = which, None
            if self.playing():
                self.stop_play()
                self.win.after_idle(self.replay)
        try:
            ppq, bpm, _ = self.app.read_project()
        except ValueError:
            return
        notes, tracks = (self.app.notes_tracks(sh) if sh is not None and all_tones(sh.get("hz") or {})  # (any layer's)
                         else (None, None))
        multi = self.app.channel_mode.get() == "auto"
        shape = (id(sh), left_edge(sh), multi, cc_key(sh.get("hz"))) if sh is not None else None
        end = int(notes[:, 1].max()) + 1 if notes is not None and len(notes) else 0
        tempo = heard_tempo(self.app.tempo, bpm, ppq, end)  # (None: one tempo, the BPM box's)
        if notes is self.notes and (ppq, bpm, tempo) == (self.ppq, self.bpm, self.tempo) and shape == self.shape:
            return
        old, same_time = self.ev, (ppq, bpm, tempo) == (self.ppq, self.bpm, self.tempo)
        self.notes, self.ppq, self.bpm, self.shape, self.tempo = notes, ppq, bpm, shape, tempo
        voiced = multi and sh is not None and own_channels(sh.get("hz")) and tracks is not None
        if voiced:  # (own channel per note: each voice on its own channel, port after port, with its own lines)
            place = np.array([p * 16 + c for p in range(STREAM_PORTS) for c in CHANNELS], np.int64)
            chans = place[np.asarray(tracks, np.int64) % len(place)]
            cc = synth_cc([(t, s, a, b, int(place[v % len(place)])) for v, got in voice_events(sh, ppq, bpm).items()
                           for t, s, a, b in got])
        else:
            # (a layer's notes clashing with another's: on another channel with Multi channel, as in the MIDI)
            chans = np.asarray(tracks) // CLASH if multi and tracks is not None and len(tracks) else None
            # (its CC lines on every channel its notes are on, as in the MIDI)
            used = (sorted({c + (c >= 9) for c in np.minimum(np.unique(chans), 14).tolist()}) if chans is not None
                    else [0])
            cc = synth_cc([(t, s, a, b, c) for t, s, a, b in cc_events(sh, ppq, bpm) for c in used]) if sh else None
        ev = events(notes, ppq, bpm, chans, cc, voiced, tempo) if notes is not None and len(notes) else None
        if ev is None or len(ev) <= 3:  # (no notes, or only ones above key 127, which can't sound: nothing to play)
            self.ev, self.span, self.last = None, (0, 0), 0
            self.clear()
            return
        self.ev = ev
        ticks = ev["tick"][2:-1]
        self.last = int(self.frames(int(ticks[-1])))
        self.span = (int(self.frames(int(ticks[0]))), self.last)
        if old is None or not same_time:
            self.clear()
            return self.set_end()
        n = min(len(old), len(ev))  # the changed stretch: from the first event that differs to the last one
        a, b = old[:n], ev[:n]
        diff = np.flatnonzero((a["tick"] != b["tick"]) | (a["param"] != b["param"]) | (a["chan"] != b["chan"]))
        first = int(diff[0]) if len(diff) else n
        if first == len(old) == len(ev):
            return self.set_end()
        a, b = old[::-1][:n], ev[::-1][:n]
        diff = np.flatnonzero((a["tick"] != b["tick"]) | (a["param"] != b["param"]) | (a["chan"] != b["chan"]))
        last =int(diff[0]) if len(diff) else n
        lo = min(int(old["tick"][min(first, len(old) - 1)]), int(ev["tick"][min(first, len(ev) - 1)]))
        hi = max(int(old["tick"][max(len(old) - 1 - last, 0)]), int(ev["tick"][max(len(ev) - 1 - last, 0)]))
        self.clear(self.frames(lo), self.frames(hi) + TAIL * RATE)
        self.set_end()

    def set_end(self):
        """The sound ends where a piece found the ring died away, else TAIL after the last note (until it's made)."""
        self.span = (self.span[0], int(min([self.last + TAIL * RATE] + list(self.ends.values()))))

    # ------------------------------------------------------------ making the sound

    def _work(self, job, ev, ppq):
        started = time.perf_counter()
        stats = {}
        try:
            job.result = self.synth.render(ev, ppq, int(job.i * CHUNK * RATE), int(CHUNK * RATE),
                                           self.cfg["voices"], self.cfg["nofx"], job.cancel,
                                           lambda n: setattr(job, "done", n), stats)
        except SynthError as e:
            job.error = str(e)
        job.took, job.voices, job.end = time.perf_counter() - started, stats.get("voices", 0), stats.get("end")
        job.finished = True

    def anchor(self):
        """The frame the moving window starts at: the play line while playing, else the view's left edge."""
        if self.playing():
            return self.play_at
        return max(0, int(self.frame_of(self.win.beat_at(self.win.kb_w))))

    def wanted(self):
        """The pieces that should be made, in the order they're needed."""
        lo, hi = self.span
        if hi <= lo:
            return []
        at = max(self.anchor(), lo)
        first, last = int(at // (CHUNK * RATE)), int(min(at + AHEAD * RATE, hi - 1) // (CHUNK * RATE))
        return list(range(first, last + 1))

    def tick(self):
        self._tick = None
        if not self.win.winfo_exists():
            return
        if self.load is not None and not self.load.is_alive():  # the soundfont is open (or failed)
            err, path, self.load = self.load.error, self.load.path, None
            if path != self.cfg["font"]:  # (another one was picked while it opened: that one now)
                self._load_font()
            elif err:
                return self.win.preview_failed(err, font=True)
            else:
                self.loaded_at = time.perf_counter()
                self.app.ring_check()  # (Auto's CC run-on: measured from it now if it isn't known)
        self.look()
        for i, job in list(self.jobs.items()):
            if job.finished:
                del self.jobs[i]
                if job.error:
                    return self.win.preview_failed(job.error)
                elif job.result is not None and job.ver == self.ver.get(i, 0):
                    self.chunks[i] = job.result
                    if job.end is not None:
                        self.ends[i] = job.end
                        self.set_end()
                    if job.voices and i * CHUNK * RATE < self.last:  # (after the last note: only the ring, quick)
                        self.took = (self.took + [job.took])[-2 * WORKERS:]
                        self.voices_used = max(self.voices_used, job.voices)
        want = self.wanted()
        keep = set(want)
        at = self.anchor()
        for i in list(self.chunks):  # (far behind or ahead: thrown away)
            if i not in keep and not (at - BEHIND * RATE <= (i + 1) * CHUNK * RATE and i * CHUNK * RATE <= at):
                del self.chunks[i]
        for i, job in list(self.jobs.items()):
            if i not in keep:
                job.cancel.set()
                del self.jobs[i]
        # (a piece thrown away still runs until it sees cancel, which waits while its samples load: counted until it
        # ends, or a slider dragged over a big soundfont piled up threads)
        self.threads = [t for t in self.threads if t.is_alive()]
        if self.ev is not None and not self.loading() and (self.playing() or self.settled()):
            for i in want:
                if len(self.threads) >= WORKERS:
                    break
                if i not in self.chunks and i not in self.jobs:
                    job = self.jobs[i] = _Job(i, self.ver.get(i, 0))
                    t = threading.Thread(target=self._work, args=(job, self.ev, self.ppq), daemon=True)
                    t.start()
                    self.threads.append(t)
        if self.player is not None:
            pos = self.player.position()
            if pos is not None:
                self.line = self.beat_of(pos) + (self.shape[1] if self.shape else 0.0)
            if not self.player.active():
                self.stop_play()
        self.win.draw_preview()
        self._tick = self.win.after(TICK_MS, self.tick)

    def settled(self):
        """True once SETTLE has passed since the last change."""
        return time.perf_counter() - self.changed >= SETTLE

    def grey(self):
        """(from, to) window beats not made yet, in the order they come."""
        lo, hi = self.span
        spans, piece = [], CHUNK * RATE
        for i in range(int(lo // piece), int((hi - 1) // piece) + 1 if hi > lo else 0):
            if i in self.chunks:
                continue
            job = self.jobs.get(i)
            a = max(lo, i * piece + (job.done if job else 0))
            b = min(hi, (i + 1) * piece)
            if b <= a:
                continue
            if spans and abs(spans[-1][1] - a) < 1:
                spans[-1][1] = b
            else:
                spans.append([a, b])
        return [(self.beat_of(a), self.beat_of(b)) for a, b in spans]

    @property
    def speed(self):
        """How many times faster than real time the sound is made, all the pieces at once (None = not known)."""
        return CHUNK * len(self.took) / max(sum(self.took), 1e-6) * WORKERS if self.took else None

    def busy(self):
        return bool(self.jobs)

    # ------------------------------------------------------------ playing

    def playing(self):
        return self.player is not None

    def play_beat(self):
        """The play line as a window beat (at the first note until it's put somewhere)."""
        if self.line is None:
            return self.beat_of(self.span[0])
        return self.line - (self.shape[1] if self.shape else 0.0)

    def play(self):
        """Plays from the play line. Returns an error text, or None."""
        if self.synth is None or self.ev is None:
            return None
        if not self.synth.can_play:
            return tr("synth.no_device")
        self.play_at = max(0, int(self.frame_of(self.play_beat())))
        self.line = self.play_beat() + (self.shape[1] if self.shape else 0.0)
        try:
            self.player = Player(self.synth, self.pull, self.cfg["volume"])
        except SynthError as e:
            self.player = None
            return str(e)
        self.player.play(self.play_at)
        return None

    def replay(self):
        """(Another shape picked while playing) plays on from its first note, once its notes are in."""
        if self.win.winfo_exists() and not self.playing():
            self.play()
            self.win.draw_preview()

    def put_line(self, beat):
        """The play line to this window beat (playing: plays on from there)."""
        self.line = beat + (self.shape[1] if self.shape else 0.0)
        if self.playing():
            self.stop_play()
            self.play()

    def pull(self, n):
        """(BASS's thread) up to n frames of sound from play_at: None = not made yet (wait), empty = the end."""
        at = self.play_at
        lo, hi = self.span
        if at >= hi:
            return np.zeros((0, 2), np.float32)
        if at < lo:  # (before the first note: silence)
            m = min(n, lo - at)
            self.play_at += m
            return np.zeros((m, 2), np.float32)
        piece = int(CHUNK * RATE)
        got = self.chunks.get(at // piece)
        if got is None:
            return None
        start = at - (at // piece) * piece
        m = min(n, piece - start, hi - at)
        self.play_at += m
        return got[start:start + m]

    def stop_play(self):
        if self.player is not None:
            self.player.stop()
            self.player = None
