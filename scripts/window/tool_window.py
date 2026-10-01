"""What the Claw machine and Strum windows share: a window that changes the selected shapes' notes through one
setting of theirs (sh[KEY]), shown live on the piano roll. Accept keeps the change (one undo step), X / Esc puts the
notes back, Reset sets everything back to "does nothing". The main window can be used while it's open: the window
follows the selection, and before anything else changes there, the setting being tried out is kept as its own undo
step (settle). Also the round Knob both use."""

import json
import math
import time
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from window.widgets import LocalUndo

ORANGE = "#f5a623"
GREEN = "#7cc21b"


class Knob(tk.Canvas):
    """A round dial from -100 to 100 (0 = straight up, all the way = straight down). Drag up / down (Shift = fine),
    the mouse wheel or the arrow keys turn it; it sticks at 0 for a moment on the way past; the right mouse button
    points it at the mouse; a middle-click puts it back to 0. changed(value, done): done = the end of one turn.
    Greyed out (on(False)), it shows its value but can't be turned."""

    TURN = 180  # degrees each way
    STICK = 10  # pixels of dragging that stay at 0

    def __init__(self, parent, scale, changed, color=ORANGE, size=44):
        self.size = size = round(size * scale)
        super().__init__(parent, width=size, height=size, highlightthickness=0, takefocus=True,
                         background=ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
        self.value, self.changed, self.drag, self.color, self.enabled = 0.0, changed, None, color, True
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", lambda e: self.release())
        self.bind("<ButtonPress-2>", lambda e: self.enabled and self.turn_to(0, True))
        self.bind("<ButtonPress-3>", self.point)
        self.bind("<B3-Motion>", self.point)
        self.bind("<ButtonRelease-3>", lambda e: self.enabled and self.changed(self.value, True))
        self.bind("<MouseWheel>", lambda e: self.step(5 if e.delta > 0 else -5))
        for key, d in (("Up", 1), ("Right", 1), ("Down", -1), ("Left", -1)):
            self.bind(f"<{key}>", lambda e, d=d: self.step(d * (1 if e.state & 1 else 5)))
        self.bind("<FocusIn>", lambda e: self.draw())
        self.bind("<FocusOut>", lambda e: self.draw())
        self.draw()

    def set(self, value):
        self.value = value
        self.draw()

    def on(self, enabled):
        if enabled != self.enabled:
            self.enabled = enabled
            self.config(takefocus=enabled)
            self.draw()

    def draw(self):
        self.delete("all")
        s, m = self.size, max(3, self.size // 9)
        ring = "#888" if self.focus_get() is self else "#bbb"
        self.create_oval(m, m, s - m, s - m, outline=ring, width=max(2, m // 2))
        if self.value:
            self.create_arc(m, m, s - m, s - m, start=90, extent=-self.value / 100 * self.TURN, style="arc",
                            outline=self.color if self.enabled else "#ccc", width=max(2, m // 2))
        c, r = s / 2, s / 2 - m * 1.8
        a = math.radians(90 - self.value / 100 * self.TURN)
        self.create_oval(c - r, c - r, c + r, c + r, fill="#555" if self.enabled else "#aaa", outline="")
        self.create_line(c, c, c + r * math.cos(a), c - r * math.sin(a), fill="white", width=2)

    def step(self, d):
        if self.enabled:
            v = self.value + d
            self.turn_to(0 if v * self.value < 0 else v, True)  # (stops at 0 on the way past)

    def point(self, e):
        """Right mouse button: the dial points at the mouse."""
        if not self.enabled:
            return
        self.focus_set()
        c = self.size / 2
        if (e.x - c) ** 2 + (e.y - c) ** 2 > 4:  # (not right on the middle: no direction there)
            self.turn_to(math.degrees(math.atan2(e.x - c, c - e.y)) / self.TURN * 100)

    def press(self, e):
        if not self.enabled:
            return
        self.focus_set()
        self.drag = (e.y, self.value + math.copysign(self.STICK, self.value) if self.value else 0.0)

    def move(self, e):
        if self.drag:  # (the mouse moves a "raw" value that has STICK extra on each side of 0)
            y, r = self.drag
            r = max(-100.0 - self.STICK, min(100.0 + self.STICK, r + (y - e.y) * (0.2 if e.state & 1 else 1)))
            self.drag = (e.y, r)
            self.turn_to(0 if abs(r) <= self.STICK else r - math.copysign(self.STICK, r))

    def release(self):
        if self.drag:
            self.drag = None
            self.changed(self.value, True)

    def turn_to(self, value, done=False):
        value = round(max(-100.0, min(100.0, value)), 1)
        if value != self.value:
            self.set(value)
            self.changed(value, done)
        elif done:
            self.changed(value, True)


class ToolWindow(tk.Toplevel):
    """The shared part. Each window sets KEY (the shape setting; also the start of its text keys: KEY.window_title,
    KEY.nothing, KEY.shape, KEY.n_shapes, and KEY.step = the undo step's name), DEFAULTS, ATTR (the app's
    attribute holding the open window), POS (the app's attribute remembering where it was), and has
    clean(settings) -> the setting a shape keeps (None = changes nothing), build(box) (its widgets; row 0 is taken)
    and show() (the widgets show self.cfg). put(key, value, done) = a setting changed."""

    KEY, ATTR, POS, DEFAULTS = "", "", "", {}

    @classmethod
    def open(cls, app):
        if not app.sels:
            return
        w = getattr(app, cls.ATTR)
        if w:
            w.lift()
        else:
            w = cls(app)
            setattr(app, cls.ATTR, w)
        w.focus_set()

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr(f"{self.KEY}.window_title"))
        self.transient(app)
        self.resizable(False, False)
        if getattr(app, self.POS):
            self.geometry(getattr(app, self.POS))
        self.cfg = dict(self.DEFAULTS)
        self.late, self.took = None, 0.0  # (preview)
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text="", foreground="#777")
        self.what.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.build(box)
        self.undo = LocalUndo(self, lambda: json.dumps(self.cfg, sort_keys=True), self.put_state)
        self.bind("<Control-Key>", lambda e: "break")  # (the piano roll's shortcuts wait until it's closed)
        self.bind("<F1>", lambda e: (app.open_help(self.KEY), "break")[1])
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.accept())
        self.bind("<Configure>", self.remember, add="+")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.retarget()
        self.after_idle(lambda: app.tips.show(self.KEY, parent=self))

    def build(self, box):
        raise NotImplementedError

    def show(self):
        raise NotImplementedError

    def clean(self, cfg):
        raise NotImplementedError

    def settings(self):
        """The selected shapes' setting, by shape number."""
        return {i: self.app.shapes[i].get(self.KEY) for i in self.targets if i < len(self.app.shapes)}

    def retarget(self):
        """Work on the selected shapes, showing their setting (the first one's that has one)."""
        app = self.app
        self.targets = sorted(app.sels)
        self.saved = json.dumps(app.shapes)  # (for the undo step)
        self.before = self.now = self.settings()  # (before: put back by X / Esc; now: as this window last left them)
        shown = next((c for c in self.before.values() if c), None)
        self.cfg = dict(self.DEFAULTS, **json.loads(json.dumps(shown or {})))
        n = len(self.targets)
        self.what.config(text=tr(f"{self.KEY}.nothing") if not n else tr(f"{self.KEY}.n_shapes", n=n) if n > 1
                         else tr(f"{self.KEY}.shape", shape_label=app.shape_label(app.shapes[self.targets[0]])))
        self.show()
        self.undo.reset()

    def sync(self):
        """The main window changed the selection or the shapes."""
        if sorted(self.app.sels) != self.targets:
            self.settle()
        elif self.settings() == self.now:
            return
        self.retarget()

    def settle(self):
        """Something else is about to change in the main window: the setting tried so far is kept (its own undo
        step), and from now on X / Esc only puts back what changes after this."""
        self.catch_up()
        if self.now != self.before and self.settings() == self.now:
            saved = self.saved
            self.saved, self.before = json.dumps(self.app.shapes), self.now
            self.app.add_undo_step(saved, tr(f"{self.KEY}.step"))

    def put(self, key, value, done=True):
        """A setting changed: show it on the piano roll."""
        self.cfg[key] = value
        self.show()
        self.preview(done)
        self.undo.mark(None if done else key)

    def put_state(self, state):
        self.cfg = json.loads(state)
        self.show()
        self.preview()

    def preview(self, now=True):
        """The piano roll shows the setting. While a number is dragged / a dial turned (not now) and that's slow
        (lots of notes), only the window follows the mouse: the notes catch up when the mouse rests."""
        if self.late:
            self.after_cancel(self.late)
            self.late = None
        if not now and self.took > 0.15:
            self.late = self.after(250, self.preview)
            return
        started = time.perf_counter()
        cl = self.clean(self.cfg)
        for i in self.targets:
            sh = self.app.shapes[i]
            if cl:
                sh[self.KEY] = dict(cl)
            else:
                sh.pop(self.KEY, None)
        self.now = self.settings()
        self.app.shapes_changed(now=True)
        self.app.update_idletasks()  # (the piano roll redrawn now, so the time counts it)
        self.took = time.perf_counter() - started

    def catch_up(self):
        """A preview left for later (preview): now."""
        if self.late:
            self.preview()

    def reset(self):
        self.cfg = dict(self.DEFAULTS)
        self.show()
        self.preview()
        self.undo.mark()

    def accept(self):
        self.settle()
        self.close()

    def cancel(self):
        app = self.app
        if self.late:
            self.after_cancel(self.late)
            self.late = None
        for i, c in self.before.items():
            app.shapes[i].pop(self.KEY, None)
            if c:
                app.shapes[i][self.KEY] = c
        app.shapes_changed()
        self.close()

    def remember(self, e):
        if e.widget is self:
            setattr(self.app, self.POS, f"+{self.winfo_x()}+{self.winfo_y()}")

    def close(self):
        if self.late:
            self.after_cancel(self.late)
        setattr(self.app, self.ATTR, None)
        self.destroy()
        self.app.roll.focus_set()
