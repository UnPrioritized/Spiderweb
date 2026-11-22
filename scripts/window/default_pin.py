"""The "use as default" pin (user 2026-11-22/23; first in Gate extend + Gate subtract, then every window that changes
shapes: PLAN.md "pin on every window"): one pin left of a window's OK. Its menu: "Save current as default" (what the
window is set to now = what it starts with from then on) / "Forget my default" (never changes the open window).
Black leaning 135 degrees = none saved, green straight down = saved. Saved in user/shape-defaults.ini
(files/shape_defaults.py), one [section] per window. Settings → User set defaults lists them; its tick "Show the use
as default pin" (app.default_pins, settings.ini) hides every pin (saved defaults are still used).

A window mixes in DefaultPin and sets
    KEY   its section in shape-defaults.ini
    ATTR  the App attribute holding the open window
and gives
    clean_saved(v)            classmethod: a saved value made valid (None = unusable)
    default_now()             what "Save current as default" saves (None = nothing to save)
    can_save_default()        the menu item usable (default: default_now() isn't None)
    describe_default(v, ppq)  classmethod: the Settings list's few words
    editor_class()            classmethod: the window as "<name>: your default" (Settings → double-click a row)
    default_title()           classmethod: its name in the Settings list (default: "<KEY>.title")
and calls self.pin_row(row) where the pin goes (packed left, before OK). Every window with a pin is listed in
WINDOWS (section -> "module:Class"), in the order Settings shows them."""

import importlib
import tkinter as tk
from tkinter import ttk

from files import shape_defaults
from files.lang import tr
from window import look
from window.widgets import Tooltip

WINDOWS = {
    "extend": "window.extend_window:ExtendWindow",
    "subtract": "window.subtract_window:SubtractWindow",
    "sustain": "window.sustain_window:SustainWindow",
    "range": "window.range_window:RangeGraph",
    "funnel_range": "window.range_window:FunnelRangeGraph",
    "tumour": "window.tumour_window:TumourWindow",
}


def window_class(sec):
    """The window class of one section (None: not a window with a pin)."""
    where = WINDOWS.get(sec)
    if where is None:
        return None
    mod, name = where.split(":")
    return getattr(importlib.import_module(mod), name)


def saved(sec):
    """The section's saved default made valid, or None."""
    cls = window_class(sec)
    v = shape_defaults.get(sec).get("settings")
    if cls is None or v is None:
        return None
    return cls.clean_saved(v)


def open_windows(app):
    """Every open window that has a pin."""
    for sec in WINDOWS:
        cls = window_class(sec)
        w = getattr(app, cls.ATTR, None) if cls else None
        if w is not None and not getattr(w, "closed", False) and w.winfo_exists():
            yield w


def defaults_changed(app):
    """A default saved / forgotten / reset: an open Settings window's User set defaults list shows it at once (user
    2026-11-23: it waited for a page change) and every open window's pin turns green / black (hunt: a Reset in
    Settings left a green pin)."""
    w = getattr(app, "settings_window", None)
    fill = getattr(w, "fill_defaults", None)
    if fill is not None and w.winfo_exists():
        fill()
    for g in open_windows(app):
        if getattr(g, "pin", None) is not None:
            g.show_pin()


def pins_shown(app):
    """The Settings tick "Show the use as default pin" changed: every open window's pin shown / hidden at once."""
    for g in open_windows(app):
        if getattr(g, "pin", None) is not None:
            g.place_pin()


def editors(app):
    """Every open "<name>: your default" window (Settings → User set defaults)."""
    for sec in WINDOWS:
        cls = window_class(sec)
        w = getattr(app, cls.ATTR + "_defaults", None) if cls else None
        if w is not None and not getattr(w, "closed", False) and w.winfo_exists():
            yield w


def default_editor(cls):
    """cls changing the user's DEFAULT only (Settings → User set defaults, double-click a row; user 2026-11-23): the
    same window on a stand-in shape no one sees; no shape, selection or undo step touched; OK saves it as the
    default, Cancel changes nothing; its on / off tick greyed (a default goes only with Reset). The window needs:
    targets() (the shapes' numbers it works on; here [0]), tgts (here the stand-in), and store() skipping the main
    window's work while editing_default. Its own extras: stand_in(), put_default(sh, v), editor_greyed()."""
    class Editor(cls):
        ATTR = cls.ATTR + "_defaults"
        editing_default = True

        def __init__(self, app, done=None):
            self.fake = cls.stand_in()
            saved = self.saved_default()
            if saved is not None:
                cls.put_default(self.fake, saved)
            self.done = done
            super().__init__(app)
            self.title(tr("defaults.edit_title", name=cls.default_title()))

        @property
        def tgts(self):
            return [self.fake]

        def targets(self):
            return [0]

        def pin_row(self, row):
            """(no pin here: OK is what saves)"""

        def retarget(self, start=False):
            super().retarget(start=True)

        def sync(self):
            """(the main window's selection doesn't matter here)"""

        def settle(self, also=()):
            """(nothing tried on shapes to keep)"""

        def show(self):
            super().show()
            for w in self.editor_greyed():
                if isinstance(w, ttk.Widget):
                    w.state(["disabled"])  # (state(): config(state=) drops a half-tick)
                else:
                    w.config(state="disabled")

        def ok(self):
            self.take_typing()
            self.save_default()
            self.close()

        def cancel(self):
            self.close()

        def close(self):
            self.closed = True
            setattr(self.app, self.ATTR, None)
            self.destroy()
    Editor.__name__ = cls.__name__ + "Default"
    return Editor


