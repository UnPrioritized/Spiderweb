"""Small Tk helpers shared by the windows."""

import re
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

from files.lang import size as lang_size, tr
from files.mathexpr import calc, fmt
from window import look

SHIFT, CTRL = 0x1, 0x4
DRAG_PX = 4  # pixels of label dragging per step
TIP_WIDTH = 560  # tooltips wrap longer lines at this width (at 100 % scaling), so a text needs no line breaks
TALL = 120  # px: a widget taller than this gets its tooltip under the mouse instead of under itself


SMALLEST_FIT = 7  # points: a squeezed dropdown's text never gets smaller than this


def fit_dropdowns(root):
    """Every dropdown fits its longest choice (measured in its own font, so wide letters count right) the first
    time it shows, and again if its choices changed: it only ever GROWS, so English looks as it was. Dropdowns
    holding names (shapes, fonts, devices...) opt out with keep_width; the synth window's keep their size and
    make the text smaller instead (its knob grid stays put)."""
    root.bind_class("TCombobox", "<Map>", lambda e: fit_dropdown(e.widget), add="+")


def line_up(rows, labels=()):
    """Rows (frames laid out with grid) line up like one table: each column as wide as its widest cell in any of
    them. A cell spanning several columns doesn't count (a dropdown or a long unit at a row's end). labels: rows
    lined up by their first column only (their labels count too)."""
    widest = {}
    for row in list(rows) + list(labels):
        for w in row.grid_slaves(column=None if row in rows else 0):
            info = w.grid_info()
            if int(info["columnspan"]) > 1:
                continue
            pad = info["padx"]
            pad = sum(int(p) for p in (pad if isinstance(pad, tuple) else str(pad).split()))
            c = int(info["column"])
            widest[c] = max(widest.get(c, 0), w.winfo_reqwidth() + pad)
    for row in rows:
        for c, px in widest.items():
            row.columnconfigure(c, minsize=px)
    for row in labels:
        row.columnconfigure(0, minsize=widest.get(0, 0))


def label_column(labels):
    """Labels packed at the left of their own rows: padded to the widest one, so what comes after them starts at
    one place (like a table's first column). Returns that width in pixels."""
    widest = max(lb.winfo_reqwidth() for lb in labels)
    for lb in labels:
        lb.pack_configure(padx=(0, widest - lb.winfo_reqwidth()))
    return widest


def keep_width(cb):
    """This dropdown keeps the width it was given (its choices are names someone typed, not texts); a name cut off
    in its open list shows whole in a tip (ListTip)."""
    if not getattr(cb, "keep_width", False):
        ListTip.dropdown(cb)
    cb.keep_width = True
    return cb


