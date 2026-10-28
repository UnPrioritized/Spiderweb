"""Jump areas (PLAN.md #12 group C): the Fill with tempo jumps window's "Where I click" choice. Each click in the
Conductor pane adds an EDGE, every two neighbouring edges make an area (one jump each, the window's shape across
them), like the polyline tool for time. The tempo is laid again over the line from before the chain at every
change, so it shows as you click; the whole chain = ONE undo step, Ctrl+Z mid-chain = the last edge only (its own
steps). Press + hold = the edge follows the mouse; near an edge = drag it. Enter / Esc / right-click = done."""

import copy

import numpy as np

from files.lang import tr
from notes.tempo import tempo_text
from window import look

GRAB_PX = 6  # an edge this near the mouse is grabbed instead of a new one made
SNAP_PX = 10  # a melody note start this near: the edge goes onto it


class JumpChain:
    def __init__(self, win):
        self.win, self.app = win, win.app
        self.edges = []  # beats, in click order (the areas: sorted)
        self.undos, self.redos = [], []  # the edges before each click / drag (Ctrl+Z / Ctrl+Y mid-chain)
        self.base = copy.deepcopy(self.app.tempo)  # the line before the chain: laid again at every change
        self.pushed = False  # the chain's one undo step made (at the first area)
        self.seen = tempo_text(self.app.tempo)  # the line as the chain left it: changed elsewhere = chain done
        self.grab = None  # the edge held (its place in edges)
        self.moved = False

    def sorted(self):
        return sorted(self.edges)

    def alive(self):
        """Still the line the chain left (any other tempo change ends the chain, like the pane's live line)."""
        return tempo_text(self.app.tempo) == self.seen

    # ------------------------------------------------------------ mouse

    def edge_near(self, x):
        roll = self.app.roll
        near = [(abs(roll.t2x(b) - x), k) for k, b in enumerate(self.edges)]
        best = min(near, default=None)
        return best[1] if best and best[0] <= GRAB_PX * self.app.tempo_pane.scale else None

    def snap(self, x, e):
        """Where an edge at x goes: the nearest melody note start within SNAP_PX, else the grid; Shift = ticks."""
        pane, roll, ppq = self.app.tempo_pane, self.app.roll, max(1, self.app.ppq)
        b = max(0.0, roll.x2t(x))
        if e.state & 0x0001:
            return round(b * ppq) / ppq
        starts = self.win.snap_starts
        if len(starts) and roll.sx:
            r = SNAP_PX * pane.scale / roll.sx
            i = int(np.searchsorted(starts, b))
            near = [float(starts[j]) for j in (i - 1, i) if 0 <= j < len(starts) and abs(starts[j] - b) <= r]
            if near:
                return round(min(near, key=lambda s: abs(s - b)) * ppq) / ppq
        return round(pane.snap_beat(b, e) * ppq) / ppq

    def press(self, e):
        self.undos.append(list(self.edges))
        self.redos.clear()
        k = self.edge_near(e.x)
        if k is None:
            b = self.snap(e.x, e)
            if any(abs(b - o) < 1e-9 for o in self.edges):  # (on an edge already: that one is held)
                k = next(j for j, o in enumerate(self.edges) if abs(b - o) < 1e-9)
            else:
                self.edges.append(b)
                k = len(self.edges) - 1
                self.moved = True
        self.grab = k
        self.lay()

    def drag(self, e):
        if self.grab is None:
            return
        b = self.snap(e.x, e)
        if any(abs(b - o) < 1e-9 for j, o in enumerate(self.edges) if j != self.grab):
            return  # (onto another edge: stays where it was)
        if b != self.edges[self.grab]:
            self.edges[self.grab] = b
            self.moved = True
            self.lay()

    def release(self):
        self.grab = None
        if not self.moved and self.undos and self.undos[-1] == self.edges:  # (a click on an edge: no step)
            self.undos.pop()
        self.moved = False
        self.app.tempo_pane.request_redraw()

    def step(self, redo=False):
        """Ctrl+Z / Ctrl+Y mid-chain: the last edge (or move) back / again. False when there's none to take."""
        src, dst = (self.redos, self.undos) if redo else (self.undos, self.redos)
        if not src:
            return False
        dst.append(list(self.edges))
        self.edges = src.pop()
        self.lay()
        return True

    # ------------------------------------------------------------ the tempo

    def lay(self):
        """The line from before the chain with the areas' tempo laid in (fewer than 2 edges: as before)."""
        app, pane = self.app, self.app.tempo_pane
        edges = self.sorted()
        clip = self.win.chain_clip(self.base, edges) if len(edges) > 1 else None
        if clip is None and not self.pushed:  # (nothing laid yet: the line stays as it is)
            return pane.request_redraw()
        if clip is not None and not self.pushed:
            app.push_undo(name=tr("melody.step_areas"))
            self.pushed = True
        tempo = app.tempo
        tempo.clear()
        tempo.update(copy.deepcopy(self.base))
        if clip is not None:
            pane.lay_copies(tempo, clip, clip["at"], pane.ed_base(), teal=False)
        pane.sel, pane.part, pane.bands = [], None, []
        app.tempo_changed()
        self.seen = tempo_text(app.tempo)
        pane.request_redraw()

    # ------------------------------------------------------------ the pane

    def draw(self, pane, lw):
        """Each edge a line the pane's whole height, "Area 1, 2…" along the bottom between them."""
        roll = self.app.roll
        h, w, kb, s = pane.winfo_height(), pane.winfo_width(), roll.kb_w, pane.scale
        edges = self.sorted()
        held = self.edges[self.grab] if self.grab is not None else None
        for n, (a, b) in enumerate(zip(edges, edges[1:])):
            x0, x1 = roll.t2x(a), roll.t2x(b)
            if x1 < kb or x0 > w:
                continue
            text = tr("melody.area_n", n=n + 1)
            # (at the bottom: the top holds the catch-ups' BPMs and the signatures)
            item = pane.create_text((max(x0, kb) + min(x1, w)) / 2, h - 9 * s, text=text, fill=look.MELODY_EDGE,
                                    font=look.font(8, "bold"))
            box = pane.bbox(item)
            if box and box[2] - box[0] > x1 - x0 - 4 * s:  # (no room for the words: the number only, or nothing)
                pane.itemconfig(item, text=str(n + 1))
                box = pane.bbox(item)
                if box and box[2] - box[0] > x1 - x0 - 2 * s:
                    pane.delete(item)
        for b in edges:
            x = roll.t2x(b)
            if kb <= x <= w:
                pane.create_line(x, 0, x, h, fill=look.MELODY_EDGE, width=lw * (3 if b == held else 2),
                                 dash=() if b == held else (5, 3))
