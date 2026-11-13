"""Ctrl+C on huge shapes (user 2026-11-09): the text put on the clipboard to share them (files/share.shapes_line:
JSON + zlib, ~1 s at 3 M pasted notes) is made on a thread, so the window goes on answering. Spiderweb's own copy
(what Ctrl+V pastes) is there at once; the text reaches the clipboard when it's made. Only when that takes longer
than SHOW_AFTER_S does a small "Copying notes to the clipboard…" window show over the piano roll (progress bar, no
buttons, never takes the keyboard, closes by itself). A copy done within QUICK_S is finished at once, as before.

App.put_clipboard starts a CopyJob; App.finish_copy waits for it (Spiderweb's own paste, closing); a newer copy or
anything else put on the clipboard meanwhile wins (the job's text is then dropped, never put over it)."""

import threading
import time
import tkinter as tk
from tkinter import ttk

from files import system
from files.lang import tr
from window.widgets import centre_over

SHOW_AFTER_S = 0.5
QUICK_S = 0.05
POLL_MS = 30


def _run(make, out):
    try:
        out["line"] = make()
    except BaseException as e:  # noqa: BLE001 (MemoryError too: handed to done on Tk's thread)
        out["error"] = e


class CopyJob:
    """make() run on a thread; done(job) called on Tk's thread once it's made (job.line, or job.error)."""

    def __init__(self, app, make, done):
        self.app, self.done = app, done
        self.line, self.error = None, None
        self.began = time.perf_counter()
        self.popup = None
        self.finished = False
        self.out = {}  # (what the thread hands back: it never holds this job / the window, so nothing of Tk's is
        #                let go of on it)
        self.thread = threading.Thread(target=_run, args=(make, self.out), daemon=True, name="copy text")
        self.thread.start()

    def quick(self):
        """Made within QUICK_S: finished here and now (True), else it goes on in the background (False)."""
        self.thread.join(QUICK_S)
        if self.thread.is_alive():
            self.app.after(POLL_MS, self.poll)
            return False
        self._end()
        return True

    def poll(self):
        if self.finished:
            return
        if self.thread.is_alive():
            if self.popup is None and time.perf_counter() - self.began >= SHOW_AFTER_S:
                self.popup = CopyPopup(self.app)
            self.app.after(POLL_MS, self.poll)
            return
        self._end()

    def finish(self):
        """Wait for the text here (Tk's thread), then hand it on."""
        if not self.finished:
            self.thread.join()
            self._end()

    def drop(self):
        """Not wanted any more (a newer copy): the text is never put on the clipboard."""
        self.done = None
        self.close_popup()

    def close_popup(self):
        if self.popup is not None:
            self.popup.close()
            self.popup = None

    def _end(self):
        if self.finished:
            return
        self.finished = True
        self.line, self.error = self.out.get("line"), self.out.get("error")
        self.close_popup()
        if self.done is not None:
            self.done(self)


class CopyPopup(tk.Toplevel):
    """The small "Copying notes to the clipboard…" window over the piano roll."""

    def __init__(self, app):
        try:
            had = app.focus_get()  # (before this window exists: the keyboard goes back there)
        except (KeyError, tk.TclError):
            had = None
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title(tr("app.copying_title"))
        self.transient(app)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # (no buttons: it closes by itself)
        s = getattr(app, "scale", 1)
        box = ttk.Frame(self, padding=(int(16 * s), int(12 * s)))
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("app.copying_clip")).pack(anchor="w")
        self.bar = ttk.Progressbar(box, mode="indeterminate", length=int(240 * s))
        self.bar.pack(fill="x", pady=(int(8 * s), 0))
        centre_over(self, getattr(app, "roll", None) or app)
        self.deiconify()
        if system.WINDOWS:  # (clicked: still never takes the keyboard)
            from window.domino_overlay import no_keyboard
            no_keyboard(self)
        self.bar.start(15)
        if had is not None:
            self.after(30, lambda: had.focus_force() if had.winfo_exists() and self.winfo_exists() else None)

    def close(self):
        try:
            self.bar.stop()
            self.destroy()
        except tk.TclError:
            pass
