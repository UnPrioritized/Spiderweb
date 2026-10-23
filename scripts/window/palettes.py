"""Note colour palettes (user, 2026-10-11): the 16 channel colours notes are drawn with (roll_shared.SLOT_COLORS; the
16th = channel 10's, used with "Use channel 10"; the drawer's strokes and Hz bass layers use them too). Saved with the
app, not the project, as switchable profiles: "Default" is built in (today's colours: the light look's, or
look.NOTES_DARK in the dark look) and can't be renamed or deleted; the user's are files in user/palettes/ (one file
each, so a palette can be handed to someone), named inside the file like the shape library; files of 15 colours
(before channel 10 had one) get Default's 16th. The picked one: settings.ini [look] palette. Switching repaints at
once. Files keep SLOT_COLORS' order; the Settings window shows them by channel (ORDER)."""

import json
import os

from files import settings
from files.about import USER
from files.safefile import write_text
from roll import roll_shared
from window.drawer import clean_name, file_stem

FOLDER = os.path.join(USER, "palettes")
DEFAULT = ""  # (the built-in palette's id; its shown name is tr("palette.default"))
SLOTS = len(roll_shared.SLOT_COLORS)
BUILT_IN = [tuple(p) for p in roll_shared.SLOT_COLORS]  # (as the look made them at start)
ORDER = roll_shared.COLOURS_16.tolist()  # channel 1..16 -> its place in the colours


def _colour(c):
    """"#rrggbb" (also "#rgb", any capitals), None when it isn't a colour code."""
    c = str(c).strip().lower()
    if not (c[:1] == "#" and len(c) in (4, 7) and all(ch in "0123456789abcdef" for ch in c[1:])):
        return None
    return c if len(c) == 7 else "#" + "".join(ch * 2 for ch in c[1:])


def clean(colours):
    """A palette's colours as SLOTS (fill, edge) pairs; missing / wrong ones = the built-in one in that slot. A fill
    alone (or with a wrong edge) gets its edge made like a picked colour's."""
    out = []
    for k in range(SLOTS):
        pair = colours[k] if isinstance(colours, list) and k < len(colours) else None
        if isinstance(pair, str):
            pair = [pair]
        fill = edge = None
        if isinstance(pair, (list, tuple)) and 1 <= len(pair) <= 2:
            fill = _colour(pair[0])
            edge = _colour(pair[1]) if len(pair) == 2 else None
            if fill and not edge:
                edge = with_edge(fill)[1]
        out.append((fill or BUILT_IN[k][0], edge or BUILT_IN[k][1]))
    return out


def _oklab(c):
    """"#rrggbb" -> OKLab (even steps look even to the eye: gradients don't go muddy in the middle)."""
    lin = [((v / 255 + 0.055) / 1.055) ** 2.4 if v / 255 > 0.04045 else v / 255 / 12.92
           for v in (int(c[i:i + 2], 16) for i in (1, 3, 5))]
    r, g, b = lin
    l_, m_, s_ = ((0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3),
                  (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3),
                  (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3))
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _hex(lab):
    L, a, b = lab
    l_, m_, s_ = L + 0.3963377774 * a + 0.2158037573 * b, L - 0.1055613458 * a - 0.0638541728 * b, \
        L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    rgb = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    out = []
    for v in rgb:
        v = min(1.0, max(0.0, v))
        v = 1.055 * v ** (1 / 2.4) - 0.055 if v > 0.0031308 else 12.92 * v
        out.append(round(v * 255))
    return "#%02x%02x%02x" % tuple(out)


def with_edge(fill):
    """(fill, edge) for a picked colour: the note's outline a darker shade of it, like the built-in ones."""
    L, a, b = _oklab(fill)
    return fill.lower(), _hex((L * 0.5, a * 1.1, b * 1.1))


def by_channel(made):
    """Colours made in channel order (channel 1 first) -> SLOT_COLORS' order."""
    out = [None] * SLOTS
    for ch, c in enumerate(made):
        out[ORDER[ch]] = c
    return out


def gradient(first, last):
    """Every channel's colour on the way from first (channel 1) to last (channel 16), evenly as the eye sees it
    over the 15 channels used without "Use channel 10"; channel 10 halfway between 9 and 11 (hunt 2026-10-12)."""
    a, b = _oklab(first), _oklab(last)
    at = [0, 1, 2, 3, 4, 5, 6, 7, 8, 8.5, 9, 10, 11, 12, 13, 14]
    n = at[-1]
    return by_channel([with_edge(first if k == 0 else last if k == n else
                                 _hex(tuple(x + (y - x) * k / n for x, y in zip(a, b)))) for k in at])


