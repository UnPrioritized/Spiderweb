"""The program's colours and fonts, all in one place (user, 2026-10-07: for dark mode and the planned Look settings
window). Each colour is pick(light, dark): the light one is the look as it always was; dark None = not picked yet
(the light one is used). DARK is fixed as this file is first read, before any window is made: the look picked in
Settings → Look is saved in user/settings.ini and put on at the next start (user).

Not here: the note colours (roll_shared.SLOT_COLORS: the same in both looks), colours a user picks, and the synth
window, which has its own dark look (synth_look.py, user: keep it as it is)."""

import json
import os

from files import settings
from files.about import USER
from files.system import WINDOWS, dark_system

LOOKS = ("light", "dark", "windows")  # (saved as these; windows = follow the system's setting)
LOOK_FILE = os.path.join(USER, "look.json")  # (older versions' file: moved into settings.ini once, kept as look-old)


def read_look():
    """The look picked in Settings → Look ("light" when none was, or the file won't read)."""
    how = settings.get("look", "look")
    if how is None and os.path.exists(LOOK_FILE):
        try:
            with open(LOOK_FILE, encoding="utf-8") as f:
                how = json.load(f).get("look")
        except (OSError, ValueError, AttributeError):
            how = None
        if how in LOOKS:
            try:
                save_look(how)
                os.replace(LOOK_FILE, os.path.splitext(LOOK_FILE)[0] + "-old.json")
            except OSError:
                pass
    return how if how in LOOKS else "light"


def save_look(how):
    settings.put("look", "look", how)


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

# Settings → Look's Size, Font and Text size (user, 2026-10-11; put on at the next start, like the look):
# size = "system" (the screen's own scaling) or a percent; font = "" (the usual one) or a family; text = a step
SIZES = ("system", 75, 100, 125, 150, 175, 200)
TEXT_STEPS = {"small": -1, "normal": 0, "large": 2}  # (points added to every text)
STEP = 0  # the Text size in use (apply)
RUNNING = ("system", "", "normal")  # the Size, Font, Text size this start put on (apply_size)
USUAL_FAMILY = SANS  # Tk's own widget font before a picked Font replaced it (the Font list's "Default")
USUAL_SIZE, SCREEN_DPI = 9, 96.0  # ... its size in points, and the screen's pixels an inch (the Preview's sizes)
WIDE = 1.0  # how many times wider the picked Font / Text size makes texts (never under 1): the side panel's width
TK_FONTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
            "TkIconFont", "TkTooltipFont")


def whole_number(value):
    """A saved number as an int, also when edited by hand ("150.0", "150 %"); None = not a whole number."""
    try:
        number = float(str(value).strip().rstrip("%").strip())
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def read_size():
    """The Size picked: "system" or a percent (an int)."""
    size = whole_number(settings.get("look", "size", "system"))
    return size if size in SIZES else "system"


def read_font():
    return settings.get("look", "font", "") or ""


def read_text():
    text = str(settings.get("look", "text", "normal")).strip().lower()
    return text if text in TEXT_STEPS else "normal"


LINE_WIDTHS = (1, 2, 3, 4)


def read_lines():
    """Shape lines: how thick the shapes' lines are in the piano roll, in pixels (a selected one's a pixel more)."""
    width = whole_number(settings.get("look", "lines") or 1)
    return width if width in LINE_WIDTHS else 1


LINES = read_lines()  # (changed live from Settings)


def font(size, *style, family=None, fit=False):
    """A font for a widget or a canvas text: font(9), font(8, "bold"). fit = sized to fit a fixed room (the piano
    roll's key names): the Text size leaves it alone."""
    return (family or SANS, size if fit else max(6, size + STEP), *style)


def mono(size, *style):
    """A font where every letter is as wide (formulas, text to copy)."""
    return (MONO, max(6, size + STEP), *style)


