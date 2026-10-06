"""What the Claw machine, Strum and Chop windows share: a window that changes the selected shapes' notes through
pages of theirs (sh["fx"], notes/fx.py), shown live on the piano roll. A row on top: one tab per page of this tool
(numbered by its place among all the shape's pages) and "+", picked when the window opens: a new page, added as
soon as a setting changes (user, 2026-10-06). Accept keeps the change (one undo step), X / Esc puts the notes back,
Reset sets the page back to "does nothing". The main window can be used while it's open: the window follows the
selection, and before anything else changes there, the change being tried out is kept as its own undo step
(settle). Also the round Knob both use."""

import json
import math
import time
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from notes.fx import copied, is_page, pages
from window.widgets import LocalUndo, Tooltip

NEW = "new"  # the "+" tab
TAB_STYLE = "ToolTab.Toolbutton"  # (text in the middle: "+" sat at the left of its button)

ORANGE = "#f5a623"
GREEN = "#7cc21b"


class Knob(tk.Canvas):
    """A round dial from -100 to 100 (0 = straight up, all the way = straight down). Drag up / down (Shift = fine),
    the mouse wheel or the arrow keys turn it; it sticks at 0 for a moment on the way past; the right mouse button
    points it at the mouse; a middle-click puts it back to 0. changed(value, done): done = the end of one turn.
    Greyed out (on(False)), it shows its value and can't be turned, only put back to 0 by a middle-click."""

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
        self.bind("<ButtonPress-2>", lambda e: self.turn_to(0, True))  # (greyed out too)
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


class ThinBar(tk.Canvas):
    """A thin sideways scrollbar (the tab row's): drag the grey bar, or click beside it for a page. scroll = the
    scrolled widget's xview; the widget's xscrollcommand = set."""

    def __init__(self, parent, scroll, scale):
        super().__init__(parent, width=1, height=max(5, round(6 * scale)), highlightthickness=0, borderwidth=0,
                         background="#e2e2e2")
        self.scroll, self.lo, self.hi, self.drag = scroll, 0.0, 1.0, None
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.move)
        self.bind("<ButtonRelease-1>", lambda e: (setattr(self, "drag", None), self.draw()))
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        self.create_rectangle(self.lo * w, 0, self.hi * w, h, outline="",
                              fill="#7a7a7a" if self.drag else "#a6a6a6")

    def press(self, e):
        f = e.x / max(1, self.winfo_width())
        if self.lo <= f <= self.hi:
            self.drag = (e.x, self.lo)
            self.draw()
        else:
            self.scroll("scroll", -1 if f < self.lo else 1, "pages")

    def move(self, e):
        if self.drag:
            x, lo = self.drag
            self.scroll("moveto", lo + (e.x - x) / max(1, self.winfo_width()))


