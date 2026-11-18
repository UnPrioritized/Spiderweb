"""Settings → New projects (user 2026-11-18: its own page, set right there): the song settings New and Load MIDI
start with (files/new_project.py), laid out like the Project panel's rows with the same names and tips; every
change saved at once like the rest of Settings. A master tick (off = New keeps the open project's, the old way),
"Use the open project's settings" (fills the page from the open project), "Fit view on New"."""

import os
import tkinter as tk
from tkinter import filedialog, ttk

from files import new_project
from files.about import HERE
from files.domino_clip import DOMINO_STARTS
from files.lang import tr
from files.mathexpr import calc_int
from notes.engine import CHANNEL_MODES, SPLITS
from notes.paths import KEYS
from window import look
from window.widgets import Tooltip

BOOLS = ("use_ch10", "ch10_order", "new_rounds", "pad_rounds", "single_split", "multi_cut", "multi_later",
         "multi_stretch", "edo31", "edo31_audio", "edo31_lanes")


def build(win, f):
    """win = the SettingsWindow, f = its Form for the page."""
    from window.app import CHANNEL_CHOICES, PPQS, SPLIT_CHOICES  # (the Project panel's own lists)
    app = win.app
    page = f.page
    f.head(tr("settings.head_new"))
    use = tk.BooleanVar(value=new_project.used_on())
    greyed = []  # every control the master tick greys

    def keep(w):
        greyed.append(w)
        return w

    v = {k: tk.BooleanVar() for k in BOOLS}
    ppq = tk.StringVar()
    keys = tk.StringVar()
    out = tk.StringVar()
    mode = tk.StringVar()
    loading = [False]  # (show() setting the boxes: not a change to save)

    def save(*a):
        if loading[0]:
            return
        data = {k: var.get() for k, var in v.items()}
        try:
            data["ppq"] = str(calc_int(ppq.get(), 1, 65535))
        except ValueError:  # (a wrong PPQ: not saved, the box goes back on leaving it)
            pass
        data["keys"] = KEYS[1] if keys.get() == str(KEYS[1]) else KEYS[0]
        data["channel_mode"] = mode.get() if mode.get() in CHANNEL_MODES else "single"
        data["channel_split"] = SPLITS[max(split_box.current(), 0)]
        data["domino_start"] = DOMINO_STARTS[max(domino_box.current(), 0)][0]
        data["output"] = out.get().strip()  # (empty = spiderweb.mid in Spiderweb's output folder)
        status.grid_remove()  # (the button's "Saved." is about the button only)
        try:
            new_project.save(data)
        except OSError:
            status.config(text=tr("settings.new_not_saved"), foreground=look.WARN)
            status.grid()
        sync()

    def tick(parent, key, text_key, tip_key):
        c = keep(ttk.Checkbutton(parent, text=tr(text_key), variable=v[key], takefocus=False, command=save))
        Tooltip(c, tr(tip_key))
        return c

    def switched():
        new_project.set_used(use.get())
        for w in greyed:
            if w.winfo_exists():
                w.state(["!disabled"] if use.get() else ["disabled"])
        if getattr(win, "new_page", None):
            sync()  # (the panel's own greying on top)
    f.row(None, win.tick(page, "settings.new_use", use, switched, tip=tr("settings.new_use_tip")))

    # PPQ: typed or picked, taken on Enter / leaving / a pick (like the panel's box)
    ppq_box = keep(ttk.Combobox(page, textvariable=ppq, values=PPQS, width=7, height=12))
    f.row(tr("settings.new_ppq"), ppq_box)

    def ppq_done(e=None):
        try:
            calc_int(ppq.get(), 1, 65535)
        except ValueError:
            ppq.set((new_project.read() or {}).get("ppq", "960"))
            return
        save()
    for seq in ("<Return>", "<FocusOut>", "<<ComboboxSelected>>"):
        ppq_box.bind(seq, ppq_done, add="+")

    krow = ttk.Frame(page)
    keys_box = keep(ttk.Combobox(krow, textvariable=keys, values=[str(k) for k in KEYS], state="readonly", width=7))
    keys_box.pack(side="left")
    keys_box.bind("<<ComboboxSelected>>", save)
    Tooltip(keys_box, tr("app.128_the_standard_keys_0_127"))
    edo = ttk.Frame(krow)  # (shown with 256 keys, like the panel)
    tick(edo, "edo31", "app.edo31", "app.edo31_tip").pack(side="left", padx=(6, 0))
    audio = tick(edo, "edo31_audio", "app.edo31_audio", "app.edo31_audio_tip")
    audio.pack(side="left", padx=(4, 0))
    f.row(tr("settings.new_keys"), krow, widget_tip=False)

    orow = ttk.Frame(page)
    out_box = keep(ttk.Entry(orow, textvariable=out, width=34))
    out_box.pack(side="left", fill="x", expand=True)
    for seq in ("<Return>", "<FocusOut>"):
        out_box.bind(seq, save, add="+")

    def browse():
        cur = out.get()
        path = filedialog.asksaveasfilename(parent=win, title=tr("project.midi_output"),
                                            initialdir=os.path.dirname(cur) or HERE, initialfile=os.path.basename(cur),
                                            defaultextension=".mid", filetypes=[(tr("project.midi_file"), "*.mid *.midi")])
        if path:
            out.set(os.path.normpath(path))
            save()
    keep(ttk.Button(orow, text="…", width=3, takefocus=False, command=browse)).pack(side="left", padx=(3, 0))
    f.row(tr("settings.new_output"), orow, widget_tip=False)

    # Channels: the panel's rows, each shown when the panel would show it
    ch = ttk.Frame(page)
    f.row(tr("settings.new_channels"), ch, widget_tip=False, pady=(6, 3))
    ch.master.grid_slaves(row=f.r - 1, column=0)[0].grid(sticky="nw")
    for value, text, tip in CHANNEL_CHOICES:
        b = keep(ttk.Radiobutton(ch, text=text, value=value, variable=mode, takefocus=False, command=save))
        b.pack(anchor="w")
        Tooltip(b, tip)
    single = tick(ch, "single_split", "app.single_split", "app.single_split_tip")
    multi = ttk.Frame(ch)
    names = [name for _, name in SPLIT_CHOICES]
    split_box = keep(ttk.Combobox(multi, state="readonly", values=names, width=max(map(len, names))))
    split_box.pack(anchor="w", pady=(2, 0))
    split_box.bind("<<ComboboxSelected>>", save)
    lanes = tick(multi, "edo31_lanes", "app.edo31_lanes", "app.edo31_lanes_tip")
    ch10 = tick(multi, "use_ch10", "app.use_ch10", "app.use_ch10_tip")
    order = tick(multi, "ch10_order", "app.ch10_order", "app.ch10_order_tip")
    rounds = tick(multi, "new_rounds", "app.new_rounds", "app.new_rounds_tip")
    pad = tick(multi, "pad_rounds", "app.pad_rounds", "app.pad_rounds_tip")
    cut = tick(multi, "multi_cut", "app.multi_cut", "app.multi_cut_tip")
    more = ttk.Frame(multi)
    wins = [tr("app.multi_earlier_win"), tr("app.multi_later_win")]
    win_box = keep(ttk.Combobox(more, state="readonly", values=wins, width=max(map(len, wins))))
    win_box.pack(anchor="w", pady=(1, 0))
    win_box.bind("<<ComboboxSelected>>", lambda e: (v["multi_later"].set(win_box.current() == 1), save()))
    Tooltip(win_box, tr("app.multi_win_tip"))
    tick(more, "multi_stretch", "app.multi_stretch", "app.multi_stretch_tip").pack(anchor="w", pady=(2, 0))
    multi_rows = [(lanes, None), (ch10, None), (order, None), (rounds, None), (pad, None), (cut, None), (more, 18)]

    names = [name for _, name in DOMINO_STARTS]
    domino_box = keep(ttk.Combobox(page, state="readonly", values=names))
    domino_box.bind("<<ComboboxSelected>>", save)
    f.row(tr("settings.new_domino"), domino_box, tip=tr("app.where_copied_and_pasted_notes_start"), pady=(6, 3))

    def sync():
        """Rows shown as the Project panel shows them."""
        tall = keys.get() == str(KEYS[1])
        if tall and not edo.winfo_manager():
            edo.pack(side="left")
        elif not tall and edo.winfo_manager():
            edo.pack_forget()
        m = mode.get()
        single.pack_forget()
        multi.pack_forget()
        if m == "single":
            single.pack(anchor="w", padx=(18, 0))
        elif m == "auto":
            multi.pack(anchor="w", padx=(18, 0))
        shown = {lanes: tall and v["edo31"].get(), ch10: True, order: v["use_ch10"].get(), rounds: True,
                 pad: v["new_rounds"].get(), cut: True, more: v["multi_cut"].get()}
        for w, pad_x in multi_rows:
            w.pack_forget()
        for w, pad_x in multi_rows:
            if shown[w]:
                w.pack(anchor="w", padx=(pad_x or 0, 0), pady=(2, 0))
        # greyed as the panel greys them (sync_edo, sync_ch10_grey): Audio needs 31-EDO; with "pitch kept" on, channel
        # 10 and its order change nothing
        lanes_on = tall and v["edo31"].get() and v["edo31_lanes"].get()
        for w, ok in ((audio, v["edo31"].get()), (ch10, not lanes_on), (order, not lanes_on)):
            w.state(["!disabled"] if use.get() and ok else ["disabled"])
        if getattr(win, "new_page", None):  # (a change after the window is built: its height may grow)
            win.fit_pages()

    def show():
        """The page set from the saved settings."""
        s = new_project.read() or {}
        loading[0] = True
        try:
            for k, var in v.items():
                var.set(bool(s.get(k, k in ("pad_rounds", "edo31_lanes"))))
            ppq.set(s.get("ppq", "960"))
            keys.set(str(s.get("keys", KEYS[0])))
            out.set(s.get("output", ""))
            mode.set(s.get("channel_mode", "single"))
            split_box.current(SPLITS.index(s["channel_split"]) if s.get("channel_split") in SPLITS else 0)
            win_box.current(int(v["multi_later"].get()))
            starts = [x for x, _ in DOMINO_STARTS]
            domino_box.current(starts.index(s["domino_start"]) if s.get("domino_start") in starts else 0)
        finally:
            loading[0] = False
        sync()

    def take():
        try:
            new_project.save(app.project_for_new())
        except OSError:
            status.config(text=tr("settings.new_not_saved"), foreground=look.WARN)
        else:
            status.config(text=tr("settings.new_saved"), foreground=look.HINT)
        status.grid()
        show()
    b = keep(ttk.Button(page, text=tr("settings.new_take"), takefocus=False, command=take))
    f.row(None, b, tip=tr("settings.new_take_tip"), pady=(10, 3))
    status = f.note()
    status.grid_remove()
    fit = tk.BooleanVar(value=new_project.fit_view())
    f.row(None, win.tick(page, "settings.new_fit", fit, lambda: new_project.set_fit_view(fit.get()),
                         tip=tr("settings.new_fit_tip")), pady=(10, 3))
    f.note(tr("settings.new_open_note"))
    show()
    switched()
    sync()  # (the panel's greying again, over the master tick's)

    def take_typed():
        """Settings closing: a PPQ / output typed without Enter is kept (like the voice limit and Opacity)."""
        if ppq_box.winfo_exists() and use.get():
            ppq_done()
            save()
    win.take_new_typed = take_typed
    win.new_page = {"show": show, "ppq": ppq_box, "keys": keys_box, "out": out_box, "mode": mode,
                    "split": split_box, "domino": domino_box, "take": b, "use": use, "vars": v}  # (tests)