# --- Texts on the window
HINT = pick("#777", "#a0a0a4")  # grey hints and units beside boxes
LABEL_OFF = pick("#a0a0a0", "#7c7c80")  # a word beside a greyed box (light ttk labels don't grey by themselves)
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
LIST_HERE = pick("SystemHighlight" if WINDOWS else "#3874d8", "#2f6fd0")  # ... while it has the keyboard
LIST_HERE_TEXT = pick("SystemHighlightText" if WINDOWS else "#ffffff", "#ffffff")
ICON = pick("#000000", "#e2e2e4")  # small drawn pictures on buttons (snap notes, ▾, a pinned tool's pin)
ICON_OFF = pick("#b0b0b0", "#6c6c70")  # an unpinned tool's pin
SEARCH_HIT = pick("#fff08a", "#6b6420")  # Help: the words searched for
OUTLINE_SOFT = pick("#555", "#8a8a8e")  # a thin edge round a small colour box

# --- Piano roll
ROLL_BG = pick("#ffffff", "#3c3c3f")  # white-key rows (quiet notes fade toward it too)
ROW_BLACK = pick("#e7eefa", "#353538")  # black-key rows
ROW_LINE = pick("#dfe6f2", "#353538")  # between two white-key rows
# 31-EDO: half sharp / half flat rows, and its sharp / flat rows a little darker than ROW_BLACK (three clear steps)
ROW_HALF, ROW_BLACK_EDO = pick("#eef2fa", "#373739"), pick("#dce4f4", "#303033")
OCTAVE_LINE = pick("#606060", "#7a7a7c")  # under each C
OUTSIDE_KEYS = pick("#ececec", "#2f2f31")  # past the lowest / highest key
GRID_SNAP, GRID_BEAT, GRID_BAR = pick("#eef1f6", "#464649"), pick("#bcc4d2", "#57575a"), pick("#3a3a3a", "#8a8a8c")
KEY_WHITE = pick("#ffffff", "#b5b5b6")
KEY_OUTSIDE = pick("#e2e2e2", "#9a9a9b")  # keys outside a real 88-key piano (faintly greyed)
KEY_BLACK, KEY_BLACK_OUTSIDE = pick("#222222", "#212123"), pick("#6a6a6a", "#4a4a4c")
KEY_HALF, KEY_HALF_OUTSIDE = pick("#8c8c8c", "#6a6a6c"), pick("#b4b4b4", "#58585a")  # 31-EDO's half sharps / flats
KEY_EDGE_C, KEY_EDGE_F = pick("#606060", "#5a5a5c"), pick("#b0b0b0", "#8a8a8c")  # under C, under F
KEY_TEXT = pick("#333", "#363637")
ROLL_EDGE = pick("#808080", "#656567")  # beside the keys, under the ruler
RULER_BG, RULER_TICK, RULER_TEXT = pick("#f0f0f0", "#434346"), pick("#555", "#a0a0a4"), pick("#333", "#e2e2e4")
RULER_TEMPO = pick("#8a8a8a", "#a0a0a4")  # the tempo changes marked above the bar numbers (light)
RULER_TEMPO_LINE = pick("#d2d2d2", "#5a5a5e")  # their line on to the next change (fainter still)
RULER_PICK, RULER_PICK_TEXT =pick("#141414", "#e2e2e4"), pick("#ffffff", "#2f2f31")  # the ruler under a Select box
#                                                                                       over every key (dark: turned)
PLAY_LINE = pick("#0a50e0", "#d6e8ff")  # (dark: pale blue, the bright notes' blue hid a stronger one, user)
# The notes' (fill, edge) per channel slot in the dark look (user's pick "B warm": the light look's colours lighter
# with a little cream; light look's in roll_shared.SLOT_COLORS), the shape being drawn
NOTES_DARK = [("#a3bef1", "#19449b"), ("#f7a39e", "#a51610"), ("#a6f4a0", "#36842b"), ("#f5e1a0", "#9c7b18"),
              ("#cea4ef", "#611b95"), ("#a6f4ef", "#328983"), ("#f7c59e", "#a54f10"), ("#ceccc7", "#605b55"),
              ("#f6a3ce", "#a31762"), ("#dff4a0", "#79931d"), ("#a5a9f0", "#1c2098"), ("#f5caa0", "#89592b"),
              ("#a6f4cc", "#35865a"), ("#f5a4ef", "#922887"), ("#a6d2ef", "#36617e"), ("#5fc4b4", "#1d6b60")]
