"""The drawing tools' button in the toolbar: the drawing tool picked last (lit while it's the tool; a click picks it
again) and an arrow that opens the list of every drawing tool. A tool pinned in the list (the pin on its right)
gets its own button next to it. The tool shown and the pins are remembered in the autosave (state / restore).
The main window's toolbar has one; the Hz bass window has its own (var / owner / tips / group / fit, user 2026-10-09)."""

import base64
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from window import look
from window.help_texts import BY_ID, TOOL_TOPICS
from window.snap_picker import SIZE, _png, _segment
from window.widgets import Tooltip

FIRST = "line"  # the tool the button shows on the very first start
ROW_BG, HOVER_BG, PICKED_BG = look.TOOL_ROW, look.TOOL_HOVER, look.TOOL_PICKED
LIST_OPEN = "ToolListOpen"  # a bind tag put first on every other widget while the list is open (see open)
# pinned together (user): one Custom shape button, with Circle / Polygon opening beside it while one of them is the tool
GROUP = ("custom", "circle", "polygon")


def _pin_inside(x, y):
    """A push pin, leaning right."""
    return (_segment(x, y, 2.5, 13.5, 6.5, 9.5, 1.3) or _segment(x, y, 4.2, 7.6, 8.4, 11.8, 1.8) or
            _segment(x, y, 6.0, 10.0, 10.5, 5.5, 4.2) or _segment(x, y, 8.2, 2.2, 13.8, 7.8, 2.6))


def _arrow_inside(x, y):
    """A triangle pointing down, in the middle of the box."""
    return 5.5 <= y <= 10.5 and abs(x - 8) <= (10.5 - y) * 0.9


def _let_go_on(e):
    """The mouse was let go over the widget it was pressed on."""
    return e.widget.winfo_containing(e.x_root, e.y_root) is e.widget


def _picture(size, rgb, inside=_pin_inside):
    return tk.PhotoImage(data=base64.b64encode(_png(inside, size, rgb)), format="png")


class RestTip(Tooltip):
    """A tip beside the list that shows only while the mouse rests on a row: any move hides it until it rests again."""

    def __init__(self, widget, text, beside):
        super().__init__(widget, text)
        self.beside = beside
        widget.bind("<Motion>", lambda e: self.used or self.schedule(), add="+")

    def show(self):
        super().show()
        if self.tip:  # right of the list, so it never covers the other rows
            x = self.beside.winfo_rootx() + self.beside.winfo_width() + 4
            self.tip.wm_geometry(f"+{x}+{self.widget.winfo_rooty()}")


