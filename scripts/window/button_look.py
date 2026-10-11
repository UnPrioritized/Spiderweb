"""Settings → Look's Button style (user, 2026-10-11): the buttons and boxes as the system draws them (the light
look's usual; the system's can't be dark, so the dark look shows Plain for it), Plain (Tk's "clam": the dark look's
usual), Raised, Outlined or Flat. Put on at start (look.apply) like the Size; the Settings preview shows a pick at
once with its own styles ("Preview.T..."), the real widgets wait for the restart.

Raised / Outlined / Flat are drawn with small pictures (image elements: their edges are ours to colour). Each style's
pieces are made once per window under their own names ("SwRaised1.Button.border"...), so the app and the preview
can show different ones side by side, whichever theme is running."""

from tkinter import PhotoImage, TclError, ttk

from files import settings

STYLES = ("system", "plain", "raised", "outlined", "flat")
KINDS = ("TButton", "TMenubutton", "Toolbutton", "TEntry", "TCombobox", "TSpinbox")
FRAMES = {"TButton": "Button.border", "TMenubutton": "Menubutton.border", "Toolbutton": "Toolbutton.border",
          "TEntry": "Entry.field", "TCombobox": "Combobox.field", "TSpinbox": "Spinbox.field"}
CLAM_PIECES = tuple(FRAMES.values()) + ("Menubutton.indicator", "Combobox.downarrow", "Spinbox.uparrow",
                                        "Spinbox.downarrow", "Checkbutton.indicator")
PREVIEW_KINDS = KINDS + ("TCheckbutton",)  # (the preview's tick box follows the style too: the app's go clam with it)
EDGES = (1, 2, 3, 4)  # how thick the edges are, in pixels (System's own can't change: Tk draws none of it)
RUNNING, RUNNING_EDGE = "system", 1  # the style showing (start)
SYSTEM_THEME, SYSTEM_LAYOUTS, CLAM_LAYOUTS = "", {}, {}
PICTURES = []  # (kept: Tk forgets a picture nothing in Python holds)


def read_style():
    style = str(settings.get("look", "button") or "").strip().lower()  # (edited by hand: any capitals)
    return style if style in STYLES else "system"


def read_edge():
    from window.look import whole_number
    edge = whole_number(settings.get("look", "edge") or 1)
    return edge if edge in EDGES else 1


def shown_style(style):
    """The style that really shows for a pick (the system's own in the dark look = Plain)."""
    from window import look
    return "plain" if style == "system" and look.DARK else style


def start(st):
    """Before look.apply changes the theme: the system's own theme and layouts, clam's (for the preview)."""
    global RUNNING, RUNNING_EDGE, SYSTEM_THEME
    RUNNING, RUNNING_EDGE = shown_style(read_style()), read_edge()
    SYSTEM_THEME = st.theme_use()
    for kind in PREVIEW_KINDS:
        SYSTEM_LAYOUTS[kind] = st.layout(kind)
        spec = st.tk.call("ttk::style", "theme", "settings", "clam", "ttk::style layout " + kind)
        CLAM_LAYOUTS[kind] = ttk._list_from_layouttuple(st.tk, st.tk.splitlist(spec))


