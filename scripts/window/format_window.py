"""Image to notes: the colour list format editor (its own small window, user). Start / Each colour / End per list,
what goes between colours, tags to click in, and "What Copy gives" following every key press: known tags blue,
unknown ones red (in the output too), {{ }} grey; spaces / tabs at a line's start or end tinted and a faint ↵ at
line ends (shown only, never copied; user found hidden spaces confusing). Built-in formats can't change: editing one
makes "<name> (mine)". The formats themselves: files/colour_list.py."""

import copy
import re
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from files import clipboard
from files import colour_list as CL
from files.lang import tr
from window import look

WS = re.compile(r"^[ \t]+|[ \t]+$", re.M)
TAGS = [("{n}", "fmt.tag_n"), ("{n0}", "fmt.tag_n0"), ("{n:2}", "fmt.tag_n2"), ("{hex}", "fmt.tag_hex"),
        ("{HEX}", "fmt.tag_HEX"), ("{r} {g} {b}", "fmt.tag_rgb"), ("{{  }}", "fmt.tag_plain"),
        ("{a}", "fmt.tag_a")]


class FormatWindow(tk.Toplevel):
    """colours(): the colours to show ("RRGGBB" list), by / use10: how {n} counts; done(name): called with the
    format picked when the window closes."""

    def __init__(self, parent, colours, by, use10, picked, done):
        super().__init__(parent)
        self.title(tr("fmt.title"))
        self.transient(parent)
        self.colours, self.by, self.use10, self.done = colours, by, use10, done
        self.built = CL.built_in()
        self.yours = CL.load_formats()
        self.fmt = copy.deepcopy(self.find(picked) or self.built[0])
        self.cur = 0  # the list shown
        self.show_ws = tk.BooleanVar(value=True)
        self.build()
        self.show_format()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())

    def find(self, name):
        return next((f for f in self.built + self.yours if f["name"] == name), None)

    def is_built(self):
        return any(f["name"] == self.fmt["name"] for f in self.built)

    # ------------------------------------------------------------ the window

    def build(self):
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        r = ttk.Frame(box)
        r.pack(fill="x")
        ttk.Label(r, text=tr("fmt.format")).pack(side="left")
        self.box = ttk.Combobox(r, width=32, state="readonly")
        self.box.pack(side="left", padx=6)
        self.box.bind("<<ComboboxSelected>>", lambda e: self.pick(self.box.get()))
        ttk.Button(r, text=tr("fmt.save_as"), command=self.save_as).pack(side="left")
        self.rename_btn = ttk.Button(r, text=tr("fmt.rename"), command=self.rename)
        self.rename_btn.pack(side="left", padx=4)
        self.delete_btn = ttk.Button(r, text=tr("fmt.delete"), command=self.delete)
        self.delete_btn.pack(side="left")
        ttk.Label(box, text=tr("fmt.built_in_note"), foreground=look.HINT).pack(anchor="w", pady=(2, 8))
        r = self.lists_row = ttk.Frame(box)
        r.pack(fill="x")
        mid = ttk.Frame(box)
        mid.pack(fill="x", pady=(8, 0))
        self.edit = ttk.LabelFrame(mid, text="", padding=6)
        self.edit.pack(side="left", fill="both", expand=True)
        self.texts = {}
        for k in ("start", "each", "end"):
            ttk.Label(self.edit, text=tr("fmt." + k)).pack(anchor="w")
            t = tk.Text(self.edit, height=2, width=50, font=look.mono(10), undo=True)
            t.pack(fill="x", pady=(0, 6))
            for name, kw in (("tag", dict(foreground=look.FMT_TAG)), ("plain", dict(foreground=look.FMT_PLAIN, background=look.FMT_PLAIN_BG)),
                             ("bad", dict(foreground=look.BAD_MARK_TEXT, background=look.BAD_MARK)), ("ws", dict(background=look.FMT_SPACE))):
                t.tag_config(name, **kw)
            t.bind("<KeyRelease>", lambda e, k=k: self.typed(k))
            self.texts[k] = t
        r = ttk.Frame(self.edit)
        r.pack(fill="x")
        ttk.Label(r, text=tr("fmt.between")).pack(side="left")
        self.sep = tk.StringVar()
        for v in ("line", "comma", "space", "none"):
            ttk.Radiobutton(r, text=tr("fmt.sep_" + v), variable=self.sep, value=v,
                            command=lambda: self.change("sep", self.sep.get())).pack(side="left", padx=3)
        r = ttk.Frame(self.edit)
        r.pack(fill="x", pady=(3, 0))
        ttk.Label(r, text="", width=15).pack(side="left")
        ttk.Radiobutton(r, text=tr("fmt.custom"), variable=self.sep, value="custom",
                        command=lambda: self.change("sep", "custom")).pack(side="left", padx=3)
        self.custom = tk.Text(r, width=14, height=1, wrap="none", font=look.mono(10))
        self.custom.tag_config("ws", background=look.FMT_SPACE)
        self.custom.pack(side="left")
        self.custom.bind("<Return>", lambda e: "break")
        self.custom.bind("<KeyRelease>", lambda e: self.change("custom", self.custom.get("1.0", "end-1c")))
        ttk.Label(self.edit, text=tr("fmt.codes"), foreground=look.HINT, font=look.mono(9), justify="left").pack(
            anchor="w", pady=(2, 0))
        r = ttk.Frame(self.edit)
        r.pack(fill="x", pady=(4, 0))
        ttk.Label(r, text=tr("fmt.pad")).pack(side="left")
        self.pad = tk.StringVar()
        sb = ttk.Spinbox(r, from_=0, to=256, width=5, textvariable=self.pad, command=self.pad_typed)
        sb.pack(side="left", padx=4)
        sb.bind("<KeyRelease>", lambda e: self.pad_typed())
        ttk.Label(r, text=tr("fmt.filler")).pack(side="left")
        self.filler = ttk.Entry(r, width=8)
        self.filler.pack(side="left", padx=4)
        self.filler.bind("<KeyRelease>", lambda e: self.filler_typed())

        tags = ttk.LabelFrame(mid, text=tr("fmt.tags"), padding=6)
        tags.pack(side="left", fill="y", padx=(8, 0))
        for t, what in TAGS:
            rr = ttk.Frame(tags)
            rr.pack(fill="x", pady=1)
            tk.Button(rr, text=t, font=look.mono(9), width=11, relief="groove",
                      command=lambda t=t: self.put_tag(t)).pack(side="left")
            ttk.Label(rr, text=tr(what), foreground=look.INFO).pack(side="left", padx=4)
        rr = ttk.Frame(tags)
        rr.pack(fill="x", pady=(2, 0))
        ttk.Label(rr, text="{a} =", width=13, anchor="e").pack(side="left")
        self.alpha = ttk.Entry(rr, width=6)
        self.alpha.pack(side="left", padx=4)
        self.alpha.bind("<KeyRelease>", lambda e: self.change("alpha", self.alpha.get()))
        ttk.Checkbutton(tags, text=tr("fmt.show_ws"), variable=self.show_ws, command=self.refresh).pack(
            anchor="w", pady=(6, 0))

        ttk.Label(box, text=tr("fmt.output")).pack(anchor="w", pady=(8, 2))
        self.out = tk.Text(box, height=9, font=look.mono(10), background=look.FIELD_SOFT, wrap="none")
        self.out.pack(fill="both", expand=True)
        self.out.tag_config("bad", foreground=look.BAD_MARK_TEXT, background=look.BAD_MARK)
        self.out.tag_config("ws", background=look.FMT_SPACE)
        self.status = ttk.Label(box, text="", foreground=look.INFO)
        self.status.pack(anchor="w", pady=(4, 0))
        r = ttk.Frame(box)
        r.pack(fill="x", pady=(6, 0))
        ttk.Button(r, text=tr("fmt.close"), command=self.close).pack(side="right")
        ttk.Button(r, text=tr("fmt.copy"), command=self.copy).pack(side="right", padx=6)

    def show_format(self):
        """Every control shows self.fmt (list self.cur)."""
        names = [f["name"] for f in self.built + self.yours]
        if self.fmt["name"] not in names:
            names.append(self.fmt["name"])
        self.box.config(values=names)
        self.box.set(self.fmt["name"])
        mine = not self.is_built() and any(f["name"] == self.fmt["name"] for f in self.yours)
        for b in (self.rename_btn, self.delete_btn):
            b.config(state="normal" if mine else "disabled")
        for w in self.lists_row.winfo_children():
            w.destroy()
        ttk.Label(self.lists_row, text=tr("fmt.lists")).pack(side="left")
        for k in range(len(self.fmt["lists"])):
            tk.Button(self.lists_row, text=tr("fmt.list_n", n=k + 1), width=8,
                      relief="sunken" if k == self.cur else "raised",
                      command=lambda k=k: self.show_list(k)).pack(side="left", padx=2)
        ttk.Button(self.lists_row, text=tr("fmt.add_list"), command=self.add_list).pack(side="left", padx=(8, 2))
        ttk.Button(self.lists_row, text=tr("fmt.remove_list"), command=self.remove_list,
                   state="normal" if len(self.fmt["lists"]) > 1 else "disabled").pack(side="left")
        self.edit.config(text=tr("fmt.list_n", n=self.cur + 1))
        lst = self.fmt["lists"][self.cur]
        for k, t in self.texts.items():
            t.delete("1.0", "end")
            t.insert("1.0", lst.get(k, ""))
        self.sep.set(self.fmt["sep"])
        self.custom.delete("1.0", "end")
        self.custom.insert("1.0", self.fmt.get("custom", ""))
        self.alpha.delete(0, "end")
        self.alpha.insert(0, self.fmt.get("alpha", "FF"))
        self.pad.set(str(self.fmt.get("pad", 0)))
        self.filler.delete(0, "end")
        self.filler.insert(0, self.fmt.get("filler", "000000"))
        self.refresh()

    def show_list(self, k):
        self.cur = k
        self.show_format()

    # ------------------------------------------------------------ editing

    def mine(self):
        """Editing a built-in format makes it the user's own copy first."""
        if self.is_built():
            base = tr("fmt.mine", name=self.fmt["name"])
            name, n = base, 2
            while self.find(name):
                name, n = f"{base} {n}", n + 1
            self.fmt["name"] = name
            self.show_format_name()

    def show_format_name(self):
        names = [f["name"] for f in self.built + self.yours] + [self.fmt["name"]]
        self.box.config(values=list(dict.fromkeys(names)))
        self.box.set(self.fmt["name"])

    def typed(self, k):
        self.change_list(k, self.texts[k].get("1.0", "end-1c"))

    def change_list(self, k, v):
        if self.fmt["lists"][self.cur].get(k, "") != v:
            self.mine()
            self.fmt["lists"][self.cur][k] = v
        self.refresh()

    def change(self, k, v):
        if self.fmt.get(k) != v:
            self.mine()
            self.fmt[k] = v
        self.refresh()

    def pad_typed(self):
        try:
            n = max(0, min(256, int(self.pad.get())))
        except ValueError:
            return
        self.change("pad", n)

    def filler_typed(self):
        v = self.filler.get().strip().lstrip("#")
        if re.fullmatch(r"[0-9A-Fa-f]{6}", v):
            self.change("filler", v.upper())

    def put_tag(self, t):
        """A tag button: the tag goes where the text cursor is in Each colour."""
        t = t.split()[0] if t != "{{  }}" else "{{}}"
        if t == "{r}":
            t = "{r} {g} {b}"
        box = self.texts["each"]
        box.insert("insert", t)
        box.focus_set()
        self.typed("each")

    def add_list(self):
        self.mine()
        self.fmt["lists"].append({"start": "\n", "each": self.fmt["lists"][-1]["each"], "end": ""})
        self.cur = len(self.fmt["lists"]) - 1
        self.show_format()

    def remove_list(self):
        if len(self.fmt["lists"]) > 1:
            self.mine()
            del self.fmt["lists"][self.cur]
            self.cur = min(self.cur, len(self.fmt["lists"]) - 1)
            self.show_format()

    def pick(self, name):
        f = self.find(name)
        if f:
            self.fmt, self.cur = copy.deepcopy(f), 0
            self.show_format()

    # ------------------------------------------------------------ saving

    def store(self, name):
        """The format being edited saved as `name` (replacing one of the user's with that name)."""
        if any(f["name"].casefold() == name.casefold() for f in self.built):
            messagebox.showwarning(tr("fmt.title"), tr("fmt.built_in_name"), parent=self)
            return False
        f = CL.clean_format(dict(self.fmt, name=name))
        if not f:
            return False
        items = [g for g in CL.load_formats() if g["name"].casefold() != name.casefold()] + [f]
        try:
            CL.save_formats(items)
        except OSError as e:
            messagebox.showerror(tr("fmt.title"), tr("fmt.cant_save", path=CL.FORMATS_FILE, e=e), parent=self)
            return False
        self.yours, self.fmt = items, copy.deepcopy(f)
        self.show_format()
        return True

    def save_as(self):
        name = simpledialog.askstring(tr("fmt.title"), tr("fmt.name_ask"), parent=self,
                                      initialvalue="" if self.is_built() else self.fmt["name"])
        if name and name.strip():
            self.store(name.strip()[:100])

    def rename(self):
        old = self.fmt["name"]
        name = simpledialog.askstring(tr("fmt.title"), tr("fmt.name_ask"), parent=self, initialvalue=old)
        if name and name.strip() and name.strip() != old:
            if self.store(name.strip()[:100]):
                items = [g for g in self.yours if g["name"] != old]
                CL.save_formats(items)
                self.yours = items
                self.show_format()

    def delete(self):
        name = self.fmt["name"]
        if not messagebox.askyesno(tr("fmt.title"), tr("fmt.delete_ask", name=name), parent=self):
            return
        items = [g for g in CL.load_formats() if g["name"] != name]
        try:
            CL.save_formats(items)
        except OSError as e:
            messagebox.showerror(tr("fmt.title"), tr("fmt.cant_save", path=CL.FORMATS_FILE, e=e), parent=self)
            return
        self.yours, self.fmt, self.cur = items, copy.deepcopy(self.built[0]), 0
        self.show_format()

    # ------------------------------------------------------------ the output

    def mark(self, t):
        """Spaces / tabs at a line's start or end tinted, a faint ↵ at every line end (shown only)."""
        for name in t.window_names():
            t.delete(t.index(name))
        t.tag_remove("ws", "1.0", "end")
        if not self.show_ws.get():
            return
        s = t.get("1.0", "end-1c")
        for m in WS.finditer(s):
            t.tag_add("ws", "1.0+%dc" % m.start(), "1.0+%dc" % m.end())
        for line in range(1, s.count("\n") + 1):
            t.window_create("%d.end" % line, window=tk.Label(t, text="↵", fg=look.FMT_RETURN, bg=t.cget("background"),
                                                             bd=0, padx=0, pady=0, font=look.mono(9)))

    def tag_colours(self):
        t = self.texts["each"]
        for name in ("tag", "bad", "plain"):
            t.tag_remove(name, "1.0", "end")
        s = t.get("1.0", "end-1c")
        for m in CL.TAG.finditer(s):
            g = m.group()
            name = "plain" if g in ("{{", "}}") else "tag" if CL.GOOD.match(g) else "bad"
            t.tag_add(name, "1.0+%dc" % m.start(), "1.0+%dc" % m.end())

    def refresh(self):
        boxes = list(self.texts.values()) + [self.custom]
        for t in boxes:  # (marks out first: they'd count as characters in the tag positions)
            for name in t.window_names():
                t.delete(t.index(name))
        self.tag_colours()
        for t in boxes:
            self.mark(t)
        ok = CL.unescape(self.fmt.get("custom", ""))[1]
        self.custom.config(background=look.FIELD if ok else look.ERROR_BG)
        pieces = CL.write(self.fmt, self.colours(), self.by(), self.use10())
        out = self.out
        out.config(state="normal")
        out.delete("1.0", "end")
        for text, good in pieces:
            out.insert("end", text, () if good else ("bad",))
        self.mark(out)
        out.config(state="disabled")
        bad = CL.unknown(pieces)
        n = len(self.colours())
        self.status.config(text=tr("fmt.status", n=n, lists=len(self.fmt["lists"])) +
                           (tr("fmt.status_bad", bad=bad) if bad else ""), foreground=look.FMT_BAD if bad else look.INFO)

    def copy(self):
        copy_colours(self, self.fmt, self.colours(), self.by(), self.use10())

    def close(self):
        name = self.fmt["name"] if self.find(self.fmt["name"]) else self.box.get()
        self.destroy()
        self.done(name if self.find(name) else None)


def copy_colours(parent, fmt, colours, by, use10):
    """The colour list in this format -> the clipboard (asks first when it has unknown tags). True if copied."""
    pieces = CL.write(fmt, colours, by, use10)
    bad = CL.unknown(pieces)
    if bad and not messagebox.askyesno(tr("fmt.title"), tr("fmt.copy_anyway", bad=bad), parent=parent):
        return False
    if not clipboard.put_text(CL.text_of(pieces)):
        messagebox.showwarning(tr("fmt.title"), tr("fmt.clipboard_busy"), parent=parent)
        return False
    return True
