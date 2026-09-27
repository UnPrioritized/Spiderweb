"""Piano roll: editing funnels (curve starts, anchors + handles, links, extra lines, the wall, highlighted lines /
curves)."""

import json
import math
from tkinter import messagebox

from notes.bezier import anchor_count, difference, handle_anchor, nearest, remove_anchor, segments, split
from window.curve_dialog import CurveFormulaDialog
from notes.funnel import (box_point, box_uf, curve_box, funnel_curves, funnel_lines, line_index, new_start,
                          next_link, partners, preset_curve, remove_funnel_parts, set_shape, turned, turned_curve)
from files.mathexpr import formula
from roll.roll_shared import ALT, CTRL


def seg_dist(x, y, a, b):
    """Distance from (x, y) to the segment a-b (screen pixels)."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    u = 0 if ll == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll))
    return math.hypot(x - ax - u * dx, y - ay - u * dy)


class FunnelEditing:
    """Mixed into PianoRoll."""

    def on_funnel_line(self, sh, e, near=6):
        """(line number, where along it 0..1) of the funnel line the mouse is on, or None."""
        for line in range(len(funnel_lines(sh))):
            at = self.line_at(sh, e, line, near)
            if at is not None:
                return line, at
        return None

    def near_wall(self, sh, pt, near=8):
        """Is the point (beat, pitch) on the funnel's wall (on screen)?"""
        (xa, ya), (xb, yb) = [(self.t2x(b), self.p2y(p)) for b, p in sh["pts"][2:4]]
        x, y = self.t2x(pt[0]), self.p2y(pt[1])
        dx, dy = xb - xa, yb - ya
        ll = dx * dx + dy * dy
        u = 0 if ll == 0 else max(0, min(1, ((x - xa) * dx + (y - ya) * dy) / ll))
        return math.hypot(x - xa - u * dx, y - ya - u * dy) <= near

    def screen_dir(self, a, b):
        return (b[0] - a[0]) * self.sx, (a[1] - b[1]) * self.sy

    def arrange_funnel(self, pts):
        """[line start, line end, wall 1, wall 2] as drawn -> in order, or None if they run side by side.
        Whichever was drawn first, the more upright one is the wall, and the line starts at its end away from
        the wall."""
        (ax, ay), (bx, by) = self.screen_dir(pts[0], pts[1]), self.screen_dir(pts[2], pts[3])
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la == 0 or lb == 0 or abs(ax * by - ay * bx) < 0.2 * la * lb:  # less than ~12 degrees apart
            return None
        line, wall = [list(p) for p in pts[:2]], [list(p) for p in pts[2:]]
        if abs(ay) / la > abs(by) / lb:
            line, wall = wall, line
        if self.wall_dist(wall, line[0]) < self.wall_dist(wall, line[1]):
            line.reverse()
        return line + wall

    def wall_dist(self, wall, pt):
        """How far pt is from the (endless) wall line, on screen."""
        dx, dy = self.screen_dir(wall[0], wall[1])
        px, py = self.screen_dir(wall[0], pt)
        return abs(dx * py - dy * px) / (math.hypot(dx, dy) or 1)

    def line_at(self, sh, e, line=0, near=None):
        """Where (0..1) along a funnel's line the mouse is (snapped to the grid unless Shift), or None if it's
        further than `near` pixels from the line."""
        i = line_index(line)
        (xa, ya), (xb, yb) = [(self.t2x(b), self.p2y(p)) for b, p in sh["pts"][i:i + 2]]
        dx, dy = xb - xa, yb - ya
        ll = dx * dx + dy * dy
        if ll == 0:
            return None

        def along(x, y):
            return max(0.0, min(1.0, ((x - xa) * dx + (y - ya) * dy) / ll))
        u = along(e.x, e.y)
        if near is not None and math.hypot(e.x - xa - u * dx, e.y - ya - u * dy) > near:
            return None
        b, p = self.event_pt(e)
        return along(self.t2x(b), self.p2y(p))

    @staticmethod
    def crossing(line, wall):
        """Where the (endless) wall line crosses the (endless) line, or None if they don't."""
        (ax, ay), (bx, by) = line
        (cx, cy), (dx, dy) = wall
        d1, d2 = (bx - ax, by - ay), (dx - cx, dy - cy)
        det = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(det) < 1e-12:
            return None
        t = ((cx - ax) * d2[1] - (cy - ay) * d2[0]) / det
        return ax + t * d1[0], ay + t * d1[1]

    def curve_at(self, sh, k, end):
        """(curve, its box) of start k's curve to wall end `end`."""
        st = sh["starts"][k]
        c = st["ends"][end]
        return c, (curve_box(sh, st["at"], end, st.get("line", 0)) if c else None)

    def uf_screen(self, box, p):
        b, q = box_point(box, *p)
        return self.t2x(b), self.p2y(q)

    def screen_uf(self, box, x, y):
        return list(box_uf(box, self.x2t(x), self.y2p(y)))

    def drag_funnel(self, sh, hid, e):
        """Dragging a funnel handle (not snapped):
        a curve start slides along its line;
        an anchor moves with its handles (Alt: pulls new handles out of it, both sides alike);
        a handle point moves, and on a smooth anchor the other handle turns with it to keep the curve smooth
        (Alt: just this one, the anchor becomes a sharp corner).
        Linked curves follow along unless Ctrl is held."""
        st = sh["starts"][hid[1]]
        if hid[0] == "start":
            at = self.line_at(sh, e, st.get("line", 0))
            if at is not None:
                st["at"] = at
            return
        kind, k, end, i = hid
        c, box = self.curve_at(sh, k, end)
        if not box:
            return
        pts = c["pts"]
        before, sharp_before = [list(p) for p in pts], list(c["sharp"])
        new = self.screen_uf(box, e.x, e.y)
        if kind == "anchor":
            if e.state & ALT:
                d = [new[0] - pts[i][0], new[1] - pts[i][1]]
                pts[i + 1] = [pts[i][0] + d[0], pts[i][1] + d[1]]
                pts[i - 1] = [pts[i][0] - d[0], pts[i][1] - d[1]]
                c["sharp"] = [a for a in c["sharp"] if a != i // 3]
            else:
                d = [new[0] - pts[i][0], new[1] - pts[i][1]]
                for j in (i - 1, i, i + 1):
                    pts[j] = [pts[j][0] + d[0], pts[j][1] + d[1]]
        else:
            pts[i] = new
            a = handle_anchor(i)
            if e.state & ALT:
                if 0 < a < len(pts) - 1 and a // 3 not in c["sharp"]:
                    c["sharp"] = sorted(c["sharp"] + [a // 3])
            elif 0 < a < len(pts) - 1 and a // 3 not in c["sharp"]:
                # a smooth anchor: its other handle points the opposite way, keeping its length on screen
                other = 2 * a - i
                ax, ay = self.uf_screen(box, pts[a])
                hx, hy = self.uf_screen(box, pts[i])
                ox, oy = self.uf_screen(box, pts[other])
                d, length = math.hypot(hx - ax, hy - ay), math.hypot(ox - ax, oy - ay)
                if d > 0 and length > 0:
                    pts[other] = self.screen_uf(box, ax - (hx - ax) / d * length, ay - (hy - ay) / d * length)
        self.sync_linked(sh, k, end, before, sharp_before, e)

    def sync_linked(self, sh, k, end, before, sharp_before, e):
        """The curves linked to this one take over the points that just changed (unless Ctrl is held)."""
        if e is not None and e.state & CTRL:
            return
        c = sh["starts"][k]["ends"][end]
        n = len(c["pts"])
        changed = [j for j, (p, q) in enumerate(zip(before, c["pts"])) if p != q]
        for k2, e2, flip in partners(sh, k, end):
            c2 = sh["starts"][k2]["ends"][e2]
            if len(c2["pts"]) != n:
                continue  # made different with Ctrl: leave it be
            mine = turned_curve(c, flip)
            for j in changed:
                jj = n - 1 - j if flip else j
                c2["pts"][jj] = list(mine["pts"][jj])
            if c["sharp"] != sharp_before:
                c2["sharp"] = mine["sharp"]

    def funnel_click(self, sh, e):
        """A click on the selected funnel: on one of its lines = a new curve start there, elsewhere near the
        funnel = a new anchor on the nearest curve, moved to where you clicked (so the curve goes through there).
        Linked curves get the anchor too, unless Ctrl is held. True if something was added."""
        if sh["kind"] != "funnel" or len(sh["pts"]) < 4:
            return False
        hit = self.on_funnel_line(sh, e)
        if hit is not None:
            st = new_start(sh, hit[1], hit[0])
            if not st:
                return False
            self.app.push_undo()
            sh["starts"].append(st)
            self.app.shape_edited()
            self.app.sync_funnel()
            self.app.tips.show("funnel_links")  # the first curves: how linking works
            return True
        curves = funnel_curves(sh)
        pts = [(self.t2x(b), self.p2y(p)) for c in [sh["pts"]] + [c for _, _, c, _ in curves] for b, p in c]
        pad = 40 * self.scale
        if not curves or not (min(x for x, _ in pts) - pad <= e.x <= max(x for x, _ in pts) + pad
                              and min(y for _, y in pts) - pad <= e.y <= max(y for _, y in pts) + pad):
            return False
        best = None
        for k, end, _, _ in curves:
            c, box = self.curve_at(sh, k, end)
            seg, t, d = nearest(c["pts"], lambda p: self.uf_screen(box, p), e.x, e.y)
            if best is None or d < best[0]:
                best = d, k, end, seg, t
        _, k, end, seg, t = best
        c, box = self.curve_at(sh, k, end)
        self.app.push_undo()
        n_before = len(c["pts"])
        self.add_anchor(c, seg, t)
        i = 3 * (seg + 1)
        target = self.screen_uf(box, e.x, e.y)
        d = [target[0] - c["pts"][i][0], target[1] - c["pts"][i][1]]
        for j in (i - 1, i, i + 1):
            c["pts"][j] = [c["pts"][j][0] + d[0], c["pts"][j][1] + d[1]]
        if not e.state & CTRL:
            for k2, e2, flip in partners(sh, k, end):
                c2 = sh["starts"][k2]["ends"][e2]
                if len(c2["pts"]) != n_before:
                    continue
                nseg = len(segments(c2["pts"]))
                self.add_anchor(c2, nseg - 1 - seg if flip else seg, 1 - t if flip else t)
                mine = turned_curve(c, flip)
                ii = len(c2["pts"]) - 1 - i if flip else i
                for j in (ii - 1, ii, ii + 1):
                    c2["pts"][j] = list(mine["pts"][j])
        self.app.shape_edited()
        return True

    @staticmethod
    def add_anchor(c, seg, t):
        """An anchor at t on segment seg of curve c (its shape stays the same)."""
        c["pts"] = split(c["pts"], seg, t)
        c["sharp"] = [a + 1 if a > seg else a for a in c["sharp"]]

    @staticmethod
    def drop_anchor(c, a):
        c["pts"] = remove_anchor(c["pts"], a)
        c["sharp"] = [b - 1 if b > a else b for b in c["sharp"] if b != a]

    def add_funnel_line(self):
        """The line just drawn starts or ends on the selected funnel's wall: it becomes another line of that
        funnel (ending on the wall). True if it did."""
        sh = self.app.selected()
        a, b = self.draft["pts"]
        if not sh or sh["kind"] != "funnel" or len(sh["pts"]) < 4:
            return False
        if not (self.near_wall(sh, a) or self.near_wall(sh, b)):
            return False
        pts = self.arrange_funnel([a, b] + sh["pts"][2:4])
        if not pts or pts[2:] != sh["pts"][2:4]:
            return False
        self.cancel_draft()
        self.app.push_undo()
        sh["pts"] += pts[:2]
        self.app.shape_edited()
        self.app.build_points()
        self.app.sync_funnel()
        return True

    def delete_funnel_handle(self, sh, hid, e):
        """Right-click on a curve start (removes its curves), an anchor (removes it) or a handle point (pulls it
        back into its anchor: a sharp corner there). Linked curves do the same unless Ctrl is held."""
        k = hid[1]
        if hid[0] == "start":
            self.app.push_undo()
            del sh["starts"][k]
            self.app.parts = set()  # the curves after it moved up a number
        else:
            _, _, end, i = hid
            c = sh["starts"][k]["ends"][end]
            a = handle_anchor(i) if hid[0] == "ctrl" else i
            if a in (0, len(c["pts"]) - 1):
                return  # the handles at the start and the wall end stay (you couldn't grab them again)
            self.app.push_undo()
            if hid[0] == "anchor":
                n = anchor_count(c["pts"])
                if not e.state & CTRL:
                    for k2, e2, flip in partners(sh, k, end):
                        c2 = sh["starts"][k2]["ends"][e2]
                        if len(c2["pts"]) == len(c["pts"]):
                            self.drop_anchor(c2, n - 1 - i // 3 if flip else i // 3)
                self.drop_anchor(c, i // 3)
            else:
                before, sharp_before = [list(p) for p in c["pts"]], list(c["sharp"])
                c["pts"][i] = list(c["pts"][a])
                if a // 3 not in c["sharp"]:
                    c["sharp"] = sorted(c["sharp"] + [a // 3])
                self.sync_linked(sh, k, end, before, sharp_before, e)
        self.app.shape_edited()
        self.app.sync_funnel()

    # ------------------------------------------------------------ highlighted lines and curves
    # With one funnel selected, clicking it again (Select tool) highlights the line or curve under the mouse
    # (a curve together with its twin on the other side; Ctrl+click adds / removes just one). app.parts holds
    # ("line", n) and ("curve", start, wall end); Del, Ctrl+C/V/H/J and the right-click menu then work on them.

    def part_at(self, sh, x, y, near=6):
        """The funnel line or curve at (x, y) on screen, or None."""
        if len(sh["pts"]) < 4:
            return None
        best, best_d = None, near

        def screen(pts):
            return [(self.t2x(b), self.p2y(p)) for b, p in pts]
        found = [(seg_dist(x, y, *screen(seg)), ("line", n)) for n, seg in enumerate(funnel_lines(sh))]
        for k, end, curve, _ in funnel_curves(sh):
            pts = screen(curve)
            found.append((min(seg_dist(x, y, a, b) for a, b in zip(pts, pts[1:])), ("curve", k, end)))
        for d, part in found:
            if d < best_d:
                best, best_d = part, d
        return best

    @staticmethod
    def part_group(sh, part):
        """A clicked part: a curve comes with the curves linked to it."""
        if part[0] == "curve":
            return {part} | {("curve", k2, e2) for k2, e2, _ in partners(sh, part[1], part[2])}
        return {part}

    def funnel_parts(self):
        """(the funnel, {line numbers}, {(start, wall end)}) highlighted, or None."""
        app = self.app
        sh = app.selected()
        if not app.parts or not sh or sh["kind"] != "funnel" or len(app.sels) != 1:
            return None
        lines = {p[1] for p in app.parts if p[0] == "line" and p[1] < len(funnel_lines(sh))}
        curves = {p[1:] for p in app.parts if p[0] == "curve" and p[1] < len(sh["starts"])
                  and sh["starts"][p[1]]["ends"][p[2]]}
        return (sh, lines, curves) if lines or curves else None

    def curve_parts(self):
        got = self.funnel_parts()
        return got[2] if got else set()

    def parts_text(self):
        """'2 curves and 1 line' (what's highlighted), or ''."""
        got = self.funnel_parts()
        if not got:
            return ""
        words = [f"{n} {what}{'s' if n > 1 else ''}" for n, what in ((len(got[2]), "curve"), (len(got[1]), "line"))
                 if n]
        return " and ".join(words)

    def delete_parts(self):
        got = self.funnel_parts()
        if not got:
            return
        sh, lines, curves = got
        app = self.app
        if len(lines) >= len(funnel_lines(sh)):  # every line: the whole funnel goes
            return app.delete_selected()
        app.push_undo()
        remove_funnel_parts(sh, lines, curves)
        app.parts = set()
        app.build_points()
        app.shape_edited()
        app.sync_funnel()

    def set_curves(self, shape_of, undo=True):
        """Give every highlighted curve a new shape: shape_of(curve) -> a curve (pts + sharp). Highlighted curves
        linked to each other get it the way their link says (the same, or turned end to end)."""
        got = self.funnel_parts()
        if not got or not got[2]:
            return
        sh, _, curves = got
        if undo:
            self.app.push_undo()
        done = set()
        for k, end in sorted(curves):
            if (k, end) in done:
                continue
            c = sh["starts"][k]["ends"][end]
            new = shape_of(c)
            set_shape(c, new)
            done.add((k, end))
            for k2, e2, flip in partners(sh, k, end):
                if (k2, e2) in curves and (k2, e2) not in done:
                    set_shape(sh["starts"][k2]["ends"][e2], turned_curve(new, flip))
                    done.add((k2, e2))
        self.app.shape_edited()
        self.app.sync_funnel()

    def apply_formula(self, text):
        """A preset or saved formula (None = the default curve) onto the highlighted curves."""
        try:
            shape = preset_curve(None if text is None else formula(text))
        except ValueError as e:
            messagebox.showerror("Spiderweb", f"Can't use the formula \"{text}\":\n{e}", parent=self)
            return
        self.set_curves(lambda _: shape)

    def copy_curve(self):
        curves = self.curve_parts()
        if curves:
            k, end = min(curves)
            self.curve_clip = turned_curve(self.app.selected()["starts"][k]["ends"][end], False)
            self.app.status.config(text="Copied the curve's shape — highlight other curves and Ctrl+V to give it to them")

    def paste_curve(self):
        if self.curve_clip:
            clip = self.curve_clip
            self.set_curves(lambda _: clip)

    # ------------------------------------------------------------ links between curves

    def link_relation(self, sh, a, b):
        """How curve b looks next to curve a when linked with the same [u, f]: "Mirrored" (their boxes open to
        different sides, like a start's two curves) or "Same shape"."""
        (_, ua, va), (_, ub, vb) = self.curve_at(sh, *a)[1], self.curve_at(sh, *b)[1]
        same_side = (ua[0] * va[1] - ua[1] * va[0] > 0) == (ub[0] * vb[1] - ub[1] * vb[0] > 0)
        return "Same shape" if same_side else "Mirrored"

    def closest_link(self, sh, a, b):
        """Is curve b closer to curve a turned end to end (True) than to a as it is (False)?"""
        pa, pb = sh["starts"][a[0]]["ends"][a[1]]["pts"], sh["starts"][b[0]]["ends"][b[1]]["pts"]
        return difference(pb, turned(pa)) < difference(pb, pa)

    def link_curves(self, mode, source):
        """Link the highlighted curves: mode "auto" (each takes whichever is closer to how it looks now), "same"
        (same [u, f]) or "flip" (turned end to end); None = unlink them. source keeps its shape, the others
        follow it."""
        got = self.funnel_parts()
        if not got or not got[2]:
            return
        sh, _, curves = got
        self.app.push_undo()
        if mode is None:
            for k, end in curves:
                c = sh["starts"][k]["ends"][end]
                c.pop("link", None)
                c.pop("flip", None)
        else:
            src = source if source in curves else min(curves)
            cs = sh["starts"][src[0]]["ends"][src[1]]
            cs["link"], cs["flip"] = next_link(sh), False
            for other in sorted(curves - {src}):
                c = sh["starts"][other[0]]["ends"][other[1]]
                flip = self.closest_link(sh, src, other) if mode == "auto" else mode == "flip"
                set_shape(c, turned_curve(cs, flip))
                c["link"], c["flip"] = cs["link"], flip
        self.app.shape_edited()
        self.app.sync_funnel()

    def open_formulas(self):
        """The custom formula window, previewing on the highlighted curves as you type."""
        got = self.funnel_parts()
        if not got or not got[2]:
            return
        sh, _, curves = got
        app = self.app
        before = json.dumps(app.shapes)
        orig = {(k, end): turned_curve(sh["starts"][k]["ends"][end], False) for k, end in curves}

        def preview(shape):
            if shape:
                self.set_curves(lambda _: shape, undo=False)
            else:
                for (k, end), old in orig.items():
                    set_shape(sh["starts"][k]["ends"][end], old)
                app.shapes_changed()
                app.sync_funnel()

        def done(shape):
            preview(shape)
            if shape:
                app.push_undo(before)
        CurveFormulaDialog(app, preview, done)
