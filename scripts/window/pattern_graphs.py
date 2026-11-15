"""Graphs on a pattern's numbers (the "…" after each number box of "Pattern along the line"): the graph window
(graph_window.py) on p["graphs"][name] (pattern.py). Loops = how crowded the loops are along the line; any other
number = that number changing along the line. In the side panel (PanelGraphs: the selected curves / polygons, one
undo step per change) and in the Custom… window (DialogGraphs: its pattern, its own Ctrl+Z, Cancel there puts it
all back)."""

import copy

from files.lang import tr
from files.mathexpr import fmt
from notes.pattern import graph_trouble
from notes.tumour import clean_graph
from window.graph_window import GraphSource, GraphWindow

GRAPH_HINT = tr("graph_window.drag_a_point_to_move_it").split("\n")[0]


def set_graph(p, key, pts):
    """p's graph for key = pts (flat at 100 % = none)."""
    g = clean_graph(pts)
    graphs = dict(p.get("graphs") or {})
    if g:
        graphs[key] = [list(q) for q in g]
    else:
        graphs.pop(key, None)
    if graphs:
        p["graphs"] = graphs
    else:
        p.pop("graphs", None)


def graphed(p):
    """The names of p's numbers that follow a graph ("loops" too)."""
    return set((p or {}).get("graphs") or {})


def graph_label(name):
    return tr("panel_pattern.loops") if name == "loops" else name


class PatternGraphs(GraphSource):
    """What both kinds share: texts, and the box's number."""

    def pattern(self):
        """The first target's pattern (the one shown)."""
        return None

    def hint(self, key):
        return GRAPH_HINT + "\n" + tr("pattern_graph.loops_hint" if key == "loops" else "pattern_graph.hint")

    def zero_text(self, label):
        return tr("pattern_graph.zero", label=label)

    def note(self, key, pts):
        if key == "loops":
            return tr("pattern_graph.all_zero") if all(f <= 1e-12 for _, f in pts) else None
        p = self.pattern()
        if p and graph_trouble(p, key, pts):
            return tr("pattern_graph.some_plain", label=graph_label(key))
        return None

    def box_number(self, key):
        p = self.pattern()
        if not p:
            return 1.0
        return 1.0 if key == "loops" else p["vars"].get(key, 1.0)  # (Loops: crowdedness, never 0)

    def value_text(self, key, f):
        return tr("pattern_graph.loops_value", f=fmt(round(f * 100, 1))) if key == "loops" else None

    def info_text(self, key, label):
        return tr("pattern_graph.loops_info") if key == "loops" else None

    def number_row(self, key):
        return key != "loops"

    def empty_text(self, name):
        return tr("pattern_graph.select", name=name)


class PanelGraphs(PatternGraphs):
    """The side panel's pattern rows: the selected curves, lines, arcs and polygons with a pattern."""
    attr = "pattern_graph"

    def __init__(self, app, key, anchor):
        super().__init__(app, anchor)
        self.key = key
        self.step = 0  # (each finished change its own undo step)

    def targets(self):
        key = self.key
        return [h for h in self.app.with_layer("pattern")
                if key == "loops" or key in h["pattern"]["vars"] and not h["pattern"].get("loop")]

    def pattern(self):
        tgts = self.targets()
        return tgts[0]["pattern"] if tgts else None

    def graph(self, key):
        return ((self.pattern() or {}).get("graphs") or {}).get(key)

    def store(self, key, pts):
        app = self.app
        tgts = self.targets()
        if not tgts:
            return
        app.begin_edit(("pattern_graph", key, tuple(sorted(app.sels)), self.step))
        for h in tgts:
            set_graph(h["pattern"], key, pts)
        app.formulas_edited()
        app.sync_pattern()  # ("× graph")

    def done(self, key):
        self.step += 1

    def snapshot(self, tgts, key):
        return [copy.deepcopy((h["pattern"].get("graphs") or {}).get(key)) for h in tgts]

    def restore(self, tgts, saved, key):
        app = self.app
        now = self.targets()
        alive = [(h, g) for h, g in zip(tgts, saved) if any(h is t for t in now)]
        if not alive or all((h["pattern"].get("graphs") or {}).get(key) == g for h, g in alive):
            return
        app.begin_edit(("pattern_graph", key, tuple(sorted(app.sels)), "cancel"))
        for h, g in alive:
            set_graph(h["pattern"], key, g or [[0.0, 1.0], [1.0, 1.0]])
        app.formulas_edited()
        app.sync_pattern()  # ("× graph")

    def closed(self, gw):
        self.app.pattern_graph = None


class DialogGraphs(PatternGraphs):
    """The Custom… window's pattern (shown on the curves as it changes; Apply / Cancel there decide)."""
    attr = "graph_window"

    def __init__(self, dialog, anchor):
        super().__init__(dialog, anchor)
        self.dialog = dialog

    def targets(self):
        return [self.dialog] if self.dialog.layer == "pattern" else []

    def pattern(self):
        return self.dialog.pat

    def graph(self, key):
        return (self.dialog.pat.get("graphs") or {}).get(key)

    def store(self, key, pts):
        set_graph(self.dialog.pat, key, pts)
        self.dialog.refresh()

    def done(self, key):
        self.dialog.mark()

    def snapshot(self, tgts, key):
        return copy.deepcopy(self.graph(key))

    def restore(self, tgts, saved, key):
        if self.graph(key) != saved:
            set_graph(self.dialog.pat, key, saved or [[0.0, 1.0], [1.0, 1.0]])
            self.dialog.refresh()
            self.dialog.mark()

    def closed(self, gw):
        self.dialog.graph_window = None


class PathGraphs(PatternGraphs):
    """The Copy along a path window's formula (its page's cfg "pattern"): changes show at once, each finished one a
    step of the window's own undo (Accept makes it the piano roll's)."""
    attr = "pattern_graph"

    def __init__(self, win, anchor):
        super().__init__(win, anchor)
        self.win = win
        self.step = 0

    def targets(self):
        return [self.win.cfg] if self.win.cfg.get("pattern") else []

    def pattern(self):
        return self.win.cfg.get("pattern")

    def graph(self, key):
        return ((self.pattern() or {}).get("graphs") or {}).get(key)

    def store(self, key, pts):
        p = self.pattern()
        if not p:
            return
        p = copy.deepcopy(p)
        set_graph(p, key, pts)
        self.win.cfg["pattern"] = p
        self.win.show()
        self.win.preview(False)
        self.win.undo.mark(("pattern_graph", key, self.step))

    def done(self, key):
        self.win.preview(True)
        self.win.undo.mark(("pattern_graph", key, self.step), end=True)
        self.step += 1

    def snapshot(self, tgts, key):
        return copy.deepcopy(self.graph(key))

    def restore(self, tgts, saved, key):
        if self.pattern() and self.graph(key) != saved:
            self.store(key, saved or [[0.0, 1.0], [1.0, 1.0]])
            self.done(key)

    def closed(self, gw):
        self.win.pattern_graph = None


def open_graph(holder, src, key):
    """Open (or bring up) holder's graph window for key (src.attr names where holder keeps it)."""
    attr = src.attr
    gw = getattr(holder, attr, None)
    if gw and gw.key != key:
        gw.ok()
        gw = None
    if not gw:
        gw = GraphWindow(src, key, graph_label(key), "unit.none")
        setattr(holder, attr, gw)
    gw.lift()
    gw.focus_set()
    return gw
