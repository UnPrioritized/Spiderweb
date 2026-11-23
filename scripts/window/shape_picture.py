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
NEAR = 0.004  # (share of the drawing's size, ~1/3 px at 100 %) a point this near the last one kept is left out:
#              every corner stays (stage P, user 2026-11-24: keeping every n-th point cut corners = stray lines)
WAIT_MS = 30  # how often Tk's side looks for a picture made in the background
KEEP_MOST = 256  # pictures kept (all forgotten past it)
LIST_WATCH_MS = 100  # an open dropdown list: how often its highlighted row is looked at


def thinned(ln):
    """A line without the points closer than NEAR to the last one kept (its ends always kept)."""
    out = [ln[0]]
    lx, ly = ln[0]
    for x, y in ln[1:-1]:
        if abs(x - lx) + abs(y - ly) >= NEAR:
            out.append((x, y))
            lx, ly = x, y
    if len(ln) > 1:
        out.append(ln[-1])
    return out


def shape_lines(name, size=SIZE):
    """A library shape's picture: (lines as [[(x, y), ...], ...] in a 0..1 square (proportions kept, centred,
    y DOWN), the area colours painted by hand as a size x size RGB array or None); None when it can't be read.
    Runs in the background: plain data only."""
    got = picture_lines(name)
    if got is None:
        return None
    lines, strokes, areas, box = got
    return lines, (area_colours(strokes, areas, box, size) if areas else None)


def picture_lines(name):
    """shape_lines' quick part: (lines, the strokes, areas painted by hand, the box laid out) or None. The colours
    (area_colours, 10-40x slower: stage P, measured on the user's library) come after, as a second step."""
    from notes.custom import stroke_points
    strokes, areas = load_drawing(name)
    if not strokes:
        return None
    lines = [stroke_points(st) for st in strokes]
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    us = [u for ln in lines for u, _ in ln]
    vs = [v for ln in lines for _, v in ln]
    u0, v0 = min(us), min(vs)
    w, h = max(us) - u0, max(vs) - v0
    k = 1 / max(w, h, 1e-9)
    du, dv = (1 - w * k) / 2, (1 - h * k) / 2
    lines = [thinned([(du + (u - u0) * k, 1 - (dv + (v - v0) * k)) for u, v in ln]) for ln in lines]
    return lines, strokes, areas, (u0, v0, k, du, dv)


def area_colours(strokes, areas, box, size):
    """The areas coloured by hand as the drawer shows them (drawer_areas.area_image, its colours), size x size
    RGB, laid like shape_lines' lines (box = their u0, v0, scale, margins); None: no areas found."""
    import numpy as np
    from files.colours import rgb
    from notes.custom import DRAWN_FRAME, area_paint, filled_spots, shape_areas
    from window.drawer_areas import AREA_EMPTY, AREA_NORMAL, area_color
    from notes.areas import ALL_COLOURS
    sh = {"kind": "custom", "strokes": strokes, "pts": DRAWN_FRAME, "fill": "fill"}
    amap = shape_areas(sh)
    if amap is None:
        return None
    fc, area, x, y, g = amap.exact()
    filled = np.zeros(len(area), bool)
    filled[g] = filled_spots(sh, x, y)
    u0, v0, k, du, dv = box
    room = size - 2 * EDGE
    at = (np.arange(size) + 0.5 - EDGE) / room
    u, v = u0 + (at - du) / k, v0 + (1 - at - dv) / k
    face = fc.area_grid(u, v)
    paint = area_paint({"areas": areas}, amap)[area]
    lut = np.zeros((len(area), 3), np.uint8)
    tint = np.zeros(len(area), bool)
    normal = filled & (paint < 0)
    lut[normal], tint[normal] = rgb(AREA_NORMAL), True
    lut[paint == 0], tint[paint == 0] = rgb(AREA_EMPTY), True
    for c in range(1, ALL_COLOURS + 1):
        lut[paint == c], tint[paint == c] = rgb(area_color(c)), True
    img = np.empty((size, size, 3), np.uint8)
    img[:] = rgb(look.BOARD)
    return np.where(tint[face][..., None], lut[face], img).astype(np.uint8)


