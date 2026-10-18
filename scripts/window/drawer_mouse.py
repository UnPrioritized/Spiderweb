"""Mixed into Drawer: the mouse on the board (drawing, the Select tool, dragging, sticking, the eraser, right-click
menus) and copy / paste / flip / turn of the picked strokes."""

import json
import math
import tkinter as tk
from types import SimpleNamespace

import numpy as np

from files.lang import tr
from files.system import ALT
from notes.areas import COLOURS
from notes.bezier import (add_anchor, add_anchor_pair, can_delete, delete_point, drag_point, half_at, nearest, pen_handles,
                          set_symmetry)
from notes.custom import ROLES, carried_spots, carry_areas, role_of, stroke_points, takes_formula
from notes.pattern import moved_formulas
from notes.slice import is_closed
from roll.roll_shared import BOX_STILL, line_touches_box, mouse_trail, shown_points
from window.drawer_areas import AREA_FRAME
from window.formula_host import DrawerHost, formula_menu
from window.layers import pasted
from window.sticky import REACH
from window.widgets import symmetry_menu

SHIFT, CTRL = 0x1, 0x4
MOD_KEYS = ("shift_l", "shift_r", "control_l", "control_r")
STICK_RANK = {"point": 5, "cross": 4, "two": 3, "on": 2, "rest": 1, "line": 0}  # (moving a stroke: on / two / rest
# = its line)
GUIDE_REACH = 6 * REACH  # pixels: a circle this near to sticking shows where it would touch (dotted)
DRAW_TOOLS = ("line", "poly", "curve", "arc", "square", "circle", "slice")  # (the ones whose points stick)


def colour_menu(parent, var, pick, most=COLOURS):
    """A menu of the shape's own colour and colours 1 .. most (a stroke's outline colour; areas.most_colours), var =
    the one on."""
    m = tk.Menu(parent, tearoff=0)
    m.add_radiobutton(label=tr("drawer.colour_own"), value=0, variable=var, command=lambda: pick(0))
    for c in range(1, max(most, var.get()) + 1):  # (a 16th kept on a shape is still shown ticked)
        m.add_radiobutton(label=tr("drawer.area_n", n=c), value=c, variable=var, command=lambda c=c: pick(c))
    return m


