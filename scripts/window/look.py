"""The program's colours and fonts, all in one place (user, 2026-10-07: for dark mode and the planned Look settings
window). Each colour is pick(light, dark): the light one is the look as it always was; dark None = not picked yet
(the light one is used). DARK is fixed as this file is first read, before any window is made: the look picked in
Help → About is saved in look.json and put on at the next start (user).

Not here: the note colours (roll_shared.SLOT_COLORS: the same in both looks), colours a user picks, and the synth
window, which has its own dark look (synth_look.py, user: keep it as it is)."""

import json
import os

from files.about import HERE
from files.safefile import write_text
from files.system import dark_system

LOOKS = ("light", "dark", "windows")  # (saved as these; windows = follow the system's setting)
LOOK_FILE = os.path.join(HERE, "look.json")


def read_look():
    """The look picked in Help → About ("light" when none was, or the file won't read)."""
    try:
        with open(LOOK_FILE, encoding="utf-8") as f:
            how = json.load(f).get("look")
    except (OSError, ValueError, AttributeError):
        how = None
    return how if how in LOOKS else "light"


def save_look(how):
    write_text(LOOK_FILE, json.dumps({"look": how}) + "\n")


def is_dark(how):
    return how == "dark" or (how == "windows" and dark_system())


# (SPIDERWEB_LOOK in the environment wins: the saved tests run in the light look whatever the user picked)
DARK = is_dark(os.environ.get("SPIDERWEB_LOOK") or read_look())


def pick(light, dark=None):
    """The colour for the look in use."""
    return dark if DARK and dark else light


# --- Fonts (sizes stay where they're used; the families are set here)
SANS = "Segoe UI"
MONO = "Consolas"
TK = "TkDefaultFont"  # Tk's own font for widgets (a few texts drawn on the piano roll match it)


def font(size, *style, family=None):
    """A font for a widget or a canvas text: font(9), font(8, "bold")."""
    return (family or SANS, size, *style)


def mono(size, *style):
    """A font where every letter is as wide (formulas, text to copy)."""
    return (MONO, size, *style)


# --- Texts on the window
HINT = pick("#777", "#a0a0a4")  # grey hints and units beside boxes
INFO = pick("#555", "#b4b4b8")  # status / info lines
SOFT_TEXT = pick("#666", "#a8a8ac")  # longer help texts, legends
FAINT_TEXT = pick("#999", "#808084")  # barely there (a version number, things not used)
FAINTER_TEXT = pick("#aaa", "#6c6c70")
DARK_TEXT = pick("#222", "#e2e2e4")  # values in a list of numbers
LABEL = pick("#333", "#e2e2e4")  # small texts drawn on canvases (numbers on a graph's edge, bar numbers)
ERROR = pick("#d00000", "#ff6b6b")  # a wrong value, a warning that blocks something
ERROR_DARK = pick("#c00000", "#ff6b6b")  # (the pattern window's)
ERROR_BG = pick("#f6c0c0", "#6a3434")  # a box holding something that can't be used
BAD_MARK_TEXT, BAD_MARK = pick("white"), pick("#d33", "#c43c3c")  # a wrong letter marked in a text box
GOOD = pick("#1d6b1d", "#6fd16f")  # done / fine
WARN = pick("#c06000", "#ffa040")  # orange: something to look at (a gap closed, a limit reached)
WARN_DARK = pick("#9a4b00", "#e8913a")
FILE_OK, FILE_WARN = pick("#2a7", "#4fd18f"), pick("#c60", "#ffa040")  # a picture file found / missing or changed
LINK = pick("#1a5fb4", "#7ab4ff")
VALUE = pick("#0a50e0", "#7ab4ff")  # the value under the mouse on a graph, a setting a graph drives
TIP_BG = pick("#ffffe8", "#505054")  # tooltips
SASH = pick("#c8c8c8", "#2a2a2c")  # the bar between the side panel and the piano roll
FIELD_SOFT = pick("#fafafa", "#333336")  # text boxes only read from
FIELD = pick("white", "#2f2f31")  # text boxes typed in
WHITE_BOX = pick("#ffffff", "#2f2f31")  # a plain white box (the font preview)
CLOSE, CLOSE_HOT = pick("#888", "#9a9a9e"), pick("#d00", "#ff6b6b")  # the × closing a tab
GAP_PICKED = pick("#ffd9b0")  # a picked list row in the warning colour
FUTURE, FUTURE_PICKED = pick("#a0a0a0", "#77777b"), pick("#ffffff")  # History: steps undone
MISSING_SHAPE = pick("#808080", "#8a8a8e")  # a placed shape's name the library no longer has
LIST_AWAY = pick("#d9d9d9", "#55555a")  # a picked list row while the keyboard is elsewhere
LIST_AWAY_TEXT = pick("black", "#e2e2e4")
SEARCH_HIT = pick("#fff08a", "#6b6420")  # Help: the words searched for
OUTLINE_SOFT = pick("#555", "#8a8a8e")  # a thin edge round a small colour box