class Pictures:
    """The lines of shapes asked for, made one at a time in the background (only the LAST asked matters)."""

    def __init__(self):
        self.kept = {}  # {(name in lower case, size): (shape_stamp, shape_lines' answer or None)}
        self.want = None  # (name, stamp, size) still to make
        self.busy = False

    def fresh(self, name, stamp, size):
        got = self.kept.get((name.lower(), size))
        return got if got and got[0] == stamp else None

    def ask(self, name, widget, done, size=SIZE):
        """done(shape_lines' answer) on Tk's side once the picture is ready (at once when kept); a shape with
        painted areas calls it twice: its lines first, then with the colours (stage P: lines stay as quick as
        before). Not called when another shape is asked for first."""
        stamp = shape_stamp(name)
        got = self.fresh(name, stamp, size)
        if got:
            done(got[1])
            if got[2]:
                return
        self.want = (name, stamp, size)
        if not self.busy:
            self.busy = True
            threading.Thread(target=self.work, daemon=True, name="shape pictures").start()
        shown = [got[1] if got else False]

        def wait():
            if self.want and self.want[0] != name:
                return  # (another asked since)
            got = self.fresh(name, stamp, size)
            if got and got[1] is not shown[0]:
                shown[0] = got[1]
                done(got[1])
            if got and got[2]:
                return
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
            name, stamp, size = job
            key = (name.lower(), size)
            try:
                got = picture_lines(name)
            except Exception:  # (a broken file: no picture, never a crash)
                got = None
            if len(self.kept) >= KEEP_MOST:
                self.kept.clear()
            # (kept: stamp, (lines, colours), complete; the colours only while this shape is still the one asked)
            self.kept[key] = (stamp, got and (got[0], None), not (got and got[2]))
            if got and got[2] and self.want == job:
                try:
                    colours = area_colours(got[1], got[2], got[3], size)
                except Exception:  # (areas that can't be worked out: the lines alone)
                    colours = None
                self.kept[key] = (stamp, (got[0], colours), True)
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
        PICTURES.ask(name, self.owner, lambda got: self.draw(name, got, beside, row_y), self.size)

    def draw(self, name, got, beside, row_y):
        lines, colours = got or (None, None)
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
            box = tk.Frame(self.win, background=look.BOARD, highlightthickness=1,
                           highlightbackground=look.BOARD_EDGE)
            box.pack()
            self.canvas = tk.Canvas(box, width=s, height=s, background=look.BOARD, highlightthickness=0)
            self.canvas.pack()
            # (the whole name under the drawing: a long one's tip covered the picture, user 2026-11-24)
            self.caption = tk.Label(box, background=look.BOARD, foreground=look.PEEK_TEXT, font=look.font(8),
                                    wraplength=s - 4, justify="center")
            self.caption.pack(fill="x", padx=2, pady=(0, 2))
        self.caption.config(text=name)
        c = self.canvas
        c.delete("all")
        if colours is not None:  # (the areas coloured by hand, under the lines)
            from files.speed import Photo
            if getattr(self, "pic", None) is None:
                self.pic = Photo(self.win, s, s)
            self.pic.put(colours)
            c.create_image(0, 0, image=self.pic.photo, anchor="nw")
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
        self.win.update_idletasks()
        y = max(top, min(row_y, bottom - self.win.winfo_reqheight() - 2))
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
    cb.pictured = True  # (ListTip.dropdown: a shape row's name is on its picture, no tip of its own)
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
            from window.widgets import skipped  # (the divider and folder headings have none)
            name = lb.get(i).lstrip(" ") if i is not None and i not in skipped(cb) else None
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