class DefaultPin:
    """The pin's part of a window (see the module's text)."""
    KEY = ATTR = None
    editing_default = False  # (True: the "your default" window, default_editor)

    @classmethod
    def stand_in(cls):
        """The shape the "your default" window works on (a whole one: some windows work out numbers from its notes)."""
        from notes.engine import clean_shape
        return clean_shape({"kind": "line", "pts": [[0, 60], [1, 61]], "vel0": 100, "vel1": 100})

    @classmethod
    def put_default(cls, sh, v):
        sh[cls.KEY] = v

    def editor_greyed(self):
        """What the "your default" window greys (its on / off tick)."""
        tick = getattr(self, "on_tick", None)
        return [tick] if tick is not None else []

    @classmethod
    def clean_saved(cls, v):
        return v

    @classmethod
    def describe_default(cls, v, ppq):
        return ""

    @classmethod
    def editor_class(cls):
        return default_editor(cls)

    @classmethod
    def default_title(cls):
        return tr(f"{cls.KEY}.title")

    def default_now(self):
        return None

    def can_save_default(self):
        """"Save current as default" usable (asked when the menu opens: no typing taken yet)."""
        return self.default_now() is not None

    def saved_default(self):
        """The default saved for this window, made valid (None: the program's own)."""
        v = shape_defaults.get(self.KEY).get("settings")
        return None if v is None else self.clean_saved(v)

    def pin_row(self, row):
        """The pin, left of OK (its own small frame, so hiding / showing it keeps its place)."""
        self.pin_box = ttk.Frame(row)
        self.pin_box.pack(side="left")
        self.pin = ttk.Label(self.pin_box, cursor="hand2")
        self.pin.bind("<ButtonRelease-1>", lambda e: self.pin_menu())
        self.pin.bind("<Destroy>", lambda e: self.drop_pin_image(), add="+")
        self.pin_tip = Tooltip(self.pin, "")
        self.show_pin()
        self.place_pin()

    def place_pin(self):
        """Shown / hidden by the Settings tick."""
        var = getattr(self.app, "default_pins", None)
        if var is None or var.get():
            self.pin.pack(side="left", padx=(0, 8))
        else:
            self.pin.pack_forget()

    def drop_pin_image(self):
        """The pin's picture freed now (a closed window's picture stayed in Tk: window_traces_freed)."""
        old, self.pin_image = getattr(self, "pin_image", None), None
        if old is not None:
            try:
                self.tk.call("image", "delete", str(old))
            except tk.TclError:
                pass

    def show_pin(self):
        from window.tool_picker import pin_picture
        got = self.saved_default() is not None
        new = pin_picture(self.app.scale, look.rgb(look.PIN_ON if got else look.PIN_OFF), 90 if got else 135)
        self.pin.config(image=new)
        self.drop_pin_image()
        self.pin_image = new
        self.pin_tip.set_text(tr("defaults.pin_saved_tip" if got else "defaults.pin_tip", name=self.default_title()))

    def pin_menu(self):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label=tr("defaults.save"), command=self.save_default,
                      state="normal" if self.can_save_default() else "disabled")
        m.add_command(label=tr("defaults.forget"), command=self.forget_default,
                      state="normal" if self.saved_default() is not None else "disabled")
        m.tk_popup(self.pin.winfo_rootx(), self.pin.winfo_rooty() + self.pin.winfo_height())

    def save_default(self):
        """What the window is set to now = what it starts with from now on."""
        mine = self.default_now()
        if mine is None:
            return
        try:
            shape_defaults.put(self.KEY, {"settings": mine})
        except OSError:
            self.app.status.config(text=tr("defaults.cant_save"))
        if getattr(self, "pin", None) is not None:
            self.show_pin()
        defaults_changed(self.app)

    def forget_default(self):
        try:
            shape_defaults.forget(self.KEY)
        except OSError:
            self.app.status.config(text=tr("defaults.cant_save"))
        if getattr(self, "pin", None) is not None:
            self.show_pin()
        defaults_changed(self.app)
