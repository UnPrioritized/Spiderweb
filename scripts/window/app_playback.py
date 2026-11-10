"""Mixed into App: playing the notes (Space), right-drag listening and the MIDI out, Built-in BASSMIDI's
soundfont and settings."""

import collections
import math
import os
import threading
import time
from tkinter import filedialog, messagebox

import numpy as np

from files import errors
from files.lang import tr, trn
from files.mathexpr import calc
from files.ahead_play import FOLLOW_S
from files.playback import AHEAD, BUILTIN, EDO_LANE, KESTREL, MAESTRO, cc_list
from files.synth import FONT_TYPES, LIVE_DRUMS_CODE, LIVE_PORTS, RATE, Live, Synth, SynthError, silent_list
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
AGAIN_WAIT_MS = 400  # (a Kestrel number box stepped while playing: started again once the steps stop this long)
STUCK_PLAY_S = 5.0  # Kestrel made no new sound this long while playing: stopped and said (its driver stuck)
QUIET_S = 3.0  # (a question nobody asked for waits this long after the last key / click: quiet_now)
# the red "Overloaded" text kept steady (user 2026-10-29: it flashed): on this long after the last overload, its
# number changing at most this often, a loaded MIDI's notes left out averaged over this long
MAESTRO_LATE = 100.0  # (Maestro's render load: above = its sound falls behind)
MAESTRO_EVERY_S = 0.25  # (note-ons a second sent to Maestro: counted over this; more than its lowered limit =
# it's leaving notes out)
OVER_HOLD_S, OVER_EVERY_S, OVER_AVG_S = 4.0, 1.0, 4.0  # (tries faster leave a few notes out every 2-3 s)
EASY_S = 1.5  # Slow down: a loaded MIDI asking for almost no notes this long = its busy part is over, straight to 1x
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


def scrub_note(out, ch, p, vel):
    """Right-drag listening's note on / off: channel None = a piano roll row played in 31-EDO, -1 - n = a row played
    on channel n (31-EDO's channel groups: its octaves), else as it is (a loaded MIDI's notes stay as written then,
    like playback)."""
    if ch is None or ch < 0:
        out.note(0 if ch is None else -1 - ch, p, vel, rows=True)
    else:
        out.note(ch, p, vel)


def edo_place(ch):
    """A channel as right-drag listening tells 31-EDO rows apart: its place in its group (0-2: channels 1 and 4 sound
    a row alike, so one key; "Overlaps keep their pitch" puts overlaps there), channel 10 as itself (drums)."""
    return 9 if ch == 9 else int(EDO_LANE[ch & 15])


def readable(n):
    """n to 3 figures for the status line's red text (466,814 -> 467,000: it changes every moment, unreadable)."""
    n = int(round(n))
    return n if n < 1000 else int(round(n, 3 - len(str(n))))


