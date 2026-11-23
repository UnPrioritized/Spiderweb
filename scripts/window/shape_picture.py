"""Shape pictures (QoL I, user 2026-11-23): a small rough picture of a library shape beside a list of shape names:
the side panel's Shape box list (the highlighted row while it's open) and the drawer's shape library (the row under
the mouse). The lines are worked out in the BACKGROUND (plain data only, never Tk there), kept per shape until its
file changes (shape_stamp), then drawn on Tk's side."""
import threading
import tkinter as tk

from files import system
from window import look
from window.drawer_library import load_drawing, shape_stamp

SIZE = 96  # the picture's side, px at 100 % size
EDGE = 6  # px of room round the drawing inside it
ROUGH = 600  # most points per picture (rough: long strokes keep every n-th point)
WAIT_MS = 30  # how often Tk's side looks for a picture made in the background
KEEP_MOST = 256  # pictures kept (all forgotten past it)
LIST_WATCH_MS = 100  # an open dropdown list: how often its highlighted row is looked at


def shape_lines(name):
    """A library shape's lines as [[(x, y), ...], ...] in a 0..1 square (proportions kept, centred, y DOWN), at
    most ROUGH points; None when it can't be read. Runs in the background: plain data only."""
    from notes.custom import stroke_points
    strokes, _ = load_drawing(name)
    if not strokes:
        return None
    lines = [stroke_points(st) for st in strokes]
    lines = [ln for ln in lines if ln]
    total = sum(len(ln) for ln in lines)
    if not total:
        return None
    step = max(1, -(-total // ROUGH))
    lines = [ln[::step] + ([ln[-1]] if (len(ln) - 1) % step else []) for ln in lines]
    us = [u for ln in lines for u, _ in ln]
    vs = [v for ln in lines for _, v in ln]
    u0, v0 = min(us), min(vs)
    w, h = max(us) - u0, max(vs) - v0
    k = 1 / max(w, h, 1e-9)
    du, dv = (1 - w * k) / 2, (1 - h * k) / 2
    return [[(du + (u - u0) * k, 1 - (dv + (v - v0) * k)) for u, v in ln] for ln in lines]


class Pictures:
    """The lines of shapes asked for, made one at a time in the background (only the LAST asked matters)."""

    def __init__(self):
        self.kept = {}  # {name in lower case: (shape_stamp, lines or None)}
        self.want = None  # (name, stamp) still to make
        self.busy = False

    def fresh(self, name, stamp):
        got = self.kept.get(name.lower())
        return got if got and got[0] == stamp else None

    def ask(self, name, widget, done):
        """done(lines) on Tk's side once the picture is ready (at once when kept); not called when another
        shape is asked for first."""
        stamp = shape_stamp(name)
        got = self.fresh(name, stamp)
        if got:
            return done(got[1])
        self.want = (name, stamp)
        if not self.busy:
            self.busy = True
            threading.Thread(target=self.work, daemon=True, name="shape pictures").start()

        def wait():
            if self.want and self.want[0] != name:
                return  # (another asked since)
            got = self.fresh(name, stamp)
            if got:
                return done(got[1])
            try:
                widget.after(WAIT_MS, wait)
            except tk.TclError:  # (the list's window closed)
                pass
        wait()

    def work(self):
        while True:
            job = self.want
            if job is None:
                self.busy = False
                if self.want is None:  # (one asked just now: on)
                    return
                self.busy = True
                continue
            name, stamp = job
            try:
                lines = shape_lines(name)
            except Exception:  # (a broken file: no picture, never a crash)
                lines = None
            if len(self.kept) >= KEEP_MOST:
                self.kept.clear()
            self.kept[name.lower()] = (stamp, lines)
            if self.want == job:
                self.want = None


PICTURES = Pictures()


class ShapePeek:
    """The picture's little window beside a list (on top of a dropdown's open list too)."""

    def __init__(self, owner, scale=1.0, topmost=False):
        self.owner, self.size = owner, max(48, round(SIZE * scale))
        self.topmost = topmost  # (a dropdown's: over its open list, which stays on top; the drawer's: not)
        self.win = None
        self.name = None  # the shape shown / asked for

    def show(self, name, beside, row_y):
        """name's picture beside the widget `beside` (right of it, left when there's no room), its top at screen
        height row_y. name None = hidden."""
        if name == self.name:
            return
        self.name = name
        if not name:
            return self.hide()
        PICTURES.ask(name, self.owner, lambda lines: self.draw(name, lines, beside, row_y))

    def draw(self, name, lines, beside, row_y):
        if self.name != name:
            return
        try:
            if not beside.winfo_ismapped():
                return self.hide()
        except tk.TclError:
            return self.hide()
        if not lines:
            return self.hide(keep=True)
        s = self.size
        if self.win is None:
            self.win = tk.Toplevel(self.owner)
            self.win.wm_overrideredirect(True)
            if self.topmost:  # (over a dropdown's open list, which stays on top; never over other programs:
                self.win.attributes("-topmost", True)  # the drawer's one isn't, hunt 2026-11-23)
            self.canvas = tk.Canvas(self.win, width=s, height=s, background=look.BOARD, highlightthickness=1,
                                    highlightbackground=look.BOARD_EDGE)
            self.canvas.pack()
        c = self.canvas
        c.delete("all")
        room = s - 2 * EDGE
        for ln in lines:
            xy = [v for x, y in ln for v in (EDGE + x * room, EDGE + y * room)]
            if len(xy) >= 4:
                c.create_line(*xy, fill=look.SHAPE_LINE, width=1)
            elif xy:
                c.create_rectangle(xy[0], xy[1], xy[0] + 1, xy[1] + 1, outline=look.SHAPE_LINE)
        from files.system import work_area
        x = beside.winfo_rootx() + beside.winfo_width() + 4
        left, top, right, bottom = work_area(beside, x, row_y)
        if x + s + 2 > right:
            x = beside.winfo_rootx() - s - 6
        y = max(top, min(row_y, bottom - s - 2))
        self.win.wm_geometry(f"+{max(left, x)}+{y}")
        self.win.deiconify()
        self.win.lift()

    def hide(self, keep=False):
        """keep: the name stays (a shape with no picture: not asked again until another row)."""
        if not keep:
            self.name = None
        if self.win is not None:
            try:
                self.win.withdraw()
            except tk.TclError:
                self.win = None


def peek_listbox(lb, scale=1.0, name_of=None):
    """The drawer's library list: the picture of the row under the mouse (none while a button is held).
    name_of(row) = the shape a row shows (None: no picture, e.g. a folder row); else the row's text."""
    peek = ShapePeek(lb, scale)

    def motion(e):
        if e.state & 0x1f00:
            return peek.show(None, lb, 0)
        i = lb.nearest(e.y)
        box = lb.bbox(i) if i >= 0 else None
        if not box or not box[1] <= e.y < box[1] + box[3]:
            return peek.show(None, lb, 0)
        peek.show(name_of(i) if name_of else lb.get(i), lb, lb.winfo_rooty() + box[1])
    lb.bind("<Motion>", motion, add="+")
    peek.motion = motion  # (tests)
    for seq in ("<Leave>", "<ButtonPress>", "<MouseWheel>", "<KeyPress>", "<Destroy>"):  # (a key may change the rows)
        lb.bind(seq, lambda e: peek.show(None, lb, 0), add="+")
    return peek


def peek_dropdown(cb, scale=1.0):
    """A shape name dropdown (name_list): while its list is open, the picture of the highlighted row (mouse or
    arrow keys; the divider row has none)."""
    from window.widgets import open_list
    path = open_list(cb)
    if path is None:
        return None
    pop = cb.tk.call("ttk::combobox::PopdownWindow", cb)
    lb = tk.Listbox.__new__(tk.Listbox)  # (a Python handle on Tk's own list, like ListTip.dropdown)
    lb.tk, lb._w, lb.master, lb.children, lb._tclCommands = cb.tk, path, cb, {}, None
    peek = ShapePeek(cb, scale, topmost=True)
    front = {"seen": False}  # (Spiderweb was in front while the list was open: lists_close_away's rule)
    job = {"id": None}

    def look_at():
        job["id"] = None
        try:
            if not lb.winfo_ismapped():
                return peek.show(None, lb, 0)
            cur = lb.curselection()
            i = cur[0] if cur else None
            name = lb.get(i) if i is not None and i != getattr(cb, "divider_at", None) else None
            box = lb.bbox(i) if name else None
            here = system.program_in_front()
            front["seen"] = front["seen"] or here is True
            if here is False and front["seen"]:  # (another program in front: the list sinks, so the picture goes;
                name = None                          # hunt 2026-11-23)
            if name and box:
                peek.show(name, lb, lb.winfo_rooty() + box[1])
            else:
                peek.show(None, lb, 0)
            job["id"] = cb.after(LIST_WATCH_MS, look_at)
        except tk.TclError:
            pass

    def opened():
        front["seen"] = False
        if job["id"] is None:
            job["id"] = cb.after(LIST_WATCH_MS, look_at)

    def closed():
        if job["id"] is not None:
            cb.after_cancel(job["id"])
            job["id"] = None
        peek.show(None, lb, 0)
    cb.tk.call("bind", pop, "<Map>", "+" + cb.register(opened))
    cb.tk.call("bind", pop, "<Unmap>", "+" + cb.register(closed))
    peek.look_at = look_at  # (tests)
    return peek
