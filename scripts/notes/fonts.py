"""Letter outlines from the fonts installed in Windows (GDI through ctypes, no extra packages).

Everything is in em units: 1.0 = the font size, x to the right, y up from the baseline. A letter's outline is a
list of closed contours; a contour is a Bezier curve ([x, y] points: anchor, handle, handle, anchor, ... like
bezier.py) that ends where it starts. The direction of each contour is kept (holes run the other way round),
which is what the nonzero fill rule needs.
"""

import ctypes
import struct
from ctypes import wintypes

EM = 2048  # the font is asked for at this many units per em (fine enough for any size)
WEIGHTS = [(100, "Thin"), (200, "Extra light"), (300, "Light"), (400, "Regular"), (500, "Medium"),
           (600, "Semibold"), (700, "Bold"), (800, "Extra bold"), (900, "Black")]

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
    return gdi


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
        self.kerning = self._kerning()
        h = self.glyph("H")[1]
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

    def _read(self, ch):
        gdi, gm = self._gdi, _GLYPHMETRICS()
        code = ord(ch) if len(ch) == 1 and ord(ch) <= 0xFFFF else ord("?")
        fmt = GGO_BEZIER | GGO_UNHINTED
        size = gdi.GetGlyphOutlineW(self._dc, code, fmt, ctypes.byref(gm), 0, None, ctypes.byref(_IDENTITY))
        if size == GDI_ERROR:
            gdi.GetGlyphOutlineW(self._dc, code, GGO_METRICS, ctypes.byref(gm), 0, None, ctypes.byref(_IDENTITY))
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


def font_families(root):
    """Installed font family names, sorted (vertical "@" variants left out)."""
    from tkinter import font as tkfont
    return sorted({f for f in tkfont.families(root) if not f.startswith("@")}, key=str.lower)
