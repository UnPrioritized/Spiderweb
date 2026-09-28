"""Join (selected lines / polylines / freehand strokes / curves / arcs -> one curve) and Split (a joined curve back
into pieces, a curve / polyline / line cut in two where it was right-clicked, a custom shape into its separate
drawings). The maths is in notes/joined.py."""

import copy
import math

from notes.bezier import anchor_count, nearest, split
from notes.joined import join_shapes, piece_velocity, sections, split_at, split_custom, split_pieces, custom_groups
from notes.tumour import LINE_KINDS
from roll.roll_shared import cached_path

TOUCH_PX = 6  # ends closer than this on screen count as touching


def span(sh):
    bs = [b for b, _ in cached_path(sh)]
    return min(bs), max(bs)


class JoinSplit:
    """Mixed into App."""

    def can_join(self):
        return len(self.sels) >= 2 and all(self.shapes[i]["kind"] in LINE_KINDS for i in self.sels)

    def join_selected(self):
        if not self.can_join() or self.roll.sx is None:
            return
        roll = self.roll

        def touch(p, q):
            return math.hypot(roll.t2x(p[0]) - roll.t2x(q[0]), roll.p2y(p[1]) - roll.p2y(q[1])) <= TOUCH_PX

        order = sorted(self.sels)
        new = join_shapes([self.shapes[i] for i in order], roll.sy / roll.sx, touch)
        if new is None:
            return
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
        if old["kind"] != "custom":
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
        return a if d <= TOUCH_PX + 2 else None

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
        if u * seg_px <= TOUCH_PX + 2:
            cut, k = list(a), j
        elif (1 - u) * seg_px <= TOUCH_PX + 2:
            cut, k = list(b), j + 1
        else:
            cut, k = [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u], None
        if k is not None and k in (0, len(pts) - 1):
            return None
        left = pts[:j + 1] + [cut] if k is None else pts[:k + 1]
        right = [cut] + pts[j + 1:] if k is None else pts[k:]
        out = []
        for half in (left, right):
            new = copy.deepcopy({key: v for key, v in sh.items() if key != "pts"})
            new["pts"] = [list(p) for p in half]
            new["kind"] = "line" if len(half) == 2 else "poly"
            out.append(new)
        return out
