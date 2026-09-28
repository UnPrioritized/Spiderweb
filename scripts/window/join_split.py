"""Join (selected lines / polylines / freehand strokes / curves / arcs -> one curve) and Split (a joined curve back
into pieces, a curve / polyline / line cut in two where it was right-clicked, a custom shape into its separate
drawings). The maths is in notes/joined.py."""

import copy
import math
from tkinter import ttk

from notes.bezier import anchor_count, nearest, split
from notes.joined import (custom_groups, join_shapes, join_velocity, piece_velocity, sections, split_at, split_custom,
                          split_pieces)
from notes.tumour import LINE_KINDS, split_tumour
from roll.roll_shared import cached_path
from window.widgets import Tooltip

TOUCH_PX = 8  # ends closer than this on screen (times the display scaling) count as touching, like Live shape snaps


def span(sh):
    bs = [b for b, _ in cached_path(sh)]
    return min(bs), max(bs)


JOIN_KINDS = "lines, polylines, freehand strokes, curves and arcs"
JOIN_TIP = "Joins the selected shapes into one Curve shape (ends that touch become one line with a corner)."
SPLIT_TIP = ("Splits a joined curve back into its pieces, or a custom shape (like one drawn with Live shape)\n"
             "into its separate drawings.")
SPLIT_HERE = "To cut a line in two where you want: right-click it there → Split here."