SELECTED_DARK, DRAFT_DARK = ("#ffd196", "#b26402"), ("#a6f4a0", "#318926")
HANDLE = pick("#0050d0", "#5b9bff")  # a shape's points and handles
HANDLE_FILL = pick("#ffffff")  # inside them, and the white edge round handle lines
HANDLE_FIXED = pick("#c00000", "#ff4d4d")  # a corner that isn't free, the points of a shape being drawn
STROKE_POINT = pick("#7a1fe0", "#b37aff")  # the points of a custom shape's strokes (purple, like a picked stroke)
PART = pick("#7a1fe0", "#b37aff")  # a highlighted funnel line / the curve clicked
TWIN = pick("#00a39a", "#2fd6cb")  # the curves linked to it
LIVE_LINE = pick("#7a1fe0", "#b37aff")  # a live shape's stroke being drawn
RING = pick("#b40000", "#f4f4f6")  # round the selected shapes' notes (user: #e00000 looked bright, almost pink;
# dark: white, red was harsh and grey hid against the light notes)
PREVIEW_LINE, PREVIEW_HALO = pick("#ff1f1f"), pick("#ffa8a8", "#7a3030")  # the outline gate's preview line
SHAPE_LINE = pick("#c0392b", "#a4a4a8")  # a shape's line (shown lines), an arrow for a shape above the top key
SHAPE_LINE_PICKED = pick("#ff1f1f", "#dcdce0")  # ... selected (dark: greyish white like the ring, user)
STROKE = pick("#c0392b", "#dcdce0")  # the drawer's strokes, its Empty cross (dark: greyish white, user)
ABOVE_EDGE = pick("#ffffff")  # round that arrow
ORIGIN, ORIGIN_PICKED = pick("#efc0c0", "#7a5656"), pick("#e89a9a", "#a86a6a")  # a line as drawn under its tumours
DRAFT_LINE = pick("#0a8f0a", "#3fcf3f")  # the shape being drawn
GHOST_NOTE = pick(("#f0f0f0", "#dadada"), ("#454548", "#505054"))  # a masked shape's notes the mask hides (mask.py)
MASK_LINE, MASK_LINE_PICKED = pick("#008c8c", "#3fc9c9"), pick("#00b3b3", "#7ae6e6")  # a mask's dashed line
MASK_GROUPS = [  # a mask group's dashed line, (line, picked) (mask.GROUP_COLOURS; never red or the plain mask's teal)
    (pick("#d97a00", "#f0a040"), pick("#ff9a20", "#ffc580")),  # orange
    (pick("#7b3fb8", "#b58ae6"), pick("#9a5ce0", "#d4b8f5")),  # purple
    (pick("#c2338a", "#ec7fc0"), pick("#e04fa8", "#f6b3dc")),  # pink
    (pick("#2563c9", "#7aa8f0"), pick("#3d7ef0", "#b3cdf8")),  # blue
    (pick("#5f8a1c", "#a2cc5c"), pick("#78aa28", "#c8e69a")),  # green
    (pick("#8a5a2b", "#c99b6c"), pick("#a8703a", "#e0c09c")),  # brown
]
MASKED_LINE = pick("#f0c8c8", "#6a5a5a")  # the line of a shape a mask covers: faint
CUT, CUT_UNDER = pick("#d00000", "#ff5050"), pick("#ffffff")  # Slice line, cut marks, scissors (+ the white under a mark)
PATH_LINE = pick("#0d8f46", "#62d996")  # Copy along a path: the path on the piano roll, its points A / middle / B
SUSTAIN_STOP = pick("#b0179a", "#f07ae0")  # Sustain: its stops put by hand, lines on the piano roll (roll_sustain.py)
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
CHART_LINE = pick("#d00000", "#dcdce0")  # the line you draw (dark: greyish white, user)
CHART_LINE_FAINT = pick("#f0b0b0", "#6a6a6e")
PUSH_PICKED = pick("#fde7a8", "#9a8640")  # (Add between preview: a pale band under the shape the push pad pushes)
CHART_OFF = pick("#b0b0b0", "#707074")  # the line while it's switched off
CHART_POINT = pick("#ffffff", "#3c3c3f")  # inside the line's points
# Tempo pane: the tempo line, its points, a catch-up (teal) and the drawn part it pays back (amber), red = can't
TEMPO_LINE, TEMPO_PICKED = pick("#2f6fdb", "#7ab0ff"), pick("#1d4fb0", "#b8d4ff")
TEMPO_CATCH, TEMPO_CATCH_BG = pick("#12a08a", "#3cc9b0"), pick("#d6f2ec", "#2f4a46")
TEMPO_DRAWN_BG = pick("#fbefd9", "#4a4234")
TEMPO_DRAWN_EDGE = pick("#d9a54a", "#b08a4a")  # the catch-up area's left edge (draggable)
TEMPO_BAD_BG = pick("#fadcdc", "#553636")
TEMPO_KEEP, TEMPO_KEEP_BG = pick("#8a4fc8", "#c39cf0"), pick("#ece2f8", "#3e3550")  # Keep timing parts
TEMPO_LIMIT = pick("#e89a9a", "#9a5050")  # the slowest / fastest tempo a MIDI file holds (faint red)
TEMPO_BASE = pick("#8fa6c8", "#7a7a80")  # the project's BPM across the pane
SIG_MARK = pick("#5a5a5a", "#b8b8bc")  # the Conductor pane's time / key signature marks (short lines from the top)
RANGE_BAR, RANGE_BAR_HOT = pick("#4a90e2"), pick("#1f5fb0", "#7ab4ff")  # Range…: notes per gate, the one pointed at
RANGE_NONE = pick("#e0503c")  # gates with none
PAT_BORDER = pick("#c0c0c0", "#5c5c5f")  # Pattern window: round the drawing
PAT_ORIGIN, PAT_AB = pick("#e0a0a0", "#6a6a6e"), pick("#a05050", "#b0b0b4")  # the curve it goes along, its ends' letters
PAT_NEIGHBOUR = pick("#d8d8e8", "#55556a")  # the loops before and after, faint
PAT_FORMULA = pick("#d0d0d0", "#5a5a5d")  # the formula it came from, under it
PAT_LINE, PAT_ARM = pick("#d01010", "#dcdce0"), pick("#6080c0", "#7a9ae0")  # the loop, its handle lines
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
STICK_GUIDE = pick("#dcb4ee", "#7a5a90")  # the drawer's Circle guide: the touched stroke's faint dotted copy
LIST_LINE = pick("#e6e6e6", "#4a4a4e")  # the faint line under each row of the drawer's Layers list
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
HZ_NOTE_BAR = pick("#bfe5cb", "#3d5e47")  # Hz shape window: each key's note, faint (from its hit to its end)
HZ_NOTE_BAR_EDGE = pick("#7fc496", "#5f8a6b")  # ... its edge (notes touching stay apart)
HZ_BEND = pick("#7a3cff", "#b08cff")  # the Bend tool: a note's own bend line and its points
HZ_UNMADE = pick("#8a8a8a", "#1f1f21")  # over what the preview hasn't made yet
HZ_BAND_FIXED = (pick("#8ee0a4"), pick("#18a048"))
HZ_BAND_MIXED = (pick("#ffc27a"), pick("#c06000"))
HZ_LAYER_BG, HZ_LAYER_PICKED = pick("#f7f7f7", "#38383b"), pick("#dbe6fb", "#2f4466")  # the layers strip, picked row
HZ_LAYER_MUTE, HZ_LAYER_SOLO = pick("#f0a030", "#d98a20"), pick("#e8d040", "#c9b230")  # M / S switched on
HZ_LAYER_OFF, HZ_LAYER_ON_TEXT = pick("#ececec", "#46464a"), pick("#000000", "#000000")
HZ_LAYER_SEP = pick("#a8a8ac", "#242426")  # the line between the layers list and the piano roll's keys
HZ_LAYER_DROP = pick("#2f6fd8", "#5b9bff")  # where a dragged layer row goes
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
# the light look on Tk's own buttons (a Button style other than the system's: button_look.py), like Windows' colours
LIGHT_PARTS = dict(window="#f0f0f0", text="#000000", text_off="#8c8c8c", field="#ffffff", trough="#e6e6e6",
                   button="#e1e1e1", hot="#e5f1fb", down="#cce4f7", edge="#adadad", pick="#0078d7", focus="#0078d7",
                   tick_hot="#e5f1fb", outline="#707070", hi="#ffffff", lo="#a0a0a0", lo2="#696969", tab="#e1e1e1")