def fit_dropdown(cb):
    import math
    from tkinter import font as tkfont
    if not isinstance(cb, ttk.Combobox) or getattr(cb, "keep_width", False):
        return
    try:
        values = (tuple(str(v) for v in cb.cget("values") if str(v) != DIVIDER)  # (name_list's grey line: no room)
                  + tuple(getattr(cb, "fit_also", ())))
        if not values or getattr(cb, "fitted_for", None) == values:
            return
        cb.fitted_for = values
        f = tkfont.Font(cb, font=cb.cget("font") or "TkTextFont")
        width = int(cb.cget("width"))
        zero = max(1, f.measure("0"))  # (a dropdown's width counts "0"s)
        need = math.ceil(max(f.measure(v) for v in values) / zero)
        if need <= width:
            return
        if not str(cb.cget("style")).startswith("Synth."):
            cb.config(width=need)
            return
        room = width * zero  # (pixels: the box stays this wide, its width then counts the smaller font's "0"s)
        size = f.actual("size") or 9
        sign, size = (-1 if size < 0 else 1), abs(size)  # (below 0 = pixels: kept so)
        while size > SMALLEST_FIT and max(f.measure(v) for v in values) > room:
            size -= 1
            f = tkfont.Font(cb, family=f.actual("family"), size=sign * size, weight=f.actual("weight"))
        cb.fit_font = f  # (kept: Tk forgets a font once Python lets go of it)
        cb.config(font=f, width=max(1, room // max(1, f.measure("0"))))
    except (tk.TclError, ValueError):
        pass


JUMP_MS = 1000  # name lists open: letters typed closer together than this make one word (letter_jump)
DIVIDER = "─" * 12  # name lists: the row between the ready-made names and the saved ones (set_names)


def letter_jump(cb):
    """Typing while the dropdown's list is open jumps to the first name starting with the letters typed (a pause of
    JUMP_MS starts a new word; the same letter again = the next name with it). Returns the jump function (tests)."""
    lb = cb.tk.call("ttk::combobox::PopdownWindow", cb) + ".f.l"
    jump = {"text": "", "at": 0}

    def jump_to(char, when):
        if len(char) != 1 or not char.isprintable():
            return
        when = int(when)
        jump["text"] = (jump["text"] if when - jump["at"] < JUMP_MS else "") + char.casefold()
        jump["at"] = when
        values = cb.tk.splitlist(cb.tk.call(lb, "get", 0, "end"))
        k = next((i for i, f in enumerate(values) if f.casefold().startswith(jump["text"])), None)
        if k is None and len(jump["text"]) > 1:  # (the same letter again: the next name with it)
            jump["text"] = char.casefold()
            now = int(cb.tk.call(lb, "index", "active"))
            k = next((i for i, f in list(enumerate(values))[now + 1:] + list(enumerate(values))[:now + 1]
                      if f.casefold().startswith(jump["text"])), None)
        if k is not None:
            cb.tk.call(lb, "selection", "clear", 0, "end")
            cb.tk.call(lb, "selection", "set", k)
            cb.tk.call(lb, "activate", k)
            cb.tk.call(lb, "see", k)
    cb.tk.call("bind", lb, "<KeyPress>", "+" + cb.register(jump_to) + " %A %t")
    return jump_to


def name_list(cb):
    """A dropdown of names (shapes, palettes, presets...; user 2026-10-14): ready-made names first, a divider row,
    then the saved ones (set_names), letters typed jump (letter_jump), names keep the box's width. The divider can't
    be picked: a click on it changes nothing, the mouse wheel steps over it."""
    keep_width(cb)
    cb.jump = letter_jump(cb)
    cb.divider_at = None
    cb.row_colours = {}  # {row in the open list: its text colour}
    cb.name_before = cb.get()
    pop = cb.tk.call("ttk::combobox::PopdownWindow", cb)
    lb = pop + ".f.l"

    def shown():  # (the list opened, just filled: the divider greyed, the name picked before kept)
        cb.name_before, cb.list_open = cb.get(), True
        for k, colour in cb.row_colours.items():  # (rows with a colour of their own, e.g. orange = a warning)
            if k < int(cb.tk.call(lb, "size")):
                cb.tk.call(lb, "itemconfigure", k, "-foreground", colour)
        k = cb.divider_at
        if k is not None and k < int(cb.tk.call(lb, "size")):
            cb.tk.call(lb, "itemconfigure", k, "-foreground", look.HINT, "-selectforeground", look.HINT,
                       "-selectbackground", cb.tk.call(lb, "cget", "-background"))

    def hidden():
        cb.list_open = False
    cb.list_open = False
    cb.tk.call("bind", pop, "<Map>", "+" + cb.register(shown))
    cb.tk.call("bind", pop, "<Unmap>", "+" + cb.register(hidden))

    def picked(e):
        k = cb.divider_at
        if k is None or cb.current() != k:
            cb.name_before = cb.get()
            return None
        values = cb.cget("values")
        if cb.list_open or not values:  # (a click on the divider: as before, nothing picked)
            cb.set(cb.name_before)
            return "break"
        before = list(values).index(cb.name_before) if cb.name_before in values else k - 1
        cb.current(k + 1 if before < k and k + 1 < len(values) else k - 1)  # (the wheel: over it)
        cb.name_before = cb.get()
        return None
    tag = f"NameList{cb}"
    cb.bind_class(tag, "<<ComboboxSelected>>", picked)

    def wheel(e):  # (what it shows NOW is where the wheel starts: set by the program since, e.g. undo, counts)
        if not cb.list_open:
            cb.name_before = cb.get()
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):  # (before the dropdown's own wheel step)
        cb.bind_class(tag, seq, wheel)
    cb.bindtags((tag,) + cb.bindtags())
    return cb


def set_names(cb, top, saved):
    """A name_list's choices: the ready-made names, the divider (only when both kinds are there), the saved ones."""
    values = list(top) + ([DIVIDER] if top and saved else []) + list(saved)
    cb.divider_at = len(top) if top and saved else None
    cb.config(values=values)
    return values


def name_index(cb):
    """The picked choice's place in the names as given to set_names (ready-made, then saved: the divider not
    counted), or -1 (none, or the divider)."""
    k, d = cb.current(), getattr(cb, "divider_at", None)
    return -1 if k < 0 or k == d else k - 1 if d is not None and k > d else k


def on_screen(widget, x, y):
    """Is the point (x, y) (screen pixels) on one of the screens? (Windows: any monitor; elsewhere the screen Tk
    knows, which on Linux spans them all.)"""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            find = ctypes.WINFUNCTYPE(ctypes.c_void_p, wintypes.POINT, wintypes.DWORD)(
                ("MonitorFromPoint", ctypes.windll.user32))
            return bool(find(wintypes.POINT(x, y), 0))  # (0: no monitor there = none)
        except (OSError, AttributeError):
            pass
    return 0 <= x < widget.winfo_screenwidth() and 0 <= y < widget.winfo_screenheight()


def placed(widget, geo):
    """A window's saved geometry ("WxH+X+Y" / "+X+Y") without its place when its title bar would be off every screen
    (a screen unplugged / made smaller since, user 2026-10-07): it opens where it would the first time. "" = nothing
    left to use."""
    m = re.fullmatch(r"(\d+x\d+)?\+(-?\d+)\+(-?\d+)", geo or "")
    if m and not on_screen(widget, int(m[2]) + 60, int(m[3]) + 15):
        return m[1] or ""
    return geo or ""


def fit_screen(win):
    """Keeps a window whole on its screen, above the taskbar (a big Size / screen scaling on a small screen opened
    windows past its bottom edge): one that can be resized is made smaller, then moved in. Call it after the window
    has its first size / place (remember_place does). A window not shown yet is fitted once its contents are made
    (measuring it now showed it empty first): one that can't be resized just before it shows, one that can right
    after (until then Tk gives its size as 1x1). One that can't be resized is moved in again when it grows (rows
    shown, another page)."""
    if not any(win.resizable()) and not getattr(win, "_fit_size", None):
        win._fit_size = [None]

        def grown(e):
            if e.widget is win and win._fit_size[0] not in (None, (e.width, e.height)):
                win.after_idle(lambda: win.winfo_exists() and _fit_screen(win))
            if e.widget is win:
                win._fit_size[0] = (e.width, e.height)
        win.bind("<Configure>", grown, add="+")
    if win.winfo_ismapped():
        _fit_screen(win)
        return

    def made():
        if not win.winfo_exists():
            return
        if not any(win.resizable()):
            _fit_screen(win)
            return
        once = []

        def shown(e):
            if e.widget is win and not once:
                once.append(True)
                _fit_screen(win)
        win.bind("<Map>", shown, add="+")
    win.after_idle(made)


def _fit_screen(win):
    from files.system import work_area
    if win.state() in ("zoomed", "withdrawn"):
        return
    win.update_idletasks()
    m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", win.wm_geometry())
    if not m:
        return
    w, h, x, y = map(int, m.groups())
    if w <= 1 or h <= 1:  # (no size given: as big as it asks)
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    if win.winfo_ismapped():  # the frame Windows adds: the title bar and the edges (not shown yet: about as big)
        side, title = max(0, win.winfo_rootx() - win.winfo_x()), max(0, win.winfo_rooty() - win.winfo_y())
    else:
        k = getattr(look, "SCREEN_DPI", 96) / 96
        side, title = round(8 * k), round(31 * k)
    left, top, right, bottom = work_area(win, x + w // 2, y + h // 2)
    sizes = any(win.resizable())
    nw = min(w, right - left - 2 * side) if sizes else w
    nh = min(h, bottom - top - title - side) if sizes else h
    nx = max(left, min(x, right - nw - 2 * side))
    ny = max(top, min(y, bottom - nh - title - side))
    if (nw, nh, nx, ny) == (w, h, x, y):
        return
    if sizes:
        least_w, least_h = win.minsize()
        win.minsize(min(least_w, nw), min(least_h, nh))
    win.geometry(f"{nw}x{nh}+{nx}+{ny}" if sizes else f"+{nx}+{ny}")


PLACE = r"(\d+x\d+)?\+-?\d+\+-?\d+"  # a remembered window place: "WxH+X+Y" (one that can be resized) or "+X+Y"


def remember_place(win, name):
    """User 2026-10-09: every window opens where it was last, and as big when it can be resized. Call it after the
    window has set its own first size / place (that's used when nothing is remembered, or the place is off every
    screen). The places are the main window's window_places (saved in the autosave)."""
    places = win._root().__dict__.setdefault("window_places", {})
    sizes = any(win.resizable())
    geo = placed(win, places.get(name, ""))
    if geo and not sizes:
        geo = geo[geo.index("+"):] if "+" in geo else ""
    if geo:
        win.geometry(geo)
    fit_screen(win)

    def keep(e=None):
        if (e is None or e.widget is win) and win.winfo_ismapped():
            geo = win.wm_geometry()
            places[name] = geo if sizes else geo[geo.index("+"):]
    win.bind("<Configure>", keep, add="+")  # (moved or resized: remembered at once)


def grid_shown(w, on):
    """Show / hide a gridded widget (where it was gridded before), only when that changes: placing a widget again
    lays its window out again, which flashes while a number is stepped."""
    if on and not w.winfo_manager():
        w.grid()
    elif not on and w.winfo_manager():
        w.grid_remove()


def wrap_to_room(label):
    """A label packed with fill="x" (expand=True when packed side by side, and packed after its neighbours, so
    they get their room first): its text wraps onto more lines when it's given less room than one line needs,
    instead of being cut at the edge (user 2026-10-19: no cut texts, also with other Windows languages' wider
    fonts). NEVER in a frame that takes its width from what's in it (the Image window's settings): each wrap
    makes it narrower, which wraps it again, on and on (froze the window)."""
    edge = []  # (the label's own edges beside its text: measured once, while it isn't wrapped yet)

    def fit(e):
        if e.width <= 1:
            return
        if not edge:
            text = str(label.cget("text"))
            font = tkfont.Font(font=label.cget("font") or "TkDefaultFont")
            edge.append(max(4, label.winfo_reqwidth() - max(font.measure(t) for t in text.split("\n"))))
        room = max(1, e.width - edge[0])
        if str(label.cget("wraplength")) != str(room):
            label.configure(wraplength=room)
    label.bind("<Configure>", fit, add="+")
    return label


def chart_edges(s, left_texts, left, bottom):
    """A graph's left and bottom margins (pixels): at least left / bottom (at 100 % scaling), wider / taller when
    the labels drawn there (look.font(7): left_texts beside the plot, a line under it) need more in this font."""
    f = tkfont.Font(font=look.font(7))
    return (max(int(left * s), max(f.measure(t) for t in left_texts) + int(6 * s)),
            max(int(bottom * s), f.metrics("linespace") + int(6 * s)))


class WrapRow:
    """The widgets packed side by side in a frame, laid out again on more lines when the room given is too narrow
    for one (like words). together = tuples of widgets kept on one line (a label and its box). Made once they're
    all packed; the owner calls fit(room) when its width changes, and changed() is called (when idle) whenever
    one of the widgets changes size (a pinned tool, a longer text)."""

    def __init__(self, frame, together=(), changed=None):
        self.frame = frame
        slaves = frame.pack_slaves()
        self.opts = {w: {k: v for k, v in w.pack_info().items() if k != "in"} for w in slaves}
        self.groups, seen = [], set()
        for w in slaves:
            if w not in seen:
                group = next((t for t in together if w in t), (w,))
                group = [x for x in slaves if x in group]
                seen.update(group)
                self.groups.append(group)
        self.lines, self.laid = [], None
        if changed:
            sizes = {}

            def resized(e):
                if e.widget in self.opts and sizes.get(e.widget) != (e.width, e.height):
                    sizes[e.widget] = (e.width, e.height)
                    frame.after_idle(changed)
            for w in slaves:
                w.bind("<Configure>", resized, add="+")

    def width(self, group):
        def pad(p):
            return sum(int(v) for v in (p if isinstance(p, tuple) else str(p).split()))
        return sum(w.winfo_reqwidth() + pad(self.opts[w].get("padx", 0)) for w in group)

    def natural(self):
        """The width all of it takes on one line."""
        return sum(self.width(g) for g in self.groups)

    def widest(self):
        """The width of its widest group (a line can't be narrower without cutting it)."""
        return max((self.width(g) for g in self.groups), default=0)

    def fit(self, room):
        """Lines filled from the first, each as full as room allows (a group wider than room gets a line of its
        own). Returns how many lines."""
        laid, line, used = [], 0, 0
        for g in self.groups:
            w = self.width(g)
            if used and used + w > room:
                line, used = line + 1, 0
            laid.append(line)
            used += w
        laid = tuple(laid)
        if laid != self.laid:
            self.laid = laid
            while len(self.lines) <= line:
                f = ttk.Frame(self.frame)
                f.lower()  # (made after the widgets: kept under them, or it would hide them)
                self.lines.append(f)
            for g in self.groups:
                for w in g:
                    w.pack_forget()
            for i, f in enumerate(self.lines):
                if i <= line:
                    f.pack(side="top", fill="x", pady=(2 if i else 0, 0))  # (fill: side="right" ones at the end)
                else:
                    f.pack_forget()
            for g, i in zip(self.groups, laid):
                for w in g:
                    w.pack(in_=self.lines[i], **self.opts[w])
        return line + 1


def box_text(e):
    """What a box holds (from its variable: while that's being set, the box itself still shows the old text).
    globalgetvar: typing in a box sets its variable from inside Tk's own key code, where a plain getvar looks for
    the name there and fails ("can't read PY_VAR17")."""
    name = str(e.cget("textvariable"))
    return str(e.tk.globalgetvar(name)) if name else e.get()


def no_spaces(var):
    """A box's spaces taken out (Space types one there, user; numbers have none) when the box is left."""
    if " " in var.get():
        var.set(var.get().replace(" ", ""))


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


def mixed_tip(w):
    """The widget's own tooltip for the shared "different settings" sentence (made the first time)."""
    if not hasattr(w, "mixed_tip"):
        w.mixed_tip = Tooltip(w, "")
    return w.mixed_tip


def same_or_blank(e, var, values):
    """A number box for several shapes (call it while app._loading): their number when they all have the same, else
    left empty with the shared tip (user: the box showed the first one's). Typing a number sets it on all of them;
    stepping it starts from the first one's (Scrub). True when they differ."""
    values = [str(v) for v in values]
    mixed = len(set(values)) > 1
    var.set("" if mixed else values[0] if values else "")
    e.blank_from = values[0] if mixed else None
    mixed_tip(e).text = tr("widgets.mixed_tip") if mixed else ""
    return mixed


def show_mixed(box, mixed, tip=None, text=""):
    """A dropdown for several shapes with different choices: shows the shared word (it isn't one of its choices);
    tip: its tooltip, which then starts with the shared sentence (text = its usual text)."""
    if mixed:
        box.set(tr("widgets.mixed"))
        if not getattr(box, "fit_also", ()):  # (the shared word fits too: a long translation of it isn't cut)
            box.fit_also = (tr("widgets.mixed"),)
            box.fitted_for = None
            fit_dropdown(box)
    if tip is not None:
        tip.text = (tr("widgets.mixed_tip") + "\n\n" + text if text else tr("widgets.mixed_tip")) if mixed else text


def remember_good(root):
    """Every number box's last good value starts as what it shows when it gets the keyboard (then good())."""
    def got(ev):
        w = ev.widget
        if isinstance(w, ttk.Entry) and str(w.cget("style")) != "Bad.TEntry":
            w.good_text = w.get()
    root.bind_class("TEntry", "<FocusIn>", got, add="+")


class ScrollingSide:
    """A window's side panel that scrolls (scrollbar, mouse wheel) when everything in it doesn't fit; otherwise it's
    stretched to the window's height (the main window, the drawer)."""
    SIDE_SKIP = (tk.Listbox, tk.Text, ttk.Combobox)  # (the wheel over these leaves the panel alone)

    def make_side(self, width):
        """The panel, side_width pixels wide (wider by the scrollbar while it shows; wider still when the font
        makes texts wider than the usual one, look.WIDE): its frame to fill."""
        self.side_width = width = int(width * look.WIDE)
        box = self.side_box = ttk.Frame(self, width=width)
        box.pack(side="right", fill="y")
        box.pack_propagate(False)
        c = self.side_canvas = tk.Canvas(box, highlightthickness=0, bd=0,
                                         bg=ttk.Style().lookup("TFrame", "background") or "SystemButtonFace")
        self.side_bar = ttk.Scrollbar(box, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=self.side_bar.set)
        c.pack(side="left", fill="both", expand=True)
        side = self.side = ttk.Frame(c, padding=(6, 0, 6, 6))
        self._side_win = c.create_window(0, 0, window=side, anchor="nw")
        c.bind("<Configure>", lambda e: self.fit_side())
        side.bind("<Configure>", lambda e: self.after_idle(self.fit_side))
        self.bind("<MouseWheel>", self.side_wheel, add="+")
        return side

    def fit_side(self):
        c, side = self.side_canvas, self.side
        need, have = side.winfo_reqheight(), c.winfo_height()
        # room to spare: stretched to the panel's height; too tall: its own height (0), so
        # it keeps following what it needs
        c.itemconfigure(self._side_win, width=c.winfo_width(), height=have if need < have else 0)
        c.configure(scrollregion=(0, 0, c.winfo_width(), max(need, have)))
        if need > have + 1:
            if not self.side_bar.winfo_ismapped():  # the panel gets wider by the scrollbar, not narrower inside
                self.side_box.config(width=self.side_width + self.side_bar.winfo_reqwidth())
                self.side_bar.pack(side="right", fill="y", before=c)
        elif self.side_bar.winfo_ismapped():
            self.side_bar.pack_forget()
            self.side_box.config(width=self.side_width)
            c.yview_moveto(0)

    def scroll_side(self, e, anyway=False):
        """The wheel over the panel scrolls it (not over SIDE_SKIP widgets unless anyway). True when it did."""
        if (str(e.widget).startswith(str(self.side_canvas)) and self.side_bar.winfo_ismapped()
                and (anyway or not isinstance(e.widget, self.SIDE_SKIP))):
            self.side_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
            return True
        return False

    def side_wheel(self, e):
        self.scroll_side(e)


class PointGraph:
    """A small graph of points [u, y] (both 0..1) on a canvas self.w x self.h with margins self.ml / mr / mt / mb,
    self.pts the points, self.s the scale (Add between's Spread, Gate Range's graph)."""
    U_SNAP, Y_SNAP = 1 / 40, 1 / 20  # dragging moves points in these steps (Shift = free)

    def u2x(self, u):
        return self.ml + u * (self.w - self.ml - self.mr)

    def y2c(self, y):
        return self.mt + (1 - y) * (self.h - self.mt - self.mb)

    def x2u(self, x):
        return min(1.0, max(0.0, (x - self.ml) / (self.w - self.ml - self.mr)))

    def c2y(self, c):
        return min(1.0, max(0.0, 1 - (c - self.mt) / (self.h - self.mt - self.mb)))

    def point_at(self, x, y):
        """The point under the canvas spot x, y (the nearest within 8 px), or None."""
        r = 8 * self.s
        best = None
        for i, (u, v) in enumerate(self.pts):
            d = max(abs(self.u2x(u) - x), abs(self.y2c(v) - y))
            if d <= r and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def snapped(self, e):
        u, y = self.x2u(e.x), self.c2y(e.y)
        if not e.state & SHIFT:
            u, y = round(u / self.U_SNAP) * self.U_SNAP, round(y / self.Y_SNAP) * self.Y_SNAP
        return u, y


class StatusLine(ttk.Label):
    """The main window's status line. Text put in with config(text=...) is a message: it stays HOLD_MS (user: the
    mouse position wrote over it at once), then the line goes back to what show() gave last (position, counts)."""
    HOLD_MS = 3000

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._normal = ""
        self._job = None
        self._full = kw.get("text", "")  # (the text before cutting it short to fit)
        self.reserve = 0  # pixels kept free at the right end (the red "Overloaded")
        # (text longer than the line ends in "…", the whole of it in a tip; user 2026-10-19: no texts cut off)
        self.tip = Tooltip(self, "")
        self.bind("<Configure>", lambda e: self._put(self._full), add="+")

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict) and "text" in cnf:
            kw, cnf = dict(cnf, **kw), None
        if "text" in kw:
            if self._job:
                self.after_cancel(self._job)
            self._job = self.after(self.HOLD_MS, self.release)
            self._put(kw.pop("text"))
            if not kw:
                return None
        return super().configure(cnf, **kw)

    config = configure

    def keep_free(self, px):
        """Keep px pixels free at the right end (0 = none); the text is cut short to fit beside them."""
        self.reserve = px
        self._put(self._full)

    def _put(self, text):
        self._full = text = str(text)
        pad = [str(p) for p in self.tk.splitlist(self.cget("padding"))]  # (the line's own: left [top right bottom])
        pad = self.winfo_fpixels(pad[0]) + self.winfo_fpixels(pad[2 if len(pad) > 2 else 0]) if pad else 0
        room = self.winfo_width() - self.reserve - pad
        self.tip.text = ""
        if text and self.winfo_width() > 1:
            font = tkfont.Font(font=self.cget("font") or "TkDefaultFont")
            if font.measure(text) > room:
                lo, hi = 0, len(text)  # (the longest start that fits with "…")
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if font.measure(text[:mid] + "…") <= room:
                        lo = mid
                    else:
                        hi = mid - 1
                text = text[:lo].rstrip() + "…"
                self.tip.text = self._full
        super().configure(text=text)

    def show(self, text):
        """The line's usual text: shown now, or when the message on it is done."""
        self._normal = text
        if not self._job:
            self._put(text)

    def release(self):
        """The message is done (its time is up, or e.g. a busy "Copying…" ended): the usual text back."""
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        if self.winfo_exists():
            self._put(self._normal)

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

    def __init__(self, app, boxes, steps, lo=None, hi=None, label=None, drag_box=False, no_undo=False, text=None):
        """drag_box: each box can be dragged sideways itself too (a click without moving still types). no_undo: the
        boxes make no undo step (app settings): Ctrl+Z while held puts their numbers back itself. text: number ->
        what the box shows (default fmt)."""
        self.app, self.boxes, self.steps, self.lo, self.hi = app, boxes, steps, lo, hi
        self.text = text or fmt
        self.no_undo = no_undo
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
        for w in [label] + [entry for entry, _, _ in boxes] if drag_box else [label]:
            if w is not None:  # (its window closed mid-drag, e.g. Esc: the let-go never comes)
                w.bind("<Destroy>", lambda e: self.gone(), add="+")

    def gone(self):
        """A box / label being dragged went away: the drag ends there (no more steps)."""
        if self.drag:
            if self.drag["job"]:
                self.app.after_cancel(self.drag["job"])
            self.drag = self.held = None
            self.app.scrubbing = False
            self.app.held_scrub = None
            self.app.after_idle(self.app.catch_up_notes)

    def step_size(self, state):
        step, big, fine = self.steps() if callable(self.steps) else self.steps
        return big if state & SHIFT else fine if state & CTRL else step

    def change(self, boxes, n, state, gesture):
        """Every box (that's on and holds a number) n steps up (minus: down). self.read (optional): the box's text ->
        its number (default calc); self.step_for (optional): (number, n, state) -> the step size there."""
        d = n * self.step_size(state)
        read = getattr(self, "read", None) or calc
        step_for = getattr(self, "step_for", None)

        def run():
            for entry, var, apply in boxes:
                if str(entry.cget("state")) == "disabled":
                    continue
                try:  # (an empty box for shapes with different numbers: from the first one's, same_or_blank)
                    value = float(read(var.get() or getattr(entry, "blank_from", None) or ""))
                except (ValueError, ZeroDivisionError):
                    continue
                value = round(value + (n * step_for(value, n, state) if step_for else d), 6)
                if self.lo is not None:
                    value = max(self.lo, value)
                if self.hi is not None:
                    value = min(self.hi, value)
                var.set(self.text(value))
                if apply:
                    apply()
        self.app.scrub_step(gesture, run)

    def key(self, e, box):
        self.change([box], 1 if e.keysym == "Up" else -1, e.state, box[0])
        return "break"

    def wheel(self, e, box):
        if self.app.focus_get() is not box[0] or self.app.side_gliding(box[0]):
            return None  # not being typed in / the side panel is being scrolled: the panel scrolls
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
                     "boxes": boxes or self.boxes, "start": [var.get() for _, var, _ in boxes or self.boxes]}
        self.app.scrubbing = True  # (slow notes are made when the mouse rests or is let go: App.shapes_changed)
        self.app.held_scrub = self  # (shortcuts / Delete wait, Ctrl+Z = cancel: App.key_undo)

    def cancel(self):
        """Ctrl+Z while held: the drag ends, the numbers go back to the press with no step (the mouse does nothing
        more until let go)."""
        d = self.drag
        if not d:
            return
        if d["job"]:
            self.app.after_cancel(d["job"])
        if self.held:
            self.held["box"][0].config(cursor="")
        self.drag = self.held = None
        self.app.held_scrub = None
        self.app.take_back_scrub(d["gesture"])
        if self.no_undo:
            for (entry, var, apply), was in zip(d["boxes"], d["start"]):
                if var.get() != was:
                    var.set(was)
                    if apply:
                        apply()

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
        self.app.held_scrub = None
        self.app.catch_up_notes()


