"""File → Load MIDI… (user 2026-10-23): a MIDI file opened as a NEW project, its notes drawn under Spiderweb's own
shapes (notes/loaded.py: the file stays the boss, its notes are never copied into shapes). One window walks through
it: how to hold the notes (asked BEFORE reading), the reading with each step and Cancel, then a summary.
The project is replaced only once the reading is done; Cancel / an error leave it as it was."""

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from files import midi_saved as saved
from files.play_dense import kept_bytes
from files.lang import tr, trn
from files.midi_read import Cancelled, FileChanged, MidiError, MidiFile, M_SMPTE
from files.project import OUTPUT_DIR
from files.system import free_memory
from notes import loaded as L
from window import look
from window.widgets import ShortLabel, remember_place

POLL_MS = 50
WIDTH = 490  # px: the pages' width (their wrapped hint lines are 440-460 + indent)
REOPEN_SHOW_MS = 400  # an opened project's MIDI loaded again: the window shows only after this long


def size_text(n):
    return tr("load.gb", n=f"{n / 1e9:.1f}") if n >= 1e9 else tr("load.mb", n=f"{max(n, 1e5) / 1e6:.1f}")


def load_midi(app, path=None):
    """The File menu's Load MIDI…; path = a file dropped / opened with Spiderweb (window/open_files.py)."""
    if path is None:
        path = filedialog.askopenfilename(title=tr("load.pick"), initialdir=OUTPUT_DIR,
                                          filetypes=[(tr("load.midi_files"), "*.mid *.midi *.rmi *.smf"),
                                                     (tr("project.all_files"), "*.*")])
    if not path or not app.may_replace(os.path.basename(path)):
        return
    try:
        mf = MidiFile(path)
    except MidiError as e:
        messagebox.showerror(tr("load.title"), tr(e.key, **e.info), parent=app)
        return
    if M_SMPTE in mf.marks:  # (time in frames: Spiderweb works in beats)
        messagebox.showerror(tr("load.title"), tr("load.smpte"), parent=app)
        return
    LoadWindow(app, mf)


def ask_choice(parent, title, text, buttons, icon="question", focus=None, skip=None):
    """A question with named buttons [(answer, label)], the first one picked by Enter (focus = the answer whose
    button has the keyboard instead: Enter / Space press that one); Esc / X = the last one's answer. skip: a list
    = a "Don't ask again until Spiderweb is closed" tick under the text, its state appended to it."""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.resizable(False, False)
    win.transient(parent.winfo_toplevel())
    body = ttk.Frame(win, padding=(14, 14, 14, 8))
    body.pack(fill="both", expand=True)
    ttk.Label(body, image=f"::tk::icons::{icon}").grid(row=0, column=0, sticky="n", padx=(0, 12))
    ttk.Label(body, text=text, wraplength=460, justify="left").grid(row=0, column=1, sticky="w")
    ticked = tk.BooleanVar(win, value=False)
    if skip is not None:
        ttk.Checkbutton(body, text=tr("big_ask.dont_ask"), variable=ticked).grid(row=1, column=1, sticky="w",
                                                                                 pady=(12, 0))
    row = ttk.Frame(win, padding=(14, 4, 14, 12))
    row.pack(fill="x")
    answer = [buttons[-1][0]]

    def close(how):
        answer[0] = how
        win.destroy()
    made = []
    first = None
    for how, label in reversed(buttons):
        b = ttk.Button(row, text=label, command=lambda how=how: close(how))
        b.pack(side="right", padx=(6, 0))
        made.append(b)
        if how == focus:
            first = b
    win.bind("<Return>", lambda e: e.widget.invoke() if isinstance(e.widget, ttk.Button) else close(buttons[0][0]))
    win.bind("<Escape>", lambda e: close(buttons[-1][0]))
    win.protocol("WM_DELETE_WINDOW", lambda: close(buttons[-1][0]))
    win.update_idletasks()
    top = parent.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - win.winfo_reqwidth()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    remember_place(win, "load_ask")
    win.deiconify()
    win.grab_set()
    (first or made[-1]).focus_set()
    win.wait_window()
    if skip is not None:
        skip.append(ticked.get())
    return answer[0]


