"""The program's colours and fonts, all in one place (user, 2026-10-07: for dark mode and the planned Look settings
window). Each colour is pick(light, dark): the light one is the look as it always was; dark None = not picked yet
(the light one is used). DARK is fixed before any window is made (changing it needs a restart, user).

Not here: the note colours (roll_shared.SLOT_COLORS: the same in both looks), colours a user picks, and the synth
window, which has its own dark look (synth_look.py, user: keep it as it is)."""

DARK = False


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
HINT = pick("#777")  # grey hints and units beside boxes
INFO = pick("#555")  # status / info lines
SOFT_TEXT = pick("#666")  # longer help texts, legends
FAINT_TEXT = pick("#999")  # barely there (a version number, things not used)
FAINTER_TEXT = pick("#aaa")
DARK_TEXT = pick("#222")  # values in a list of numbers
LABEL = pick("#333")  # small texts drawn on canvases (numbers on a graph's edge, bar numbers)
ERROR = pick("#d00000")  # a wrong value, a warning that blocks something
ERROR_DARK = pick("#c00000")  # (the pattern window's)
ERROR_BG = pick("#f6c0c0")  # a box holding something that can't be used
BAD_MARK_TEXT, BAD_MARK = pick("white"), pick("#d33")  # a wrong letter marked in a text box
GOOD = pick("#1d6b1d")  # done / fine
WARN = pick("#c06000")  # orange: something to look at (a gap closed, a limit reached)
WARN_DARK = pick("#9a4b00")
FILE_OK, FILE_WARN = pick("#2a7"), pick("#c60")  # a picture file found / missing or changed
LINK = pick("#1a5fb4")
VALUE = pick("#0a50e0")  # the value under the mouse on a graph, a setting a graph drives
TIP_BG = pick("#ffffe8")  # tooltips
SASH = pick("#c8c8c8")  # the bar between the side panel and the piano roll
FIELD_SOFT = pick("#fafafa")  # text boxes only read from
FIELD = pick("white")  # text boxes typed in
WHITE_BOX = pick("#ffffff")  # a plain white box (the font preview)
CLOSE, CLOSE_HOT = pick("#888"), pick("#d00")  # the × closing a tab
GAP_PICKED = pick("#ffd9b0")  # a picked list row in the warning colour
FUTURE, FUTURE_PICKED = pick("#a0a0a0"), pick("#ffffff")  # History: steps undone
MISSING_SHAPE = pick("#808080")  # a placed shape's name the library no longer has
LIST_AWAY, LIST_AWAY_TEXT = pick("#d9d9d9"), pick("black")  # a picked list row while the keyboard is elsewhere
SEARCH_HIT = pick("#fff08a")  # Help: the words searched for
OUTLINE_SOFT = pick("#555")  # a thin edge round a small colour box