def dress(st, target, style, p):
    """The colours of the buttons and boxes in the picked style (target "" = the app's own, "Preview." = the
    Settings preview's). Plain = the dark look's as it always was."""
    if target:  # (the preview may sit on the system's theme: everything clam's pieces read)
        for kind in PREVIEW_KINDS:
            st.configure(target + kind, foreground=p["text"], background=p["window"], bordercolor=p["edge"],
                         fieldbackground=p["field"], insertcolor=p["text"], arrowcolor=p["text"],
                         focuscolor=p["edge"], selectbackground=p["pick"], selectforeground="#ffffff")
    for name in ("TButton", "TMenubutton"):
        st.configure(target + name, background=p["button"], lightcolor=p["button"], darkcolor=p["button"],
                     padding=("3.75p", "1.5p"))  # (a little shorter than clam's 3.75p all round, user)
        st.map(target + name, background=[("disabled", p["window"]), ("pressed", p["down"]), ("active", p["hot"])],
               lightcolor=[("pressed", p["down"]), ("active", p["hot"])],
               darkcolor=[("pressed", p["down"]), ("active", p["hot"])])
    st.configure(target + "Toolbutton", background=p["window"], lightcolor=p["window"], darkcolor=p["window"],
                 bordercolor=p["window"])  # (the picked tool = a normal button's grey, user: clam's own was too bright)
    tool = [("disabled", p["window"]), ("pressed", p["down"]), ("active", p["hot"]), ("selected", p["button"])]
    st.map(target + "Toolbutton", background=tool, lightcolor=tool[1:], darkcolor=tool[1:],  # (like Undo: lit, darker held)
           bordercolor=[("pressed", p["edge"]), ("active", p["edge"]), ("selected", p["edge"])])
    for name in ("TEntry", "TSpinbox", "TCombobox"):
        st.configure(target + name, lightcolor=p["field"], darkcolor=p["field"])
        st.map(target + name, fieldbackground=[("disabled", p["window"]), ("readonly", p["field"])],
               bordercolor=[("focus", p["focus"])])
    # (the preview's dropdown, picked while it had the keyboard: its text showed white on white on the system's
    # theme, user. No highlight there at all)
    st.map(target + "TCombobox", background=[("active", p["hot"])],
           selectbackground=[("readonly", p["field"]) if target else ("readonly", "!focus", p["field"])],
           selectforeground=[("readonly", p["text"]) if target else ("readonly", "!focus", p["text"])])
    # the dropdown's / number box's arrow boxes (inside the box's own edge in the drawn styles)
    for name in ("TCombobox", "TSpinbox"):
        if style == "raised":
            st.configure(target + name, background=p["button"], lightcolor=p["hi"], darkcolor=p["lo"],
                         bordercolor=p["lo2"])
            st.map(target + name, bordercolor=[("focus", p["lo2"])])
        elif style in ("outlined", "flat"):
            st.configure(target + name, background=p["field"], bordercolor=p["field"])
            st.map(target + name, bordercolor=[("focus", p["field"])])
        else:
            st.configure(target + name, background=p["button"])
    st.map(target + "TSpinbox", background=[("active", p["hot"])])
    if target:  # (the preview's tick box: clam's, as the app's are with any style but the system's own)
        st.configure(target + "TCheckbutton", foreground=p["text"], background=p["window"],
                     indicatorbackground=p["field"], indicatorforeground=p["text"], upperbordercolor=p["edge"],
                     lowerbordercolor=p["edge"])
        st.map(target + "TCheckbutton", background=[("active", p["tick_hot"])],
               indicatorbackground=[("pressed", p["button"])])


def ring_picture(st, fill, rings):
    """A small picture: rings from the outside in, each (top, left, bottom, right) colour, round the fill (Tk
    stretches the middle)."""
    n = len(rings)
    size = 2 * n + 4
    pic = PhotoImage(master=st.master, width=size, height=size)
    pic.put(fill, to=(0, 0, size, size))
    for i, (top, left, bottom, right) in enumerate(rings):
        far = size - i
        pic.put(top, to=(i, i, far, i + 1))
        pic.put(left, to=(i, i, i + 1, far))
        pic.put(bottom, to=(i, far - 1, far, far))
        pic.put(right, to=(far - 1, i, far, far))
    PICTURES.append(pic)
    return pic


def button_rings(style, p, t, how):
    """A button's edge (how: up, hot, down, off) as rings, t pixels thick."""
    if style == "flat":
        return []
    if style == "raised":
        hi, lo, lo2 = p["hi"], p["lo"], p["lo2"]
        if how == "down":  # (pressed in: the light and shade swap)
            return [(lo2, lo2, hi, hi)] + [(lo, lo, hi, hi)] * (t - 1)
        return [(hi, hi, lo2, lo2)] + [(hi, hi, lo, lo)] * (t - 1)
    if style == "outlined":
        return [(p["pick"] if how == "hot" else p["edge"] if how == "off" else p["outline"],) * 4] * t
    return [(p["edge"],) * 4] * t


