"""The effects pane of the Hz bass window (hzbass.py "Effects"): under the notes, in step with their time.

On the left the effects, each in its own colour. An effect is put on a note by dragging its name onto the note (on
one of several selected notes: onto all of them), or by clicking the name while notes are selected. The pane shows
the lines of the note clicked last: a line through points, 0 % at the bottom, 100 % at the top, over the note's
length. A click on a name highlights that effect: its line is in full colour and the only one that can be grabbed,
the others are faint; with none highlighted they're all in full colour and the nearest one is grabbed.
Drag a point to move it, press on the line for a new point, double click a point to delete it (the last point
takes the effect off the note). The other selected notes with the same effect change by the same amount. Every
change is one undo step of the main window, made when the mouse is let go."""

import copy
import tkinter as tk

from files.lang import tr
from notes.hzbass import FX, FX_START, OFF_PITCH, TREMOLO, VIBRATO, group_count
from roll.roll_shared import CTRL, SHIFT

FX_COLOR = {"slant": "#8a3ff0", "groups": "#0a8f8f", "offpitch": "#d0189a", "noisy": "#8a5a14",
            "vibrato": "#00a5d8", "sweep": "#7f8c00", "wah": "#2c3e6b", "tremolo": "#e0607a", "octave": "#1d6b3a",
            "sine": "#b060c0", "square": "#606060", "saw": "#c0a000", "triangle": "#c05a30"}  # (not orange, red, green or blue: selected notes, the red line, the exact tone, notes)