def parts():
    """The window's colours for the look in use (the Button styles draw with these)."""
    if not DARK:
        return LIGHT_PARTS
    return dict(window=WINDOW_BG, text=TEXT, text_off=TEXT_OFF, field=FIELD_BG, trough=TROUGH_BG, button=BUTTON_BG,
                hot=BUTTON_HOT, down=BUTTON_DOWN, edge=EDGE, pick=PICK_BG, focus="#6d8fc7", tick_hot="#424245",
                outline="#9a9a9e", hi="#6a6a6e", lo="#2c2c2e", lo2="#1e1e20", tab=BUTTON_DOWN)


TITLED = set()  # the open windows whose title bar was made dark (apply)


def rgb(colour):
    """"#rrggbb" as (r, g, b) 0-255 (for pictures drawn pixel by pixel)."""
    return tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))


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


def apply_size(root):
    """Settings → Look's Size, Font and Text size (called once, before any widget is made; nothing saved = nothing
    changes). Size = Tk's points-to-pixels (the windows' own sizes follow it: App.scale is read from it after)."""
    import tkinter
    from tkinter import font as tkfont
    global SANS, STEP, RUNNING, USUAL_FAMILY, USUAL_SIZE, SCREEN_DPI, WIDE
    size, family, text = RUNNING = read_size(), read_font(), read_text()
    STEP, WIDE = TEXT_STEPS[text], 1.0
    dpi = SCREEN_DPI = root.winfo_fpixels("1i")  # (the screen's, before Size changes it)
    try:
        usual = tkfont.nametofont("TkDefaultFont", root=root)
        USUAL_FAMILY, USUAL_SIZE = usual.actual("family"), usual.cget("size")
        if USUAL_SIZE < 0:
            USUAL_SIZE = round(-USUAL_SIZE * 72 / dpi)
    except tkinter.TclError:
        pass
    if family:
        SANS = family
    if family or STEP or size != "system":
        for name in TK_FONTS:
            try:
                f = tkfont.nametofont(name, root=root)
            except tkinter.TclError:
                continue
            points = f.cget("size")
            if points < 0:  # (in pixels on some Linux setups: as points, so Size grows them too)
                points = round(-points * 72 / dpi)
            f.configure(size=max(6, points + STEP), **({"family": family} if family else {}))
    if size != "system":
        root.tk.call("tk", "scaling", size / 100 * 96 / 72)
    WIDE = text_wider(root)