class DrawerMouse:
    """Mixed into Drawer."""

    def on_press(self, e):
        self.canvas.focus_set()
        if self.follow:  # a stroke started with a click: this click finishes it
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.on_release(e, second=True)
            return
        tool = self.tool.get()
        if tool == "select":
            return self.select_press(e)
        if tool == "areas":
            return self.area_press(e)
        if tool == "slice":  # drag a line: the strokes it crosses are cut there (slice_along)
            pt = self.event_pt(e)
            self.knife, self.drag = [pt, list(pt)], ("slice", e.x, e.y)
            return self.redraw()
        j = self.curve_handle_at(e.x, e.y) if self.draft is None else None
        if j is not None:  # the selected curve's anchors and handles work with any tool
            self.push_undo()
            self.drag = ("pen", self.sel, j)
            return
        if tool == "erase":  # a click = the stroke there; a drag = a box, every stroke it touches when let go
            self.drag = ("erasebox", e.x, e.y, e.x, e.y)
            return
        pt = self.event_pt(e) if tool != "free" else None
        if tool == "poly":  # click its points, or drag each segment
            if self.draft is None:
                self.draft = {"kind": "poly", "pts": [pt, list(pt)]}
            elif self.poly_point(pt, e):
                return
            self.drag = ("segment", e.x, e.y)
            self.redraw()
            return
        if tool == "free":
            self.draft = {"kind": "poly", "pts": [self.mirror_end(self.event_pt(e, snap=False), e.state)]}
            trail = mouse_trail(e.x_root, e.y_root, None)
            self.drag = ("free", e.x, e.y, trail[-1][2] if trail else None)
            return
        if tool == "arc":  # three clicks: start, a point it passes through, end (or drag start -> end, then bend)
            if self.draft is None:
                self.draft = {"kind": "arc", "pts": [pt, list(pt)], "k": 1.0}
                self.drag = ("arcdrag", e.x, e.y)
            elif self.arc_bend:  # dragged start -> end: this click sets the point it passes through
                self.draft["pts"][1] = pt
                self.arc_bend = False
                self.commit()
                return
            elif len(self.draft["pts"]) == 2:
                self.draft["pts"][-1] = pt
                self.draft["pts"].append(list(pt))
            elif pt != self.draft["pts"][0] or pt != self.draft["pts"][1]:  # the end on the start: a whole circle
                self.draft["pts"][-1] = pt
                self.commit()
            self.redraw()
            return
        self.drag, self.drag_xy = ("box", pt, e.x, e.y), (e.x, e.y)

    def erase_release(self, drag):
        _, x0, y0, x1, y1 = drag
        if abs(x1 - x0) >= BOX_STILL or abs(y1 - y0) >= BOX_STILL:
            hit = self.pick_box(x0, y0, x1, y1)
        else:
            hit = [i for i in [self.hit_stroke(x0, y0)] if i is not None]
        if hit:
            self.push_undo()
            self.remove_strokes(hit)
            self.changed()
        else:
            self.redraw()  # (the box goes)

    def chosen(self):
        """The selected strokes' indexes, in order."""
        return sorted(self.picks | ({self.sel} if self.sel is not None else set()))

    def deselect(self):
        self.sel, self.picks, self.boxes = None, set(), []

    def remove_strokes(self, idx, partners=True):
        """Deletes those strokes (partners: and the strokes Mirror links with them); the selection keeps pointing at
        the same strokes (its boxes go if a selected one is deleted)."""
        if partners:
            idx = self.with_partners(idx)
        if set(idx) & set(self.chosen()):
            self.boxes = []
        self.keep_names()  # (the others' names don't count along again)
        keep = [i for i in range(len(self.strokes)) if i not in idx]
        new = {old: k for k, old in enumerate(keep)}
        self.strokes = [self.strokes[i] for i in keep]
        self.sel = new.get(self.sel)
        self.picks = {new[i] for i in self.picks if i in new}

    def screen_boxes(self):
        """The kept select boxes on screen: [(x0, y0, x1, y1)], x0 < x1, y0 < y1."""
        out = []
        for u0, v0, u1, v1 in self.boxes:
            (x0, y0), (x1, y1) = self.to_screen(u0, v1), self.to_screen(u1, v0)
            out.append((x0, y0, x1, y1))
        return out

    def in_boxes(self, x, y):
        """(x, y) on screen is inside a kept select box (Select tool only)."""
        return self.tool.get() == "select" and any(x0 <= x <= x1 and y0 <= y <= y1
                                                   for x0, y0, x1, y1 in self.screen_boxes())

    def pick_box(self, x0, y0, x1, y1):
        """The strokes a box on screen touches."""
        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        found = []
        for i, st in enumerate(self.strokes):
            if not self.pickable(i):
                continue
            pts = np.array([self.to_screen(u, v) for u, v in stroke_points(st)], float).reshape(-1, 2)
            if len(pts) and line_touches_box(pts[:, 0], pts[:, 1], x0, y0, x1, y1):
                found.append(i)
        return found

    def handles(self):
        """Draggable points: [(stroke index, point index or ellipse corner, u, v)], the selected stroke first.
        Long strokes (freehand) show only some of their points until selected."""
        order = ([self.sel] if self.sel is not None else []) + [i for i in range(len(self.strokes) - 1, -1, -1)
                                                              if i != self.sel]
        out = []
        for i in order:
            st = self.strokes[i]
            if not self.pickable(i):
                continue
            if st["kind"] == "ellipse":
                u0, v0, u1, v1 = st["box"]
                out += [(i, k, u, v) for k, (u, v) in enumerate(((u0, v0), (u1, v0), (u1, v1), (u0, v1)))]
            elif st["kind"] == "curve":  # its anchors and handles only when selected (anchors on top)
                out += [(i, j, *st["pts"][j]) for j, _ in reversed(pen_handles(st["pts"], i == self.sel))]
            else:
                out += [(i, j, *st["pts"][j]) for j in shown_points(len(st["pts"]), i == self.sel)]
        return out

    def is_pen_point(self, i, j):
        """A curve's anchor or handle point that isn't one of its two ends."""
        st = self.strokes[i]
        return st["kind"] == "curve" and 0 < j < len(st["pts"]) - 1

    def curve_handle_at(self, x, y):
        """The point number of the selected curve's anchor / handle point at (x, y) (not its ends), or None."""
        if self.sel is None or self.strokes[self.sel]["kind"] != "curve" or not self.pickable(self.sel):
            return None
        pts = self.strokes[self.sel]["pts"]
        r = max(7, 8 * self.scale)
        for j, kind in reversed(pen_handles(pts)):
            hx, hy = self.to_screen(*pts[j])
            if kind != "end" and abs(hx - x) <= r and abs(hy - y) <= r:
                return j
        return None

    def select_press(self, e):
        r = max(7, 8 * self.scale)
        ctrl = e.state & CTRL and not e.state & SHIFT  # (Ctrl+Shift: a drag on this side only, not Ctrl's picking)
        for i, j, u, v in self.handles():
            x, y = self.to_screen(u, v)
            if abs(x - e.x) <= r and abs(y - e.y) <= r and not ctrl:
                self.select(i)
                self.push_undo()  # (after: undo leaves the stroke grabbed selected, like the piano roll)
                if self.strokes[i]["kind"] == "ellipse":
                    self.drag = ("corner", i, j, list(self.strokes[i]["box"]))
                elif self.is_pen_point(i, j):
                    self.drag = ("pen", i, j)
                else:
                    # points in the same spot move together (a closed outline's ends, lines that meet)
                    group = [(a, b) for a, st in enumerate(self.strokes) if st["kind"] != "ellipse" and self.pickable(a)
                             for b, p in enumerate(st["pts"])
                             if not self.is_pen_point(a, b) and math.dist(p, (u, v)) < 1e-6]
                    self.drag = ("points", group)
                self.redraw()
                return
        i = self.hit_stroke(e.x, e.y)
        if i is not None and ctrl:  # Ctrl+click: in or out of the selection
            if i in self.chosen():
                rest = [k for k in self.chosen() if k != i]
                self.sel, self.picks = (rest[-1], set(rest[:-1])) if rest else (None, set())
            else:
                self.picks = set(self.chosen())
                self.sel = i
        elif i is not None or (self.chosen() and self.in_boxes(e.x, e.y)):
            # the stroke moves, and the others selected with it (inside the select box: all of them)
            if i is not None:
                self.select(i)
            self.push_undo()  # (the last item: the stroke grabbed, None = the select box's empty space)
            self.drag = ("stroke", self.event_pt(e, snap=False),
                         {k: json.dumps(self.strokes[k]) for k in self.movable()}, json.dumps(self.boxes), i)
        else:  # a select box (Ctrl = adds another to what's selected); a click = deselect
            self.drag = ("boxsel", e.x, e.y, e.x, e.y, bool(ctrl))
        self.redraw()

    def select(self, i):
        """Clicked stroke i: it's selected, with its group (user; Ctrl+click = one stroke); the others selected stay
        so when it's one of them."""
        if i in self.chosen():
            self.picks = set(self.chosen()) - {i}
        else:
            self.picks, self.boxes = set(self.mates(i)) - {i}, []
        self.sel = i

    def select_drag(self, e):
        kind = self.drag[0]
        if kind == "boxsel":
            self.drag = self.drag[:3] + (e.x, e.y) + self.drag[5:]
            return self.redraw()
        old = self.areas and json.dumps(self.shown())  # (coloured areas keep their colours: carry_areas)
        if getattr(self, "mplan", (None,))[0] is not self.drag:  # (worked out on the drag's first move)
            self.mplan = (self.drag, *self.plan_mirror())
        _, mirror, whole, pinned = self.mplan
        one_side = e.state & CTRL and e.state & SHIFT  # (Ctrl+Shift held: this side only)
        if kind == "points":
            pt = self.event_pt(e)
            for a, b in self.drag[1]:
                if self.strokes[a]["kind"] == "curve":  # a curve's end takes its handle along (Alt: pulls one out)
                    drag_point(self.strokes[a], b, pt, e.state & ALT, self.to_xy, self.from_xy,
                               exact=True)
                else:
                    self.strokes[a]["pts"][b] = list(pt)
        elif kind == "pen":
            _, i, j = self.drag
            drag_point(self.strokes[i], j, self.event_pt(e), e.state & ALT, self.to_xy, self.from_xy, exact=True)
        elif kind == "corner":
            _, i, k, (u0, v0, u1, v1) = self.drag
            corners = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
            opp, pt = corners[(k + 2) % 4], self.event_pt(e)
            if e.state & CTRL:
                pt = self.perfect(opp, pt)
            self.strokes[i]["box"] = [min(opp[0], pt[0]), min(opp[1], pt[1]), max(opp[0], pt[0]), max(opp[1], pt[1])]
        elif kind == "stroke":
            _, start, origs, boxes, _ = self.drag
            cur = self.event_pt(e, snap=False)
            du, dv = cur[0] - start[0], cur[1] - start[1]
            self.stuck = self.stuck2 = None
            if not e.state & SHIFT:  # the strokes' point / line nearest to a line sticks to it; else grid squares
                got = self.stick_move({i: json.loads(o) for i, o in origs.items()}, du, dv)
                if got:
                    du, dv = got
                else:
                    n = int(self.grid_n.get())
                    du, dv = round(du * n) / n, round(dv * n) / n
            if not one_side and (pinned[0] and du or pinned[1] and dv):  # (lying across a mirror line: it stays
                du, dv = (0 if pinned[0] else du), (0 if pinned[1] else dv)  # on it, only moving along it)
                self.stuck = self.stuck2 = None
            exact =(lambda c: c) if self.stuck else (lambda c: round(c, 5))  # (stuck: exactly on that line)
            for i, orig in origs.items():
                st = json.loads(orig)
                if st["kind"] == "ellipse":
                    u0, v0, u1, v1 = st["box"]
                    st["box"] = [exact(u0 + du), exact(v0 + dv), exact(u1 + du), exact(v1 + dv)]
                else:
                    st["pts"] = [[exact(u + du), exact(v + dv)] for u, v in st["pts"]]
                link = (st.get("layer") or {}).get("mirror")
                if link and link["id"] in whole:  # (all its mirrored strokes moved: their mirror line goes along)
                    link["at"] = [exact(link["at"][0] + du), exact(link["at"][1] + dv)]
                self.strokes[i] = st
            self.boxes = [[u0 + du, v0 + dv, u1 + du, v1 + dv] for u0, v0, u1, v1 in json.loads(boxes)]
        if not one_side:
            self.mirror_drag(mirror)
        if old and old != json.dumps(self.shown()):
            frame = AREA_FRAME
            self.areas = carry_areas({"strokes": json.loads(old), "pts": frame, "areas": self.areas},
                                     {"strokes": self.shown(), "pts": frame, "areas": self.areas})
        self.redraw()

    def select_release(self):
        if self.drag[0] == "boxsel":
            _, x0, y0, x1, y1, adding = self.drag
            self.drag = None
            if abs(x1 - x0) < BOX_STILL and abs(y1 - y0) < BOX_STILL:  # a click
                if not adding:
                    self.deselect()
                return self.redraw()
            got = self.pick_box(x0, y0, x1, y1)
            (u0, v1), (u1, v0) = self.from_screen(min(x0, x1), min(y0, y1)), self.from_screen(max(x0, x1),
                                                                                                max(y0, y1))
            # it stays (until a click outside it), with the boxes before when Ctrl added it
            self.boxes = (self.boxes if adding else []) + ([[u0, v0, u1, v1]] if got else [])
            got = sorted(set(got) | set(self.chosen() if adding else []))
            self.sel = got[-1] if got else None  # (the top one: its handles show if it's a curve)
            self.picks = set(got[:-1])
            return self.redraw()
        if self.undo_stack and self.undo_stack[-1][0] == self.snap():
            self.undo_stack.pop()  # clicked without moving anything (the undone steps stay redoable)
            self.redo_stack = self.redo_kept
        else:
            self.changed(settle=False)  # (the drag took the colours along: carry_areas)

    def holding(self):
        """The mouse holds a stroke, points, a curve handle or an ellipse corner (a drag that changes strokes)."""
        return bool(self.drag) and self.drag[0] in ("stroke", "points", "pen", "corner")

    def let_go(self):
        """A held drag ends as if the mouse was let go there (Esc, a tool key, Delete): what it moved is one
        step (undo, "not saved yet")."""
        if self.holding():
            self.select_release()
        self.drag = self.stuck = self.stuck2 = None

    def delete_dragged(self):
        """Delete while the mouse holds something (like the piano roll): the drag ends where it is, then only the
        stroke grabbed goes (grabbed by the select box's empty space: all it holds); the rest stay selected."""
        kind = self.drag[0]
        if kind == "stroke":
            gone = list(self.drag[2]) if self.drag[4] is None else [self.drag[4]]
        else:
            gone = [self.drag[1] if kind in ("pen", "corner") else self.sel]
        self.let_go()
        self.push_undo()
        self.remove_strokes(gone)
        self.changed()

    def delete_selected_stroke(self):
        if self.holding():
            return self.delete_dragged()
        if self.movable() and self.tool.get() in ("select", "slice"):  # (locked / hidden ones stay: Del in the list)
            self.push_undo()
            self.remove_strokes(self.movable())
            self.changed()

    def targets(self):
        """What copy / flip / turn work on: the selected strokes, or the whole drawing when nothing is selected
        (not hidden or locked ones)."""
        return self.movable() if self.chosen() else [i for i in range(len(self.strokes)) if self.pickable(i)]

    def copy(self, partners=False):
        """partners: with the strokes Mirror links with them (Cut deletes those too)."""
        if self.strokes:
            idx = self.with_partners(self.targets()) if partners else self.targets()
            self.clipboard = json.dumps([self.strokes[i] for i in idx])
            self.pastes = 0

    def cut(self):
        """Ctrl+X: the selected strokes copied, then deleted (nothing selected: nothing, not the whole drawing)."""
        if self.chosen() and self.movable() and not self.holding() and self.tool.get() in ("select", "slice"):
            self.copy(partners=True)
            self.delete_selected_stroke()

    def paste(self):
        """Paste one grid square further down and right each time, so it doesn't sit exactly on the copy."""
        if not self.clipboard or self.draft:
            return
        self.pastes += 1
        d = self.pastes / int(self.grid_n.get())
        copied = json.loads(self.clipboard)
        new = [pasted(self.map_stroke(st, lambda u, v: (u + d, v - d))) for st in copied]
        links, ids = {}, [self.next_link_id()]  # (a stroke pasted with its mirror copies: linked again, as a new set)
        for st, p in zip(copied, new):
            link = (st.get("layer") or {}).get("mirror")
            if link:
                links.setdefault(link["id"], (link, []))[1].append(p)
        for link, group in links.values():
            self.link_up(group, link["ways"], [link["at"][0] + d, link["at"][1] - d], ids)
        self.push_undo()
        self.strokes += new
        self.sel = len(self.strokes) - 1  # the pasted strokes are selected, so they can be moved together
        self.picks, self.boxes = set(range(len(self.strokes) - len(new), self.sel)), []
        self.changed()

    def map_stroke(self, st, fn, exact=False):
        """The stroke with every point moved by fn(u, v) -> (u, v) (exact: not rounded to 5 decimals)."""
        def pt(u, v):
            return list(fn(u, v)) if exact else [round(c, 5) for c in fn(u, v)]
        if st["kind"] == "ellipse":
            (a, b), (c, d) = pt(*st["box"][:2]), pt(*st["box"][2:])
            return dict(st, box=[min(a, c), min(b, d), max(a, c), max(b, d)])
        new = dict(st, pts=[pt(u, v) for u, v in st["pts"]])  # a curve keeps its corners and symmetry
        moved_formulas(new, fn)  # (and its formulas look the same)
        return new

    def middle(self, idx):
        """The middle of the strokes' points, in grid squares (a whole or half square if they're all on the grid,
        so flipping and turning keep them on it)."""
        n = int(self.grid_n.get())
        pts = [p for i in idx for p in (self.strokes[i]["pts"] if "pts" in self.strokes[i]
                                         else [self.strokes[i]["box"][:2], self.strokes[i]["box"][2:]])]
        us, vs = [u * n for u, _ in pts], [v * n for _, v in pts]
        cu, cv = (min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2
        on_grid = all(abs(c - round(c)) < 1e-6 for c in us + vs)
        return cu, cv, on_grid, n

    def flip(self, sideways):
        idx = self.targets()
        if not idx or self.draft:
            return
        cu, cv, _, n = self.middle(idx)
        self.transform(idx, (lambda u, v: (2 * cu / n - u, v)) if sideways else (lambda u, v: (u, 2 * cv / n - v)))

    def turn(self, clockwise):
        """Turn 90° around the middle."""
        idx = self.targets()
        if not idx or self.draft:
            return
        cu, cv, on_grid, n = self.middle(idx)
        if on_grid and (round(2 * cu) + round(2 * cv)) % 2:
            # one middle on a line, the other between lines: turning would land between grid points, so turn
            # around a point half a square off (chosen so turning back, or four times round, ends where it started)
            d = 0.5 if round(2 * cu) % 2 else -0.5
            if clockwise:
                cu += d
            else:
                cv += d
        cu, cv = cu / n, cv / n
        if clockwise:
            self.transform(idx, lambda u, v: (cu + (v - cv), cv - (u - cu)), turned=True)
        else:
            self.transform(idx, lambda u, v: (cu - (v - cv), cv + (u - cu)), turned=True)

    def transform(self, idx, fn, turned=False):
        self.push_undo()
        table = self.mirror_table()
        whole = {i for i in idx if set(self.mirror_mates(i, table)) <= set(idx)}  # (all its linked strokes: the
        # line goes too)
        leads = self.one_per_link(idx)  # (Mirror: the rest of a link made from the first, so they all match)
        mirrored, kinds = set(), {}  # the strokes made from a lead, by what the change does to them (fn as seen
        for i in leads:  # in the mirror)
            for k in self.partners(i, table) if i not in whole else []:
                g = self.partner_fn(i, k)
                if g and k not in mirrored:
                    mirrored.add(k)
                    a, b = self.link_of(i), self.link_of(k)
                    key = ("h" in a["way"]) != ("h" in b["way"]), ("v" in a["way"]) != ("v" in b["way"]), tuple(a["at"])
                    kinds.setdefault(key, ((lambda g: lambda u, v: g(*fn(*g(u, v))))(g), []))[1].append(k)
        going = self.carried_areas([i for i in idx if i not in mirrored])
        going_k = [(self.carried_areas(ks), f) for f, ks in kinds.values()]  # (a copy's colour comes along too)
        moves = [fn if go else next((f for gone, f in going_k if gone[a]), None) for a, go in enumerate(going)]
        swap = {"h": "v", "v": "h", "hv": "hv", "": "", "both": "both"}
        for i in idx:
            self.strokes[i] = self.map_stroke(self.strokes[i], fn)
            if turned and self.strokes[i]["kind"] == "arc":  # still round (see arc.py)
                self.strokes[i]["k"] = 1 / self.strokes[i].get("k", 1.0)
            link = self.link_of(i)
            if link and i in whole:
                link = dict(link, at=list(fn(*link["at"])))
                if turned:  # (a left / right mirror turned is a top / bottom one)
                    link.update({key: swap[link[key]] for key in ("ways", "way") if key in link})
                self.strokes[i]["layer"] = dict(self.layer(i), mirror=link)
        self.mirror_from(leads)
        self.areas = [[*(round(x, 5) for x in f(u, v)), c] if f else [u, v, c]
                      for (u, v, c), f in zip(self.areas, moves)]
        boxes = []
        for u0, v0, u1, v1 in self.boxes:  # the select boxes go along
            us, vs = zip(*(fn(u, v) for u, v in ((u0, v0), (u1, v1))))
            boxes.append([min(us), min(vs), max(us), max(vs)])
        self.boxes = boxes
        self.changed(settle=False)

    def carried_areas(self, idx):
        """For each coloured area: whether it goes along when strokes idx are flipped / turned (all of them: the whole
        drawing). It does when those strokes close it in more tightly than the others do (a bar turned inside a box:
        the bar's colour, not the box's)."""
        shown = self.shown_idx()  # (as the shape is: hidden strokes left out)
        return carried_spots(self.shown(), [shown.index(i) for i in idx if i in shown], self.areas, AREA_FRAME)

    def on_drag(self, e):
        self.show_position(e)
        if not self.drag:
            return
        if self.drag[0] == "areas":
            return self.area_drag(e)
        if self.drag[0] == "erasebox":
            self.drag = self.drag[:3] + (e.x, e.y)
            return self.redraw()
        if self.drag[0] == "slice":
            self.knife[1] = self.event_pt(e)
            return self.redraw()
        if self.tool.get() == "select" or self.drag[0] in ("points", "pen"):
            return self.select_drag(e)
        if self.drag[0] == "free":  # also where Windows skipped the mouse while busy (see mouse_trail)
            _, lx, ly, since = self.drag
            trail = mouse_trail(e.x_root, e.y_root, since)
            ox, oy = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
            spots = [(x - ox, y - oy) for x, y, _ in trail] or [(e.x, e.y)]
            n = len(self.draft["pts"])
            for x, y in spots:
                if math.hypot(x - lx, y - ly) >= 3:
                    self.draft["pts"].append(self.event_pt(SimpleNamespace(x=x, y=y, state=e.state), snap=False))
                    lx, ly = x, y
            self.drag = ("free", lx, ly, trail[-1][2] if trail else since)
            if len(self.draft["pts"]) > n:
                self.redraw()
            return
        if self.drag[0] in ("segment", "arcdrag"):  # a polyline's next point / an arc's end, at the mouse
            self.draft["pts"][-1] = self.event_pt(e)
            self.redraw()
            return
        tool = self.tool.get()
        self.guide, self.stuck2 = [], None
        self.drag_xy = e.x, e.y
        start, pt = self.drag[1], self.event_pt(e, stick=tool != "circle")
        if tool in ("square", "circle") and e.state & CTRL:
            pt = self.perfect(start, pt)
        self.square_sides = [1, 2]
        if tool == "square" and not e.state & (CTRL | SHIFT):  # its sides rest on lines too, like a moved stroke
            # (user): on one line, or on two at once (a smaller reach); the mouse's corner sticking to a point or a
            # crossing still comes first. Resting by moving one way only, the other way stays on the grid.
            raw = self.from_xy(e.x, e.y)
            best = self.stuck and (STICK_RANK[self.stuck[0]], -self.stuck[2])
            n = int(self.grid_n.get())
            targets, view = self.stick_targets(), self.stick_view()
            for kind, (su, sv), far, touch, sides in targets.rest_box(start, raw, view, REACH * self.scale):
                at = raw
                if kind == "rest" and 0 in (su, sv):  # (again from the grid that other way)
                    at = [raw[0], round(raw[1] * n) / n] if sv == 0 else [round(raw[0] * n) / n, raw[1]]
                    again = [g for g in targets.rest_box(start, at, view, REACH * self.scale) if g[0] == "rest"]
                    if not again or (again[0][1][1] == 0) != (sv == 0):
                        continue
                    _, (su, sv), far, touch, sides = again[0]
                if best is None or (STICK_RANK[kind], -far) > best:
                    best = STICK_RANK[kind], -far
                    pt = [at[0] + su, at[1] + sv]
                    self.stuck, self.stuck2 = ("line", touch[0], far), touch[1:] and ("line", touch[1], far) or None
                    self.square_sides = sides
        if tool == "circle" and not e.state & SHIFT:  # its line sticks, not the dragged corner (user; the pressed
            # corner sticks like any point): the circle to the mouse, grown / shrunk from that corner until it touches
            raw = self.from_xy(e.x, e.y)
            if e.state & CTRL:
                raw = self.perfect(start, raw)
            d = (raw[0] - start[0], raw[1] - start[1])
            targets, view, lines = self.circle_targets(), self.stick_view(), self.mirror_lines()
            # each counted as how far the mouse is from where it would touch: on one line / point (the nearest), or
            # resting on two lines at once (user; not with Ctrl: a perfect circle from that corner rarely can), which
            # wins within half the reach (like a moved stroke / square), so the mouse on a one-line guide point
            # sticks there unless a two-line spot is right beside it
            one = targets.touch_circle(start, d, view, GUIDE_REACH * self.scale)
            two = None if e.state & CTRL else targets.touch_two(start, d, view, GUIDE_REACH * self.scale)
            if two and two[1] <= REACH * self.scale / 2:
                (t1, _), (t2, _) = two[2]
                self.stuck, self.stuck2 = ("line", t1, two[1]), ("line", t2, two[1])
                pt = list(two[0])
            elif one and one[2] <= REACH * self.scale:
                self.stuck, s = one[:3], one[3]
                pt = [start[0] + s * d[0], start[1] + s * d[1]]
            # where to point the mouse for it to touch: a dotted purple point on the touched stroke's shape moved to
            # pass through it (faint, dotted); shown while it's stuck too (user)
            if one:
                self.guide.append(self.guide_at((start[0] + one[3] * d[0], start[1] + one[3] * d[1]),
                                                [(one[1], one[4])], lines))
            if two:
                self.guide.append(self.guide_at(two[0], two[2], lines))
        (u0, v0), (u1, v1) = start, pt
        if tool == "line":
            self.draft = {"kind": "poly", "pts": [start, pt]}
        elif tool == "curve":  # a gentle S-curve to start with, like on the piano roll
            mid = round((u0 + u1) / 2, 5)
            self.draft = {"kind": "curve", "pts": [start, [mid, v0], [mid, v1], pt]}
        elif tool == "square":
            self.draft = {"kind": "poly", "pts": [[u0, v0], [u1, v0], [u1, v1], [u0, v1], [u0, v0]]}
        else:
            self.draft = {"kind": "ellipse", "box": [min(u0, u1), min(v0, v1), max(u0, u1), max(v0, v1)]}
        self.redraw()

    def on_release(self, e, second=False):
        """second: the click that finishes a stroke started with a click (see follow)."""
        self.stuck = self.stuck2 = None  # (the mark goes; hovering shows it again)
        self.guide = []
        if self.drag and (self.tool.get() == "select" or self.drag[0] in ("points", "pen")):
            self.select_release()
        drag, self.drag = self.drag, None
        if drag and drag[0] == "erasebox":
            return self.erase_release(drag)
        if drag and drag[0] == "slice":
            knife, self.knife = self.knife, None
            if knife and knife[0] != knife[1]:
                return self.slice_along(*knife)
            return self.redraw()
        if not drag or drag[0] == "areas":
            return
        still = drag[0] in ("box", "segment", "arcdrag") and abs(e.x - drag[-2]) < 4 and abs(e.y - drag[-1]) < 4
        if still and drag[0] == "box":
            if second:  # clicked twice in the same spot: nothing
                return self.cancel_draft()
            self.follow = drag  # a click, not a drag: the stroke follows the mouse until the next click
            return
        if drag[0] == "segment" and not still:  # dragged: the point goes where it was let go
            self.poly_point(self.event_pt(e), e)
            return
        if drag[0] == "arcdrag" and not still:  # dragged start -> end: now it bends with the mouse until a click
            (u0, v0), end = self.draft["pts"]
            self.draft["pts"] = [[u0, v0], [round((u0 + end[0]) / 2, 5), round((v0 + end[1]) / 2, 5)], end]
            self.arc_bend = True
            self.redraw()
            return
        if not self.draft or drag[0] in ("segment", "arcdrag"):
            return
        st = self.draft
        if drag[0] == "free":
            pts = st["pts"]
            if len(pts) < 2:
                return self.cancel_draft()
            fx, fy = self.to_screen(*pts[0])
            if len(pts) >= 3 and math.hypot(e.x - fx, e.y - fy) < 12 * self.scale:
                pts.append(list(pts[0]))  # let go near the start: closed
            else:
                pts[-1] = self.mirror_end(pts[-1], e.state)
            return self.commit(pts)
        if st["kind"] == "ellipse":
            u0, v0, u1, v1 = st["box"]
            ok = min(u1 - u0, v1 - v0) * self.px() >= 1  # (not one under a pixel: it can't be seen)
        else:
            us, vs = [p[0] for p in st["pts"]], [p[1] for p in st["pts"]]
            wide, tall = max(us) > min(us), max(vs) > min(vs)
            ok = (wide or tall) if st["kind"] == "curve" or self.tool.get() == "line" else (wide and tall)
        if ok:
            self.commit()
        else:
            self.cancel_draft()

    def on_double(self, e):
        if self.draft and self.tool.get() == "poly":
            self.finish_poly()
        else:
            self.on_press(e)

    def on_motion(self, e):
        self.show_position(e)
        cursor = "fleur" if self.draft is None and (self.curve_handle_at(e.x, e.y) is not None or (
            self.chosen() and self.in_boxes(e.x, e.y))) else "crosshair"
        if str(self.canvas.cget("cursor")) != cursor:
            self.canvas.config(cursor=cursor)
        if self.follow:  # a stroke started with a click follows the mouse
            self.drag, self.follow = self.follow, None
            self.on_drag(e)
            self.follow, self.drag = self.drag, None
        elif self.draft and self.tool.get() in ("poly", "arc"):
            self.draft["pts"][1 if self.arc_bend else -1] = self.event_pt(e)
            self.redraw()
        elif self.tool.get() in DRAW_TOOLS and self.draft is None:  # where a press would stick: the mark
            was = self.stuck
            self.event_pt(e)
            if self.stuck != was:
                self.redraw()
        elif self.stuck:
            self.stuck = None
            self.redraw()
        if self.tool.get() == "areas":
            lab = self.area_at(e)
            if lab != self.hover:
                self.hover = lab
                self.redraw()
            if lab is not None:
                c = self.area_paint(self.area_info()[0])[lab]
                self.pos_label.config(text=tr("drawer.area_normal") if c < 0 else tr("drawer.area_empty") if c == 0
                                      else tr("drawer.area_n", n=int(c)))

    def on_leave(self, e):
        """The mouse left the board: no sticking mark, no area darkened under it (user: it stayed dark)."""
        stuck, hover = self.stuck and not self.drag, self.hover is not None
        if stuck:
            self.stuck = None
        if hover:
            self.hover = None
            self.pos_label.config(text="")
        if stuck or hover:
            self.redraw()

    def mod_changed(self, e, down):
        """Shift / Ctrl pressed or let go while a square / circle / line / curve is dragged out (or follows the mouse
        after a click): done again where the mouse is, so Shift drops the sticking and Ctrl makes it perfect at once."""
        held = self.drag if self.drag and self.drag[0] == "box" else self.follow
        if not (held and held[0] == "box" and self.drag_xy):
            return
        bit = SHIFT if e.keysym.lower().startswith("shift") else CTRL
        state = (e.state | bit) if down else (e.state & ~bit)  # (a key event's state is from before it)
        spot = SimpleNamespace(x=self.drag_xy[0], y=self.drag_xy[1], state=state)
        if self.drag:
            self.on_drag(spot)
        else:
            self.drag, self.follow = self.follow, None
            self.on_drag(spot)
            self.follow, self.drag = self.drag, None

    def right_click(self, e):
        """Finishes a polyline being drawn. Otherwise, like on the piano roll: on the selected curve's anchor =
        remove it, on a handle dot = pull it back in; near a stroke = its menu; empty space = deselect."""
        self.canvas.focus_set()  # (keys go to the drawing now, not the shape list)
        if self.holding():  # (the left button holds a stroke: nothing, its menu would work on it mid-move)
            return
        if self.erasing() or self.knife:  # an eraser box / Slice line being dragged: dropped (like Esc)
            return self.cancel_draft()
        if self.draft or self.follow:
            if self.draft and self.draft["kind"] == "poly" and self.tool.get() == "poly":
                return self.finish_poly()
            return self.cancel_draft()
        if self.tool.get() == "areas":  # an area back to how Fill / Spam fill it as normal
            lab = self.area_at(e)
            if lab is not None:
                self.set_area(lab, None, None)
            return
        j = self.curve_handle_at(e.x, e.y)
        if j is not None:
            st = self.strokes[self.sel]
            what = can_delete(st, j)
            if what == "middle":
                self.pos_label.config(text=tr("drawer.the_middle_anchor_of_a_symmetric"))
                return
            if what:
                self.push_undo()
                # a curve across the mirror line: its mirror point too (the later anchor first)
                pair = {j, len(st["pts"]) - 1 - j} if self.own_turn(self.sel) else {j}
                for k in sorted(pair, reverse=True):
                    delete_point(st, k, self.to_xy, exact=True)
                self.mirror_from([self.sel])
                return self.changed()
        chosen = self.chosen()
        if chosen and self.in_boxes(e.x, e.y):  # inside the select box: what works on all of them
            return self.show_menu(e, chosen[0]) if len(chosen) == 1 else self.show_group_menu(e)
        self.boxes = []  # (a click outside the box drops it)
        i = self.hit_stroke(e.x, e.y)
        if i is None:
            if chosen:
                self.deselect()
            self.redraw()
            return
        if i in chosen and len(chosen) > 1:  # one of several selected (Ctrl+click)
            self.redraw()
            return self.show_group_menu(e)
        self.select(i)
        self.redraw()
        if len(self.chosen()) > 1:  # (a stroke in a group: the group's menu)
            return self.show_group_menu(e)
        self.show_menu(e, i)

    def show_group_menu(self, e):
        """The menu for several selected strokes: what works on all of them at once (roles, colour, delete: only
        the ones the board can change, like Del; the layer items: all of them)."""
        picked, idx = self.chosen(), self.movable()
        m = tk.Menu(self, tearoff=0)
        roles = {role_of(self.strokes[i]) or "both" for i in idx}
        self._role_var = tk.StringVar(value=roles.pop() if len(roles) == 1 else "")  # (kept, so the dot shows)
        for role in ("both",) + ROLES:
            m.add_radiobutton(label=tr("drawer.role_" + role), value=role, variable=self._role_var,
                              command=lambda r=role: self.set_role(idx, r))
        colours = {self.strokes[i].get("colour", 0) for i in idx if role_of(self.strokes[i]) != "cut"}
        self._colour_var = tk.IntVar(value=min(colours) if len(colours) == 1 else -1)
        m.add_cascade(label=tr("drawer.outline_colour"), menu=colour_menu(
            m, self._colour_var, lambda c: self.set_stroke_colour(idx, c), self.most_colours()),
            state="normal" if colours else "disabled")
        m.add_separator()
        m.add_command(label=tr("drawer.delete_strokes", n=len(idx)), accelerator=tr("drawer.del"),
                      command=lambda: self.delete_stroke(idx[0]), state="normal" if idx else "disabled")
        m.add_command(label=tr("drawer.copy_strokes", n=len(idx)), accelerator=tr("drawer.ctrl_c"), command=self.copy,
                      state="normal" if idx else "disabled")
        m.add_command(label=tr("drawer.paste"), accelerator=tr("drawer.ctrl_v"), command=self.paste,
                      state="normal" if self.clipboard else "disabled")
        m.add_separator()
        self.layer_menu_items(m, picked)
        self.add_flip_turn(m)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def add_flip_turn(self, m):
        m.add_separator()
        m.add_command(label=tr("drawer.flip_sideways"), accelerator=tr("drawer.ctrl_h"),
                      command=lambda: self.flip(True))
        m.add_command(label=tr("drawer.flip_upside_down"), accelerator=tr("drawer.ctrl_j"),
                      command=lambda: self.flip(False))
        m.add_command(label=tr("drawer.turn_90_left"), accelerator=tr("drawer.ctrl_left"),
                      command=lambda: self.turn(False))
        m.add_command(label=tr("drawer.turn_90_right"), accelerator=tr("drawer.ctrl_right"),
                      command=lambda: self.turn(True))

    def show_menu(self, e, i):
        st = self.strokes[i]
        m = tk.Menu(self, tearoff=0)
        if st["kind"] == "curve":
            m.add_command(label=tr("drawer.add_anchor_here"), command=lambda: self.add_curve_anchor(e))
            # one half follows the other; the half right-clicked keeps its shape
            symmetry_menu(m, st.get("sym"), lambda mode: self.set_curve_symmetry(i, mode, e))
        elif st["kind"] == "poly":
            m.add_command(label=tr("drawer.add_point_here"), command=lambda: self.add_poly_point(i, e))
        closed = is_closed(stroke_points(st))  # (cut only all the way through: the Slice tool, user)
        m.add_command(label=tr("drawer.split_closed" if closed else "drawer.split_here"),
                      command=lambda: self.split_here(i, e), state="disabled" if closed else "normal")
        if takes_formula(st):
            self._formula_picks = {}  # (kept, so the dots show)
            formula_menu(m, DrawerHost(self), self._formula_picks)
        self._role_var = tk.StringVar(value=role_of(st) or "both")  # (kept, so the dot shows)
        for role in ("both",) + ROLES:
            m.add_radiobutton(label=tr("drawer.role_" + role), value=role, variable=self._role_var,
                              command=lambda r=role: self.set_role([i], r))
        self._colour_var = tk.IntVar(value=st.get("colour", 0))
        m.add_cascade(label=tr("drawer.outline_colour"), menu=colour_menu(
            m, self._colour_var, lambda c: self.set_stroke_colour([i], c), self.most_colours()),
            state="disabled" if role_of(st) == "cut" else "normal")
        m.add_separator()
        m.add_command(label=tr("drawer.delete_stroke"), accelerator=tr("drawer.del"),
                      command=lambda: self.delete_stroke(i))
        m.add_command(label=tr("drawer.copy_stroke"), accelerator=tr("drawer.ctrl_c"), command=self.copy)
        m.add_command(label=tr("drawer.paste"), accelerator=tr("drawer.ctrl_v"), command=self.paste,
                      state="normal" if self.clipboard else "disabled")
        m.add_separator()
        self.layer_menu_items(m, [i])
        if self.mirror_mode() != "off" or self.link_of(i):
            m.add_separator()
            m.add_command(label=tr("drawer.mirror_again"), command=lambda: self.mirror_again(i),
                          state="normal" if self.mirror_mode() != "off" else "disabled")
            m.add_command(label=tr("drawer.unlink_mirror"), command=lambda: self.unlink_mirror(i),
                          state="normal" if self.link_of(i) else "disabled")
        self.add_flip_turn(m)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def add_curve_anchor(self, e, near=None):
        """A new anchor on the selected curve where it's nearest to the mouse, moved to the mouse (snapped unless
        Shift). near: only if the curve is that close (pixels)."""
        if self.sel is None or self.strokes[self.sel]["kind"] != "curve" or not self.pickable(self.sel):
            return  # (a locked / hidden curve picked in the layers list stays as it is)
        st = self.strokes[self.sel]
        seg, t, d = nearest(st["pts"], self.to_xy, e.x, e.y)
        if near is not None and d > near:
            return
        before = self.snap()
        turn = self.own_turn(self.sel)  # (a curve across the mirror line: its mirror anchor too)
        if (add_anchor_pair(st, seg, t, self.event_pt(e), turn) if turn else
                add_anchor(st, seg, t, self.event_pt(e), self.to_xy, exact=True)):
            self.push_undo(before)
            self.mirror_from([self.sel])
            self.changed()

    def add_poly_point(self, i, e):
        """A point (snapped unless Shift) put into the line's nearest part."""
        pts = self.strokes[i]["pts"]
        pt = self.event_pt(e)
        if pt in pts or len(pts) < 2:
            return
        screen = [self.to_screen(u, v) for u, v in pts]

        def dist(k):  # from the mouse to the part between points k and k + 1
            (ax, ay), (bx, by) = screen[k], screen[k + 1]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy
            t = 0 if ll == 0 else max(0, min(1, ((e.x - ax) * dx + (e.y - ay) * dy) / ll))
            return math.hypot(e.x - ax - t * dx, e.y - ay - t * dy)
        k = min(range(len(pts) - 1), key=dist)
        self.push_undo()
        pts.insert(k + 1, pt)
        self.mirror_point(i, k + 1)
        self.mirror_from([i])
        self.changed()

    def set_curve_symmetry(self, i, mode, e):
        st = self.strokes[i]
        if (st.get("sym") or None) == mode:
            return
        self.push_undo()
        set_symmetry(st, mode, half_at(st["pts"], self.to_xy, e.x, e.y), self.to_xy, exact=True)
        self.mirror_from([i])
        self.changed()

    def set_role(self, idx, role):
        """The strokes idx: outline and fill ("both") / outline only ("edge") / fill line ("cut")."""
        sts = [self.strokes[i] for i in idx if (role_of(self.strokes[i]) or "both") != role]
        if not sts:
            return
        self.push_undo()
        for st in sts:
            st.pop("role", None)
            if role != "both":
                st["role"] = role
        self.mirror_from([i for i in idx if any(self.strokes[i] is s for s in sts)])
        self.changed()

    def set_stroke_colour(self, idx, colour):
        """The strokes idx's outline notes in colour (1 .. COLOURS, like the areas') or the shape's own (0). Not
        fill lines (they make no notes)."""
        sts = [self.strokes[i] for i in idx if role_of(self.strokes[i]) != "cut"
               and self.strokes[i].get("colour", 0) != colour]
        if not sts:
            return
        self.push_undo()
        for st in sts:
            st.pop("colour", None)
            if colour:
                st["colour"] = colour
        self.mirror_from([i for i in idx if any(self.strokes[i] is s for s in sts)])
        self.changed()

    def delete_stroke(self, i):
        """Menu > Delete on stroke i: it, or the picked ones it's among (locked / hidden ones stay, like Del)."""
        gone = [k for k in (self.chosen() if i in self.chosen() else [i]) if self.pickable(k)]
        if not gone:
            return
        self.push_undo()
        self.remove_strokes(gone)
        self.changed()

    def poly_point(self, pt, e):
        """The polyline being drawn gets its next point at pt (a new one then follows the mouse). At its first
        point again (on screen, e = the mouse) it's closed and done: True."""
        pts = self.draft["pts"]
        fx, fy = self.to_screen(*pts[0])
        if len(pts) >= 3 and math.hypot(e.x - fx, e.y - fy) < 8 * self.scale:
            pts[-1] = list(pts[0])
            self.commit([p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]])
            return True
        pts[-1] = pt
        pts.append(list(pt))
        self.redraw()
        return False

    def finish_poly(self):
        if not (self.draft and self.draft["kind"] == "poly" and self.tool.get() == "poly"):
            return
        pts = []
        for p in self.draft["pts"][:-1]:  # the last point is the one following the mouse
            if not pts or p != pts[-1]:
                pts.append(p)
        if len(pts) >= 2:
            self.commit(pts)
        else:
            self.cancel_draft()

    def hit_stroke(self, x, y):
        for i in range(len(self.strokes) - 1, -1, -1):
            if not self.pickable(i):
                continue
            xy = self.screen_points(stroke_points(self.strokes[i]))
            pts = list(zip(xy[::2], xy[1::2]))
            if len(pts) == 1 and math.hypot(pts[0][0] - x, pts[0][1] - y) < 8:
                return i
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                dx, dy = bx - ax, by - ay
                ll = dx * dx + dy * dy
                t = 0 if ll == 0 else max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / ll))
                if math.hypot(x - ax - t * dx, y - ay - t * dy) < 8:
                    return i
        return None