def offer_index(app, lm):
    """A project's MIDI loaded again slowly (no summary page there): the summary's Save Index question in its own
    window (user 2026-10-25, picked A: deleting the index meant it was never offered again)."""
    if app.loaded is not lm or not saved.offer(lm):
        return
    answer = ask_choice(app, tr("load.title"), tr("load.index_ask", s=f"{lm.seconds:.0f}",
                                                  size=size_text(lm.index_bytes() + kept_bytes(lm))),
                        [("save", tr("load.index_save")), ("never", tr("load.index_never")),
                         ("later", tr("load.index_later"))])
    if answer == "never":
        saved.decline(lm.path)
    elif answer == "save":
        app.config(cursor="watch")
        app.update_idletasks()
        try:
            size = saved.save_index(lm)
        except OSError as e:
            messagebox.showerror(tr("load.title"), tr("load.index_failed", why=e.strerror or str(e)), parent=app)
        else:
            app.status.config(text=tr("load.index_saved_status", size=size_text(size)))
        finally:
            app.config(cursor="")


def use_changed(app, lm):
    """A project's MIDI changed since it was saved, "Use it as it is now": loaded, its Conductor line taken, and what
    was made for the old file let go (said in one message): the Extracts (their boxes are the old file's ticks +
    tracks: they'd hide other notes, hunt 2026-10-25), Delete loaded notes' boxes and the dragged track order (hunt
    2026-10-26)."""
    from notes.loaded import drop_extract, shape_boxes
    took = [sh for sh in app.shapes if shape_boxes([sh])]
    for sh in took:
        drop_extract(sh)
    gone, moved = bool(app.deleted), app.file_order is not None or bool(app.file_gone) or bool(app.file_props)
    app.deleted, app.file_order, app.file_gone, app.file_props = [], None, [], {}
    app.reattach_midi(lm, retempo=True)
    if took or gone or moved:
        app.shapes_changed()  # (the Shapes list's names: no longer Extracts)
        text = "\n\n".join(([trn("load.extract_released", len(took), n=len(took))] if took else [])
                           + ([tr("load.deleted_released")] if gone else [])
                           + ([tr("load.order_released")] if moved else []))
        messagebox.showinfo(tr("load.title"), text, parent=app)


def said_without(app):
    """The project goes on without its MIDI (not found / not picked / kept without): the status line says so and the
    title says "not found" (only now: while it's still looked for or loading, its name alone, hunt 2026-10-26)."""
    app.midi_missing = True
    app.status.config(text=tr("load.status_without"))
    app.sync_window_title()


def find_loaded(app, pick=False):
    """An opened project's MIDI loaded again (its link: notes.loaded.clean_link). Not where it was = asked (find
    it / go on without it); pick = straight to the file picker. Found = loaded with the progress window, then
    checked against the link (LoadWindow.finished)."""
    link = app.midi_link
    if link is None or app.loaded is not None:
        return
    running = getattr(app, "_finding", None)
    if running is not None:  # (one already loading: this project's = let it be; another's = stopped, hunt 2026-10-24)
        if running.link is link:
            return
        running.drop()
    folder = os.path.dirname(app.project_file) if app.project_file else None
    path = None if pick else L.located(link, folder)
    if path is None:
        if not pick and ask_choice(app, tr("load.title"), tr("load.link_missing", path=link["path"]),
                                   [("find", tr("load.find")), ("keep", tr("load.keep_without"))],
                                   icon="warning") != "find":
            said_without(app)
            return
        path = filedialog.askopenfilename(
            title=tr("load.find_title"), initialdir=folder or os.path.dirname(link["path"]) or OUTPUT_DIR,
            initialfile=os.path.basename(link["path"]),
            filetypes=[(tr("load.midi_files"), "*.mid *.midi *.rmi *.smf"), (tr("project.all_files"), "*.*")])
        if not path:
            said_without(app)
            return
    try:
        mf = MidiFile(path)
    except MidiError as e:
        messagebox.showerror(tr("load.title"), tr(e.key, **e.info), parent=app)
        return
    if M_SMPTE in mf.marks:
        messagebox.showerror(tr("load.title"), tr("load.smpte"), parent=app)
        return
    LoadWindow(app, mf, link=link)


