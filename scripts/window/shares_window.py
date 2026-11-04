"""Recolour's Shares… window (side panel Shares…, panel_recolour.py; notes/recolour.py; user 2026-10-25 picked a
small window): Colours = how many each colour set uses (its own ones first, then new ones = new channels), and each
colour's share, a knob + a box (25 or 1/4) + a padlock per colour (user 2026-10-25: the "Pin" tick row was too
crowded; texts say "locked", the saved key stays "pins"). The shares always add up to 100 %: one changed, the others
keep their sizes to each other (user), locked ones stay (user: what can't be done simply doesn't move).
Stays open and follows the selection like Gate sensitive merge's. Several shapes: each takes a change on its own list
(so the shown % holds for each); a box where they differ is blank, like the side panel's Mixed. Each knob turn /
wheel run / typed number / Even shares / lock is one undo step; Ctrl+Z while a knob is held = back to the press."""

import math
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.recolour import KEEP, MOST_PUSH, MOST_TIGHT, PATTERNS, SPLIT, TIGHT, clean_recolour, palettes, wanted, \
    weights
from window import look
from window.tool_window import Knob
from window.widgets import Scrub, Tooltip, bad, good, remember_place, show_mixed, wheel_ready

COLUMNS = 4


class ShareKnob(Knob):
    """A knob from 0 (pointing down-left) to top (down-right), turned like the note tool knobs, no stop on the way.
    Middle-click = even()."""

    TURN = 135

    def __init__(self, parent, scale, changed, pressed, even, top=100.0):
        self.top = top
        super().__init__(parent, scale, changed, look.KNOB_GREEN, 40, pressed)
        self.bind("<ButtonPress-2>", lambda e: even())  # (back to an even share)

    def draw(self):
        self.delete("all")
        s, m = self.size, max(3, self.size // 9)
        ring = look.KNOB_RING_FOCUS if wheel_ready(self) else look.KNOB_RING
        self.create_arc(m, m, s - m, s - m, start=225, extent=-270, style="arc", outline=ring, width=max(2, m // 2))
        if self.value:
            self.create_arc(m, m, s - m, s - m, start=225, extent=-self.value / self.top * 270, style="arc",
                            outline=self.color if self.enabled else look.KNOB_RING_OFF, width=max(2, m // 2))
        c, r = s / 2, s / 2 - m * 1.8
        a = math.radians(225 - self.value / self.top * 270)
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
            self.turn_to((deg + 135) / 270 * self.top)

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
            v = max(0.0, min(self.top, v + (y - e.y) * (0.1 if e.state & 1 else 0.5) * self.top / 100))
            self.drag = (e.y, v)
            self.turn_to(v)

    def turn_to(self, value, done=False):
        value = round(max(0.0, min(self.top, value)), 1)
        if value != self.value:
            self.set(value)
            self.changed(value, done)
        elif done:
            self.changed(value, True)


class AngleKnob(ShareKnob):
    """Split: Clumps' Angle, -360..360 (most: Stir's Turn goes on for many turns, user 11-04): its pointer shows the
    way (0 = right, turning counter-clockwise); dragged 1° a pixel (Shift 0.1°), the right button points it at the
    mouse (in the turn it's on), middle-click = 0. Past a whole turn the ring is full in the last turn's colour and
    the new turn's arc goes over it in the next colour (look.KNOB_LAPS)."""

    def __init__(self, parent, scale, changed, pressed, most=360.0):
        self.most = most
        super().__init__(parent, scale, changed, pressed, lambda: self.turn_to(0, True), 360.0)

    def draw(self):
        self.delete("all")
        s, m = self.size, max(3, self.size // 9)
        ring = look.KNOB_RING_FOCUS if wheel_ready(self) else look.KNOB_RING
        self.create_oval(m, m, s - m, s - m, outline=ring, width=max(2, m // 2))
        if self.value:
            laps = int(abs(self.value) // 360)
            rest = math.copysign(abs(self.value) - 360 * laps, self.value)
            colour = lambda n: (look.KNOB_LAPS[n % len(look.KNOB_LAPS)] if self.most > 360 else self.color) \
                if self.enabled else look.KNOB_RING_OFF
            if laps:  # (whole turns done: the ring full in the last one's colour)
                self.create_oval(m, m, s - m, s - m, outline=colour(laps - 1), width=max(2, m // 2))
            if rest:
                self.create_arc(m, m, s - m, s - m, start=0, extent=max(-359.9, min(359.9, rest)), style="arc",
                                outline=colour(laps), width=max(2, m // 2))
        c, r = s / 2, s / 2 - m * 1.8
        a = math.radians(self.value)
        self.create_oval(c - r, c - r, c + r, c + r, fill=look.KNOB_BODY if self.enabled else look.KNOB_BODY_OFF,
                         outline="")
        self.create_line(c, c, c + r * math.cos(a), c - r * math.sin(a), fill=look.KNOB_POINTER, width=2)

    def point(self, e):
        if not self.enabled:
            return
        self.focus_set()
        c = self.size / 2
        if (e.x - c) ** 2 + (e.y - c) ** 2 > 4:
            a = math.degrees(math.atan2(c - e.y, e.x - c))
            if self.most > 360:  # (stays in the turn it's on: pointing never throws whole turns away)
                a += 360 * round((self.value - a) / 360)
            self.turn_to(a)

    def move(self, e):
        if self.drag:
            y, v = self.drag
            v = max(-self.most, min(self.most, v + (y - e.y) * (0.1 if e.state & 1 else 1)))
            self.drag = (e.y, v)
            self.turn_to(v)

    def turn_to(self, value, done=False):
        value = round(max(-self.most, min(self.most, value)), 1)
        if value != self.value:
            self.set(value)
            self.changed(value, done)
        elif done:
            self.changed(value, True)


class LockButton(tk.Canvas):
    """A small padlock drawn with lines (no font glyph: Linux): open + grey = free, shut + dark = locked, shut +
    grey = mixed. Click / Space / Enter = toggled(); greyed = nothing."""

    def __init__(self, parent, scale, toggled):
        self.size = size = round(18 * scale)
        super().__init__(parent, width=size, height=size, highlightthickness=0, takefocus=True, cursor="hand2",
                         background=ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
        self.locked, self.enabled, self.toggled = False, True, toggled  # (locked: True / False / None = mixed)
        self.bind("<ButtonPress-1>", lambda e: self.click())
        for key in ("<space>", "<Return>"):
            self.bind(key, lambda e: self.click())
        self.draw()

    def click(self):
        if self.enabled:
            self.focus_set()
            self.toggled()

    def show(self, state, enabled):
        if (state, enabled) != (self.locked, self.enabled):
            self.locked, self.enabled = state, enabled
            self.config(takefocus=enabled, cursor="hand2" if enabled else "")
            self.draw()

    def draw(self):
        self.delete("all")
        s = self.size
        shut = self.locked is not False
        col = look.LOCK_ON if self.locked is True and self.enabled else \
            look.KNOB_RING_OFF if not self.enabled else look.LOCK_OFF
        w = max(1, round(s / 10))
        x0, x1, y0, y1 = s * 0.2, s * 0.8, s * 0.45, s * 0.9  # (body)
        lift = 0 if shut else s * 0.12  # (open: the shackle lifted, its right leg off the body)
        r0, r1 = s * 0.3, s * 0.7
        self.create_arc(r0, s * 0.1 - lift, r1, s * 0.6 - lift, start=0, extent=180, style="arc", outline=col,
                        width=w)
        self.create_line(r0, s * 0.35 - lift, r0, y0, fill=col, width=w)
        self.create_line(r1, s * 0.35 - lift, r1, y0 - (lift and s * 0.12), fill=col, width=w)
        self.create_rectangle(x0, y0, x1, y1, outline=col, width=w, fill=col if self.locked is True else "")


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


def spread(shares, i, v, pins=()):
    """shares (%, adding up to 100) with place i set to v: the others not pinned scaled to make 100 again, keeping
    their sizes to each other (all of them 0: shared evenly); pinned ones stay. v is kept within what the pinned
    ones leave; nothing free to take the rest = unchanged."""
    n = len(shares)
    free = [k for k in range(n) if k != i and k not in pins]
    if not free:
        return list(shares)
    fixed = sum(shares[k] for k in range(n) if k != i and k in pins)
    v = max(0.0, min(v, 100 - fixed))
    out = list(shares)
    rest = sum(out[k] for k in free)
    for k in free:
        out[k] = (out[k] / rest * (100 - fixed - v)) if rest > 0 else (100 - fixed - v) / len(free)
    out[i] = v
    out = [round(x, 3) for x in out]
    big = max(free, key=lambda k: out[k])
    out[big] = round(max(0.0, out[big] + 100 - sum(out)), 3)  # (the rounding left over: 100 exactly)
    return out


def evened(shares, pins=()):
    """Every share not pinned the same, sharing what the pinned ones leave."""
    free = [k for k in range(len(shares)) if k not in pins]
    if not free:
        return list(shares)
    left = max(0.0, 100 - sum(x for k, x in enumerate(shares) if k in pins))
    out = [x if k in pins else round(left / len(free), 3) for k, x in enumerate(shares)]
    out[free[0]] = round(max(0.0, out[free[0]] + 100 - sum(out)), 3)
    return out


class KnobHold:
    """A share knob held (App.held_scrub): shortcuts wait, Ctrl+Z goes back to the press with no step."""

    def __init__(self, win, knob):
        self.win, self.knob, self.drag = win, knob, {"gesture": object()}

    def cancel(self):
        self.win.call_off(self)


class HeldKnobs:
    """Knobs in a small window (Shares…, Stir…): a held turn = one undo step, wheel / arrow steps in a row too,
    Ctrl+Z while held = back to the press. Needs self.app, self.hold, self.wheel, self.sync(), self.take_typing()."""

    def knob_pressed(self, knob):
        self.take_typing()  # (a number typed in a box first: its own step)
        self.hold = KnobHold(self, knob)
        self.app.held_scrub = self.hold  # (shortcuts wait, Ctrl+Z = back to the press: App.key_undo)
        self.app.scrubbing = "follow"  # (slow notes made in the background over and over while held: App.shapes_changed)

    def on_turn(self, knob, done, run):
        """A knob turned (run = its change): a held turn is one undo step (scrub_step), wheel / arrow steps in a
        row too."""
        hold = self.hold if self.hold and self.hold.knob is knob else None
        if hold:
            gesture = hold.drag["gesture"]
        else:
            sc = self.app._scrub
            if not (self.wheel and self.wheel[0] is knob and sc and sc["gesture"] is self.wheel[1]):
                self.wheel = (knob, object())
            gesture = self.wheel[1]
        if not (hold and done and not knob.held()):
            self.app.scrub_step(gesture, run)
        if hold and done and not knob.held():  # (let go: the notes catch up, the window shows what's there)
            self.end_hold()
        else:  # (held too: the boxes follow the turn, user 2026-11-02; the held knob is left to the mouse: held_knob)
            self.sync()

    def held_knob(self):
        """The knob held now (sync leaves it where the mouse put it), or None."""
        return self.hold.knob if self.hold else None

    def end_hold(self):
        self.hold = None
        self.app.held_scrub = None
        self.app.scrubbing = False
        self.app.catch_up_notes()
        self.sync()

    def call_off(self, hold):
        """Ctrl+Z while a knob is held: the settings as at the press, no step; the held mouse turns nothing more."""
        if self.hold is not hold:
            return
        hold.knob.call_off()
        self.hold = None
        self.app.held_scrub = None
        self.app.scrubbing = False
        self.app.take_back_scrub(hold.drag["gesture"])
        self.app.catch_up_notes()
        self.sync()


class SharesWindow(HeldKnobs, tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        app.shares_window = self
        self.title(tr("shares.title"))
        self.transient(app)
        self.resizable(False, False)
        self.hold = None  # a knob held (KnobHold)
        self.wheel = None  # (knob, gesture) of wheel / arrow steps in a row: one undo step
        self.n_shown = ""
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
            lock = LockButton(r, app.scale, lambda i=i: self.on_pin(i))
            lock.pack(side="left", padx=(2, 0))
            ent.bind("<Return>", lambda ev, i=i: self.on_typed(i))
            ent.bind("<FocusOut>", lambda ev, i=i: self.on_typed(i))
            Tooltip(lock, tr("shares.lock_tip"))
            tips = [Tooltip(w, tr("shares.share_tip")) for w in (name, knob, ent)]
            self.cells.append({"cell": cell, "name": name, "knob": knob, "var": var, "entry": ent, "shown": "",
                               "lock": lock, "tips": tips})
        self.boxes = {}  # Clumps' number boxes: name -> {var, entry, shown, lo, hi}
        self._build_clumps(box)
        self.hint = ttk.Label(box, text=tr("shares.hint"), foreground=look.HINT, wraplength=int(300 * app.scale),
                              justify="left")
        self.hint.pack(anchor="w", fill="x", pady=(8, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text=tr("shares.close"), command=self.close).pack(side="right")
        self.even = ttk.Button(row, text=tr("shares.even"), command=self.on_even)
        self.even.pack(side="left")
        Tooltip(self.even, tr("shares.even_tip"))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.sync()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        remember_place(self, "shares")
        app.centre_first = "clumps"  # (its dots win over the Stir's on one spot: roll_centre.centre_boxes)
        self.bind("<FocusIn>", lambda e: setattr(app, "centre_first", "clumps"), add="+")
        self.deiconify()

    def _build_clumps(self, parent):
        """Split: Clumps' areas (shown while a selected shape uses it; user 2026-10-25 picked this window): Pattern,
        Angle, Swirl tightness, Centre point, Push."""
        app = self.app
        box = self.clumps_box = ttk.Frame(parent)  # (packed by sync)
        head = ttk.Frame(box)
        head.pack(fill="x", pady=(10, 4))
        ttk.Label(head, text=tr("shares.clumps")).pack(side="left")
        ttk.Separator(head).pack(side="left", fill="x", expand=True, padx=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Label(row, text=tr("shares.pattern")).pack(side="left")
        self.pattern = ttk.Combobox(row, values=[tr("shares.pattern_" + p) for p in PATTERNS], state="readonly",
                                    width=max(len(tr("shares.pattern_" + p)) for p in PATTERNS) + 1)
        self.pattern.pack(side="left", padx=(5, 0))
        self.pattern.bind("<<ComboboxSelected>>", lambda e: self.on_pattern())
        self.pattern_tip = Tooltip(self.pattern, tr("shares.pattern_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        for name, unit, lo, hi in (("angle", "°", -360, 360), ("tight", "", 0, MOST_TIGHT)):
            cell = ttk.Frame(row, padding=(0, 0, 14, 0))
            cell.pack(side="left", anchor="n")
            word = ttk.Label(cell, text=tr("shares." + name))
            word.pack()
            turned = lambda v, done, name=name: self.on_turned(name, v, done)
            if name == "angle":
                knob = self.angle_knob = AngleKnob(cell, app.scale, turned, self.knob_pressed)
            else:
                knob = self.tight_knob = ShareKnob(cell, app.scale, turned, self.knob_pressed,
                                                   lambda: self.set_clumps("tight", TIGHT), MOST_TIGHT)
            knob.pack()
            r = ttk.Frame(cell)
            r.pack()
            self._number_box(r, name, lo, hi, word)
            if unit:
                ttk.Label(r, text=unit).pack(side="left")
            for w in (word, knob):
                Tooltip(w, tr(f"shares.{name}_tip"))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        word = ttk.Label(row, text=tr("shares.centre"))
        word.pack(side="left")
        Tooltip(word, tr("shares.centre_tip"))
        for k, name in enumerate(("across", "up")):
            w = ttk.Label(row, text=tr("shares.centre_" + name))
            w.pack(side="left", padx=(6, 0))
            self._number_box(row, "centre" + str(k), 0, 100, w)
            ttk.Label(row, text="%").pack(side="left")
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        cross = ttk.Frame(row)
        cross.pack(side="left")
        self.pushes = {}
        for way, (r, c, dx, dy) in {"up": (0, 1, 0, 1), "left": (1, 0, -1, 0), "right": (1, 2, 1, 0),
                                    "down": (2, 1, 0, -1)}.items():
            b = ttk.Button(cross, text=tr("shares.push_" + way), width=7,
                           command=lambda dx=dx, dy=dy: self.on_push(dx, dy))
            b.grid(row=r, column=c, padx=1, pady=1)
            Tooltip(b, tr("shares.push_tip"))
            self.pushes[way] = b
        b = self.push_none = ttk.Button(cross, text="·", width=3, command=lambda: self.on_push(None, None))
        b.grid(row=1, column=1)
        Tooltip(b, tr("shares.push_none_tip"))
        side = ttk.Frame(row)
        side.pack(side="left", padx=(8, 0))
        ttk.Label(side, text=tr("shares.push")).pack(anchor="w")
        self.push_text = ttk.Label(side, text="", foreground=look.HINT)
        self.push_text.pack(anchor="w")
        # (flipped / turned since: the areas went with it, the numbers stay as typed; recolour.py FRAME)
        self.framed = ttk.Label(box, text=tr("shares.framed"), foreground=look.HINT,
                                wraplength=int(300 * app.scale), justify="left")

    def _number_box(self, parent, name, lo, hi, word):
        var = tk.StringVar()
        e = ttk.Entry(parent, textvariable=var, width=7, justify="right")
        e.pack(side="left", padx=(4, 1))
        self.boxes[name] = {"var": var, "entry": e, "shown": "", "lo": lo, "hi": hi}
        e.bind("<Return>", lambda ev: self.on_box(name))
        e.bind("<FocusOut>", lambda ev: self.on_box(name))
        Scrub(self.app, [(e, var, lambda: self.on_box(name))], (1, 10, 0.1), lo, hi, label=word, drag_box=True)

    # ---- what the selection holds

    def targets(self):
        """The selected recoloured shapes (not Off with only a Stir)."""
        return [t for t in self.app.recolour_targets() if (clean_recolour(t.get("recolour")) or {}).get("mode")
                not in (None, KEEP)]

    def layout(self, sh):
        """sh's {own: the most own colours in a set, least: the fewest Colours can be (Split: 2), used: colours used
        in a set, shares: % per place, pins}."""
        rc = clean_recolour(sh.get("recolour")) or {}
        owns = self.app.colour_owns(sh)
        own = max((len(o) for o in owns), default=1)
        used = max((len(p) for p in palettes(owns, wanted(rc), self.app.set_most())), default=1)
        shares = [round(x * 100, 3) for x in weights(used, rc.get("shares")).tolist()]
        return {"own": own, "least": max(own, 2) if rc.get("mode") in SPLIT else own, "used": used,
                "shares": shares, "pins": [p for p in rc.get("pins", ()) if p < used]}

    def sync(self):
        """Show the selected recoloured shapes' colours and shares (a held knob stays where the mouse put it): a box
        where they differ is blank; boxes being typed in (changed, with the keyboard) keep the typing."""
        app = self.app
        held = self.held_knob()
        tgts = self.targets()
        lays = [self.layout(t) for t in tgts]
        self.what.config(text=tr("shares.nothing") if not tgts else
                         tr("shares.shape", name=app.shape_label(tgts[0])) if len(tgts) == 1 else
                         tr("shares.n_shapes", n=len(tgts)))
        on = bool(lays)
        useds = {L["used"] for L in lays}
        used = max(useds, default=0)
        own = lays[0]["own"] if on else 1
        most = max(app.set_most(), own)
        self.n_entry.config(state="normal" if on else "disabled")
        n_text = str(used) if len(useds) == 1 else ""
        if self.focus_get() is not self.n_entry or self.n_var.get() == self.n_shown:
            self.n_var.set(n_text)
        self.n_shown = n_text
        self.n_tip.text = tr("shares.colours_tip", least=lays[0]["least"] if on else 1, most=most)
        self.even.state(["!disabled" if on and used > 1 else "disabled"])
        rcs = [clean_recolour(t.get("recolour")) for t in tgts]
        areas = bool(rcs) and all(r["mode"] == "clumps" for r in rcs)  # (the shares = area sizes)
        for i, c in enumerate(self.cells):
            show = i < used
            if show != bool(c["cell"].winfo_manager()):
                if show:
                    c["cell"].grid(row=i // COLUMNS, column=i % COLUMNS, sticky="n")
                else:
                    c["cell"].grid_remove()
            if not show:
                continue
            mine = [L for L in lays if i < L["used"]]
            vals = {L["shares"][i] for L in mine}
            first = mine[0]
            c["name"].config(text=tr("shares.colour" if i < first["own"] else "shares.new", n=i + 1))
            if c["knob"] is not held:
                c["knob"].set(round(first["shares"][i], 1))
                c["knob"].on(used > 1)
            text = fmt(first["shares"][i]) if len(vals) == 1 else ""
            if self.focus_get() is not c["entry"] or c["var"].get() == c["shown"]:
                c["var"].set(text)
            c["shown"] = text
            for tip in c["tips"]:
                tip.text = tr("shares.area_tip" if areas else "shares.share_tip") if len(vals) == 1 else \
                    tr("shares.mixed_tip")
            c["entry"].config(state="normal" if used > 1 else "disabled")
            pinned = {i in L["pins"] for L in mine}
            c["lock"].show(pinned.pop() if len(pinned) == 1 else None, used > 1)
        self.hint.config(text=tr("shares.hint_clumps" if areas else "shares.hint"))
        self.sync_clumps([r for r in rcs if r["mode"] == "clumps"])
        app.roll.request_redraw()  # (the Centre point dots: roll_centre.py)

    def sync_clumps(self, rcs):
        """The Clumps rows: shown while a selected shape uses Split: Clumps (what they set goes to those only)."""
        shown = bool(rcs)
        if shown != bool(self.clumps_box.winfo_manager()):
            if shown:
                self.clumps_box.pack(anchor="w", fill="x", before=self.hint)
            else:
                self.clumps_box.pack_forget()
        if not shown:
            return
        pats = {r.get("pattern", "bands") for r in rcs}
        if len(pats) == 1:
            self.pattern.current(PATTERNS.index(next(iter(pats))))
        show_mixed(self.pattern, len(pats) > 1, self.pattern_tip, tr("shares.pattern_tip"))
        turned = pats != {"bands"}
        for name, knob, vals, on in (
                ("angle", self.angle_knob, [r.get("angle", 0.0) for r in rcs], True),
                ("tight", self.tight_knob, [r.get("tight", TIGHT) for r in rcs], "swirl" in pats),
                ("centre0", None, [r.get("centre", [50, 50])[0] for r in rcs], turned),
                ("centre1", None, [r.get("centre", [50, 50])[1] for r in rcs], turned)):
            if knob and knob is not self.held_knob():
                knob.set(round(vals[0], 1))
                knob.on(on)
            self.show_box(name, vals, on)
        pushes = {tuple(r.get("push", (0, 0))) for r in rcs}
        px, py = next(iter(pushes))
        if len(pushes) > 1:
            text = tr("shares.push_mixed")
        elif not (px or py):
            text = tr("shares.push_no")
        else:
            text = ", ".join(tr("shares.pushed_" + way, n=abs(v)) for v, way in
                             ((px, "right" if px > 0 else "left"), (py, "up" if py > 0 else "down")) if v)
        self.push_text.config(text=text)
        for way, ok in (("up", py < MOST_PUSH), ("down", py > -MOST_PUSH), ("left", px > -MOST_PUSH),
                        ("right", px < MOST_PUSH)):
            self.pushes[way].state(["!disabled" if ok or len(pushes) > 1 else "disabled"])
        self.push_none.state(["!disabled" if pushes != {(0, 0)} else "disabled"])
        framed = any(r.get("frame") for r in rcs)
        if framed != bool(self.framed.winfo_manager()):
            if framed:
                self.framed.pack(anchor="w", fill="x", pady=(6, 0))
            else:
                self.framed.pack_forget()

    def show_box(self, name, vals, on):
        """A Clumps number box shows vals (blank where they differ), unless being typed in."""
        b = self.boxes[name]
        text = fmt(vals[0]) if len(set(vals)) == 1 else ""
        if self.focus_get() is not b["entry"] or b["var"].get() == b["shown"]:
            b["var"].set(text)
        b["shown"] = text
        b["entry"].config(state="normal" if on else "disabled")

    # ---- changes (each shape on its own list)

    def change(self, fn, name=None, push=True):
        """Each selected shape's shares / pins = fn(its layout) -> (shares, pins); one undo step."""
        def changed(old, sh):
            if not old:
                return old
            got = fn(self.layout(sh))
            if got is None:
                return old
            shares, pins = got
            out = {k: v for k, v in old.items() if k not in ("shares", "pins")}
            if shares is not None:
                out["shares"] = shares
            if pins:
                out["pins"] = pins
            return out
        return self.app.change_recolour(changed, name or tr("recolour.undo_shares"), push)

    def set_share(self, i, v, name=None):
        return self.change(lambda L: (spread(L["shares"], i, v, L["pins"]), L["pins"]) if i < L["used"] else None,
                           name)

    def on_knob(self, i, v, done):
        self.on_turn(self.cells[i]["knob"], done, lambda: self.set_share(i, v))

    def on_turned(self, name, v, done):
        """The Angle / Swirl tightness knob turned."""
        knob = self.angle_knob if name == "angle" else self.tight_knob
        self.on_turn(knob, done, lambda: self.set_clumps(name, v))

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
        self.wheel = None
        c["shown"] = c["var"].get()  # (a sync while the shapes change shows what they got)
        self.set_share(i, v)
        self.sync()

    def on_even_one(self, i):
        self.wheel = None
        self.change(lambda L: (spread(L["shares"], i, 100 / L["used"], L["pins"]), L["pins"])
                    if 1 < L["used"] and i < L["used"] else None)

    def on_even(self):
        self.wheel = None
        self.change(lambda L: (evened(L["shares"], L["pins"]) if L["pins"] else None, L["pins"]))

    def on_pin(self, i):
        on = self.cells[i]["lock"].locked is not True  # (mixed = locked for all, like a half-ticked box)
        self.wheel = None

        def pinned(L):
            if i >= L["used"]:
                return None
            pins = sorted(set(L["pins"]) | {i}) if on else [p for p in L["pins"] if p != i]
            return L["shares"], pins
        self.change(pinned, tr("shares.undo_pin"))
        self.sync()

    def on_colours(self):
        """Colours typed: each set uses that many (each shape: its own ones at least, at most a set's 15 / 16)."""
        if not self.targets() or str(self.n_entry.cget("state")) == "disabled" or self.n_var.get() == self.n_shown:
            return
        try:
            n = int(round(calc(self.n_var.get())))
        except ValueError:
            self.n_var.set(self.n_shown)
            return bad(self.n_entry)
        good(self.n_entry)
        self.wheel = None
        most = self.app.set_most()

        def changed(old, sh):
            if not old:
                return old
            L = self.layout(sh)
            out = {k: v for k, v in old.items() if k != "colours"}
            m = max(L["least"], min(most, n))
            return dict(out, colours=m) if m > L["least"] else out

        self.n_shown = self.n_var.get()
        self.app.change_recolour(changed, tr("recolour.undo_colours"))
        self.sync()

    # ---- Split: Clumps

    def set_clumps(self, key, value, name=None):
        """Each selected Split: Clumps shape's key = value (centre0 / centre1: one side of the Centre point); one
        undo step."""
        def changed(old, sh):
            if not old or old["mode"] != "clumps":
                return old
            if key.startswith("centre"):
                c = list(old.get("centre", [50, 50]))
                c[int(key[-1])] = value
                return dict(old, centre=c)
            return dict(old, **{key: value})
        return self.app.change_recolour(changed, name or tr("shares.undo_clumps"))

    def on_pattern(self):
        i = self.pattern.current()
        if i >= 0:
            self.wheel = None
            self.set_clumps("pattern", PATTERNS[i])
        self.app.roll.focus_set()

    def on_box(self, name):
        b = self.boxes[name]
        if b["var"].get() == b["shown"] or str(b["entry"].cget("state")) == "disabled":
            return
        try:
            v = round(calc(b["var"].get().strip().rstrip("%°")), 3)
            ok = b["lo"] <= v <= b["hi"]
        except ValueError:
            ok = False
        if not ok:
            b["var"].set(b["shown"])
            return bad(b["entry"])
        good(b["entry"])
        self.wheel = None
        b["shown"] = b["var"].get()
        self.set_clumps(name, v)
        self.sync()

    def on_push(self, dx, dy):
        """A Push button: the areas a step more toward that side (dx None: no push)."""
        self.wheel = None

        def pushed(old, sh):
            if not old or old["mode"] != "clumps":
                return old
            if dx is None:
                return {k: v for k, v in old.items() if k != "push"}
            px, py = old.get("push", (0, 0))
            p = [max(-MOST_PUSH, min(MOST_PUSH, px + dx)), max(-MOST_PUSH, min(MOST_PUSH, py + dy))]
            return dict(old, push=p)
        self.app.change_recolour(pushed, tr("shares.undo_push"))

    def take_typing(self):
        """A number typed without Enter is used (closing: like Settings' typed voice limit)."""
        for i, c in enumerate(self.cells):
            if c["cell"].winfo_manager() and c["var"].get() != c["shown"]:
                self.on_typed(i)
        for name, b in self.boxes.items():
            if self.clumps_box.winfo_manager() and b["var"].get() != b["shown"]:
                self.on_box(name)
        if self.n_var.get() != self.n_shown:
            self.on_colours()

    def close(self):
        if self.hold:
            self.call_off(self.hold)
        self.take_typing()
        self.app.shares_window = None
        roll = self.app.roll
        if (roll.drag or ("",))[0] == "rccentre":  # (a Centre point dot held: let go, what it moved kept)
            roll.drag = None
        self.destroy()
        self.app.roll.focus_set()
        self.app.roll.request_redraw()


def open_shares(app):
    w = app.shares_window
    if w:
        w.sync()
        w.lift()
        w.focus_set()
    else:
        SharesWindow(app)
