"""Piano roll: the selected custom shape's box (resize, turn) and placing custom shapes.
Worked out in screen units (beats * sx, pitch * sy, no scroll offset), so turning looks right on screen."""

import copy
import math
import tkinter as tk

from notes.basics import seg_dist
from notes.custom import box_drawn, box_pts, even_on, frame_from_box
from roll.roll_shared import SELECT_CURSOR, SHIFT
from window import look


def tidy(pts):
    """Frame points without the float noise screen units leave (a side meant for 103.5 at 103.5000000000001 gave
    the row above a stray 1-tick note, user)."""
    return [[round(b, 9) + 0.0, round(p, 9) + 0.0] for b, p in pts]


class CustomBox:
    """Mixed into PianoRoll."""

    @staticmethod
    def inside_strokes(strokes, b, p):
        """Even-odd: inside if a line going right from (b, p) crosses the outlines an odd number of times."""
        inside = False
        for poly in strokes:
            for (xa, ya), (xb, yb) in zip(poly, poly[1:]):
                if (ya <= p) != (yb <= p) and b < xa + (xb - xa) * (p - ya) / (yb - ya):
                    inside = not inside
        return inside

    @staticmethod
    def row_box(a, b, shift=False):
        """A custom shape's box being drawn from the pressed point a to the mouse's b (both key middles when snapped)
        -> its two corners with the keys on the LINES BETWEEN KEYS, both keys whole inside (like the Select box; user
        2026-11-20, PLAN "Custom shapes on the lines between keys": a corner on a key's middle left that row half
        inside = half a step). One key = flat (nothing is made, user). Shift / free keys: as they are."""
        if shift or not (float(a[1]).is_integer() and float(b[1]).is_integer()):
            return list(a), list(b)
        if a[1] == b[1]:
            return [a[0], a[1]], [b[0], b[1]]
        lo, hi = min(a[1], b[1]) - 0.5, max(a[1], b[1]) + 0.5
        return ([a[0], lo], [b[0], hi]) if a[1] < b[1] else ([a[0], hi], [b[0], lo])

    def to_su(self, b, p):
        return b * self.sx, p * self.sy

    def from_su(self, x, y):
        return [x / self.sx, y / self.sy]

    def custom_corners(self, sh):
        """The four corners of a custom shape's box (a fitted one: custom.box_pts), in beats/pitch, going round:
        u0v0, u1v0, u1v1, u0v1."""
        (b0, p0), (b1, p1), (b2, p2) = box_pts(sh)
        return [(b0, p0), (b1, p1), (b1 + b2 - b0, p1 + p2 - p0), (b2, p2)]

    def custom_hit(self, x, y):
        """("corner", k) on a corner square of the selected custom shape, ("side", k) on side k (0 bottom,
        1 right, 2 top, 3 left as drawn), ("skew", k) just outside the middle of side k, ("turn", k) just outside
        a corner, ("inside",) inside its box, or None. Only with the Select and Custom shape tools."""
        sh = self.point_shape()
        if not sh or sh["kind"] != "custom" or self.draft or self.tool_now() not in ("select", "custom"):
            return None
        corners = [(self.t2x(b), self.p2y(p)) for b, p in self.custom_corners(sh)]
        s = self.scale
        k = min(range(4), key=lambda i: math.hypot(corners[i][0] - x, corners[i][1] - y))
        d = math.hypot(corners[k][0] - x, corners[k][1] - y)
        if d <= 9 * s:
            return ("corner", k)
        for side in range(4):
            (ax, ay), (bx, by) = corners[side], corners[(side + 1) % 4]
            if seg_dist(x, y, ax, ay, bx, by) <= 7 * s:
                return ("side", side)
        inside = self.inside_box(corners, x, y)
        if inside and "notes" in sh and self.big_on_screen(sh) and self.note_owner(x, y, [self.app.sel]) is None:
            return None  # (big pasted notes: their gaps are empty space, hit_shape)
        if not inside and even_on(sh):  # (Even steps: no free turn / skew, user; they'd break the steps)
            return None
        if not inside:
            mids = [((corners[i][0] + corners[(i + 1) % 4][0]) / 2, (corners[i][1] + corners[(i + 1) % 4][1]) / 2)
                    for i in range(4)]
            m = min(range(4), key=lambda i: math.hypot(mids[i][0] - x, mids[i][1] - y))
            dm = math.hypot(mids[m][0] - x, mids[m][1] - y)
            if dm <= 20 * s and dm < d:  # nearer a side's middle than a corner: skew
                return ("skew", m)
        if d <= 24 * s and not inside:
            return ("turn", k)
        return ("inside",) if inside else None

    def frame_for_box(self, box):
        """The selected shape's frame for its shown box dragged to box: the same as box without a fitted box, else
        the frame that keeps its place in the box (inside_fit, taken at the press)."""
        return frame_from_box(box, self.inside_fit) if self.inside_fit else box

    @staticmethod
    def inside_box(corners, x, y):
        (ax, ay), (bx, by), _, (dx, dy) = corners
        ux, uy, vx, vy = bx - ax, by - ay, dx - ax, dy - ay
        det = ux * vy - uy * vx
        if abs(det) < 1e-9:
            return False
        u = ((x - ax) * vy - (y - ay) * vx) / det
        v = (ux * (y - ay) - uy * (x - ax)) / det
        return 0 <= u <= 1 and 0 <= v <= 1

    def custom_cursor(self, hit):
        kind = hit[0] if hit else None
        if kind == "turn":
            return "exchange"
        if kind in ("corner", "side", "skew"):
            c = [(self.t2x(b), self.p2y(p)) for b, p in self.custom_corners(self.app.selected())]
            k = hit[1]
            if kind == "corner":  # halfway between the corner's two sides, pointing out (diagonal even when long)
                dx = dy = 0
                for n in (c[(k - 1) % 4], c[(k + 1) % 4]):
                    ll = math.hypot(c[k][0] - n[0], c[k][1] - n[1]) or 1
                    dx, dy = dx + (c[k][0] - n[0]) / ll, dy + (c[k][1] - n[1]) / ll
                return self.arrow_cursor(dx, dy)
            elif kind == "skew":  # along the side
                (ax, ay), (bx, by) = c[k], c[(k + 1) % 4]
            else:  # a side moves along the neighbouring sides
                (ax, ay), (bx, by) = c[(k + 1) % 4], c[(k + 2) % 4]
            return self.arrow_cursor(bx - ax, by - ay)
        if kind == "inside" and self.tool_now() == "select":
            return "fleur"
        return {"select": SELECT_CURSOR, "text": "xterm"}.get(self.tool_now(), "crosshair")

    @staticmethod
    def arrow_cursor(dx, dy):
        """The double arrow closest to the direction (dx, dy) on screen: -, /, | or \\."""
        a = math.degrees(math.atan2(-dy, dx)) % 180
        return ("size_we", "size_ne_sw", "size_ns", "size_nw_se")[int((a + 22.5) // 45) % 4]

    def set_cursor(self, name):
        if getattr(self, "_cursor", None) != name:
            self._cursor = name
            try:
                self.config(cursor=name)
            except tk.TclError:  # (the select pointer's file can't be read: an arrow)
                self.config(cursor="arrow" if name == SELECT_CURSOR else "fleur")

    @staticmethod
    def grid_aligned(pts):
        """The box's sides run along time and pitch (not turned or skewed), so its corners can sit on the grid."""
        (b0, p0), (b1, p1), (b2, p2) = pts
        flat = lambda db, dp: abs(db) < 1e-9 or abs(dp) < 1e-9
        return flat(b1 - b0, p1 - p0) and flat(b2 - b0, p2 - p0)

    def row_lines(self, sh, orig):
        """(box, on lines) for a resize starting: an upright custom shape box whose top and bottom lie on the lines
        between keys keeps them there (resize_point); an older Circle / Polygon / library shape with them on key
        middles is moved onto the lines first, half a key out each way (user 2026-11-20: "whole box at first edit").
        Live drawings, text, pasted notes, Hz bass: as they are."""
        if (not sh or sh.get("kind") != "custom" or sh.get("text") or "notes" in sh or sh.get("hz")
                or not self.grid_aligned(orig)):
            return orig, False
        ps = sorted({p for _, p in orig})
        if len(ps) != 2:
            return orig, False
        if all(abs(p - math.floor(p) - 0.5) < 1e-9 for p in ps) or even_on(sh):
            return orig, True  # (Even steps: its flat sides go on the lines anyway; its box hugs the drawing a
            # millionth of a key inside them, or at a tip: the dragged edge on a line still, hunt 2026-11-20)
        if all(float(p).is_integer() for p in ps) and box_drawn(sh):
            lo, hi = ps
            return [[b, lo - 0.5 if p == lo else hi + 0.5] for b, p in orig], True
        return orig, False

    def keep_rows(self, orig, k, box):
        """A Ctrl corner resize (box = resize_custom's, proportions kept) of a box on the lines between keys: the
        dragged corner moved so the height is whole keys from the corner that stays (rounded out, keep_aspect) and the
        width follows (hunt 2026-11-20: a sideways Ctrl drag left the top at 86.3125)."""
        c0, c1 = self.custom_corners({"pts": orig}), self.custom_corners({"pts": box})
        stay, moved = c0[(k + 2) % 4], c1[k]
        w, h = abs(c0[1][0] - c0[0][0]) * self.sx, abs(c0[3][1] - c0[0][1]) * self.sy
        if w < 1e-9 or h < 1e-9:
            return box
        return self.resize_custom(orig, k, self.keep_aspect(stay, moved, w / h), False)

    def row_edge(self, orig, k, side, e):
        """The dragged top / bottom of a box on the lines between keys: the line past the mouse's key seen from the
        side that stays (both keys whole, like drawing it; at least one key tall). None: not a top / bottom."""
        c = self.custom_corners({"pts": orig})
        hp = c[k][1] if not side else (c[k][1] + c[(k + 1) % 4][1]) / 2
        ps = sorted({p for _, p in orig})
        if not any(abs(hp - p) < 1e-9 for p in ps):
            return None
        stay = ps[1] if abs(hp - ps[0]) < 1e-9 else ps[0]
        key = self.event_pt(e)[1]  # (a key's middle: snapped)
        return key + 0.5 if key > stay else key - 0.5

    def resize_point(self, orig, k, side, start, e):
        """Where the dragged corner / side is going (beats/pitch). A grid-aligned box snaps it onto the grid; a
        turned or skewed one moves it from where it was by the mouse's movement in whole grid steps (snapping the
        spot itself would pull it off the mouse). Shift = free. Pasted notes / images: their box's top and bottom
        snap BETWEEN key rows, where they sit (on a row's middle that row's notes came and went, user)."""
        if self.grid_aligned(orig):
            pt = self.event_pt(e)
            sh = self.app.selected()
            if sh and sh.get("kind") == "custom" and "notes" in sh and self.app.snap_beats() and not e.state & SHIFT:
                y = min(max(e.y, self.ruler_h), self.winfo_height())
                _, lo, hi = self.room_edges()
                pt[1] = min(max(round(self.y2p(y) - 0.5) + 0.5, lo - 0.5), hi + 0.5)
            elif getattr(self, "resize_rows", False) and self.app.snap_beats() and not e.state & SHIFT:
                edge = self.row_edge(orig, k, side, e)  # (a box on the lines between keys stays on them)
                if edge is not None:
                    pt[1] = edge
            return pt
        c = self.custom_corners({"pts": orig})
        b, p = c[k] if not side else [(c[k][i] + c[(k + 1) % 4][i]) / 2 for i in (0, 1)]
        db, dp = self.drag_steps(start, e)
        return [b + db, p + dp]

    def drag_steps(self, start, e):
        """How far the mouse moved since start (beats, keys): whole grid steps / keys unless Shift is held."""
        pt = self.event_pt(e, snap=False)
        db, dp = pt[0] - start[0], pt[1] - start[1]
        sb = self.app.snap_beats()
        if sb and not e.state & SHIFT:
            db, dp = round(db / sb) * sb, round(dp)
        return db, dp

    def resize_custom(self, orig, k, pt, keep_shape, side=False):
        """Frame points of a custom shape with corner k dragged to pt (beats/pitch), the opposite corner staying.
        keep_shape: keep its width/height as it was. side: k is a side instead (0 bottom, 1 right, 2 top, 3 left);
        only that side moves."""
        if side:
            cu, cv, lock = ((0, 0, "u"), (1, 0, "v"), (0, 1, "u"), (0, 0, "v"))[k]
        else:
            cu, cv, lock = (0, 1, 1, 0)[k], (0, 0, 1, 1)[k], None
        return self._resize(orig, cu, cv, lock, pt, keep_shape and not side)

    def _resize(self, orig, cu, cv, lock, pt, keep_shape):
        """cu, cv: where the dragged corner is in the box (0 or 1); lock: "u" / "v" = that size stays."""
        p0, pu, pv = (self.to_su(*q) for q in orig)
        U, V = (pu[0] - p0[0], pu[1] - p0[1]), (pv[0] - p0[0], pv[1] - p0[1])
        lu, lv = math.hypot(*U), math.hypot(*V)
        eu = (U[0] / lu, U[1] / lu) if lu > 1e-9 else (1.0, 0.0)
        ev = (V[0] / lv, V[1] / lv) if lv > 1e-9 else (-eu[1], eu[0])
        ou, ov = 1 - cu, 1 - cv  # the corner that stays put
        opp = (p0[0] + ou * U[0] + ov * V[0], p0[1] + ou * U[1] + ov * V[1])
        m = self.to_su(*pt)
        d = (m[0] - opp[0], m[1] - opp[1])
        # d split into steps along the box's own sides (they needn't be at right angles: skewed boxes)
        det = eu[0] * ev[1] - eu[1] * ev[0]
        if abs(det) < 1e-9:
            return copy.deepcopy(orig)
        a = (d[0] * ev[1] - d[1] * ev[0]) / det * (cu - ou)  # new width, signed (dragging past the other side mirrors)
        b = (eu[0] * d[1] - eu[1] * d[0]) / det * (cv - ov)
        if lock == "u":
            a = lu
        elif lock == "v":
            b = lv
        if keep_shape and lu > 1e-9 and lv > 1e-9:
            ratio = lu / lv
            if abs(a) > abs(b) * ratio:
                b = math.copysign(abs(a) / ratio, b or 1)
            else:
                a = math.copysign(abs(b) * ratio, a or 1)
        nu, nv = (eu[0] * a, eu[1] * a), (ev[0] * b, ev[1] * b)
        n0 = (opp[0] - ou * nu[0] - ov * nv[0], opp[1] - ou * nu[1] - ov * nv[1])
        return tidy([self.from_su(*n0), self.from_su(n0[0] + nu[0], n0[1] + nu[1]),
                     self.from_su(n0[0] + nv[0], n0[1] + nv[1])])

    def skew_custom(self, orig, k, db, dp):
        """Frame points of a custom shape with side k (0 bottom, 1 right, 2 top, 3 left) slid along itself by the
        part of (db beats, dp keys) that runs along it, as it looks on screen; the opposite side stays."""
        corners = [self.to_su(*q) for q in self.custom_corners({"pts": orig})]
        (ax, ay), (bx, by) = corners[k], corners[(k + 1) % 4]
        ll = math.hypot(bx - ax, by - ay)
        if ll < 1e-9:
            return copy.deepcopy(orig)
        mx, my = self.to_su(db, dp)
        t = (mx * (bx - ax) + my * (by - ay)) / ll / ll
        d = self.from_su(t * (bx - ax), t * (by - ay))
        moved = ((0, 1), (1,), (2,), (0, 2))[k]  # which frame points carry side k
        return tidy([[b + d[0], p + d[1]] if i in moved else list(orig[i]) for i, (b, p) in enumerate(orig)])

    def turn_custom(self, orig, angle):
        """Frame points of a custom shape turned by angle (radians, clockwise on screen) around its middle."""
        pts = [self.to_su(*q) for q in orig]
        (x0, y0), (x1, y1), (x2, y2) = pts
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2  # middle of the box
        c, s = math.cos(-angle), math.sin(-angle)  # screen y points down, pitch up
        return tidy([self.from_su(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c)
                     for x, y in pts])

    def screen_angle(self, sh_pts, x, y):
        """Angle of the mouse around the middle of the box (clockwise on screen)."""
        (b1, p1), (b2, p2) = sh_pts[1], sh_pts[2]
        return math.atan2(y - self.p2y((p1 + p2) / 2), x - self.t2x((b1 + b2) / 2))

    def keep_aspect(self, start, pt, aspect):
        """pt moved so the box from start has the drawing's width/height, as it looks on screen. start on a line
        between keys (row_box): the height rounded OUT to whole keys and the width following it, so the box stays on
        those lines, as round as that lets it be (hunt 2026-11-20: an exact circle's top at 75.504 gave the row it
        grazed stray notes)."""
        dx, dy = (pt[0] - start[0]) * self.sx, (pt[1] - start[1]) * self.sy
        if abs(dx) > abs(dy) * aspect:
            dy = math.copysign(abs(dx) / aspect, dy or 1)
        else:
            dx = math.copysign(abs(dy) * aspect, dx or 1)
        b0, lo, hi = self.room_edges()  # (beat 0 and the keys, or the Space past them)
        if abs(start[1] - math.floor(start[1]) - 0.5) < 1e-9:
            keys = max(1, math.ceil(abs(dy) / self.sy - 1e-6))
            top = start[1] + math.copysign(keys, dy or 1)
            top = min(max(top, lo - 0.5), hi + 0.5)  # (at the keys' ends: their outer lines)
            keys = abs(top - start[1])
            dx = math.copysign(keys * self.sy * aspect, dx or 1)
            return [max(b0, start[0] + dx / self.sx), top]
        return [max(b0, start[0] + dx / self.sx), min(max(start[1] + dy / self.sy, lo), hi)]

    def draw_custom_box(self, sh):
        """The box of the selected custom shape: dashed outline and corner squares to resize it."""
        corners = [(self.t2x(b), self.p2y(p)) for b, p in self.custom_corners(sh)]
        self.create_polygon(*[c for pt in corners for c in pt], fill="", outline=look.HANDLE, dash=(4, 3))
        r, m = 4 * self.scale, 3 * self.scale
        for k, (x, y) in enumerate(corners):
            nx, ny = corners[(k + 1) % 4]
            mx, my = (x + nx) / 2, (y + ny) / 2  # the middle of each side: drag the side
            self.create_rectangle(mx - m, my - m, mx + m, my + m, fill=look.HANDLE_FILL, outline=look.HANDLE)
        for x, y in corners:
            self.create_rectangle(x - r, y - r, x + r, y + r, fill=look.HANDLE_FILL, outline=look.HANDLE, width=2)