def faint(colour, by=0.6):
    """colour mixed with white."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(v + (255 - v) * by) for v in (r, g, b))


class FxPane:
    def __init__(self, win):
        self.win, self.s = win, win.s
        self.active = None  # the effect highlighted
        self.drag = None
        self.says = ""  # for the window's status line
        self.row_h, self.pad = round(15 * self.s), round(9 * self.s)
        c = self.canvas = tk.Canvas(win, background="white", highlightthickness=0,
                                    height=max(round(112 * self.s), round(8 * self.s) + len(FX) * self.row_h))
        c.bind("<Configure>", lambda e: self.redraw())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-3>", self.on_menu)
        c.bind("<Motion>", self.on_motion)
        c.bind("<Leave>", lambda e: self.say(""))
        c.bind("<MouseWheel>", self.on_wheel)

    # ------------------------------------------------------------ what it shows

    def shown(self):
        """The note whose lines are shown: the one clicked last (None when it's gone)."""
        return next((n for n in self.win.tones if n["id"] == self.win.last), None)

    def targets(self, n=None):
        """The notes a change is for: n (or the note shown) and, when it's one of the selected, those too."""
        win = self.win
        n = n or self.shown()
        picked = [win.tones[i] for i in sorted(win.sel) if i < len(win.tones)]
        if n is None:
            return picked
        return picked if any(p is n for p in picked) else [n]

    def y_of(self, value):
        h = self.canvas.winfo_height()
        return self.pad + (1.0 - value) * (h - 2 * self.pad)

    def value_at(self, y):
        h = self.canvas.winfo_height()
        return min(1.0, max(0.0, 1.0 - (y - self.pad) / max(1, h - 2 * self.pad)))

    def line(self, n, name):
        """[(x, y)] of a note's effect line: flat before its first point and after its last one."""
        win = self.win
        pts = n["fx"][name]
        xy = [(win.x_of(n["t"] + u * n["len"]), self.y_of(v)) for u, v in pts]
        return [(win.x_of(n["t"]), xy[0][1])] + xy + [(win.x_of(n["t"] + n["len"]), xy[-1][1])]

    def grabbable(self, n):
        """The effects of a note whose lines can be grabbed: the highlighted one, or all when none is."""
        return [name for name in FX if name in (n.get("fx") or {}) and self.active in (None, name)]

    def redraw(self):
        c, win, s = self.canvas, self.win, self.s
        c.delete("all")
        w, h, kb = c.winfo_width(), c.winfo_height(), win.kb_w
        if w < 50 or h < 20:
            return
        n = self.shown()
        fx = (n or {}).get("fx") or {}
        for v in (0.0, 0.5, 1.0):
            c.create_line(kb, self.y_of(v), w, self.y_of(v), fill="#e4e4e4")
        if n is not None:  # outside the note: grey
            x0, x1 = max(kb, win.x_of(n["t"])), max(kb, win.x_of(n["t"] + n["len"]))
            for a, b in ((kb, x0), (x1, w)):
                if b > a:
                    c.create_rectangle(a, 0, b, h, fill="#f1f1f1", outline="")
            for x in (x0, x1):
                c.create_line(x, 0, x, h, fill="#c4c4c4")
        if not fx:
            c.create_text((kb + w) / 2, h / 2, text=tr("hz.fx_hint"), fill="#777", width=w - kb - 40 * s,
                          justify="center")
        order = [name for name in FX if name in fx and name != self.active] + [self.active] * (self.active in fx)
        for name in order:
            lit = self.active in (None, name)
            colour = FX_COLOR[name] if lit else faint(FX_COLOR[name])
            xy = self.line(n, name)
            c.create_line(*[v for p in xy for v in p], fill=colour, width=max(2, round(2 * s)) if lit else 1)
            if lit:
                r = 3.5 * s
                for x, y in xy[1:-1]:
                    c.create_rectangle(x - r, y - r, x + r, y + r, fill="white", outline=colour,
                                       width=max(1, round(1.5 * s)))
        c.create_rectangle(0, 0, kb, h, fill="#fafafa", outline="")  # the effects
        for i, name in enumerate(FX):
            y = 4 * s + i * self.row_h + self.row_h / 2
            lit = self.active in (None, name)
            colour = FX_COLOR[name] if lit else faint(FX_COLOR[name], 0.5)
            r = 3 * s
            c.create_rectangle(4 * s, y - r, 4 * s + 2 * r, y + r, outline=colour, fill=colour if name in fx else "")
            c.create_text(8 * s + 2 * r, y, text=tr("hz.fx_" + name), anchor="w", fill=colour,
                          font=("Segoe UI", 8, "bold" if self.active == name else "normal"))
        for v in (0.0, 0.5, 1.0):  # what the heights mean
            c.create_text(w - 4 * s, self.y_of(v), text=f"{v * 100:.0f} %", anchor="e", fill="#999",
                          font=("Segoe UI", 7))
        c.create_line(kb, 0, kb, h, fill="#707070")
        c.create_line(0, 0, w, 0, fill="#707070")

    def say(self, text):
        if text != self.says:
            self.says = text
            self.win.show_status()

    # ------------------------------------------------------------ mouse

    def name_at(self, x, y):
        """The effect whose name is under the mouse (the left column), or None."""
        i = int((y - 4 * self.s) // self.row_h)
        return FX[i] if x < self.win.kb_w and y >= 4 * self.s and 0 <= i < len(FX) else None

    def hit(self, x, y):
        """("point", effect, number) / ("line", effect) of the note shown under the mouse, or None."""
        n = self.shown()
        if n is None or x < self.win.kb_w:
            return None
        r = 6 * self.s
        names = self.grabbable(n)[::-1]
        for name in names:
            for i, (px, py) in enumerate(self.line(n, name)[1:-1]):
                if abs(x - px) <= r and abs(y - py) <= r:
                    return "point", name, i
        for name in names:
            xy = self.line(n, name)
            for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
                if x0 <= x <= x1 and abs(y - (y0 if x1 - x0 < 1e-9 else y0 + (y1 - y0) * (x - x0) / (x1 - x0))) <= r:
                    return "line", name
        return None

    def note_under(self, e):
        """The note of the window's piano roll under the mouse (the mouse was pressed here, in the pane)."""
        roll = self.win.canvas
        hit = self.win.hit(e.x_root - roll.winfo_rootx(), e.y_root - roll.winfo_rooty())
        return hit[1] if hit and hit[0] not in ("in", "out") else None

    def on_motion(self, e):
        name, hit = self.name_at(e.x, e.y), self.hit(e.x, e.y)
        self.canvas.config(cursor="hand2" if name else "fleur" if hit and hit[0] == "point" else
                           "crosshair" if hit else "")
        n = self.shown()
        if name:
            self.say(tr("hz.fx_%s_tip" % name))
        elif hit and hit[0] == "point":
            self.say(self.value_text(hit[1], n["fx"][hit[1]][hit[2]][1]))
        else:
            self.say("")

    @staticmethod
    def value_text(name, value):
        if name == "groups":
            n = int(group_count(value))
            return tr("hz.fx_value_groups" if n > 1 else "hz.fx_value_together", name=tr("hz.fx_" + name), n=n)
        if name == "tremolo":
            return tr("hz.fx_value_beat", name=tr("hz.fx_" + name), n=f"{value * TREMOLO:.3g}")
        if name in ("offpitch", "vibrato"):  # how far apart the lowest and the highest key's tones are / how far
            most = OFF_PITCH if name == "offpitch" else VIBRATO  # the pitch goes up and down
            return tr("hz.fx_value", name=tr("hz.fx_" + name), value=f"{value * most * 100:.3g}")
        return tr("hz.fx_value", name=tr("hz.fx_" + name), value=f"{value * 100:.4g}")

    def on_press(self, e):
        win = self.win
        win.canvas.focus_set()
        self.drag = None
        name = self.name_at(e.x, e.y)
        if name:
            self.drag = {"kind": "name", "fx": name, "x": e.x, "y": e.y, "moved": False}
            return
        hit = self.hit(e.x, e.y)
        if hit is None:
            if e.x >= win.kb_w and self.active is not None:  # a click on nothing: no effect highlighted
                self.active = None
                win.redraw()
            return
        n, name = self.shown(), hit[1]
        before = copy.deepcopy(win.tones)
        if hit[0] == "line":  # a new point where the line was pressed, on every note the change is for
            u = min(1.0, max(0.0, (win.beat_at(e.x) - n["t"]) / n["len"]))
            i = self.add_point(n, name, u)
            for m in self.others(n, name):
                self.add_point(m, name, u)
        else:
            i = hit[2]
        self.drag = {"kind": "point", "fx": name, "i": i, "before": before, "orig": copy.deepcopy(win.tones),
                     "note": n["id"]}
        if hit[0] == "line":
            self.on_drag(e)
        win.redraw()

    @staticmethod
    def add_point(n, name, u):
        """A point put on a note's line at u (the line keeps its shape); its number."""
        pts = n["fx"][name]
        i = sum(1 for p in pts if p[0] <= u)
        v = pts[0][1] if i == 0 else pts[-1][1] if i == len(pts) else (
            pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (u - pts[i - 1][0]) / max(1e-12, pts[i][0] - pts[i - 1][0]))
        pts.insert(i, [u, v])
        return i

    def others(self, n, name):
        """The other notes a change of n's line is for: the selected ones (when n is one of them) with the effect."""
        return [m for m in self.targets(n) if m is not n and name in (m.get("fx") or {})]

    def on_drag(self, e):
        d, win = self.drag, self.win
        if not d:
            return
        if d["kind"] == "name":  # an effect's name dragged onto a note
            if not d["moved"] and abs(e.x - d["x"]) < 4 and abs(e.y - d["y"]) < 4:
                return
            d["moved"] = True
            over = self.note_under(e)
            self.canvas.config(cursor="hand2" if over is not None else "X_cursor")
            if over != win.drop_hover or win.drop_colour != FX_COLOR[d["fx"]]:
                win.drop_hover, win.drop_colour = over, FX_COLOR[d["fx"]]
                win.redraw()
            return
        n = next((m for m in win.tones if m["id"] == d["note"]), None)
        if n is None:
            return
        name, i = d["fx"], d["i"]
        by = {m["id"]: m for m in d["orig"]}
        was = by[n["id"]]["fx"][name][i]
        pts = n["fx"][name]
        lo, hi = (pts[i - 1][0] if i else 0.0), (pts[i + 1][0] if i + 1 < len(pts) else 1.0)
        u = min(hi, max(lo, (win.snap(win.beat_at(e.x), e) - n["t"]) / n["len"]))
        v = round(self.value_at(e.y), 3 if e.state & SHIFT else 2)
        pts[i][0], pts[i][1] = u, v
        for m in self.others(n, name):  # the same point of the other notes moves by the same amount
            mp, orig = m["fx"][name], by[m["id"]]["fx"][name]
            if i < len(mp) and len(mp) == len(orig):
                lo, hi = (mp[i - 1][0] if i else 0.0), (mp[i + 1][0] if i + 1 < len(mp) else 1.0)
                mp[i][0] = min(hi, max(lo, orig[i][0] + u - was[0]))
                mp[i][1] = min(1.0, max(0.0, orig[i][1] + v - was[1]))
        self.says = self.value_text(name, v)
        win.redraw()

    def on_release(self, e):
        d, win = self.drag, self.win
        self.drag = None
        if not d:
            return
        if d["kind"] == "point":
            self.says = ""
            if win.tones != d["before"]:
                win.commit(tr("hz.step_fx"), d["before"])
            return
        name = d["fx"]
        over = self.note_under(e) if d["moved"] else None
        win.drop_hover = None
        self.canvas.config(cursor="hand2")
        if d["moved"]:
            if over is None:
                return win.redraw()
            notes = self.targets(win.tones[over])
            win.last = win.tones[over]["id"]
        else:  # a click: onto the selected notes that haven't got it; they all have it: highlighted (or not)
            notes = [m for m in self.targets() if name not in (m.get("fx") or {})] if win.sel else []
            if not notes:
                self.active = None if self.active == name else name
                return win.redraw()
        self.put(name, notes)

    def put(self, name, notes):
        """An effect put on notes (those that have it keep their line); it's the one highlighted then."""
        win = self.win
        before = copy.deepcopy(win.tones)
        for m in notes:
            m.setdefault("fx", {}).setdefault(name, copy.deepcopy(FX_START[name]))
        self.active = name
        if win.tones != before:
            win.commit(tr("hz.step_fx"), before)
        else:
            win.redraw()

    def on_double(self, e):
        """A double click on a point deletes it; the last point takes the effect off the note."""
        hit = self.hit(e.x, e.y)
        if hit and hit[0] == "point":
            self.delete_point(hit[1], hit[2])
        else:
            self.on_press(e)

    def delete_point(self, name, i):
        win, n = self.win, self.shown()
        before = copy.deepcopy(win.tones)
        count = len(n["fx"][name])
        for m in [n] + [m for m in self.others(n, name) if len(m["fx"][name]) == count]:
            del m["fx"][name][i]
            if not m["fx"][name]:
                self.take_off(m, name)
        self.drag = None
        win.commit(tr("hz.step_fx"), before)

    @staticmethod
    def take_off(n, name):
        n["fx"].pop(name, None)
        if not n["fx"]:
            del n["fx"]

    def remove(self, name):
        """The effect taken off the notes a change is for."""
        win = self.win
        before = copy.deepcopy(win.tones)
        for m in self.targets():
            if name in (m.get("fx") or {}):
                self.take_off(m, name)
        if win.tones != before:
            win.commit(tr("hz.step_fx"), before)

    def on_menu(self, e):
        name, hit = self.name_at(e.x, e.y), self.hit(e.x, e.y)
        menu = tk.Menu(self.win, tearoff=0)
        if hit and hit[0] == "point":
            menu.add_command(label=tr("hz.fx_delete_point"), command=lambda: self.delete_point(hit[1], hit[2]))
        for fx in [name or (hit and hit[1])] if name or hit else FX:
            has = any(fx in (m.get("fx") or {}) for m in self.targets())
            menu.add_command(label=tr("hz.fx_remove", name=tr("hz.fx_" + fx)), command=lambda fx=fx: self.remove(fx),
                             state="normal" if has else "disabled")
        menu.tk_popup(e.x_root, e.y_root)

    def on_wheel(self, e):
        """The time of the notes above: wheel = sideways, Ctrl = zoom around the mouse."""
        win = self.win
        up = e.delta > 0
        if e.state & CTRL:
            b = win.beat_at(e.x)
            win.sx = min(100000.0, max(0.05, win.sx * (1.25 if up else 0.8)))
            win.t0 = b - (e.x - win.kb_w) / win.sx
        else:
            win.t0 += (-1 if up else 1) * 120 / win.sx
        win.clamp_view()
        win.redraw()
