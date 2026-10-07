"""Letter outlines from the fonts installed in Windows (GDI through ctypes, no extra packages).

Everything is in em units: 1.0 = the font size, x to the right, y up from the baseline. A letter's outline is a
list of closed contours; a contour is a Bezier curve ([x, y] points: anchor, handle, handle, anchor, ... like
bezier.py) that ends where it starts. The direction of each contour is kept (holes run the other way round),
which is what the nonzero fill rule needs.

Letters the font doesn't have come from another installed font that has them (like Windows' own programs do): the
fonts Windows lists as stand-ins for this one, then BACKUPS. Not for symbol fonts (their letters ARE other pictures).

Elsewhere (Linux) Font is fonts_ft.FtFont: the same, read with fontconfig + FreeType.
"""

import bisect
import ctypes
import struct
from ctypes import wintypes

from files.lang import tr
from files.system import WINDOWS

EM = 2048  # the font is asked for at this many units per em (fine enough for any size)
# where letters a font lacks are looked for, after the ones Windows lists for that font (first that has it wins)
BACKUPS = ("Segoe UI", "Segoe UI Emoji", "Segoe UI Symbol", "Yu Gothic", "Meiryo", "MS Gothic", "Microsoft YaHei",
           "Microsoft JhengHei", "Malgun Gothic", "Nirmala UI", "Leelawadee UI", "Ebrima", "Gadugi", "Javanese Text",
           "Myanmar Text", "Mongolian Baiti", "Microsoft Yi Baiti", "Microsoft Himalaya", "Segoe UI Historic",
           "Cambria Math", "Arial Unicode MS")
GGO_GLYPH_INDEX, GGI_MARK_NONEXISTING_GLYPHS, SYMBOL_CHARSET = 0x80, 1, 2
CMAP = struct.unpack("<I", b"cmap")[0]  # (GetFontData's table name)


def _no_width(ch):
    """Joiners and variation selectors (an emoji's "show in colour" mark): nothing to draw, take no room."""
    o = ord(ch)
    return o in (0x200C, 0x200D) or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF
WEIGHTS = [(100, tr("fonts.thin")), (200, tr("fonts.extra_light")), (300, tr("fonts.light")),
           (400, tr("fonts.regular")), (500, tr("fonts.medium")), (600, tr("fonts.semibold")), (700, tr("fonts.bold")),
           (800, tr("fonts.extra_bold")), (900, tr("fonts.black"))]

GGO_METRICS, GGO_BEZIER, GGO_UNHINTED = 0, 3, 0x100
GDI_ERROR = 0xFFFFFFFF
TT_PRIM_LINE, TT_PRIM_QSPLINE, TT_PRIM_CSPLINE = 1, 2, 3


class _GLYPHMETRICS(ctypes.Structure):
    _fields_ = [("gmBlackBoxX", wintypes.UINT), ("gmBlackBoxY", wintypes.UINT), ("gmptGlyphOrigin", wintypes.POINT),
                ("gmCellIncX", ctypes.c_short), ("gmCellIncY", ctypes.c_short)]


class _FIXED(ctypes.Structure):
    _fields_ = [("fract", wintypes.WORD), ("value", ctypes.c_short)]


class _MAT2(ctypes.Structure):
    _fields_ = [("eM11", _FIXED), ("eM12", _FIXED), ("eM21", _FIXED), ("eM22", _FIXED)]


class _TEXTMETRIC(ctypes.Structure):
    _fields_ = [("tmHeight", wintypes.LONG), ("tmAscent", wintypes.LONG), ("tmDescent", wintypes.LONG),
                ("tmInternalLeading", wintypes.LONG), ("tmExternalLeading", wintypes.LONG),
                ("tmAveCharWidth", wintypes.LONG), ("tmMaxCharWidth", wintypes.LONG), ("tmWeight", wintypes.LONG),
                ("tmOverhang", wintypes.LONG), ("tmDigitizedAspectX", wintypes.LONG),
                ("tmDigitizedAspectY", wintypes.LONG), ("tmFirstChar", wintypes.WCHAR),
                ("tmLastChar", wintypes.WCHAR), ("tmDefaultChar", wintypes.WCHAR), ("tmBreakChar", wintypes.WCHAR),
                ("tmItalic", ctypes.c_byte), ("tmUnderlined", ctypes.c_byte), ("tmStruckOut", ctypes.c_byte),
                ("tmPitchAndFamily", ctypes.c_byte), ("tmCharSet", ctypes.c_byte)]


