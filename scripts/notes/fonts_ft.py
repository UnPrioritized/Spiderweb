"""Letter outlines on Linux (untested until a Linux user reports): fontconfig finds the font file (the way the
desktop's own programs do), FreeType reads its letters, both through ctypes (Tk itself needs both, so they're
there). The same Font as fonts.py's Windows one: em units, y up, Bezier contours ending where they start, each
contour's direction kept. Bold / slanted letters a family doesn't have are made as fontconfig says (thicker /
slanted outlines), like Windows makes them. Letters the font lacks come from the fonts fontconfig lists after it."""

import ctypes
import ctypes.util
from ctypes import POINTER, byref, c_char_p, c_double, c_int, c_long, c_short, c_ubyte, c_uint, c_ushort, c_void_p

EM = 2048  # the font is asked for at this many pixels per em (fine enough for any size)
MOST_BACKUPS = 60  # fonts looked through for a letter the font lacks


def _tag(s):
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


ENC_UNICODE, ENC_SYMBOL = _tag("unic"), _tag("symb")
LOAD_NO_HINTING, LOAD_NO_BITMAP = 0x2, 0x8
HAS_KERNING = 1 << 6
KERNING_UNSCALED = 2


class _Vector(ctypes.Structure):
    _fields_ = [("x", c_long), ("y", c_long)]


class _Generic(ctypes.Structure):
    _fields_ = [("data", c_void_p), ("finalizer", c_void_p)]


class _BBox(ctypes.Structure):
    _fields_ = [("xMin", c_long), ("yMin", c_long), ("xMax", c_long), ("yMax", c_long)]


class _Metrics(ctypes.Structure):
    _fields_ = [(n, c_long) for n in ("width", "height", "horiBearingX", "horiBearingY", "horiAdvance",
                                      "vertBearingX", "vertBearingY", "vertAdvance")]


class _Bitmap(ctypes.Structure):
    _fields_ = [("rows", c_uint), ("width", c_uint), ("pitch", c_int), ("buffer", c_void_p),
                ("num_grays", c_ushort), ("pixel_mode", c_ubyte), ("palette_mode", c_ubyte), ("palette", c_void_p)]


class _Outline(ctypes.Structure):
    _fields_ = [("n_contours", c_short), ("n_points", c_short), ("points", c_void_p), ("tags", c_void_p),
                ("contours", c_void_p), ("flags", c_int)]


class _Slot(ctypes.Structure):  # FT_GlyphSlotRec, as far as the outline
    _fields_ = [("library", c_void_p), ("face", c_void_p), ("next", c_void_p), ("glyph_index", c_uint),
                ("generic", _Generic), ("metrics", _Metrics), ("linearHoriAdvance", c_long),
                ("linearVertAdvance", c_long), ("advance", _Vector), ("format", c_uint), ("bitmap", _Bitmap),
                ("bitmap_left", c_int), ("bitmap_top", c_int), ("outline", _Outline)]


class _CharMap(ctypes.Structure):
    _fields_ = [("face", c_void_p), ("encoding", c_uint), ("platform_id", c_ushort), ("encoding_id", c_ushort)]


class _Face(ctypes.Structure):  # FT_FaceRec, as far as the glyph slot
    _fields_ = [("num_faces", c_long), ("face_index", c_long), ("face_flags", c_long), ("style_flags", c_long),
                ("num_glyphs", c_long), ("family_name", c_char_p), ("style_name", c_char_p),
                ("num_fixed_sizes", c_int), ("available_sizes", c_void_p), ("num_charmaps", c_int),
                ("charmaps", POINTER(POINTER(_CharMap))), ("generic", _Generic), ("bbox", _BBox),
                ("units_per_EM", c_ushort), ("ascender", c_short), ("descender", c_short), ("height", c_short),
                ("max_advance_width", c_short), ("max_advance_height", c_short),
                ("underline_position", c_short), ("underline_thickness", c_short), ("glyph", POINTER(_Slot))]


class _Matrix(ctypes.Structure):  # FT_Matrix (16.16 numbers)
    _fields_ = [("xx", c_long), ("xy", c_long), ("yx", c_long), ("yy", c_long)]


class _FcMatrix(ctypes.Structure):
    _fields_ = [("xx", c_double), ("xy", c_double), ("yx", c_double), ("yy", c_double)]


class _FcFontSet(ctypes.Structure):
    _fields_ = [("nfont", c_int), ("sfont", c_int), ("fonts", POINTER(c_void_p))]


