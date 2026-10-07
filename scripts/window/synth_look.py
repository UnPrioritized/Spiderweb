"""The synth window's dark look (user 2026-10-05: dark like a synth, only that window for now; a dark mode for all of
Spiderweb is planned): its colours, a box with a header strip, the big tab buttons, and the "Synth.*" ttk styles.

Windows' own ttk parts (vista theme) can't be coloured, so the styles' boxes, dropdowns, tick boxes, buttons and
scroll bars are the plain "clam" theme's parts, which can."""

import colorsys
import sys
import tkinter as tk
from tkinter import ttk

BG = "#1d2026"  # the window
PANEL = "#2a2f37"  # a box
HEAD = "#343a43"  # a box's header strip
FIELD = "#16191e"  # number boxes, dropdowns
PIC = "#15181c"  # pictures
TEXT = "#d5dae1"
DIM = "#8a929d"
EDGE = "#434a55"
GRID = "#2a2f36"  # faint lines in pictures
MID = "#3f4650"  # ... a bit stronger
LIGHT_ON, LIGHT_OFF = "#62d36a", "#4a505a"
WARN = "#f0a040"
TAB = "#23272e"
BUTTON, BUTTON_HOT = "#3a404a", "#4a515d"
HEAD_FONT = ("Segoe UI Semibold", 9)
ENTRY = "Synth.TEntry"  # (a number box's style: hz_knobs puts it back after a wrong value)


def bright(colour):
    """A colour light enough to see on the dark look (its hue kept, a bit more vivid)."""
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(l, 0.6)
    if s > 0.1:
        s = max(s, 0.55)
    return "#%02x%02x%02x" % tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, l, s))


def mix(colour, toward, by):
    """colour mixed `by` (0..1) toward another."""
    a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * by) for x, y in zip(a, b))