# Undo / redo keys. Upper case = Caps Lock on; Ctrl+Shift+Z is redo too (it wins over <Control-Z>: Shift is named)
UNDO_KEYS = ("<Control-z>", "<Control-Z>")
REDO_KEYS = ("<Control-y>", "<Control-Y>", "<Control-Shift-z>", "<Control-Shift-Z>")


class LocalUndo:
    """Ctrl+Z / Ctrl+Y inside a pop-up window: its own steps, which never reach the main window's undo.
    get(): how it is now (a string, e.g. JSON); put(state): make it so. mark(key) after each change: changes with the
    same key (not None) in a row are one step (typing in one box, stepping one number). main: () -> the main window's
    last undo step, for windows whose Ctrl+Z on the piano roll steps theirs first (main_newer)."""

    def __init__(self, win, get, put, limit=300, main=None):
        self.get, self.put, self.limit, self.main = get, put, limit, main
        self.reset()
        for k in UNDO_KEYS:
            win.bind(k, lambda e: (self.undo(), "break")[1])
        for k in REDO_KEYS:
            win.bind(k, lambda e: (self.redo(), "break")[1])

    def reset(self):
        """Start again from how it is now (nothing to undo)."""
        self.states, self.at, self.key = [self.get()], 0, None
        self.seen()

    def seen(self):
        """The main window's steps so far are older than this window's (its own settled step, a step of it made)."""
        self.main_was = self.main() if self.main else None

    def main_newer(self):
        """Has the main window made a step since this window's last one (a shape changed while open)?"""
        return self.main is not None and self.main() is not self.main_was

    def mark(self, key=None, end=False):
        """end: the last change of a keyed gesture (a drag let go): it joins the gesture's step, then the gesture
        is over (a slow preview catching up on let go made a second step, so Ctrl+Z seemed to do nothing)."""
        state = self.get()
        if state == self.states[self.at]:
            if key is None or end:  # (a gesture ended: the next keyed change is a step of its own, hunt)
                self.key = None
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
        self.key = None if end else key
        self.seen()

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
    # its own bind tag, first on the widget: the widget's own <ButtonPress-1> / wheel bindings (knobs, number box
    # labels, tabs) would otherwise win over a plain <ButtonPress> and the box stayed up while they were used
    TAG = "Tooltip"

    RAW = {}  # Tk's own widgets (a dropdown's list): path -> their tooltips (the event has only the path)

    def __init__(self, widget, text, owner=None):
        self.widget, self.text, self.tip, self.job, self.used = widget, text, None, None, False
        self.owner = owner or widget  # (the box is made in its window)
        if not widget.bind_class(self.TAG, "<Enter>"):  # (once per Tk)
            for seq, name in (("<Enter>", "enter"), ("<Leave>", "leave"), ("<ButtonPress>", "use"),
                              ("<MouseWheel>", "use"), ("<KeyPress>", "use")):
                widget.bind_class(self.TAG, seq, lambda e, n=name: [
                    getattr(t, n)(e) for t in getattr(e.widget, "tooltips", None)
                    or Tooltip.RAW.get(str(e.widget), ())] and None)
        if not hasattr(widget, "tooltips"):
            widget.tooltips = []
            widget.bindtags((self.TAG,) + widget.bindtags())
        widget.tooltips.append(self)

    def enter(self, e):
        if not self.used and not e.state & self.BUTTONS:
            self.schedule()

    def leave(self, e):
        self.hide()
        if not e.state & self.BUTTONS:
            self.used = False

    def use(self, e=None):
        self.used = True
        self.hide()

    def schedule(self):
        self.hide()
        self.job = self.later(400, self.show)

    def later(self, ms, func):
        """after() on the main window, not the widget: a widget closed before it runs would leave Tk calling a
        command that is gone (an error box); func checks alive() itself."""
        return self.owner._root().after(ms, func)

    def alive(self):
        try:
            return bool(self.widget.winfo_exists() and self.owner.winfo_exists())
        except tk.TclError:
            return False

    def show(self):
        self.job = None
        if not self.text or not self.alive():
            return
        w = self.widget
        self.tip = tk.Toplevel(self.owner)
        self.tip.wm_overrideredirect(True)
        if self.owner is not w:  # (over a dropdown's open list, which stays on top)
            self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=self.text, justify="left", background=look.TIP_BG, relief="solid",
                 borderwidth=1, padx=6, pady=4, wraplength=round(lang_size("size.tooltip_width", TIP_WIDTH) * w.winfo_fpixels("1i") / 96)).pack()
        self.tip.update_idletasks()
        # kept inside the window (the panel sits at its right edge), but never past its left edge or the screen's
        # (a narrow window at the screen's left: the text's start stays readable)
        top = self.owner.winfo_toplevel()
        x, y = self.spot()
        x = min(x, top.winfo_rootx() + top.winfo_width() - self.tip.winfo_reqwidth() - 4)
        left = top.winfo_rootx()
        if not on_screen(w, left, y):
            left = w.winfo_pointerx() - 12  # (the mouse is on a screen)
        self.tip.wm_geometry(f"+{max(x, left)}+{y}")

    def spot(self):
        """Under the widget, or under the mouse for a tall one (a list as tall as the window: not at its bottom)."""
        w = self.widget
        if w.winfo_height() > TALL:
            return w.winfo_pointerx(), w.winfo_pointery() + 20
        return w.winfo_rootx() + 12, w.winfo_rooty() + w.winfo_height() + 4

    def hide(self):
        if self.job:
            self.owner._root().after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ListTip(Tooltip):
    """A list's row whose text is cut off at the list's right edge shows its whole text while the mouse rests on
    it, under that row."""

    WATCH_MS = 250  # while the mouse rests on a row: how often the row is checked for a refill / scroll under it

    def __init__(self, listbox, owner=None):
        super().__init__(listbox, "", owner)
        self.row, self.y, self.watch_job = None, 0, None
        listbox.bind("<Motion>", self.motion, add="+")
        listbox.bind("<ButtonRelease-1>", self.let_go, add="+")

    @classmethod
    def dropdown(cls, cb):
        """The same on a dropdown's open list (Tk makes that list itself: a Python handle on it here)."""
        pop = cb.tk.call("ttk::combobox::PopdownWindow", cb)
        lb = tk.Listbox.__new__(tk.Listbox)
        lb.tk, lb._w, lb.master, lb.children, lb._tclCommands = cb.tk, pop + ".f.l", cb, {}, None
        tip = cls(lb, cb)
        Tooltip.RAW[lb._w] = lb.tooltips
        cb.tk.call("bind", pop, "<Unmap>", "+" + cb.register(lambda: (setattr(tip, "row", None), tip.hide())))
        cb.bind("<Destroy>", lambda e: Tooltip.RAW.pop(lb._w, None) if e.widget is cb else None, add="+")
        return tip

    def under(self, y):
        """The row at height y, and its text when it is cut off ("" when it fits, and for a divider row)."""
        lb = self.widget
        i = lb.nearest(y)
        box = lb.bbox(i) if i >= 0 else None
        if not box or not box[1] <= y < box[1] + box[3]:
            return None, ""
        edge = lb.winfo_width() - int(lb.cget("borderwidth")) - int(lb.cget("highlightthickness"))
        text = lb.get(i) if box[0] + box[2] > edge else ""
        return i, "" if text == DIVIDER else text

    def enter(self, e):
        self.motion(e)

    def motion(self, e):
        if e.state & self.BUTTONS:
            return
        self.y = e.y
        row, text = self.under(e.y)
        if row == self.row:
            return
        self.row, self.text, self.used = row, text, False  # (another row: its tip may show again after a click)
        self.hide()
        if text:
            self.schedule()
        if row is not None and self.watch_job is None:
            self.watch_job = self.later(self.WATCH_MS, self.watch)

    def watch(self):
        """The list filled again or scrolled under a resting mouse: the tip goes and the row is forgotten, so the
        next small move shows the row's own text."""
        self.watch_job = None
        if self.row is None or not self.alive():
            return
        if self.under(self.y) != (self.row, self.text):
            self.hide()
            self.row = None
            return
        self.watch_job = self.later(self.WATCH_MS, self.watch)

    def show(self):
        if self.alive() and self.row is not None and self.under(self.y) != (self.row, self.text):
            self.job, self.row = None, None  # (changed in the wait)
            return
        super().show()

    def let_go(self, e):
        """A drag through the rows counts as using the list: the row let go on is taken as the one under the mouse,
        so its tip doesn't pop up on the next nudge."""
        self.y = e.y
        self.row, self.text = self.under(e.y)
        self.used = True
        self.hide()

    def leave(self, e):
        self.row = None
        if self.watch_job:
            self.owner._root().after_cancel(self.watch_job)
            self.watch_job = None
        super().leave(e)

    def spot(self):
        lb = self.widget
        box = lb.bbox(self.row) if self.row is not None else None
        if not box:
            return super().spot()
        return lb.winfo_rootx() + 12, lb.winfo_rooty() + box[1] + box[3] + 2


ONE = {"unit.keys": "unit.key", "unit.ticks": "unit.tick", "unit.degrees": "unit.degree", "unit.beats": "unit.beat",
       "unit.steps": "unit.step", "unit.cents": "unit.cent"}  # (unit keys -> their key for 1)


def unit_text(text, many, one=None):
    """The unit word for a number box's text: key one (default: ONE's, else many + "_one" if there is one) while it
    holds exactly 1 or -1 ("1 tick"), else many."""
    try:
        n = abs(float(calc(text)))
    except Exception:
        n = None
    if n != 1:
        return tr(many)
    one = one or ONE.get(many) or many + "_one"
    got = tr(one)
    return tr(many) if got == one else got  # (no such key: the usual word)


def unit_word(label, var, many, one=None):
    """A unit word beside a number box (label: a ttk.Label): unit_text, following the box as it's typed. Returns
    the label."""
    def put(*_):
        text = unit_text(var.get(), many, one)
        if label.winfo_exists() and label.cget("text") != text:
            label.config(text=text)
    trace = var.trace_add("write", put)
    label.bind("<Destroy>", lambda e: var.trace_remove("write", trace) if e.widget is label else None, add="+")
    put()
    return label


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
