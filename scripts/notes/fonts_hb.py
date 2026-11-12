"""Letter outlines on a Mac (untested: no Mac here), read from the font FILES with the joining package (uharfbuzz),
so no FreeType / fontconfig is needed. The same Font as fonts.py's Windows one and fonts_ft.py's Linux one: em
units, y up, Bezier contours ending where they start, each contour's direction kept.

Font files are looked for in the Mac's font folders (scan(): once, then kept); a family's face is picked by
italic first, then the nearest weight. Bold / slanted letters a family doesn't have are made as fontconfig makes
them on Linux (thicker by a 24th of the size / slanted 0.2). Letters the font lacks come from BACKUPS, then every
other font found. Kerning: the font's plain kerning table, as FreeType reads it on Linux.

Without uharfbuzz a Mac keeps fonts_ft (FreeType, if the Mac has it), as before.
Tests force this class on Windows / Linux: SPIDERWEB_FONTS=hb + SPIDERWEB_FONT_DIRS (folders, os.pathsep)."""
import os
import struct

MOST_BACKUPS = 1000  # (every font found: unlike fontconfig's list, the rest isn't sorted by which letters they have)
FOLDERS = ("/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
           os.path.expanduser("~/Library/Fonts"))
SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")
# where letters a font lacks are looked for first (Mac's own fonts; ones not installed are skipped; Apple Color
# Emoji left out: pictures, no outlines)
BACKUPS = ("Helvetica Neue", "Helvetica", "Lucida Grande", "Hiragino Sans", "Hiragino Kaku Gothic ProN",
           "PingFang SC", "PingFang TC", "PingFang HK", "Apple SD Gothic Neo", "Geeza Pro", "Damascus", "Al Nile",
           "Arial Hebrew", "Kohinoor Devanagari", "ITF Devanagari", "Devanagari Sangam MN", "Kohinoor Bangla",
           "Bangla Sangam MN", "Gurmukhi MN", "Gujarati Sangam MN", "Tamil Sangam MN", "Telugu Sangam MN",
           "Kannada Sangam MN", "Malayalam Sangam MN", "Sinhala Sangam MN", "Thonburi", "Ayuthaya", "Lao Sangam MN",
           "Khmer Sangam MN", "Myanmar Sangam MN", "Kailasa", "Noto Sans Javanese", "Apple Symbols",
           "STIX Two Math", "Arial Unicode MS")
DEFAULTS = ("Helvetica", "Helvetica Neue", "Arial", "Lucida Grande")
EMBOLDEN, SLANT = 1 / 24, 0.2

_faces = None    # every face found: [(English family names, weight, italic, path, index, width, weight span,
#                  other-language names)]
_by_family = {}  # family (lower) -> [its faces]


def folders():
    env = os.environ.get("SPIDERWEB_FONT_DIRS")
    return [d for d in env.split(os.pathsep) if d] if env else list(FOLDERS)


def _face_count(path):
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return 0
    if head[:4] == b"ttcf" and len(head) == 12:
        return struct.unpack(">I", head[8:12])[0]
    return 1 if len(head) >= 4 else 0


def _table(face, tag):
    try:
        return face.reference_table(tag).data or b""
    except Exception:  # noqa: BLE001 (no such table / a broken one)
        return b""


def _names(face):
    """(English family names, other ones): its typographic family first (16), then the plain one (1). A Japanese /
    Chinese font also has its own language's names: those find it too, but the font list shows the English."""
    out, other = [], []
    try:
        langs = sorted({str(lang) for nid, lang in ((int(r.name_id), r.language) for r in face.list_names())
                        if nid in (1, 16) and lang})
    except Exception:  # noqa: BLE001
        langs = []
    for lang in ["en"] + [g for g in langs if not g.startswith("en")] + [None]:
        for nid in (16, 1):
            try:
                n = face.get_name(nid, lang) if lang else face.get_name(nid)
            except Exception:  # noqa: BLE001
                n = None
            if n and n not in out and n not in other:
                (out if lang == "en" or not out else other).append(n)
    return out, other


def _style(face):
    """(weight 100-900, italic, width 1-9 (5 = normal)) from the OS/2 table (the head table's style bits when it
    has none)."""
    os2 = _table(face, "OS/2")
    if len(os2) >= 64:
        weight, width = struct.unpack_from(">HH", os2, 4)
        sel = struct.unpack_from(">H", os2, 62)[0]
        return (weight or 400), bool(sel & 0x201), (width or 5)  # (italic or oblique)
    head = _table(face, "head")
    style = struct.unpack_from(">H", head, 44)[0] if len(head) >= 46 else 0
    return (700 if style & 1 else 400), bool(style & 2), 5


