"""Piano roll: the Hz bass tool. A click on a Hz bass selects it, a click anywhere else marks where a new one starts;
either way the Hz bass window (window/hz_window.py) opens, where its notes are placed."""

from window.hz_window import RED, hz_made, open_hz


class HzStart:
    """Mixed into PianoRoll. app.hz_start = the beat a new Hz bass starts at (None: no spot picked)."""

    def hz_at(self, x, y):
        """The Hz bass (made with this tool) whose box is under (x, y) on screen, or None."""
        for i in range(len(self.app.shapes) - 1, -1, -1):
            sh = self.app.shapes[i]
            if hz_made(sh):
                corners = [(self.t2x(b), self.p2y(p)) for b, p in self.custom_corners(sh)]
                if self.inside_box(corners, x, y):
                    return i
        return None

    def hz_click(self, e):
        app = self.app
        i = self.hz_at(e.x, e.y)
        if i is None:
            app.hz_start = self.event_pt(e)[0]
        app.select(i)
        open_hz(app)
        self.request_redraw()

    def draw_hz_start(self):
        """The spot picked for a new Hz bass: a dashed red line over the keys it will repeat."""
        app = self.app
        if app.hz_start is None or app.sels or app.tool.get() != "hz":
            return
        x = self.t2x(app.hz_start)
        y0, y1 = self.p2y(app.hz_defaults["hi"] + 0.5), self.p2y(app.hz_defaults["lo"] - 0.5)
        w = max(2, round(2 * self.scale))
        self.create_line(x, y0, x, y1, fill=RED, width=w, dash=(5, 3))
        for y in (y0, y1):
            self.create_line(x, y, x + 8 * self.scale, y, fill=RED, width=w)