class LoadWindow(tk.Toplevel):
    def __init__(self, app, mf, link=None):
        """link: an opened project's MIDI loaded again (no question, no summary; checked against the link)."""
        super().__init__(app)
        self.withdraw()
        self.app, self.mf, self.link = app, mf, link
        self.title(tr("load.title"))
        self.resizable(False, False)
        self.transient(app)
        self.loader = self.thread = None
        self.body = ttk.Frame(self, padding=(16, 14, 16, 8))
        self.body.pack(fill="both", expand=True)
        self.buttons = ttk.Frame(self, padding=(16, 0, 16, 12))
        self.buttons.pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        if link is None:
            self.ask_page()
            self.show()
        else:  # (as it was saved; shown only when it takes a moment: a small file loads in a blink)
            app._finding = self
            self.bind("<Destroy>", lambda e: e.widget is self and getattr(app, "_finding", None) is self
                      and setattr(app, "_finding", None))
            self.start(link["mode"] == "memory" and L.can_keep(mf, free_memory()))
            self.after(REOPEN_SHOW_MS, lambda: self.winfo_exists() and self.show())

    def show(self):
        if self.winfo_ismapped():
            return
        app = self.app
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "load_midi")
        self.deiconify()
        self.grab_set()

    def clear(self):
        for w in self.body.winfo_children() + self.buttons.winfo_children():
            w.destroy()

    def name_row(self, path):
        """Each page's first row: the file's name, cut with "…" (whole name in its tip) so a long name never
        widens the window; every page the same width."""
        ttk.Frame(self.body, width=WIDTH, height=0).pack(anchor="w")
        ShortLabel(self.body, text=os.path.basename(path), font=look.font(10, "bold")).pack(anchor="w", fill="x")

    # ------------------------------------------------------------------ 1: the question

    def ask_page(self):
        mf = self.mf
        need, free = L.memory_need(mf), free_memory()
        keep_ok = L.can_keep(mf, free)
        self.name_row(mf.path)
        ttk.Label(self.body, text=tr("load.file_info", size=size_text(mf.size),
                                     tracks=trn("load.tracks", len(mf.tracks), n=f"{len(mf.tracks):,}"),
                                     ppq=mf.ppq)).pack(anchor="w", pady=(2, 10))
        ttk.Label(self.body, text=tr("load.how")).pack(anchor="w")
        indexed = saved.has_index(mf)  # (a saved index: reading from the file opens in a moment, so it's picked)
        self.keep = tk.BooleanVar(self, value=keep_ok and not indexed)
        ttk.Radiobutton(self.body, text=tr("load.from_file"), variable=self.keep, value=False).pack(anchor="w",
                                                                                                    pady=(6, 0))
        ttk.Label(self.body, text=tr("load.from_file_info"), foreground=look.HINT, wraplength=440,
                  justify="left").pack(anchor="w", padx=(22, 0))
        if indexed:
            ttk.Label(self.body, text=tr("load.index_found"), foreground=look.GOOD, wraplength=440,
                      justify="left").pack(anchor="w", padx=(22, 0), pady=(2, 0))
        r = ttk.Radiobutton(self.body, text=tr("load.keep", size=size_text(need)), variable=self.keep, value=True)
        r.pack(anchor="w", pady=(8, 0))
        if keep_ok:
            info = tr("load.keep_info", free=size_text(free))
        else:
            r.state(["disabled"])
            info = (tr("load.keep_no", need=size_text(need + L.SPARE), free=size_text(free)) if free is not None
                    else tr("load.keep_unknown"))
        ttk.Label(self.body, text=info, foreground=look.HINT, wraplength=440, justify="left").pack(anchor="w",
                                                                                               padx=(22, 0))
        if self.app.shapes:
            ttk.Label(self.body, text=tr("load.new_project"), foreground=look.HINT, wraplength=460,
                      justify="left").pack(anchor="w", pady=(10, 0))
        ttk.Button(self.buttons, text=tr("load.cancel"), command=self.cancel).pack(side="right")
        go = ttk.Button(self.buttons, text=tr("load.load"), command=lambda: self.start(self.keep.get()))
        go.pack(side="right", padx=(0, 6))
        go.focus_set()
        self.bind("<Return>", lambda e: self.start(self.keep.get()))

    # ------------------------------------------------------------------ 2: reading

    def start(self, keep):
        self.unbind("<Return>")
        self.clear()
        self.name_row(self.mf.path)
        self.steps = {}
        for key in ("wait", "read", "overview", "done"):
            row = ttk.Label(self.body, text="", width=60)
            row.pack(anchor="w", pady=(4 if key == "wait" else 1, 0))
            self.steps[key] = row
            if key == "read":  # (its counts on a line of their own: Shinki's cut the step line off)
                self.count = ttk.Label(self.body, text="", foreground=look.HINT)
                self.count.pack(anchor="w", padx=(18, 0))
        self.bar = ttk.Progressbar(self.body, length=440, maximum=1000)
        self.bar.pack(anchor="w", pady=(10, 2))
        self.info = ttk.Label(self.body, text="", foreground=look.HINT)
        self.info.pack(anchor="w")
        ttk.Button(self.buttons, text=tr("load.cancel"), command=self.cancel).pack(side="right")
        self.loader = L.Loader(self.mf, keep)
        self.thread = threading.Thread(target=self.loader.run, daemon=True, name="load midi")
        self.thread.start()
        self.poll()

    def drop(self):
        """Another project opened while this one's MIDI loads again: its reading stops, the window goes, nothing
        said (find_loaded starts the new project's own)."""
        if self.loader is not None:
            self.loader.cancelled = True
        self.destroy()

    def poll(self):
        if not self.winfo_exists():  # (dropped: drop())
            return
        ld = self.loader
        stage = ld.stage
        order = ["wait", "read", "overview", "done"]
        at = order.index(stage)
        took = time.perf_counter() - ld.started
        texts = {"wait": tr("load.step_wait"),
                 "read": tr("load.step_read"),
                 "overview": tr("load.step_overview"), "done": tr("load.step_done" if self.link is None else "load.step_again")}
        for i, key in enumerate(order):
            if key == "wait" and at > 0 and took < 0.2:
                self.steps[key].config(text="")  # (the speed-ups were ready: no line for them)
                continue
            mark = "✓ " if i < at else ("▶ " if i == at else "   ")
            self.steps[key].config(text=mark + texts[key], foreground=look.DARK_TEXT if i <= at else look.HINT)
        self.count.config(text=tr("load.step_read_count", done=f"{ld.tracks_done:,}", tracks=f"{len(self.mf.tracks):,}",
                                  notes=f"{ld.notes_found:,}") if at >= 1 else "")
        if stage == "read" and ld.total:
            share = ld.done / ld.total
            self.bar.config(value=int(1000 * share))
            speed = ld.done / max(took, 1e-3)
            left = (ld.total - ld.done) / speed if speed and share > 0.02 else None
            self.info.config(text=tr("load.speed", mb=f"{speed / 1e6:,.0f}", done=size_text(ld.done),
                                     total=size_text(ld.total))
                             + ("  " + tr("load.left", s=f"{left:.0f}") if left is not None and left >= 2 else ""))
        if stage != "done":
            self.after(POLL_MS, self.poll)
            return
        self.finished()

    def finished(self):
        ld = self.loader
        if isinstance(ld.error, Cancelled) or ld.cancelled:  # (Cancel in the last step too: the project stays, hunt)
            self.destroy()
            if self.link is not None:
                said_without(self.app)
            return
        if ld.error is not None:
            e = ld.error
            if isinstance(e, MemoryError):
                text = tr("load.out_of_memory")
            elif isinstance(e, MidiError):
                text = tr(e.key, **e.info)
            else:
                from files.errors import write_log
                write_log(type(e), e, e.__traceback__, "(Load MIDI)")
                text = tr("load.failed", e=e)
            self.destroy()
            messagebox.showerror(tr("load.title"), text, parent=self.app)
            return
        lm = ld.result
        if self.link is None:
            self.app.attach_midi(lm)
            self.summary_page(lm)
            return
        self.destroy()
        app, link = self.app, self.link
        if app.midi_link is not link or app.loaded is not None:  # (another project opened meanwhile)
            return
        if not link["hash"] or lm.link()["hash"] == link["hash"]:  # (the same file, moved or not)
            app.reattach_midi(lm)
            app.after_idle(lambda: offer_index(app, lm))
            return
        # changed since the project was saved (saved again from another program?): never used quietly
        answer = ask_choice(app, tr("load.title"), tr("load.link_changed", path=lm.path),
                            [("use", tr("load.use_changed")), ("find", tr("load.find_original")),
                             ("keep", tr("load.keep_without"))], icon="warning")
        if answer == "use":
            use_changed(app, lm)
            app.after_idle(lambda: offer_index(app, lm))
        elif answer == "find":
            app.after_idle(lambda: find_loaded(app, pick=True))
        else:
            said_without(app)

    # ------------------------------------------------------------------ 3: summary

    def summary_page(self, lm):
        self.clear()
        c = lm.counts()
        self.name_row(lm.path)
        lines =[tr("load.sum_time", s=f"{c['seconds']:.2f}" if c['seconds'] < 1 else f"{c['seconds']:.1f}"),
                 trn("load.sum_notes", c["notes"], n=f"{c['notes']:,}"),
                 tr("load.sum_tracks", tracks=f"{c['tracks']:,}", notes=f"{c['with_notes']:,}"),
                 trn("load.sum_channels", c["channels"], n=c["channels"]),
                 trn("load.sum_tempos", c["tempos"], n=f"{c['tempos']:,}")]
        if lm.mode == "memory":
            lines.append(tr("load.sum_memory", size=size_text(lm.held_bytes() + lm.index_bytes())))
        else:
            lines.append(tr("load.sum_index", size=size_text(lm.index_bytes())))
        notes = []
        if c["zero"]:
            notes.append(trn("load.sum_zero", c["zero"], n=f"{c['zero']:,}"))
        if c["hang"]:
            notes.append(trn("load.sum_hang", c["hang"], n=f"{c['hang']:,}"))
        if c["orphan"]:
            notes.append(trn("load.sum_orphan", c["orphan"], n=f"{c['orphan']:,}"))
        if c["keys_256"]:
            notes.append(tr("load.sum_256"))
        if c["resynced"]:
            notes.append(tr("load.sum_resynced", tracks=", ".join(str(n + 1) for n in c["resynced"][:10])))
        if c["damaged"]:
            notes.append(tr("load.sum_damaged", tracks=", ".join(str(n + 1) for n in c["damaged"][:10])))
        if lm.from_index:
            lines.append(tr("load.sum_from_index"))
        for t in lines:
            ttk.Label(self.body, text=t).pack(anchor="w", pady=(2, 0))
        for t in notes:
            ttk.Label(self.body, text=t, foreground=look.WARN, wraplength=460, justify="left").pack(anchor="w",
                                                                                                 pady=(6, 0))
        never = None
        if saved.offer(lm):  # (a slow load: its index kept? asked once, step 12)
            never = self.index_offer(lm)

        def close():
            if never is not None and never.get():
                saved.decline(lm.path)
            self.destroy()
        ok = ttk.Button(self.buttons, text=tr("load.ok"), command=close)
        ok.pack(side="right")
        ok.focus_set()
        self.bind("<Return>", lambda e: close())
        self.protocol("WM_DELETE_WINDOW", close)
        self.bind("<Escape>", lambda e: close())

    def index_offer(self, lm):
        """The summary's question: save the index (its size), or never ask for this file. Returns the tick's var."""
        box = ttk.Frame(self.body)
        box.pack(anchor="w", fill="x", pady=(12, 0))
        ttk.Label(box, text=tr("load.index_ask", s=f"{lm.seconds:.0f}", size=size_text(lm.index_bytes() + kept_bytes(lm))),
                  wraplength=460, justify="left").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        never = tk.BooleanVar(self, value=False)

        def save():
            self.config(cursor="watch")
            self.update_idletasks()
            try:
                size = saved.save_index(lm)
            except OSError as e:
                done.config(text=tr("load.index_failed", why=e.strerror or str(e)), foreground=look.WARN)
            else:
                done.config(text=tr("load.index_saved", size=size_text(size)))
                for w in (button, tick):
                    w.state(["disabled"])
            finally:
                self.config(cursor="")
        button = ttk.Button(row, text=tr("load.index_save"), command=save)
        button.pack(side="left")
        tick = ttk.Checkbutton(row, text=tr("load.index_never"), variable=never)
        tick.pack(side="left", padx=(10, 0))
        done = ttk.Label(box, text="", foreground=look.HINT)
        done.pack(anchor="w", pady=(4, 0))
        return never

    def cancel(self):
        if self.loader is None:
            self.destroy()
            return
        self.loader.cancelled = True  # (poll sees it end and closes the window)