def scan():
    """Every font face with outlines in the font folders, read once: [(family names, weight, italic, path,
    index, width, (lowest, highest weight: a variable font's weight dial), other-language names)]. Hidden system
    faces (names starting with ".") left out."""
    global _faces
    if _faces is not None:
        return _faces
    import uharfbuzz as hb
    _faces, seen = [], set()
    paths = []
    for folder in folders():  # (with their subfolders; a folder listed twice / inside another: once)
        for here, dirs, files in os.walk(folder):
            dirs.sort()
            paths += [os.path.join(here, n) for n in sorted(files) if n.lower().endswith(SUFFIXES)]
    for path in paths:
        if os.path.realpath(path) in seen:
            continue
        seen.add(os.path.realpath(path))
        try:
            blob = hb.Blob.from_file_path(path)
        except Exception:  # noqa: BLE001 (unreadable)
            continue
        for index in range(_face_count(path)):
            entry = _entry(hb, blob, path, index)
            if entry:
                _faces.append(entry)
    for entry in _faces:
        for n in {n.lower() for n in entry[0] + entry[7]}:
            _by_family.setdefault(n, []).append(entry)
    return _faces


def _entry(hb, blob, path, index):
    """One face's scan() entry, or None (unreadable, pictures only like Apple Color Emoji, hidden)."""
    try:
        face = hb.Face(blob, index)
        tags = set(face.table_tags)
    except Exception:  # noqa: BLE001
        return None
    if not tags & {"glyf", "CFF ", "CFF2"}:
        return None
    names, other = _names(face)
    names = [n for n in names if not n.startswith(".")]
    if not names:
        return None
    weight, italic, width = _style(face)
    span = (weight, weight)
    if "fvar" in tags:  # (a weight dial: every weight on it)
        try:
            span = next((a.min_value, a.max_value) for a in face.axis_infos if a.tag == "wght")
        except Exception:  # noqa: BLE001 (StopIteration too: no weight axis)
            pass
    return names, weight, italic, path, index, width, span, other


def families():
    """The families found (each face's family names: "Arial" and "Arial Narrow"), sorted."""
    return sorted({n for e in scan() for n in e[0]}, key=str.lower)


def default_family():
    """A plain font every Mac has (no scan needed at start)."""
    return DEFAULTS[0]


def _pick(family, weight, italic):
    """(the face entry, made bold, made slanted) for family / weight / italic; another family when it isn't there."""
    scan()
    faces = _by_family.get(family.lower())
    if not faces:
        faces = next((_by_family[d.lower()] for d in DEFAULTS if d.lower() in _by_family), None)
        if not faces and _faces:  # (none of the plain ones: a plain "Sans" family, not a mono / maths one)
            fam = min(families(), key=lambda n: ("sans" not in n.lower(),
                                                 any(w in n.lower() for w in ("mono", "math", "symbol")), len(n), n))
            faces = _by_family[fam.lower()]
        faces = faces or []
    if not faces:
        return None, False, False
    same = [e for e in faces if e[2] == bool(italic)] or faces

    def off(e):  # (how far the weight asked for is from the face's: 0 inside a weight dial's range)
        lo, hi = e[6]
        return max(lo - weight, weight - hi, 0)
    def other_name(e):  # ("Arial Narrow" is also called "Arial": its own plain name isn't the one asked for)
        return len(e[0]) > 1 and family.lower() not in (n.lower() for n in e[0][1:])
    # (normal width first; then the face named just that; then the nearest weight)
    best = min(same, key=lambda e: (abs(e[5] - 5), other_name(e), off(e),
                                    -e[6][1] if weight >= 500 else e[6][0]))
    return best, weight > 500 >= best[6][1], bool(italic) and not best[2]


