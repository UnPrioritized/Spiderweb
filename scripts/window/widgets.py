"""Small Tk helpers shared by the windows."""

import re
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

from files.lang import size as lang_size, tr
from files.mathexpr import calc, fmt
from window import look
from window.widgets_place import (on_screen, placed, fit_screen, _fit_screen, set_least, PLACE, remember_place,  # (moved)
                                  centre_over)
from window.widgets_wheel import (set_glide, _last, took, wheel_widget, can_change, wheel_ready, wheel_turns,  # (moved)
                                  _does_something, click_first, linux_wheel, mac_buttons, wheel_unit)
from window.widgets_tips import TIP_WIDTH, TALL, Tooltip, ListTip  # (moved)

SHIFT, CTRL = 0x1, 0x4
DRAG_PX = 4  # pixels of label dragging per step


SMALLEST_FIT = 7  # points: a squeezed dropdown's text never gets smaller than this


def no_focus(root):
    """Ticks / buttons / radio buttons under root never take the keyboard (user 2026-10-22: a clicked Tempo pane
    tick kept the focus and Space ticked it off instead of playing)."""
    for w in root.winfo_children():
        if w.winfo_class() in ("TCheckbutton", "TButton", "TRadiobutton", "Checkbutton", "Button", "Radiobutton",
                               "TMenubutton", "Menubutton"):
            w.configure(takefocus=False)
        no_focus(w)


def fit_dropdowns(root):
    """Every dropdown fits its longest choice (measured in its own font, so wide letters count right) the first
    time it shows, and again if its choices changed: it only ever GROWS, so English looks as it was. Dropdowns
    holding names (shapes, fonts, devices...) opt out with keep_width; the synth window's keep their size and
    make the text smaller instead (its knob grid stays put)."""
    root.bind_class("TCombobox", "<Map>", lambda e: fit_dropdown(e.widget), add="+")
    # (a pick from the list leaves the text highlighted, and highlighted text is drawn in the plain colour: an
    # orange warning box showed black or half black, hunt 2026-10-25; a box you can't type in never needs it)
    root.bind_class("TCombobox", "<<ComboboxSelected>>", lambda e: unhighlight(e.widget), add="+")


def unhighlight(cb):
    if isinstance(cb, ttk.Combobox) and cb.winfo_exists() and "readonly" in str(cb.cget("state")):
        cb.selection_clear()  # (after the box's own handler: it may have closed its window)


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


def open_list(cb):
    """The Tk path of a dropdown's open list (Tk makes it itself: pop.f.l on Windows / Linux; a macOS user's Tk
    kept it elsewhere, so it's looked for), or None when there's none: the extras on the list are left off."""
    todo = [cb.tk.call("ttk::combobox::PopdownWindow", cb)]
    while todo:
        w = todo.pop(0)
        if cb.tk.call("winfo", "class", w) == "Listbox":
            return w
        todo += cb.tk.splitlist(cb.tk.call("winfo", "children", w))
    return None


def letter_jump(cb):
    """Typing while the dropdown's list is open jumps to the first name starting with the letters typed (a pause of
    JUMP_MS starts a new word; the same letter again = the next name with it). Returns the jump function (tests)."""
    lb = open_list(cb)
    jump = {"text": "", "at": 0}
    if lb is None:
        return lambda char, when: None

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
    lb = open_list(cb)

    def shown():  # (the list opened, just filled: the divider greyed, the name picked before kept)
        cb.name_before, cb.list_open = cb.get(), True
        if lb is None:
            return
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


def grid_shown(w, on):
    """Show / hide a gridded widget (where it was gridded before), only when that changes: placing a widget again
    lays its window out again, which flashes while a number is stepped."""
    if on and not w.winfo_manager():
        w.grid()
    elif not on and w.winfo_manager():
        w.grid_remove()


def pack_shown(w, on, **opts):
    """Show (packed with opts) / hide a packed widget, only when that changes (grid_shown's rule: re-packing every
    sync flashed the panel)."""
    if on and not w.winfo_manager():
        w.pack(**opts)
    elif not on and w.winfo_manager():
        w.pack_forget()


def open_or_lift(app, attr, make):
    """A tool window kept in app.<attr>: open = brought up to date + to the front (False); else make() opens it
    (True: the caller's first-open steps follow)."""
    w = getattr(app, attr, None)
    if w:
        w.sync()
        w.lift()
        w.focus_set()
        return False
    make()
    return True