def text_wider(root):
    """How many times wider texts come out than in the font the windows were laid out for (Segoe UI 9: the
    side panel's 330 px): a wider system font (Chinese Windows' Microsoft YaHei UI, Linux's), a picked Font or a
    bigger Text size. Never under 1."""
    import tkinter
    from tkinter import font as tkfont
    sample = "Domino start  First note at tick 0  Output file  Generate"
    try:
        made = tkfont.Font(root=root, family="Segoe UI", size=9)
        if made.actual("family") == "Segoe UI":
            usual = made.measure(sample)
        else:  # (not on this computer: its width at 9 points, 96 pixels an inch, at this screen's pixels)
            usual = 284 * root.winfo_fpixels("1i") / 96
        return max(1.0, tkfont.nametofont("TkDefaultFont", root=root).measure(sample) / usual)
    except (tkinter.TclError, ZeroDivisionError):
        return 1.0


def apply(root):
    """The dark look for every window (called once, before any widget is made): Tk's plain "clam" buttons and
    boxes (Windows' own can't be coloured) and default colours for the plain Tk widgets; each window's title bar
    goes dark as it opens. The light look: nothing changes, unless a Button style other than the system's own is
    picked (the same clam, in light colours: button_look.py). Then the Size / Font / Text size (apply_size)."""
    from window import button_look
    apply_size(root)
    from tkinter import ttk
    st = ttk.Style(root)
    button_look.start(st)
    style = button_look.RUNNING
    if style == "system":
        return
    if not DARK:  # (the window grey = the system's own, which plain Tk widgets keep: #f0f0f0 on Windows)
        try:
            LIGHT_PARTS["window"] = "#%02x%02x%02x" % tuple(v >> 8 for v in root.winfo_rgb(root.cget("background")))
        except (ttk.tkinter.TclError, ValueError):
            pass
    p = parts()
    st.theme_use("clam")
    st.configure(".", background=p["window"], foreground=p["text"], fieldbackground=p["field"], bordercolor=p["edge"],
                 lightcolor=p["window"], darkcolor=p["window"], troughcolor=p["trough"], selectbackground=p["pick"],
                 selectforeground="#ffffff", insertcolor=p["text"], arrowcolor=p["text"], focuscolor=p["edge"])
    st.map(".", foreground=[("disabled", p["text_off"])], background=[("disabled", p["window"])])
    button_look.dress(st, "", style, p)
    for name in ("TCheckbutton", "TRadiobutton"):
        st.configure(name, indicatorbackground=p["field"], indicatorforeground=p["text"], upperbordercolor=p["edge"],
                     lowerbordercolor=p["edge"])
        st.map(name, background=[("active", p["tick_hot"])], indicatorbackground=[("pressed", p["button"]),
                                                                                   ("disabled", p["window"])])
    # (half ticked = a filled blue square: clam's own half tick shows as a full ✕ with these colours)
    st.map("TCheckbutton", indicatorbackground=[("alternate", p["pick"]), ("pressed", p["button"]),
                                                ("disabled", p["window"])], indicatorforeground=[("alternate", p["pick"])])
    st.configure("TScrollbar", background=p["button"], lightcolor=p["button"], darkcolor=p["button"],
                 bordercolor=p["window"], gripcount=0)
    st.map("TScrollbar", background=[("active", p["hot"])])
    st.configure("TNotebook", background=p["window"])
    st.configure("TNotebook.Tab", background=p["tab"], lightcolor=p["tab"])
    st.map("TNotebook.Tab", background=[("selected", p["window"])], lightcolor=[("selected", p["window"])])
    st.configure("Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["text"])
    st.map("Treeview", background=[("selected", p["pick"])], foreground=[("selected", "#ffffff")])
    st.configure("Treeview.Heading", background=p["button"])
    st.configure("TProgressbar", background=p["pick"], lightcolor=p["pick"], darkcolor=p["pick"])
    st.configure("TScale", background=p["button"])
    st.configure("TSeparator", background=p["edge"])
    if style != "plain" or button_look.RUNNING_EDGE != 1:
        button_look.put_on(root, st, "", style, p, t=button_look.RUNNING_EDGE)
    if not DARK:  # (the light look's plain Tk widgets keep the system's colours)
        return
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

    def title(e):
        w = e.widget
        if w not in TITLED and not w.wm_overrideredirect():  # (no title bar: tooltips, the tools list)
            TITLED.add(w)  # (a moment after it shows: set at once, some windows kept a light title bar)
            w.bind("<Destroy>", lambda e: TITLED.discard(w) if e.widget is w else None, add="+")
            dark_title(w)  # (at once too: a shorter white flash)
            w.after(20, lambda: w.winfo_exists() and dark_title(w))
    for cls in (root.winfo_class(), "Toplevel"):  # (the main window's class is the program's name)
        root.bind_class(cls, "<Map>", title, add="+")