_MOVE = ctypes.CFUNCTYPE(c_int, POINTER(_Vector), c_void_p)
_CONIC = ctypes.CFUNCTYPE(c_int, POINTER(_Vector), POINTER(_Vector), c_void_p)
_CUBIC = ctypes.CFUNCTYPE(c_int, POINTER(_Vector), POINTER(_Vector), POINTER(_Vector), c_void_p)


class _Funcs(ctypes.Structure):
    _fields_ = [("move_to", _MOVE), ("line_to", _MOVE), ("conic_to", _CONIC), ("cubic_to", _CUBIC),
                ("shift", c_int), ("delta", c_long)]


_libs = None


def _load():
    """(freetype, fontconfig, FT_Library), loaded once. OSError when they can't be."""
    global _libs
    if _libs:
        return _libs
    ft = ctypes.CDLL(ctypes.util.find_library("freetype") or "libfreetype.so.6")
    fc = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
    ft.FT_New_Face.argtypes = [c_void_p, c_char_p, c_long, POINTER(POINTER(_Face))]
    ft.FT_Done_Face.argtypes = [POINTER(_Face)]
    ft.FT_Set_Char_Size.argtypes = [POINTER(_Face), c_long, c_long, c_uint, c_uint]
    ft.FT_Select_Charmap.argtypes = [POINTER(_Face), c_uint]
    ft.FT_Get_Char_Index.argtypes = [POINTER(_Face), ctypes.c_ulong]
    ft.FT_Get_Char_Index.restype = c_uint
    ft.FT_Load_Glyph.argtypes = [POINTER(_Face), c_uint, ctypes.c_int32]
    ft.FT_Get_Kerning.argtypes = [POINTER(_Face), c_uint, c_uint, c_uint, POINTER(_Vector)]
    ft.FT_Outline_Decompose.argtypes = [POINTER(_Outline), POINTER(_Funcs), c_void_p]
    ft.FT_Outline_Transform.argtypes = [POINTER(_Outline), POINTER(_Matrix)]
    ft.FT_Outline_EmboldenXY.argtypes = [POINTER(_Outline), c_long, c_long]
    p = c_void_p
    for name, args, res in (("FcInit", [], c_int), ("FcPatternCreate", [], p), ("FcPatternDestroy", [p], None),
                            ("FcPatternAddString", [p, c_char_p, c_char_p], c_int),
                            ("FcPatternAddInteger", [p, c_char_p, c_int], c_int),
                            ("FcPatternAddBool", [p, c_char_p, c_int], c_int),
                            ("FcPatternGetString", [p, c_char_p, c_int, POINTER(c_char_p)], c_int),
                            ("FcPatternGetInteger", [p, c_char_p, c_int, POINTER(c_int)], c_int),
                            ("FcPatternGetBool", [p, c_char_p, c_int, POINTER(c_int)], c_int),
                            ("FcPatternGetMatrix", [p, c_char_p, c_int, POINTER(POINTER(_FcMatrix))], c_int),
                            ("FcConfigSubstitute", [p, p, c_int], c_int), ("FcDefaultSubstitute", [p], None),
                            ("FcFontMatch", [p, p, POINTER(c_int)], p),
                            ("FcFontSort", [p, p, c_int, p, POINTER(c_int)], POINTER(_FcFontSet)),
                            ("FcFontSetDestroy", [POINTER(_FcFontSet)], None),
                            ("FcWeightFromOpenType", [c_int], c_int)):
        fn = getattr(fc, name)
        fn.argtypes, fn.restype = args, res
    fc.FcInit()
    lib = c_void_p()
    if ft.FT_Init_FreeType(byref(lib)):
        raise OSError("FreeType couldn't start")
    _libs = ft, fc, lib
    return _libs


def _pattern(fc, family, weight=400, italic=False):
    """A fontconfig pattern asking for this family / weight (100-900) / italic, filled in like any program does."""
    pat = fc.FcPatternCreate()
    fc.FcPatternAddString(pat, b"family", family.encode("utf-8"))
    fc.FcPatternAddInteger(pat, b"weight", fc.FcWeightFromOpenType(int(weight)))
    fc.FcPatternAddInteger(pat, b"slant", 100 if italic else 0)  # (FC_SLANT_ITALIC / ROMAN)
    fc.FcPatternAddBool(pat, b"scalable", 1)  # (outlines only, no bitmap fonts)
    fc.FcConfigSubstitute(None, pat, 0)  # (FcMatchPattern)
    fc.FcDefaultSubstitute(pat)
    return pat


