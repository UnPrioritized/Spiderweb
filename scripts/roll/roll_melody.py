"""The piano roll's part of Fill with tempo jumps (window/melody_window.py): while its window is open, the notes the
jumps follow light up (an outline round each one in view). Past MOST_LIT in view only the first ones (zoomed out,
thousands of outlines would only slow the drawing)."""

import numpy as np

from window import look

MOST_LIT = 4000  # outlines drawn at most


class MelodyMarks:
    def draw_melody_lit(self):
        self.delete("melodylit")
        w = getattr(self.app, "melody_window", None)
        lit = w.lit if w else None
        if lit is None or not len(lit[0]) or self.sx is None:
            return
        s, e, k = lit
        lo, hi = self.x2t(self.kb_w), self.x2t(self.winfo_width())
        top, bottom = self.y2p(self.ruler_h), self.y2p(self.winfo_height())
        shown = np.flatnonzero((e > lo) & (s < hi) & (k <= top + 0.5) & (k >= bottom - 0.5))[:MOST_LIT]
        line = max(1, round(2 * self.scale))
        for i in shown.tolist():
            x0, x1 = self.t2x(float(s[i])), self.t2x(float(e[i]))
            y0, y1 = self.p2y(int(k[i]) + 0.5), self.p2y(int(k[i]) - 0.5)
            self.create_rectangle(x0, y0, max(x1, x0 + 2), y1, outline=look.MELODY_LIT, width=line,
                                  tags="melodylit")
