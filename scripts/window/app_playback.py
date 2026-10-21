"""Mixed into App: playing the notes (Space), right-drag listening and the MIDI out, Built-in BASSMIDI's
soundfont and settings."""

import math
import os
import threading
from tkinter import filedialog, messagebox

from files import errors
from files.lang import tr, trn
from files.mathexpr import calc
from files.playback import BUILTIN, cc_list
from files.synth import FONT_TYPES, LIVE_PORTS, Live, Synth, SynthError, silent_list
from notes.engine import slot_track_channel
from notes.hz_cc import cc_apart
from notes.hzbass import has_cc
from window.hz_preview import VOICES
from window.widgets import bad, good


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
        if dev == BUILTIN and not self.hz_preview["font"] and not self.pick_soundfont():
            return
        self.fresh_builtin()
        err = self.out.open(dev)
        while err and dev == BUILTIN and self.font_failed:  # (moved, gone or broken: offer another, hunt 2026-10-09)
            if not messagebox.askyesno(tr("app.spiderweb_2"), err + "\n\n" + tr("synth.pick_other"), parent=self) \
                    or not self.pick_soundfont():
                return
            err = self.out.open(dev)
        if err:
            messagebox.showerror(tr("app.spiderweb_2"), err)
            return
        if self.hz_window:  # (one thing plays at a time, user)
            self.hz_window.preview.stop_play()
            self.hz_window.draw_preview()
        # (Overlapping shapes keep their colours: shapes overlapping on a key share its channel, render keep)
        self.player.shared = self.rows_apart()
        rendered, ccs, ports = self.play_parts(ppq, bpm)
        self.player.start(rendered, ppq, bpm, self.playhead, self.play_end(ppq, beats, self.playhead),
                          self.picture_use10, ccs, ports)
        if self.out.name == BUILTIN:
            self.overload.start(self.out.handle, self.play_voices, self.play_guard.get(), keep)
        elif self.out.edo:
            self.status.config(text=tr("app.edo31_audio_builtin_only"))
        self.play_btn.config(text=tr("app.stop_space"))
        self.roll.show_playhead(start=True)
        self._play_job = self.after(15, self._play_tick)

    def play_end(self, ppq, beats, start):
        """Where playing stops: a quarter bar after the last note ends (or after the play line if already past
        it), rounded up to the next quarter-bar line."""
        quarter = beats / 4
        last = (int(self.rendered[:, 1].max()) if len(self.rendered) else 0) / ppq
        return math.ceil((max(last, start) + quarter) / quarter - 1e-9) * quarter

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
        self.player.update(rendered, bpm, self.play_end(ppq, beats, self.player.position()), self.picture_use10, ccs,
                           ports)

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
        if not per_shape:
            return self.rendered, {}, 0
        ports = LIVE_PORTS if (device or self.out.name) == BUILTIN else 0
        rendered, msgs = cc_apart(self.rendered, per_shape, self.picture_use10, ports)
        return rendered, msgs, ports

    def _play_tick(self):
        self._play_job = None
        self.playhead = self.player.position()
        if not self.player.running:
            self.stop_play()
            return
        self.roll.show_playhead()
        self.show_overload()
        self._play_job = self.after(15, self._play_tick)

    def stop_play(self):
        if self._play_job:
            self.after_cancel(self._play_job)
            self._play_job = None
        if self.player.running or self.player.thread is not None:
            self.playhead = self.player.position()
            self.player.stop()
            self.roll.show_playhead()
            self.schedule_autosave()
        self.overload.stop()
        self.show_overload()
        self.play_btn.config(text=tr("app.play_space"))

    def show_overload(self):
        """The red text at the status line's right end (and in Settings → Sound): "Overloaded", with the
        voices playing when the limit was lowered by itself."""
        text = self.overload.text()
        if text != self.overload_text:
            self.overload_text = text
            self.overload_label.config(text=text)
            if text:
                self.overload_label.place(relx=1.0, rely=0.5, anchor="e", x=-6)
            else:
                self.overload_label.place_forget()
            # (a long status message stops short of it with "…" instead of running under it)
            self.status.keep_free(self.overload_label.winfo_reqwidth() + 18 if text else 0)
            if self.builtin_window is not None:
                self.builtin_window.refresh()

    def midi_device_picked(self):
        """MIDI out picked: the old one stops and closes (the same one picked again changes nothing)."""
        if self.midi_device.get() == self._device_was:
            return
        self._device_was = self.midi_device.get()
        self.stop_play()
        self.out.close()
        self.sync_builtin()
        self.edo_warn()
        if self.hz_window:
            self.hz_window.show_cc()  # (16 channels or plenty)
        self.schedule_autosave()

    def make_live(self):
        """(MidiOut.open) The built-in synth started for playing, with the soundfont (the Hz bass preview's) and
        voice limit set now. Raises SynthError (font_failed = True when the soundfont couldn't be read)."""
        self.font_failed = False
        font = self.hz_preview["font"]
        if not font:
            raise SynthError("synth.no_font")
        self.busy(tr("app.opening_soundfont"))  # (a big soundfont takes a moment)
        try:
            if self.synth is None:
                self.synth = Synth()
            if self.synth.needs_open(font):
                try:
                    self.synth.set_font(font)
                except SynthError:
                    self.font_failed = True
                    raise
            live = Live(self.synth, self.play_voices, limiter=self.play_limiter.get())
            self.note_builtin()
            return live
        finally:
            self.busy(None)

    def sync_builtin(self):
        """Settings → Sound shows Built-in BASSMIDI's settings only while it's the MIDI out."""
        on = self.midi_device.get() == BUILTIN
        w = self.builtin_window  # (in Settings → Sound while that's open)
        if w is not None and w.winfo_exists():
            if on:
                w.refresh()  # (the soundfont's name)
            else:
                w.take_voices()
            w.master.winfo_toplevel().show_builtin()

    def ask_soundfont(self, parent, title):
        """The soundfont file picker (shared soundfont: Built-in BASSMIDI and the Hz bass preview), over parent with
        the title text key. True = one was picked (it's the shared one now)."""
        cfg = self.hz_preview
        path = filedialog.askopenfilename(
            parent=parent, title=tr(title),
            initialdir=os.path.dirname(cfg["font"]) if cfg["font"] else None,
            filetypes=[(tr("hz.preview_fonts"), FONT_TYPES), (tr("hz.preview_all"), "*.*")])
        if not path:
            return False
        cfg["font"] = os.path.normpath(path)
        self.schedule_autosave()
        self.soundfont_changed()  # (Settings → Sound shows the new name)
        return True

    def pick_soundfont(self):
        """Pick the soundfont (shared by Built-in BASSMIDI and the Hz bass preview). True = one was picked."""
        if not self.ask_soundfont(self, "app.pick_soundfont_title"):
            return False
        hz = self.hz_window
        if hz is not None:  # (the preview makes its sound again with it)
            if hz.settings_window and hz.settings_window.winfo_exists():
                hz.settings_window.remake()
            elif hz.preview_on.get():
                err = hz.preview.remake()
                if err:
                    hz.preview_failed(err)
        return True

    def fresh_builtin(self):
        """Built-in BASSMIDI open with another soundfont than the shared one (one picked where nobody told this
        window, e.g. the Hz window asking when its file is gone): closed, so it opens the new one."""
        live = self.out.handle
        if self.out.name == BUILTIN and live and live.font_path != self.hz_preview["font"]:
            self.out.close()

    def soundfont_changed(self):
        """The shared soundfont was changed (here or in the Hz bass preview's settings): Built-in BASSMIDI opens it
        at the next note."""
        if self.out.name == BUILTIN:
            self.stop_play()
            self.scrub_end()
            self.out.close()
        self.sync_builtin()
        if self.synth is not None:
            self.synth.stale = True  # (picked again = opened again: samples put back since then are found)
        self.check_samples(self.hz_preview["font"])

    def check_samples(self, path):
        """(An .sfz just picked) its sound files checked in the background: missing ones or ones in a format the
        synth can't read = one warning, as the synth opens such a soundfont without a word and those sounds are
        missing. A soundfont the synth refuses altogether: nothing here, its "couldn't be read" lists them. Only
        the newest pick's check speaks (another pick meanwhile, even of the same file, stops this one)."""
        self._sample_check = getattr(self, "_sample_check", 0) + 1
        if not path or not path.lower().endswith(".sfz"):
            return
        try:
            if self.synth is None:
                self.synth = Synth()
        except SynthError:
            return  # (said when the synth is used)
        synth, result, me = self.synth, [], self._sample_check

        def work():
            try:
                result.append(synth.silent_samples(path, cancel=lambda: self._sample_check != me))
            except OSError:
                pass  # (the soundfont itself unreadable: the synth says so when it opens it)
        job = threading.Thread(target=work, daemon=True)
        job.start()

        def done():
            if job.is_alive():
                self.after(200, done)
            elif result and result[0][1] and not result[0][2] and self._sample_check == me and self.winfo_exists():
                try:
                    focus = self.focus_get()
                except KeyError:
                    focus = None  # (a dropdown's list open: Tk's own widget, unknown here)
                messagebox.showwarning(
                    tr("synth.silent_title"),
                    trn("synth.silent", len(result[0][1]), name=os.path.basename(path), n=f"{len(result[0][1]):,}")
                    + "\n\n" + silent_list(path, result[0][1]),
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
            self.note_builtin()
            self.schedule_autosave()

    def on_play_guard(self):
        self.overload.set_guard(self.play_guard.get())
        self.note_builtin()
        self.schedule_autosave()

    def on_play_limiter(self):
        """Heard at once while Built-in BASSMIDI is open."""
        if self.out.name == BUILTIN and self.out.handle:
            self.out.handle.set_limiter(self.play_limiter.get())
        self.note_builtin()
        self.schedule_autosave()

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
        if not self.out.handle:
            err = self.out.open(self.midi_device.get())
            if err:
                self.status.config(text=err)
                return False
        lo, hi = min(t_from, t_to), max(t_from, t_to)
        now = {}  # (channel, key) -> (start of the latest note there, velocity)
        near = self.roll.visible_notes(lo, hi)
        s_, e_ = near[:, 0], near[:, 1]
        tuned = self.out.tuned  # (31-EDO: a row is one channel whatever its colour: kept by row)
        for s, e, p, v, slot, _ in near[((lo <= s_) & (s_ <= hi)) | ((s_ <= t_to) & (t_to < e_))].tolist():
            k = (0 if tuned else slot_track_channel(slot, self.picture_use10)[1], p)
            if k not in now or s > now[k][0]:
                now[k] = (s, v)
        held = self._scrub_held
        if self.out.edo and self.out.name != BUILTIN and now and not held:
            self.status.config(text=tr("app.edo31_audio_builtin_only"))
        for k in [k for k in held if k not in now]:
            self.out.note(*k, 0, rows=True)
            del held[k]
        for (ch, p), (s, v) in now.items():
            if held.get((ch, p)) != s:  # a new note on this key (back-to-back spam notes each sound)
                if (ch, p) in held:
                    self.out.note(ch, p, 0, rows=True)
                self.out.note(ch, p, v, rows=True)
                held[(ch, p)] = s
        self.roll.scrub_t = t_to / self.ppq  # (a line of its own: the play line stays where it is, user)
        self.roll.draw_playhead()
        return True

    def scrub_end(self):
        for ch, p in self._scrub_held:
            self.out.note(ch, p, 0, rows=True)
        self._scrub_held.clear()
        if self.roll.scrub_t is not None:
            self.roll.scrub_t = None
            self.roll.draw_playhead()
