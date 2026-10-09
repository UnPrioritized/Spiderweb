"""The Hz bass window's "Settings…" window (user, 2026-10-09: the toolbar was too full). Two parts:
- Hz bass: Pitch (cents), Gates + the Auto threshold, the project's PPQ, "Shape length follows the notes". Its boxes
  are the Hz bass window's own (win.pitch_entry, win.gates, win.auto_row, win.grow_box): the window is made once with
  the Hz bass window and only hidden when closed, so they're always there. Changes are undo steps of the Hz bass
  window (this window sits inside it: App.in_hz).
- Preview (hz_preview.py): soundfont, voice limit, no reverb / chorus, volume, the live keys' memory (hz_live.py),
  and what the preview is doing (voices in use against the limit, how fast the sound is made, the live keys'
  recordings kept), updated while it works so a changed voice limit shows its effect at once. These are program
  settings, not the project's: no undo, kept with the window settings in the autosave."""

import os
import tkinter as tk
from tkinter import filedialog, ttk

from files.lang import tr
from files.mathexpr import calc
from files.synth import FONT_TYPES
from notes.hzbass import AUTO_MOST
from window import look
from window.hz_gates import GATE_MODES
from window.hz_preview import LIVE_MB, VOICES, WORKERS
from window.widgets import Scrub, Tooltip, bad, good, remember_place

ORANGE = look.WARN


def open_settings(win):
    """win = the Hz bass window: its Settings window shown (or brought to the front)."""
    win.settings_window.show()


open_preview_settings = open_settings  # (its old name)


def no_spaces(var):
    """A box's spaces taken out (Space types one there, user; numbers have none) when the box is left."""
    if " " in var.get():
        var.set(var.get().replace(" ", ""))


def auto_box(app, parent, var, apply):
    """The Auto gates threshold box ("within [3] cents"): a frame (not packed) with .entry. apply() on Enter,
    leaving the box, and each step of the number."""
    f = ttk.Frame(parent)
    lb = ttk.Label(f, text=tr("hz.auto_within"))
    lb.pack(side="left")
    f.entry = ttk.Entry(f, textvariable=var, width=4)
    f.entry.pack(side="left", padx=(4, 2))
    ttk.Label(f, text=tr("panel_custom.hz_cents"), foreground=look.HINT).pack(side="left", padx=(0, 4))
    f.entry.bind("<Return>", lambda e: apply())
    f.entry.bind("<FocusOut>", lambda e: no_spaces(var) or apply())
    Scrub(app, [(f.entry, var, apply)], (0.5, 5, 0.1), 0, AUTO_MOST, label=lb)
    for w in (lb, f.entry):
        Tooltip(w, tr("hz.auto_tip", most=f"{AUTO_MOST:g}"))
    return f