class HbFont:
    """One font (family, weight, italic) read from its file. Unknown families come out as a plain Mac font;
    `face` says which one that is."""

    def __init__(self, family, weight=400, italic=False):
        import uharfbuzz as hb
        self.family, self.weight, self.italic = family, weight, italic
        self._glyphs, self._names, self._missing, self._by_index = {}, None, {}, {}
        entry, self.embolden, slanted = _pick(family, int(weight), italic)
        if entry is None:
            raise OSError("no fonts found")
        names, self._file, self._index = entry[0], entry[3], entry[4]
        self._names_here, self.face = names + entry[7], names[0]
        face = hb.Face(hb.Blob.from_file_path(self._file), self._index)
        font = hb.Font(face)
        if "fvar" in face.table_tags:  # (a font with a weight dial: the weight asked for, as shaping.py does)
            font.set_variations({"wght": float(weight)})
        if self.embolden:
            font.synthetic_bold = (EMBOLDEN, EMBOLDEN, False)
        if slanted:
            font.synthetic_slant = SLANT
        self._hf, self._upem = font, face.upem or 1000
        self._hb = (font, self._upem)  # (shaping._hb_font takes this one: same weight / bold / slant)
        cmap = _table(face, "cmap")
        subs = {struct.unpack_from(">HH", cmap, 4 + 8 * i) for i in range(struct.unpack_from(">H", cmap, 2)[0])} \
            if len(cmap) >= 4 else set()
        self.symbol = (3, 0) in subs and not subs & {(3, 1), (3, 10), (0, 3), (0, 4), (0, 6)}
        self.ascent, self.descent, self.line_height = _metrics(face, self._upem)
        self.kerning = _Kerning(self, _kern_pairs(_table(face, "kern"))) if _table(face, "kern") else {}
        h = self._read("H", backup=False)[1]  # (its own H: a font without letters has no cap height)
        self.cap = (max(y for c in h for _, y in c) if h else 0) or 0.7

    @property
    def found(self):
        want = self.family.lower()
        return any(n.lower() == want for n in self._names_here)

    def index(self, ch):
        """The font's glyph number for ch (0 = it has none)."""
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF:
            return 0
        gi = self._hf.get_nominal_glyph(o)
        if not gi and self.symbol and o < 0x100:  # (symbol fonts keep their letters at F000-F0FF)
            gi = self._hf.get_nominal_glyph(0xF000 + o)
        return gi or 0

    def has(self, ch):
        return self.index(ch) != 0

    def glyph(self, ch):
        """(advance, contours) of one character; contours = [Bezier point lists]."""
        g = self._glyphs.get(ch)
        if g is None:
            g = self._glyphs[ch] = self._read(ch)
        return g

    def backup_names(self):
        """The fonts a letter this one lacks is looked for in: BACKUPS that are here, then the other fonts found."""
        if self._names is None:
            seen = {n.lower() for n in self._names_here} | {self.family.lower()}
            self._names = []
            for name in list(BACKUPS) + families():
                if name.lower() not in seen and name.lower() in _by_family:
                    seen.add(name.lower())
                    self._names.append(name)
                    if len(self._names) >= MOST_BACKUPS:
                        break
        return self._names

    def missing(self, ch):
        from notes.fonts import no_width, backup_font
        if ch not in self._missing:
            self._missing[ch] = not (ch.isspace() or no_width(ch) or self.symbol or self.has(ch)
                                     or backup_font(self, ch))
        return self._missing[ch]

    def _read(self, ch, backup=True):
        from notes.fonts import no_width, backup_font
        if no_width(ch):
            return 0.0, []
        if backup and not self.symbol and not self.has(ch):
            other = backup_font(self, ch)
            if other is not None:
                return other.glyph(ch)
        return self._outline(self.index(ch))

    def glyph_at(self, gid):
        """The contours of the font's own glyph number gid (joined letters: shaping.py), kept for next time."""
        g = self._by_index.get(gid)
        if g is None:
            g = self._by_index[gid] = self._outline(gid)[1]
        return g

    def hb_blob(self):
        """(font file bytes, which font in it) for the joining package (shaping.py uses self._hb instead)."""
        try:
            with open(self._file, "rb") as f:
                return f.read(), self._index
        except (OSError, TypeError):
            return b"", 0

    def _outline(self, gid):
        pen = _Pen(1 / self._upem)
        try:
            self._hf.draw_glyph_with_pen(gid, pen)
            advance = self._hf.get_glyph_h_advance(gid) / self._upem
        except Exception:  # noqa: BLE001 (a broken glyph: nothing drawn)
            return 0.0, []
        return advance, pen.done()

    def close(self):
        pass  # (the file is mapped by the package and let go with the font)