def field_rings(style, p, t, focus):
    """A text box's edge as rings, t pixels thick (focus: the box being typed in)."""
    edge = p["focus"] if focus else p["edge"]
    if style == "flat":  # (a line under it only)
        return [(p["field"], p["field"], p["pick"] if focus else p["outline"], p["field"])] * t
    if style == "raised":  # (sunk in)
        hi, lo, lo2 = p["hi"], p["lo"], p["lo2"]
        rings = [(lo2, lo2, hi, hi)] + [(lo, lo, hi, hi)] * (t - 1)
        return [(edge,) * 4] + rings[1:] if focus else rings
    if style == "outlined":
        return [(p["pick"] if focus else p["outline"],) * 4] * t
    return [(edge,) * 4] * t


def pieces(st, style, p, t=1):
    """The style's pieces (made once per window), named "Sw<Style><t>.<clam's name>"; returns the name start."""
    start_name = f"Sw{style.title()}{t}."
    if start_name + "Button.border" in st.element_names():
        return start_name
    if style == "system":
        names = set()

        def walk(layout):
            for name, opts in layout:
                names.add(name)
                walk(opts.get("children", []))
        for layout in SYSTEM_LAYOUTS.values():
            walk(layout)
        names.add("Button.border")  # (the mark that this set is made)
        for name in names:
            try:
                st.element_create(start_name + name, "from", SYSTEM_THEME, name)
            except TclError:
                pass
        return start_name
    drawn = style != "plain" or t != 1
    for name in CLAM_PIECES:
        if not drawn or name not in FRAMES.values():
            st.element_create(start_name + name, "from", "clam", name)
    if drawn:
        pad = max(t, 2)
        for kind, name in FRAMES.items():
            if kind in ("TEntry", "TCombobox", "TSpinbox"):
                up = field_rings(style, p, t, False)
                states = [("disabled", ring_picture(st, p["window"], up)),
                          ("focus", ring_picture(st, p["field"], field_rings(style, p, t, True)))]
                plain = ring_picture(st, p["field"], up)
            else:
                tool = kind == "Toolbutton"
                up = button_rings(style, p, t, "up")
                states = [("disabled", ring_picture(st, p["window"], [(p["window"],) * 4] * len(up) if tool
                                                    else button_rings(style, p, t, "off"))),
                          ("pressed", ring_picture(st, p["down"], button_rings(style, p, t, "down"))),
                          ("active", ring_picture(st, p["hot"], button_rings(style, p, t, "hot")))]
                face = p["window"] if style == "outlined" else p["button"]
                if tool:  # (a tool button: no edge until picked or under the mouse)
                    picked = (ring_picture(st, p["down"], button_rings(style, p, t, "down")) if style == "raised"
                              else ring_picture(st, face, up))
                    states.append(("selected", picked))
                    plain = ring_picture(st, p["window"], [(p["window"],) * 4] * len(up))
                else:
                    plain = ring_picture(st, face, up)
            st.element_create(start_name + name, "image", plain, *states, border=len(up), padding=pad,
                              sticky="nswe")
    return start_name


def renamed(layout, start_name, names, drawn=False):
    """A layout with the pieces in names under their own names (drawn: without clam's "border" option, which
    hid a picture piece's insides)."""
    out = []
    for name, opts in layout:
        opts = {k: v for k, v in opts.items() if not (drawn and k == "border")}
        if "children" in opts:
            opts["children"] = renamed(opts["children"], start_name, names, drawn)
        out.append((start_name + name if name in names else name, opts))
    return out


def put_on(root, st, target, style, p, kinds=KINDS, t=1):
    """The style's pieces on the buttons and boxes (target "" = the app's own, "Preview." = the preview's)."""
    start_name = pieces(st, style, p, t)
    for kind in kinds:
        if style == "system":
            made = {n[len(start_name):] for n in st.element_names() if n.startswith(start_name)}
            layout = renamed(SYSTEM_LAYOUTS[kind], start_name, made)
        else:
            layout = CLAM_LAYOUTS[kind]
            drawn = style != "plain" or t != 1
            if kind == "TCombobox" and drawn:  # (the arrow inside the box's edge)
                layout = [("Combobox.field", {"sticky": "nswe", "children": [
                    ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                    ("Combobox.padding", {"sticky": "nswe", "children": [
                        ("Combobox.textarea", {"sticky": "nswe"})]})]})]
            layout = renamed(layout, start_name, CLAM_PIECES, drawn)
        st.layout(target + kind, layout)
