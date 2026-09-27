"""What the piano roll's parts (and the velocity pane) share: colours, key names, modifier keys, shape outline
caches."""

import ctypes

from notes.engine import cached_path, cached_strokes  # (used from here by the piano roll's parts)

PREVIEW_LIMIT = 200_000  # a custom shape / funnel being drawn with more notes than this previews as its outline only


class _MouseMovePoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("time", ctypes.c_uint32),
                ("extra", ctypes.c_size_t)]


try:
    _get_trail = ctypes.windll.user32.GetMouseMovePointsEx
    _get_trail.argtypes = [ctypes.c_uint, ctypes.POINTER(_MouseMovePoint), ctypes.POINTER(_MouseMovePoint),
                           ctypes.c_int, ctypes.c_uint]
except (AttributeError, OSError):
    _get_trail = None


def mouse_trail(x_root, y_root, since):
    """Where the mouse was on the way to (x_root, y_root) on the screen: [(x, y, time)], oldest first, only moves
    after time `since` (ms). Windows keeps the last 64 positions; while the program is busy (redrawing) it only
    hands over the latest one, so a fast freehand stroke would lose its bends without this. since None: just the
    latest position (to start from). [] if unknown (e.g. the cursor was put there by a program, not moved)."""
    if _get_trail is None:
        return []
    now = _MouseMovePoint(x_root & 0xFFFF, y_root & 0xFFFF, 0, 0)
    buf = (_MouseMovePoint * 64)()
    n = _get_trail(ctypes.sizeof(_MouseMovePoint), ctypes.byref(now), buf, 64, 1)  # 1 = screen pixels
    out = []
    for p in buf[:max(n, 0)]:  # newest first
        if since is not None and not 0 < (p.time - since) & 0xFFFFFFFF < 0x80000000:
            break  # not newer than `since` (the clock wraps after 49 days)
        # a screen left of / above the main one gives coordinates past 32767
        out.append((p.x - 65536 if p.x > 32767 else p.x, p.y - 65536 if p.y > 32767 else p.y, p.time))
        if since is None:
            break
    return out[::-1]


BLACK = {1, 3, 6, 8, 10}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Note colours per channel slot (auto channels), blue first. 15 of them, one per usable channel,
# so a colour always means the same channel
SLOT_COLORS = [("#7ea6f5", "#1f3a93"), ("#f58e8e", "#8f1f1f"), ("#8fd68f", "#1f6f1f"), ("#e6c65c", "#7a5f00"),
               ("#c79bf2", "#5a2a8f"), ("#6fd6d6", "#136b6b"), ("#f2a36b", "#8a4310"), ("#b0b0b0", "#404040"),
               ("#f59ad0", "#8f1f63"), ("#c2e36b", "#4f6b0f"), ("#9aa0f5", "#2a2f8f"), ("#c9a27e", "#5c3a1a"),
               ("#7fd6b0", "#135c3f"), ("#e38ae3", "#7a1f7a"), ("#8fb8d6", "#1f4a6b")]
SELECTED_COLOR = ("#ffb65c", "#9a4b00")
DRAFT_COLOR = ("#9be39b", "#1d6b1d")

SHIFT, CTRL, ALT = 0x1, 0x4, 0x20000
PIANO_88 = range(21, 109)  # A0 to C8, the keys of a real piano


def fade(color, amount=0.72):
    """color mixed towards white"""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(c + (255 - c) * amount) for c in (r, g, b))


def note_name(p):
    return f"{NOTE_NAMES[p % 12]}{p // 12 - 1}"