# --- Piano roll
ROLL_BG = pick("#ffffff", "#3c3c3f")  # white-key rows (quiet notes fade toward it too)
ROW_BLACK = pick("#e7eefa", "#353538")  # black-key rows
ROW_LINE = pick("#dfe6f2", "#353538")  # between two white-key rows
OCTAVE_LINE = pick("#606060", "#7a7a7c")  # under each C
OUTSIDE_KEYS = pick("#ececec", "#2f2f31")  # past the lowest / highest key
GRID_SNAP, GRID_BEAT, GRID_BAR = pick("#eef1f6", "#464649"), pick("#bcc4d2", "#57575a"), pick("#3a3a3a", "#8a8a8c")
KEY_WHITE = pick("#ffffff", "#b5b5b6")
KEY_OUTSIDE = pick("#e2e2e2", "#9a9a9b")  # keys outside a real 88-key piano (faintly greyed)
KEY_BLACK, KEY_BLACK_OUTSIDE = pick("#222222", "#212123"), pick("#6a6a6a", "#4a4a4c")
KEY_EDGE_C, KEY_EDGE_F = pick("#606060", "#5a5a5c"), pick("#b0b0b0", "#8a8a8c")  # under C, under F
KEY_TEXT = pick("#333", "#363637")
ROLL_EDGE = pick("#808080", "#656567")  # beside the keys, under the ruler
RULER_BG, RULER_TICK, RULER_TEXT = pick("#f0f0f0", "#434346"), pick("#555", "#a0a0a4"), pick("#333", "#e2e2e4")
PLAY_LINE = pick("#0a50e0", "#5b9bff")
HANDLE = pick("#0050d0", "#5b9bff")  # a shape's points and handles
HANDLE_FILL = pick("#ffffff")  # inside them, and the white edge round handle lines
HANDLE_FIXED = pick("#c00000", "#ff4d4d")  # a corner that isn't free, the points of a shape being drawn
STROKE_POINT = pick("#7a1fe0", "#b37aff")  # the points of a custom shape's strokes (purple, like a picked stroke)
PART = pick("#7a1fe0", "#b37aff")  # a highlighted funnel line / the curve clicked
TWIN = pick("#00a39a", "#2fd6cb")  # the curves linked to it
LIVE_LINE = pick("#7a1fe0", "#b37aff")  # a live shape's stroke being drawn
RING = pick("#b40000", "#ff6060")  # round the selected shapes' notes (user: #e00000 looked bright, almost pink)
PREVIEW_LINE, PREVIEW_HALO = pick("#ff1f1f"), pick("#ffa8a8", "#7a3030")  # the outline gate's preview line
SHAPE_LINE = pick("#c0392b", "#e0574a")  # a shape's line (shown lines), an arrow for a shape above the top key
SHAPE_LINE_PICKED = pick("#ff1f1f", "#ff3b3b")  # ... selected
ABOVE_EDGE = pick("#ffffff")  # round that arrow
ORIGIN, ORIGIN_PICKED = pick("#efc0c0", "#7a5656"), pick("#e89a9a", "#a86a6a")  # a line as drawn under its tumours
DRAFT_LINE = pick("#0a8f0a", "#3fcf3f")  # the shape being drawn
CUT, CUT_UNDER = pick("#d00000", "#ff5050"), pick("#ffffff")  # Slice line, cut marks, scissors (+ the white under a mark)
FAINT_CUT = pick("#f0c8c8", "#8a5a5a")  # the really faint line from a piece's cut end to the other piece's (user)
LABEL_BG, LABEL_EDGE = pick("#fffbe6"), pick("#c9b26b")  # a placed picture's name above it (dark text: stays light)
SELECT_BOX = pick("#000000", "#e8e8ea")  # the Select box's line
TEXT_BOX = pick("#3a7bd5", "#5b9bff")  # the Text tool's box and selected letters
TEXT_CARET = pick("#000000", "#ffffff")
TROUGH, THUMB = pick("#e4e4e4", "#39393b"), pick("#b4b4b4", "#6c6c6f")  # scrollbars
THUMB_HOT, GRIP = pick("#9a9a9a", "#808083"), pick("#707070", "#a0a0a4")