def _strings(fc, pat, what):
    out, i, s = [], 0, c_char_p()
    while fc.FcPatternGetString(pat, what, i, byref(s)) == 0:
        out.append(s.value.decode("utf-8", "replace"))
        i += 1
    return out


def default_family():
    """The desktop's own plain font (fontconfig's "sans-serif"), or "DejaVu Sans" when it can't say."""
    try:
        _, fc, _ = _load()
        pat = _pattern(fc, "sans-serif")
        res = c_int()
        m = fc.FcFontMatch(None, pat, byref(res))
        fc.FcPatternDestroy(pat)
        if m:
            names = _strings(fc, m, b"family")
            fc.FcPatternDestroy(m)
            if names:
                return names[0]
    except OSError:
        pass
    return "DejaVu Sans"


class FtFont:
    """One font (family, weight, italic) found by fontconfig. Unknown families come out as the font fontconfig picks
    instead; `face` says which one that is."""

    def __init__(self, family, weight=400, italic=False):
        self.family, self.weight, self.italic = family, weight, italic
        self._glyphs, self._names, self._missing = {}, None, {}
        self._ft, fc, lib = _load()
        pat = _pattern(fc, family, weight, italic)
        res = c_int()
        m = fc.FcFontMatch(None, pat, byref(res))
        fc.FcPatternDestroy(pat)
        if not m:
            raise OSError(f"no font for {family!r}")
        try:
            path, index, emb, mat = c_char_p(), c_int(), c_int(), POINTER(_FcMatrix)()
            fc.FcPatternGetString(m, b"file", 0, byref(path))
            fc.FcPatternGetInteger(m, b"index", 0, byref(index))
            self._names_here = _strings(fc, m, b"family")  # (all its names: a family can have several)
            self.embolden = fc.FcPatternGetBool(m, b"embolden", 0, byref(emb)) == 0 and bool(emb.value)
            self.slant = None  # (a slant fontconfig asks for: made by hand, as for a missing italic)
            if fc.FcPatternGetMatrix(m, b"matrix", 0, byref(mat)) == 0 and mat:
                x = mat.contents
                if (x.xx, x.xy, x.yx, x.yy) != (1, 0, 0, 1):
                    self.slant = _Matrix(*(int(round(v * 65536)) for v in (x.xx, x.xy, x.yx, x.yy)))
            file = path.value
        finally:
            fc.FcPatternDestroy(m)
        self.face = self._names_here[0] if self._names_here else family
        self._face = POINTER(_Face)()
        if self._ft.FT_New_Face(lib, file, index.value, byref(self._face)):
            raise OSError(f"FreeType couldn't open {file!r}")
        ft, f = self._ft, self._face
        self.symbol = False
        if ft.FT_Select_Charmap(f, ENC_UNICODE):  # (no Unicode letter list: a symbol font's own, if any)
            self.symbol = not ft.FT_Select_Charmap(f, ENC_SYMBOL)
        ft.FT_Set_Char_Size(f, 0, EM * 64, 72, 72)
        rec = f.contents
        em = rec.units_per_EM or EM
        self.ascent, self.descent = rec.ascender / em, -rec.descender / em
        self.line_height = rec.height / em  # baseline to baseline
        self.kerning = _Kerning(self) if rec.face_flags & HAS_KERNING else {}
        h = self._read("H", backup=False)[1]  # (its own H: a font without letters, e.g. emoji, has no cap height)
        self.cap = (max(y for c in h for _, y in c) if h else 0) or 0.7  # capital letter height

    @property
    def found(self):
        want = self.family.lower()
        return any(n.lower() == want for n in self._names_here)

    def index(self, ch):
        """The font's glyph number for ch (0 = it has none)."""
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF or not self._face:
            return 0
        gi = self._ft.FT_Get_Char_Index(self._face, o)
        if not gi and self.symbol and o < 0x100:  # (symbol fonts keep their letters at F000-F0FF)
            gi = self._ft.FT_Get_Char_Index(self._face, 0xF000 + o)
        return gi

    def has(self, ch):
        """True if this font has its own letter for ch."""
        return self.index(ch) != 0

    def glyph(self, ch):
        """(advance, contours) of one character; contours = [Bezier point lists]."""
        g = self._glyphs.get(ch)
        if g is None:
            g = self._glyphs[ch] = self._read(ch)
        return g

    def backup_names(self):
        """The fonts a letter this one lacks is looked for in: fontconfig's own list after this font."""
        if self._names is None:
            _, fc, _ = _load()
            pat = _pattern(fc, self.family, self.weight, self.italic)
            res = c_int()
            fs = fc.FcFontSort(None, pat, 1, None, byref(res))
            fc.FcPatternDestroy(pat)
            seen, self._names = {n.lower() for n in self._names_here} | {self.family.lower()}, []
            if fs:
                for i in range(fs.contents.nfont):
                    names = _strings(fc, fs.contents.fonts[i], b"family")
                    if names and names[0].lower() not in seen:
                        seen.add(names[0].lower())
                        self._names.append(names[0])
                        if len(self._names) >= MOST_BACKUPS:
                            break
                fc.FcFontSetDestroy(fs)
        return self._names

    def missing(self, ch):
        """True if ch takes room but neither this font nor any font it borrows from has it (it shows as a box /
        "?"). Never for symbol fonts."""
        from notes.fonts import _no_width, backup_font
        if ch not in self._missing:
            self._missing[ch] = not (ch.isspace() or _no_width(ch) or self.symbol or self.has(ch)
                                     or backup_font(self, ch))
        return self._missing[ch]

    def _read(self, ch, backup=True):
        from notes.fonts import _no_width, backup_font
        if _no_width(ch):
            return 0.0, []
        if backup and not self.symbol and not self.has(ch):  # from another font that has it
            other = backup_font(self, ch)
            if other is not None:
                return other.glyph(ch)
        ft = self._ft
        if ft.FT_Load_Glyph(self._face, self.index(ch), LOAD_NO_HINTING | LOAD_NO_BITMAP):
            return 0.0, []
        slot = self._face.contents.glyph.contents
        advance = slot.advance.x
        outline = byref(slot.outline)
        if self.embolden:  # (as FreeType's own synthetic bold: thicker by a 24th of the size)
            grow = EM * 64 // 24
            ft.FT_Outline_EmboldenXY(outline, grow, grow)
            advance += grow
        if self.slant is not None:
            ft.FT_Outline_Transform(outline, byref(self.slant))
        return advance / 64 / EM, _contours(ft, outline)

    def close(self):
        if self._face:
            self._ft.FT_Done_Face(self._face)
            self._face = None


