"""Mixed into App: Kestrel (GPU) starting + its Settings → Sound rows, and the soundfont picker (shared by
Built-in BASSMIDI, Kestrel and the Hz bass preview)."""

import math
import os
import threading
import time
from tkinter import filedialog, messagebox

from files import errors
from files.lang import tr, trn
from files.mathexpr import calc
from files.playback import BUILTIN, KESTREL
from files.synth import FONT_TYPES, Synth, SynthError, silent_list
from notes.hz_cc import set_auto_ring, typed_ring
from notes.hzbass import RING_MOST, has_cc
from window.app_playback import _same_opts, fonts_sig, kestrel_voices_top
from window.hz_preview import font_list
from window.widgets import bad, centre_over, good, read_number

RING_KEYS = (24, 36, 48, 60, 72, 84)  # keys whose ring is measured for Auto's CC run-on (the longest counts)
SLOW_START_S = 15.0  # Kestrel still starting after this: "Use Built-in BASSMIDI" / "Keep waiting" (user 2026-10-28:
# 60 s nobody waits for; its normal start 6-11 s with two soundfonts)
STUCK_START_S = 60.0  # ... after this, with no answer, the start is given up (the backstop)
SHOW_START_MS = 300  # Kestrel's "Starting" window only after this (soundfonts kept loaded: a start is ~20 ms)
AGAIN_WAIT_MS = 400  # (a Kestrel number box stepped while playing: started again once the steps stop this long)


class KestrelSetup:
    """Mixed into App."""

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
        centre_over(win, self)

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
            v = read_number(var.get(), lo, hi, whole=True)
            if v is None:
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