# --- Piano roll
ROLL_BG = pick("#ffffff")  # white-key rows
ROW_BLACK = pick("#e7eefa")  # black-key rows
ROW_LINE = pick("#dfe6f2")  # between two white-key rows
OCTAVE_LINE = pick("#606060")  # under each C
OUTSIDE_KEYS = pick("#ececec")  # past the lowest / highest key
GRID_SNAP, GRID_BEAT, GRID_BAR = pick("#eef1f6"), pick("#bcc4d2"), pick("#3a3a3a")
KEY_WHITE = pick("#ffffff")
KEY_OUTSIDE = pick("#e2e2e2")  # keys outside a real 88-key piano (faintly greyed)
KEY_BLACK, KEY_BLACK_OUTSIDE = pick("#222222"), pick("#6a6a6a")
KEY_EDGE_C, KEY_EDGE_F = pick("#606060"), pick("#b0b0b0")  # under C, under F
KEY_TEXT = pick("#333")
ROLL_EDGE = pick("#808080")  # beside the keys, under the ruler
RULER_BG, RULER_TICK, RULER_TEXT = pick("#f0f0f0"), pick("#555"), pick("#333")
PLAY_LINE = pick("#0a50e0")
HANDLE = pick("#0050d0")  # a shape's points and handles
HANDLE_FILL = pick("#ffffff")  # inside them, and the white edge round handle lines
HANDLE_FIXED = pick("#c00000")  # a corner that isn't free, the points of a shape being drawn
STROKE_POINT = pick("#7a1fe0")  # the points of a custom shape's strokes (purple, like a picked stroke)
PART = pick("#7a1fe0")  # a highlighted funnel line / the curve clicked
TWIN = pick("#00a39a")  # the curves linked to it
LIVE_LINE = pick("#7a1fe0")  # a live shape's stroke being drawn
RING = pick("#b40000")  # round the selected shapes' notes (user: #e00000 looked bright, almost pink)
PREVIEW_LINE, PREVIEW_HALO = pick("#ff1f1f"), pick("#ffa8a8")  # the outline gate's preview line
SHAPE_LINE = pick("#c0392b")  # a shape's line (shown lines), an arrow for a shape above the top key
SHAPE_LINE_PICKED = pick("#ff1f1f")  # ... selected
ABOVE_EDGE = pick("#ffffff")  # round that arrow
ORIGIN, ORIGIN_PICKED = pick("#efc0c0"), pick("#e89a9a")  # a line as drawn under its tumours / pattern (dashed)
DRAFT_LINE = pick("#0a8f0a")  # the shape being drawn
CUT, CUT_UNDER = pick("#d00000"), pick("#ffffff")  # Slice line, cut marks, scissors (+ the white under a mark)
FAINT_CUT = pick("#f0c8c8")  # the really faint line from a piece's cut end to the other piece's (user)
LABEL_BG, LABEL_EDGE = pick("#fffbe6"), pick("#c9b26b")  # a placed picture's name above it
SELECT_BOX = pick("#000000")  # the Select box's line
TEXT_BOX = pick("#3a7bd5")  # the Text tool's box and selected letters
TEXT_CARET = pick("#000000")
TROUGH, THUMB, THUMB_HOT, GRIP = pick("#e4e4e4"), pick("#b4b4b4"), pick("#9a9a9a"), pick("#707070")  # scrollbars

# --- Graphs (velocity pane, tumour graph, Range…, pattern window)
CHART_BG = pick("#ffffff")
CHART_BORDER = pick("#a0a0a0")  # round the graph's box
CHART_FRAME = pick("#808080")  # round the drawing area
CHART_GRID, CHART_GRID_STRONG = pick("#d3dff0"), pick("#9fb2cf")  # (strong: 0 and 100 %)
CHART_BAND = pick("#e4e4e4")  # a greyed stretch / faint lines
CHART_LINE = pick("#d00000")  # the line you draw
CHART_LINE_FAINT = pick("#f0b0b0")
CHART_OFF = pick("#b0b0b0")  # the line while it's switched off
CHART_POINT = pick("#ffffff")  # inside the line's points
RANGE_BAR, RANGE_BAR_HOT, RANGE_NONE = pick("#4a90e2"), pick("#1f5fb0"), pick("#e0503c")  # Range…: notes per gate
PAT_BORDER = pick("#c0c0c0")  # Pattern window: round the drawing
PAT_ORIGIN, PAT_AB = pick("#e0a0a0"), pick("#a05050")  # the curve it goes along, its ends' letters
PAT_NEIGHBOUR = pick("#d8d8e8")  # the loops before and after, faint
PAT_FORMULA = pick("#d0d0d0")  # the formula it came from, under it
PAT_LINE, PAT_ARM = pick("#d01010"), pick("#6080c0")  # the loop, its handle lines
PAT_GRID, PAT_GRID_STRONG, PAT_GRID_TEXT = pick("#eeeeee"), pick("#d4d4d4"), pick("#aaaaaa")

# --- Drawer
DRAWER_BG = pick("#f4f4f4")  # around the board
BOARD = pick("#ffffff")
AREA_NORMAL, AREA_EMPTY = pick("#d4d4d4"), pick("#fbe4e4")  # area colours: plain / left empty
AREA_HOVER = pick("#e8eef8")  # the area under the mouse (when it has no colour)
BOARD_MIDDLE, BOARD_GRID_MAJOR, BOARD_GRID = pick("#7f8fb0"), pick("#9aa4b4"), pick("#dde3ec")
BOARD_EDGE = pick("#606060")
SWATCH_EDGE, SWATCH_EDGE_ON = pick("#909090"), pick("#000000")  # the area colour boxes (picked: black)
STROKE_PICKED, POINT_PICKED = pick("#ff8c1a"), pick("#c05a00")
OPEN_END, OPEN_END_EDGE = pick("#ff2020"), pick("#800000")  # red dots at a stroke's open ends
ERASE_BOX = pick("#d02020")
STICK = pick("#d000d0")  # the mark where a point sticks
STICK_LINE = pick("#c070e0")  # the parts ending at that point (lighter, so the mark stands out on them)

