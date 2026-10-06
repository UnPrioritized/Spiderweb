"""Image to notes: Paste colours (its own window). Text with colours in it (a whole player settings file is fine) ->
the lists found in it (grouped by how their lines look; switched-off lines skipped), one picked, its colours put in
the picture's slots (by the channel number written on each line when colouring by channel, else in order; channel
10 left out unless it's used), then locked or only a starting point. Reading: files/colour_list.read_colours."""

import tkinter as tk
from tkinter import ttk

from files import clipboard
from files import colour_list as CL
from files.lang import tr


class PasteWindow(tk.Toplevel):
    """by / use10: how the slots are counted; colours_wanted: how many the picture has; done(slots, locked, alpha):
    called with {slot: "RRGGBB"}, True = lock them, alpha = the see-through value found (or None)."""

    def __init__(self, parent, by, use10, colours_wanted, done):
        super().__init__(parent)
        self.title(tr("paste.title"))
        self.transient(parent)
        self.by, self.use10, self.wanted, self.done = by, use10, colours_wanted, done
        self.lists, self.skipped = [], []
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        r = ttk.Frame(box)
        r.pack(fill="x")
        ttk.Label(r, text=tr("paste.intro")).pack(side="left")
        ttk.Button(r, text=tr("paste.from_clipboard"), command=self.from_clipboard).pack(side="right")
        self.text = tk.Text(box, height=12, width=100, font=("Consolas", 9), wrap="none", undo=True)
        self.text.pack(fill="x", pady=(4, 0))
        self.text.tag_config("use", background="#bfe0ff")
        self.text.tag_config("other", background="#e6e6e6")
        self.text.tag_config("off", foreground="#aaa", overstrike=True)
        self.text.bind("<KeyRelease>", lambda e: self.read())
        self.legend = ttk.Label(box, text="", foreground="#666")
        self.legend.pack(anchor="w", pady=(2, 8))
        self.found = ttk.LabelFrame(box, text="", padding=6)
        self.found.pack(fill="x")
        self.pick = tk.IntVar(value=0)
        self.where = ttk.LabelFrame(box, text=tr("paste.where"), padding=6)
        self.where.pack(fill="x", pady=(8, 0))
        f = ttk.LabelFrame(box, text=tr("paste.then"), padding=6)
        f.pack(fill="x", pady=(8, 0))
        self.how = tk.StringVar(value="lock")
        ttk.Radiobutton(f, variable=self.how, value="lock", text=tr("paste.lock")).pack(anchor="w")
        ttk.Radiobutton(f, variable=self.how, value="start", text=tr("paste.start")).pack(anchor="w")
        r = ttk.Frame(box)
        r.pack(fill="x", pady=(8, 0))
        ttk.Button(r, text=tr("paste.cancel"), command=self.destroy).pack(side="right")
        self.use_btn = ttk.Button(r, text=tr("paste.use"), command=self.use)
        self.use_btn.pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.from_clipboard()

    def from_clipboard(self):
        got = clipboard.get_text()
        if got:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", got.replace("\r\n", "\n"))
        self.read()

    def read(self):
        self.lists, self.skipped = CL.read_colours(self.text.get("1.0", "end-1c"))
        if self.pick.get() >= len(self.lists):
            self.pick.set(0)
        self.show()

    def slots(self):
        """{slot: (hex, alpha)} for the picked list."""
        if not self.lists:
            return {}
        found = self.lists[self.pick.get()][1]
        out = {}
        if self.by == "channel" and all(c[5] is not None for c in found):  # by the channel number on each line
            chans = [c + 1 for c in range(16) if self.use10 or c != 9]
            for c in found:
                if c[5] in chans:
                    out[chans.index(c[5])] = (c[3], c[4])
        else:
            for k, c in enumerate(found):
                out[k] = (c[3], c[4])
        return {k: v for k, v in out.items() if k < 16}

    def show(self):
        t = self.text
        for name in ("use", "other", "off"):
            t.tag_remove(name, "1.0", "end")
        for ln in self.skipped:
            t.tag_add("off", "%d.0" % ln, "%d.end" % ln)
        for gi, (_, found) in enumerate(self.lists):
            for ln, a, b, *_ in found:
                t.tag_add("use" if gi == self.pick.get() else "other", "%d.%d" % (ln, a), "%d.%d" % (ln, b))
        self.legend.config(text=tr("paste.legend", n=len(self.skipped)))
        for w in self.found.winfo_children() + self.where.winfo_children():
            w.destroy()
        n = len(self.lists)
        self.found.config(text=tr("paste.found", n=n) if n else tr("paste.none_found"))
        for gi, (pattern, found) in enumerate(self.lists):
            row = ttk.Frame(self.found)
            row.pack(fill="x", pady=2)
            ttk.Radiobutton(row, variable=self.pick, value=gi, command=self.show,
                            text=tr("paste.list", pattern=pattern[:40], n=len(found))).pack(side="left")
            sw = ttk.Frame(row)
            sw.pack(side="right")
            for c in found[:32]:
                tk.Frame(sw, width=14, height=14, background="#" + c[3], highlightthickness=1,
                         highlightbackground="#555").pack(side="left", padx=1)
        slots = self.slots()
        grid = ttk.Frame(self.where)
        grid.pack(anchor="w")
        chans = [c + 1 for c in range(16) if self.use10 or c != 9]
        for k in range(min(16, len(chans) if self.by == "channel" else 16)):
            cell = ttk.Frame(grid)
            cell.pack(side="left", padx=2)
            c = slots.get(k)
            cv = tk.Canvas(cell, width=32, height=24, background="#" + c[0] if c else "#f0f0f0",
                           highlightthickness=1, highlightbackground="#555")
            cv.pack()
            used = c is not None and k < self.wanted
            if c is not None and not used:
                cv.create_line(0, 0, 34, 26, fill="#999")
            ttk.Label(cell, text=(tr("paste.ch", n=chans[k]) if self.by == "channel" else str(k + 1)),
                      foreground="#333" if used else "#999").pack()
        note = (tr("paste.ch10_left") if self.by == "channel" and not self.use10 else "")
        alpha = {c[1] for c in slots.values() if c[1]}
        if alpha:
            note += ("  " if note else "") + tr("paste.alpha", a="/".join(sorted(alpha)))
        if note:
            ttk.Label(self.where, text=note, foreground="#666").pack(anchor="w", pady=(4, 0))
        self.use_btn.config(state="normal" if slots else "disabled")

    def use(self):
        slots = self.slots()
        if not slots:
            return
        alpha = next((c[1] for c in slots.values() if c[1]), None)
        self.done({k: c[0] for k, c in slots.items()}, self.how.get() == "lock", alpha)
        self.destroy()
