"""Update checks: asking once whether (and how often) to look on GitHub for a newer version, looking at start when
it's time, the "update available" popup (with what's new in it), and the "What's new" Help page the first time a new
version starts. Settings are in the autosave's window settings: update_check (how often; missing = not asked yet),
update_last (the last check that reached GitHub) and seen_version (the version that last started). The looking itself
is files/update_check.py; it runs in the background, so the window never waits for it."""

import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

from files.about import VERSION, WEBSITE
from files.lang import tr
from files.update_check import newer_releases
from window import look
from window.widgets import remember_place

OFTEN = ["launch", "day", "week", "month", "off"]  # (saved as these)
PERIOD = {"launch": 0, "day": 86400, "week": 7 * 86400, "month": 30 * 86400}
ASK_AFTER = 60  # seconds after the start tips (welcome, then the view tip) before asking; at most ASK_LATEST from start
ASK_LATEST = 180


def often_names():
    return {k: tr("updates.often." + k) for k in OFTEN}


class Updates:
    def __init__(self, app):
        self.app = app
        self.often = None  # one of OFTEN; None = the user wasn't asked yet
        self.last = 0.0
        self.seen_version = None
        self.old_user = False  # there were window settings: not the very first start
        self.running = False
        self.reports = []  # the About page's lines waiting for the running check's answer
        self.popup = None
        self.question = None

    def state(self):
        """For the autosave's window settings."""
        got = {"update_last": self.last, "seen_version": VERSION}
        if self.often:
            got["update_check"] = self.often
        return got

    def restore(self, win):
        self.old_user = bool(win)
        self.often = win.get("update_check") if win.get("update_check") in OFTEN else None
        try:
            self.last = float(win.get("update_last") or 0)
        except (TypeError, ValueError):
            self.last = 0.0
        seen = win.get("seen_version")
        self.seen_version = str(seen) if seen else None

    def start(self):
        """At start: What's new (a new version's first start, not the very first start), then the question or a
        check if it's time."""
        if self.old_user and self.seen_version != VERSION:
            from window.help import open_help
            self.app.after(1200, lambda: open_help(self.app, "whats_new"))
        self.app.schedule_autosave()  # (remembers this version as seen)
        if self.often is None:
            self._started = time.time()
            self.app.after(1000, self._wait_for_tips)
        elif self.often != "off" and (time.time() - self.last >= PERIOD[self.often]
                                      or self.last > time.time()):  # (a clock once set ahead: due now)
            self.app.after(3000, self.check)

    def _wait_for_tips(self):
        """Ask ASK_AFTER seconds after the second start tip showed (or the tips were closed / are off / were seen
        before), and at the latest ASK_LATEST seconds after the start."""
        tips = self.app.tips
        tip_open = tips.popup is not None and tips.popup.winfo_exists()
        done = not tips.on.get() or "view" in tips.seen or ("welcome" in tips.seen and not tip_open)
        waited = time.time() - self._started
        if done or waited >= ASK_LATEST - ASK_AFTER:
            self.app.after(ASK_AFTER * 1000, self.ask)
        else:
            self.app.after(1000, self._wait_for_tips)

    def ask(self):
        if self.often is None and (self.question is None or not self.question.winfo_exists()):
            self.question = UpdateQuestion(self)

    def set_often(self, often):
        self.often = often
        self.app.schedule_autosave()

    def check(self, report=None):
        """Look on GitHub in the background. report(text): the About page's line, told how it went (the check at
        start has none: nothing shows unless there's an update). Asked while a check runs: told when that one ends."""
        if report:
            self.reports.append(report)
        if self.running:
            return
        self.running = True
        result = {}

        def work():
            try:
                result["found"] = newer_releases()
            except Exception:  # no connection, GitHub down, an odd answer: just no news
                result["error"] = True

        worker = threading.Thread(target=work, daemon=True)
        worker.start()

        def wait():  # (Tk isn't touched from the other thread: this looks for its answer)
            if worker.is_alive():
                self.app.after(200, wait)
                return
            self.running = False
            reports, self.reports = self.reports, []

            def tell(msg):
                for r in reports:
                    r(msg)
            if "error" in result:
                tell(tr("updates.no_connection"))
                return
            self.last = time.time()
            self.app.schedule_autosave()
            if result["found"]:
                tell(tr("updates.found", version=result["found"][0]["version"]))
                self.show_popup(result["found"])
            else:
                tell(tr("updates.newest", VERSION=VERSION))

        self.app.after(200, wait)

    def show_popup(self, found):
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = UpdatePopup(self, found)


