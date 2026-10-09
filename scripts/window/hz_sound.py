"""The Hz bass window's sound: the key held heard on MIDI out, and the preview (made in pieces, Space plays it, the
play line)."""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from files.lang import tr
from files.synth import FONT_TYPES
from notes.engine import slot_track_channel
from window import look
from window.hz_view import GREEN, GREY, ORANGE, PLAY_LINE


class HzSound:
    def sound(self, notes):
        """The notes held with the mouse sound on the MIDI-out device. notes = one key (held until let go), or the
        selected notes moved (user, 2026-10-01): played in time as in the song (one starting 1/4 beat later sounds
        1/4 beat later), each stopping at its end. When what they'd play changes, it starts again (None = let go:
        notes off)."""
        app = self.app
        if isinstance(notes, int):
            notes = [(notes, 0.0, None)]
        play = None
        if notes:
            t0 = min(t for _, t, _ in notes)
            play = tuple(sorted((k, round(t - t0, 6), ln if len(notes) > 1 else None) for k, t, ln in notes))
        if self.sounding is not None and self.sounding[1] == play:
            return
        self.hush()
        if not play:
            return
        if not app.out.handle and app.out.open(app.midi_device.get()):
            return  # (no device: silent)
        sh, ch, vel = self.target(), 0, app.defaults["vel0"]
        if sh is not None:  # the shape's own channel and velocity
            vel = sh.get("vel0", vel)
            mine = app.rendered[app.rendered[:, 5] == app.sel] if len(app.rendered) else ()
            if len(mine):
                ch = slot_track_channel(int(mine[0, 4]), app.picture_use10)[1]
        vel = max(1, min(127, int(vel)))
        try:
            ms = 60000.0 / app.read_project()[1]  # (one beat)
        except ValueError:
            ms = 500.0
        self.sounding = (ch, play)
        for k, t, ln in play:  # (sorted by key, then time: a note's end comes before the next one's start)
            if t:
                self.sound_jobs.append(self.after(int(round(t * ms)), self.sound_key, ch, k, vel))
            else:
                self.sound_key(ch, k, vel)
            if ln:
                self.sound_jobs.append(self.after(int(round((t + ln) * ms)), self.sound_key, ch, k, 0))

    def sound_key(self, ch, key, vel):
        """One note on (vel 0 = off) for sound()."""
        out = self.app.out
        if key in self.sound_on:
            out.note(ch, key, 0)
            self.sound_on.discard(key)
        if vel:
            out.note(ch, key, vel)
            self.sound_on.add(key)

    def hush(self):
        """Everything sound() started stops."""
        for job in self.sound_jobs:
            self.after_cancel(job)
        self.sound_jobs = []
        if self.sounding is not None:
            for k in self.sound_on:
                self.app.out.note(self.sounding[0], k, 0)
        self.sound_on, self.sounding = set(), None

    def preview_again(self):
        """At opening: the preview was on last time. On again if its soundfont is still there (nothing asked)."""
        if self.winfo_exists() and os.path.isfile(self.app.hz_preview["font"]):
            self.preview_on.set(True)
            self.on_preview()

    def on_preview(self):
        """The Preview toggle. The first time (no soundfont yet, or it's gone) it asks for one."""
        cfg = self.app.hz_preview
        if not self.preview_on.get():
            cfg["on"] = False
            self.preview.stop()
            self.live.stop()
        else:
            if not os.path.isfile(cfg["font"]) and not self.ask_font():
                self.preview_on.set(False)
                return
            err = self.preview.start()
            if err:
                self.preview_failed(err)
                return
            cfg["on"] = True
        self.app.schedule_autosave()
        self.redraw()
        self.canvas.focus_set()  # (so Space plays)

    def ask_font(self):
        """The soundfont picker: True when one was picked (it's the preview's now)."""
        cfg = self.app.hz_preview
        path = filedialog.askopenfilename(
            parent=self, title=tr("hz.preview_pick_font"),
            initialdir=os.path.dirname(cfg["font"]) if cfg["font"] else None,
            filetypes=[(tr("hz.preview_fonts"), FONT_TYPES), (tr("hz.preview_all"), "*.*")])
        if not path:
            return False
        cfg["font"] = os.path.normpath(path)
        self.app.schedule_autosave()
        return True

    def preview_failed(self, err, font=False):
        """The synth or the soundfont didn't work: the preview goes off and says why. font: the soundfont couldn't
        be opened: another one is offered (else the same file was tried again at every switch-on)."""
        self.preview_on.set(False)
        self.app.hz_preview["on"] = False
        self.preview.stop()
        self.live.stop()
        if not font:
            return messagebox.showerror(tr("hz.window_title"), err, parent=self)
        if messagebox.askyesno(tr("hz.window_title"), err + "\n\n" + tr("hz.preview_other_font"), icon="error",
                               parent=self) and self.winfo_exists() and self.ask_font():
            self.preview_on.set(True)
            self.on_preview()

    def on_space(self, e):
        """Space: the preview plays / stops (preview off: nothing; the main piano roll only plays from its own
        window). In a text box it's just a space (no_spaces takes it out when the box is left)."""
        if isinstance(e.widget, (tk.Entry, ttk.Entry)):  # (ttk.Combobox is one too)
            return None
        if not self.preview_on.get():
            return "break"
        if self.preview.playing():
            self.preview.stop_play()
        else:
            if self.app.player.running:
                self.app.stop_play()
            err = self.preview.play()
            if err:
                messagebox.showerror(tr("hz.window_title"), err, parent=self)
        self.draw_preview()
        return "break"

    def put_play_line(self, beat):
        """A click on the bar numbers (preview on): the play line goes there (playing: plays on from there)."""
        self.preview.put_line(max(0.0, beat))
        self.draw_preview()

    def draw_preview(self):
        """The grey over what isn't made yet, the play line, and the words next to the toggle. The canvas is only
        changed when what it shows changed."""
        c, p = self.canvas, self.preview
        on = self.preview_on.get()
        grey = p.grey() if on else []
        line = p.play_beat() if on and p.ev is not None else None
        if line is not None and p.playing() and not self.drag:  # (playing past the right edge: the next page; not
            # while the mouse holds a note / box: it would jump a page, user)
            x, w = self.x_of(line), c.winfo_width()
            if x > w - 6 * self.s or x < self.kb_w:
                self.t0 = line
                self.clamp_view()
                return self.redraw()
        shown = (tuple((round(self.x_of(a)), round(self.x_of(b))) for a, b in grey),
                 None if line is None else round(self.x_of(line)))
        if shown != p.shown:
            p.shown = shown
            c.delete("preview")
            w, h, rh = c.winfo_width(), c.winfo_height(), self.ruler_h
            for a, b in shown[0]:
                a, b = max(a, self.kb_w), min(b, w)
                if b > a:
                    c.create_rectangle(a, rh, b, h, fill=GREY, outline="", stipple="gray50", tags="preview")
            if shown[1] is not None and self.kb_w <= shown[1] <= w:
                c.create_line(shown[1], rh, shown[1], h, fill=PLAY_LINE, width=max(1, round(self.s)),
                              tags="preview")
            if c.find_withtag("frame"):
                c.tag_lower("preview", "frame")
        says, colour = "", look.INFO
        if on:
            vo = tr("hz.preview_voices", used=f"{p.voices_used:,}", limit=f"{self.app.hz_preview['voices']:,}")
            if p.loading():
                says, colour = tr("hz.preview_loading"), ORANGE
            elif p.just_loaded():
                says, colour = tr("hz.preview_loaded"), GREEN
            elif p.ev is None:
                says = tr("hz.preview_nothing")
            elif grey:
                says = tr("hz.preview_making", speed=f"{p.speed:.1f}" if p.speed else "…", voices=vo)
            else:
                says = tr("hz.preview_ready", voices=vo)
            if not self.live.active() and not p.loading():  # (the live keys' words: in the synth window only)
                self.live.warm()
        if (self.preview_says.cget("text"), str(self.preview_says.cget("foreground"))) != (says, colour):
            self.preview_says.config(text=says, foreground=colour)
        self.fx.draw_dots()  # (the moving dots on the effects' lines)
        if self.synth_win:
            self.synth_win.draw_live()
        if self.settings_window:
            self.settings_window.refresh()