TAP_VELOCITY = 100  # a piano key pressed while picking Glissando keys (tap_key)
AHEAD_IDLE_MS = 150  # Rendered ahead's making while stopped looks this often (M2)
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
        self.colour_edit_leave()  # (a colour step being edited: heard whole, its later steps back; PLAN.md stage C)
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("app.spiderweb_2"), str(e))
            return
        dev = self.midi_device.get()
        if dev in (BUILTIN, KESTREL, AHEAD) and not self.hz_preview["font"] and not self.pick_soundfont():
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
        if err is None and self.out.name == MAESTRO:  # (its delay read again; turned off / stopped = said)
            err = self.maestro_problem(self.out.handle.refresh())
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
        # (Rendered ahead: the loaded MIDI is made ahead with the rest, files/ahead_loaded.py; not played live too)
        # (31-EDO's Loaded MIDI tick: its notes go along with your own instead, play_apart)
        along = self.loaded is not None and self.out.name != AHEAD and not self.edo_loaded()
        self.player.along = along
        rendered, ccs, ports = self.play_parts(ppq, bpm)
        end, tempo = self.end_and_tempo(ppq, bpm, beats, self.playhead)
        self.player.ready = None
        self.player.stop()
        self.player._clock = None  # (the loaded MIDI's feeder waits for the new line, never the last play's)
        self.player.map = None  # (its reader waits for this play's tempo: Player.start sets it)
        self.piano_back()
        if along:  # (first: the play line waits until its first notes are read)
            self.start_loaded(int(round(self.playhead * ppq)))
        self.kestrel_parts()
        self.slow_down.reset()  # (each Play at the song's own speed: Player.start too)
        self._pace_seen = self._pace_seen_b = None  # (Kestrel's silence / BASSMIDI's notes thinned, seen)
        self._k_drops = None  # (Kestrel's notes left out by its own lowering: count, when it last grew)
        self._thin_hist = collections.deque()  # (a loaded MIDI's (time, notes left out, sent): notes_left_text)
        # (the last play's count of notes left out forgotten: the new play's starts at 0, read as "left out" it showed
        # a red Overloaded for ~5 s, hunt 2026-10-29); the steady text's hold too
        self._thin_seen, self._easy_since, self._ov_last = (0, 0.0), None, float("-inf")
        if self.out.name == AHEAD:  # (the sound made ahead: files/ahead_play.py; the player runs the line)
            err = self.ahead_play.start(rendered, ccs, ports, ppq, bpm, tempo, self.playhead)
            if err:
                messagebox.showerror(tr("app.spiderweb_2"), err, parent=self)
                return
            self.player.ready = self.ahead_play.gate  # (the line waits there until the first batch is made)
        self.player.start(rendered, ppq, bpm, self.playhead, end, self.picture_use10, ccs, ports, tempo)
        if self.out.name == BUILTIN:
            self.overload.start(self.out.handle, self.play_voices, self.play_guard.get(), keep)
        elif self.out.edo and not self.out.tuned:
            self.status.config(text=tr("app.edo31_audio_builtin_only"))
        if self.edo_loaded():  # (what the tuned play leaves out of the loaded MIDI, when it has any; user)
            left = self.edo_left_text()
            if left:
                self.status.config(text=left)
        elif self.edo_loaded_wanted():  # (too many notes to hold: as written, said; Rendered ahead too, hunt)
            self.status.config(text=tr("app.edo31_loaded_too_many", n=f"{self.edo_most():,}"))
        self.play_btn.config(text=tr("app.stop_space"))
        self.roll.show_playhead(start=True)
        if self.loaded is not None:  # (a redraw now: the loaded notes' exact pictures start reading ahead the way
            self.roll.request_redraw()  # it plays before the first page turn, roll_loaded._keep_ahead)
        self._play_job = self.after(15, self._play_tick)
        if self.out.name == AHEAD:  # (the first time: what the faint grey and the wait at Play are)
            self.after_idle(lambda: self.tips.show("ahead", wait=True))

    def edo_left_text(self):
        """The status line's "left out" text for 31-EDO's Loaded MIDI tick: the loaded MIDI's pitch bends, instrument
        changes and controller changes counted (only the kinds it has), "" when it has none."""
        from notes.loaded import edo_left_out
        parts = [trn(k, n, n=n) for k, n in zip(("n.pitch_bends", "n.instrument_changes", "n.controller_changes"),
                                                edo_left_out(self.loaded)) if n]
        return tr("app.edo31_loaded_left", list=tr("app.edo31_left_sep").join(parts)) if parts else ""

    def kestrel_stuck(self, live, now):
        """Kestrel made no new sound for STUCK_PLAY_S while the speakers kept taking it (silence handed out): its
        render is stuck (a hung graphics driver never reports an error; the play line would wait forever, user
        2026-10-29). The speakers not taking any (the sound device) doesn't count."""
        st = live.k.stats()
        seen = getattr(self, "_k_made", None)
        if seen is None or seen[0] is not live or st["rendered"] != seen[1] or st["handed"] == seen[2]:
            self._k_made = (live, st["rendered"], st["handed"], now)  # (making sound, or nothing taken: reset)
            return False
        if st["handed"] < seen[2]:
            self._k_made = None
            return False
        return now - seen[3] > STUCK_PLAY_S

    def maestro_problem(self, why):
        """Maestro's problem() as the message shown, or None. A Play refused closes it (opened again next time)."""
        if why is None:
            return None
        if self.player.thread is None:
            self.out.close()
        return tr("playback.maestro_disabled" if why == "disabled" else "playback.maestro_stopped")

    def kestrel_parts(self):
        """Kestrel: channel 10 a normal instrument on every port, as Built-in BASSMIDI plays Spiderweb's own shapes
        (it made them drums: a GM soundfont's snare, hunt 2026-10-28), except the ports a loaded MIDI uses: its
        channel 10 stays drums there. Each Play: the last play's choice never carries over. Maestro the same (hunt
        2026-11-01, user picked)."""
        if self.out.name not in (KESTREL, MAESTRO) or not self.out.handle:
            return
        lp = self.loaded_play
        files_ports = set((lp.tport % 16).tolist()) if lp is not None and lp.tport is not None else set()
        if self.out.name == MAESTRO:
            self.out.handle.drum_parts(files_ports)
            return
        from files.kestrel import PART_TYPE
        # (31-EDO: channel 10's drum notes go to port 1's channel 10, MidiOut.drum_code: drums there)
        kit = {0} if self.out.tuned and self.out.drum_code is not None else set()
        for port in range(16):
            self.out.handle.send(PART_TYPE | 9 << 8 | (1 if port in files_ports | kit else 0) << 16 | port << 24)
        # 31-EDO (13g): its channels bent for this play on the highest two ports the loaded MIDI leaves free (hunt
        # 2026-10-29: on its ports they played as drums / bent its notes), else the last ones put back
        from files.playback import edo_ports
        self.out.bend_edo(edo_ports(files_ports | kit) if self.out.tuned else False)

    def play_end(self, ppq, beats, start):
        """Where playing stops: one beat after the last note ends (a loaded MIDI's too; or after the play line if
        already past it), rounded up to the next beat line, whatever the time signature (user 2026-11-03: a
        quarter bar was half a beat in 2/4)."""
        out = self.outgoing()  # (+ wrapped kept notes)
        last = (int(out[:, 1].max()) if len(out) else 0) / ppq
        if self.loaded is not None:
            last = max(last, self.loaded.end / ppq)
        return float(math.ceil(max(last, start) + 1.0 - 1e-9))

    def end_and_tempo(self, ppq, bpm, beats, start, device=None):
        """(play end in beats, heard tempo). Rendered ahead: the sound decides the end (Play stops once the ring
        died away, AheadPlay.ended), so the line's own end is put past the longest ring (TAIL at the fastest tempo);
        a quarter bar cut the soundfont's release short (user 2026-11-03). device = the MIDI out to plan for (None:
        the open one)."""
        end = self.play_end(ppq, beats, start)
        tempo = heard_tempo(self.tempo, bpm, ppq, int(end * ppq) + 1, self.file_heard())
        if (device or self.out.name) == AHEAD:
            from files.ahead_pieces import TAIL
            fastest = max([bpm] + [60e6 / us for _, us in (tempo or ()) if us > 0])
            end += (TAIL + 2.0) * fastest / 60.0
            tempo = heard_tempo(self.tempo, bpm, ppq, int(end * ppq) + 1, self.file_heard())
        return end, tempo

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
        sinks, kind = None, {KDMAPI: "kdmapi", KESTREL: "kestrel", MAESTRO: "maestro"}.get(self.out.name, "device")
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
        PPQ: started again from there). Stopped: Rendered ahead's making while stopped builds its song again."""
        if not self.player.running:
            self.ahead_play.mark_dirty()
            self.kick_ahead()
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
        end, tempo = self.end_and_tempo(ppq, bpm, beats, self.player.position())
        self.player.update(rendered, bpm, end, self.picture_use10, ccs, ports, tempo)
        if self.out.name == AHEAD:
            self.ahead_play.changed(rendered, ccs, ports, ppq, bpm, tempo)

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
        if self.out.name == AHEAD:  # (the sound is made at the new tempo: made again)
            return self.play_changed()
        self.player.set_tempo(bpm, heard_tempo(self.tempo, bpm, ppq, end, self.file_heard()))

    def play_parts(self, ppq, bpm, device=None):
        """play_apart's, the CC lines as the player's messages (cc_list)."""
        rendered, msgs, ports = self.play_apart(ppq, bpm, device)
        return rendered, cc_list(msgs, self.picture_use10, ports) if msgs else [], ports

    def ahead_parts(self):
        """(rendered, ccs, ports, ppq, bpm, tempo) as Play would hand Rendered ahead from the play line, while
        stopped (M2: the MIDI out isn't open then); None while a project number is being typed."""
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError:
            return None
        rendered, ccs, ports = self.play_parts(ppq, bpm, AHEAD)
        _, tempo = self.end_and_tempo(ppq, bpm, beats, self.playhead, AHEAD)
        return rendered, ccs, ports, ppq, bpm, tempo

    def kick_ahead(self):
        """Making ahead while stopped (M2): its loop started when it's wanted and not running yet."""
        if getattr(self, "_ahead_idle_job", None) is None and self.ahead_play.idle_wanted():
            self._ahead_idle_job = self.after(AHEAD_IDLE_MS, self._ahead_idle)

    def _ahead_idle(self):
        self._ahead_idle_job = None
        if self.edit_shown:  # (a colour step being edited: made once its later steps are back, PLAN.md stage C;
            self._ahead_idle_job = self.after(AHEAD_IDLE_MS, self._ahead_idle)  # the whole result each change
            return                                                               # undid the speed-up)
        try:
            go = self.ahead_play.idle()
        except Exception as e:  # (never a dead loop with no word: logged, said once)
            errors.write_log(type(e), e, e.__traceback__, "Rendered ahead while stopped")
            return
        if not self.player.running:
            self.show_overload()  # (M3: "Audio buffer" while making stopped; gone once not wanted)
        if go:
            self._ahead_idle_job = self.after(AHEAD_IDLE_MS, self._ahead_idle)

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
        # (tracks muted in the track list: not played; + wrapped kept notes, App.outgoing)
        heard = self.track_list.unmuted(self.outgoing())
        edo = self.edo_loaded_rows(device)  # (31-EDO's Loaded MIDI tick: its notes as rows, beside your own)

        def with_edo(notes):
            if edo is None or not len(edo):
                return notes
            return np.concatenate([np.asarray(notes, np.int64).reshape(-1, edo.shape[1]), edo])
        if not per_shape and not any(pasted.values()):
            return with_edo(heard), {}, 0
        ports = LIVE_PORTS if (device or self.out.name) in (BUILTIN, KESTREL, MAESTRO, AHEAD) else 0
        if per_shape and self.edo_heard(device or self.out.name):
            # (31-EDO played tuned: the channel sets the octave, so a moved Hz bass jumped 2 / 4 octaves; its CC lines
            # never reach the tuned notes anyway (live play sends none, Rendered ahead's go on the plain channels))
            rendered, msgs = heard, {}
            for messages in per_shape.values():
                for s, events in messages.items():
                    msgs.setdefault(s, []).extend(events)
            for events in msgs.values():
                events.sort(key=lambda e: e[0])
        else:
            rendered, msgs = cc_apart(heard, per_shape, self.picture_use10, ports) if per_shape else (heard, {})
        for s, ms in pasted.items():
            if ms:
                msgs.setdefault(s, []).extend(ms)
                msgs[s].sort(key=lambda e: e[0])
        return with_edo(rendered), msgs, ports

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
            if why is None and self.kestrel_stuck(live, now):  # (its graphics driver hung: no error ever comes)
                why = "stuck"
            if why is not None:  # (silence with "Overloaded" flickering, every later Play too: stopped, said)
                self.stop_play()
                self.out.close()
                self.kestrel_failed(SynthError("playback.kestrel_failed", err=why), during=True)
                return
        if self.out.name == MAESTRO and self.out.handle is not None:  # (turned off / its engine stopped meanwhile:
            # stopped and said, never playing on silent)
            err = self.maestro_problem(self.out.handle.problem())
            if err:
                self.stop_play()
                self.out.close()  # (the next Play opens it again: works again once it's back)
                messagebox.showerror(tr("app.spiderweb_2"), err, parent=self)
                return
        if self.out.name == AHEAD and self.ahead_play.playing():
            err = self.ahead_play.tick()
            if err:  # (a piece couldn't be made: stopped and said, never playing on silent)
                self.stop_play()
                messagebox.showerror(tr("app.spiderweb_2"), err, parent=self)
                return
            if self.ahead_play.ended():  # (the ring died away: stops there, user 2026-11-03)
                self.stop_play()
                return
            sec = self.ahead_play.heard_sec()
            pmap = self.player.map
            if pmap is not None:
                # (the line = the heard sound; silence while waiting = the line held where the sound waits: the
                # frame handed out next. Held at "where it is now" it crept on a tick at a time, user 2026-11-03)
                now_sec = self.ahead_play.play_at / RATE if sec is None else sec
                if sec is None or abs(pmap.sec(self.playhead * self.player.ppq) - sec) > FOLLOW_S:
                    self.player.follow(now_sec)
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
        self._pace_lower = True  # (play_load: False = what's overloaded is something "Lower it by itself" can't help)
        load, behind, easy, lowered, effort = self.play_load()
        if on:
            can_lower = (self.kestrel_opts["lower"] if self.out.name == KESTREL else self.play_guard.get()) and \
                self._pace_lower
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
            # (its work, to climb back as fast as fits (user 2026-10-29: slow, Kestrel's fast) and to see whether
            # slowing helps. WHICH SIDE is busy (hunt 2026-10-29): own shapes' synth (its CPU: the measured climb, and
            # "Lower it by itself" can lower its voices) or a loaded MIDI's note budget (note-ons asked / budget: its
            # chords come in one go at any speed, so careful tries; "Lower it by itself" never touches those notes:
            # no hand-over, slowing goes on to 10 %)
            from files.slow_down import FIT_CPU
            asked = lp.asked if lp is not None and lp.sinks else None
            main = o.load if o.load is not None else 0.0
            own = asked is None or main >= asked
            effort = main if own else max(asked, lp.load or 0.0)
            self._pace_fit = FIT_CPU if own else None
            self._pace_lower = own
            # (the busy part over = straight back: only after EASY_S of the loaded MIDI asking for almost nothing,
            # none left out, own shapes' synth calm too (hunt 2026-10-29: one 0.1 s look between two chords said
            # "over", it swung 100 % <-> 54 % every beat; over a quiet intro own spam swung too))
            now = time.perf_counter()
            # (own shapes' CPU as it would be at 1x: calm at 73 % speed can be overloaded at 1x)
            quiet = asked is not None and asked < 0.05 and not behind and main / max(self.player.speed, 0.1) < FIT_CPU
            self._easy_since = (getattr(self, "_easy_since", None) or now) if quiet else None
            easy = quiet and now - self._easy_since >= EASY_S
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
        self.ahead_play.stop()  # (fades out; what's made is kept for the next Play)
        self.ahead_play.forget_closed()  # (... unless its MIDI was closed: New closes it first, hunt 2026-11-03)
        self.ahead_play.draw_grey()  # (its grey goes; making while stopped shows its own, M2)
        self.kick_ahead()
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
        # (Maestro: the loaded MIDI's tracks on their own ports, the shapes on the first LIVE_PORTS: all 16 ports)
        for port in range(16 if self.out.name == MAESTRO else 1):
            for ch in range(16):
                for m in (0xB0 | ch, 0xB0 | ch | 32 << 8, 0xC0 | ch):
                    self.out.send(m | port << 24)

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
                m = f"{top:,}"  # (of your Max voices, user 2026-10-29)
                text = (tr("app.overloaded_1", m=m) if cap == 1 else
                        tr("app.overloaded_n", n=f"{readable(cap):,}", m=m)) if 0 < cap < top else tr("app.overloaded")
        elif self.out.name == MAESTRO and self.out.handle and self.player.running and not text:
            # (more notes a second than its limit lets through, or its sound falling behind: notes left out, hunt
            # 2026-11-01; Spiderweb's own sending follows its limit, play_loaded. nps_limit: <= 0.2.3 = its load
            # limiter's lowered limit, 0 while not lowered; 0.2.4 = the limit set in Maestro, 0 = none)
            h, now = self.out.handle, time.perf_counter()
            seen = getattr(self, "_m_ons", None)
            if seen is None or seen[0] is not h or h.ons < seen[1]:
                seen = self._m_ons = (h, h.ons, now, 0.0)
            elif now - seen[2] >= MAESTRO_EVERY_S:  # (note-ons a second it was asked for, lately)
                seen = self._m_ons = (h, h.ons, now, (h.ons - seen[1]) / (now - seen[2]))
            st = h.stats()
            if st and (st["load"] > MAESTRO_LATE or st["nps_limit"] and seen[3] > st["nps_limit"]):
                text = tr("app.overloaded")
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
        buf = self.ahead_play.buffer_text() if self.midi_device.get() == AHEAD else ""
        if text != self.overload_text or slowed != self.slowed_text or buf != self.buffer_text:
            self.overload_text, self.slowed_text, self.buffer_text = text, slowed, buf
            self.overload_label.config(text=text)
            self.slowed_label.config(text=slowed)
            self.buffer_label.config(text=buf)
            self.place_status_marks()
            if self.builtin_window is not None:
                self.builtin_window.refresh()

    def place_status_marks(self):
        """The texts at the status line's right end, right to left: Rendered ahead's buffer, red "Overloaded",
        orange "Slowed to"; a long status message stops short of them with "…" instead of running under them. (The
        red off-range count sits after the note count instead: App.update_status.)"""
        free, first = 0, True
        for label, text in ((self.buffer_label, self.buffer_text), (self.overload_label, self.overload_text),
                            (self.slowed_label, self.slowed_text)):
            if text:
                label.place(relx=1.0, rely=0.5, anchor="e", x=-6 - free)
                free += label.winfo_reqwidth() + (18 if first and label is not self.buffer_label else 12)
                first = False
            else:
                label.place_forget()
        self.status.keep_free(free)

    def midi_device_picked(self):
        """MIDI out picked: the old one stops and closes (the same one picked again changes nothing)."""
        if self.midi_device.get() == self._device_was:
            return
        self._device_was = self.midi_device.get()
        self.stop_play()
        self.out.close()
        if self._device_was != AHEAD:  # (Rendered ahead's made sound: let go, memory)
            self.ahead_play.drop()
        if self._device_was != KESTREL:  # (only Kestrel uses it)
            self.drop_read_ahead()
            from files import kestrel
            if self._kestrel_job is not None:  # (a start on its way: its window closes at once, it gives up)
                self._kestrel_job["close"]()
            if self._device_was != AHEAD:  # (Rendered ahead's pieces use the same soundfonts on the card)
                kestrel.let_go()  # (its soundfonts kept loaded: freed, user 2026-10-28)
        self.sync_builtin()
        self.edo_warn()
        self.edo_loaded_tidy()
        if self.hz_window:
            self.hz_window.show_cc()  # (16 channels or plenty)
        self.schedule_autosave()
        if READY_AFTER_MS is not None:
            self.after(300, self.ready_builtin)
        self.read_ahead()
        self.ahead_play.mark_dirty()
        self.kick_ahead()

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
        loaded = kestrel.kept_for(fonts, self.kestrel_opts["card"])
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

    def card_news(self, job):
        """A start played on Kestrel's own pick instead of the picked graphics card: missing = the setting back to
        Automatic; failed = kept (tried again at the next Spiderweb start), said on the status line."""
        if job.get("card_gone") and self.kestrel_opts["card"] == job["card_gone"]:
            self.kestrel_card_gone(job["card_gone"])
        elif job.get("card_failed") and self.kestrel_opts["card"] == job["card_failed"]:
            self.status.config(text=tr("ks.card_failed", card=job["card_failed"].partition(":")[2]))

    def kestrel_card_gone(self, card):
        """The graphics card picked in Settings → Sound isn't on this PC now (another PC, a laptop's card turned off):
        Kestrel played on its own pick, the setting back to Automatic, said on the status line."""
        self.kestrel_opts["card"] = ""
        self.kestrel_vars["card"].set("")
        self.kestrel_top = None
        self.status.config(text=tr("ks.card_gone", card=card.partition(":")[2]))
        self.schedule_autosave()

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
                loaded = kestrel.loaded_for(fonts, opts["card"])
                if loaded.card_why == "missing":  # (the picked graphics card isn't there now: Kestrel's own pick)
                    job["card_gone"] = opts["card"]
                    opts["card"] = loaded.card
                elif loaded.card_why == "failed" and loaded.card_wanted == opts["card"]:  # (there, can't start: its
                    job["card_failed"] = opts["card"]  # own pick this run, the setting kept, said once)
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
                self.card_news(job)  # (said even so: else the next start sends the soundfonts again, hunt 10-29)
                return
            self._kestrel_job = None
            if win.winfo_exists():
                win.destroy()
            self.card_news(job)
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
        """Settings → Sound shows Built-in BASSMIDI's / Kestrel's / Rendered ahead's settings only while it's the MIDI
        out."""
        on = self.midi_device.get() in (BUILTIN, KESTREL, AHEAD)
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
        if not self.player.running:  # (Rendered ahead's making while stopped: with the new soundfonts)
            self.ahead_play.mark_dirty()
            self.kick_ahead()

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
        if self.out.name in (BUILTIN, KESTREL, AHEAD):  # (Rendered ahead too: its pieces read the old list back onto
            # the graphics card mid-song, a silent gap, hunt 2026-11-03)
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
            self.after(150, self.fresh_overload_text)  # ("of M" = the new limit: once the watcher has it)

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

    def on_ahead_seconds(self):
        """Rendered ahead's "Render ahead" box (Settings → Sound): whole seconds in render_ahead.SECONDS, typed wrong
        = red, Settings closed = back to the one in use (BuiltinPanel.take_voices)."""
        from files.render_ahead import read_seconds
        e = getattr(self, "ahead_entry", None)
        try:
            v = read_seconds(self.ahead_var.get())
        except ValueError:
            if e is not None and e.winfo_exists():
                bad(e)
            return
        if e is not None and e.winfo_exists():
            good(e)
        if str(v) != self.ahead_var.get():
            self.ahead_var.set(str(v))
        if v != self.ahead_seconds:
            self.ahead_seconds = v
            self.schedule_autosave()
            self.kick_ahead()

    def on_ahead_opt(self, key=None):
        """Rendered ahead's Engine / Slow down (key None) or one of its engine rows (app.ahead_vars[key]) changed:
        saved, used from the next Play. A number typed wrong = red, Settings closed = back to the one in use."""
        if key is not None:
            from files.render_ahead import BLOCKS, check_opt
            var, e = self.ahead_vars[key], self.ahead_entries.get(key)
            raw = var.get()
            try:
                v = check_opt(key, BLOCKS[raw] if key == "block" else raw)
            except (ValueError, IndexError):
                if e is not None and e.winfo_exists():
                    bad(e)
                return
            if e is not None and e.winfo_exists():
                good(e)
            if v == self.ahead_opts[key]:
                self.show_ahead_opts(key)  # ("1m" shown as the number)
                return
            self.ahead_opts[key] = v
            self.show_ahead_opts(key)
        self.schedule_autosave()
        if not self.player.running:  # (making while stopped: with the new settings, or stopped by the tick)
            self.ahead_play.mark_dirty()
            self.kick_ahead()

    def show_ahead_opts(self, only=None):
        """app.ahead_opts into the rows' vars (lists by place, the ceiling as "-1", not "-1.0")."""
        from files.render_ahead import BLOCKS, BOOLS
        for k, v in self.ahead_opts.items():
            if only is None or k == only:
                shown = BLOCKS.index(v) if k == "block" else v if k in ("interpolation", "limiter") + BOOLS \
                    else f"{v:g}" if k == "ceiling_db" else str(v)
                if self.ahead_vars[k].get() != shown:
                    self.ahead_vars[k].set(shown)

    def take_ahead_opts(self, saved):
        """Saved Rendered ahead engine settings (autosave window / settings.ini: "ahead_" + key), checked."""
        from files.render_ahead import take_opts
        self.ahead_opts = take_opts(saved)
        self.show_ahead_opts()

    def on_play_guard(self):
        self.overload.set_guard(self.play_guard.get())
        self.lower_changed()
        self.note_builtin()
        self.schedule_autosave()

    def lower_changed(self):
        """A "Lower it by itself" tick changed (either synth's): Slow down may go on to 10 % again (unticked after
        the hand-over it stayed where it was and stuttered), the red text follows at once (hunt 2026-10-29)."""
        self.slow_down.free_floor()
        self.fresh_overload_text()

    def fresh_overload_text(self):
        """The red text says what's true now, not held steady (a setting it shows just changed): from the next play
        tick (15 ms)."""
        self._ov_last = self._ov_changed = float("-inf")

    def on_play_limiter(self):
        """Heard at once while Built-in BASSMIDI is open."""
        if self.out.name == BUILTIN and self.out.handle:
            self.out.handle.set_limiter(self.play_limiter.get())
        if self.loaded_group is not None:
            self.loaded_group.set_limiter(self.play_limiter.get())
        self.note_builtin()
        self.schedule_autosave()

    def on_edo10_piano(self):
        """Settings → Sound's 31-EDO channel 10 tick: heard from now on (playing: started again from where it is,
        like the 31-EDO ticks; sounding notes end first, their offs where their ons went)."""
        playing = self.player.running
        if playing:
            self.stop_play()
        self.scrub_end()
        self.out.edo_drums = not self.edo10_piano.get()
        if playing:
            self.start_play(keep=True)
        else:
            self.play_changed()  # (Rendered ahead's sound made while stopped: old)
        self.schedule_autosave()

    def on_kestrel_opt(self, key):
        """A Kestrel (GPU) row in Settings → Sound changed (app.kestrel_vars[key]): Lower it by itself is heard at
        once, the others from the next Play (Kestrel opened again with them: fresh_builtin). Max voices typed wrong
        = back to the one in use."""
        from files.kestrel import BLOCKS, CHOICES, DECIMALS, RANGES, VOICES_RANGE, read_voices
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
        elif key in RANGES:  # (13g number boxes: whole numbers in their range, else back to the one in use)
            e = getattr(self, "kestrel_entries", {}).get(key)
            lo, hi = RANGES[key]
            try:
                v = int(round(float(calc(var.get()))))
                if not lo <= v <= hi:
                    raise ValueError
            except (ValueError, ZeroDivisionError, OverflowError):
                if e is not None and e.winfo_exists():
                    bad(e)
                return
            if e is not None and e.winfo_exists():
                good(e)
            var.set(str(v))
        elif key in DECIMALS:  # (Ceiling: tenths, minus taken: "-1", "-0.5"; else back to the one in use)
            e = getattr(self, "kestrel_entries", {}).get(key)
            lo, hi = DECIMALS[key]
            try:
                v = round(float(calc(var.get())), 1) + 0.0  # (+ 0.0: -0.0 shown as 0)
                if not lo <= v <= hi:
                    raise ValueError
            except (ValueError, ZeroDivisionError, OverflowError):
                if e is not None and e.winfo_exists():
                    bad(e)
                return
            if e is not None and e.winfo_exists():
                good(e)
            var.set(f"{v:g}")
        elif key in CHOICES:
            v = max(0, min(CHOICES[key] - 1, int(var.get())))
        elif key == "card":
            v = var.get()
        else:
            v = bool(var.get())
        if v == self.kestrel_opts[key]:
            return
        self.kestrel_opts[key] = v
        if key == "card":  # (the old card's most voices doesn't hold for this one: learned again, hunt 2026-10-29)
            self.kestrel_top = None
        if key == "lower":
            self.lower_changed()
        if key == "lower" and self.out.name == KESTREL and self.out.handle:
            self.out.handle.k.set_lower(v)
        elif self.out.name == KESTREL and self.player.running:
            self.kestrel_again(wait=key == "voices" or key in RANGES or key in DECIMALS)
        errors.note("Kestrel (GPU)", ", ".join(f"{k} {v}" for k, v in self.kestrel_opts.items()))
        self.schedule_autosave()

    def kestrel_again(self, wait=False):
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
        if wait:  # (a number box's Up / Down / wheel step: once the steps stop, hunt 2026-10-29)
            self._again_job = self.after(AGAIN_WAIT_MS, self.kestrel_again)
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
        from files.kestrel import BLOCKS, CHOICES, DECIMALS, KESTREL_DEFAULTS, RANGES, VOICES_RANGE
        for k, d in KESTREL_DEFAULTS.items():
            v = opts.get(k)
            if k == "limiter" and isinstance(v, bool):  # (the old tick, before 1.4.0: on = Balanced now, off stays off)
                v = 2 if v else 0
            whole = isinstance(v, int) and not isinstance(v, bool)
            if k in DECIMALS:
                ok = isinstance(v, (int, float)) and not isinstance(v, bool) and DECIMALS[k][0] <= v <= DECIMALS[k][1]
                v = round(float(v), 1) + 0.0 if ok else v
            elif k == "voices":
                ok = whole and VOICES_RANGE[0] <= v <= VOICES_RANGE[1]
            elif k == "block":
                ok = v in BLOCKS and not isinstance(v, bool)
            elif k in RANGES:
                ok = whole and RANGES[k][0] <= v <= RANGES[k][1]
            elif k in CHOICES:
                ok = whole and 0 <= v < CHOICES[k]
            elif k == "card":
                ok = isinstance(v, str) and len(v) < 300
            else:
                ok = isinstance(v, bool)
            self.kestrel_opts[k] = v if ok else d
        for k, v in self.kestrel_opts.items():
            self.kestrel_vars[k].set(str(v) if k == "voices" or k in RANGES else f"{v:g}" if k in DECIMALS
                                     else BLOCKS.index(v) if k == "block" else v)

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
            err = out.open(self.scrub_device())
            if err:
                self.status.config(text=err)
                return False
        lo, hi = min(t_from, t_to), max(t_from, t_to)
        now = {}  # (channel, key) -> (start of the latest note there, velocity); channel -1 - n = a 31-EDO row on n
        from roll.roll_draw import TRACK_FADE
        seen = TRACK_FADE[0] > 0  # (like Domino: every note shown is heard, the track list's faint tracks too;
        near = self.roll.visible_notes(lo, hi)  # Faint 0 % = not shown, not heard; user 2026-11-01)
        if not seen:
            near = near[near[:, 3] >= -1]
        wrapped = self.scrub_wrapped()  # (Wrap on: the kept notes where they land, heard like Play hears them)
        if len(wrapped):
            ws, we = wrapped[:, 0], wrapped[:, 1]
            wrapped = wrapped[((lo <= ws) & (ws <= hi)) | ((ws <= t_to) & (t_to < we))]
            if len(wrapped):
                near = np.concatenate([np.asarray(near, np.int64), np.asarray(wrapped, np.int64)])
        s_, e_ = near[:, 0], near[:, 1]
        tuned = out.tuned  # (31-EDO: a row + its channel's place in its group: scrub_note)
        for s, e, p, v, slot, _ in near[((lo <= s_) & (s_ <= hi)) | ((s_ <= t_to) & (t_to < e_))].tolist():
            if v <= -2:  # (a faint track's copy: -2 - its velocity)
                v = -2 - v
            ch = slot_track_channel(slot, self.picture_use10)[1]
            k = (-1 - edo_place(ch) if tuned else ch, p)
            if k not in now or s > now[k][0]:
                now[k] = (s, v)
        held = self._scrub_held
        # (a loaded MIDI's notes drawn on top there; Spiderweb's own win a key both sound; 31-EDO: its notes played
        # as written, like playback plays them, Spiderweb's own rows tuned beside them; Loaded MIDI ticked: tuned too)
        if self.loaded is not None:
            from roll.roll_loaded import loud_on, scrub_notes, shown_only, shown_tracks
            order, faint = shown_tracks(self, self.loaded)
            if order is not None and not seen:  # (faint tracks at 0 %: not shown, not heard)
                order = tuple(n for n in order if n not in faint)
            ppq_lm = self.loaded.ppq / self.ppq  # (its own ticks)
            got, rough = scrub_notes(self.loaded, order, lo * ppq_lm, hi * ppq_lm, t_to * ppq_lm, loud_on(self),
                                     shown_only(self, self.loaded))
            drums = out.name == BUILTIN  # (its channel 10 = drums, as playback plays it: Built-in BASSMIDI's
            edo = tuned and self.edo_loaded()  # own drum channel; other MIDI outs' channel 10 is drums already;
            for (ch, p), (s, v) in got.items():  # 31-EDO's Loaded MIDI tick: rows, like playback plays them)
                k = (-1 - edo_place(ch), p) if edo else (LIVE_DRUMS_CODE if drums and ch == 9 else ch, p)
                if k not in now:  # (sounding without a start in this sweep: held on as it is, a new key sounds)
                    now[k] = (held.get(k, -1) if s is None else -2 - s, v)
            if rough:  # (too many notes to read in time: every key drawn, one loudness; user 2026-10-25)
                self.status.config(text=tr("app.scrub_rough"))
        if out.edo and out.name != BUILTIN and now and not held:
            self.status.config(text=tr("app.edo31_audio_builtin_only"))
        for k in [k for k in held if k not in now]:
            scrub_note(out, *k, 0)
            del held[k]
        for (ch, p), (s, v) in now.items():
            if held.get((ch, p)) != s:  # a new note on this key (back-to-back spam notes each sound)
                if (ch, p) in held:
                    scrub_note(out, ch, p, 0)
                scrub_note(out, ch, p, v)
                held[(ch, p)] = s
        self.roll.scrub_t = t_to / self.ppq  # (a line of its own: the play line stays where it is, user)
        self.roll.draw_playhead()
        return True

    def tap_key(self, key):
        """A piano key pressed while picking Glissando keys: it sounds until the button is let go (tap_end; user
        11-02: as long as it's held) on the MIDI out right-drag listening uses."""
        self.tap_end()
        self.fresh_builtin()
        out = self.scrub_out()
        if not out.handle:
            err = out.open(self.scrub_device())
            if err:
                self.status.config(text=err)
                return
        ch = None if out.tuned else 0
        scrub_note(out, ch, key, TAP_VELOCITY)
        self._tap = (out, ch, key)

    def tap_end(self):
        """The held piano key's note-off (tap_key), if one sounds."""
        tap, self._tap = getattr(self, "_tap", None), None
        if tap and tap[0].handle:  # (closed meanwhile: nothing is sounding)
            scrub_note(*tap, 0)

    def scrub_device(self):
        """The MIDI out listening opens: Built-in BASSMIDI for Kestrel (sounds ~0.1 s late) and Rendered ahead (can't
        make what the mouse decides ahead; user 2026-11-03), else the one picked."""
        dev = self.midi_device.get()
        return BUILTIN if dev in (KESTREL, AHEAD) else dev

    def scrub_out(self):
        """Right-drag listening's MIDI out (piano keys, the Hz bass window's keys too): the one picked, but with
        Kestrel (GPU) or Rendered ahead picked Built-in BASSMIDI's own synth (user 2026-10-27: listening stays on
        BASSMIDI; Kestrel sounds ~0.1 s late; 2026-11-03: Rendered ahead too, never a second copy of the soundfont:
        make_live opens it on the app's one Synth, the one Rendered ahead's BASSMIDI engine uses)."""
        if self.midi_device.get() not in (KESTREL, AHEAD):
            return self.out
        side = getattr(self, "_scrub_side", None)
        if side is None:
            from files.playback import MidiOut
            side = self._scrub_side = MidiOut(self.make_live)
        side.edo = self.out.edo  # (31-EDO's sound works there)
        side.edo_drums = self.out.edo_drums
        return side

    def scrub_end(self):
        out = self.scrub_out()
        for ch, p in self._scrub_held:
            scrub_note(out, ch, p, 0)
        self._scrub_held.clear()
        if self.roll.scrub_t is not None:
            self.roll.scrub_t = None
            self.roll.draw_playhead()