# --- Graphs (velocity pane, tumour graph, Range…, pattern window)
CHART_BG = pick("#ffffff", "#3c3c3f")
CHART_BORDER = pick("#a0a0a0", "#5c5c5f")  # round the graph's box
CHART_FRAME = pick("#808080", "#808083")  # round the drawing area
CHART_GRID, CHART_GRID_STRONG = pick("#d3dff0", "#4f4f52"), pick("#9fb2cf", "#6a6a6d")  # (strong: 0 and 100 %)
CHART_BAND = pick("#e4e4e4", "#4a4a4d")  # a greyed stretch / faint lines
CHART_LINE = pick("#d00000", "#ff5050")  # the line you draw
CHART_LINE_FAINT = pick("#f0b0b0", "#7a4040")
CHART_OFF = pick("#b0b0b0", "#707074")  # the line while it's switched off
CHART_POINT = pick("#ffffff")  # inside the line's points
RANGE_BAR, RANGE_BAR_HOT = pick("#4a90e2"), pick("#1f5fb0", "#7ab4ff")  # Range…: notes per gate, the one pointed at
RANGE_NONE = pick("#e0503c")  # gates with none
PAT_BORDER = pick("#c0c0c0", "#5c5c5f")  # Pattern window: round the drawing
PAT_ORIGIN, PAT_AB = pick("#e0a0a0", "#8a5a5a"), pick("#a05050", "#d08080")  # the curve it goes along, its ends' letters
PAT_NEIGHBOUR = pick("#d8d8e8", "#55556a")  # the loops before and after, faint
PAT_FORMULA = pick("#d0d0d0", "#5a5a5d")  # the formula it came from, under it
PAT_LINE, PAT_ARM = pick("#d01010", "#ff5050"), pick("#6080c0", "#7a9ae0")  # the loop, its handle lines
PAT_GRID, PAT_GRID_STRONG = pick("#eeeeee", "#444447"), pick("#d4d4d4", "#545457")
PAT_GRID_TEXT = pick("#aaaaaa", "#808084")

# --- Drawer
DRAWER_BG = pick("#f4f4f4", "#333336")  # around the board
BOARD = pick("#ffffff", "#3c3c3f")
AREA_NORMAL, AREA_EMPTY = pick("#d4d4d4", "#5a5a5e"), pick("#fbe4e4", "#5a3c3c")  # area colours: plain / left empty
AREA_HOVER = pick("#e8eef8", "#4a5060")  # the area under the mouse (when it has no colour)
BOARD_MIDDLE = pick("#7f8fb0")
BOARD_GRID_MAJOR, BOARD_GRID = pick("#9aa4b4", "#66707e"), pick("#dde3ec", "#48484c")
BOARD_EDGE = pick("#606060", "#8a8a8c")
SWATCH_EDGE, SWATCH_EDGE_ON = pick("#909090", "#8a8a8e"), pick("#000000", "#ffffff")  # area colour boxes (picked)
STROKE_PICKED, POINT_PICKED = pick("#ff8c1a"), pick("#c05a00", "#ff8c1a")
OPEN_END, OPEN_END_EDGE = pick("#ff2020"), pick("#800000")  # red dots at a stroke's open ends
ERASE_BOX = pick("#d02020", "#ff5050")
STICK = pick("#d000d0", "#ff4dff")  # the mark where a point sticks
STICK_LINE = pick("#c070e0", "#d08cff")  # the parts ending at that point (lighter, so the mark stands out on them)
STICK_GUIDE = pick("#dcb4ee", "#7a5a90")  # the drawer's Circle guide: the faint dotted line to the point
MIRROR_SIDE = pick("#f0f0f2", "#36363a")  # Mirror: the side that gets the mirrored copy (faint grey)
MIRROR_LINE = pick("#0097a7", "#26c6da")  # Mirror: the line it's mirrored across

