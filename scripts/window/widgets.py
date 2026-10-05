"""Small Tk helpers shared by the windows."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt

SHIFT, CTRL = 0x1, 0x4
DRAG_PX = 4  # pixels of label dragging per step
TIP_WIDTH = 560  # tooltips wrap longer lines at this width (at 100 % scaling), so a text needs no line breaks
TALL = 120  # px: a widget taller than this gets its tooltip under the mouse instead of under itself


def grid_shown(w, on):
    """Show / hide a gridded widget (where it was gridded before), only when that changes: placing a widget again
    lays its window out again, which flashes while a number is stepped."""
    if on and not w.winfo_manager():
        w.grid()
    elif not on and w.winfo_manager():
        w.grid_remove()


def box_text(e):
    """What a box holds (from its variable: while that's being set, the box itself still shows the old text).
    globalgetvar: typing in a box sets its variable from inside Tk's own key code, where a plain getvar looks for
    the name there and fails ("can't read PY_VAR17")."""
    name = str(e.cget("textvariable"))
    return str(e.tk.globalgetvar(name)) if name else e.get()


def good(e):
    """A number box's value taken: kept as its last good value (bad() goes back to it), shown as normal."""
    e.good_text = box_text(e)
    e.config(style="TEntry")


def bad(e, typing=False):
    """A wrong value in a number box (not a number, out of range): back to its last good value (user 2026-10-05: not
    left red). typing = checked at every key: red until it's right, put back on Enter / leaving it (watch_bad)."""
    text = getattr(e, "good_text", None)
    if typing or text is None:  # (none known: red, as it can't go back)
        return e.config(style="Bad.TEntry")
    e.config(style="TEntry")
    if e.get() != text:
        e.delete(0, "end")
        e.insert(0, text)


def watch_bad(e):
    """A box checked at every key (bad(typing=True)): still red on Enter / when it's left = back to its good value."""
    def check(ev):
        if str(e.cget("style")) == "Bad.TEntry":
            bad(e)
    e.bind("<Return>", check, add="+")
    e.bind("<FocusOut>", check, add="+")


def leave_box(app, e, var, fn):
    """A side panel box that takes its number on Enter or when it's left. fn(left=True) is called when the box is
    left, and before the selection changes with a number typed but not entered (App.commit_typing: it goes to the
    shapes it was typed for, user). With left it must do nothing while unchanged(e): the box still shows what the
    panel put there (several shapes with different numbers all got the first one's, user)."""
    var.trace_add("write", lambda *_: setattr(e, "shown_text", var.get()) if app._loading else None)
    e.bind("<FocusOut>", lambda ev: fn(left=True))
    app.leave_boxes[e] = lambda: fn(left=True)
    e.bind("<Destroy>", lambda ev: app.leave_boxes.pop(e, None), add="+")


def unchanged(e):
    """The box still shows what the panel put in it (leave_box)."""
    return box_text(e) == getattr(e, "shown_text", None)


def varies_tip(w):
    """The widget's own tooltip for the shared "different settings" sentence (made the first time)."""
    if not hasattr(w, "varies_tip"):
        w.varies_tip = Tooltip(w, "")
    return w.varies_tip


def same_or_blank(e, var, values):
    """A number box for several shapes (call it while app._loading): their number when they all have the same, else
    left empty with the shared tip (user: the box showed the first one's). Typing a number sets it on all of them;
    stepping it starts from the first one's (Scrub). True when they differ."""
    values = [str(v) for v in values]
    varies = len(set(values)) > 1
    var.set("" if varies else values[0] if values else "")
    e.blank_from = values[0] if varies else None
    varies_tip(e).text = tr("widgets.varies_tip") if varies else ""
    return varies


def show_varies(box, varies, tip=None, text=""):
    """A dropdown for several shapes with different choices: shows the shared word (it isn't one of its choices);
    tip: its tooltip, which then starts with the shared sentence (text = its usual text)."""
    if varies:
        box.set(tr("widgets.varies"))
    if tip is not None:
        tip.text = (tr("widgets.varies_tip") + "\n\n" + text if text else tr("widgets.varies_tip")) if varies else text


def remember_good(root):
    """Every number box's last good value starts as what it shows when it gets the keyboard (then good())."""
    def got(ev):
        w = ev.widget
        if isinstance(w, ttk.Entry) and str(w.cget("style")) != "Bad.TEntry":
            w.good_text = w.get()
    root.bind_class("TEntry", "<FocusIn>", got, add="+")


class StatusLine(ttk.Label):
    """The main window's status line. Text put in with config(text=...) is a message: it stays HOLD_MS (user: the
    mouse position wrote over it at once), then the line goes back to what show() gave last (position, counts)."""
    HOLD_MS = 3000

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._normal = ""
        self._job = None

    def configure(self, cnf=None, **kw):
        if "text" in kw or (isinstance(cnf, dict) and "text" in cnf):
            if self._job:
                self.after_cancel(self._job)
            self._job = self.after(self.HOLD_MS, self.release)
        return super().configure(cnf, **kw)

    config = configure

    def show(self, text):
        """The line's usual text: shown now, or when the message on it is done."""
        self._normal = text
        if not self._job:
            super().configure(text=text)

    def release(self):
        """The message is done (its time is up, or e.g. a busy "Copying…" ended): the usual text back."""
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        if self.winfo_exists():
            super().configure(text=self._normal)

    def destroy(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        super().destroy()


class Scrub:
    """Quick number changes for entry boxes: drag the label sideways, Up / Down in the box, or the
    mouse wheel over the box while it has the keyboard. Shift = big steps, Ctrl = fine steps.

    boxes: [(entry, variable, apply)] — apply() is what Enter does in that box (None: the variable applies itself);
    a label over several boxes steps them all together. steps: (step, Shift step, Ctrl step), or a function giving
    them. One scrub of a box is one undo step (App.scrub_step)."""

    def __init__(self, app, boxes, steps, lo=None, hi=None, label=None, drag_box=False):
        """drag_box: each box can be dragged sideways itself too (a click without moving still types)."""
        self.app, self.boxes, self.steps, self.lo, self.hi = app, boxes, steps, lo, hi
        self.drag = None  # label drag: {"x": where the last step was, "n": steps waiting, "job", "gesture", "boxes"}
        self.held = None  # drag_box: the box pressed, until the mouse has moved far enough to be a drag
        for entry, var, apply in boxes:
            entry._scrub = True  # the side panel's wheel scrolling leaves it alone
            for key in ("<Up>", "<Down>"):
                entry.bind(key, lambda e, box=(entry, var, apply): self.key(e, box))
            entry.bind("<MouseWheel>", lambda e, box=(entry, var, apply): self.wheel(e, box))
            if drag_box:
                entry.bind("<ButtonPress-1>", lambda e, box=(entry, var, apply): self.box_press(e, box))
                entry.bind("<B1-Motion>", self.box_motion)
                entry.bind("<ButtonRelease-1>", self.box_release)
            # the first time one is clicked into: how else it can be changed
            entry.bind("<FocusIn>", lambda e: app.tips.show("numbers", wait=True), add="+")
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
                try:  # (an empty box for shapes with different numbers: from the first one's, same_or_blank)
                    value = float(calc(var.get() or getattr(entry, "blank_from", None) or ""))
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

    def box_press(self, e, box):
        self.held = {"x": e.x_root, "box": box}  # (the box's own click still puts the text cursor there)

    def box_motion(self, e):
        """In the box: once the mouse has gone DRAG_PX sideways it's a scrub of that box, not a text selection."""
        h = self.held
        if not h:
            return None
        if not self.drag:
            if abs(e.x_root - h["x"]) < DRAG_PX * self.app.scale:
                return None
            entry = h["box"][0]
            entry.selection_clear()
            entry.config(cursor="sb_h_double_arrow")
            self.press(e, [h["box"]])
            self.drag["x"] = h["x"]  # (the steps count from where it was pressed)
        self.motion(e)
        return "break"

    def box_release(self, e):
        h, self.held = self.held, None
        if h and self.drag:
            h["box"][0].config(cursor="")
            self.release(e)
            return "break"
        return None

    def press(self, e, boxes=None):
        self.drag = {"x": e.x_root, "n": 0, "job": None, "gesture": object(), "state": e.state,
                     "boxes": boxes or self.boxes}
        self.app.scrubbing = True  # (slow notes are made when the mouse rests or is let go: App.shapes_changed)

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
            self.change(d["boxes"], n, d["state"], d["gesture"])

    def release(self, e):
        if self.drag and self.drag["job"]:
            self.app.after_cancel(self.drag["job"])
            self.flush()
        self.drag = None
        self.app.scrubbing = False
        self.app.catch_up_notes()


class LocalUndo:
    """Ctrl+Z / Ctrl+Y inside a pop-up window: its own steps, which never reach the main window's undo.
    get(): how it is now (a string, e.g. JSON); put(state): make it so. mark(key) after each change: changes with the
    same key (not None) in a row are one step (typing in one box, stepping one number)."""

    def __init__(self, win, get, put, limit=300):
        self.get, self.put, self.limit = get, put, limit
        self.reset()
        for k in ("z", "Z"):
            win.bind(f"<Control-{k}>", lambda e: (self.undo(), "break")[1])
        for k in ("y", "Y"):
            win.bind(f"<Control-{k}>", lambda e: (self.redo(), "break")[1])

    def reset(self):
        """Start again from how it is now (nothing to undo)."""
        self.states, self.at, self.key = [self.get()], 0, None

    def mark(self, key=None):
        state = self.get()
        if state == self.states[self.at]:
            return
        del self.states[self.at + 1:]
        if key is not None and key == self.key and self.at > 0:
            self.states[self.at] = state
        else:
            self.states.append(state)
            self.at += 1
            if len(self.states) > self.limit:
                del self.states[0]
                self.at -= 1
        self.key = key

    def undo(self):
        self.step(-1)

    def redo(self):
        self.step(1)

    def step(self, d):
        if self.get() != self.states[self.at]:  # (a change not marked yet: it's the step to undo)
            self.mark()
        if 0 <= self.at + d < len(self.states):
            self.at += d
            self.key = None
            self.put(self.states[self.at])


class Tooltip:
    """Shows a small box of text while the mouse rests on a widget (none while the text is empty). Once the widget
    is used (clicked, dragged, wheel, keys), the box stays away until the mouse leaves it with no button held."""

    BUTTONS = 0x1f00  # (event.state: a mouse button is held)

    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self.job, self.used = widget, text, None, None, False
        widget.bind("<Enter>", self.enter, add="+")
        widget.bind("<Leave>", self.leave, add="+")
        for seq in ("<ButtonPress>", "<MouseWheel>", "<KeyPress>"):
            widget.bind(seq, lambda e: self.use(), add="+")

    def enter(self, e):
        if not self.used and not e.state & self.BUTTONS:
            self.schedule()

    def leave(self, e):
        self.hide()
        if not e.state & self.BUTTONS:
            self.used = False

    def use(self):
        self.used = True
        self.hide()

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
        # under the widget, or under the mouse for a tall one (a list as tall as the window: not at its bottom);
        # kept inside the window (the panel sits at its right edge)
        top = w.winfo_toplevel()
        if w.winfo_height() > TALL:
            x, y = w.winfo_pointerx(), w.winfo_pointery() + 20
        else:
            x, y = w.winfo_rootx() + 12, w.winfo_rooty() + w.winfo_height() + 4
        x = min(x, top.winfo_rootx() + top.winfo_width() - self.tip.winfo_reqwidth() - 4)
        self.tip.wm_geometry(f"+{x}+{y}")

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
