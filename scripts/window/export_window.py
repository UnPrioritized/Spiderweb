"""The small window while a loaded MIDI is exported (user 2026-10-29: the status line alone said too little on a
17 GB file): the steps it goes through (✓ done, ▶ now), a bar for the step, how fast and how long, and Cancel
(nothing of the export is kept). Not modal: the piano roll can be used meanwhile (the export works on a copy)."""
import os
import time
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from window import look
from window.widgets import ShortLabel, remember_place

POLL_MS = 100
WIDTH = 490  # px, like the Load MIDI window
# job["step"] -> the line shown for it, in the order they run
STEPS = {"load.reading_ticks": "export.step_ticks", "project.tempo_working": "export.step_tempo",
         "load.checking_own": "export.step_own", "load.exporting": "export.step_write"}


def size_text(n):
    from window.load_midi import size_text as text  # (load_midi imports project: only once it's all loaded)
    return text(n)


class ExportWindow(tk.Toplevel):
    def __init__(self, app, job, path, steps):
        """job: export_loaded's job dict ("step", "done", "total", "cancel", "finished"); steps: the STEPS keys this
        export goes through."""
        super().__init__(app)
        self.withdraw()
        self.app, self.job, self.order = app, job, [s for s in STEPS if s in steps]
        self.title(tr("export.title"))
        self.resizable(False, False)
        self.transient(app)
        body = ttk.Frame(self, padding=(16, 14, 16, 8))
        body.pack(fill="both", expand=True)
        ttk.Frame(body, width=WIDTH, height=0).pack(anchor="w")
        ShortLabel(body, text=os.path.basename(path), font=look.font(10, "bold")).pack(anchor="w", fill="x")
        ShortLabel(body, text=os.path.dirname(os.path.abspath(path)), foreground=look.HINT).pack(anchor="w", fill="x")
        self.rows = {}
        for n, key in enumerate(self.order):
            row = ttk.Label(body, text="")
            row.pack(anchor="w", pady=(8 if n == 0 else 1, 0))
            self.rows[key] = row
        self.bar = ttk.Progressbar(body, length=440, maximum=1000)
        self.bar.pack(anchor="w", pady=(10, 2))
        self.info = ttk.Label(body, text="", foreground=look.HINT)
        self.info.pack(anchor="w")
        buttons = ttk.Frame(self, padding=(16, 0, 16, 12))
        buttons.pack(fill="x")
        self.stop = ttk.Button(buttons, text=tr("load.cancel"), command=self.cancel)
        self.stop.pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.step, self.since = None, time.perf_counter()  # (the step shown, and when it began: its speed)
        self.refresh()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "export_midi")
        self.deiconify()

    def cancel(self):
        """Stops the export (its half-written file deleted); the window closes once it has stopped."""
        self.job["cancel"] = True
        self.stop.state(["disabled"])
        self.info.config(text=tr("export.cancelling"))

    def refresh(self):
        """Shows where the export is (called by its poll until it has finished)."""
        job = self.job
        if not self.winfo_exists() or job["cancel"]:
            return
        step = job["step"]
        if step != self.step:
            self.step, self.since = step, time.perf_counter()
        at = self.order.index(step) if step in self.order else 0
        for i, key in enumerate(self.order):
            mark = "✓ " if i < at else ("▶ " if i == at else "   ")
            self.rows[key].config(text=mark + tr(STEPS[key]), foreground=look.DARK_TEXT if i <= at else look.HINT)
        took = time.perf_counter() - self.since
        if not job["total"]:  # (a step that can't say how far it is: a moving bar + how long so far)
            if str(self.bar.cget("mode")) != "indeterminate":
                self.bar.config(mode="indeterminate", maximum=100)
                self.bar.start(15)
            self.info.config(text=tr("export.busy_for", s=f"{took:.0f}"))
            return
        if str(self.bar.cget("mode")) != "determinate":
            self.bar.stop()
            self.bar.config(mode="determinate", maximum=1000)
        done, total = job["done"], job["total"]
        share = min(1.0, done / total)
        self.bar.config(value=int(1000 * share))
        speed = done / took if took > 0.5 else 0
        left = (total - done) / speed if speed and share > 0.02 else None
        self.info.config(text=(tr("export.speed", done=size_text(done), total=size_text(total), mb=f"{speed / 1e6:,.0f}")
                               if speed else tr("export.pct", pct=int(100 * share)))
                         + ("  " + tr("load.left", s=f"{left:.0f}") if left is not None and left >= 2 else ""))