def often_box(parent, updates, width=26):
    """The "how often" dropdown (Help → About, the update popup)."""
    names = often_names()
    var = tk.StringVar(value=names[updates.often or "launch"])
    box = ttk.Combobox(parent, textvariable=var, values=list(names.values()), state="readonly", width=width)
    box.bind("<<ComboboxSelected>>",
             lambda e: updates.set_often(next(k for k, n in names.items() if n == var.get())))
    return box


def on_top(win, parent, name, focus=True):
    """A small window over Spiderweb (and over any open tip), in the middle of it (or where it was last: name).
    focus=False: the keyboard stays where it is."""
    win.transient(parent)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    remember_place(win, name)
    win.lift()
    if focus:
        win.focus_force()


class UpdateQuestion(tk.Toplevel):
    """Asked once: look for updates, and how often. Closing it with X asks again at the next start."""

    def __init__(self, updates):
        app = updates.app
        f = app.focus_get()  # (before this window exists)
        super().__init__(app)
        self.updates = updates
        s = app.scale
        self.title(tr("updates.question_title"))
        box = ttk.Frame(self, padding=(16, 12, 16, 12))
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("updates.question_head"), font=look.font(10, "bold")).pack(anchor="w")
        ttk.Label(box, text=tr("updates.question_text"), wraplength=int(400 * s),
                  justify="left").pack(anchor="w", pady=(4, 8))
        self.var = tk.StringVar(value="launch")
        for k, name in often_names().items():
            ttk.Radiobutton(box, text=name, value=k, variable=self.var).pack(anchor="w", padx=(8, 0))
        ttk.Label(box, text=tr("updates.question_later"), foreground=look.HINT).pack(anchor="w", pady=(8, 10))
        ttk.Button(box, text=tr("updates.ok"), command=self.ok).pack(side="right")
        self.bind("<Return>", lambda e: self.ok())
        # it shows while you work: the keyboard stays where it was (an Enter meant for a box doesn't answer it)
        on_top(self, app, "update_question", focus=False)
        if f is not None:
            self.after(30, lambda: f.focus_force() if f.winfo_exists() else None)

    def ok(self):
        self.updates.set_often(self.var.get())
        self.destroy()
        if self.updates.often != "off":
            self.updates.check()


class UpdatePopup(tk.Toplevel):
    """A newer version is out: which, what's new in it (every newer version's notes, newest first), the download
    page (the browser opens the GitHub release; nothing downloads by itself), Later, and how often to look."""

    def __init__(self, updates, found):
        app = updates.app
        super().__init__(app)
        self.url = found[0]["url"]
        self.title(tr("updates.popup_title"))
        box = ttk.Frame(self, padding=(16, 12, 16, 12))
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=tr("updates.popup_head", version=found[0]["version"]),
                  font=look.font(11, "bold")).pack(anchor="w")
        ttk.Label(box, text=tr("updates.you_have", VERSION=VERSION), foreground=look.HINT).pack(anchor="w", pady=(2, 8))
        ttk.Label(box, text=tr("updates.whats_new"), font=look.font(10, "bold")).pack(anchor="w")
        text_box = ttk.Frame(box)
        text_box.pack(fill="both", expand=True, pady=(4, 10))
        text = tk.Text(text_box, wrap="word", font=look.font(9), width=64, height=14, relief="solid",
                       borderwidth=1, padx=8, pady=6, spacing3=2, cursor="arrow")
        sb = ttk.Scrollbar(text_box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        text.tag_configure("version", font=look.font(10, "bold"), spacing1=4, spacing3=4)
        text.tag_configure("bullet", lmargin2=text.tk.call("font", "measure", text.cget("font"), "•  "))
        for n, rel in enumerate(found):
            if len(found) > 1:
                text.insert("end", ("\n" if n else "") + tr("updates.version", version=rel["version"]) + "\n",
                            "version")
            for line in (rel["notes"] or tr("updates.no_notes")).split("\n"):  # (a long bullet wraps under its text)
                text.insert("end", line + "\n", "bullet" if line.startswith("•") else ())
        text.config(state="disabled")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text=tr("updates.check_for_updates")).pack(side="left")
        often_box(row, updates).pack(side="left", padx=(6, 0))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text=tr("updates.later"), command=self.destroy).pack(side="right")
        ttk.Button(row, text=tr("updates.open_download_page"), command=self.download).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda e: self.destroy())
        on_top(self, app, "update_popup")

    def download(self):
        webbrowser.open(self.url or WEBSITE + "/releases/latest")
        self.destroy()