# --- Small windows
TOOL_ROW, TOOL_HOVER, TOOL_PICKED = pick("#ffffff"), pick("#e5f3ff"), pick("#cce4f7")  # the drawing tools list
KNOB_ORANGE, KNOB_GREEN = pick("#f5a623"), pick("#7cc21b")  # Claw / Strum / Chop knobs
KNOB_RING, KNOB_RING_FOCUS, KNOB_RING_OFF = pick("#bbb"), pick("#888"), pick("#ccc")
KNOB_BODY, KNOB_BODY_OFF, KNOB_POINTER = pick("#555"), pick("#aaa"), pick("white")
TAB_TROUGH, TAB_THUMB, TAB_THUMB_HELD = pick("#e2e2e2"), pick("#a6a6a6"), pick("#7a7a7a")  # the tab row's scrollbar
CHOP_BORDER, CHOP_GRID, CHOP_GRID_STRONG, CHOP_MIDDLE = pick("#bbb"), pick("#e8e8e8"), pick("#bbb"), pick("#ddd")
CHOP_MARK = pick("#b06d00")  # where a note is cut
FMT_TAG, FMT_PLAIN, FMT_PLAIN_BG = pick("#1060c0"), pick("#888"), pick("#eee")  # Format window: a code, plain text
FMT_SPACE = pick("#cfe2ff")  # spaces at the ends of a line
FMT_RETURN = pick("#8aa8d0")  # the ↵ at a line's end
FMT_BAD = pick("#c33")
PASTE_USE, PASTE_OTHER, PASTE_EMPTY = pick("#bfe0ff"), pick("#e6e6e6"), pick("#f0f0f0")  # Paste from…: lines used
IMG_PREVIEW_BG, IMG_BORDER = pick("black"), pick("#999")  # Image window: behind the picture
IMG_HANDLE = pick("#dfe8f5")  # the box to drag onto the piano roll
IMG_MARK, IMG_MARK_EDGE = pick("white"), pick("black")  # the corner mark on a picked colour
IMG_KEYS, IMG_KEY_BLACK = pick("#e8e8e8"), pick("#222")  # the small keyboards round the preview
IMG_DRAG = pick("#e02020")  # the picture's box while dragged

# --- Hz bass window
HZ_BG = pick("white")
HZ_ROW_BLACK = pick("#eef1f8")
HZ_OCTAVE_LINE, HZ_ROW_LINE = pick("#c9c9c9"), pick("#ececec")
HZ_GRID_BAR, HZ_GRID_BEAT, HZ_GRID = pick("#707070"), pick("#bdbdbd"), pick("#ececec")
HZ_SHADE, HZ_SHADE_EDGE = pick("#d8d8d8"), pick("#909090")  # past the shape's end
HZ_KEYS, HZ_KEY_BLACK, HZ_KEY_LINE, HZ_KEY_TEXT = pick("#fafafa"), pick("#303030"), pick("#d0d0d0"), pick("#222")
HZ_EDGE = pick("#707070")  # beside the keys, under the bar numbers
HZ_RULER = pick("#f3f3f3")
HZ_DOT = pick("white")  # inside a slide's dot
HZ_RED = pick("#e02020")  # the red tune line
HZ_FAINT = pick("#f0a0a0")  # behind the red line: each repeat's own pitch
HZ_GREEN = pick("#18a048")  # a note's exact tone, where each repeat would start
HZ_UNMADE = pick("#8a8a8a")  # over what the preview hasn't made yet
HZ_BAND_FIXED = (pick("#8ee0a4"), pick("#18a048"))
HZ_BAND_MIXED = (pick("#ffc27a"), pick("#c06000"))
# effect panes (the synth window has its own, synth_look)
FX_BG, FX_GRID, FX_OUTSIDE, FX_NOTES = pick("white"), pick("#e4e4e4"), pick("#f1f1f1"), pick("#c8d6f5")
FX_REPEAT, FX_NAMES, FX_TEXT = pick("#dcdcdc"), pick("#fafafa"), pick("#999")
FX_EDGE, FX_BOX, FX_POINT = pick("#707070"), pick("#555"), pick("white")
