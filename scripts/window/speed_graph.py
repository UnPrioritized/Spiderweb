"""Copy along a path's Speed graph (Speed: Custom, its "Graph…" button; user 2026-10-16): the tumour graph's window
(graph_window.py) on the path window's page. Left = the first copy, right = the last; the height = how fast the copies
go there: high = far apart, low = bunched up, 0 = on top of each other (notes/copypath.py graph_curve). Stored as the
page's "graph"; each change is a step in the path window's own undo too."""

import json

from files.lang import tr
from files.mathexpr import fmt
from notes.copypath import SPEED_GRAPHS, SPEED_LIMIT
from window import look
from window.graph_window import FLAT, GraphWindow
from window.widgets import remember_place

# (a zigzag: bunched up and spread out again, three times)
WAVES = [[0.0, 1.0]] + [[(2 * k + 1) / 12, 1.9 if k % 2 == 0 else 0.1] for k in range(6)] + [[1.0, 1.0]]


class SpeedGraph(GraphWindow):
    PRESETS = [(tr("path.even"), SPEED_GRAPHS["even"]), (tr("path.up"), SPEED_GRAPHS["up"]),
               (tr("path.down"), SPEED_GRAPHS["down"]),
               (tr("speed_graph.slow_fast_slow"), [[0.0, 0.0], [0.5, 2.0], [1.0, 0.0]]),
               (tr("speed_graph.fast_slow_fast"), [[0.0, 2.0], [0.5, 0.0], [1.0, 2.0]]),
               (tr("speed_graph.waves"), WAVES)]
    VIEWS = [(0, 100), (0, 200), (0, 400), (0, 1000)]
    LO, HI = 0.0, SPEED_LIMIT
    HINT = tr("speed_graph.hint")
    FORMULA_HINT = tr("speed_graph.formula_hint")
    ALONG = tr("speed_graph.along")

    def __init__(self, pw):
        self.pw = pw
        super().__init__(pw, "speed", tr("path.speed"), "unit.percent")

    def put_place(self):
        pw = self.pw
        self.geometry(f"+{pw.winfo_rootx() + pw.winfo_width() + int(8 * self.s)}+{pw.winfo_rooty()}")
        remember_place(self, "speed_graph")

    def live(self):
        pw = self.pw
        return bool(pw.targets) and not pw.idle and pw.cfg.get("speed") == "custom"

    def read(self):
        return self.pw.cfg.get("graph")

    def write(self, pts):
        self.pw.put("graph", [list(p) for p in pts], not self.drag)

    def release(self, e):
        super().release(e)
        if not self.job:  # (a point let go without moving: the path window's step ends too)
            self.pw.undo.mark()

    def session_key(self):
        pw = self.pw
        return json.dumps([pw.targets, sorted((pw.at or {}).items())])

    def same_session(self):
        return self.session["key"] == self.session_key()

    def begin(self):
        """Start again from the page's graph as it is now (Cancel goes back to this)."""
        self.session = {"key": self.session_key(), "graph": json.loads(json.dumps(self.pw.cfg.get("graph")))}

    def restore(self):
        if self.same_session() and self.pw.cfg.get("graph") != self.session["graph"]:
            self.pw.put("graph", self.session["graph"])

    def fit_order(self):
        v = self.VIEWS
        return [v[1], v[0], v[2], v[3]]

    def zero_text(self):
        return tr("speed_graph.all_zero")

    def is_zero(self):
        return self.live() and all(abs(f) < 1e-12 for _, f in self.pts)

    def info_text(self):
        return tr("speed_graph.info") if self.live() else tr("speed_graph.pick_custom")

    def hover_text(self, at):
        return tr("speed_graph.at", at=fmt(round(at[0] * 100, 1)), v=fmt(round(at[1] * 100, 1)))

    def box_number(self):
        return None

    def draw_under(self, y_top, y_bot):
        pass

    def draw_below(self, y_bot):
        cv, s = self.canvas, self.s
        cv.create_text(self.u2x(0), y_bot + 4 * s, text=tr("speed_graph.first"), anchor="nw", fill=look.LABEL,
                       font=look.font(7))
        cv.create_text(self.u2x(1), y_bot + 4 * s, text=tr("speed_graph.last"), anchor="ne", fill=look.LABEL,
                       font=look.font(7))


def start_graph(speed):
    """The graph Custom starts from: the Speed picked before it, drawn (the same places)."""
    return [list(p) for p in SPEED_GRAPHS.get(speed, FLAT)]
