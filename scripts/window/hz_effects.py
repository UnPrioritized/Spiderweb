"""The effects pane of the Hz bass window (hzbass.py "Effects"): under the notes, in step with their time.

On the left the effects, each in its own colour. Clicking a name puts that effect on the Hz bass: one line over all
the notes, through points at beats, 0 % at the bottom, 100 % at the top (flat before the first point and after the
last). Clicking a name that's on highlights it: its line is in full colour and the only one that can be grabbed, the
others are faint; with none highlighted they're all in full colour and the nearest one is grabbed.
Drag a point to move it, press on the line for a new point, double click a point to delete it (the last point
takes the effect off). Every change is one undo step of the main window, made when the mouse is let go."""

import copy
import tkinter as tk

from files.lang import tr
from notes.hzbass import FX, FX_START, OFF_PITCH, TREMOLO, VIBRATO, group_count, tones_span
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

    def y_of(self, value):
        h = self.canvas.winfo_height()
        return self.pad + (1.0 - value) * (h - 2 * self.pad)

    def value_at(self, y):
        h = self.canvas.winfo_height()
        return min(1.0, max(0.0, 1.0 - (y - self.pad) / max(1, h - 2 * self.pad)))

    def line(self, name):
        """[(x, y)] of an effect's line: its points, and flat out to both sides of the pane."""
        win = self.win
        xy = [(win.x_of(b), self.y_of(v)) for b, v in win.fxl[name]]
        return [(min(win.kb_w, xy[0][0]), xy[0][1])] + xy + [(max(self.canvas.winfo_width(), xy[-1][0]), xy[-1][1])]

    def grabbable(self):
        """The effects whose lines can be grabbed: the highlighted one, or all when none is."""
        return [name for name in FX if name in self.win.fxl and self.active in (None, name)]

    def redraw(self):
        c, win, s = self.canvas, self.win, self.s
        c.delete("all")
        w, h, kb = c.winfo_width(), c.winfo_height(), win.kb_w
        if w < 50 or h < 20:
            return
        fx = win.fxl
        for v in (0.0, 0.5, 1.0):
            c.create_line(kb, self.y_of(v), w, self.y_of(v), fill="#e4e4e4")
        if win.tones:  # before and after the notes: grey
            x0, x1 = max(kb, win.x_of(0.0)), max(kb, win.x_of(tones_span(win.tones)))
            for a, b in ((kb, x0), (x1, w)):
                if b > a:
                    c.create_rectangle(a, 0, b, h, fill="#f1f1f1", outline="")
        for n in win.tones:  # where the notes are, faintly
            c.create_rectangle(max(kb, win.x_of(n["t"])), h - 3 * s, max(kb, win.x_of(n["t"] + n["len"])), h,
                               fill="#c8d6f5", outline="")
        if not fx:
            c.create_text((kb + w) / 2, h / 2, text=tr("hz.fx_hint"), fill="#777", width=w - kb - 40 * s,
                          justify="center")
        order = [name for name in FX if name in fx and name != self.active] + [self.active] * (self.active in fx)
        for name in order:
            lit = self.active in (None, name)
            colour = FX_COLOR[name] if lit else faint(FX_COLOR[name])
            xy = self.line(name)
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
        """("point", effect, number) / ("line", effect) under the mouse, or None."""
        if x < self.win.kb_w:
            return None
        r = 6 * self.s
        names = self.grabbable()[::-1]
        for name in names:
            for i, (px, py) in enumerate(self.line(name)[1:-1]):
                if abs(x - px) <= r and abs(y - py) <= r:
                    return "point", name, i
        for name in names:
            xy = self.line(name)
            for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
                if x0 <= x <= x1 and abs(y - (y0 if x1 - x0 < 1e-9 else y0 + (y1 - y0) * (x - x0) / (x1 - x0))) <= r:
                    return "line", name
        return None

    def on_motion(self, e):
        name, hit = self.name_at(e.x, e.y), self.hit(e.x, e.y)
        self.canvas.config(cursor="hand2" if name else "fleur" if hit and hit[0] == "point" else
                           "crosshair" if hit else "")
        if name:
            self.say(tr("hz.fx_%s_tip" % name))
        elif hit and hit[0] == "point":
            self.say(self.value_text(hit[1], self.win.fxl[hit[1]][hit[2]][1]))
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
            self.drag = {"kind": "name", "fx": name}
            return
        hit = self.hit(e.x, e.y)
        if hit is None:
            if e.x >= win.kb_w and self.active is not None:  # a click on nothing: no effect highlighted
                self.active = None
                win.redraw()
            return
        name = hit[1]
        before = copy.deepcopy(win.fxl)
        if hit[0] == "line":  # a new point where the line was pressed
            i = self.add_point(win.fxl[name], max(0.0, win.beat_at(e.x)))
        else:
            i = hit[2]
        self.drag = {"kind": "point", "fx": name, "i": i, "before": before}
        if hit[0] == "line":
            self.on_drag(e)
        win.redraw()

    @staticmethod
    def add_point(pts, beat):
        """A point put on a line at beat (the line keeps its shape); its number."""
        i = sum(1 for p in pts if p[0] <= beat)
        v = pts[0][1] if i == 0 else pts[-1][1] if i == len(pts) else (
            pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (beat - pts[i - 1][0]) / max(1e-12, pts[i][0] - pts[i - 1][0]))
        pts.insert(i, [beat, v])
        return i

    def on_drag(self, e):
        d, win = self.drag, self.win
        if not d or d["kind"] != "point":
            return
        name, i = d["fx"], d["i"]
        pts = win.fxl[name]
        lo, hi = (pts[i - 1][0] if i else 0.0), (pts[i + 1][0] if i + 1 < len(pts) else float("inf"))
        pts[i][0] = min(hi, max(lo, win.snap(win.beat_at(e.x), e)))
        pts[i][1] = v = round(self.value_at(e.y), 3 if e.state & SHIFT else 2)
        self.says = self.value_text(name, v)
        win.redraw()

    def on_release(self, e):
        d, win = self.drag, self.win
        self.drag = None
        if not d:
            return
        if d["kind"] == "point":
            self.says = ""
            if win.fxl != d["before"]:
                win.commit_fx(d["before"])
            return
        name = d["fx"]
        if self.name_at(e.x, e.y) != name:
            return
        if name in win.fxl:  # on already: highlighted (or not any more)
            self.active = None if self.active == name else name
            return win.redraw()
        self.put(name)

    def put(self, name):
        """An effect put on, its line going over all the notes (or what's in view without notes); it's the one
        highlighted then."""
        win = self.win
        before = copy.deepcopy(win.fxl)
        if win.tones:
            a, b = 0.0, tones_span(win.tones)
        else:
            a, b = max(0.0, win.beat_at(win.kb_w)), max(0.0, win.beat_at(win.canvas.winfo_width()))
        win.fxl[name] = [[a + u * (b - a), v] for u, v in FX_START[name]]
        self.active = name
        win.commit_fx(before)

    def on_double(self, e):
        """A double click on a point deletes it; the last point takes the effect off."""
        hit = self.hit(e.x, e.y)
        if hit and hit[0] == "point":
            self.delete_point(hit[1], hit[2])
        else:
            self.on_press(e)

    def delete_point(self, name, i):
        win = self.win
        before = copy.deepcopy(win.fxl)
        del win.fxl[name][i]
        if not win.fxl[name]:
            del win.fxl[name]
        self.drag = None
        win.commit_fx(before)

    def remove(self, name):
        """The effect taken off."""
        win = self.win
        before = copy.deepcopy(win.fxl)
        if win.fxl.pop(name, None) is not None:
            win.commit_fx(before)

    def on_menu(self, e):
        name, hit = self.name_at(e.x, e.y), self.hit(e.x, e.y)
        menu = tk.Menu(self.win, tearoff=0)
        if hit and hit[0] == "point":
            menu.add_command(label=tr("hz.fx_delete_point"), command=lambda: self.delete_point(hit[1], hit[2]))
        for fx in [name or (hit and hit[1])] if name or hit else FX:
            menu.add_command(label=tr("hz.fx_remove", name=tr("hz.fx_" + fx)), command=lambda fx=fx: self.remove(fx),
                             state="normal" if fx in self.win.fxl else "disabled")
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