def styles(w):
    """The "Synth.*" ttk styles (made once)."""
    st = ttk.Style(w)
    if "Synth.Entry.field" in st.element_names():
        return
    for el in ("Entry.field", "Combobox.field", "Combobox.downarrow", "Checkbutton.indicator", "Button.border",
               "Menubutton.border", "Menubutton.indicator", "Vertical.Scrollbar.trough", "Vertical.Scrollbar.thumb"):
        st.element_create("Synth." + el, "from", "clam", el)
    for name, bg in (("Synth", BG), ("Synth.Box", PANEL)):
        st.configure(f"{name}.TFrame", background=bg)
        st.configure(f"{name}.TLabel", background=bg, foreground=TEXT)
        st.configure(f"{name}.Dim.TLabel", background=bg, foreground=DIM)
        st.configure(f"{name}.Warn.TLabel", background=bg, foreground=WARN)
        st.layout(f"{name}.TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Synth.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nswe"})]})])
        st.configure(f"{name}.TCheckbutton", background=bg, foreground=TEXT, indicatorbackground=FIELD,
                     indicatorforeground=LIGHT_ON, upperbordercolor=EDGE, lowerbordercolor=EDGE)
        st.map(f"{name}.TCheckbutton", background=[("active", bg)], indicatorbackground=[("pressed", bg)])
    st.layout(ENTRY, [("Synth.Entry.field", {"sticky": "nswe", "border": "1", "children": [
        ("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
    st.configure(ENTRY, fieldbackground=FIELD, foreground=TEXT, bordercolor=EDGE, lightcolor=FIELD, darkcolor=FIELD,
                 insertcolor=TEXT, selectbackground="#3d5a8a", selectforeground=TEXT, padding=1)
    st.map(ENTRY, bordercolor=[("focus", "#6d8fc7")])
    st.layout("Synth.TCombobox", [("Synth.Combobox.field", {"sticky": "nswe", "children": [
        ("Synth.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Combobox.padding", {"sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]})]})])
    st.configure("Synth.TCombobox", fieldbackground=FIELD, foreground=TEXT, background=BUTTON, bordercolor=EDGE,
                 lightcolor=FIELD, darkcolor=FIELD, arrowcolor=TEXT, padding=(4, 1))
    st.map("Synth.TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
           selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)],
           background=[("active", BUTTON_HOT)])
    st.layout("Synth.TButton", [("Synth.Button.border", {"sticky": "nswe", "border": "1", "children": [
        ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])
    st.configure("Synth.TButton", background=BUTTON, foreground=TEXT, bordercolor=EDGE, lightcolor=BUTTON,
                 darkcolor=BUTTON, padding=(8, 2), anchor="center")
    st.map("Synth.TButton", background=[("disabled", TAB), ("active", BUTTON_HOT)],
           lightcolor=[("disabled", TAB), ("active", BUTTON_HOT)], darkcolor=[("disabled", TAB), ("active", BUTTON_HOT)],
           foreground=[("disabled", "#5a616b")])
    st.layout("Synth.TMenubutton", [("Synth.Menubutton.border", {"sticky": "nswe", "children": [
        ("Synth.Menubutton.indicator", {"side": "right", "sticky": ""}),
        ("Menubutton.padding", {"sticky": "we", "children": [("Menubutton.label", {"side": "left", "sticky": ""})]})]})])
    st.configure("Synth.TMenubutton", background=FIELD, foreground="#7fc4ff", bordercolor=EDGE, lightcolor=FIELD,
                 darkcolor=FIELD, arrowcolor=TEXT, padding=(8, 3), font=("Segoe UI Semibold", 10))
    st.map("Synth.TMenubutton", background=[("active", "#1f232a")])
    st.layout("Synth.Vertical.TScrollbar", [("Synth.Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Synth.Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("Synth.Vertical.TScrollbar", troughcolor=BG, background=BUTTON_HOT, bordercolor=BG,
                 lightcolor=BUTTON_HOT, darkcolor=BUTTON_HOT, gripcount=0)


def dark_list(cb):
    """A dropdown's list dark too."""
    try:
        pd = cb.tk.call("ttk::combobox::PopdownWindow", cb)
        cb.tk.call(f"{pd}.f.l", "configure", "-background", FIELD, "-foreground", TEXT, "-selectbackground",
                   "#3d5a8a", "-selectforeground", TEXT)
    except tk.TclError:
        pass


def dark_menu(m):
    """A menu (a dropdown list of choices) dark too."""
    m.config(background=FIELD, foreground=TEXT, activebackground="#3d5a8a", activeforeground=TEXT,
             disabledforeground=DIM, selectcolor=TEXT, relief="flat", bd=1)


def dark_title(win):
    """The window's title bar dark too (Windows 10 / 11 only: elsewhere left as it is)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):  # (DWMWA_USE_IMMERSIVE_DARK_MODE: 20, older Windows 10: 19)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        # (drawn again now, not only when the window is next clicked: SWP_NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED)
        user32 = ctypes.windll.user32
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x20)
        # (the window with the keyboard kept a light title bar until it lost and got the keyboard back: that
        # repaint, faked with WM_NCACTIVATE off then as it really is)
        active = user32.GetForegroundWindow() == hwnd
        user32.SendMessageW(hwnd, 0x86, not active, 0)
        user32.SendMessageW(hwnd, 0x86, active, 0)
    except (AttributeError, OSError):
        pass


class Light(tk.Canvas):
    """A small round light: lit = green (or a colour)."""

    def __init__(self, parent, s, bg, colour=LIGHT_ON):
        self.d = round(10 * s)
        super().__init__(parent, width=self.d, height=self.d, background=bg, highlightthickness=0, bd=0)
        self.colour, self.on = colour, None
        self.light(False)

    def light(self, on):
        if on == self.on:
            return
        self.on = on
        self.delete("all")
        m = 1
        self.create_oval(m, m, self.d - m, self.d - m, fill=self.colour if on else LIGHT_OFF, outline="")


class Box(tk.Frame):
    """A box of knobs: a header strip (a light, the name in capitals in the box's colour) over its body
    (Box.body, a ttk frame to grid the cells in)."""

    def __init__(self, parent, s, title, colour, padding=(10, 6, 10, 8)):
        super().__init__(parent, background=PANEL, highlightthickness=1, highlightbackground=EDGE, bd=0)
        self.head = tk.Frame(self, background=HEAD)
        self.head.pack(fill="x")
        self.lamp = Light(self.head, s, HEAD)
        self.lamp.pack(side="left", padx=(round(8 * s), round(6 * s)), pady=round(5 * s))
        self.title = tk.Label(self.head, text=title.upper(), background=HEAD, foreground=colour, font=HEAD_FONT)
        self.title.pack(side="left")
        self.body = ttk.Frame(self, style="Synth.Box.TFrame", padding=padding)
        self.body.pack(fill="both", expand=True)


class BigTab(tk.Frame):
    """One of the big tab buttons at the top (a light over its name; picked = lit, the box's colour)."""

    def __init__(self, parent, s, text, pick):
        super().__init__(parent, background=TAB, highlightthickness=1, highlightbackground=EDGE, cursor="hand2")
        self.inner = tk.Frame(self, background=TAB)  # (the light and name kept together in the middle of the tab)
        self.inner.pack(expand=True)
        self.lamp = Light(self.inner, s, TAB)  # (more room over the light: the name's letters leave room under them)
        self.lamp.pack(pady=(round(8 * s), round(2 * s)))
        self.label = tk.Label(self.inner, text=text.upper(), background=TAB, foreground=DIM,
                              font=("Segoe UI Semibold", 10), padx=round(16 * s), pady=0)
        self.label.pack(pady=(0, round(3 * s)))
        for w in (self, self.inner, self.lamp, self.label):
            w.bind("<ButtonPress-1>", lambda e: pick())

    def picked(self, on):
        bg = PANEL if on else TAB
        for w in (self, self.inner, self.lamp, self.label):
            w.config(background=bg)
        self.label.config(foreground=TEXT if on else DIM)
        self.lamp.light(on)