class _KERNINGPAIR(ctypes.Structure):
    _fields_ = [("wFirst", wintypes.WORD), ("wSecond", wintypes.WORD), ("iKernAmount", ctypes.c_int)]


_IDENTITY = _MAT2(_FIXED(0, 1), _FIXED(0, 0), _FIXED(0, 0), _FIXED(0, 1))
_fonts = {}


def _gdi():
    gdi = ctypes.windll.gdi32
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateFontW.restype = wintypes.HFONT
    gdi.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    gdi.GetTextFaceW.argtypes = [wintypes.HDC, ctypes.c_int, wintypes.LPWSTR]
    gdi.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.POINTER(_TEXTMETRIC)]
    gdi.GetGlyphOutlineW.restype = wintypes.DWORD
    gdi.GetGlyphOutlineW.argtypes = [wintypes.HDC, wintypes.UINT, wintypes.UINT, ctypes.POINTER(_GLYPHMETRICS),
                                     wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(_MAT2)]
    gdi.GetKerningPairsW.restype = wintypes.DWORD
    gdi.GetKerningPairsW.argtypes = [wintypes.HDC, wintypes.DWORD, ctypes.c_void_p]
    gdi.GetGlyphIndicesW.restype = wintypes.DWORD
    gdi.GetGlyphIndicesW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.WORD),
                                     wintypes.DWORD]
    gdi.GetFontData.restype = wintypes.DWORD
    gdi.GetFontData.argtypes = [wintypes.HDC, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    return gdi


def _linked(family):
    """The fonts Windows itself uses for letters this family lacks (its font link list), or []."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\FontLink\SystemLink") as key:
            entries = winreg.QueryValueEx(key, family)[0]
    except (ImportError, OSError):
        return []
    # entries like "MSGOTHIC.TTC,MS UI Gothic" or "MSGOTHIC.TTC,MS UI Gothic,128,96" (just a file: no family name)
    return [e.split(",")[1].strip() for e in entries if isinstance(e, str) and "," in e and e.split(",")[1].strip()]


def _local_first():
    """Chinese / Korean Windows: that language's font first for the letters the languages share (Japanese and
    others: BACKUPS' order, Japanese fonts first)."""
    try:
        lang = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return []
    if lang & 0x3FF == 0x04:  # Chinese: Taiwan, Hong Kong, Macao = traditional
        return ["Microsoft JhengHei"] if lang >> 10 in (1, 3, 5) else ["Microsoft YaHei"]
    return ["Malgun Gothic"] if lang & 0x3FF == 0x12 else []


def _wide_map(raw):
    """A font's cmap table -> its letter groups past U+FFFF [(first, last, first glyph)] (format 12), or []."""
    try:
        for i in range(struct.unpack_from(">H", raw, 2)[0]):
            plat, enc, off = struct.unpack_from(">HHI", raw, 4 + 8 * i)
            if (plat, enc) in ((3, 10), (0, 4), (0, 6)) and struct.unpack_from(">H", raw, off)[0] == 12:
                n = struct.unpack_from(">I", raw, off + 12)[0]
                return sorted(struct.unpack_from(">III", raw, off + 16 + 12 * g) for g in range(n))
    except struct.error:  # (a broken table: as if it had none)
        pass
    return []


class Font:
    """One font (family, weight, italic) read from Windows. Unknown families come out as the font Windows picks
    instead; `face` says which one that is."""

    def __init__(self, family, weight=400, italic=False):
        self.family, self.weight, self.italic = family, weight, italic
        self._glyphs = {}
        gdi = self._gdi = _gdi()
        self._dc = gdi.CreateCompatibleDC(None)
        self._font = gdi.CreateFontW(-EM, 0, 0, 0, int(weight), 1 if italic else 0, 0, 0, 1, 4, 0, 4, 0, family)
        gdi.SelectObject(self._dc, self._font)
        buf = ctypes.create_unicode_buffer(64)
        gdi.GetTextFaceW(self._dc, 64, buf)
        self.face = buf.value
        tm = _TEXTMETRIC()
        gdi.GetTextMetricsW(self._dc, ctypes.byref(tm))
        self.ascent, self.descent = tm.tmAscent / EM, tm.tmDescent / EM
        self.line_height = (tm.tmHeight + tm.tmExternalLeading) / EM  # baseline to baseline
        self.symbol = tm.tmCharSet == SYMBOL_CHARSET  # (its letters are pictures: never swapped)
        self._wide = None     # its letters past U+FFFF ([groups], [their firsts]), read when first needed
        self._names = None    # the fonts letters it lacks come from (backup_names)
        self._missing = {}    # letter -> no font has it (missing)
        self.kerning = self._kerning()
        h = self._read("H", backup=False)[1]  # (its own H: a font without letters, e.g. emoji, has no cap height)
        self.cap = (max(y for c in h for _, y in c) if h else 0) or 0.7  # capital letter height

    @property
    def found(self):
        return self.face.lower() == self.family.lower()

    def _kerning(self):
        gdi = self._gdi
        n = gdi.GetKerningPairsW(self._dc, 0, None)
        if not n or n == GDI_ERROR:
            return {}
        pairs = (_KERNINGPAIR * n)()
        n = gdi.GetKerningPairsW(self._dc, n, pairs)
        return {(chr(p.wFirst), chr(p.wSecond)): p.iKernAmount / EM for p in pairs[:n] if p.iKernAmount}

    def glyph(self, ch):
        """(advance, contours) of one character; contours = [Bezier point lists]."""
        g = self._glyphs.get(ch)
        if g is None:
            g = self._glyphs[ch] = self._read(ch)
        return g

    def has(self, ch):
        """True if this font has its own letter for ch."""
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF:  # (half of a broken pair: nobody has it)
            return False
        if o > 0xFFFF:
            return self._wide_glyph(o) is not None
        idx = wintypes.WORD()
        n = self._gdi.GetGlyphIndicesW(self._dc, ch, 1, ctypes.byref(idx), GGI_MARK_NONEXISTING_GLYPHS)
        return n != GDI_ERROR and idx.value not in (0, 0xFFFF)

    def _wide_glyph(self, o):
        """The glyph number of letter o (past U+FFFF) in this font, or None."""
        if self._wide is None:
            size = self._gdi.GetFontData(self._dc, CMAP, 0, None, 0)
            groups = []
            if size not in (0, GDI_ERROR):
                buf = ctypes.create_string_buffer(size)
                if self._gdi.GetFontData(self._dc, CMAP, 0, buf, size) == size:
                    groups = _wide_map(buf.raw)
            self._wide = (groups, [g[0] for g in groups])
        groups, firsts = self._wide
        i = bisect.bisect_right(firsts, o) - 1
        if i >= 0 and groups[i][0] <= o <= groups[i][1]:
            return groups[i][2] + o - groups[i][0] or None
        return None

    def backup_names(self):
        """The fonts a letter this one lacks is looked for in, in order: Windows' list for it, then BACKUPS."""
        if self._names is None:
            seen, self._names = {self.face.lower(), self.family.lower()}, []
            for name in _linked(self.family) + _linked(self.face) + _local_first() + list(BACKUPS):
                if name.lower() not in seen:
                    seen.add(name.lower())
                    self._names.append(name)
        return self._names

    def missing(self, ch):
        """True if ch takes room but neither this font nor any font it borrows from has it (it shows as a box /
        "?"). Never for symbol fonts."""
        if ch not in self._missing:
            self._missing[ch] = not (ch.isspace() or _no_width(ch) or self.symbol or self.has(ch)
                                     or backup_font(self, ch))
        return self._missing[ch]

    def _read(self, ch, backup=True):
        if _no_width(ch):
            return 0.0, []
        if backup and not self.symbol and not self.has(ch):  # from another font that has it
            other = backup_font(self, ch)
            if other is not None:
                return other.glyph(ch)
        gdi, gm = self._gdi, _GLYPHMETRICS()
        code, by_index = ord(ch), 0
        if code > 0xFFFF:  # (Windows only finds these by the font's own glyph number)
            gi = self._wide_glyph(code)
            code, by_index = (ord("?"), 0) if gi is None else (gi, GGO_GLYPH_INDEX)
        fmt = GGO_BEZIER | GGO_UNHINTED | by_index
        size = gdi.GetGlyphOutlineW(self._dc, code, fmt, ctypes.byref(gm), 0, None, ctypes.byref(_IDENTITY))
        if size == GDI_ERROR:
            gdi.GetGlyphOutlineW(self._dc, code, GGO_METRICS | by_index, ctypes.byref(gm), 0, None,
                                 ctypes.byref(_IDENTITY))
            return gm.gmCellIncX / EM, []
        advance = None
        contours = []
        if size:
            buf = ctypes.create_string_buffer(size)
            if gdi.GetGlyphOutlineW(self._dc, code, fmt, ctypes.byref(gm), size, buf,
                                    ctypes.byref(_IDENTITY)) != GDI_ERROR:
                contours = _parse(buf.raw)
        advance = gm.gmCellIncX / EM
        return advance, contours

    def close(self):
        if self._dc:
            self._gdi.DeleteObject(self._font)
            self._gdi.DeleteDC(self._dc)
            self._dc = None


def _parse(raw):
    """GetGlyphOutline's GGO_BEZIER buffer -> contours as Bezier point lists [[x, y], ...] in em units."""
    fx = lambda i: struct.unpack_from("<i", raw, i)[0] / 65536 / EM
    contours, pos = [], 0
    while pos + 16 <= len(raw):
        cb = struct.unpack_from("<I", raw, pos)[0]
        end = pos + cb
        sx, sy = fx(pos + 8), fx(pos + 12)
        pts = [[sx, sy]]

        def line_to(x, y):  # a straight piece: handles a third of the way along
            (x0, y0) = pts[-1]
            pts.extend([[x0 + (x - x0) / 3, y0 + (y - y0) / 3], [x0 + (x - x0) * 2 / 3, y0 + (y - y0) * 2 / 3], [x, y]])

        i = pos + 16
        while i < end:
            kind, n = struct.unpack_from("<HH", raw, i)
            ps = [[fx(i + 4 + 8 * k), fx(i + 8 + 8 * k)] for k in range(n)]
            if kind == TT_PRIM_CSPLINE:
                pts += ps[:n - n % 3]
            else:
                for x, y in ps:
                    line_to(x, y)
            i += 4 + 8 * n
        if pts[-1] != [sx, sy]:  # closed back to the start
            line_to(sx, sy)
        if len(pts) >= 4:
            contours.append(pts)
        pos = end
    return contours


if WINDOWS:
    DEFAULT_FONT = "Arial"  # new texts, and a text whose font isn't installed once it's edited
else:
    from notes.fonts_ft import FtFont as Font, default_family
    DEFAULT_FONT = default_family()


def get_font(family, weight=400, italic=False):
    """A Font, kept for next time (reading letters from Windows isn't free)."""
    key = (family.lower(), int(weight), bool(italic))
    f = _fonts.get(key)
    if f is None:
        if len(_fonts) > 40:
            for old in _fonts.values():
                old.close()
            _fonts.clear()
        f = _fonts[key] = Font(family, weight, italic)
    return f


_backups = {}  # (family, weight, italic) -> Font, or None when it isn't installed (kept: never closed)


def backup_font(font, ch):
    """The first font in font's backup_names (same weight / italic) that has ch, or None."""
    for name in font.backup_names():
        key = (name.lower(), font.weight, font.italic)
        if key not in _backups:
            _backups[key] = None  # (first: making it can't come back here for itself)
            f = Font(name, font.weight, font.italic)
            # (not `found`: Windows answers with the local name, e.g. Yu Gothic's in Japanese; one it swapped for
            # another font is harmless, it just may not have the letter)
            if not f.symbol:
                _backups[key] = f
            else:
                f.close()
        f = _backups[key]
        if f is not None and f.has(ch):
            return f
    return None


def font_families(root):
    """Installed font family names, sorted (vertical "@" variants left out)."""
    from tkinter import font as tkfont
    return sorted({f for f in tkfont.families(root) if not f.startswith("@")}, key=str.lower)
