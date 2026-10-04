"""The Hz bass window's "Preview settings…" window (hz_preview.py): soundfont, voice limit, no reverb / chorus,
volume, the live keys' memory (hz_live.py), and what the preview is doing (voices in use against the limit, how
fast the sound is made, the live keys' recordings kept), updated while it works so a changed voice limit shows its
effect at once. These are program settings, not the project's: no undo, kept with the window settings in the
autosave."""

import os
import tkinter as tk
from tkinter import filedialog, ttk

from files.lang import tr
from files.mathexpr import calc
from window.hz_preview import LIVE_MB, VOICES, WORKERS
from window.widgets import Scrub, Tooltip

ORANGE = "#c06000"


def open_preview_settings(win):
    """win = the Hz bass window."""
    if win.settings_window and win.settings_window.winfo_exists():
        win.settings_window.lift()
    else:
        win.settings_window = PreviewSettings(win)
    win.settings_window.refresh()


class PreviewSettings(tk.Toplevel):
    def __init__(self, win):
        super().__init__(win)
        self.win, self.app = win, win.app
        self.title(tr("ps.title"))
        self.transient(win)
        self.resizable(False, False)
        s = self.app.scale
        cfg = self.app.hz_preview
        box = ttk.Frame(self, padding=(12, 10, 12, 12))
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(box, text=tr("ps.font")).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        self.font_name = ttk.Label(box, text="", wraplength=round(250 * s))
        self.font_name.grid(row=r, column=1, sticky="w", pady=3)
        b = ttk.Button(box, text=tr("ps.change"), command=self.pick_font)
        b.grid(row=r, column=2, sticky="w", padx=(8, 0), pady=3)
        Tooltip(b, tr("ps.change_tip"))
        r += 1

        lb = ttk.Label(box, text=tr("ps.voices"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        self.voices = tk.StringVar(value=str(cfg["voices"]))
        e = self.voices_entry = ttk.Entry(box, textvariable=self.voices, width=8)
        e.grid(row=r, column=1, sticky="w", pady=3)
        e.bind("<Return>", lambda ev: (self.on_voices(), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_voices())
        Scrub(self.app, [(e, self.voices, self.on_voices)], (50, 500, 1), *VOICES, label=lb)
        Tooltip(e, tr("ps.voices_tip"))
        Tooltip(lb, tr("ps.voices_tip"))
        r += 1

        self.nofx = tk.BooleanVar(value=cfg["nofx"])
        c = ttk.Checkbutton(box, text=tr("ps.nofx"), variable=self.nofx, command=self.on_nofx, takefocus=False)
        c.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(c, tr("ps.nofx_tip"))
        r += 1

        ttk.Label(box, text=tr("ps.volume")).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        self.volume = tk.DoubleVar(value=cfg["volume"] * 100)
        sc = ttk.Scale(box, from_=0, to=100, variable=self.volume, command=self.on_volume,
                       length=round(200 * s))
        sc.grid(row=r, column=1, sticky="w", pady=3)
        sc.bind("<ButtonRelease-1>", lambda e: self.app.schedule_autosave())
        self.volume_says = ttk.Label(box, text="", width=6)
        self.volume_says.grid(row=r, column=2, sticky="w", padx=(8, 0))
        r += 1

        lb = ttk.Label(box, text=tr("ps.live_mb"))
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        self.live_mb = tk.StringVar(value=str(cfg["live_mb"]))
        e = self.live_entry = ttk.Entry(box, textvariable=self.live_mb, width=8)
        e.grid(row=r, column=1, sticky="w", pady=3)
        e.bind("<Return>", lambda ev: (self.on_live_mb(), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_live_mb())
        Scrub(self.app, [(e, self.live_mb, self.on_live_mb)], (100, 1000, 10), *LIVE_MB, label=lb)
        Tooltip(e, tr("ps.live_mb_tip"))
        Tooltip(lb, tr("ps.live_mb_tip"))
        r += 1

        ttk.Separator(box).grid(row=r, column=0, columnspan=3, sticky="ew", pady=8)
        r += 1
        self.used = ttk.Label(box, text="")
        self.used.grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.speed = ttk.Label(box, text="")
        self.speed.grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 0))
        r += 1
        self.live_used = ttk.Label(box, text="")
        self.live_used.grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 0))
        r += 1
        ttk.Label(box, text=tr("ps.limiter"), foreground="#666", wraplength=round(380 * s)).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<space>", lambda e: self.win.on_space(e) if self.focus_get() is not self.voices_entry else None)
        self.on_volume()
        self.update_idletasks()  # (next to the Hz bass window's top right)
        x = win.winfo_rootx() + max(0, win.winfo_width() - self.winfo_reqwidth() - round(20 * s))
        self.geometry(f"+{x}+{win.winfo_rooty() + round(60 * s)}")

    def refresh(self):
        """What the preview is doing (called by the Hz bass window every time it looks)."""
        if not self.winfo_exists():
            return
        cfg, p = self.app.hz_preview, self.win.preview
        name = os.path.basename(cfg["font"]) if cfg["font"] else tr("ps.no_font")
        if self.font_name.cget("text") != name:
            self.font_name.config(text=name)
        limit = cfg["voices"]
        if not self.win.preview_on.get():
            used, colour = tr("ps.off"), "#555"
            speed = ""
        else:
            full = p.voices_used >= limit
            used = tr("ps.used", used=f"{p.voices_used:,}", limit=f"{limit:,}") + (
                "  " + tr("ps.full") if full else "")
            colour = ORANGE if full else "#222"
            if p.speed:
                slow = p.speed < 1.0
                speed = tr("ps.speed", speed=f"{p.speed:.1f}", n=WORKERS) + ("  " + tr("ps.slow") if slow else "")
            else:
                slow, speed = False, tr("ps.speed_none")
        if (self.used.cget("text"), str(self.used.cget("foreground"))) != (used, colour):
            self.used.config(text=used, foreground=colour)
        sc = ORANGE if speed and p.speed and p.speed < 1.0 else "#222"
        if (self.speed.cget("text"), str(self.speed.cget("foreground"))) != (speed, sc):
            self.speed.config(text=speed, foreground=sc)
        qs = getattr(self.app, "quick", None)  # the live keys' recordings (orange: full, old ones thrown away)
        used = qs.used_mb() if qs is not None else 0.0
        live = tr("ps.live_used", used=f"{used:,.0f}", limit=f"{cfg['live_mb']:,}")
        lc = ORANGE if used >= cfg["live_mb"] * 0.98 else "#222"
        if (self.live_used.cget("text"), str(self.live_used.cget("foreground"))) != (live, lc):
            self.live_used.config(text=live, foreground=lc)

    def pick_font(self):
        cfg = self.app.hz_preview
        path = filedialog.askopenfilename(
            parent=self, title=tr("hz.preview_pick_font"),
            initialdir=os.path.dirname(cfg["font"]) if cfg["font"] else None,
            filetypes=[(tr("hz.preview_fonts"), "*.sf2 *.sf3 *.sfz *.sf2pack"), (tr("hz.preview_all"), "*.*")])
        if not path:
            return
        cfg["font"] = os.path.normpath(path)
        self.app.schedule_autosave()
        self.remake()

    def on_voices(self):
        try:
            v = int(round(float(calc(self.voices.get()))))
            if not VOICES[0] <= v <= VOICES[1]:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            self.voices_entry.config(style="Bad.TEntry")
            return
        self.voices_entry.config(style="TEntry")
        if str(v) != self.voices.get():
            self.voices.set(str(v))
        cfg = self.app.hz_preview
        if v != cfg["voices"]:
            cfg["voices"] = v
            self.app.schedule_autosave()
            self.remake()

    def on_live_mb(self):
        try:
            v = int(round(float(calc(self.live_mb.get()))))
            if not LIVE_MB[0] <= v <= LIVE_MB[1]:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            self.live_entry.config(style="Bad.TEntry")
            return
        self.live_entry.config(style="TEntry")
        if str(v) != self.live_mb.get():
            self.live_mb.set(str(v))
        cfg = self.app.hz_preview
        if v != cfg["live_mb"]:
            cfg["live_mb"] = v
            self.app.schedule_autosave()
            if getattr(self.app, "quick", None) is not None:
                self.app.quick.set_budget(v)
            self.refresh()

    def on_nofx(self):
        self.app.hz_preview["nofx"] = bool(self.nofx.get())
        self.app.schedule_autosave()
        self.remake()

    def on_volume(self, *_):
        v = round(float(self.volume.get())) / 100
        self.app.hz_preview["volume"] = v
        self.volume_says.config(text=f"{round(v * 100)} %")
        player = self.win.preview.player
        if player is not None:
            player.volume = v

    def remake(self):
        """A setting that changes the sound: made again (if the preview is on)."""
        if self.win.preview_on.get():
            err = self.win.preview.remake()
            if err:
                self.win.preview_failed(err)
        self.win.redraw()
        self.refresh()
