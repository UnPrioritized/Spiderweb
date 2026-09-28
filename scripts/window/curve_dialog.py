"""The custom curve formula window (a funnel curve from a formula like x^2) and the user's saved formulas
(spiderweb/curves.json)."""

import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

from files.lang import tr
from notes.bezier import anchor_count, sample
from notes.funnel import formula_curve, preset_curve
from files.mathexpr import formula
from files.project import HERE
from files.safefile import write_text

CURVES_FILE = os.path.join(HERE, "curves.json")
HELP = tr("curve_dialog.x_goes_from_0_at_the")


def load_formulas():
    """[(name, formula)] the user saved."""
    try:
        with open(CURVES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [(str(c["name"]), str(c["formula"])) for c in data.get("curves", [])]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def save_formulas(items):
    write_text(CURVES_FILE, json.dumps({"curves": [{"name": n, "formula": t} for n, t in items]}, indent=1))


class CurveFormulaDialog(tk.Toplevel):
    """preview(curve or None) shows a curve on the roll while typing (None = back to how it was);
    done(curve or None) is called once when it closes (None = cancelled)."""

    def __init__(self, app, preview, done):
        super().__init__(app)
        self.title(tr("curve_dialog.custom_curve_formula"))
        self.transient(app)
        self.resizable(False, False)
        self.preview, self.done = preview, done
        self.curve = None
        self.target = None
        s = self.scale = app.scale
        self.size = int(260 * s)

        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        left = ttk.LabelFrame(body, text=tr("curve_dialog.saved_formulas"), padding=6)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, height=14, width=24, activestyle="none", exportselection=False,
                                  font=("Segoe UI", 9))
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_pick)
        ttk.Button(left, text=tr("curve_dialog.delete"), command=self.delete).pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        right.pack(side="left", fill="both")
        right.columnconfigure(1, weight=1)
        self.name = tk.StringVar()
        self.text = tk.StringVar()
        ttk.Label(right, text=tr("curve_dialog.name")).grid(row=0, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.name, width=30).grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=1)
        ttk.Label(right, text=tr("curve_dialog.y")).grid(row=1, column=0, sticky="w")
        entry = ttk.Entry(right, textvariable=self.text, width=30, font=("Consolas", 11))
        entry.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=1)
        ttk.Label(right, text=HELP, foreground="#777", font=("Segoe UI", 8), wraplength=int(300 * s),
                  justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 6))
        self.canvas = tk.Canvas(right, width=self.size, height=self.size, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#c0c0c0")
        self.canvas.grid(row=3, column=0, columnspan=2)
        self.info = ttk.Label(right, text="", font=("Segoe UI", 9))
        self.info.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        btns = ttk.Frame(right)
        btns.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text=tr("curve_dialog.save"), command=self.save).pack(side="left")
        ttk.Button(btns, text=tr("curve_dialog.cancel"), command=self.cancel).pack(side="right")
        ttk.Button(btns, text=tr("curve_dialog.apply"), command=self.apply).pack(side="right", padx=4)

        self.text.trace_add("write", lambda *_: self.update_curve())
        self.bind("<Return>", lambda e: self.apply())
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.fill_list()
        self.update_curve()
        # over the middle of the main window
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()  # the roll can't change under it while it previews
        entry.focus_set()

    def fill_list(self):
        self.saved = load_formulas()
        self.listbox.delete(0, "end")
        for name, text in self.saved:
            self.listbox.insert("end", f"{name}   ({text})")

    def on_pick(self, _):
        sel = self.listbox.curselection()
        if sel:
            name, text = self.saved[sel[0]]
            self.name.set(name)
            self.text.set(text)

    def update_curve(self):
        """Read the formula, fit the curve and show it (here and on the roll)."""
        try:
            fn = formula(self.text.get())
            self.target = formula_curve(fn)
            self.curve = preset_curve(fn)
            self.info.config(text=tr("curve_dialog.ok_anchors", anchor_count=anchor_count(self.curve['pts'])),
                             foreground="#1d6b1d")
        except ValueError as e:
            self.target = self.curve = None
            self.info.config(text=tr("curve_dialog.can_t_use_it",
                                     e=e) if self.text.get().strip() else tr("curve_dialog.type_a_formula_of_x"),
                             foreground="#c00000" if self.text.get().strip() else "#777")
        self.draw()
        self.preview(self.curve)

    def draw(self):
        c, n = self.canvas, self.size
        c.delete("all")
        pad = int(22 * self.scale)
        w = n - 2 * pad

        def xy(u, f):
            return pad + u * w, n - pad - f * w
        c.create_rectangle(*xy(0, 0), *xy(1, 1), outline="#d8d8d8")
        c.create_line(*xy(0, 0), *xy(1, 1), fill="#d8d8d8", dash=(3, 3))
        font = ("Segoe UI", 8)
        c.create_text(*xy(0, 0), text=tr("curve_dialog.a_start"), anchor="n", fill="#555", font=font)
        c.create_text(pad + w, pad - 3, text=tr("curve_dialog.b_wall_end"), anchor="se", fill="#555", font=font)
        if self.target:
            c.create_line(*[v for u, f in self.target for v in xy(u, f)], fill="#c8c8c8", width=5)
        if self.curve:
            pts = self.curve["pts"]
            c.create_line(*[v for p in sample(pts) for v in xy(*p)], fill="#0050d0", width=2)
            r = 3 * self.scale
            for i in range(len(pts)):
                if i % 3:
                    a = pts[i - 1] if i % 3 == 1 else pts[i + 1]
                    x, y = xy(*pts[i])
                    c.create_line(*xy(*a), x, y, fill="#6080c0")
                    c.create_oval(x - r + 1, y - r + 1, x + r - 1, y + r - 1, fill="#0050d0", outline="")
            for u, f in pts[::3]:
                x, y = xy(u, f)
                c.create_oval(x - r, y - r, x + r, y + r, fill="#ffffff", outline="#0050d0")

    def save(self):
        name, text = self.name.get().strip(), self.text.get().strip()
        if not self.curve:
            return messagebox.showerror(tr("curve_dialog.spiderweb"), tr("curve_dialog.the_formula_doesn_t_work_yet"),
                                        parent=self)
        if not name:
            return messagebox.showerror(tr("curve_dialog.spiderweb"), tr("curve_dialog.give_the_formula_a_name_first"),
                                        parent=self)
        names = [n for n, _ in self.saved]
        if name in names and not messagebox.askyesno(tr("curve_dialog.spiderweb"),
                                                     tr("curve_dialog.replace_the_saved_formula", name=name),
                                                     parent=self):
            return
        items = [(n, t) for n, t in self.saved if n != name] + [(name, text)]
        try:
            save_formulas(items)
        except OSError as e:
            return messagebox.showerror(tr("curve_dialog.spiderweb"), tr("curve_dialog.couldn_t_save", e=e),
                                        parent=self)
        self.fill_list()
        i = [n for n, _ in self.saved].index(name)
        self.listbox.selection_set(i)
        self.listbox.see(i)

    def delete(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.saved[sel[0]][0]
        if not messagebox.askyesno(tr("curve_dialog.spiderweb"), tr("curve_dialog.delete_the_saved_formula", name=name),
                                   parent=self):
            return
        try:
            save_formulas([(n, t) for n, t in self.saved if n != name])
        except OSError as e:
            return messagebox.showerror(tr("curve_dialog.spiderweb"), tr("curve_dialog.couldn_t_save", e=e),
                                        parent=self)
        self.fill_list()

    def apply(self):
        if not self.curve:
            return
        curve = self.curve
        self.destroy()
        self.done(curve)

    def cancel(self):
        self.destroy()
        self.done(None)