class JoinSplit:
    """Mixed into App."""

    def _build_join(self, box):
        """The Shapes box's Join / Split buttons (also in the right-click menu)."""
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(4, 0))
        self.join_btn = ttk.Button(row, text="Join shapes into one curve", command=self.join_selected)
        self.join_btn.pack(side="left")
        self.split_btn = ttk.Button(row, text="Split into separate shapes", command=self.split_selected)
        self.split_btn.pack(side="left", padx=(4, 0))
        self.join_tip, self.split_tip = Tooltip(self.join_btn, JOIN_TIP), Tooltip(self.split_btn, SPLIT_TIP)

    def join_problem(self):
        """Why the selection can't be joined (None = it can)."""
        sels = [i for i in self.sels if i < len(self.shapes)]
        if len(sels) < 2:
            return f"Select two or more {JOIN_KINDS.replace(' and ', ' or ')} to join them (Ctrl+click adds one)."
        other = sorted({self.shape_label(self.shapes[i]).split(":")[0] for i in sels
                        if self.shapes[i]["kind"] not in LINE_KINDS})
        if other:
            return f"Only {JOIN_KINDS} can be joined ({' and '.join(other).lower()} selected)."
        return None

    def split_problem(self):
        """Why the selection can't be split into separate shapes (None = it can)."""
        if len(self.sels) != 1 or self.sel is None or self.sel >= len(self.shapes):
            return "Select one shape to split it."
        sh = self.selected()
        if sh["kind"] == "custom" and (sh.get("text") or "notes" in sh):
            return "Text and pasted notes can't be split."
        if not self.can_split_pieces(sh):
            return ("It's all one piece: nothing to split into separate shapes." if sh["kind"] in ("curve", "custom")
                    else "Only joined curves and custom shapes split into separate shapes.")
        return None

    def sync_join(self):
        """Join / Split buttons greyed out (their tooltip says why) when they can't be used."""
        for btn, tip, text, problem in ((self.join_btn, self.join_tip, JOIN_TIP, self.join_problem()),
                                        (self.split_btn, self.split_tip, SPLIT_TIP, self.split_problem())):
            btn.state(["disabled"] if problem else ["!disabled"])
            tip.text = f"{text}\n\n{problem}" if problem else text
            if btn is self.split_btn:
                tip.text += "\n" + SPLIT_HERE

    def split_selected(self):
        if not self.split_problem():
            self.split_pieces(self.sel)

    def can_join(self):
        return self.join_problem() is None

    def join_selected(self):
        if not self.can_join() or self.roll.sx is None:
            return
        roll = self.roll

        def touch(p, q):
            return math.hypot(roll.t2x(p[0]) - roll.t2x(q[0]), roll.p2y(p[1]) - roll.p2y(q[1])) <= TOUCH_PX * self.scale

        order = sorted(self.sels)
        olds = [self.shapes[i] for i in order]
        new = join_shapes(olds, roll.sy / roll.sx, touch)
        if new is None:
            return
        join_velocity(new, olds, [span(sh) for sh in olds], span(new))  # (each keeps its velocities)
        self.roll.cancel_draft()
        self.push_undo()
        at = order[0]
        for i in reversed(order):
            del self.shapes[i]
        self.shapes.insert(at, new)
        self.select(at)
        self.shapes_changed()
        pieces = len(new.get("gaps", [])) + 1
        self.status.config(text=f"Joined {len(order)} shapes into one curve" +
                           (f" ({pieces} pieces: some ends didn't touch)" if pieces > 1 else ""))

    def can_split_pieces(self, sh):
        """A joined curve with more than one piece / shape in it, or a custom drawing with separate parts."""
        if sh["kind"] == "curve":
            return len(sections(sh)) > 1
        return (sh["kind"] == "custom" and "notes" not in sh and not sh.get("text") and
                len(custom_groups(sh)) > 1)

    def replace_shape(self, i, parts):
        """Shape i replaced by parts (selected), velocities kept where they were."""
        old = self.shapes[i]
        self.roll.cancel_draft()
        self.push_undo()
        whole = span(old)
        for p in parts:
            piece_velocity(p, old, span(p), whole)
        self.shapes[i:i + 1] = parts
        self.select_many(range(i, i + len(parts)), i)
        self.shapes_changed()

    def split_pieces(self, i):
        sh = self.shapes[i]
        if not self.can_split_pieces(sh):
            return
        parts = split_pieces(sh) if sh["kind"] == "curve" else split_custom(sh)
        self.replace_shape(i, parts)
        self.status.config(text=f"Split into {len(parts)} shapes")

    def split_here(self, i, at):
        """Cut a curve / polyline / line in two where it was right-clicked (at: x, y on screen; near an anchor or
        polyline point: there)."""
        sh = self.shapes[i]
        roll = self.roll
        if sh["kind"] == "curve":
            c = copy.deepcopy(sh)
            seg, t, _ = nearest(sh["pts"], roll.to_xy, at.x, at.y, gaps=sh.get("gaps", ()))
            a = self.anchor_near(sh["pts"], seg, at)
            if a is None:  # a new anchor there first
                c["pts"] = split(sh["pts"], seg, t)
                for key in ("sharp", "gaps", "splits"):
                    if c.get(key):
                        c[key] = [x + 1 if x > seg else x for x in c[key]]
                c["sharp"] = sorted(set(c.get("sharp", [])) | {seg + 1})  # (a corner there, so each half keeps its shape)
                a = seg + 1
            got = split_at(c, a)
        else:
            got = self.split_line(sh, at)
        if not got:
            self.status.config(text="Can't split there (that's an end)")
            return
        self.replace_shape(i, list(got))
        self.status.config(text="Split in two")

    def anchor_near(self, pts, seg, at):
        """The segment's anchor the right-click was on (near), or None."""
        d, a = min((math.hypot(self.roll.to_xy(pts[3 * a])[0] - at.x, self.roll.to_xy(pts[3 * a])[1] - at.y), a)
                   for a in (seg, seg + 1))
        return a if d <= TOUCH_PX * self.scale else None

    def split_line(self, sh, at):
        """A line / polyline cut in two: at the point right-clicked on, or on the nearest part of it."""
        roll = self.roll
        pts = sh["pts"]
        best = None
        for j, (a, b) in enumerate(zip(pts, pts[1:])):
            ax, ay, bx, by = roll.t2x(a[0]), roll.p2y(a[1]), roll.t2x(b[0]), roll.p2y(b[1])
            L = (bx - ax) ** 2 + (by - ay) ** 2
            u = 0.0 if L == 0 else max(0.0, min(1.0, ((at.x - ax) * (bx - ax) + (at.y - ay) * (by - ay)) / L))
            d = math.hypot(ax + (bx - ax) * u - at.x, ay + (by - ay) * u - at.y)
            if best is None or d < best[0]:
                best = (d, j, u)
        _, j, u = best
        a, b = pts[j], pts[j + 1]
        seg_px = math.hypot(roll.t2x(b[0]) - roll.t2x(a[0]), roll.p2y(b[1]) - roll.p2y(a[1]))
        if u * seg_px <= TOUCH_PX * self.scale:
            cut, k = list(a), j
        elif (1 - u) * seg_px <= TOUCH_PX * self.scale:
            cut, k = list(b), j + 1
        else:
            cut, k = [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u], None
        if k is not None and k in (0, len(pts) - 1):
            return None
        left = pts[:j + 1] + [cut] if k is None else pts[:k + 1]
        right = [cut] + pts[j + 1:] if k is None else pts[k:]
        out = []
        tms = split_tumour(sh.get("tumour"), left, right)  # (the bumps stay where they were)
        for half, tm in zip((left, right), tms):
            new = copy.deepcopy({key: v for key, v in sh.items() if key not in ("pts", "tumour")})
            new["pts"] = [list(p) for p in half]
            new["kind"] = "line" if len(half) == 2 else "poly"
            if tm:
                new["tumour"] = tm
            out.append(new)
        return out