# --- Small windows
TOOL_ROW, TOOL_HOVER = pick("#ffffff", "#2f2f31"), pick("#e5f3ff", "#3d4a5c")  # the drawing tools list
TOOL_PICKED = pick("#cce4f7", "#2f5a8a")
KNOB_ORANGE, KNOB_GREEN = pick("#f5a623"), pick("#7cc21b")  # Claw / Strum / Chop knobs
KNOB_RING, KNOB_RING_FOCUS, KNOB_RING_OFF = pick("#bbb", "#6c6c70"), pick("#888", "#a0a0a4"), pick("#ccc", "#4f4f52")
KNOB_BODY, KNOB_BODY_OFF, KNOB_POINTER = pick("#555", "#252527"), pick("#aaa", "#4a4a4d"), pick("white", "#e2e2e4")
TAB_TROUGH, TAB_THUMB = pick("#e2e2e2", "#333336"), pick("#a6a6a6", "#6c6c6f")  # the tab row's scrollbar
TAB_THUMB_HELD = pick("#7a7a7a", "#8a8a8e")
CHOP_BORDER, CHOP_GRID = pick("#bbb", "#5c5c5f"), pick("#e8e8e8", "#48484b")
CHOP_GRID_STRONG, CHOP_MIDDLE = pick("#bbb", "#6c6c70"), pick("#ddd", "#555558")
CHOP_MARK = pick("#b06d00", "#ffa040")  # where a note is cut
FMT_TAG, FMT_PLAIN = pick("#1060c0", "#7ab4ff"), pick("#888", "#9a9a9e")  # Format window: a code, plain text
FMT_PLAIN_BG = pick("#eee", "#45454a")
FMT_SPACE = pick("#cfe2ff", "#2f4a6a")  # spaces at the ends of a line
FMT_RETURN = pick("#8aa8d0", "#6a88b0")  # the ↵ at a line's end
FMT_BAD = pick("#c33", "#ff6b6b")
PASTE_USE, PASTE_OTHER = pick("#bfe0ff", "#2f4a6a"), pick("#e6e6e6", "#45454a")  # Paste from…: lines used / not
PASTE_EMPTY = pick("#f0f0f0", "#3a3a3c")
IMG_PREVIEW_BG, IMG_BORDER = pick("black"), pick("#999", "#5c5c5f")  # Image window: behind the picture
IMG_HANDLE = pick("#dfe8f5", "#3d4a5c")  # the box to drag onto the piano roll
IMG_MARK, IMG_MARK_EDGE = pick("white"), pick("black")  # the corner mark on a picked colour
IMG_KEYS, IMG_KEY_BLACK = pick("#e8e8e8", "#b5b5b6"), pick("#222", "#212123")  # the small keyboards round the preview
IMG_DRAG = pick("#e02020", "#ff5050")  # the picture's box while dragged

