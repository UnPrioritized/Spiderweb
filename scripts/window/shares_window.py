"""Recolour's Shares… window (side panel Shares…, panel_recolour.py; notes/recolour.py; user 2026-10-25 picked a
small window): Colours = how many each colour set uses (its own ones first, then new ones = new channels), and each
colour's share, a knob + a box (25 or 1/4) per colour. The shares always add up to 100 %: one changed, the others
keep their sizes to each other (user). Stays open and follows the selection like Gate sensitive merge's; each turn /
typed number / Even shares is one undo step on the selected shapes (several: all get the shown shares)."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.recolour import clean_recolour, palettes, weights
from window import look
from window.tool_window import Knob
from window.widgets import Scrub, Tooltip, bad, good, remember_place, wheel_ready

COLUMNS = 4


class ShareKnob(Knob):
    """A knob from 0 (pointing down-left) to 100 (down-right), turned like the note tool knobs, no stop on the way."""

    TURN = 135

    def __init__(self, parent, scale, changed, pressed, even):
        super().__init__(parent, scale, changed, look.KNOB_GREEN, 40, pressed)
        self.bind("<ButtonPress-2>", lambda e: even())  # (back to an even share)

    def draw(self):
        self.delete("all")
        s, m = self.size, max(3, self.size // 9)
        ring = look.KNOB_RING_FOCUS if wheel_ready(self) else look.KNOB_RING
        self.create_arc(m, m, s - m, s - m, start=225, extent=-270, style="arc", outline=ring, width=max(2, m // 2))
        if self.value:
            self.create_arc(m, m, s - m, s - m, start=225, extent=-self.value / 100 * 270, style="arc",
                            outline=self.color if self.enabled else look.KNOB_RING_OFF, width=max(2, m // 2))
        c, r = s / 2, s / 2 - m * 1.8
        a = math.radians(225 - self.value / 100 * 270)
        self.create_oval(c - r, c - r, c + r, c + r, fill=look.KNOB_BODY if self.enabled else look.KNOB_BODY_OFF,
                         outline="")
        self.create_line(c, c, c + r * math.cos(a), c - r * math.sin(a), fill=look.KNOB_POINTER, width=2)

    def step(self, d):
        if self.enabled:
            self.turn_to(self.value + d)

    def point(self, e):
        if not self.enabled:
            return
        self.focus_set()
        c = self.size / 2
        if (e.x - c) ** 2 + (e.y - c) ** 2 > 4:
            deg = math.degrees(math.atan2(e.x - c, c - e.y))  # (0 = up, clockwise)
            self.turn_to((deg + 135) / 270 * 100)

    def press(self, e):
        if not self.enabled:
            return
        self.focus_set()
        if self.pressed and not self.held():
            self.pressed(self)
        self.drag = (e.y, self.value)

    def move(self, e):
        if self.drag:
            y, v = self.drag
            v = max(0.0, min(100.0, v + (y - e.y) * (0.1 if e.state & 1 else 0.5)))
            self.drag = (e.y, v)
            self.turn_to(v)

    def turn_to(self, value, done=False):
        value = round(max(0.0, min(100.0, value)), 1)
        if value != self.value:
            self.set(value)
            self.changed(value, done)
        elif done:
            self.changed(value, True)


def share_text(text):
    """A typed share -> %: "1/4" = 25, "25" or "25%" = 25 (ValueError: not one)."""
    text = text.strip().rstrip("%").strip()
    if "/" in text:
        top, _, bottom = text.partition("/")
        a, b = calc(top), calc(bottom)
        if b <= 0 or a < 0 or a > b:
            raise ValueError
        return a / b * 100
    v = calc(text)
    if not 0 <= v <= 100:
        raise ValueError
    return v


def spread(shares, i, v):
    """shares (%, adding up to 100) with place i set to v: the others scaled to make 100 again, keeping their sizes
    to each other (all of them 0: shared evenly)."""
    out = list(shares)
    rest = sum(x for k, x in enumerate(out) if k != i)
    for k in range(len(out)):
        if k != i:
            out[k] = (out[k] / rest * (100 - v)) if rest > 0 else (100 - v) / (len(out) - 1)
    out[i] = v
    out = [round(x, 3) for x in out]
    big = max((k for k in range(len(out)) if k != i), key=lambda k: out[k], default=None)
    if big is not None and out[big] > 0:  # (the rounding left over goes on the biggest other: 100 exactly)
        out[big] = round(out[big] + 100 - sum(out), 3)
    return out


class SharesWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.shares_window = self
        self.title(tr("shares.title"))
        self.transient(app)
        self.resizable(False, False)
        self.turning = None  # a knob held / wheel steps: (it, the undo step it made)
        self.places = 0  # colours shown
        box = ttk.Frame(self, padding=12)
        box.pack(fill="both", expand=True)
        self.what = ttk.Label(box, text="", foreground=look.HINT)
        self.what.pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(8, 0))
        lb = ttk.Label(row, text=tr("shares.colours"))
        lb.pack(side="left")
        self.n_var = tk.StringVar()
        e = self.n_entry = ttk.Entry(row, textvariable=self.n_var, width=4)
        e.pack(side="left", padx=4)
        Scrub(app, [(e, self.n_var, self.on_colours)], (1, 1, 1), 1, 16, label=lb, drag_box=True)
        e.bind("<Return>", lambda ev: self.on_colours())
        e.bind("<FocusOut>", lambda ev: self.on_colours())
        self.n_tip = Tooltip(lb, "")
        self.grid_box = ttk.Frame(box)
        self.grid_box.pack(anchor="w", pady=(8, 0))
        self.cells = []
        for i in range(16):
            cell = ttk.Frame(self.grid_box, padding=(4, 2))
            name = ttk.Label(cell, text="")
            name.pack()
            knob = ShareKnob(cell, app.scale, lambda v, done, i=i: self.on_knob(i, v, done), self.knob_pressed,
                             lambda i=i: self.on_even_one(i))
            knob.pack()
            r = ttk.Frame(cell)
            r.pack()
            var = tk.StringVar()
            ent = ttk.Entry(r, textvariable=var, width=7, justify="right")
            ent.pack(side="left")
            ttk.Label(r, text="%").pack(side="left")
            ent.bind("<Return>", lambda ev, i=i: self.on_typed(i))
            ent.bind("<FocusOut>", lambda ev, i=i: self.on_typed(i))
            for w in (name, knob, ent):
                Tooltip(w, tr("shares.share_tip"))
            self.cells.append({"cell": cell, "name": name, "knob": knob, "var": var, "entry": ent, "shown": ""})
        ttk.Label(box, text=tr("shares.hint"), foreground=look.HINT, wraplength=int(300 * app.scale),
                  justify="left").pack(anchor="w", fill="x", pady=(8, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text=tr("shares.close"), command=self.close).pack(side="right")
        self.even = ttk.Button(row, text=tr("shares.even"), command=self.on_even)
        self.even.pack(side="left")
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.sync()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "shares")
        self.deiconify()

    # ---- what the selection holds

    def targets(self):
        """(the selected recoloured shapes, their Recolours)."""
        app = self.app
        tg = [(t, clean_recolour(t.get("recolour"))) for t in app.recolour_targets()]
        tg = [(t, r) for t, r in tg if r]
        return [t for t, _ in tg], [r for _, r in tg]

    def layout(self):
        """(least colours = the most own ones in a set, colours used, own ones in the biggest set, shares in % per
        place) of the first selected recoloured shape, or None."""
        tgts, rcs = self.targets()
        if not tgts:
            return None
        most = self.app.set_most()
        owns = self.app.colour_owns(tgts[0])
        own = max((len(o) for o in owns), default=1)
        pals = palettes(owns, rcs[0].get("colours"), most)
        used = max((len(p) for p in pals), default=1)
        shares = [round(x * 100, 3) for x in weights(used, rcs[0].get("shares")).tolist()]
        return own, used, shares, most

    def sync(self):
        """Show the first selected recoloured shape's colours and shares (not while a knob is held)."""
        if self.turning and self.turning[0].held():
            return
        app = self.app
        tgts, _ = self.targets()
        got = self.layout()
        self.what.config(text=tr("shares.nothing") if not tgts else
                         tr("shares.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("shares.n_shapes", n=len(tgts)))
        on = got is not None
        own, used, shares, most = got if on else (1, 0, [], 15)
        self.n_entry.config(state="normal" if on else "disabled")
        if self.focus_get() is not self.n_entry:
            self.n_var.set(str(used) if on else "")
        self.n_tip.text = tr("shares.colours_tip", least=own, most=most)
        self.even.state(["!disabled" if on and used > 1 else "disabled"])
        for i, c in enumerate(self.cells):
            show = i < used
            if show != bool(c["cell"].winfo_manager()):
                if show:
                    c["cell"].grid(row=i // COLUMNS, column=i % COLUMNS, sticky="n")
                else:
                    c["cell"].grid_remove()
            if not show:
                continue
            c["name"].config(text=tr("shares.colour" if i < own else "shares.new", n=i + 1))
            c["knob"].set(round(shares[i], 1))
            c["knob"].on(used > 1)
            text = fmt(shares[i])
            if self.focus_get() is not c["entry"] or c["shown"] != c["var"].get():
                c["var"].set(text)
            c["shown"] = text
            c["entry"].config(state="normal" if used > 1 else "disabled")
        self.places = used

    # ---- changes

    def put_shares(self, shares, push=True):
        return self.app.change_recolour(lambda old: dict(old, shares=shares) if old else old,
                                        tr("recolour.undo_shares"), push)

    def shown_shares(self):
        got = self.layout()
        return list(got[2]) if got else []

    def knob_pressed(self, knob):
        self.turning = (knob, None)

    def on_knob(self, i, v, done):
        """A knob turned: while held one undo step for the whole turn; wheel / arrow steps in a row one step too."""
        knob = self.cells[i]["knob"]
        shares = self.shown_shares()
        if not shares or i >= len(shares):
            return
        step = self.turning[1] if self.turning and self.turning[0] is knob else None
        push = step is None or step is not self.app.last_step()
        if self.put_shares(spread(shares, i, v), push) and push:
            self.turning = (knob, self.app.last_step())
        if done and not knob.held():
            self.sync()

    def on_typed(self, i):
        c = self.cells[i]
        if c["var"].get() == c["shown"] or str(c["entry"].cget("state")) == "disabled":
            return
        try:
            v = round(share_text(c["var"].get()), 3)
        except ValueError:
            c["var"].set(c["shown"])
            return bad(c["entry"])
        good(c["entry"])
        shares = self.shown_shares()
        if i < len(shares):
            self.turning = None
            self.put_shares(spread(shares, i, v))
            self.sync()

    def on_even_one(self, i):
        shares = self.shown_shares()
        if len(shares) > 1:
            self.turning = None
            self.put_shares(spread(shares, i, 100 / len(shares)))

    def on_even(self):
        self.turning = None
        self.app.change_recolour(lambda old: {k: v for k, v in old.items() if k != "shares"} if old else old,
                                 tr("recolour.undo_shares"))

    def on_colours(self):
        """Colours typed: each set uses that many (its own ones at least, at most a set's 15 / 16)."""
        got = self.layout()
        if not got or str(self.n_entry.cget("state")) == "disabled":
            return
        own, used, _, most = got
        try:
            n = int(round(calc(self.n_var.get())))
        except ValueError:
            self.n_var.set(str(used))
            return bad(self.n_entry)
        n = max(own, min(most, n))
        self.n_var.set(str(n))
        good(self.n_entry)
        if n == used:
            return
        self.turning = None

        def changed(old):
            if not old:
                return old
            out = {k: v for k, v in old.items() if k != "colours"}
            return dict(out, colours=n) if n > own else out

        self.app.change_recolour(changed, tr("recolour.undo_colours"))

    def close(self):
        self.app.shares_window = None
        self.destroy()
        self.app.roll.focus_set()


def open_shares(app):
    w = app.shares_window
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        SharesWindow(app)
