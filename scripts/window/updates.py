"""Update checks: asking once whether (and how often) to look on GitHub for a newer version, looking at start when
it's time, the "update available" popup (with what's new in it), and the "What's new" Help page the first time a new
version starts. Settings are in user/settings.ini [updates] (files/settings.APP_KEYS; handed over in the window
settings): update_check (how often; missing = not asked yet), update_last (the last check that reached GitHub) and
seen_version (the version that last started). The looking itself
is files/update_check.py; it runs in the background, so the window never waits for it."""

import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

from files import errors, self_update, system
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
        often = str(win.get("update_check") or "").strip().lower()  # (settings.ini typed by hand: any capitals)
        self.often = often if often in OFTEN else None
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
        if getattr(self, "often_var", None) is not None:  # (open boxes show it, e.g. after the first question)
            self.often_var.set(often_names()[often or "launch"])
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
            if self.popup.busy:  # (downloading: never a second download, never a hidden one)
                self.popup.lift()
                return
            self.popup.destroy()
        self.popup = UpdatePopup(self, found)


def often_box(parent, updates, width=26):
    """The "how often" dropdown (Settings → Updates, the update popup)."""
    names = often_names()
    var = getattr(updates, "often_var", None)  # (one value for every such box: Settings and the popup agree)
    if var is None:
        var = updates.often_var = tk.StringVar(updates.app)
    var.set(names[updates.often or "launch"])
    box = ttk.Combobox(parent, textvariable=var, values=list(names.values()), state="readonly", width=width)
    box.bind("<<ComboboxSelected>>",
             lambda e: updates.set_often(list(names)[e.widget.current()]))  # (the place: alike words stay apart)
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
    APP_ONLY = True  # (keys pressed here never change the shapes: App.app_only)

    def __init__(self, updates):
        app = updates.app
        try:
            f = app.focus_get()  # (before this window exists)
        except (KeyError, tk.TclError):  # (a dropdown's list has it: older Pythons raise)
            f = None
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
    page (the browser opens the GitHub release), Later, and how often to look. A signed release that can be checked
    here also gets "Update and restart" (exe) / "Download and check" (scripts): files/self_update.py, only on a
    press, never by itself."""
    APP_ONLY = True  # (keys pressed here never change the shapes: App.app_only)

    def __init__(self, updates, found):
        app = updates.app
        super().__init__(app)
        self.updates = updates
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
        # updating from here (files/self_update.py): only when the release is signed and it can be checked
        self.release = found[0]
        self.cancel_event = None
        self.work = ttk.Frame(box)  # (progress + what happened: shown once the update button is pressed)
        self.bar = ttk.Progressbar(self.work, mode="determinate", maximum=1000)
        self.say = ttk.Label(self.work, text="", wraplength=int(440 * app.scale), justify="left")
        self.stop_button = ttk.Button(self.work, text=tr("updates.cancel"), command=self.stop)
        self.row = row = ttk.Frame(box)
        row.pack(fill="x")
        self.later = ttk.Button(row, text=tr("updates.later"), command=self.close)
        self.later.pack(side="right")
        self.go = None
        if self_update.possible(self.release) is None:
            self.go = ttk.Button(row, text=tr("updates.update_exe" if self_update.exe_mode() else "updates.update_zip"),
                                 command=self.start_update)
            self.go.pack(side="right", padx=(0, 6))
        ttk.Button(row, text=tr("updates.open_download_page"), command=self.download).pack(side="right", padx=(0, 6))
        self.busy = False  # (downloading / finishing: a new check only brings this popup to the front)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Destroy>", self._gone, add="+")
        self.protocol("WM_DELETE_WINDOW", self.close)
        on_top(self, app, "update_popup")

    def download(self):
        webbrowser.open(self.url or WEBSITE + "/releases/latest")
        self.close()

    def _gone(self, e):
        """Closed any way (Spiderweb itself closing too): a running download stops, its part file deleted."""
        if e.widget is self and self.cancel_event is not None:
            self.cancel_event.set()

    def close(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
        self.destroy()

    def stop(self):
        """The Cancel button while downloading (the popup stays; shown at once, the download ends when its
        connection next answers)."""
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.stop_button.state(["disabled"])
            self.say.configure(text=tr("updates.stopping"), foreground="")

    def start_update(self):
        self.busy = True
        self.stop_button.state(["!disabled"])
        self.go.state(["disabled"])
        self.cancel_event = threading.Event()
        self.progress = [0, 0]
        self.result = {}
        release, cancel, result, progress = self.release, self.cancel_event, self.result, self.progress

        def work():
            try:
                result["path"] = self_update.download(release, lambda d, s: progress.__setitem__(slice(None), [d, s]),
                                                      cancel)
            except self_update.Refused as e:
                result["fail"] = str(e)
            except OSError:  # (disk trouble comes as Refused("disk"): what's left is the connection)
                result["fail"] = "no_connection"
            except Exception:
                result["fail"] = "other"
                errors.write_log(*sys.exc_info(), "Update download")
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.work.pack(fill="x", pady=(0, 10), before=self.row)
        self.say.configure(text=tr("updates.connecting"), foreground="")
        self.say.pack(fill="x")
        self.bar.configure(value=0)
        self.stop_button.pack(side="right", pady=(4, 0))
        self.bar.pack(side="left", fill="x", expand=True, pady=(4, 0), padx=(0, 6))
        self.after(100, self.poll)

    def poll(self):
        """(Tk isn't touched from the download thread: this looks at what it reached.)"""
        if not self.winfo_exists():
            return
        done, size = self.progress
        mb = lambda n: "%.1f" % (n / 1048576)  # noqa: E731
        if done and not self.cancel_event.is_set():
            self.bar.configure(value=done * 1000 // size if size else 0)
            self.say.configure(text=tr("updates.downloading", done=mb(done), size=mb(size)) if size else
                               tr("updates.downloading_unknown", done=mb(done)))
        if self.worker.is_alive():
            self.after(100, self.poll)
            return
        self.cancel_event = None
        self.bar.pack_forget()
        self.stop_button.pack_forget()
        if "fail" in self.result:
            self.failed(self.result["fail"])
        elif self_update.exe_mode():
            self.finish_exe(self.result["path"])
        else:
            self.finish_zip(self.result["path"])

    def failed(self, why):
        self.busy = False
        self.say.configure(text=tr("updates.fail." + why), foreground="" if why == "cancelled" else look.WARN)
        if self.go is not None:
            self.go.state(["!disabled"])

    def finish_zip(self, path):
        self.busy = False
        self.say.configure(text=tr("updates.zip_ready", file=path), foreground="")
        self.go.configure(text=tr("updates.show_file"), command=lambda: system.show_file(path))
        self.go.state(["!disabled"])
        system.show_file(path)

    def finish_exe(self, path):
        """Swap the checked exe in, close Spiderweb as usual (it may ask things: cancelled = swapped back), then start
        the new one."""
        self.say.configure(text=tr("updates.restarting"), foreground="")
        self.update_idletasks()
        try:
            size = os.path.getsize(path)
            undo = self_update.swap_in(path)
        except OSError:
            try:
                os.remove(path)
            except OSError:
                pass
            self.failed("swap_failed")
            return
        if not self_update.new_ready(size):  # (taken away since it was checked: an antivirus?)
            self.back(undo, "blocked")
            return
        app = self.updates.app
        self.attributes("-topmost", False)  # (closing's own questions must come in front of this popup)
        try:
            app.on_close()
        except Exception:  # (an error while closing: Spiderweb stays as it is, the old exe back)
            errors.write_log(*sys.exc_info(), "Closing for an update")
        try:
            still_open = bool(app.winfo_exists())
        except tk.TclError:
            still_open = False
        if still_open:
            self.back(undo, "not_closed")
            return
        try:
            self_update.start_new()
        except OSError:  # (Windows refused it, e.g. an antivirus): the old exe back + a message, then it starts
            back = self_update.give_back()
            system.error_box(tr("updates.popup_title"), tr("updates.fail.start_failed" if back else
                                                           "updates.fail.start_failed_kept"))
            if back:
                try:
                    self_update.start_new()  # (the old version again: it waits for this one's lock too)
                except OSError:
                    pass

    def back(self, undo, why):
        """Spiderweb stays open: the old exe back in place (or, when that fails, ask for a restart: the running
        program must not go on reading the new exe's file)."""
        ok = undo()
        if not self.winfo_exists():
            return
        self.attributes("-topmost", True)
        self.failed(why if ok else "undo_failed")