# --- Hz bass window
HZ_BG = pick("white", "#3c3c3f")
HZ_ROW_BLACK = pick("#eef1f8", "#353538")
HZ_OCTAVE_LINE, HZ_ROW_LINE = pick("#c9c9c9", "#5a5a5d"), pick("#ececec", "#424245")
HZ_GRID_BAR, HZ_GRID_BEAT, HZ_GRID = pick("#707070", "#8a8a8c"), pick("#bdbdbd", "#57575a"), pick("#ececec", "#464649")
HZ_SHADE, HZ_SHADE_EDGE = pick("#d8d8d8", "#2a2a2c"), pick("#909090", "#707074")  # past the shape's end
HZ_KEYS, HZ_KEY_BLACK = pick("#fafafa", "#b5b5b6"), pick("#303030", "#212123")
HZ_KEY_LINE, HZ_KEY_TEXT = pick("#d0d0d0", "#8a8a8c"), pick("#222", "#363637")
HZ_EDGE = pick("#707070", "#656567")  # beside the keys, under the bar numbers
HZ_RULER = pick("#f3f3f3", "#434346")
HZ_DOT = pick("white")  # inside a slide's dot
HZ_RED = pick("#e02020", "#ff5050")  # the red tune line
HZ_FAINT = pick("#f0a0a0", "#8a5050")  # behind the red line: each repeat's own pitch
HZ_GREEN = pick("#18a048", "#3fcf6f")  # a note's exact tone, where each repeat would start
HZ_UNMADE = pick("#8a8a8a", "#1f1f21")  # over what the preview hasn't made yet
HZ_BAND_FIXED = (pick("#8ee0a4"), pick("#18a048"))
HZ_BAND_MIXED = (pick("#ffc27a"), pick("#c06000"))
# effect panes (the synth window has its own, synth_look)
FX_BG, FX_GRID = pick("white", "#3c3c3f"), pick("#e4e4e4", "#4a4a4d")
FX_OUTSIDE, FX_NOTES = pick("#f1f1f1", "#333336"), pick("#c8d6f5", "#3d4a66")
FX_REPEAT, FX_NAMES, FX_TEXT = pick("#dcdcdc", "#555558"), pick("#fafafa", "#434346"), pick("#999", "#a0a0a4")
FX_EDGE, FX_BOX, FX_POINT = pick("#707070", "#656567"), pick("#555", "#a0a0a4"), pick("white", "#3c3c3f")

# --- The window itself in the dark look (the light look keeps the system's own buttons and boxes)
WINDOW_BG, TEXT, TEXT_OFF = "#3a3a3c", "#e2e2e4", "#7c7c80"
FIELD_BG, TROUGH_BG = "#2f2f31", "#333336"
BUTTON_BG, BUTTON_HOT, BUTTON_DOWN = "#4a4a4d", "#57575a", "#424245"
EDGE, PICK_BG = "#5c5c5f", "#2f6fd0"


def readable(colour):
    """A colour made for a white background, light enough to see on the dark one (its hue kept): the light look
    keeps it as it is."""
    if not DARK:
        return colour
    import colorsys
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    lum = max(lum, 0.6)
    if s > 0.1:
        s = max(s, 0.55)
    return "#%02x%02x%02x" % tuple(round(v * 255) for v in colorsys.hls_to_rgb(h, lum, s))


