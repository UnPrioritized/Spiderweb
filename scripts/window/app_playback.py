"""Mixed into App: playing the notes (Space), right-drag listening and the MIDI out, Built-in BASSMIDI's
soundfont and settings."""

import collections
import math
import os
import threading
import time
from tkinter import filedialog, messagebox

from files import errors
from files.lang import tr, trn
from files.mathexpr import calc
from files.playback import BUILTIN, KESTREL, cc_list
from files.synth import FONT_TYPES, LIVE_DRUMS_CODE, LIVE_PORTS, Live, Synth, SynthError, silent_list
from notes.engine import slot_track_channel
from notes.hz_cc import cc_apart, set_auto_ring, typed_ring
from notes.hzbass import RING_MOST, has_cc
from notes.tempo_file import heard_tempo
from window.hz_preview import VOICES, font_list
from window.widgets import bad, good

RING_KEYS = (24, 36, 48, 60, 72, 84)  # keys whose ring is measured for Auto's CC run-on (the longest counts)
SLOW_START_S = 15.0  # Kestrel still starting after this: "Use Built-in BASSMIDI" / "Keep waiting" (user 2026-10-28:
# 60 s nobody waits for; its normal start 6-11 s with two soundfonts)
STUCK_START_S = 60.0  # ... after this, with no answer, the start is given up (the backstop)
SHOW_START_MS = 300  # Kestrel's "Starting" window only after this (soundfonts kept loaded: a start is ~20 ms)
QUIET_S = 3.0  # (a question nobody asked for waits this long after the last key / click: quiet_now)
# the red "Overloaded" text kept steady (user 2026-10-29: it flashed): on this long after the last overload, its
# number changing at most this often, a loaded MIDI's notes left out averaged over this long
OVER_HOLD_S, OVER_EVERY_S, OVER_AVG_S = 4.0, 1.0, 4.0  # (tries faster leave a few notes out every 2-3 s)
GROUP_MOST = 16  # synths side by side playing a loaded MIDI at most (Settings → Sound)
GROUP_DEFAULT = max(1, min(GROUP_MOST, (os.cpu_count() or 4) // 2))  # ... to start: half the cores (user
# 2026-10-24: every core would halt the PC)


def font_sig(path):
    """The soundfont file as its size, time changed and path (Auto's run-on measured for exactly it), None: none."""
    try:
        st = os.stat(path) if path else None
    except OSError:
        return None
    return f"{st.st_size}:{int(st.st_mtime)}:{os.path.normpath(path)}" if st else None


def fonts_sig(paths):
    """font_sig of every soundfont in the list ("|" between; one soundfont = its own font_sig), None: none / one
    missing. Auto's run-on is made by the whole list (hunt 2026-10-25: keyed on the top one, changes under it were
    never measured again)."""
    sigs = [font_sig(p) for p in paths]
    return "|".join(sigs) if sigs and None not in sigs else None


def kestrel_voices_top(err):
    """The most voices this graphics card takes, from Kestrel's refusal to open with more ("... cap --max-voices at
    16519104 ..."), else None."""
    import re
    m = re.search(r"max-voices at (\d+)", str(err or ""))
    return int(m.group(1)) if m else None


def _same_opts(a, b):
    """Kestrel opened with settings a = with b ("lower" changes while open: never counts)."""
    return {k: v for k, v in a.items() if k != "lower"} == {k: v for k, v in b.items() if k != "lower"}


def readable(n):
    """n to 3 figures for the status line's red text (466,814 -> 467,000: it changes every moment, unreadable)."""
    n = int(round(n))
    return n if n < 1000 else int(round(n, 3 - len(str(n))))


READY_AFTER_MS = 1500  # Built-in BASSMIDI made ready this long after the start (ready_builtin); None = never (tests)
LIVE_SETTLE_S = 0.4  # a synth opened right at Space runs this long before the notes (hunt 2026-10-25: a new BASSMIDI
# stream lost its first 12-46 ms; notes 0.4 s after it opened lost none)


class Playback:
    """Mixed into App."""

    def toggle_play(self):
        if self.player.running:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self, keep=False):
        """keep = started again at once (play line moved while playing, new PPQ): Built-in BASSMIDI keeps a voice
        limit lowered by itself."""
        self.scrub_end()
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("app.spiderweb_2"), str(e))
            return
        dev = self.midi_device.get()
        if dev in (BUILTIN, KESTREL) and not self.hz_preview["font"] and not self.pick_soundfont():
            return
        self.fresh_builtin()
        made = self._kestrel_made
        if dev == KESTREL and made is not None and made[0] is not None and (
                not _same_opts(made[0].opts, self.kestrel_opts) or list(made[2]) != font_list(self.hz_preview)):
            # (a setting / the soundfonts changed while it started: started again with the small window, never in
            # the main one, which froze for its seconds, hunts 2026-10-28)
            made[0].close()
            self._kestrel_made = None
        if dev == KESTREL and not (self.out.handle and self.out.name == dev) and self._kestrel_made is None:
            self.kestrel_first(keep)  # (plays once it's started)
            return
        fresh = dev == BUILTIN and not (self.out.handle and self.out.name == dev)
        err = self.out.open(dev)
        while err and dev in (BUILTIN, KESTREL) and self.font_failed:  # (moved, gone, broken: offer another)
            if not messagebox.askyesno(tr("app.spiderweb_2"), err + "\n\n" + tr("synth.pick_other"), parent=self) \
                    or not self.pick_soundfont(self.font_failed):
                return
            err = self.out.open(dev)
        if err:
            messagebox.showerror(tr("app.spiderweb_2"), err)
            return
        if fresh and self.out.handle:  # (opened just now, not ahead by ready_builtin: it runs a moment first)
            time.sleep(LIVE_SETTLE_S)
        if self.hz_window:  # (one thing plays at a time, user)
            self.hz_window.preview.stop_play()
            self.hz_window.draw_preview()
        # (Overlapping shapes keep their colours: shapes overlapping on a key share its channel, render keep)
        self.player.shared = self.rows_apart()
        self.player.along = self.loaded is not None
        rendered, ccs, ports = self.play_parts(ppq, bpm)
        end = self.play_end(ppq, beats, self.playhead)
        self.player.ready = None
        self.player.stop()
        self.player._clock = None  # (the loaded MIDI's feeder waits for the new line, never the last play's)
        self.player.map = None  # (its reader waits for this play's tempo: Player.start sets it)
        self.piano_back()
        if self.loaded is not None:  # (first: the play line waits until its first notes are read)
            self.start_loaded(int(round(self.playhead * ppq)))
        self.kestrel_parts()
        self.slow_down.reset()  # (each Play at the song's own speed: Player.start too)
        self._pace_seen = self._pace_seen_b = None  # (Kestrel's silence / BASSMIDI's notes thinned, seen)
        self._k_drops = None  # (Kestrel's notes left out by its own lowering: count, when it last grew)
        self._thin_hist = collections.deque()  # (a loaded MIDI's (time, notes left out, sent): notes_left_text)
        self.player.start(rendered, ppq, bpm, self.playhead, end, self.picture_use10, ccs, ports,
                          heard_tempo(self.tempo, bpm, ppq, int(end * ppq) + 1, self.file_heard()))
        if self.out.name == BUILTIN:
            self.overload.start(self.out.handle, self.play_voices, self.play_guard.get(), keep)
        elif self.out.edo:
            self.status.config(text=tr("app.edo31_audio_builtin_only"))
        self.play_btn.config(text=tr("app.stop_space"))
        self.roll.show_playhead(start=True)
        if self.loaded is not None:  # (a redraw now: the loaded notes' exact pictures start reading ahead the way
            self.roll.request_redraw()  # it plays before the first page turn, roll_loaded._keep_ahead)
        self._play_job = self.after(15, self._play_tick)

    def kestrel_parts(self):
        """Kestrel: channel 10 a normal instrument on every port, as Built-in BASSMIDI plays Spiderweb's own shapes
        (it made them drums: a GM soundfont's snare, hunt 2026-10-28), except the ports a loaded MIDI uses: its
        channel 10 stays drums there. Each Play: the last play's choice never carries over."""
        if self.out.name != KESTREL or not self.out.handle:
            return
        from files.kestrel import PART_TYPE
        lp = self.loaded_play
        files_ports = set((lp.tport % 16).tolist()) if lp is not None and lp.tport is not None else set()
        for port in range(16):
            self.out.handle.send(PART_TYPE | 9 << 8 | (1 if port in files_ports else 0) << 16 | port << 24)

    def play_end(self, ppq, beats, start):
        """Where playing stops: a quarter bar after the last note ends (a loaded MIDI's too; or after the play
        line if already past it), rounded up to the next quarter-bar line."""
        quarter = beats / 4
        last = (int(self.rendered[:, 1].max()) if len(self.rendered) else 0) / ppq
        if self.loaded is not None:
            last = max(last, self.loaded.end / ppq)
        return math.ceil((max(last, start) + quarter) / quarter - 1e-9) * quarter

    def file_heard(self):
        """A loaded MIDI's own tempo for heard_tempo (its notes keep their file time in Keep timing parts), else
        None."""
        from notes.loaded import file_tempos
        return file_tempos(self.loaded) if self.loaded is not None else None

    def start_loaded(self, t0):
        """The loaded MIDI plays along from tick t0, the play line (files/play_loaded.py): Built-in BASSMIDI = its
        own synths side by side (a LiveGroup, opened for each play), other MIDI outs = the same device. The Player
        starts its line once the first notes are read (Player.ready)."""
        from files.play_loaded import instruments, preload
        self.stop_loaded()
        synth = self.synth if self.out.name == BUILTIN else None
        if synth is None or not synth.presets_missing(*instruments(self.loaded)):
            self._start_loaded_now(t0)
            return
        # the file's instruments loaded first, in the background (hunt 2026-10-25: a whole piano from a hard disk
        # froze the window for seconds at the first Space); the play line waits for them (gate), "Loading…" shown
        gate, me, lm = threading.Event(), object(), self.loaded
        self.player.ready = gate
        self._instruments_job = me
        self.busy(tr("app.loading_instruments"))
        job = threading.Thread(target=preload, args=(synth, lm), daemon=True)
        job.start()

        def done():
            if job.is_alive():
                self.after(30, done)
                return
            if self._instruments_job is not me:
                return  # (stopped meanwhile, or played again: that one goes on)
            self._instruments_job = None
            self.busy(None)
            if self.player.running and self.loaded is lm:
                self._start_loaded_now(t0, gate)
        self.after(30, done)

    def _start_loaded_now(self, t0, gate=None):
        """start_loaded once the file's instruments are loaded. gate = the play line's waiting (opened once the
        loaded MIDI's first notes are read)."""
        from files.play_loaded import LoadedPlayer
        from files.playback import KDMAPI
        from files.synth import LiveGroup
        sinks, kind = None, {KDMAPI: "kdmapi", KESTREL: "kestrel"}.get(self.out.name, "device")
        if self.out.name == BUILTIN and self.synth is not None:
            try:
                self.loaded_group = LiveGroup(self.synth, self.loaded_synths, self.play_voices,
                                              limiter=self.play_limiter.get())
                sinks, kind = self.loaded_group.lives, "group"
            except SynthError as e:  # (its own shapes still play: told on the status line)
                self.status.config(text=str(e))
                if gate is not None:
                    gate.set()
                return
        if kind == "kestrel":  # (its densest parts read ahead in the background, once: files/play_dense.py)
            from files.play_dense import ensure
            ensure(self.loaded)
            if getattr(self, "_ahead_job", None) is None:  # (asked after playing, never as Play is pressed)
                self._ahead_job = self.after(1000, self.watch_ahead)
        tl = self.track_list
        lp = self.loaded_play = LoadedPlayer(self.loaded, self.player, self.out, sinks, kind,
                                             muted=tl.loaded_mutes)
        if gate is None:
            self.player.ready = lp.ready
        else:  # (the line already waits on gate: opened once this one's first notes are read, or it's stopped)
            def pass_on():
                while not lp.ready.wait(0.01) and self.loaded_play is lp:
                    pass
                gate.set()
            threading.Thread(target=pass_on, daemon=True).start()
        lp.start(t0)

    def stop_loaded(self):
        if getattr(self, "_instruments_job", None) is not None:  # (instruments still loading: that play is off)
            self._instruments_job = None
            self.busy(None)
        if self.loaded_play is not None:
            self.loaded_play.stop()
            self.loaded_play = None
        if self.loaded_group is not None:
            self.loaded_group.close()
            self.loaded_group = None

    def play_changed(self):
        """The notes or the project's numbers changed while playing: it plays on with them from where it is (a new
        PPQ: started again from there)."""
        if not self.player.running:
            return
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError:
            return  # (a number being typed: plays on as it was)
        if ppq != self.player.ppq or ppq != self.ppq:
            self.stop_play()
            if ppq == self.ppq:
                self.start_play(keep=True)
            return
        # (the end: never before where it is, so a note deleted at the end doesn't stop it at once)
        rendered, ccs, ports = self.play_parts(ppq, bpm)
        end = self.play_end(ppq, beats, self.player.position())
        self.player.update(rendered, bpm, end, self.picture_use10, ccs, ports,
                           heard_tempo(self.tempo, bpm, ppq, int(end * ppq) + 1, self.file_heard()))

    def play_tempo(self):
        """The tempo line changed while playing: the player goes on at the new speed (the notes stay)."""
        self._tempo_play_job = None
        if not self.player.running:
            return
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError:
            return
        if ppq != self.player.ppq:
            return self.play_changed()
        end = int(self.player.stop_beat * ppq) + 1
        self.player.set_tempo(bpm, heard_tempo(self.tempo, bpm, ppq, end, self.file_heard()))

    def play_parts(self, ppq, bpm):
        """play_apart's, the CC lines as the player's messages (cc_list)."""
        rendered, msgs, ports = self.play_apart(ppq, bpm)
        return rendered, cc_list(msgs, self.picture_use10, ports) if msgs else [], ports

    def play_apart(self, ppq, bpm, device=None):
        """(the notes, the Hz basses' CC lines {slot: messages}, the synth's ports) for the player: Built-in BASSMIDI
        puts slots on its ports as the MIDI export does (engine.slot_ports; other MIDI outs have one port: 16 channels). A Hz
        bass's CC lines never reach other shapes' notes while there's a free channel (hz_cc.cc_apart; files keep
        their channels). device = the MIDI out to plan for (None: the open one)."""
        per_shape = {i: self.cc_messages(ppq, bpm, {i}) for i, sh in enumerate(self.shapes)
                     if has_cc(sh.get("hz") or {})}
        per_shape = {i: got for i, got in per_shape.items() if got}
        # (pasted notes' CCs too, hunt 2026-10-22: as the files have them; their channels are kept for them already)
        pasted = self.cc_messages(ppq, bpm, {i for i, sh in enumerate(self.shapes) if "cc" in sh}) \
            if any("cc" in sh for sh in self.shapes) else {}
        heard = self.track_list.unmuted(self.rendered)  # (tracks muted in the track list: not played)
        if not per_shape and not any(pasted.values()):
            return heard, {}, 0
        ports = LIVE_PORTS if (device or self.out.name) in (BUILTIN, KESTREL) else 0
        rendered, msgs = cc_apart(heard, per_shape, self.picture_use10, ports) if per_shape else (heard, {})
        for s, ms in pasted.items():
            if ms:
                msgs.setdefault(s, []).extend(ms)
                msgs[s].sort(key=lambda e: e[0])
        return rendered, msgs, ports

    def _play_tick(self):
        self._play_job = None
        self.playhead = self.player.position()
        if not self.player.running:
            self.stop_play()
            return
        lp = self.loaded_play
        if lp is not None and lp.error is not None:  # (its reading failed: stop, never play on silent; user)
            self.stop_play()
            messagebox.showerror(tr("app.spiderweb_2"), tr("app.loaded_play_failed"), parent=self)
            return
        live = self.out.handle if self.out.name == KESTREL else None
        now = time.perf_counter()
        if live is not None and now - getattr(self, "_k_checked", 0.0) > 0.25:  # (its engine dead: hunt 10-28)
            self._k_checked = now
            why = live.k.error()
            if why is not None:  # (silence with "Overloaded" flickering, every later Play too: stopped, said)
                self.stop_play()
                self.out.close()
                self.kestrel_failed(SynthError("playback.kestrel_failed", err=why), during=True)
                return
        self.pace_play(now)
        self.roll.show_playhead()
        self.show_overload()
        self._play_job = self.after(15, self._play_tick)

    def slowing(self):
        """The Slow down tick of the MIDI out in use (13f; other MIDI outs have none)."""
        return self.slow_kestrel.get() if self.out.name == KESTREL else \
            self.slow_builtin.get() if self.out.name == BUILTIN else False

    def on_slow_down(self):
        """A Slow down tick changed: off = back to the song's own speed at once."""
        if not self.slowing() and self.player.speed != 1.0:
            self.player.set_speed(1.0)
        self.slow_down.reset()
        self.show_overload()

    def pace_play(self, now):
        """13f: the synth's load -> the speed the song plays at (files/slow_down.py), while its tick is on.
        "Lower it by itself" (both synths) waits until it's at the slowest (user: slow down first), or where slowing
        stopped helping (SlowDown.floor, user 2026-10-29), and on the way
        back gives its notes / voices back BEFORE the speed climbs (hunt 2026-10-29: handed back at 11 %, the full
        limit came back at once and the stutter with it)."""
        from files.slow_down import FIT
        on = self.slowing()
        if not on and self.player.speed != 1.0:
            self.player.set_speed(1.0)
        self._pace_fit = FIT  # (Kestrel: its block time; BASSMIDI: play_load)
        load, behind, easy, lowered, effort = self.play_load()
        if on:
            can_lower = self.kestrel_opts["lower"] if self.out.name == KESTREL else self.play_guard.get()
            v = self.slow_down.step(load, now, behind, easy=easy, hold=lowered, effort=effort, can_lower=can_lower,
                                    fit=self._pace_fit)
            if v is not None:
                self.player.set_speed(v)
        free = not on or self.slow_down.floored or lowered  # (at 10 %, or where slowing stopped helping)
        if self.overload.running:
            guard = self.play_guard.get() and free
            if self.overload.guard != guard:
                self.overload.set_guard(guard)
        if self.out.name == KESTREL and self.out.handle and self.player.running:
            self.out.handle.k.set_lower(self.kestrel_opts["lower"] and free)  # (a flag the render thread reads)

    def play_load(self):
        """(the synth's load = time making the sound / the time it lasts, None = not known; whether the sound broke
        up lately; clearly room to spare; "Lower it by itself" still holding notes / voices back; how hard the synth
        works, to see whether slowing helps, None = not known)."""
        if self.out.name == KESTREL and self.out.handle:
            from files.kestrel import RATE as K_RATE
            from files.slow_down import queue_load
            k = self.out.handle.k
            st = k.stats()
            seen, self._pace_seen = getattr(self, "_pace_seen", None), st["silence"]
            behind = seen is not None and st["silence"] > seen
            # (never "easy" either: quick blocks at 10 % say nothing about 30 %, real Lagtester at 1 M voices
            # 2026-10-29: it forgot the speed that broke up, climbed to 31 % and broke up again)
            easy = False
            # (its own lowering never goes back to "no limit" while on, only climbs past what's asked: holding back =
            # notes left out within the last second)
            drops = st.get("budget_dropped", 0)
            was = getattr(self, "_k_drops", None) or (drops, float("-inf"))
            now = time.perf_counter()
            self._k_drops = (drops, now if drops > was[0] else was[1])
            lowered = now - self._k_drops[1] < 1.0
            # (by the blocks made ahead still waiting, as its own "Lower it by itself" judges: a slow block with
            # every ready block still there is heard fine)
            effort = st["last_block_us"] / (k.block / K_RATE * 1e6) if st.get("last_block_us") else None
            return queue_load((st["rendered"] - st["handed"]) / k.block, k.ahead), behind, easy, lowered, effort
        if self.out.name == BUILTIN:  # (read by the Overload watcher / the loaded MIDI's feeder: never BASS here)
            lp, o = self.loaded_play, self.overload
            loads = [x for x in (o.load, lp.load if lp is not None else None) if x is not None]
            thinned = lp.thinned if lp is not None else 0
            seen, self._pace_seen_b = getattr(self, "_pace_seen_b", None), thinned
            # (sound missing within the last second, judged as "Overloaded" is; or notes left out since the last look)
            behind = bool(getattr(o, "over", False)) or (seen is not None and thinned > seen)
            lowered = bool(getattr(o, "guard", False)) and getattr(o, "now_limit", 0) < getattr(o, "limit", 0)
            if not loads:
                return None, behind, False, lowered, None
            # (a busy synth that still makes its sound in time isn't heard, user 2026-10-29: "fine means no
            # stuttering": from 95 % only; under 90 % with nothing lost = calm, it may speed up again (hunt: between 50
            # and 95 % it stayed slowed for good))
            c = max(loads)
            # (never "easy": its note budget can be what limits at a low CPU, Lagtester at 17 % 2026-10-29: forgetting
            # the speed that broke up there made it climb back at once, every 2-3 s)
            # (its work, to climb back as fast as fits (user 2026-10-29: slow, Kestrel's fast) and to see whether
            # slowing helps: a loaded MIDI's note-ons asked for / its note budget (exactly as the speed: its budget
            # leaves notes out at a low CPU, Lagtester at 17 %), the CPU too; own shapes alone: the CPU)
            asked = lp.asked if lp is not None and lp.sinks else None
            effort = c if asked is None else max(asked, c)
            # (a loaded MIDI: chords come in one go at any speed, so the average says nothing about how much faster
            # fits (real Lagtester: it jumped to 1x and back every 2 s); careful tries, but straight back once the song
            # asks for almost no notes (the busy part is over))
            from files.slow_down import FIT_CPU
            self._pace_fit = FIT_CPU if asked is None else None
            easy = asked is not None and asked < 0.05 and not behind
            return (c if c >= 0.95 else 0.75 if c >= 0.9 else 0.3), behind, easy, lowered, effort
        return None, False, False, False, None

    def stop_play(self):
        if self._play_job:
            self.after_cancel(self._play_job)
            self._play_job = None
        if self.player.running or self.player.thread is not None:
            self.playhead = self.player.position()
            self.player.stop()
            self.roll.show_playhead()
            self.schedule_autosave()
        self.stop_loaded()
        self.piano_back()
        self.overload.stop()
        self.show_overload()
        self.play_btn.config(text=tr("app.play_space"))

    def piano_back(self):
        """(A Windows MIDI device / OmniMIDI: the loaded MIDI and the shapes share its 16 channels) bank 0 + the
        piano on every channel, at every play's start and stop: a loaded MIDI's instruments never stay on for the
        shapes (hunt 2026-10-25; user: own shapes always the piano). Built-in BASSMIDI plays a loaded MIDI on synths
        of its own."""
        if self.out.handle is None or self.out.name in (BUILTIN, KESTREL):
            return
        for ch in range(16):
            for m in (0xB0 | ch, 0xB0 | ch | 32 << 8, 0xC0 | ch):
                self.out.send(m)

    def notes_left_text(self, lp, now):
        """A loaded MIDI on Built-in BASSMIDI: "Overloaded: 12,000 notes left out a second (24 %)" over the last
        second (its note budget leaves note starts out; user 2026-10-29: the number and the share)."""
        hist = self._thin_hist  # (filled every play tick by show_overload: an average over OVER_AVG_S)
        t0, th0, se0 = hist[0] if hist else (now, lp.thinned, lp.sent)
        if now - t0 < 0.2:
            return tr("app.overloaded")
        left, sent = lp.thinned - th0, lp.sent - se0
        if left <= 0:
            return tr("app.overloaded")
        return tr("app.overloaded_notes", n=f"{readable(left / (now - t0)):,}",
                  p=max(1, round(100 * left / max(1, left + sent))))

    def show_overload(self):
        """The red text at the status line's right end (and in Settings → Sound): "Overloaded", with the
        voices playing when the limit was lowered by itself (Kestrel's too), a loaded MIDI's notes left out a second
        on Built-in BASSMIDI (user 2026-10-29)."""
        text = self.overload.text()
        lp = self.loaded_play
        if lp is not None:  # (a loaded MIDI's notes thinned for the synth within the last second: overloaded too)
            now = time.perf_counter()
            hist = self._thin_hist
            hist.append((now, lp.thinned, lp.sent))
            while len(hist) > 2 and now - hist[0][0] > OVER_AVG_S:
                hist.popleft()
            if lp.thinned != self._thin_seen[0]:
                self._thin_seen = (lp.thinned, now)
            if not text and now - self._thin_seen[1] < 1.0:
                text = self.notes_left_text(lp, now) if lp.sinks else tr("app.overloaded")
        if self.out.name == KESTREL and self.out.handle and self.player.running:
            # (Kestrel (GPU): notes left out to keep up, or gaps in the sound, within the last second)
            now = time.perf_counter()
            st = self.out.handle.k.stats()
            seen = getattr(self, "_k_seen", (None, 0.0))
            key = (st["late"], st["budget_dropped"])
            if seen[0] is not None and key != seen[0]:
                seen = (key, now)
            elif seen[0] is None:
                seen = (key, 0.0)
            self._k_seen = seen
            if not text and now - seen[1] < 1.0:
                # (its own lowering cut the voices: how many it lets sound now, as Built-in BASSMIDI's text)
                top = self.out.handle.opts.get("voices", self.kestrel_opts["voices"])
                cap = st.get("voice_cap", 0)
                text = (tr("app.overloaded_1") if cap == 1 else tr("app.overloaded_n", n=f"{readable(cap):,}")) \
                    if 0 < cap < top else tr("app.overloaded")
        elif not self.player.running:
            self._k_seen = (None, 0.0)
        # (steady, user 2026-10-29: it flashed in and out): shown on for OVER_HOLD_S after the last overload, its
        # wording / number changing at most every OVER_EVERY_S; Stop clears it at once
        now, shown = time.perf_counter(), self.overload_text
        if self.player.running:
            if text:
                self._ov_last = now
            elif shown and now - getattr(self, "_ov_last", 0.0) < OVER_HOLD_S:
                text = shown
            if text and shown and text != shown and now - getattr(self, "_ov_changed", 0.0) < OVER_EVERY_S:
                text = shown
        if text != shown:
            self._ov_changed = now
        v = self.player.speed
        slowed = tr("app.slowed", n=max(1, round(v * 100))) if self.player.running and v < 1.0 else ""
        if text != self.overload_text or slowed != self.slowed_text:
            self.overload_text, self.slowed_text = text, slowed
            self.overload_label.config(text=text)
            self.slowed_label.config(text=slowed)
            free = 0
            if text:
                self.overload_label.place(relx=1.0, rely=0.5, anchor="e", x=-6)
                free = self.overload_label.winfo_reqwidth() + 18
            else:
                self.overload_label.place_forget()
            if slowed:  # (13f: orange, left of the red "Overloaded" when both show)
                self.slowed_label.place(relx=1.0, rely=0.5, anchor="e", x=-6 - free)
                free += self.slowed_label.winfo_reqwidth() + (12 if text else 18)
            else:
                self.slowed_label.place_forget()
            # (a long status message stops short of them with "…" instead of running under them)
            self.status.keep_free(free)
            if self.builtin_window is not None:
                self.builtin_window.refresh()

    def midi_device_picked(self):
        """MIDI out picked: the old one stops and closes (the same one picked again changes nothing)."""
        if self.midi_device.get() == self._device_was:
            return
        self._device_was = self.midi_device.get()
        self.stop_play()
        self.out.close()
        if self._device_was != KESTREL:  # (only Kestrel uses it)
            self.drop_read_ahead()
            from files import kestrel
            if self._kestrel_job is not None:  # (a start on its way: its window closes at once, it gives up)
                self._kestrel_job["close"]()
            kestrel.let_go()  # (its soundfonts kept loaded: freed, user 2026-10-28)
        self.sync_builtin()
        self.edo_warn()
        if self.hz_window:
            self.hz_window.show_cc()  # (16 channels or plenty)
        self.schedule_autosave()
        if READY_AFTER_MS is not None:
            self.after(300, self.ready_builtin)
        self.read_ahead()

    def read_ahead(self):
        """Kestrel (GPU) picked with a MIDI loaded: its densest parts read ahead now, in the background (files/
        play_dense.py; ~20 s for Shinki), so the FIRST play has them too (user 2026-10-27; it was started by Play).
        Only once Kestrel has started on this PC (hunt 2026-10-28: a minute and ~3 GB for one that never could)."""
        if self.loaded is not None and self.midi_device.get() == KESTREL and getattr(self, "kestrel_ok", False):
            from files.play_dense import ensure
            ensure(self.loaded)
            self.watch_ahead()

    def drop_read_ahead(self):
        """The loaded MIDI's read-ahead stopped and let go (its MIDI going, or another MIDI out picked): it read on
        for a minute and kept up to 3 GB after New / another MIDI / Built-in BASSMIDI (hunt 2026-10-28). Kestrel
        picked again = read again (a second when kept on disk)."""
        job = getattr(self, "_ahead_job", None)
        if job is not None:
            self.after_cancel(job)
            self._ahead_job = None
        lm = self.loaded
        if getattr(lm, "dense", None) is not None:
            lm.dense.stop()
            lm.dense = None

    def quiet_now(self):
        """A good moment for a question nobody asked for (hunt 2026-10-28: it popped up as Space was pressed, mid-drag,
        as Kestrel started): not playing or starting Kestrel, no mouse button held, no key / click for QUIET_S."""
        if not getattr(self, "_quiet_bound", False):
            self._quiet_bound = True
            self._input_at, self._buttons_down = time.perf_counter(), 0

            def press(e):  # (getattr: the watch outlives its counts when the window is set back as new, tests)
                self._input_at = time.perf_counter()
                self._buttons_down = getattr(self, "_buttons_down", 0) + 1

            def release(e):
                self._input_at = time.perf_counter()
                self._buttons_down = max(0, getattr(self, "_buttons_down", 0) - 1)
            self.bind_all("<ButtonPress>", press, add="+")
            self.bind_all("<ButtonRelease>", release, add="+")
            self.bind_all("<KeyPress>", lambda e: setattr(self, "_input_at", time.perf_counter()), add="+")
            return False
        return (not self.player.running and self._kestrel_job is None and self._kestrel_made is None
                and not self._buttons_down and time.perf_counter() - self._input_at >= QUIET_S)

    def watch_ahead(self):
        """Once the loaded MIDI's read-ahead is all read: asks whether to keep it on disk (play_dense.may_keep;
        never written unasked, user 2026-10-28: 3 GB). Waits while playing (a question mid-song)."""
        if getattr(self, "_ahead_job", None) is not None:
            self.after_cancel(self._ahead_job)
        self._ahead_job = None
        lm = self.loaded
        d = lm.dense if lm is not None else None
        if d is None:
            return
        if not d.done() or (d.unsaved and not self.quiet_now()):
            self._ahead_job = self.after(1000, self.watch_ahead)
            return
        if not d.unsaved:
            return
        from files.play_dense import decline, kept_bytes, save
        from window.load_midi import ask_choice, size_text
        size = kept_bytes(lm)
        answer = ask_choice(self, tr("ks.keep_title"),
                            tr("ks.keep_ask", name=os.path.basename(lm.path), size=size_text(size),
                               s=f"{d.took:.0f}"),
                            [("keep", tr("ks.keep_yes")), ("never", tr("ks.keep_never")),
                             ("later", tr("ks.keep_later"))], focus="later")  # (Space / Enter never keep 3 GB)
        if self.loaded is not lm or lm.dense is not d or not d.unsaved:  # (changed while asked)
            return
        if answer == "keep":
            d.unsaved = False
            got = []
            th = threading.Thread(target=lambda: got.append(save(d)), daemon=True, name="kestrel-dense-save")
            th.start()
            self.status.config(text=tr("ks.keeping_status", size=size_text(size)))

            def said():  # (the line says how it ended: "kept" showed before the writing was done, hunt 10-28)
                if th.is_alive():
                    self.after(500, said)
                elif self.winfo_exists():
                    self.status.config(text=tr("ks.kept_status" if got and got[0] else "ks.keep_failed",
                                               size=size_text(size)))
            self.after(500, said)
        elif answer == "never":
            decline(d)
        else:
            d.unsaved = False  # (asked again the next time the MIDI is loaded)

    def make_live(self):
        """(MidiOut.open) The built-in synth started for playing, with the soundfont (the Hz bass preview's) and
        voice limit set now. Raises SynthError (font_failed = the soundfont that couldn't be read, else None)."""
        self.font_failed = None
        fonts = font_list(self.hz_preview)
        if not fonts:
            raise SynthError("synth.no_font")
        self.busy(tr("app.opening_soundfont"))  # (a big soundfont takes a moment)
        try:
            if self.synth is None:
                self.synth = Synth()
            if self.synth.needs_open(fonts):
                try:
                    self.synth.set_fonts(fonts)
                except SynthError as e:
                    self.font_failed = e.path or fonts[0]
                    raise
            live = Live(self.synth, self.play_voices, limiter=self.play_limiter.get())
            self.note_builtin()
            self.ring_check()  # (Auto's CC run-on: measured from it now if it isn't known)
            return live
        finally:
            self.busy(None)

    def make_kestrel(self):
        """(MidiOut.open) Kestrel (GPU) started with the shared soundfonts (each overrides the ones above it, as
        in Built-in BASSMIDI), playing through BASS's sound device. Raises SynthError (font_failed: as make_live).
        Its own settings (Settings → Sound) come later: Kestrel's defaults, "Lower it by itself" on."""
        from files import kestrel
        from files.kestrel import KestrelOut
        self.font_failed = None
        fonts = font_list(self.hz_preview)
        if not fonts:
            raise SynthError("synth.no_font")
        made, self._kestrel_made = self._kestrel_made, None
        if made is not None:  # (started by kestrel_first)
            out, err, for_fonts = made
            if for_fonts == fonts and (out is None or _same_opts(out.opts, self.kestrel_opts)):
                if err is not None:
                    raise err
                errors.note("Kestrel (GPU)", f"{len(fonts)} soundfont(s)")
                # (Lower it by itself changed while it started: it opened with the old one, hunt 2026-10-28)
                out.k.set_lower(self.kestrel_opts["lower"])
                out.opts["lower"] = self.kestrel_opts["lower"]
                return out
            if out is not None:  # (the soundfonts / settings changed meanwhile: started again with them)
                out.close()
        loaded = kestrel.kept_for(fonts)
        if loaded is None:  # (it would read for seconds: never on the window's thread, e.g. an Hz window key; hunt
            # 2026-10-29: it froze the window and read beside a start already on its way)
            if self._kestrel_job is None:
                self.kestrel_first(False, play=False)
            raise SynthError("ks.still_starting")
        self.busy(tr("app.opening_kestrel"))
        try:
            if self.synth is None:
                self.synth = Synth()
            top = loaded.voices_top(self.kestrel_opts["block"])
            if top is not None and top < self.kestrel_opts["voices"]:
                self.kestrel_lowered(top)  # (before opening: never a refused open first)
            try:
                out = KestrelOut(self.synth, fonts, loaded=loaded, **self.kestrel_opts)
            except SynthError as e:
                top = kestrel_voices_top(e)
                if top is None or top >= self.kestrel_opts["voices"]:
                    raise
                self.kestrel_lowered(top)
                out = KestrelOut(self.synth, fonts, loaded=loaded, **self.kestrel_opts)
            errors.note("Kestrel (GPU)", f"{len(fonts)} soundfont(s)")
            self.kestrel_ok = True  # (it works on this PC: its read-ahead may start)
            return out
        finally:
            self.busy(None)

    def kestrel_lowered(self, top):
        """Max voices past what this graphics card takes: lowered to its top and said plainly (it showed Kestrel's
        command-line text every Play, hunt 2026-10-28)."""
        self.kestrel_top = top
        self.kestrel_vars["voices"].set(str(top))
        self.on_kestrel_opt("voices")
        messagebox.showinfo(tr("app.spiderweb_2"), tr("ks.voices_lowered", n=f"{top:,}"), parent=self)

    def kestrel_first(self, keep, play=True):
        """Kestrel (GPU) started on a thread for a play, a small window saying so meanwhile (user 2026-10-27: the
        first Play froze the window for seconds, the status line was easy to miss); it closes and playing starts
        once Kestrel is ready (play False: only opened). Play again / the window's X while it starts = that play is
        off (Kestrel still starts, kept for the next one; a new start waits for it: files.kestrel one at a time)."""
        import tkinter as tk
        from tkinter import ttk
        from files import kestrel
        from files.kestrel import KestrelOut
        job = self._kestrel_job
        if job is not None:  # (Play again while it starts = its X: no play, the start given up)
            job["close"]()
            return
        fonts = font_list(self.hz_preview)
        opts = dict(self.kestrel_opts)
        try:
            if self.synth is None:
                self.synth = Synth()
        except SynthError as e:
            messagebox.showerror(tr("app.spiderweb_2"), str(e))
            return
        synth = self.synth
        win = tk.Toplevel(self)
        win.withdraw()
        win.title(tr("app.spiderweb_2"))
        win.transient(self)
        win.resizable(False, False)
        box = ttk.Frame(win, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("app.opening_kestrel")).pack(anchor="w")
        bar = ttk.Progressbar(box, mode="indeterminate", length=280)
        bar.pack(fill="x", pady=(10, 0))
        bar.start(15)
        job = self._kestrel_job = {"play": play, "win": win, "out": None, "err": None, "gone": False,
                                   "began": time.perf_counter()}

        def closed():  # (X = this start given up: Play starts another; it never swallowed every later Play, hunt)
            job["play"] = False
            job["gone"] = True
            if self._kestrel_job is job:
                self._kestrel_job = None
            if win.winfo_exists():
                win.destroy()
        job["close"] = closed
        win.protocol("WM_DELETE_WINDOW", closed)
        slow = ttk.Frame(box)  # (shown after SLOW_START_S: the user picks, never made to wait for a limit)
        ttk.Label(slow, text=tr("ks.slow_start")).pack(anchor="w", pady=(10, 4))
        buttons = ttk.Frame(slow)
        buttons.pack(anchor="e")

        def use_builtin():
            closed()
            self.midi_device.set(BUILTIN)
            self.start_play(keep)
        ttk.Button(buttons, text=tr("ks.use_builtin"), command=use_builtin).pack(side="left", padx=(0, 6))
        def keep_waiting():  # (an answer: no "timed out" later, hunt 2026-10-29)
            job["waiting"] = True
            slow.pack_forget()
        ttk.Button(buttons, text=tr("ks.keep_waiting"), command=keep_waiting).pack(side="left")
        win.update_idletasks()  # (over the middle of the main window)
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

        def show():  # (only when it takes a while: on kept soundfonts a start is ~20 ms, no flash)
            if not job["gone"] and self._kestrel_job is job and win.winfo_exists():
                win.deiconify()
        self.after(SHOW_START_MS, show)

        def work():
            try:
                # (kept loaded: only soundfonts not read yet are read; a broken one is named by its reading)
                loaded = kestrel.loaded_for(fonts)
                top = loaded.voices_top(opts["block"])
                if top is not None and top < opts["voices"]:  # (lowered first: never a refused open)
                    job["top"] = top
                    return
                job["out"] = KestrelOut(synth, fonts, loaded=loaded, **opts)
            except SynthError as e:
                job["err"] = e
            except Exception as e:  # (anything else: said like a failed start)
                job["err"] = SynthError("playback.kestrel_failed", err=e)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        def done():
            waited = time.perf_counter() - job["began"]
            if thread.is_alive() and not job["gone"]:
                if waited > SLOW_START_S and not job.get("slow") and win.winfo_exists():
                    job["slow"] = True
                    slow.pack(fill="x")
                if waited > STUCK_START_S and not job.get("waiting"):  # (the backstop: nobody answered, given up)
                    closed()
                    self.kestrel_failed(SynthError("playback.kestrel_failed", err="timed out"), play=False)
                    self.after(500, done)  # (its synth closed when it comes: never left running unseen)
                    return
                self.after(30, done)
                return
            if job["gone"]:  # (given up: a start still on its way is closed when it ends)
                if thread.is_alive():
                    self.after(500, done)
                    return
                if job["out"] is not None:
                    job["out"].close()
                if self.midi_device.get() != KESTREL:  # (Use Built-in BASSMIDI / another out: what it loaded too)
                    kestrel.let_go()
                return
            self._kestrel_job = None
            if win.winfo_exists():
                win.destroy()
            if self.midi_device.get() != KESTREL or not self.winfo_exists():  # (another MIDI out picked meanwhile)
                if job["out"] is not None:
                    job["out"].close()
                kestrel.let_go()  # (what it loaded meanwhile too)
                return
            if job["err"] is not None and (getattr(job["err"], "cancelled", False)
                                           or font_list(self.hz_preview) != fonts):
                # (the soundfonts changed while it started, it gave up: started again with the new ones)
                if job["play"]:
                    self.kestrel_first(keep)
                return
            if job["out"] is not None and self.out.handle is not None and self.out.name == KESTREL:
                job["out"].close()  # (opened meanwhile another way: one Kestrel, never a second one left open)
                if job["play"] and not self.player.running:
                    self.start_play(keep)
                return
            top = job.get("top") or kestrel_voices_top(job["err"])
            if top is not None and top < self.kestrel_opts["voices"]:
                self.kestrel_lowered(top)  # (then started again: on the kept soundfonts, in a moment)
                if job["play"]:
                    self.kestrel_first(keep)
                return
            if job["err"] is not None:  # (said in plain words with a way on, hunt 2026-10-28)
                self.kestrel_failed(job["err"], play=job["play"], keep=keep)
                return
            self.kestrel_ok = True  # (it works on this PC: its read-ahead may start)
            self.read_ahead()
            self._kestrel_made = (job["out"], job["err"], fonts)
            if job["play"] and not self.player.running:
                self.start_play(keep)
            elif job["err"] is None:  # (no play: opened as after one, the next Play starts at once)
                self.out.open(KESTREL)
        self.after(30, done)

    def kestrel_failed(self, err, play=False, keep=False, during=False):
        """Kestrel couldn't start / stopped while playing (hunt 2026-10-28, user: old graphics cards and drivers are
        common, never fail silently): the reason in plain words + a way on. Use Built-in BASSMIDI = the MIDI out
        switched (and playing, when a play was asked for); Pick another MIDI out… = Settings → Sound; Cancel = as
        it was (the next Play tries Kestrel again). A soundfont it can't read = Built-in's "pick another" question."""
        from files.kestrel import drop_loaded, plain_reason
        from window.load_midi import ask_choice
        drop_loaded()  # (the graphics card's part made again at the next try; the soundfonts stay read)
        why = plain_reason(err)
        errors.note("Kestrel (GPU)", f"{'stopped' if during else 'failed'}: {err}")
        self.drop_read_ahead()  # (nothing read ahead for a Kestrel that doesn't play)
        path = getattr(err, "path", None)
        if why == "ks.why_font" and path and not during:
            text = tr("ks.why_font") + "\n" + os.path.basename(path) + "\n\n" + tr("synth.pick_other")
            if messagebox.askyesno(tr("ks.fail_title"), text, parent=self) and self.pick_soundfont(path) and play:
                self.start_play(keep)
            return
        head = tr("ks.fail_during" if during else "ks.fail_start")
        reason = tr(why) if why.startswith("ks.") else str(err)
        text = f"{head}\n\n{reason}\n\n" + tr("ks.fail_details", err=str(err))
        answer = ask_choice(self, tr("ks.fail_title"), text,
                            [("builtin", tr("ks.use_builtin")), ("pick", tr("ks.pick_out")),
                             ("cancel", tr("ks.cancel"))], icon="warning")
        if answer == "builtin":
            self.midi_device.set(BUILTIN)
            if play or during:
                self.start_play(keep)
        elif answer == "pick":
            from window.settings_window import open_settings
            open_settings(self, "sound")

    def ready_builtin(self):
        """Built-in BASSMIDI made ready before the first Space (user 2026-10-25: "the first play after the app is
        opened will always make the wonky sound", soundfonts on a hard disk): the soundfonts opened and the start
        of every piano sound loaded in the background (Synth.warm_piano: its live stream can't load them ahead,
        they came from the disk while playing), then the synth itself opened, so it already runs at the first
        note (a new one lost its first moments). After the start (READY_AFTER_MS), a soundfont change and picking
        Built-in BASSMIDI. Anything wrong = nothing done here: said when it plays."""
        if self.midi_device.get() != BUILTIN or self.out.handle is not None:
            return
        fonts = font_list(self.hz_preview)
        if not fonts or not all(os.path.isfile(p) for p in fonts):
            return
        try:
            if self.synth is None:
                self.synth = Synth()
        except SynthError:
            return
        synth, me = self.synth, object()
        self._ready_job = me

        def work():
            try:
                synth.set_fonts(fonts)
                synth.warm_piano()
            except SynthError:
                pass
        job = threading.Thread(target=work, daemon=True)
        job.start()

        def done():
            if job.is_alive():
                self.after(100, done)
                return
            if (self._ready_job is me and self.winfo_exists() and self.midi_device.get() == BUILTIN
                    and self.out.handle is None and not self.player.running and font_list(self.hz_preview) == fonts
                    and not synth.needs_open(fonts)):
                self.out.open(BUILTIN)  # (quick now: the soundfonts are open)
        self.after(100, done)

    def sync_builtin(self):
        """Settings → Sound shows Built-in BASSMIDI's / Kestrel's settings only while it's the MIDI out."""
        on = self.midi_device.get() in (BUILTIN, KESTREL)
        w = self.builtin_window  # (in Settings → Sound while that's open)
        if w is not None and w.winfo_exists():
            if on:
                w.refresh()  # (the soundfont's name)
            else:
                w.take_voices()
            w.master.winfo_toplevel().show_builtin()

    def ask_soundfont(self, parent, title, replace=None):
        """The soundfont file picker (shared soundfonts: Built-in BASSMIDI and the Hz bass preview), over parent with
        the title text key: the one picked takes the place of replace (a soundfont in the list that couldn't be
        read), else of the top one. True = one was picked."""
        cfg = self.hz_preview
        fonts = font_list(cfg)
        at = fonts.index(replace) if replace in fonts else 0
        path = filedialog.askopenfilename(
            parent=parent, title=tr(title),
            initialdir=os.path.dirname(fonts[at]) if fonts else None,
            filetypes=[(tr("hz.preview_fonts"), FONT_TYPES), (tr("hz.preview_all"), "*.*")])
        if not path:
            return False
        path = os.path.normpath(path)
        fonts[at:at + 1] = [path]
        self.set_soundfonts(fonts, again=True, added=[path], remake=False)
        return True

    def set_soundfonts(self, paths, again=False, added=(), remake=True):
        """The soundfonts list changed (font_list.FontList, a pick): saved, Built-in BASSMIDI opens them at the next
        note, the Hz bass preview makes its sound again (remake). again = open the ones still listed anew too
        (one was picked again); added = the new ones (an .sfz among them is checked)."""
        cfg = self.hz_preview
        paths = font_list({"font": "", "more_fonts": list(paths)})  # (each file once, whatever its spelling)
        cfg["font"], cfg["more_fonts"] = (paths[0] if paths else ""), paths[1:]
        self.schedule_autosave()
        self.soundfont_changed(again, added)  # (Settings → Sound shows the new names)
        if remake:
            self.remake_preview()

    def pick_soundfont(self, replace=None):
        """Pick a soundfont (shared by Built-in BASSMIDI and the Hz bass preview): in place of replace, else of the
        top one. True = one was picked."""
        if not self.ask_soundfont(self, "app.pick_soundfont_title", replace):
            return False
        self.remake_preview()
        return True

    def remake_preview(self):
        """The Hz bass preview makes its sound again with the soundfonts set now (none left = it goes off quietly,
        hunt 2026-10-25: Remove on the last one gave a "Pick a soundfont first" popup)."""
        hz = self.hz_window
        if hz is not None and not font_list(self.hz_preview):
            if hz.preview_on.get():
                hz.preview_on.set(False)
                hz.on_preview()
            return
        if hz is not None:  # (the preview makes its sound again with it)
            if hz.settings_window and hz.settings_window.winfo_exists():
                hz.settings_window.remake()
            elif hz.preview_on.get():
                err = hz.preview.remake()
                if err:
                    hz.preview_failed(err)

    def fresh_builtin(self):
        """Built-in BASSMIDI open with another soundfont than the shared one (one picked where nobody told this
        window, e.g. the Hz window asking when its file is gone): closed, so it opens the new one."""
        live = self.out.handle
        opened = getattr(live, "font_paths", None) or [p for p in [getattr(live, "font_path", None)] if p]
        if self.out.name in (BUILTIN, KESTREL) and live and list(opened) != font_list(self.hz_preview):
            self.out.close()
        elif self.out.name == KESTREL and live and not _same_opts(live.opts, self.kestrel_opts):
            self.out.close()  # (Settings → Sound changed one of Kestrel's: opened again with it)

    def soundfont_changed(self, again=True, added=None):
        """The shared soundfonts were changed (set_soundfonts): Built-in BASSMIDI opens them at the next note. again
        = opened anew even if open (picked again); added = the new ones (None: the top one) checked (.sfz)."""
        if self.out.name in (BUILTIN, KESTREL):
            self.stop_play()
            self.scrub_end()
            self.out.close()
        from files import kestrel  # (Kestrel's kept soundfonts: only ones no longer listed / picked again read anew)
        kestrel.stop_starts()  # (a start reading the old list: given up, never read again for nothing)
        kestrel.drop_loaded()
        kestrel.forget(keep=font_list(self.hz_preview))
        if again and added:
            kestrel.forget(paths=added)
        if getattr(self, "_scrub_side", None) is not None:  # (Kestrel's listening synth: opens them anew)
            self.scrub_end()
            self._scrub_side.close()
        self.sync_builtin()
        if self.synth is not None and again:
            self.synth.stale = True  # (picked again = opened again: samples put back since then are found)
        sfz = [p for p in ([self.hz_preview["font"]] if added is None else added) if p.lower().endswith(".sfz")]
        self.check_samples(sfz)
        self.ring_check()
        if READY_AFTER_MS is not None:
            self.after(300, self.ready_builtin)

    def ring_check(self, quiet=False):
        """Auto's CC run-on (hz_cc.auto_ring) for the soundfont picked: the time remembered for it, else measured in
        the background once the synth has it open (Hz preview, Built-in BASSMIDI); 3 seconds (hz_cc.RING) until
        then. quiet: only set (the settings being read at the start: the notes are made after)."""
        cfg = self.hz_preview
        fonts = font_list(cfg)
        sig = fonts_sig(fonts)
        if sig is not None and cfg.get("ring_font") == sig:
            self.auto_ring_is(cfg["ring"], quiet)
            return
        self.auto_ring_is(None, quiet)
        synth = getattr(self, "synth", None)  # (none yet while the settings are read at the start)
        if (sig is None or synth is None or not synth.font_paths
                or [os.path.normpath(p) for p in synth.font_paths] != [os.path.normpath(p) for p in fonts]
                or synth.stale or getattr(self, "_ring_job", None) == sig):
            return
        self._ring_job, result = sig, []

        def work():
            try:
                result.append(synth.ring_after(RING_KEYS, RING_MOST))
            except SynthError:
                pass
        job = threading.Thread(target=work, daemon=True)
        job.start()

        def done():
            if job.is_alive():
                self.after(200, done)
                return
            self._ring_job = None
            if result and fonts_sig(font_list(cfg)) == sig and self.winfo_exists():
                cfg["ring"], cfg["ring_font"] = math.ceil(result[0] * 10 - 1e-9) / 10, sig  # (up to a tenth)
                self.schedule_autosave()
                self.auto_ring_is(cfg["ring"])
        self.after(200, done)

    def auto_ring_is(self, seconds, quiet=False):
        """Auto's CC run-on is this now (None: not known): Hz basses sending CC on Auto made again."""
        if not set_auto_ring(seconds) or quiet:
            return
        if any(has_cc(sh.get("hz") or {}) and typed_ring(sh["hz"]) is None for sh in self.shapes):
            self.shapes_changed()  # (the Hz window's CC warning too)
        win = getattr(self, "hz_window", None)
        if win and win.settings_window:  # (only its run-on box: a whole sync mid-drag put the note dragged back)
            win.settings_window.show_ring(win.target())

    def check_samples(self, paths):
        """(The .sfz files just picked / added, one after another) their sound files checked in the background:
        missing ones or ones in a format the synth can't read = one warning each, as the synth opens such a
        soundfont without a word and those sounds are missing. A soundfont the synth refuses altogether: nothing
        here, its "couldn't be read" lists them. Only the newest pick's check speaks (another pick meanwhile, even
        of the same file, stops this one)."""
        self._sample_check = getattr(self, "_sample_check", 0) + 1
        paths = [p for p in paths if p and p.lower().endswith(".sfz")]
        if not paths:
            return
        try:
            if self.synth is None:
                self.synth = Synth()
        except SynthError:
            return  # (said when the synth is used)
        synth, result, me = self.synth, [], self._sample_check

        def work():
            for path in paths:
                try:
                    result.append((path, synth.silent_samples(path, cancel=lambda: self._sample_check != me)))
                except OSError:
                    pass  # (the soundfont itself unreadable: the synth says so when it opens it)
        job = threading.Thread(target=work, daemon=True)
        job.start()

        def done():
            if job.is_alive():
                self.after(200, done)
                return
            for path, (_, bad, refused) in result:
                if not bad or refused or self._sample_check != me or not self.winfo_exists():
                    continue
                try:
                    focus = self.focus_get()
                except KeyError:
                    focus = None  # (a dropdown's list open: Tk's own widget, unknown here)
                messagebox.showwarning(
                    tr("synth.silent_title"),
                    trn("synth.silent", len(bad), name=os.path.basename(path), n=f"{len(bad):,}")
                    + "\n\n" + silent_list(path, bad),
                    parent=focus.winfo_toplevel() if focus else self)
        self.after(200, done)

    def on_play_voices(self, left=False):
        e = self.voices_entry  # (in Settings → Sound)
        try:
            v = int(round(float(calc(self.voices_var.get()))))
            if not VOICES[0] <= v <= VOICES[1]:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            if e is not None and e.winfo_exists():
                bad(e)
            return
        if e is not None and e.winfo_exists():
            good(e)
        if str(v) != self.voices_var.get():
            self.voices_var.set(str(v))
        if v != self.play_voices:
            self.play_voices = v
            if self.overload.running:
                self.overload.set_limit(v)  # (its thread sends it: heard at once)
            elif self.out.name == BUILTIN and self.out.handle:
                self.out.handle.set_voices(v)
            if self.loaded_group is not None:
                self.loaded_group.set_voices(v)
            self.note_builtin()
            self.schedule_autosave()

    def on_loaded_synths(self):
        """Synths for a loaded MIDI typed (Settings → Sound): heard from the next Play."""
        e = self.synths_entry
        try:
            v = int(round(float(calc(self.synths_var.get()))))
            if not 1 <= v <= GROUP_MOST:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            if e is not None and e.winfo_exists():
                bad(e)
            return
        if e is not None and e.winfo_exists():
            good(e)
        if str(v) != self.synths_var.get():
            self.synths_var.set(str(v))
        if v != self.loaded_synths:
            self.loaded_synths = v
            self.schedule_autosave()

    def on_play_guard(self):
        self.overload.set_guard(self.play_guard.get())
        self.note_builtin()
        self.schedule_autosave()

    def on_play_limiter(self):
        """Heard at once while Built-in BASSMIDI is open."""
        if self.out.name == BUILTIN and self.out.handle:
            self.out.handle.set_limiter(self.play_limiter.get())
        if self.loaded_group is not None:
            self.loaded_group.set_limiter(self.play_limiter.get())
        self.note_builtin()
        self.schedule_autosave()

    def on_kestrel_opt(self, key):
        """A Kestrel (GPU) row in Settings → Sound changed (app.kestrel_vars[key]): Lower it by itself is heard at
        once, the others from the next Play (Kestrel opened again with them: fresh_builtin). Max voices typed wrong
        = back to the one in use."""
        from files.kestrel import BLOCKS, VOICES_RANGE, read_voices
        var = self.kestrel_vars[key]
        if key == "voices":
            e = getattr(self, "kestrel_voices_entry", None)
            try:
                try:
                    v = read_voices(var.get())  # ("300k", "1m")
                except ValueError:
                    v = int(round(float(calc(var.get()))))
                # (not past this graphics card's top once known: kestrel_voices_top)
                if not VOICES_RANGE[0] <= v <= min(VOICES_RANGE[1], getattr(self, "kestrel_top", None) or 1 << 62):
                    raise ValueError
            except (ValueError, ZeroDivisionError, OverflowError):  # (overflow: a huge pasted number, hunt 10-28)
                if e is not None and e.winfo_exists():
                    bad(e)
                return
            if e is not None and e.winfo_exists():
                good(e)
            var.set(str(v))
        elif key == "block":
            v = BLOCKS[max(0, min(len(BLOCKS) - 1, int(var.get())))]
        else:
            v = bool(var.get())
        if v == self.kestrel_opts[key]:
            return
        self.kestrel_opts[key] = v
        if key == "lower" and self.out.name == KESTREL and self.out.handle:
            self.out.handle.k.set_lower(v)
        elif self.out.name == KESTREL and self.player.running:
            self.kestrel_again()
        errors.note("Kestrel (GPU)", ", ".join(f"{k} {v}" for k, v in self.kestrel_opts.items()))
        self.schedule_autosave()

    def kestrel_again(self):
        """A Kestrel row changed while it plays (hunt 2026-10-28, user: they never took effect until the next Play):
        playing started again from the line, Kestrel opened with the new settings (its small window, a few seconds).
        A number box's label drag: once it's let go (else a restart at every step)."""
        job = getattr(self, "_again_job", None)
        if job is not None:
            self.after_cancel(job)
        self._again_job = None
        if getattr(self, "scrubbing", False) or getattr(self, "held_scrub", None) is not None:
            self._again_job = self.after(150, self.kestrel_again)
            return
        if self.out.name != KESTREL or not self.player.running:
            return
        live = self.out.handle
        if live is not None and _same_opts(live.opts, self.kestrel_opts):
            return
        self.stop_play()
        self.status.config(text=tr("ks.restarting"))
        self.start_play(keep=True)

    def take_kestrel_opts(self, opts):
        """The saved Kestrel settings (autosave window / settings.ini), each checked; wrong ones = defaults."""
        from files.kestrel import BLOCKS, KESTREL_DEFAULTS, VOICES_RANGE
        for k, d in KESTREL_DEFAULTS.items():
            v = opts.get(k)
            if k == "voices":
                ok = isinstance(v, int) and not isinstance(v, bool) and VOICES_RANGE[0] <= v <= VOICES_RANGE[1]
            elif k == "block":
                ok = v in BLOCKS and not isinstance(v, bool)
            else:
                ok = isinstance(v, bool)
            self.kestrel_opts[k] = v if ok else d
        for k, v in self.kestrel_opts.items():
            self.kestrel_vars[k].set(str(v) if k == "voices" else BLOCKS.index(v) if k == "block" else v)

    def note_builtin(self):
        """Built-in BASSMIDI's settings in the logs (errors.note), once it has been opened."""
        if self.synth is not None:
            errors.note("Built-in BASSMIDI", f"voice limit {self.play_voices}, lower it by itself "
                                             f"{'on' if self.play_guard.get() else 'off'}, limiter "
                                             f"{'on' if self.play_limiter.get() else 'off'}")

    def set_playhead(self, beat):
        self.playhead = max(0.0, beat)
        self.roll.show_playhead()
        self.schedule_autosave()

    def scrub(self, t_from, t_to):
        """Right-drag listening: sound the notes under the mouse (tick t_to) and any it just swept past."""
        self.fresh_builtin()
        out = self.scrub_out()
        if not out.handle:
            err = out.open(BUILTIN if self.midi_device.get() == KESTREL else self.midi_device.get())
            if err:
                self.status.config(text=err)
                return False
        lo, hi = min(t_from, t_to), max(t_from, t_to)
        now = {}  # (channel, key) -> (start of the latest note there, velocity)
        near = self.roll.visible_notes(lo, hi)
        near = near[near[:, 3] >= -1]  # (the track list's faint tracks: shown only, not listened to)
        s_, e_ = near[:, 0], near[:, 1]
        tuned = out.tuned  # (31-EDO: a row is one channel whatever its colour: kept by row)
        for s, e, p, v, slot, _ in near[((lo <= s_) & (s_ <= hi)) | ((s_ <= t_to) & (t_to < e_))].tolist():
            k = (0 if tuned else slot_track_channel(slot, self.picture_use10)[1], p)
            if k not in now or s > now[k][0]:
                now[k] = (s, v)
        held = self._scrub_held
        if self.loaded is not None and not tuned:  # (a loaded MIDI's notes drawn on top there; Spiderweb's own win
            from roll.roll_loaded import loud_on, scrub_notes, shown_only, shown_tracks  # a key both sound)
            order, faint = shown_tracks(self, self.loaded)
            if order is not None:  # (faint tracks: shown only, not listened to, like Spiderweb's)
                order = tuple(n for n in order if n not in faint)
            ppq_lm = self.loaded.ppq / self.ppq  # (its own ticks)
            got, rough = scrub_notes(self.loaded, order, lo * ppq_lm, hi * ppq_lm, t_to * ppq_lm, loud_on(self),
                                     shown_only(self, self.loaded))
            drums = out.name == BUILTIN  # (its channel 10 = drums, as playback plays it: Built-in BASSMIDI's
            for (ch, p), (s, v) in got.items():  # own drum channel; other MIDI outs' channel 10 is drums already)
                k = (LIVE_DRUMS_CODE if drums and ch == 9 else ch, p)
                if k not in now:  # (sounding without a start in this sweep: held on as it is, a new key sounds)
                    now[k] = (held.get(k, -1) if s is None else -2 - s, v)
            if rough:  # (too many notes to read in time: every key drawn, one loudness; user 2026-10-25)
                self.status.config(text=tr("app.scrub_rough"))
        if out.edo and out.name != BUILTIN and now and not held:
            self.status.config(text=tr("app.edo31_audio_builtin_only"))
        for k in [k for k in held if k not in now]:
            out.note(*k, 0, rows=True)
            del held[k]
        for (ch, p), (s, v) in now.items():
            if held.get((ch, p)) != s:  # a new note on this key (back-to-back spam notes each sound)
                if (ch, p) in held:
                    out.note(ch, p, 0, rows=True)
                out.note(ch, p, v, rows=True)
                held[(ch, p)] = s
        self.roll.scrub_t = t_to / self.ppq  # (a line of its own: the play line stays where it is, user)
        self.roll.draw_playhead()
        return True

    def scrub_out(self):
        """Right-drag listening's MIDI out: the one picked, but with Kestrel (GPU) picked Built-in BASSMIDI's own
        synth (user 2026-10-27: listening stays on BASSMIDI; Kestrel sounds ~0.1 s late)."""
        if self.midi_device.get() != KESTREL:
            return self.out
        side = getattr(self, "_scrub_side", None)
        if side is None:
            from files.playback import MidiOut
            side = self._scrub_side = MidiOut(self.make_live)
        side.edo = self.out.edo  # (31-EDO's sound works there)
        return side

    def scrub_end(self):
        out = self.scrub_out()
        for ch, p in self._scrub_held:
            out.note(ch, p, 0, rows=True)
        self._scrub_held.clear()
        if self.roll.scrub_t is not None:
            self.roll.scrub_t = None
            self.roll.draw_playhead()
