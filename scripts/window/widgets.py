"""Small Tk helpers shared by the windows."""

import tkinter as tk

from files.lang import tr
from files.mathexpr import calc, fmt

SHIFT, CTRL = 0x1, 0x4
DRAG_PX = 4  # pixels of label dragging per step
TIP_WIDTH = 560  # tooltips wrap longer lines at this width (at 100 % scaling), so a text needs no line breaks


class Scrub:
    """Quick number changes for entry boxes: drag the label sideways, Up / Down in the box, or the
    mouse wheel over the box while it has the keyboard. Shift = big steps, Ctrl = fine steps.

    boxes: [(entry, variable, apply)] — apply() is what Enter does in that box (None: the variable applies itself);
    a label over several boxes steps them all together. steps: (step, Shift step, Ctrl step), or a function giving
    them. One scrub of a box is one undo step (App.scrub_step)."""

    def __init__(self, app, boxes, steps, lo=None, hi=None, label=None):
        self.app, self.boxes, self.steps, self.lo, self.hi = app, boxes, steps, lo, hi
        self.drag = None  # label drag: {"x": where the last step was, "n": steps waiting, "job", "gesture"}
        for entry, var, apply in boxes:
            entry._scrub = True  # the side panel's wheel scrolling leaves it alone
            for key in ("<Up>", "<Down>"):
                entry.bind(key, lambda e, box=(entry, var, apply): self.key(e, box))
            entry.bind("<MouseWheel>", lambda e, box=(entry, var, apply): self.wheel(e, box))
        if label is not None:
            label.config(cursor="sb_h_double_arrow")
            label.bind("<ButtonPress-1>", self.press)
            label.bind("<B1-Motion>", self.motion)
            label.bind("<ButtonRelease-1>", self.release)

    def step_size(self, state):
        step, big, fine = self.steps() if callable(self.steps) else self.steps
        return big if state & SHIFT else fine if state & CTRL else step

    def change(self, boxes, n, state, gesture):
        """Every box (that's on and holds a number) n steps up (minus: down)."""
        d = n * self.step_size(state)

        def run():
            for entry, var, apply in boxes:
                if str(entry.cget("state")) == "disabled":
                    continue
                try:
                    value = float(calc(var.get()))
                except (ValueError, ZeroDivisionError):
                    continue
                value = round(value + d, 6)
                if self.lo is not None:
                    value = max(self.lo, value)
                if self.hi is not None:
                    value = min(self.hi, value)
                var.set(fmt(value))
                if apply:
                    apply()
        self.app.scrub_step(gesture, run)

    def key(self, e, box):
        self.change([box], 1 if e.keysym == "Up" else -1, e.state, box[0])
        return "break"

    def wheel(self, e, box):
        if self.app.focus_get() is not box[0]:
            return None  # not being typed in: the panel scrolls
        self.change([box], 1 if e.delta > 0 else -1, e.state, box[0])
        return "break"

    def press(self, e):
        self.drag = {"x": e.x_root, "n": 0, "job": None, "gesture": object(), "state": e.state}

    def motion(self, e):
        d = self.drag
        if not d:
            return
        px = DRAG_PX * self.app.scale
        n = int((e.x_root - d["x"]) / px)
        if not n:
            return
        d["x"] += n * px
        d["n"] += n
        d["state"] = e.state
        if not d["job"]:  # one change for all the mouse moves that came in while the last one was being worked out
            d["job"] = self.app.after_idle(self.flush)

    def flush(self):
        d = self.drag
        if not d:
            return
        d["job"] = None
        n, d["n"] = d["n"], 0
        if n:
            self.change(self.boxes, n, d["state"], d["gesture"])

    def release(self, e):
        if self.drag and self.drag["job"]:
            self.app.after_cancel(self.drag["job"])
            self.flush()
        self.drag = None


class Tooltip:
    """Shows a small box of text while the mouse rests on a widget (none while the text is empty)."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", lambda e: self.schedule(), add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")
        widget.bind("<ButtonPress>", lambda e: self.hide(), add="+")

    def schedule(self):
        self.hide()
        self.job = self.widget.after(400, self.show)

    def show(self):
        self.job = None
        if not self.text:
            return
        w = self.widget
        self.tip = tk.Toplevel(w)
        self.tip.wm_overrideredirect(True)
        tk.Label(self.tip, text=self.text, justify="left", background="#ffffe8", relief="solid",
                 borderwidth=1, padx=6, pady=4, wraplength=round(TIP_WIDTH * w.winfo_fpixels("1i") / 96)).pack()
        self.tip.update_idletasks()
        # keep it inside the window (the panel sits at its right edge)
        top = w.winfo_toplevel()
        x = min(w.winfo_rootx() + 12, top.winfo_rootx() + top.winfo_width() - self.tip.winfo_reqwidth() - 4)
        self.tip.wm_geometry(f"+{x}+{w.winfo_rooty() + w.winfo_height() + 4}")

    def hide(self):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def symmetry_menu(m, current, choose):
    """"Symmetric halves ▸" in menu m for a curve (current = its "sym" or None); choose(mode or None)."""
    sub = tk.Menu(m, tearoff=0)
    now = tk.StringVar(m, value=current or "off")
    m.symmetry_var = now  # keep it alive while the menu is open
    for value, label in (("off", tr("widgets.off")), ("mirror", tr("widgets.mirrored_like_an_arch")),
                         ("turn", tr("widgets.turned_half_way_round_like_an"))):
        sub.add_radiobutton(label=label, value=value, variable=now,
                            command=lambda v=value: choose(None if v == "off" else v))
    m.add_cascade(label=tr("widgets.symmetric_halves"), menu=sub)