def random_colours():
    """Random colours that still look good together: hues spread round the colour wheel from a random start
    (golden-ratio steps, so neighbouring channels differ), soft like the built-in ones, a little random in strength."""
    import colorsys
    import random
    start = random.random()
    out = []
    for k in range(SLOTS):
        h = (start + k * 0.618033988749895) % 1.0
        r, g, b = colorsys.hls_to_rgb(h, random.uniform(0.62, 0.78), random.uniform(0.55, 0.85))
        out.append(with_edge("#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))))
    return by_channel(out)


def read(path):
    """(name, colours) of a palette file, None when it can't be read."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            got = json.load(f)
        name = (clean_name(str(got.get("name") or "")) or
                clean_name(os.path.splitext(os.path.basename(path))[0]))  # (no name inside: the file's)
        return (name, clean(got.get("colours"))) if name else None
    except (OSError, ValueError, AttributeError):
        return None


def user_palettes():
    """{name: (path, colours)} of the user's palettes, by name (a broken file: left out). A name already used (two
    files with one name, or one named like Default, e.g. handed over by someone) gets " (2)" added to tell it apart."""
    from files.lang import tr
    out = {}
    try:
        files = sorted(f for f in os.listdir(FOLDER) if f.lower().endswith(".json"))
    except OSError:
        return out
    used = {tr("palette.default").casefold()}
    for f in files:
        got = read(os.path.join(FOLDER, f))
        if not got:
            continue
        name, k = got[0], 2
        while name.casefold() in used:
            name, k = f"{got[0]} ({k})", k + 1
        used.add(name.casefold())
        out[name] = (os.path.join(FOLDER, f), got[1])
    return out


def names():
    """The palettes in the list's order: DEFAULT first, then the user's (by name)."""
    return [DEFAULT] + sorted(user_palettes(), key=str.casefold)


def names_newest():
    """The palettes as the Settings list shows them (user 2026-10-14): DEFAULT first, then the user's, the last
    saved / changed on top."""
    def when(item):
        try:
            return -os.path.getmtime(item[1][0])
        except OSError:
            return 0.0
    return [DEFAULT] + [n for n, _ in sorted(user_palettes().items(), key=lambda it: (when(it), it[0].casefold()))]


def colours_of(name):
    if name != DEFAULT:
        got = user_palettes().get(name)
        if got:
            return got[1]
    return list(BUILT_IN)


def picked():
    """The palette in use (DEFAULT when the saved one is gone)."""
    name = settings.get("look", "palette", DEFAULT) or DEFAULT
    return name if name == DEFAULT or name in user_palettes() else DEFAULT


def taken(name, but=None):
    """Another palette already has this name (capitals or not; the built-in one's too)."""
    from files.lang import tr
    low = name.casefold()
    return low == tr("palette.default").casefold() or any(
        n.casefold() == low for n in user_palettes() if n != but)


def _path_for(name, keep=None):
    stem, k = file_stem(name, "palette"), 2
    path = os.path.join(FOLDER, stem + ".json")
    while os.path.exists(path) and os.path.normcase(path) != os.path.normcase(keep or ""):
        path, k = os.path.join(FOLDER, f"{stem} ({k}).json"), k + 1
    return path


def save(name, colours, keep=None):
    """Writes the palette (OSError when it can't be). keep: its file now (renaming)."""
    os.makedirs(FOLDER, exist_ok=True)
    path = _path_for(name, keep)
    text = '{"name": ' + json.dumps(name) + ',\n "colours": [\n  ' + ",\n  ".join(
        json.dumps(list(p)) for p in colours) + "\n ]\n}\n"
    write_text(path, text)
    if keep and os.path.normcase(keep) != os.path.normcase(path):
        os.remove(keep)
    return path


def rename(old, new):
    path, colours = user_palettes()[old]
    in_use = picked() == old
    save(new, colours, keep=path)
    if in_use:
        settings.put("look", "palette", new)


def delete(name):
    os.remove(user_palettes()[name][0])
    if settings.get("look", "palette", DEFAULT) == name:
        settings.put("look", "palette", DEFAULT)


def apply(colours, app=None):
    """Notes are drawn with these colours from now on (every table made from them made again); app: repainted."""
    from roll import roll_draw
    from window import velocity
    roll_shared.SLOT_COLORS[:] = clean(colours)
    roll_draw.NOTE_COLORS[:] = [(roll_shared.fade(f, 1 - level * 4 / 124), b) for f, b in roll_shared.SLOT_COLORS
                                + [roll_shared.SELECTED_COLOR, roll_shared.DRAFT_COLOR]
                                for level in range(32)]
    roll_draw.NOTE_RGB = roll_draw.np.array([[[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pair]
                                             for pair in roll_draw.NOTE_COLORS], roll_draw.np.uint8)
    roll_draw._tables.clear()
    velocity.LAYERS[:] = ([(roll_shared.fade(f), roll_shared.fade(b, 0.55)) for f, b in roll_shared.SLOT_COLORS]
                          + list(roll_shared.SLOT_COLORS) + [roll_shared.SELECTED_COLOR, roll_shared.DRAFT_COLOR])
    if app is None:
        return
    roll = app.roll
    roll._note_pic = roll._carry = roll._draft_base = None
    roll.request_redraw()
    vel = getattr(app, "vel", None)
    if vel is not None:
        vel._pic = None
        vel.request_redraw()
    for name in ("hz_window", "drawer"):  # (open windows drawing with them)
        w = getattr(app, name, None)
        try:
            if w is not None and w.winfo_exists():
                w.redraw()
                if name == "hz_window":  # (its layers strip)
                    w.layers.redraw()
                else:  # (the Areas tool's colours to pick from)
                    w.draw_swatches()
        except Exception:  # (one that can't be redrawn now does at its next change)
            pass


def use(name, app=None):
    """Picks a palette: saved as the one in use (OSError when it can't be) and put on."""
    settings.put("look", "palette", name)
    apply(colours_of(name), app)


def start():
    """At start, before anything is drawn: the saved palette put on."""
    name = picked()
    if name != DEFAULT:
        apply(colours_of(name))