def _contours(ft, outline):
    """FreeType's outline -> contours as Bezier point lists [[x, y], ...] in em units (quadratic pieces made cubic,
    straight ones with handles a third of the way along)."""
    contours = []
    scale = 1 / 64 / EM

    def pt(v):
        return [v.contents.x * scale, v.contents.y * scale]

    def close():
        if contours and len(contours[-1]) > 1 and contours[-1][-1] != contours[-1][0]:
            line(contours[-1][0])

    def line(p):
        x0, y0 = contours[-1][-1]
        contours[-1].extend([[x0 + (p[0] - x0) / 3, y0 + (p[1] - y0) / 3],
                             [x0 + (p[0] - x0) * 2 / 3, y0 + (p[1] - y0) * 2 / 3], p])

    def move_to(to, _):
        close()
        contours.append([pt(to)])
        return 0

    def line_to(to, _):
        line(pt(to))
        return 0

    def conic_to(c, to, _):
        (x0, y0), (cx, cy), p = contours[-1][-1], pt(c), pt(to)
        contours[-1].extend([[x0 + (cx - x0) * 2 / 3, y0 + (cy - y0) * 2 / 3],
                             [p[0] + (cx - p[0]) * 2 / 3, p[1] + (cy - p[1]) * 2 / 3], p])
        return 0

    def cubic_to(c1, c2, to, _):
        contours[-1].extend([pt(c1), pt(c2), pt(to)])
        return 0

    funcs = _Funcs(_MOVE(move_to), _MOVE(line_to), _CONIC(conic_to), _CUBIC(cubic_to), 0, 0)
    ft.FT_Outline_Decompose(outline, byref(funcs), None)
    close()
    return [c for c in contours if len(c) >= 4]


class _Kerning:
    """font.kerning.get((a, b), 0.0) as the Windows dict does, asked of the font's kerning table pair by pair."""

    def __init__(self, font):
        self.font, self._seen = font, {}

    def get(self, pair, default=0.0):
        k = self._seen.get(pair)
        if k is None:
            f, k = self.font, 0.0
            a, b = f.index(pair[0]), f.index(pair[1])
            if a and b and f._face:
                v = _Vector()
                if not f._ft.FT_Get_Kerning(f._face, a, b, KERNING_UNSCALED, byref(v)):
                    k = v.x / (f._face.contents.units_per_EM or EM)
            self._seen[pair] = k
        return k or default