class PreviewSettings(tk.Toplevel):
    def __init__(self, win):
        super().__init__(win)
        self.withdraw()  # (shown by Settings…)
        self.win, self.app = win, win.app
        self.title(tr("ps.title"))
        self.transient(win)
        self.resizable(False, False)
        self.placed_once = False
        app = self.app
        s = app.scale
        cfg = app.hz_preview
        box = ttk.Frame(self, padding=(12, 10, 12, 12))
        box.pack(fill="both", expand=True)
        box.columnconfigure(1, weight=1)
        r = 0

        def head(text, r, top):
            ttk.Label(box, text=text, font=look.font(9, "bold")).grid(row=r, column=0, columnspan=3, sticky="w",
                                                                     pady=(top, 4))

        # ---- the Hz bass
        head(tr("ps.hz_head"), r, 0)
        r += 1
        lb = ttk.Label(box, text=tr("hz.pitch"))  # the whole Hz bass's pitch, in cents
        lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        f = ttk.Frame(box)
        f.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        win.pitch_entry = ttk.Entry(f, textvariable=win.pitch_var, width=6)
        win.pitch_entry.pack(side="left")
        ttk.Label(f, text=tr("panel_custom.hz_cents"), foreground=look.HINT).pack(side="left", padx=(4, 0))
        for w in (lb, win.pitch_entry):
            Tooltip(w, tr("panel_custom.hz_cents_tip"))
        win.pitch_entry.bind("<Return>", lambda e: win.on_pitch())
        win.pitch_entry.bind("<FocusOut>", lambda e: no_spaces(win.pitch_var) or win.on_pitch())
        Scrub(app, [(win.pitch_entry, win.pitch_var, win.on_pitch)], (1, 10, 0.1), -1200, 1200, label=lb)
        r += 1

        ttk.Label(box, text=tr("hz.gates")).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        f = ttk.Frame(box)
        f.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        names = [tr("panel_custom.hz_" + m) for m in GATE_MODES]
        win.gates = ttk.Combobox(f, values=names, state="readonly", width=max(map(len, names)))
        win.gates.current(GATE_MODES.index("auto"))  # (a new Hz bass: Auto, user)
        win.gates.pack(side="left", padx=(0, 8))
        win.gates.bind("<<ComboboxSelected>>", win.on_gates)
        Tooltip(win.gates, tr("panel_custom.hz_gates_tip"))
        win.auto_row = auto_box(app, f, win.auto_var, win.on_auto)  # (shown with Auto gates)
        r += 1

        ttk.Label(box, text=tr("app.ppq")).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
        ppq = ttk.Combobox(box, textvariable=app.pvar["ppq"], values=app.ppq_box["values"], width=7, height=12)
        ppq.grid(row=r, column=1, sticky="w", pady=3)  # (the project's PPQ: the same box as under Project)
        ppq.bind("<FocusOut>", lambda e: no_spaces(app.pvar["ppq"]))
        Tooltip(ppq, tr("hz.ppq_tip"))
        r += 1

        win.grow_box = ttk.Checkbutton(box, text=tr("hz.grow"), variable=win.grow, command=win.on_grow,
                                       takefocus=False)
        win.grow_box.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
        Tooltip(win.grow_box, tr("hz.grow_tip"))
        r += 1

        # ---- the preview
        ttk.Separator(box).grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        r += 1
        head(tr("ps.preview_head"), r, 8)
        r += 1
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
        Scrub(app, [(e, self.voices, self.on_voices)], (50, 500, 1), *VOICES, label=lb)
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
        sc.bind("<ButtonRelease-1>", lambda e: app.schedule_autosave())
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
        Scrub(app, [(e, self.live_mb, self.on_live_mb)], (100, 1000, 10), *LIVE_MB, label=lb)
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
        ttk.Label(box, text=tr("ps.limiter"), foreground=look.SOFT_TEXT, wraplength=round(380 * s)).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<space>", win.on_space)  # (not in a box: on_space leaves those alone)
        self.on_volume()

    def show(self):
        if not self.placed_once:  # (the first time: next to the Hz bass window's top right, or where it was last)
            self.placed_once = True
            self.update_idletasks()
            s, win = self.app.scale, self.win
            x = win.winfo_rootx() + max(0, win.winfo_width() - self.winfo_reqwidth() - round(20 * s))
            self.geometry(f"+{x}+{win.winfo_rooty() + round(60 * s)}")
            remember_place(self, "preview_settings")
        self.deiconify()
        self.lift()
        self.refresh()

    def close(self):
        """Closed = hidden (its boxes are the Hz bass window's); a number typed in it is taken first."""
        self.win.canvas.focus_set()
        self.withdraw()

    def refresh(self):
        """What the preview is doing (called by the Hz bass window every time it looks)."""
        if not self.winfo_exists() or not self.winfo_viewable():
            return
        cfg, p = self.app.hz_preview, self.win.preview
        name = os.path.basename(cfg["font"]) if cfg["font"] else tr("ps.no_font")
        if self.font_name.cget("text") != name:
            self.font_name.config(text=name)
        limit = cfg["voices"]
        if not self.win.preview_on.get():
            used, colour = tr("ps.off"), look.INFO
            speed = ""
        else:
            full = p.voices_used >= limit
            used = tr("ps.used", used=f"{p.voices_used:,}", limit=f"{limit:,}") + (
                "  " + tr("ps.full") if full else "")
            colour = ORANGE if full else look.DARK_TEXT
            if p.speed:
                slow = p.speed < 1.0
                speed = tr("ps.speed", speed=f"{p.speed:.1f}", n=WORKERS) + ("  " + tr("ps.slow") if slow else "")
            else:
                slow, speed = False, tr("ps.speed_none")
        if (self.used.cget("text"), str(self.used.cget("foreground"))) != (used, colour):
            self.used.config(text=used, foreground=colour)
        sc = ORANGE if speed and p.speed and p.speed < 1.0 else look.DARK_TEXT
        if (self.speed.cget("text"), str(self.speed.cget("foreground"))) != (speed, sc):
            self.speed.config(text=speed, foreground=sc)
        qs = getattr(self.app, "quick", None)  # the live keys' recordings (orange: full, old ones thrown away)
        used = qs.used_mb() if qs is not None else 0.0
        live = tr("ps.live_used", used=f"{used:,.0f}", limit=f"{cfg['live_mb']:,}")
        lc = ORANGE if used >= cfg["live_mb"] * 0.98 else look.DARK_TEXT
        if (self.live_used.cget("text"), str(self.live_used.cget("foreground"))) != (live, lc):
            self.live_used.config(text=live, foreground=lc)

    def pick_font(self):
        cfg = self.app.hz_preview
        path = filedialog.askopenfilename(
            parent=self, title=tr("hz.preview_pick_font"),
            initialdir=os.path.dirname(cfg["font"]) if cfg["font"] else None,
            filetypes=[(tr("hz.preview_fonts"), FONT_TYPES), (tr("hz.preview_all"), "*.*")])
        if not path:
            return
        cfg["font"] = os.path.normpath(path)
        self.app.schedule_autosave()
        self.app.soundfont_changed()  # (Built-in BASSMIDI plays with it too)
        self.remake()

    def on_voices(self):
        try:
            v = int(round(float(calc(self.voices.get()))))
            if not VOICES[0] <= v <= VOICES[1]:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(self.voices_entry)
            return
        good(self.voices_entry)
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
            bad(self.live_entry)
            return
        good(self.live_entry)
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