def apply(root):
    """The dark look for every window (called once, before any widget is made): Tk's plain "clam" buttons and
    boxes (Windows' own can't be coloured) and default colours for the plain Tk widgets; each window's title bar
    goes dark as it opens. The light look: nothing changes."""
    if not DARK:
        return
    from tkinter import ttk
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=WINDOW_BG, foreground=TEXT, fieldbackground=FIELD_BG, bordercolor=EDGE,
                 lightcolor=WINDOW_BG, darkcolor=WINDOW_BG, troughcolor=TROUGH_BG, selectbackground=PICK_BG,
                 selectforeground="#ffffff", insertcolor=TEXT, arrowcolor=TEXT, focuscolor=EDGE)
    st.map(".", foreground=[("disabled", TEXT_OFF)], background=[("disabled", WINDOW_BG)])
    for name in ("TButton", "TMenubutton"):
        st.configure(name, background=BUTTON_BG, lightcolor=BUTTON_BG, darkcolor=BUTTON_BG)
        st.map(name, background=[("disabled", WINDOW_BG), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOT)],
               lightcolor=[("pressed", BUTTON_DOWN), ("active", BUTTON_HOT)],
               darkcolor=[("pressed", BUTTON_DOWN), ("active", BUTTON_HOT)])
    st.configure("Toolbutton", background=WINDOW_BG, lightcolor=WINDOW_BG, darkcolor=WINDOW_BG,
                 bordercolor=WINDOW_BG)  # (the picked tool = a normal button's grey, user: clam's own was too bright)
    tool = [("disabled", WINDOW_BG), ("pressed", BUTTON_DOWN), ("active", BUTTON_HOT), ("selected", BUTTON_BG)]
    st.map("Toolbutton", background=tool, lightcolor=tool[1:], darkcolor=tool[1:],  # (like Undo: lit, darker held)
           bordercolor=[("pressed", EDGE), ("active", EDGE), ("selected", EDGE)])
    for name in ("TCheckbutton", "TRadiobutton"):
        st.configure(name, indicatorbackground=FIELD_BG, indicatorforeground=TEXT, upperbordercolor=EDGE,
                     lowerbordercolor=EDGE)
        st.map(name, background=[("active", "#424245")], indicatorbackground=[("pressed", BUTTON_BG),
                                                                               ("disabled", WINDOW_BG)])
    for name in ("TEntry", "TSpinbox", "TCombobox"):
        st.configure(name, lightcolor=FIELD_BG, darkcolor=FIELD_BG)
        st.map(name, fieldbackground=[("disabled", WINDOW_BG), ("readonly", FIELD_BG)],
               bordercolor=[("focus", "#6d8fc7")])
    st.configure("TCombobox", background=BUTTON_BG)
    st.map("TCombobox", background=[("active", BUTTON_HOT)], selectbackground=[("readonly", "!focus", FIELD_BG)],
           selectforeground=[("readonly", "!focus", TEXT)])
    st.configure("TSpinbox", background=BUTTON_BG)
    st.configure("TScrollbar", background=BUTTON_BG, lightcolor=BUTTON_BG, darkcolor=BUTTON_BG, bordercolor=WINDOW_BG,
                 gripcount=0)
    st.map("TScrollbar", background=[("active", BUTTON_HOT)])
    st.configure("TNotebook", background=WINDOW_BG)
    st.configure("TNotebook.Tab", background=BUTTON_DOWN, lightcolor=BUTTON_DOWN)
    st.map("TNotebook.Tab", background=[("selected", WINDOW_BG)], lightcolor=[("selected", WINDOW_BG)])
    st.configure("Treeview", background=FIELD_BG, fieldbackground=FIELD_BG, foreground=TEXT)
    st.map("Treeview", background=[("selected", PICK_BG)], foreground=[("selected", "#ffffff")])
    st.configure("Treeview.Heading", background=BUTTON_BG)
    st.configure("TProgressbar", background=PICK_BG, lightcolor=PICK_BG, darkcolor=PICK_BG)
    st.configure("TScale", background=BUTTON_BG)
    st.configure("TSeparator", background=EDGE)
    for key, value in (("*Background", WINDOW_BG), ("*Foreground", TEXT), ("*activeBackground", BUTTON_HOT),
                       ("*activeForeground", TEXT), ("*disabledForeground", TEXT_OFF), ("*selectBackground", PICK_BG),
                       ("*selectForeground", "#ffffff"), ("*insertBackground", TEXT), ("*highlightBackground", WINDOW_BG),
                       ("*highlightColor", EDGE), ("*troughColor", TROUGH_BG), ("*selectColor", FIELD_BG),
                       ("*Listbox.background", FIELD_BG), ("*Text.background", FIELD_BG),
                       ("*Entry.background", FIELD_BG), ("*TCombobox*Listbox.background", FIELD_BG),
                       ("*Menu.background", FIELD_BG), ("*Menu.activeBackground", PICK_BG)):
        root.option_add(key, value)
    root.configure(background=WINDOW_BG)  # (made before the defaults above: shows round the panes)
    from window.synth_look import dark_title
    darkened = set()

    def title(e):
        w = e.widget
        if w not in darkened:  # (a moment after it shows: set at once, some windows kept a light title bar)
            darkened.add(w)
            w.after(20, lambda: w.winfo_exists() and dark_title(w))
    for cls in (root.winfo_class(), "Toplevel"):  # (the main window's class is the program's name)
        root.bind_class(cls, "<Map>", title, add="+")