class _Pen:
    """The package's drawing -> contours as Bezier point lists [[x, y], ...] in em units (quadratic pieces made
    cubic, straight ones with handles a third of the way along), like fonts_ft._contours."""

    def __init__(self, scale):
        self.s, self.contours = scale, []

    def _pt(self, p):
        return [p[0] * self.s, p[1] * self.s]

    def _line(self, p):
        x0, y0 = self.contours[-1][-1]
        self.contours[-1].extend([[x0 + (p[0] - x0) / 3, y0 + (p[1] - y0) / 3],
                                  [x0 + (p[0] - x0) * 2 / 3, y0 + (p[1] - y0) * 2 / 3], p])

    def _close(self):
        if self.contours and len(self.contours[-1]) > 1 and self.contours[-1][-1] != self.contours[-1][0]:
            self._line(self.contours[-1][0])

    def moveTo(self, p):  # noqa: N802 (the package's pen names)
        self._close()
        self.contours.append([self._pt(p)])

    def lineTo(self, p):  # noqa: N802
        self._line(self._pt(p))

    def qCurveTo(self, *ps):  # noqa: N802 (off-curve points; between two of them an implied on-curve point)
        ps = [self._pt(p) for p in ps]
        for k, c in enumerate(ps[:-1]):
            to = ps[k + 1] if k + 1 == len(ps) - 1 else [(c[0] + ps[k + 1][0]) / 2, (c[1] + ps[k + 1][1]) / 2]
            x0, y0 = self.contours[-1][-1]
            self.contours[-1].extend([[x0 + (c[0] - x0) * 2 / 3, y0 + (c[1] - y0) * 2 / 3],
                                      [to[0] + (c[0] - to[0]) * 2 / 3, to[1] + (c[1] - to[1]) * 2 / 3], to])

    def curveTo(self, *ps):  # noqa: N802
        self.contours[-1].extend(self._pt(p) for p in ps)

    def closePath(self):  # noqa: N802
        self._close()

    endPath = closePath  # noqa: N815

    def done(self):
        """The contours; one-point ones left out (anchor dots some fonts keep: Windows / FreeType skip them)."""
        self._close()
        return [c for c in self.contours if len(c) >= 4 and any(p != c[0] for p in c)]


def _metrics(face, upem):
    """(ascent, descent, baseline to baseline) as FreeType reads them: the hhea table, else OS/2's."""
    hhea, os2 = _table(face, "hhea"), _table(face, "OS/2")
    asc, desc, gap = struct.unpack_from(">hhh", hhea, 4) if len(hhea) >= 10 else (0, 0, 0)
    height = asc - desc + gap
    if not asc and not desc and len(os2) >= 78:
        asc, desc, gap = struct.unpack_from(">hhh", os2, 68)
        height = asc - desc + gap
        if not asc and not desc:
            win_asc, win_desc = struct.unpack_from(">HH", os2, 74)
            asc, desc, height = win_asc, -win_desc, win_asc + win_desc
    return asc / upem, -desc / upem, height / upem


def _kern_pairs(raw):
    """The plain kerning table (version 0, horizontal format 0 parts, as FreeType reads): {(glyph, glyph): units}."""
    pairs = {}
    try:
        if struct.unpack_from(">H", raw, 0)[0] != 0:
            return pairs
        pos = 4
        for _ in range(struct.unpack_from(">H", raw, 2)[0]):
            _, length, cov = struct.unpack_from(">HHH", raw, pos)
            if cov >> 8 == 0:  # (format 0: its pair count gives its size; big tables overflow the length field)
                n = struct.unpack_from(">H", raw, pos + 6)[0]
                if cov & 7 == 1:  # (horizontal, not "minimum" / across)
                    for k in range(n):
                        a, b, v = struct.unpack_from(">HHh", raw, pos + 14 + 6 * k)
                        pairs.setdefault((a, b), v)
                length = 14 + 6 * n
            if not length:
                break
            pos += length
    except struct.error:  # (a broken table: what was read so far)
        pass
    return pairs


class _Kerning:
    """font.kerning.get((a, b), 0.0) as the Windows dict does."""

    def __init__(self, font, pairs):
        self.font, self.pairs, self._seen = font, pairs, {}

    def get(self, pair, default=0.0):
        k = self._seen.get(pair)
        if k is None:
            a, b = self.font.index(pair[0]), self.font.index(pair[1])
            k = self.pairs.get((a, b), 0) / self.font._upem if a and b else 0.0
            self._seen[pair] = k
        return k or default
