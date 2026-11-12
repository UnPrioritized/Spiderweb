"""Joined letters + right-to-left order for the text tool (Arabic, Persian, Urdu, Hebrew; Hindi, Thai... joined).
Two optional packages: uharfbuzz (joins letters, reads font files) and python-bidi (Unicode's right-to-left rules).
Without them the text tool draws as before: letters one by one, left to right (available() is False).
Loaded only when first asked for (never at start).

Only lines that need it go through here (needs_shaping): a line of Latin / Chinese / Japanese / emoji keeps the
text tool's own letter-by-letter layout, exactly as before."""
import bisect
import re
import unicodedata

_state = {}

# Scripts whose letters join, reorder or stack (each its own run for the joining package: it guesses the script
# from the run). (first, last, name); right-to-left ones are found by their direction too.
_SCRIPTS = sorted([
    (0x0590, 0x05FF, "Hebr"), (0xFB1D, 0xFB4F, "Hebr"), (0x0600, 0x06FF, "Arab"), (0x0750, 0x077F, "Arab"),
    (0x0870, 0x08FF, "Arab"), (0xFB50, 0xFDFF, "Arab"), (0xFE70, 0xFEFF, "Arab"), (0x0700, 0x074F, "Syrc"),
    (0x0860, 0x086F, "Syrc"), (0x0780, 0x07BF, "Thaa"), (0x07C0, 0x07FF, "Nkoo"), (0x0800, 0x083F, "Samr"),
    (0x0840, 0x085F, "Mand"), (0x0900, 0x097F, "Deva"), (0xA8E0, 0xA8FF, "Deva"), (0x0980, 0x09FF, "Beng"),
    (0x0A00, 0x0A7F, "Guru"), (0x0A80, 0x0AFF, "Gujr"), (0x0B00, 0x0B7F, "Orya"), (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"), (0x0C80, 0x0CFF, "Knda"), (0x0D00, 0x0D7F, "Mlym"), (0x0D80, 0x0DFF, "Sinh"),
    (0x0E00, 0x0E7F, "Thai"), (0x0E80, 0x0EFF, "Laoo"), (0x0F00, 0x0FFF, "Tibt"), (0x1000, 0x109F, "Mymr"),
    (0xA9E0, 0xA9FF, "Mymr"), (0xAA60, 0xAA7F, "Mymr"), (0x1780, 0x17FF, "Khmr"), (0x19E0, 0x19FF, "Khmr"),
    (0x1800, 0x18AF, "Mong"), (0x1900, 0x194F, "Limb"), (0x1950, 0x197F, "Tale"), (0x1980, 0x19DF, "Talu"),
    (0x1A00, 0x1A1F, "Bugi"), (0x1A20, 0x1AAF, "Lana"), (0x1B00, 0x1B7F, "Bali"), (0x1B80, 0x1BBF, "Sund"),
    (0x1BC0, 0x1BFF, "Batk"), (0x1C00, 0x1C4F, "Lepc"), (0xA800, 0xA82F, "Sylo"), (0xA840, 0xA87F, "Phag"),
    (0xA880, 0xA8DF, "Saur"), (0xA900, 0xA92F, "Kali"), (0xA930, 0xA95F, "Rjng"), (0xA980, 0xA9DF, "Java"),
    (0xAA00, 0xAA5F, "Cham"), (0xAA80, 0xAADF, "Tavt"), (0xAAE0, 0xAAFF, "Mtei"), (0xABC0, 0xABFF, "Mtei"),
    (0x10A00, 0x10A5F, "Khar"), (0x11000, 0x11FFF, "Brah"), (0x1E900, 0x1E95F, "Adlm")])
_FIRSTS = [s[0] for s in _SCRIPTS]
_RTL = {"R", "AL", "AN"}
# Joined pieces (Letter turn / Flip turn each on its own spot, user 2026-11-12): letters of these scripts join when
# the joining package marks them as not breakable apart; headline scripts' whole words hang on one line; any other
# script: a letter group (cluster) each.
_CURSIVE = {"Arab", "Syrc", "Nkoo", "Mong", "Mand", "Adlm"}
_HEADLINE = {"Deva", "Beng", "Guru"}


def _load():
    if "ok" not in _state:
        try:
            import uharfbuzz
            from bidi.bidi import get_display_inner
            _state.update(ok=True, hb=uharfbuzz, display=get_display_inner)
        except Exception:  # (not installed / broken copy: today's drawing)
            _state["ok"] = False
    return _state["ok"]


def available():
    """Both packages load here."""
    return _load()


_LEVEL = re.compile(r"Level\(\s*(\d+),?\s*\)")


def levels(text, base=None):
    """Each letter's Unicode direction level (even = left to right, odd = right to left), from python-bidi's own
    Rust rules (current Unicode, bracket pairs included). base = "L" / "R" forces the line's direction, None = from
    its first strong letter. Its Rust part only shows levels in its debug text (one per UTF-8 byte): read from
    there; if that text ever changes shape, the package's older Python rules give them (no bracket pairs)."""
    if not text or not _load():
        return [0] * len(text)
    out = rust_levels(text, base)
    return out if out is not None else _python_levels(text, base)


def rust_levels(text, base=None):
    """levels() read from python-bidi's debug text, or None when that text's shape changed (test bidi_levels)."""
    try:
        info = _state["display"](text, base, True)
        part = info[info.index("levels:"):info.index("paragraphs:")]
        per_byte = [int(n) for n in _LEVEL.findall(part)]
        out, at = [], 0
        for ch in text:
            out.append(per_byte[at])
            at += len(ch.encode("utf-8"))
        return out if at == len(per_byte) else None
    except (ValueError, IndexError, TypeError):
        return None


def _python_levels(text, base):
    from bidi import algorithm as alg
    st = alg.get_empty_storage()
    lv = alg.get_base_level(text) if base is None else alg.PARAGRAPH_LEVELS[base]
    st["base_level"], st["base_dir"] = lv, ("L", "R")[lv]
    alg.get_embedding_levels(text, st)
    for step in (alg.explicit_embed_and_overrides, alg.resolve_weak_types, alg.resolve_neutral_types,
                 alg.resolve_implicit_levels):
        step(st, False)
    return [c["level"] for c in st["chars"]]


def visual_order(lv):
    """Unicode rule L2: typed-order indexes in the order they show, left to right (reverse every run at or above
    each level, from the highest down to the lowest odd one)."""
    order = list(range(len(lv)))
    odd = [n for n in lv if n % 2]
    if not odd:
        return order
    for level in range(max(lv), min(odd) - 1, -1):
        i = 0
        while i < len(order):
            if lv[order[i]] < level:
                i += 1
                continue
            j = i
            while j < len(order) and lv[order[j]] >= level:
                j += 1
            order[i:j] = order[i:j][::-1]
            i = j
    return order


def script_of(ch):
    """The joining / reordering script ch belongs to (_SCRIPTS' name), or None."""
    o = ord(ch)
    i = bisect.bisect_right(_FIRSTS, o) - 1
    return _SCRIPTS[i][2] if i >= 0 and o <= _SCRIPTS[i][1] else None


def needs_shaping(line):
    """True if the line has a right-to-left letter or one of a script whose letters join / reorder / stack. Only
    such lines are laid out here, and only when both packages load."""
    return any(unicodedata.bidirectional(ch) in _RTL or script_of(ch) for ch in line)


def caret_stops(line):
    """Where the caret may stop in a joined line (0 .. len), as Windows' own text boxes do (measured with a RichEdit
    box, dev/scratch/rtl_richedit.py): never before a mark (a base letter + its marks = one step), nor inside a
    conjunct (a virama joins the next letter of its script: Hindi "स्ते" = one step), an emoji skin tone or a
    joiner (ZWJ) sequence. Joined shapes of whole letters (lam-alef) keep a stop between their letters."""
    out = [0]
    for i in range(1, len(line)):
        ch, before = line[i], line[i - 1]
        if (unicodedata.category(ch)[0] == "M" or ch == "‍" or before == "‍"
                or 0x1F3FB <= ord(ch) <= 0x1F3FF
                or (unicodedata.combining(before) == 9 and unicodedata.category(ch) == "Lo"
                    and script_of(ch) is not None and script_of(ch) == script_of(before))):
            continue
        out.append(i)
    if line:
        out.append(len(line))
    return out


def line_rtl(line):
    """The line's own direction is right to left (its first strong letter is), like Unicode's paragraph rule."""
    for ch in line:
        b = unicodedata.bidirectional(ch)
        if b == "L":
            return False
        if b in ("R", "AL"):
            return True
    return False


def _hb_font(font):
    """The joining package's font for a text-tool Font (kept on it), or None when its file can't be read."""
    hf = getattr(font, "_hb", 0)
    if hf == 0:
        hf = None
        try:
            blob, index = font.hb_blob()
            if blob:
                hb = _state["hb"]
                face = hb.Face(blob, index)
                hf = hb.Font(face)
                if "fvar" in face.table_tags:  # (a font with a weight dial: the weight asked for)
                    hf.set_variations({"wght": float(font.weight)})
                hf = (hf, face.upem)
        except Exception:  # (a font the package can't read: letter by letter, as before)
            hf = None
        font._hb = hf
    return hf


def _kind(ch):
    """Which run a character's script puts it in: a script name, "L" (any other letter), None (marks, spaces,
    numbers, signs: go with the letters around them)."""
    s = script_of(ch)
    if s:
        return s
    if unicodedata.category(ch)[0] == "M":
        return None
    return "L" if unicodedata.bidirectional(ch) in ("L", "R", "AL") else None


def shape_line(font, line, track=0.0, context=("", "")):
    """One line laid out with joined letters, in em units from x = 0 (left) to "width":
    {"glyphs": [(x, advance, contours, cluster)] left to right, "cells": [(x0, x1, rtl)] one per character in
    typed order (the room it takes; characters joined into one shape share its room evenly, in reading order),
    "width", "rtl" (the line's own direction is right to left), "pieces": each glyph's joined piece (numbers going
    left to right), "src": each glyph's (font, glyph number or None, dx, dy) (Split into letters keeps them)}.
    Each character's font: the line's font, or the backup font that has it (fonts.backup_font); marks follow the
    letter before them. A run = characters with the same direction level, font and script, shaped together
    (with the whole line around it, so letters join across a font change). Tracking goes between letter shapes,
    but not inside a run of one joining script (it would cut the joins). context = (letters before, after) the line
    that its letters join to without being drawn (a split letter group in another font)."""
    from notes.fonts import backup_font, no_width
    n = len(line)
    base = "R" if line_rtl(line) else "L"
    lv = levels(line, base)
    fonts, kinds = [], []
    for i, ch in enumerate(line):
        if i and (unicodedata.category(ch)[0] == "M" or no_width(ch)):
            f = fonts[-1]
        elif font.symbol or ch.isspace() or no_width(ch) or font.has(ch):
            f = font
        else:
            f = backup_font(font, ch) or font
        fonts.append(f)
        kinds.append(_kind(ch))
    first = next((k for k in kinds if k), "L")
    for i in range(n):  # (marks / spaces / numbers: the kind before them; the first ones the first kind after)
        kinds[i] = kinds[i] or (kinds[i - 1] if i else first)
    runs, start = [], 0
    for i in range(1, n + 1):
        if i == n or (lv[i], fonts[i], kinds[i]) != (lv[start], fonts[start], kinds[start]):
            runs.append((start, i))
            start = i
    run_of = [0] * n
    for r, (a, b) in enumerate(runs):
        run_of[a:b] = [r] * (b - a)
    shown = []
    for i in visual_order(lv):
        if not shown or shown[-1] != run_of[i]:
            shown.append(run_of[i])
    hb = _state["hb"]
    pre, post = context
    cps = [ord(ch) for ch in pre + line + post]
    off = len(pre)
    glyphs, x, prev = [], 0.0, None  # (prev = the last shape's character: tracking between clusters)
    src, joins, run_at = [], [], []  # (per glyph: where it came from, joined to the glyph on its left, its run)
    for r in shown:
        a, b = runs[r]
        f, rtl = fonts[a], lv[a] % 2 == 1
        hf = _hb_font(f)
        if hf is None:  # (letter by letter, in the order they show)
            items = [(i, f.glyph(line[i])[0], f.glyph(line[i])[1], 0.0, 0.0, None, 0) for i in
                     (range(b - 1, a - 1, -1) if rtl else range(a, b))]
        else:
            buf = hb.Buffer()
            buf.add_codepoints(cps, a + off, b - a)
            buf.guess_segment_properties()
            buf.direction = "rtl" if rtl else "ltr"
            hb.shape(hf[0], buf)
            u = hf[1]
            items = [(info.cluster - off, pos.x_advance / u, f.glyph_at(info.codepoint), pos.x_offset / u,
                      pos.y_offset / u, info.codepoint, int(info.flags) & 1)
                     for info, pos in zip(buf.glyph_infos, buf.glyph_positions)]
        flags = [it[6] for it in items]
        for k, (c, adv, conts, dx, dy, gid, _) in enumerate(items):
            if prev is not None and c != prev and not (kinds[c] == kinds[prev] != "L"):
                x += track
            joined = False
            if k and c == items[k - 1][0]:
                joined = True  # (one letter group: always together)
            elif k and kinds[c] in _CURSIVE:  # (the package marks a glyph whose letter group joins the one typed
                joined = bool(flags[k - 1] if rtl else flags[k])  # before it: on its right in a right-to-left run)
            elif k and kinds[c] in _HEADLINE:  # (a word on its headline: letters up to a space / sign)
                joined = _word(line[c]) and _word(line[items[k - 1][0]])
            src.append((f, gid, dx, dy))
            joins.append(joined)
            run_at.append(r)
            if dx or dy:
                conts = [[[px + dx, py + dy] for px, py in cont] for cont in conts]
            glyphs.append((x, adv, conts, c))
            x += adv
            prev = c
    pieces, p = [], -1
    for k in range(len(glyphs)):
        if not (k and joins[k] and run_at[k] == run_at[k - 1]):
            p += 1
        pieces.append(p)
    room = {}  # (each cluster's room, shared by its characters)
    for gx, adv, _, c in glyphs:
        lo, hi = room.get(c, (gx, gx))
        room[c] = (min(lo, gx), max(hi, gx + adv))
    starts = sorted(room)
    cells = [None] * n
    for k, c in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else n
        x0, x1 = room[c]
        m = end - c
        for j in range(m):
            p0, p1 = x0 + (x1 - x0) * j / m, x0 + (x1 - x0) * (j + 1) / m
            cells[c + j] = (x0 + x1 - p1, x0 + x1 - p0, True) if lv[c] % 2 else (p0, p1, False)
    for i in range(n):  # (none left: every character is in a cluster from 0 on; kept safe anyway)
        if cells[i] is None:
            cells[i] = (0.0, 0.0, lv[i] % 2 == 1)
    return {"glyphs": glyphs, "cells": cells, "width": x, "rtl": base == "R", "pieces": pieces, "src": src}


def _word(ch):
    """A letter or mark of a word (not a space, number or sign)."""
    return unicodedata.category(ch)[0] in "LM"


def cluster_parts(sl, n):
    """A shaped line's letter groups: [(first character, end, [its glyph numbers in sl["glyphs"]])] in typed order;
    n = the line's length."""
    by = {}
    for k, g in enumerate(sl["glyphs"]):
        by.setdefault(g[3], []).append(k)
    starts = sorted(by)
    return [(c, starts[i + 1] if i + 1 < len(starts) else n, by[c]) for i, c in enumerate(starts)]


def piece_of(sl, line_len):
    """Each character's joined piece (its letter group's), for a shaped line."""
    out = [0] * line_len
    for c, e, ks in cluster_parts(sl, line_len):
        out[c:e] = [sl["pieces"][ks[0]]] * (e - c)
    return out


def keep_form(font, line, sl, c, e, ks):
    """What a letter group split out of a joined line keeps (Split into letters, user: its joined shape): the glyphs
    it is made of, as {"of": its characters, "font": the text's font settings, "glyphs": [[font family, glyph
    number, x from the group's left, dx, dy, advance]], "cells": its characters' rooms from its left, "width",
    "rtl"}. None when a glyph has no glyph number (a font the joining package couldn't read)."""
    x0 = min(sl["glyphs"][k][0] for k in ks)
    x1 = max(sl["glyphs"][k][0] + sl["glyphs"][k][1] for k in ks)
    gl = []
    for k in ks:
        f, gid, dx, dy = sl["src"][k]
        if gid is None:
            return None
        gx, adv, _, _ = sl["glyphs"][k]
        gl.append([f.family, int(gid), gx - x0, dx, dy, adv])
    cells = [[a - x0, b - x0, bool(r)] for a, b, r in sl["cells"][c:e]]
    return {"of": line[c:e], "font": [font.family, int(font.weight), bool(font.italic)], "glyphs": gl,
            "cells": cells, "width": x1 - x0, "rtl": bool(sl["cells"][c][2]),
            "pre": line[max(0, c - 5):c], "post": line[e:e + 5]}  # (the joining package looks 5 letters around)


def kept_line(font, form):
    """shape_line's answer for a split letter group from its kept glyphs (keep_form): the same glyphs, so it keeps
    the joined shape it had in its word. None when the text's font settings changed since (then it's joined again
    from its own letters)."""
    from notes.fonts import get_font
    if [font.family, int(font.weight), bool(font.italic)] != form["font"]:
        return None
    glyphs, src = [], []
    for fam, gid, gx, dx, dy, adv in form["glyphs"]:
        f = font if fam == font.family else get_font(fam, font.weight, font.italic)
        conts = f.glyph_at(gid)
        if dx or dy:
            conts = [[[px + dx, py + dy] for px, py in cont] for cont in conts]
        glyphs.append((gx, adv, conts, 0))
        src.append((f, gid, dx, dy))
    return {"glyphs": glyphs, "cells": [tuple(c) for c in form["cells"]], "width": form["width"],
            "rtl": form["rtl"], "pieces": [0] * len(glyphs), "src": src}
