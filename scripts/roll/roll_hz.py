"""Piano roll: the Hz bass tool. A click on a Hz bass selects it and opens the Hz bass window (window/hz_window.py),
where its notes are placed. Anywhere else: a drag (or click, move, click) up or down marks where a new one starts
and the keys it repeats, then the window opens."""

from files.lang import tr
from roll.roll_shared import note_name
from window.hz_window import RED, hz_made, open_hz


class HzStart:
    """Mixed into PianoRoll. app.hz_start = the beat a new Hz bass starts at (None: no spot picked),
    app.hz_defaults = the lowest and highest key it repeats."""

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
        app.select(i)
        if i is not None:
            open_hz(app)
            return
        beat, key = self.event_pt(e)
        key = int(round(key))
        app.hz_start, app.hz_defaults = beat, {"lo": key, "hi": key}
        self.drag = ("hzkeys", key, e.x, e.y)  # how tall it is follows the mouse
        self.request_redraw()

    def hz_drag(self, e):
        app = self.app
        first, key = self.drag[1], int(round(self.event_pt(e)[1]))
        app.hz_defaults = {"lo": min(first, key), "hi": max(first, key)}
        self.request_redraw()
        lo, hi = app.hz_defaults["lo"], app.hz_defaults["hi"]
        app.show_position(tr("pianoroll.hz_keys", lo=note_name(lo), hi=note_name(hi), n=hi - lo + 1))

    def hz_keys_held(self):
        """The keys of a new Hz bass being dragged (or following the mouse after a click)."""
        return any(d and d[0] == "hzkeys" for d in (self.drag, self.follow))

    def drop_hz_keys(self):
        """Esc / Ctrl+Z while the keys are dragged: called off, the start mark goes (nothing undone, like Ctrl+Z
        while drawing a shape). False when they aren't being dragged."""
        if not self.hz_keys_held():
            return False
        self.drag = self.follow = None
        self.app.hz_start = None
        self.request_redraw()
        return True

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