class ToolWindow(tk.Toplevel):
    """The shared part. Each window sets KEY (the tool: a page's "tool"; also the start of its text keys:
    KEY.window_title, KEY.nothing, KEY.shape, KEY.n_shapes, KEY.tab (a page's tab), and KEY.step = the undo step's
    name), DEFAULTS, ATTR (the app's attribute holding the open window), POS (the app's attribute remembering where
    it was), and has clean(settings) -> the page's settings (None = changes nothing), build(box) (its widgets; row 0
    is taken) and show() (the widgets show self.cfg). put(key, value, done) = a setting changed."""

    KEY, ATTR, POS, DEFAULTS = "", "", "", {}

    @classmethod
    def open(cls, app):
        if not app.note_tool_sels():  # (pictures take no note tools)
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
        self.at = None  # the page shown: {shape number: its place in the shape's fx}, None = "+" (a new page)
        self.late, self.took = None, 0.0  # (preview)
        box = ttk.Frame(self, padding=10)
        box.pack(fill="both", expand=True)
        top = ttk.Frame(box)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        head = ttk.Frame(top)
        head.pack(fill="x")
        self.what = ttk.Label(head, text="", foreground="#777")
        self.what.pack(side="left")
        self.build_tabs(top, head)
        self.build(box)
        self.targets = []
        self.undo = LocalUndo(self, self.undo_state, self.put_state)
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

    # ---- the pages

    def build_tabs(self, parent, head):
        """The tab row (its own row, the whole window wide), On on head (the shape's name's row). Each page's tab has
        a small × beside it that removes the page (user: in place of a Remove page button). The tabs scroll sideways
        (a thin scrollbar under them) once they don't fit, so the window never grows with them (user)."""
        ttk.Style().configure(TAB_STYLE, anchor="center")
        self.on = tk.BooleanVar(value=True)
        self.on_box = ttk.Checkbutton(head, text=tr("tool_window.on"), variable=self.on, command=self.on_off)
        self.on_box.pack(side="right")
        Tooltip(self.on_box, tr("tool_window.tip_on"))
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        self.strip = ttk.Frame(row)
        self.strip.pack(side="left", fill="x", expand=True)
        self.tab_view = tk.Canvas(self.strip, width=1, height=1, highlightthickness=0, borderwidth=0,
                                  background=ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
        self.tab_view.pack(fill="x")
        self.tab_bar = ThinBar(self.strip, self.tab_view.xview, self.app.scale)
        self.tab_view.config(xscrollcommand=self.tab_bar.set)
        self.tabs = ttk.Frame(self.tab_view)
        self.tab_view.create_window(0, 0, window=self.tabs, anchor="nw")
        for w in (self.tabs, self.tab_view):
            w.bind("<Configure>", lambda e: self.fit_tabs())
            self.wheel(w)
        self.tab = tk.StringVar(value=NEW)
        self.differ = ttk.Label(row, text=tr("tool_window.differ"), foreground="#777")
        self.shown_tabs = None
        self.tab_buttons = []

    def wheel(self, w):
        """The mouse wheel over the tabs scrolls them sideways."""
        w.bind("<MouseWheel>", lambda e: self.tab_view.xview_scroll(-1 if e.delta > 0 else 1, "units"))

    def fit_tabs(self):
        """The scrollbar shows only while the tabs are wider than their room."""
        v = self.tab_view
        w, h = self.tabs.winfo_reqwidth(), self.tabs.winfo_reqheight()
        if int(v.cget("height")) != h:
            v.config(height=h)
        v.config(scrollregion=(0, 0, w, h))
        if w > v.winfo_width() > 1:
            if not self.tab_bar.winfo_manager():
                self.tab_bar.pack(fill="x", pady=(2, 0))
        elif self.tab_bar.winfo_manager():
            self.tab_bar.pack_forget()
            v.xview_moveto(0)

    def see_tab(self):
        """The picked tab scrolled into view."""
        if not self.winfo_exists():
            return
        b = next((w for w in self.tab_buttons if str(w.cget("value")) == self.tab.get()), None)
        whole = self.tabs.winfo_reqwidth()
        if b is None or whole <= 0:
            return
        v = self.tab_view
        left, room = v.canvasx(0), v.winfo_width()
        row = self.tabs.winfo_children()
        end = row[row.index(b) + 1] if b is not row[-1] else b  # (its ×)
        x0, x1 = b.winfo_x(), end.winfo_x() + end.winfo_width()
        if x0 < left:
            v.xview_moveto(x0 / whole)
        elif x1 > left + room:
            v.xview_moveto((x1 - room) / whole)

    def first_fx(self):
        """The first selected shape's steps (its pages are the tabs)."""
        return (self.app.shapes[self.targets[0]].get("fx") or []) if self.targets else []

    def same(self):
        """Do the selected shapes all have the same steps? (Then their pages can be picked; else only "+".)"""
        fx = [json.dumps(self.app.shapes[i].get("fx") or []) for i in self.targets]
        return len(set(fx)) <= 1

    def order_text(self, fx):
        """Every step of a shape in order, for the tabs' tooltip."""
        parts, n = [], 0
        for st in fx:
            if is_page(st):
                n += 1
                parts.append(tr(f"{st['tool']}.tab", n=n) + (tr("tool_window.off_mark") if st.get("off") else ""))
            elif st["tool"] == "turn":
                parts.append(tr("tool_window.turn_cw" if st["deg"] > 0 else "tool_window.turn_ccw",
                                deg=f"{round(abs(st['deg']), 1):g}"))
            else:
                parts.append(tr(f"tool_window.{st['tool']}" + (f"_{st['axis']}" if st["tool"] == "flip" else "")))
        return tr("tool_window.order", steps=" → ".join(parts))

    def sync_tabs(self):
        """The tab row shows the first shape's pages of this tool (only when the selected shapes have the same)."""
        fx = self.first_fx() if self.same() else []
        tabs = []
        n = 0
        for k, st in enumerate(fx):
            if is_page(st):
                n += 1
                if st["tool"] == self.KEY:
                    tabs.append((k, tr(f"{self.KEY}.tab", n=n), st.get("off", False)))
        if self.at is None:
            self.tab.set(NEW)
        else:
            self.tab.set(str(self.at[self.targets[0]]))
        shown = (tabs, self.order_text(fx), self.same())
        if shown != self.shown_tabs:
            self.shown_tabs = shown
            for w in self.tabs.winfo_children():
                w.destroy()
            self.tab_buttons = []  # (the tabs alone, without their ×)
            for k, text, off in tabs:
                b = ttk.Radiobutton(self.tabs, text=text + (tr("tool_window.off_mark") if off else ""),
                                    variable=self.tab, value=str(k), style=TAB_STYLE,
                                    command=lambda k=k: self.pick_page(k))
                b.pack(side="left")
                b.bind("<ButtonPress-3>", lambda e, k=k: self.page_menu(e, k))
                Tooltip(b, shown[1])
                self.wheel(b)
                self.tab_buttons.append(b)
                x = ttk.Label(self.tabs, text="×", foreground="#888", cursor="hand2", padding=(2, 0, 6, 0))
                x.pack(side="left")
                x.bind("<Enter>", lambda e, x=x: x.config(foreground="#d00"))
                x.bind("<Leave>", lambda e, x=x: x.config(foreground="#888"))
                x.bind("<ButtonRelease-1>", lambda e, k=k: self.remove_tab(e, k))
                Tooltip(x, tr("tool_window.tip_remove"))
                self.wheel(x)
            b = ttk.Radiobutton(self.tabs, text="+", variable=self.tab, value=NEW, style=TAB_STYLE, width=3,
                                command=self.new_page)
            b.pack(side="left")
            self.tab_buttons.append(b)
            Tooltip(b, tr("tool_window.tip_new") + ("\n\n" + shown[1] if fx else ""))
            self.wheel(b)
            if self.same():
                self.differ.pack_forget()
            else:
                self.differ.pack(side="left", after=self.strip)
        self.after_idle(lambda: (self.fit_tabs(), self.see_tab()) if self.winfo_exists() else None)
        page = self.page_step()
        self.on_box.state(["!disabled"] if page else ["disabled"])
        if self.on.get() != (not (page or {}).get("off", False)):
            self.on.set(not (page or {}).get("off", False))

    def page_step(self, i=None):
        """The page shown, on shape i (default: the first), or None ("+")."""
        if self.at is None or not self.targets:
            return None
        i = self.targets[0] if i is None else i
        fx = self.app.shapes[i].get("fx") or []
        k = self.at.get(i)
        return fx[k] if k is not None and k < len(fx) else None

    def pick_page(self, k):
        """A tab clicked: its page's settings show and change from now on (the same page on every selected shape)."""
        self.catch_up()
        self.at = {i: k for i in self.targets}
        st = self.page_step()
        self.cfg = dict(self.DEFAULTS, **copied(st.get("cfg") or {}))
        self.show()
        self.sync_tabs()
        self.keep_page()

    def new_page(self):
        """"+": the settings start from the defaults; a page is added once one changes."""
        self.catch_up()
        self.at = None
        self.cfg = dict(self.DEFAULTS)
        self.show()
        self.sync_tabs()
        self.keep_page()

    def keep_page(self):
        """Ctrl+Z inside goes back from the page picked now (not from the one shown when the last change was made):
        picking isn't a step of its own."""
        u = self.undo
        if json.loads(u.states[u.at])["fx"] == json.loads(self.undo_state())["fx"]:
            u.states[u.at] = self.undo_state()

    def page_menu(self, e, k):
        self.pick_page(k)
        m = tk.Menu(self, tearoff=0)
        off = self.page_step().get("off", False)
        m.add_command(label=tr("tool_window.turn_on") if off else tr("tool_window.turn_off"), command=self.flip_on)
        m.add_command(label=tr("tool_window.remove"), command=self.remove_page)
        m.tk_popup(e.x_root, e.y_root)

    def flip_on(self):
        self.on.set(not self.on.get())
        self.on_off()

    def on_off(self):
        """The On tick: the page shown switched on / off (kept, doing nothing while off)."""
        if self.at is None:
            return
        self.change_steps(lambda fx, k: fx[:k] + [dict({a: b for a, b in fx[k].items() if a != "off"},
                                                       **({} if self.on.get() else {"off": True}))] + fx[k + 1:])
        self.undo.mark()

    def remove_tab(self, e, k):
        """A tab's × clicked (let go on it, like a button): that page goes."""
        if 0 <= e.x < e.widget.winfo_width() and 0 <= e.y < e.widget.winfo_height():
            self.pick_page(k)
            self.remove_page()

    def remove_page(self):
        """Remove: the page shown taken off the selected shapes; "+" is picked."""
        if self.at is None:
            return
        self.change_steps(lambda fx, k: fx[:k] + fx[k + 1:])
        self.at = None
        self.cfg = dict(self.DEFAULTS)
        self.show()
        self.sync_tabs()
        self.undo.mark()

    def change_steps(self, fn):
        """Each selected shape's steps -> fn(its steps, the page shown's place) (tidied), shown on the piano roll."""
        for i in self.targets:
            sh = self.app.shapes[i]
            fx = fn(copied(sh.get("fx") or []), self.at[i])
            self.put_fx(sh, fx)
        self.now = self.settings()
        self.app.shapes_changed(now=True)
        self.sync_tabs()

    @staticmethod
    def put_fx(sh, fx):
        """sh's steps set to fx (flips / velocities with no page before them dropped; none left: no "fx")."""
        while fx and not is_page(fx[0]):
            fx = fx[1:]
        if fx:
            sh["fx"] = fx
        else:
            sh.pop("fx", None)

    def settings(self):
        """The selected shapes' steps, by shape number."""
        return {i: self.app.shapes[i].get("fx") for i in self.targets if i < len(self.app.shapes)}

    def undo_state(self):
        """For Ctrl+Z inside: the page shown, its settings and every selected shape's steps."""
        return json.dumps({"at": self.at, "cfg": self.cfg, "fx": self.settings()}, sort_keys=True)

    def put_state(self, state):
        s = json.loads(state)
        self.at = {int(i): k for i, k in s["at"].items()} if s["at"] is not None else None
        self.cfg = s["cfg"]
        for i, fx in s["fx"].items():
            sh = self.app.shapes[int(i)]
            if fx:
                sh["fx"] = fx
            else:
                sh.pop("fx", None)
        self.now = self.settings()
        self.show()
        self.sync_tabs()
        self.app.shapes_changed(now=True)

    def retarget(self):
        """Work on the selected shapes, on a new page ("+")."""
        app = self.app
        self.targets = sorted(app.note_tool_sels())  # (not pictures)
        self.saved, self.saved_sel = json.dumps(app.shapes), app.sel_state()  # (for the undo step)
        self.before = self.now = self.settings()  # (before: put back by X / Esc; now: as this window last left them)
        self.at = None
        self.cfg = dict(self.DEFAULTS)
        n = len(self.targets)
        self.what.config(text=tr(f"{self.KEY}.nothing") if not n else tr(f"{self.KEY}.n_shapes", n=n) if n > 1
                         else tr(f"{self.KEY}.shape", shape_label=app.shape_label(app.shapes[self.targets[0]])))
        self.show()
        self.sync_tabs()
        self.undo.reset()

    def has_pages(self):
        """Do the selected shapes have pages of this tool already?"""
        return any(st["tool"] == self.KEY for i in self.targets for st in pages(self.app.shapes[i].get("fx")))

    def sync(self):
        """The main window changed the selection or the shapes."""
        if sorted(self.app.note_tool_sels()) != self.targets:
            self.settle()
        elif self.settings() == self.now:
            return
        self.retarget()

    def settle(self):
        """Something else is about to change in the main window: the change tried so far is kept (its own undo
        step), and from now on X / Esc only puts back what changes after this."""
        self.catch_up()
        if self.now != self.before and self.settings() == self.now:
            saved, sel = self.saved, self.saved_sel
            self.saved, self.saved_sel, self.before = json.dumps(self.app.shapes), self.app.sel_state(), self.now
            self.app.add_undo_step(saved, tr(f"{self.KEY}.step"), sel)

    def put(self, key, value, done=True):
        """A setting changed: show it on the piano roll."""
        self.cfg[key] = value
        self.show()
        self.preview(done)
        self.undo.mark(None if done else key)

    def preview(self, now=True):
        """The piano roll shows the settings (on "+": as a new page, added to each selected shape). While a number
        is dragged / a dial turned (not now) and that's slow (lots of notes), only the window follows the mouse: the
        notes catch up when the mouse rests."""
        if self.late:
            self.after_cancel(self.late)
            self.late = None
        if not now and self.took > 0.15:
            self.late = self.after(250, self.preview)
            return
        started = time.perf_counter()
        cl = self.clean(self.cfg)
        cl = dict(cl) if cl else None
        if self.at is None:
            if cl is None or not self.targets:  # (a new page that would change nothing isn't added)
                return
            self.at = {}
            for i in self.targets:
                sh = self.app.shapes[i]
                fx = copied(sh.get("fx") or [])
                self.at[i] = len(fx)
                sh["fx"] = fx + [{"tool": self.KEY, "cfg": cl}]
        else:
            for i in self.targets:
                sh = self.app.shapes[i]
                fx = copied(sh.get("fx") or [])
                k = self.at[i]
                fx[k] = dict(fx[k], cfg=cl)  # (None = changes nothing for now: dropped when the window closes)
                sh["fx"] = fx
        self.now = self.settings()
        self.sync_tabs()
        self.app.shapes_changed(now=True)
        self.app.update_idletasks()  # (the piano roll redrawn now, so the time counts it)
        self.took = time.perf_counter() - started

    def catch_up(self):
        """A preview left for later (preview): now."""
        if self.late:
            self.preview()

    def tidy(self):
        """Pages left doing nothing are dropped (the selected shapes' notes stay the same)."""
        for i in self.targets:
            if i < len(self.app.shapes):
                sh = self.app.shapes[i]
                fx = sh.get("fx") or []
                if any(is_page(st) and not st.get("cfg") for st in fx):
                    self.put_fx(sh, [st for st in fx if not is_page(st) or st.get("cfg")])
        self.now = self.settings()

    def reset(self):
        self.cfg = dict(self.DEFAULTS)
        self.show()
        self.preview()
        self.undo.mark()

    def accept(self):
        self.catch_up()
        self.tidy()
        self.settle()
        self.close()

    def cancel(self):
        app = self.app
        if self.late:
            self.after_cancel(self.late)
            self.late = None
        for i, fx in self.before.items():
            if fx:
                app.shapes[i]["fx"] = fx
            else:
                app.shapes[i].pop("fx", None)
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