def wrap_to_room(label):
    """A label packed with fill="x" (expand=True when packed side by side, and packed after its neighbours, so
    they get their room first): its text wraps onto more lines when it's given less room than one line needs,
    instead of being cut at the edge (user 2026-10-19: no cut texts, also with other Windows languages' wider
    fonts). NEVER in a frame that takes its width from what's in it (the Image window's settings): each wrap
    makes it narrower, which wraps it again, on and on (froze the window)."""
    edge = []  # (the label's own edges beside its text: measured once, while it isn't wrapped yet)

    def measure():
        if not edge:
            text = str(label.cget("text"))
            font = tkfont.Font(font=label.cget("font") or "TkDefaultFont")
            edge.append(max(4, label.winfo_reqwidth() - max(font.measure(t) for t in text.split("\n"))))

    def fit(e):
        if e.width <= 1:
            return
        measure()
        room = max(1, e.width - edge[0])
        if str(label.cget("wraplength")) != str(room):
            label.configure(wraplength=room)
    label.bind("<Configure>", fit, add="+")
    label.wrap_measure = measure  # (side_panel.loosen_wraps: measured first, then it stops asking for its
    return label  #                  one-line width)


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
    one of the widgets changes size (a pinned tool, a longer text).
    items: when the widgets come and go (pinned tools), a function giving the groups to lay out now, in order, as
    (widgets, extra px): extra = room kept for the group as if wider (a group that opens beside a button: the
    toolbar doesn't jump a line when it does). A widget not packed at the start gives its pack options as
    widget.wrap_opts. drop = widgets (a separator line) left out where their group starts a line."""

    def __init__(self, frame, together=(), changed=None, items=None, drop=()):
        self.frame, self.items, self.drop, self.changed = frame, items, set(drop), changed
        slaves = frame.pack_slaves()
        self.opts = {w: {k: v for k, v in w.pack_info().items() if k != "in"} for w in slaves}
        self.groups, seen = [], set()
        for w in slaves:
            if w not in seen:
                group = next((t for t in together if w in t), (w,))
                group = [x for x in slaves if x in group]
                seen.update(group)
                self.groups.append(group)
        self.lines, self.laid, self.packed, self.sizes, self.watched = [], None, [], {}, set()
        self.watch(slaves)

    def watch(self, widgets):
        if not self.changed:
            return
        for w in widgets:
            if str(w) not in self.watched:
                self.watched.add(str(w))
                w.bind("<Configure>", self.resized, add="+")

    def resized(self, e):
        if self.sizes.get(str(e.widget)) != (e.width, e.height):
            self.sizes[str(e.widget)] = (e.width, e.height)
            self.frame.after_idle(self.changed)

    def now(self):
        """The groups to lay out: [(widgets, extra px)]."""
        if self.items:
            return [(list(g), extra) for g, extra in self.items() if g]
        return [(g, 0) for g in self.groups]

    def opts_of(self, w):
        return self.opts.get(w) or getattr(w, "wrap_opts", {"side": "left"})

    def width(self, group, extra=0):
        def pad(p):
            return sum(int(v) for v in (p if isinstance(p, tuple) else str(p).split()))
        return extra + sum(w.winfo_reqwidth() + pad(self.opts_of(w).get("padx", 0)) for w in group)

    def natural(self):
        """The width all of it takes on one line."""
        return sum(self.width(g, x) for g, x in self.now())

    def widest(self):
        """The width of its widest group (a line can't be narrower without cutting it)."""
        return max((self.width(g, x) for g, x in self.now()), default=0)

    def fit(self, room):
        """Lines filled from the first, each as full as room allows (a group wider than room gets a line of its
        own). Returns how many lines."""
        groups = self.now()
        laid, line, used = [], 0, 0
        for g, extra in groups:
            w = self.width(g, extra)
            if used and used + w > room:
                line, used = line + 1, 0
            laid.append((line, used == 0))
            used += w
        key = tuple((tuple(str(w) for w in g), at) for (g, _), at in zip(groups, laid))
        if key != self.laid:
            self.laid = key
            while len(self.lines) <= line:
                f = ttk.Frame(self.frame)
                f.lower()  # (made after the widgets: kept under them, or it would hide them)
                self.lines.append(f)
            for w in self.packed:
                if w.winfo_exists():
                    w.pack_forget()
            for i, f in enumerate(self.lines):
                if i <= line:
                    f.pack(side="top", fill="x", pady=(2 if i else 0, 0))  # (fill: side="right" ones at the end)
                else:
                    f.pack_forget()
            self.packed = []
            for (g, _), (i, first) in zip(groups, laid):
                for w in g:
                    if w in self.drop and first and i > 0 and w is g[0]:
                        continue
                    w.pack(in_=self.lines[i], **self.opts_of(w))
                    self.packed.append(w)
            self.watch(w for g, _ in groups for w in g)
        return line + 1


def box_text(e):
    """What a box holds (from its variable: while that's being set, the box itself still shows the old text).
    globalgetvar: typing in a box sets its variable from inside Tk's own key code, where a plain getvar looks for
    the name there and fails ("can't read PY_VAR17")."""
    name = str(e.cget("textvariable"))
    return str(e.tk.globalgetvar(name)) if name else e.get()


def read_number(text, lo=None, hi=None, whole=False):
    """The number typed in a box as a float (simple math works: 960*4, calc), or None when it isn't one or is
    outside lo..hi. whole: rounded to the nearest whole number (an int) BEFORE the lo..hi check. Only the reading:
    what a wrong number does (back to the last good one, a message...) stays with each box. calc never gives an
    endless number (inf / nan)."""
    try:
        v = float(calc(text))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    if whole:
        v = int(round(v))
    if lo is not None and not lo <= v or hi is not None and not v <= hi:
        return None
    return v


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


def shortened(label, text, reserve=0):
    """text as a one-line ttk label shows it: whole when it fits in the label's width (less its padding and
    reserve px), else its longest start that fits followed by "…"."""
    if not text or label.winfo_width() <= 1:
        return text
    pad = [str(p) for p in label.tk.splitlist(label.cget("padding"))]  # (left [top right bottom])
    pad = label.winfo_fpixels(pad[0]) + label.winfo_fpixels(pad[2 if len(pad) > 2 else 0]) if pad else 0
    room = label.winfo_width() - reserve - pad
    font = tkfont.Font(font=label.cget("font") or "TkDefaultFont")
    if font.measure(text) <= room:
        return text
    lo, hi = 0, len(text)  # (the longest start that fits with "…")
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= room:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


class ShortLabel(ttk.Label):
    """A one-line label that asks for only `least` letters' room, so a long text never makes its window wider:
    packed with fill="x", expand=True, it shows as much as the room it's given holds, ending in "…" with the whole
    text in a tip when cut (hunt 2026-10-19: the velocity hint beside the Formula boxes, a long shape name in the
    Hz window). config(text=) / cget("text") are the whole text."""

    def __init__(self, master, least=12, **kw):
        text = kw.pop("text", "")
        super().__init__(master, width=least, **kw)
        self._full = ""
        self.tip = Tooltip(self, "")
        self.bind("<Configure>", lambda e: self._put(self._full), add="+")
        self._put(text)

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict) and "text" in cnf:
            kw, cnf = dict(cnf, **kw), None
        if "text" in kw:
            self._put(kw.pop("text"))
            if not kw:
                return None
        return super().configure(cnf, **kw)

    config = configure

    def cget(self, key):
        return self._full if key == "text" else super().cget(key)

    def _put(self, text):
        self._full = str(text)
        shown = shortened(self, self._full)
        self.tip.set_text(self._full if shown != self._full else "")
        super().configure(text=shown)


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
        # (one piece of the usual text drawn in red: a label laid exactly over it, set_red; the text itself stays in
        # the line, so cutting short with "…" and _full work as before)
        self.red, self.red_piece = None, ""
        # (text longer than the line ends in "…", the whole of it in a tip; user 2026-10-19: no texts cut off)
        self.tip = Tooltip(self, "")
        self.bind("<Configure>", lambda e: self._put(self._full), add="+")

    def set_red(self, label, piece):
        """Draw piece (a part of the text, e.g. "(+1,447 off-range)") in red: label (a child of this line, red
        foreground, same font) is laid over it wherever it is now; "" = none."""
        self.red, self.red_piece = label, piece
        self._place_red()

    def _place_red(self):
        lab, piece = self.red, self.red_piece
        if lab is None or not lab.winfo_exists():
            return
        full, shown = self._full, str(super().cget("text"))
        at = full.find(piece) if piece else -1
        if at < 0 or not shown.startswith(full[:at + len(piece)]):  # (not there, or cut off by the "…")
            lab.place_forget()
            return
        try:
            p = self.cget("padding")
            pad = int(str(p[0] if isinstance(p, (tuple, list)) else p).split()[0])
        except (ValueError, IndexError, TypeError, tk.TclError):
            pad = 0
        f = tkfont.Font(font=self.cget("font") or "TkDefaultFont")
        if str(lab.cget("text")) != piece:
            lab.configure(text=piece)
        lab.place(x=pad + f.measure(full[:at]), rely=0.5, anchor="w")

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
        self._full = str(text)
        shown = shortened(self, self._full, self.reserve)
        self.tip.set_text(self._full if shown != self._full else "")  # (the tip: the whole text when it's cut)
        super().configure(text=shown)
        self._place_red()

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
                lo, hi = (v() if callable(v) else v for v in (self.lo, self.hi))  # (a limit may be asked now)
                if lo is not None:
                    value = max(lo, value)
                if hi is not None:
                    value = min(hi, value)
                var.set(self.text(value))
                if apply:
                    apply()
        self.app.scrub_step(gesture, run)

    def key(self, e, box):
        self.change([box], 1 if e.keysym == "Up" else -1, e.state, box[0])
        return "break"

    def wheel(self, e, box):
        if not wheel_ready(box[0]) or not can_change(box[0]):
            return None  # not clicked into / greyed: the panel scrolls
        if wheel_turns(box[0], e):  # (else a scroll going on took it)
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
        self.app._edit_key = None  # (a new drag = a new undo step, even on the box typed in / dragged just before)
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
        self.root = win._root()  # (App: its held_scrub)
        self.hold = None  # a number box label held: (its gesture, the steps before its first change)
        self.reset()
        for k in UNDO_KEYS:
            win.bind(k, lambda e: (self.press_key(-1), "break")[1])
        for k in REDO_KEYS:
            win.bind(k, lambda e: (self.press_key(1), "break")[1])

    def reset(self):
        """Start again from how it is now (nothing to undo)."""
        self.states, self.at, self.key = [self.get()], 0, None
        self.seen()

    def press_key(self, d):
        """Ctrl+Z (d -1) / Ctrl+Y (1). A number box label held: Ctrl+Z = back to the press with no step (the steps
        it made go, redo kept), Ctrl+Y nothing, like the main window's."""
        held = getattr(self.root, "held_scrub", None)
        if not held:
            return self.step(d)
        if d > 0:
            return
        h, self.hold = self.hold, None
        if h and held.drag and h[0] is held.drag["gesture"]:
            self.states, self.at, self.key = h[1], h[2], None
        held.cancel()
        if self.get() != self.states[self.at]:
            self.put(self.states[self.at])

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
        held = getattr(self.root, "held_scrub", None)
        if held and held.drag and not (self.hold and self.hold[0] is held.drag["gesture"]):
            self.hold = (held.drag["gesture"], list(self.states), self.at)  # (press_key: back to before it)
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


ONE = {"unit.keys": "unit.key", "unit.ticks": "unit.tick", "unit.degrees": "unit.degree", "unit.beats": "unit.beat",
       "unit.steps": "unit.step", "unit.cents": "unit.cent",
       "unit.seconds": "unit.second"}  # (unit keys -> their key for 1)


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
    the label. It always takes the room of its longer word, so what comes after it never moves (user 2026-10-23:
    "1 beat" / "beats" moved the Claw machine's ▾)."""
    f = tkfont.Font(root=label, font=str(label.cget("font")) or ttk.Style(label).lookup(
        label.cget("style") or label.winfo_class(), "font") or "TkDefaultFont")
    widths = {t: f.measure(t) for t in (unit_text("2", many, one), unit_text("1", many, one))}
    shown = [None]

    def put(*_):
        text = unit_text(var.get(), many, one)
        if label.winfo_exists() and shown[0] != text:
            shown[0] = text
            label.config(text=text, padding=(0, 0, max(widths.values()) - widths.get(text, 0), 0))
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