class ToolPicker:
    """tools: [(key, label, hot)] in list order. Packs itself into parent; var (app.tool) is the tool in use. owner =
    the window it's in (app), tips = {key: tip} (the Help topics' tips), group = tools pinned together (GROUP), fit =
    called when its width changed (app.fit_toolbar)."""

    def __init__(self, app, parent, tools, var=None, owner=None, tips=None, group=GROUP, fit=None):
        self.app, self.tools = app, tools
        self.var, self.owner, self.group = var or app.tool, owner or app, group
        self.tips = tips or {key: BY_ID[TOOL_TOPICS[key]]["tip"] for key, _, _ in tools}
        self.fit = fit or app.fit_toolbar
        self.tag = LIST_OPEN + str(id(self))  # (its own bind tag: two pickers' bindings would replace each other)
        self.label = {key: f"{label} ({hot.upper()})" for key, label, hot in tools}
        self.last, self.pins, self.popup = tools[0][0] if FIRST not in self.label else FIRST, [], None
        size = max(SIZE, round(SIZE * app.scale))
        self.pin_on, self.pin_off = _picture(size, look.rgb(look.ICON)), _picture(size, look.rgb(look.ICON_OFF))
        box = self.frame = ttk.Frame(parent)
        self.main = ttk.Radiobutton(box, variable=self.var, style="Toolbutton", takefocus=False)
        self.main.pack(side="left")
        self.main_tip = Tooltip(self.main, "")
        self.arrow_pic = _picture(size, look.rgb(look.ICON), _arrow_inside)  # (drawn, so it sits right in the middle)
        self.arrow = ttk.Button(box, image=self.arrow_pic, style="Toolbutton", command=self.open, takefocus=False)
        self.arrow.pack(side="left")
        Tooltip(self.arrow, tr("app.tools_list_tip"))
        self.pinned = None  # the pinned tools' own buttons (a frame, made again in show)
        self.group_box, self.group_lit = None, tk.BooleanVar(value=False)  # (the pinned group, see show_group)
        # a click anywhere else in Spiderweb only closes the list (the click itself does nothing else)
        self.tagged, self.held = [], False
        app.bind_class(self.tag, "<ButtonPress>", self.press)
        app.bind_class(self.tag, "<ButtonRelease>", self.release)
        for b in (1, 2, 3):
            app.bind_class(self.tag, f"<B{b}-Motion>", lambda e: "break")
        owner = self.owner
        owner.bind("<Configure>", lambda e: e.widget is owner and self.close(), add="+")  # (the window moved)
        self.show()

    # ------------------------------------------------------------ the buttons

    def show(self):
        free = [k for k, _, _ in self.tools if k not in self.pins]
        if self.last not in free and free:  # the button moves on to a tool that has no button of its own
            self.last = free[0]
        self.main.config(text=self.label[self.last], value=self.last)
        self.main_tip.text = self.tips[self.last]
        if not free:  # every tool pinned: only the arrow is left (user)
            self.main.pack_forget()
        elif not self.main.winfo_manager():
            self.main.pack(side="left", before=self.arrow)
        if self.pinned:  # (a new frame: an emptied one keeps its old width, holding Live shape off to the right)
            self.pinned.destroy()
        self.pinned = ttk.Frame(self.frame)
        if self.pins:
            self.pinned.pack(side="left")
        self.group_box = None
        for key, _, _ in self.tools:
            if key not in self.pins or key in self.group[1:]:
                continue
            if self.group and key == self.group[0]:  # (lit while any of the group is the tool: they all make
                b = ttk.Checkbutton(self.pinned, text=self.label[key], variable=self.group_lit,  # custom shapes)
                                    style="Toolbutton", command=lambda: self.var.set(self.group[0]))
            else:
                b = ttk.Radiobutton(self.pinned, text=self.label[key], value=key, variable=self.var,
                                    style="Toolbutton", takefocus=False)
            b.pack(side="left", padx=(2, 0))
            Tooltip(b, self.tips[key])
            if self.group and key == self.group[0]:  # the rest of the group opens beside it while one of them is the tool
                g = self.group_box = ttk.Frame(self.pinned)
                self.group_after = b
                ttk.Separator(g, orient="vertical").pack(side="left", fill="y", padx=(2, 1), pady=2)
                for k in self.group[1:]:
                    b = ttk.Radiobutton(g, text=self.label[k], value=k, variable=self.var, style="Toolbutton")
                    b.pack(side="left", padx=(2, 0))
                    Tooltip(b, self.tips[k])
                ttk.Separator(g, orient="vertical").pack(side="left", fill="y", padx=(3, 0), pady=2)
        self.show_group()

    def show_group(self):
        """The pinned group: Circle / Polygon beside Custom shape only while one of the three is the tool."""
        on = self.var.get() in self.group
        self.group_lit.set(on)
        g = self.group_box
        if g and on != bool(g.winfo_manager()):
            if on:
                g.pack(side="left", after=self.group_after)
            else:
                g.pack_forget()
        self.owner.after_idle(self.fit)

    def tool_changed(self):
        """A drawing tool picked any way (list, key) shows on the button, unless it has its own pinned button."""
        tool = self.var.get()
        if tool in self.label and tool not in self.pins and tool != self.last:
            self.last = tool
            self.show()
            self.app.schedule_autosave()
        else:
            self.show_group()
        if self.popup:
            self.close()

    def toggle_pin(self, key):
        keys = self.group if key in self.group else (key,)  # (the group is pinned / unpinned together)
        if key in self.pins:
            self.pins = [k for k in self.pins if k not in keys]
            if self.var.get() in keys:  # the tool in use loses its own button: the main one shows it, lit
                self.last = self.var.get()
        else:
            self.pins += [k for k in keys if k not in self.pins]
        self.show()
        self.fill_list()
        self.app.schedule_autosave()

    # ------------------------------------------------------------ the list

    def open(self):
        if self.popup:
            self.close()
            return
        p = self.popup = tk.Toplevel(self.owner)
        p.overrideredirect(True)
        self.list_box = tk.Frame(p, background=ROW_BG, relief="solid", borderwidth=1)
        self.list_box.pack()
        self.fill_list()
        b = self.main if self.main.winfo_manager() else self.arrow
        p.geometry(f"+{b.winfo_rootx()}+{b.winfo_rooty() + b.winfo_height()}")
        p.bind("<Escape>", lambda e: self.close())
        # (no grab: it would keep the window's X from closing Spiderweb while the list is open)
        p.bind("<FocusOut>", lambda e: p.after_idle(self.focus_left))
        p.focus_set()
        self.untag()
        todo = [self.app]
        while todo:
            w = todo.pop()
            if w is not p:
                w.bindtags((self.tag,) + w.bindtags())
                self.tagged.append(w)
                todo += w.winfo_children()

    def focus_left(self):
        """Another program, or Spiderweb's title bar, took the keyboard: the list closes."""
        f = self.popup and self.popup.focus_get()
        if self.popup and not (f and str(f).startswith(str(self.popup))):
            self.close()

    def fill_list(self):
        if not self.popup:
            return
        for w in self.list_box.winfo_children():
            w.destroy()
        tool = self.var.get()
        for key, _, _ in self.tools:
            bg = PICKED_BG if key == tool else ROW_BG
            name = tk.Label(self.list_box, text=self.label[key], anchor="w", background=bg, padx=10, pady=3)
            pin = tk.Label(self.list_box, image=self.pin_on if key in self.pins else self.pin_off, background=bg,
                           padx=6)
            row = len(self.list_box.grid_slaves(column=0))
            name.grid(row=row, column=0, sticky="nsew")
            pin.grid(row=row, column=1, sticky="nsew")
            for w in (name, pin):
                w.bind("<Enter>", lambda e, ws=(name, pin): [x.config(background=HOVER_BG) for x in ws], add="+")
                w.bind("<Leave>", lambda e, ws=(name, pin), bg=bg: [x.config(background=bg) for x in ws], add="+")
            # (let go somewhere else = nothing, like a menu)
            name.bind("<ButtonRelease-1>", lambda e, k=key: _let_go_on(e) and self.pick(k))
            pin.bind("<ButtonRelease-1>", lambda e, k=key: _let_go_on(e) and self.toggle_pin(k))
            RestTip(name, self.tips[key], self.list_box)
            RestTip(pin, tr("app.pin_tip"), self.list_box)

    def pick(self, key):
        self.close()
        self.var.set(key)

    def press(self, e):
        """A click outside the list closes it and goes no further (on the arrow too: its click would open it again).
        Its moves and its let go are kept away too (release)."""
        self.held = True
        self.close()
        return "break"

    def release(self, e):
        self.held = False
        self.untag()
        return "break"

    def untag(self):
        for w in self.tagged:
            try:
                w.bindtags(tuple(t for t in w.bindtags() if t != self.tag))
            except tk.TclError:  # (closed meanwhile)
                pass
        self.tagged = []

    def close(self):
        p, self.popup = self.popup, None
        if p:
            p.destroy()
        if not self.held:  # (a click that closed it keeps the tags until its let go)
            self.untag()

    # ------------------------------------------------------------ autosave

    def state(self):
        return {"draw_tool_shown": self.last, "draw_tool_pins": list(self.pins), "tool": self.app.tool.get(),
                "draw_tool": self.app.draw_tool}

    def restore(self, win):
        if isinstance(win.get("draw_tool_shown"), str) and win["draw_tool_shown"] in self.label:
            self.last = win["draw_tool_shown"]
        pins = win.get("draw_tool_pins")
        if isinstance(pins, list):
            self.pins = [k for k in dict.fromkeys(pins) if isinstance(k, str) and k in self.label]
            if any(k in self.pins for k in self.group):
                self.pins += [k for k in self.group if k not in self.pins]
        # the tool in use, and the one a double right-click goes back to (the tool's own effects: App.__init__)
        tool = win.get("tool")
        if tool in self.label or tool in ("select", "slice"):
            self.app.tool.set(tool)
        if win.get("draw_tool") in self.label:
            self.app.draw_tool = win["draw_tool"]
        elif "draw_tool_shown" in win:  # (saved before this was remembered)
            self.app.draw_tool = self.last
        self.show()
