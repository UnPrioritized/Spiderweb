"""The Hz bass window's Sketch tool (S; user, 2026-10-28): a note drawn and shaped as a pitch line by hand, its
points and pieces dragged, a wobble on a piece. Everything it makes is the note's own bend line (tone["bend"], see
hz_bend.py), so the Bend tool, Copy / Paste bend and presets go on working with it. Points and curve squares
work like the Bend tool's (hz_bend.BEND_TOOLS), but a press on a note's line only picks the note: a DOUBLE-click
there adds a point (sketch_double), a double-click on a point takes it away. On empty space a press starts a new note there (grid; Shift free) and the
drag sets its end (hz_mouse's "new" drag, sketch_new_to); Ctrl+drag = a Select box.

While it's on, each note shows as a thick line where it's heard (its tune and bend; not the Pitch line / vibrato,
those are the red line's), with a dot at its start and end, a small ">" after its end and, when it bends, a faint
dashed line at its own key."""

import copy
import math
import random
import tkinter as tk

import numpy as np

from files.lang import tr, trn
from notes.hzbass import (NOTE_BEND_MOST, ON_POINT, SLIDE_BEND, WOBBLE_RATE, WOBBLE_RATE_MOST, WOBBLE_SHAPES, bend_of,
                          WOBBLE_SEED_STEP, bend_part, bends, bent_part, clean_speed, depth_at, depth_most, depth_pts,
                          fit_speed, fit_wobbles, line_at, pitch, run_copy, set_depth, speed_mean, split_wobbles, wave_end,
                          wave_start, waves_spot, wobble_keys, wobble_part, bez_piece, handles, piece_curved, side_handles,
                          split_bez, with_handles)
from roll.roll_shared import ALT, CTRL, SHIFT, note_name
from window import look
from window.hz_bend import BEND, LINE_NEAR, NEAR, drop_point, follow_wobble, set_curve, tidy_bend

SKETCH_WIDE = 0.6  # the line's thickness: this part of a key's row (between SKETCH_THIN and SKETCH_THICK px)
SKETCH_THIN, SKETCH_THICK = 3, 9
END_GAP = 10  # px: the ">" this far after a note's end (its middle)
HOLD_MS = 300  # a press on a line held still this long grabs the whole note (sketch_grab)
TOP_KEY = 127  # a whole note dragged up stops here (the notes keep keys 0..127: one past = gone, hunt 2026-10-28)
# Alt+drag curves a piece; Ctrl+Shift+drag too, everywhere: many Linux desktops take Alt+drag to move the window
# (hunt 2026-10-28, untested on Linux; its tip says so, system.WINDOWS / MAC don't)
CURVE_KEYS = CTRL | SHIFT
WOBBLE_LIFT = 12  # px: a picked piece's depth line sits this far above the piece's highest spot at depth 0
WOBBLE_STEP = 0.1  # keys: the depth goes in these steps (Shift: 0.01)
SPEED_POST = 12  # px: the speed post's height above the depth line
SPEED_DOUBLE = 60  # px: the speed post dragged this far right = twice as fast (left: half)
PHASE_STEP = 0.125  # waves: the wobble menu's Phase ◀ / ▶ move its wave this much (the plugin's 1/8, user 2026-11-13)
# the depth line's shapes (user 2026-11-13: small buttons above the wobble; the plugin's five): [[share of the run,
# share of the deepest depth(, curve)], ...]; Decay falls fast first, Swell grows slowly first
DEPTH_PRESETS = {"full": [[0.0, 1.0], [1.0, 1.0]], "fade_in": [[0.0, 0.0], [1.0, 1.0]],
                 "decay": [[0.0, 1.0, 0.6], [1.0, 0.0]], "triangle": [[0.0, 0.0], [0.5, 1.0], [1.0, 0.0]],
                 "swell": [[0.0, 0.0, -0.6], [1.0, 1.0]]}
PRESET_SIZE, PRESET_GAP = 13, 3  # px: a depth preset button's size, the room between them
DEPTH_SAMPLES = 17  # a curved piece of the depth line is drawn in this many spots
DEPTH_CURVE_PX = 40  # px: Alt+dragging a depth piece this far = a full curve (SLIDE_BEND)
DEPTH_CURVE_STICK = 0.05  # ... nearer straight than this sticks to straight (Shift: free)
SPEED_STRIP_H = 30  # px: the speed strip's height (top = the wobble's full speed, bottom = stopped; stage C)
SPEED_STEP = 0.05  # a speed point goes in these steps of full speed (Shift: 0.01)
LOCK_SIZE, LOCK_GAP = 10, 4  # px: a speed lock's size, its room from the strip's end
EVEN_SPEED = [[0.0, 1.0], [1.0, 1.0]]  # a wobble with no speed line: full speed all along
POST_DROP = 9  # px: a wobble's end post's dot this far under its depth line's depth-0 height (stage D)
POST_ROOM = 12  # px: the speed post stands this far beside a depth point it would be on (user 2026-11-13)


def curve_to(pts, j, bend, shift):
    """Piece j of a line's points (the depth or speed line) curved by `bend` (bend_of's number): straight again when
    nearer than DEPTH_CURVE_STICK (shift: free); a flat piece has nothing to curve."""
    a, b = pts[j], pts[j + 1]
    del a[2:]
    if abs(b[1] - a[1]) > 1e-9 and (abs(bend) > DEPTH_CURVE_STICK or shift):
        a.append(round(min(SLIDE_BEND, max(-SLIDE_BEND, bend)), 4))


def split_curve(bend, t):
    """A curved piece (bend_of number, an S too) split t of the way along (0..1): (first part's bend, second part's
    bend, how far up the piece the split is 0..1), each bend the one nearest the old curve over its part (a power
    curve cut isn't one again, so nearest, not exact; an S cut in its middle is exact). 0 = straight. None when the
    piece doesn't move there (flat to the split)."""
    g = float(bent_part(np.array([t]), np.array([bend]))[0])
    if not 1e-6 < g < 1 - 1e-6:
        return None
    s = np.linspace(0.0, 1.0, 33)
    first = bent_part(t * s, np.full(s.shape, bend)) / g
    second = (bent_part(t + (1.0 - t) * s, np.full(s.shape, bend)) - g) / (1.0 - g)
    return nearest_bend(first, s), nearest_bend(second, s), g


def run_points(pts, u0, u1):
    """(j0, j1): the point a stretch from u0 to u1 starts on (the last one on that spot: after a jump there) and the
    one it ends on (the first on its spot: before a jump), or None when they aren't both points (u1 > u0)."""
    j0 = max((j for j, p in enumerate(pts) if abs(p[0] - u0) < ON_POINT), default=None)
    j1 = min((j for j, p in enumerate(pts) if abs(p[0] - u1) < ON_POINT), default=None)
    if j0 is None or j1 is None or j1 <= j0 or pts[j1][0] - pts[j0][0] <= ON_POINT:
        return None
    return j0, j1


def put_point(pts, u):
    """(points, its number): a line's points with one put at u (0..1) on the line as it is (a curved piece split
    into the two curves nearest it there, as a double-click does)."""
    pts = [list(p) for p in pts]
    j = min(len(pts) - 1, max(1, sum(p[0] <= u for p in pts)))
    return pts, put_at(pts, j, u, 4)


def put_at(pts, j, u, places=4):
    """A point put at u between points j - 1 and j of a bend line (pts changed in place), on the line as it is: a
    curved piece split into the two curves nearest it there (hunt 2026-10-28), a Bézier piece split exactly (stage
    H), the points' other handles kept. Its number (j)."""
    a, b = pts[j - 1], pts[j]
    if b[0] > a[0] and bez_piece(a, b):
        a_h, mid, m_h, b_h = split_bez(a, b, u)
        pts[j - 1], pts[j] = with_handles(a, a_h), with_handles(b, b_h)
        pts.insert(j, with_handles([round(u, 9), round(mid[1], places)], m_h))
        return j
    curves = (split_curve(bend_of(a), (u - a[0]) / (b[0] - a[0]))
              if bend_of(a) and a[1] != b[1] and b[0] > a[0] else None)
    if curves:
        h = handles(a)
        pts[j - 1] = a = with_handles(a[:2] + ([curves[0]] if curves[0] else []), h)
        pts.insert(j, [round(u, 9), round(a[1] + (b[1] - a[1]) * curves[2], places)] +
                   ([curves[1]] if curves[1] else []))
    else:
        pts.insert(j, [round(u, 9), round(float(line_at(pts, u)), places)])
    return j


def nearest_bend(want, s):
    """The single curve (-SLIDE_BEND..SLIDE_BEND) whose shape over s is nearest want; 0 when straight is."""
    return nearest_bends(np.asarray(want, float)[None, :], s)[0]


def nearest_bends(wants, s):
    """nearest_bend for each row of wants at once (a list of curves; Bake's quarter waves, hunt 2026-10-28)."""
    bs = np.linspace(-SLIDE_BEND, SLIDE_BEND, 381)
    shapes = bent_part(s[None, :], bs[:, None])
    best = np.array([((shapes - w[None, :]) ** 2).sum(1).argmin() for w in wants], int).reshape(-1)
    lo, hi = bs[np.maximum(0, best - 1)], bs[np.minimum(len(bs) - 1, best + 1)]
    for _ in range(30):  # (narrowed down between its neighbours: golden section)
        m1, m2 = hi - (hi - lo) * 0.618, lo + (hi - lo) * 0.618
        e1 = ((bent_part(s[None, :], m1[:, None]) - wants) ** 2).sum(1)
        e2 = ((bent_part(s[None, :], m2[:, None]) - wants) ** 2).sum(1)
        lo, hi = np.where(e1 < e2, lo, m1), np.where(e1 < e2, m2, hi)
    return [0.0 if abs(b) < 1e-3 else b for b in (round(float(b), 4) for b in (lo + hi) / 2)]


def put_run(n, at, run):
    """Note n's wobble over the stretch at (u0, u1) = run: every wobble inside it replaced (hunt 2026-11-13: one
    there that didn't match the stretch exactly stayed, and a second one was laid on top of it)."""
    a, b = at
    n["wobble"] = sorted([w for w in n.get("wobble") or () if not (w["at"][0] < b - ON_POINT and w["at"][1] > a + ON_POINT)]
                         + [run], key=lambda w: w["at"])


BAKE_GAP = 1e-10  # bake_wobble: a jump's two sides read this far (parts of the note) before / after it


def bake_wobble(n, run):
    """Note n's bend line with its wobble run (point to point, over one piece or several: stage D) made into
    ordinary bend points: a point at every quarter wave (saw / square: every half, with their jumps as two points at
    one spot) and at every bend point under it, each piece the curve nearest the wave there (on the line under it,
    its fade too); a wave not back at its line where the run starts / ends = a jump there, as heard."""
    pts = [list(p) for p in n.get("bend") or ([0.0, 0.0], [1.0, 0.0])]
    u0, u1 = run["at"]
    j, j1 = run_points(pts, u0, u1)
    span, rate, phase = n["len"], run["rate"], wave_start(run)

    def f(u):  # (as heard: the line + the wave; u an array, all at once: hunt 2026-10-28, Bake froze for seconds)
        u = np.asarray(u, float)
        return line_at(pts, u) + wobble_keys([run], u, span)
    # (a speed line or a line under it that isn't flat: eighth waves, the line's own slope / curve bends a quarter
    # wave out of a single curve's reach; stage D: 6-10 % of the depth off on a steep piece, 1-3 % now)
    sloped = (any(abs(p[1] - pts[j][1]) > 1e-9 or piece_curved(pts, k) for k, p in enumerate(pts[j:j1], j))
              or abs(pts[j1][1] - pts[j][1]) > 1e-9)
    q = (2 if run.get("shape") in ("saw", "square") else 4) * (2 if run.get("speed") or sloped else 1)
    first, last = math.floor(phase * q) + 1, math.ceil(wave_end(run, span) * q)
    if run.get("speed"):  # (a speed line: each eighth wave where its waves get there (a changing pace bends a quarter
        # wave out of shape), + where its pace turns)
        breaks = waves_spot(run, np.arange(first, last) / q - phase, span)
        more = [u0 + p[0] * (u1 - u0) for p in run["speed"][1:-1]]
        breaks = np.unique(np.concatenate([breaks, [u for u in more if u0 < u < u1]]))
        breaks = breaks[(breaks > u0 + 1e-9) & (breaks < u1 - 1e-9)]
    else:
        breaks = u0 + (np.arange(first, last) / q - phase) / (rate * span)
    inner = [p[0] for p in pts[j + 1:j1] if u0 + 1e-9 < p[0] < u1 - 1e-9]  # (the bend's own points under it: its
    if inner:                                                            # corners / jumps on the spot)
        breaks = np.unique(np.concatenate([breaks, inner]))
    start = j == 0  # (the note's own start: no jump, nothing before it; its end: the end point, as heard)
    lefts, rights = f(breaks - BAKE_GAP).tolist(), f(breaks + BAKE_GAP).tolist()
    a0, a1 = (float(v) for v in f([u0 + BAKE_GAP, u1 - BAKE_GAP]))
    nodes = ([(u0, a0 if start else pts[j][1], a0)] + list(zip(breaks.tolist(), lefts, rights))
             + [(u1, a1, pts[j1][1])])
    out = []
    for u, left, right in nodes:
        out.append([u, round(left, 4)])
        if abs(left - right) > 1e-4:  # (a jump: two points at one spot)
            out.append([u, round(right, 4)])
    # (the run's end point as it was, the curve / handle after it kept; its handle back went with the baked piece)
    out[-1] = side_handles(pts[j1], False, True)
    if sloped:  # (a slope moves the wave's tops off its quarter waves: a piece going past both its ends gets a point
        # on its top / dip, a single curve never goes past its ends)
        ua = np.array([a[0] for a in out[:-1]])
        ub = np.array([b[0] for b in out[1:]])
        hi = np.maximum([a[1] for a in out[:-1]], [b[1] for b in out[1:]])
        lo = np.minimum([a[1] for a in out[:-1]], [b[1] for b in out[1:]])
        spots = ua[:, None] + (ub - ua)[:, None] * np.linspace(0.0, 1.0, 33)[None, 1:-1]  # (all at once: a fast
        v = f(spots.ravel()).reshape(spots.shape)                                          # long one, hunt 10-28)
        more = []
        for k, a in enumerate(out[:-1]):
            more.append(a)
            if ub[k] - ua[k] > 1e-9:
                m = (int(v[k].argmax()) if v[k].max() > hi[k] + 1e-4 else
                     int(v[k].argmin()) if v[k].min() < lo[k] - 1e-4 else None)
                if m is not None:
                    more.append([float(spots[k, m]), round(float(v[k, m]), 4)])
        out = more + [out[-1]]
    s = np.linspace(0.0, 1.0, 17)
    pairs = [(a, b) for a, b in zip(out, out[1:]) if b[0] - a[0] > 1e-12 and abs(b[1] - a[1]) > 1e-6]
    if pairs:
        ua, ub = np.array([a[0] for a, _ in pairs]), np.array([b[0] for _, b in pairs])
        va, vb = np.array([a[1] for a, _ in pairs]), np.array([b[1] for _, b in pairs])
        got = f((ua[:, None] + (ub - ua)[:, None] * s[None, 1:-1]).ravel()).reshape(len(pairs), -1)
        wants = np.column_stack([np.zeros(len(pairs)), (got - va[:, None]) / (vb - va)[:, None], np.ones(len(pairs))])
        for (a, _), curve in zip(pairs, nearest_bends(wants, s)):
            if curve:
                a[2:] = [curve]
    first = handles(pts[j])  # (the run's start point's handle back stays: the piece before it is as it was, stage H)
    if first and j > 0 and (first[0] or first[1]):
        out[0] = with_handles(out[0], [first[0], first[1], 0.0, 0.0] if not handles(out[0]) else
                              [first[0], first[1], *handles(out[0])[2:]])
    return pts[:j] + [[round(p[0], 9), *p[1:]] for p in out[:-1]] + [out[-1]] + pts[j1 + 1:]


class HzSketch:
    sketch_alt = False  # (Alt held: every piece shows its curve square, hz_bend.bend_squares)
    sketch_pick = None  # (tone id, u0, u1): the stretch clicked last; its wobble's depth line shows above it
    sketch_speed = None  # sketch_pick while its wobble's speed strip is open (its speed post clicked; stage C)
    wobble_same = "cycles"  # several notes' wobbles changed alike: "cycles" (as many waves) / "speed" (stage D)

    def picked_piece(self):
        """(tone number, its first point's number, u0, u1) of the stretch picked with a click (one piece, or the
        pieces its wobble runs over / Shift+clicked on: stage D, point to point), or None: nothing picked, its note no
        longer selected (one of several selected is fine: theirs change alike), its end points gone / moved. Once it's
        none of those it's FORGOTTEN (hunt 2026-10-28: a new note given the deleted one's number showed the line;
        another Hz bass / layer too)."""
        p = self.sketch_pick
        if not p:
            return None
        i = next((k for k in sorted(self.sel) if k < len(self.tones) and self.tones[k].get("id") == p[0]), None) \
            if self.sketching() else None
        got = i is not None and run_points(self.tones[i].get("bend") or ([0.0, 0.0], [1.0, 0.0]), p[1], p[2])
        if got:
            pts = self.tones[i].get("bend") or ([0.0, 0.0], [1.0, 0.0])
            return i, got[0], pts[got[0]][0], pts[got[1]][0]
        self.sketch_pick = None
        return None

    @staticmethod
    def wobble_run(n, u0, u1):
        """Note n's wobble on the piece from u0 to u1, or None."""
        return next((w for w in n.get("wobble") or ()
                     if abs(w["at"][0] - u0) < ON_POINT and abs(w["at"][1] - u1) < ON_POINT), None)

    def depth_line(self):
        """(x0, y at its start, x1, y at its end, y at depth 0) of the picked piece's depth line (amber): above the
        piece's highest spot, higher by its wobble's depth (keys) at each end; None when nothing's picked."""
        got = self.picked_piece()
        if not got:
            return None
        i, _, u0, u1 = got
        n = self.tones[i]
        pts = n.get("bend")
        if pts:  # (its own points between: a jump inside reaches its top on the spot)
            j0, j1 = run_points(pts, u0, u1)
            top = pitch(n) + max(float(np.max(line_at(pts, np.linspace(u0, u1, 65)))),
                                 max(p[1] for p in pts[j0:j1 + 1]))
        else:
            top = pitch(n)
        base = self.pitch_y(top) - WOBBLE_LIFT * self.s - self.sketch_wide() / 2
        run = self.wobble_run(n, u0, u1)
        d0, d1 = run["depth"] if run else (0.0, 0.0)
        x0, x1 = self.x_of(n["t"] + u0 * n["len"]), self.x_of(n["t"] + u1 * n["len"])
        return x0, base - d0 * self.sy, x1, base - d1 * self.sy, base

    def depth_shape(self):
        """The picked piece's depth line as drawn: (its points [[share, keys(, curve)], ...], [(x, y) of each point],
        [(x, y, piece number), ...] along it (each curved piece in DEPTH_SAMPLES bits)), or None."""
        got, piece = self.depth_line(), self.picked_piece()
        if not got:
            return None
        x0, _, x1, _, base = got
        run = self.wobble_run(self.tones[piece[0]], piece[2], piece[3])
        pts = depth_pts(run) if run else [[0.0, 0.0], [1.0, 0.0]]

        def xy(share, d):
            return x0 + (x1 - x0) * share, base - d * self.sy
        line = []
        for j, (a, b) in enumerate(zip(pts, pts[1:])):
            s = np.linspace(0.0, 1.0, DEPTH_SAMPLES if len(a) > 2 and a[1] != b[1] else 2)
            vals = a[1] + (b[1] - a[1]) * bent_part(s, bend_of(a))
            line += [(*xy(a[0] + (b[0] - a[0]) * f, v), j) for f, v in zip(s.tolist(), vals.tolist())]
        return pts, [xy(p[0], p[1]) for p in pts], line

    def speed_post(self):
        """(x, y at its foot, y at its top) of the picked piece's speed post (an upright stick in the middle of its
        depth line, dragged sideways = the wobble's speed), or None when it has no wobble."""
        got, piece = self.depth_line(), self.picked_piece()
        run = got and self.wobble_run(self.tones[piece[0]], piece[2], piece[3])
        if not run:
            return None
        x0, _, x1, _, base = got
        x = (x0 + x1) / 2
        room = POST_ROOM * self.s
        for p in depth_pts(run)[1:-1]:  # (a depth point there (Triangle's top): the post just beside it, user
            px = x0 + (x1 - x0) * p[0]   # 2026-11-13: it could only be grabbed by its top)
            if abs(px - x) < room:
                x = px + room if px + room < x1 else px - room
                break
        share = (x - x0) / (x1 - x0) if x1 > x0 else 0.5
        foot = base - float(depth_at(run, np.array([share]))[0]) * self.sy
        return x, foot, foot - SPEED_POST * self.s

    def end_posts(self):
        """[(end "start" / "end", x, y of its dot)] of the picked wobble's end posts (a short stick under each end of
        its depth line, a dot at its foot; dragged sideways = that end's bend point moves in time, stage D), [] when
        the picked stretch has no wobble."""
        got, piece = self.depth_line(), self.picked_piece()
        if not got or not self.wobble_run(self.tones[piece[0]], piece[2], piece[3]):
            return []
        x0, _, x1, _, base = got
        y = base + POST_DROP * self.s
        return [("start", x0, y), ("end", x1, y)]

    def preset_boxes(self):
        """[(name, x0, y0, x1, y1), ...] of the depth presets' small buttons (DEPTH_PRESETS) in a row above the
        picked piece's wobble (only with one), or []."""
        shape, post = self.depth_shape(), self.speed_post()
        if not shape or not post:
            return []
        size, gap = PRESET_SIZE * self.s, PRESET_GAP * self.s
        y1 = min(min(y for _, y, _ in shape[2]), post[2] - 4 * self.s) - 2 * gap
        y0 = max(self.ruler_h + gap, y1 - size)
        x = shape[1][0][0]
        return [(name, x + k * (size + gap), y0, x + k * (size + gap) + size, y0 + size)
                for k, name in enumerate(DEPTH_PRESETS)]

    def speed_strip(self):
        """(x0, top, x1, bottom) of the picked wobble's speed strip (open while its speed post was clicked, stage C;
        above the preset buttons, the depth line's width), or None."""
        if not self.sketch_speed or self.sketch_speed != self.sketch_pick:
            return None
        boxes, shape = self.preset_boxes(), self.depth_shape()
        if not boxes or not shape:
            return None
        gap, tall = PRESET_GAP * self.s, SPEED_STRIP_H * self.s
        top = boxes[0][2] - 2 * gap - tall
        if top < self.ruler_h + gap:  # (no room above (near the view's top): under the note instead, user 2026-11-13;
            i, _, u0, u1 = self.picked_piece()  # it covered the preset buttons)
            n = self.tones[i]
            pts = n.get("bend") or ([0.0, 0.0], [1.0, 0.0])
            run = self.wobble_run(n, u0, u1)
            low = pitch(n) + min(float(np.min(line_at(pts, np.linspace(u0, u1, 65)))),
                                 min(p[1] for p in pts if u0 - ON_POINT <= p[0] <= u1 + ON_POINT))
            low -= depth_most(run) if run else 0.0
            top = self.pitch_y(low) + self.sketch_wide() / 2 + 3 * gap
        return shape[1][0][0], top, shape[1][-1][0], top + tall

    def speed_shape(self):
        """The open speed strip's line: (its points [[share, speed 0..1(, curve)], ...], [(x, y) of each point],
        [(x, y, piece number), ...] along it), or None."""
        strip, piece = self.speed_strip(), self.picked_piece()
        run = strip and self.wobble_run(self.tones[piece[0]], piece[2], piece[3])
        if not run:
            return None
        x0, top, x1, bottom = strip
        pts = run.get("speed") or EVEN_SPEED

        def xy(share, v):
            return x0 + (x1 - x0) * share, bottom - (bottom - top) * v
        line = []
        for j, (a, b) in enumerate(zip(pts, pts[1:])):
            s = np.linspace(0.0, 1.0, DEPTH_SAMPLES if len(a) > 2 and a[1] != b[1] else 2)
            vals = a[1] + (b[1] - a[1]) * bent_part(s, bend_of(a))
            line += [(*xy(a[0] + (b[0] - a[0]) * f, v), j) for f, v in zip(s.tolist(), vals.tolist())]
        return pts, [xy(p[0], p[1]) for p in pts], line

    def speed_locks(self):
        """[(end "start" / "end", x0, y0, x1, y1), ...] of the open speed strip's two locks (just outside its ends)."""
        strip = self.speed_strip()
        if not strip:
            return []
        x0, top, x1, bottom = strip
        size, gap = LOCK_SIZE * self.s, LOCK_GAP * self.s
        y0 = (top + bottom) / 2 - size / 2
        return [("start", x0 - gap - size, y0, x0 - gap, y0 + size), ("end", x1 + gap, y0, x1 + gap + size, y0 + size)]

    @staticmethod
    def locked(run, end):
        """True when this end ("start" / "end") of run's speed line is locked."""
        lock = (run or {}).get("lock")
        return lock == "both" or lock == end

    @staticmethod
    def line_hit(spots, line, x, y, r):
        """("pt", k) a line's point k / ("on", j) its piece j at (x, y) within r px, else None (depth / speed line)."""
        for k in range(len(spots) - 1, -1, -1):
            if abs(x - spots[k][0]) <= r and abs(y - spots[k][1]) <= r:
                return "pt", k
        best = None
        for (ax, ay, j), (bx, by, j2) in zip(line, line[1:]):
            if j != j2:
                continue
            dx, dy = bx - ax, by - ay
            f = min(1.0, max(0.0, ((x - ax) * dx + (y - ay) * dy) / max(dx * dx + dy * dy, 1e-9)))
            far = math.hypot(ax + f * dx - x, ay + f * dy - y)
            if far <= r and (best is None or far < best[0]):
                best = far, j
        return ("on", best[1]) if best else None

    def depth_hit(self, x, y):
        """What of the picked piece's depth line is at (x, y): "speed" (the speed post's top), ("preset", name) a
        preset button, ("pt", k) its point k (the first / last = that end's depth: a fade), ("line", j) on its piece
        j (dragged: all of it deeper / shallower; Alt: that piece curved), ("post", "start" / "end") an end post
        (stage D: sideways = that end's point moves in time); its open speed strip: ("lock", end),
        ("spt", k) a speed point, ("sline", j) on a speed piece, ("strip",) elsewhere in it; else None."""
        shape = self.depth_shape()
        if not shape:
            return None
        r = NEAR * self.s
        for end, bx0, by0, bx1, by1 in self.speed_locks():
            if bx0 - 2 <= x <= bx1 + 2 and by0 - 2 <= y <= by1 + 2:
                return "lock", end
        strip = self.speed_strip()
        if strip:
            _, spots, line = self.speed_shape()
            got = self.line_hit(spots, line, x, y, r)
            if got:
                return ("spt" if got[0] == "pt" else "sline"), got[1]
            if strip[0] <= x <= strip[2] and strip[1] <= y <= strip[3]:
                return ("strip",)
        post = self.speed_post()
        if post and abs(x - post[0]) <= r and post[2] - r <= y <= post[2] + r:
            return "speed"
        for name, bx0, by0, bx1, by1 in self.preset_boxes():
            if bx0 <= x <= bx1 and by0 <= y <= by1:
                return "preset", name
        _, spots, line = shape
        for k in range(len(spots) - 1, -1, -1):
            if abs(x - spots[k][0]) <= r and abs(y - spots[k][1]) <= r:
                return "pt", k
        for end, px, py in self.end_posts():  # (under the line: its ends' points win just above)
            if abs(x - px) <= r and py - r <= y <= py + r:
                return "post", end
        best = None
        for (ax, ay, j), (bx, by, j2) in zip(line, line[1:]):
            if j != j2:
                continue
            dx, dy = bx - ax, by - ay
            f = min(1.0, max(0.0, ((x - ax) * dx + (y - ay) * dy) / max(dx * dx + dy * dy, 1e-9)))
            far = math.hypot(ax + f * dx - x, ay + f * dy - y)
            if far <= r and (best is None or far < best[0]):
                best = far, j
        return ("line", best[1]) if best else None

    def sketch_depth(self, e, i, which, before):
        """A press on the picked piece's depth line (depth_hit's which): held, dragged = the wobble's depth
        (sketch_depth_to: a point / all of it; Alt: a piece curved) or speed; a piece with none gets one at
        WOBBLE_RATE. A preset button: that depth shape at once (one undo step)."""
        _, _, u0, u1 = self.picked_piece()
        if which[0] == "preset":
            self.wobble_edit(i, u0, u1, "preset", which[1])
            return True
        if which[0] == "lock":  # (the speed strip's lock at that end on / off: one undo step)
            self.wobble_edit(i, u0, u1, "lock", which[1])
            return True
        if which[0] == "strip":  # (the strip's empty space: nothing)
            self.redraw()
            return True
        if which[0] == "post":  # (an end post: sideways, sketch_post_to)
            self.drag = {"kind": "wobbleend", "i": i, "end": which[1], "before": before, "x": e.x, "y": e.y,
                         "moved": False, "at": [u0, u1], "at0": (u0, u1), "orig": copy.deepcopy(self.tones[i]),
                         "pick": (self.sketch_pick, self.sketch_speed), "name": tr("hz.step_wobble_end")}
            self.redraw()
            return True
        run = self.wobble_run(self.tones[i], u0, u1)
        if which[0] in ("spt", "sline"):
            return self.speed_press(e, i, which, before, run)
        speed = which == "speed"
        orig = copy.deepcopy(run) if run else {"at": [u0, u1], "depth": [0.0, 0.0], "rate": WOBBLE_RATE}
        if which[0] == "line" and self.curving(e.state):
            which = "curve", which[1]
        self.drag = {"kind": "wobblerate" if speed else "wobbledepth", "i": i, "which": which, "before": before,
                     "x": e.x, "y": e.y, "moved": False, "at": [u0, u1], "p0": self.pitch_at(e.y),
                     "name": tr("hz.step_wobble_speed" if speed else "hz.step_wobble_depth"),
                     "orig": orig, "pts": copy.deepcopy(depth_pts(orig))}
        self.redraw()
        return True

    def sketch_depth_to(self, d, n, e):
        """The depth line held in drag d moved: a point up / down by as many keys as the mouse moved (one between
        the ends sideways too, between its neighbours, on the grid; Shift free), all of it deeper / shallower (its
        deepest spot follows the mouse, the rest in step), or (Alt) a piece curved; depths in WOBBLE_STEP steps
        (Shift: 0.01), never below 0 (0 all along = no wobble once let go)."""
        k = self.pitch_at(e.y) - d["p0"]  # (keys under the mouse: a wheel turn mid-drag moves it too, hunt 2026-10-28)
        step = 0.01 if e.state & SHIFT else WOBBLE_STEP
        pts = copy.deepcopy(d["pts"])

        def depth(v):
            return max(0.0, min(NOTE_BEND_MOST, round(round(v / step) * step, 4)))
        what, j = (d["which"], None) if isinstance(d["which"], str) else d["which"]
        if what == "pt":
            pts[j][1] = depth(pts[j][1] + k)
            if abs(e.x - d["x"]) > 3:  # (sideways only once it's moved sideways: a point off the grid dragged up /
                d["side"] = True       # down stays where it is)
            if 0 < j < len(pts) - 1 and d.get("side"):  # (between its neighbours; two at one spot = a jump in depth)
                u0, u1 = d["at"]
                start, beats = n["t"] + u0 * n["len"], (u1 - u0) * n["len"]
                share = (self.snap(self.beat_at(e.x), e) - start) / max(beats, 1e-12)
                pts[j][0] = round(min(pts[j + 1][0], max(pts[j - 1][0], share)), 9)
        elif what == "curve":
            a, b = pts[j], pts[j + 1]
            del a[2:]
            if abs(b[1] - a[1]) > 1e-9:  # (dragged up = deeper sooner; a flat piece has nothing to curve)
                bend = (d["y"] - e.y) / (DEPTH_CURVE_PX * self.s) * (1.0 if b[1] > a[1] else -1.0)
                bend += bend_of(d["pts"][j])
                if abs(bend) > DEPTH_CURVE_STICK or e.state & SHIFT:
                    a.append(round(min(SLIDE_BEND, max(-SLIDE_BEND, bend)), 4))
        else:  # (the line: its deepest spot follows the mouse, every point in step; none yet = all that deep)
            most = max(p[1] for p in pts)
            peak = depth(most + k)
            for p in pts:
                p[1] = peak if most <= 0 else round(p[1] * peak / most, 4)
        put_run(n, d["at"], set_depth(copy.deepcopy(d["orig"]), pts))
        self.spread_pick(d["i"], d["at"])  # (several selected notes: theirs change alike, live)
        self.redraw()
        return True

    def sketch_depth_double(self, e, i, which):
        """A double-click on the picked piece's depth line (on a piece): a point there (on the grid; Shift free), on
        the line as it is, held like a pressed one (it follows the mouse until let go, the plugin's way). One undo
        step with what the drag does."""
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        _, _, u0, u1 = self.picked_piece()
        n = self.tones[i]
        run = self.wobble_run(n, u0, u1)
        if run is None:
            return True
        pts = [list(p) for p in depth_pts(run)]
        start, beats = n["t"] + u0 * n["len"], (u1 - u0) * n["len"]
        share = min(1.0, max(0.0, (self.snap(self.beat_at(e.x), e) - start) / max(beats, 1e-12)))
        j = min(len(pts) - 1, max(1, sum(p[0] <= share for p in pts)))
        a, b = pts[j - 1], pts[j]
        if not a[0] < share < b[0]:
            return True
        t = (share - a[0]) / (b[0] - a[0])
        curves = split_curve(bend_of(a), t) if len(a) > 2 and a[1] != b[1] else None
        if curves:  # (a curved piece: each part the curve nearest the old one's there)
            del a[2:]
            a += [curves[0]] if curves[0] else []
            pts.insert(j, [round(share, 9), round(a[1] + (b[1] - a[1]) * curves[2], 4)]
                       + ([curves[1]] if curves[1] else []))
        else:
            pts.insert(j, [round(share, 9), round(float(depth_at(run, np.array([share]))[0]), 4)])
        set_depth(run, pts)
        self.drag = {"kind": "wobbledepth", "i": i, "which": ("pt", j), "before": before, "x": e.x, "y": e.y,
                     "moved": False, "at": [u0, u1], "p0": self.pitch_at(e.y), "name": tr("hz.step_wobble_point"),
                     "orig": copy.deepcopy(run), "pts": copy.deepcopy(pts)}
        self.redraw()
        return True

    def sketch_depth_drop(self, i, which):
        """A right-click on a depth point between the ends: it goes (one undo step). False for anything else (the
        wobble's menu). A speed point between its ends: the same."""
        if which[0] not in ("pt", "spt"):
            return False
        _, _, u0, u1 = self.picked_piece()
        run = self.wobble_run(self.tones[i], u0, u1)
        speed = which[0] == "spt"
        pts = (run.get("speed") or EVEN_SPEED if speed else depth_pts(run)) if run else ()
        if not 0 < which[1] < len(pts) - 1:
            return False
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        kept = [p for k, p in enumerate(copy.deepcopy(pts)) if k != which[1]]
        if speed:
            run.pop("speed", None)
            kept = clean_speed(kept)
            if kept:
                run["speed"] = kept
        else:
            set_depth(run, kept)
        self.spread_pick(i, (u0, u1))
        self.bend_done({"i": i, "before": before}, tr("hz.step_wobble_speed_line" if speed else "hz.step_wobble_point"))
        return True

    def speed_press(self, e, i, which, before, run):
        """A press on the open speed strip's line (depth_hit's ("spt", k) / ("sline", j)): held, dragged = the speed
        there (sketch_speed_to: a point; the line: all of it, turning on a locked end; Alt: a piece curved)."""
        pts = copy.deepcopy(run.get("speed") or EVEN_SPEED)
        what = "spt" if which[0] == "spt" else "scurve" if self.curving(e.state) else "sline"
        if what == "sline" and run.get("lock") == "both" and len(pts) == 2:  # (both ends kept: the middle bows, a
            pts.insert(1, [0.5, round(float(line_at(pts, 0.5)), 4)])        # point there to bow it)
        self.drag = {"kind": "wobblespeed", "i": i, "which": (what, which[1]), "before": before, "x": e.x, "y": e.y,
                     "moved": False, "at": list(run["at"]), "orig": copy.deepcopy(run), "pts": pts,
                     "name": tr("hz.step_wobble_speed_line")}
        self.redraw()
        return True

    def sketch_speed_to(self, d, n, e):
        """The speed line held in drag d moved: a point up / down (one between the ends sideways too, between its
        neighbours, on the grid; Shift free), all of it (each point as far as its lock lets it: a locked start stays
        and the end moves most, a locked end the other way round, both locked = the middle), or (Alt) a piece
        curved. Speeds in SPEED_STEP steps (Shift: 0.01), 0..1 of the full speed. Whole half waves come at the let
        go (fit_wobbles)."""
        strip = self.speed_strip()
        if not strip:
            return True
        tall = max(1.0, strip[3] - strip[1])
        step = 0.01 if e.state & SHIFT else SPEED_STEP
        dv = round((d["y"] - e.y) / tall / step) * step
        pts = copy.deepcopy(d["pts"])
        what, j = d["which"]

        def speed(v):
            return max(0.0, min(1.0, round(v, 4)))
        if what == "spt":
            pts[j][1] = speed(round((d["pts"][j][1] + dv) / step) * step)
            if abs(e.x - d["x"]) > 3:
                d["side"] = True
            if 0 < j < len(pts) - 1 and d.get("side"):
                u0, u1 = d["at"]
                start, beats = n["t"] + u0 * n["len"], (u1 - u0) * n["len"]
                share = (self.snap(self.beat_at(e.x), e) - start) / max(beats, 1e-12)
                pts[j][0] = round(min(pts[j + 1][0], max(pts[j - 1][0], share)), 9)
        elif what == "scurve":
            bend = (d["y"] - e.y) / (DEPTH_CURVE_PX * self.s) * (1.0 if pts[j + 1][1] > pts[j][1] else -1.0)
            curve_to(pts, j, bend + bend_of(d["pts"][j]), e.state & SHIFT)
        else:
            lock = d["orig"].get("lock")
            for p in pts:
                lift = p[0] if lock == "start" else 1.0 - p[0] if lock == "end" else \
                    4.0 * p[0] * (1.0 - p[0]) if lock == "both" else 1.0
                p[1] = speed(p[1] + dv * lift)
        run = copy.deepcopy(d["orig"])
        run.pop("speed", None)
        pts = clean_speed(pts)
        if pts:
            run["speed"] = pts
        put_run(n, d["at"], run)
        self.spread_pick(d["i"], d["at"])  # (several selected notes: theirs change alike, live)
        self.redraw()
        return True

    def sketch_speed_double(self, e, i, which):
        """A double-click on the open speed strip's line: a point there (on the grid; Shift free), on the line as it
        is, held like a pressed one (follows the mouse until let go). One undo step with what the drag does."""
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        _, _, u0, u1 = self.picked_piece()
        n = self.tones[i]
        run = self.wobble_run(n, u0, u1)
        if run is None:
            return True
        pts = [list(p) for p in run.get("speed") or EVEN_SPEED]
        start, beats = n["t"] + u0 * n["len"], (u1 - u0) * n["len"]
        share = min(1.0, max(0.0, (self.snap(self.beat_at(e.x), e) - start) / max(beats, 1e-12)))
        j = min(len(pts) - 1, max(1, sum(p[0] <= share for p in pts)))
        a, b = pts[j - 1], pts[j]
        if not a[0] < share < b[0]:
            return True
        t = (share - a[0]) / (b[0] - a[0])
        curves = split_curve(bend_of(a), t) if len(a) > 2 and a[1] != b[1] else None
        if curves:
            del a[2:]
            a += [curves[0]] if curves[0] else []
            pts.insert(j, [round(share, 9), round(a[1] + (b[1] - a[1]) * curves[2], 4)]
                       + ([curves[1]] if curves[1] else []))
        else:
            pts.insert(j, [round(share, 9), round(float(line_at(pts, share)), 4)])
        run["speed"] = pts
        self.drag = {"kind": "wobblespeed", "i": i, "which": ("spt", j), "before": before, "x": e.x, "y": e.y,
                     "moved": False, "at": [u0, u1], "orig": copy.deepcopy(run), "pts": copy.deepcopy(pts),
                     "name": tr("hz.step_wobble_speed_line")}
        self.redraw()
        return True

    def speed_toggle(self):
        """The speed post clicked (not dragged): its speed strip opens / closes."""
        self.sketch_speed = None if self.sketch_speed == self.sketch_pick else self.sketch_pick

    def depth_hint(self, x, y):
        """The status line's part for the mouse over a depth preset button, the speed post, the speed strip or a
        lock ("" elsewhere)."""
        which = self.depth_hit(x, y) if self.sketching() else None
        if not which:
            return ""
        if which[0] == "preset":
            return "     " + tr("hz.depth_preset_hint", name=tr(f"hz.depth_{which[1]}"))
        if which == "speed":
            return "     " + tr("hz.speed_post_hint")
        if which[0] == "lock":
            return "     " + tr(f"hz.speed_lock_{which[1]}")
        if which[0] in ("spt", "sline", "strip"):
            return "     " + tr("hz.speed_strip_hint")
        if which[0] == "post":
            return "     " + tr("hz.wobble_post_hint")
        return ""

    def sketch_rate_to(self, d, n, e):
        """The speed post held in drag d moved sideways: SPEED_DOUBLE px right = twice as fast, left = half, so the
        piece holds a whole number of HALF waves (it ends back on its line; Ctrl: any speed, 0.01 a beat), at least
        one half wave, at most WOBBLE_RATE_MOST a beat."""
        beats = (d["at"][1] - d["at"][0]) * n["len"]
        orig = d["orig"]
        if orig.get("speed"):  # (a speed line (stage C): the waves it makes, turning on a locked end, fit_speed)
            waves = orig["rate"] * beats * speed_mean(orig) * 2.0 ** ((e.x - d["x"]) / (SPEED_DOUBLE * self.s))
            waves = max(0.01 * beats, round(waves, 2)) if e.state & CTRL else max(1.0, round(2.0 * waves)) / 2.0
            run = fit_speed(copy.deepcopy(orig), beats, waves)
            run["rate"] = round(run["rate"], 9)
        else:
            rate = orig["rate"] * 2.0 ** ((e.x - d["x"]) / (SPEED_DOUBLE * self.s))
            if e.state & CTRL:
                rate = round(rate, 2)
            else:
                rate = max(1.0, round(2.0 * rate * beats)) / (2.0 * beats)
            rate = min(WOBBLE_RATE_MOST, max(0.5 / beats, 0.01, rate))
            run = dict(copy.deepcopy(orig), rate=round(rate, 6))
        run.pop("free", None)
        if e.state & CTRL:  # (set freely: kept as it is when its piece's length changes, hz_lines.fit_wobbles)
            run["free"] = True
        put_run(n, d["at"], run)
        self.spread_pick(d["i"], d["at"])  # (several selected notes: theirs change alike, live)
        self.redraw()
        return True

    def sketch_post_to(self, d, n, e):
        """An end post held in drag d moved sideways (stage D, the plugin's way: a wobble runs point to point): that
        end's bend point moves in time to the grid line nearest the mouse (Shift: any tick), between its neighbours;
        a wobble ending / starting on it follows (follow_wobble). The note's own start / end can't move: the wobble's
        end goes along the line instead (a delay), a new point left there (or onto a point on the way); dragged back
        = as it was."""
        o = d["orig"]
        pts = o.get("bend") or ([0.0, 0.0], [1.0, 0.0])
        u0, u1 = d["at0"]
        start = d["end"] == "start"
        j0, j1 = run_points(pts, u0, u1)
        j = j0 if start else j1
        u = min(1.0, max(0.0, (self.snap(self.beat_at(e.x), e) - n["t"]) / max(n["len"], 1e-12)))
        at = self.post_move(n, o, (u0, u1), start, u)
        if at is None:
            return True
        moved = (u - pts[j][0]) * n["len"]  # (beats: every other selected note's same point moves as far, the plugin's
        for k in sorted(self.sel):           # way, user 2026-11-13; notes with as many points and a wobble there)
            if k == d["i"] or k >= len(d["before"]) or k >= len(self.tones):
                continue
            mo = d["before"][k]
            mp = mo.get("bend") or ([0.0, 0.0], [1.0, 0.0])
            if len(mp) != len(pts) or not self.wobble_run(mo, mp[j0][0], mp[j1][0]):
                continue
            m = self.tones[k]
            mu = min(1.0, max(0.0, mp[j][0] + moved / max(m["len"], 1e-12)))
            self.post_move(m, mo, (mp[j0][0], mp[j1][0]), start, mu)  # (None: it stays where it got to, as this one)
        d["at"] = at
        pick = (n.get("id"), *at)
        if self.sketch_speed == self.sketch_pick:
            self.sketch_speed = pick
        self.sketch_pick = pick
        self.redraw()
        return True

    def post_move(self, n, o, at0, start, u):
        """sketch_post_to on one note: note n (o = it at the press) with the wobble over at0 having its start / end's
        bend point moved to u (parts of the note). The note's own start / end can't move: the wobble's end goes
        along the line instead, a new point left there. The new (u0, u1), or None (onto its other end: as it was)."""
        pts = [list(p) for p in o.get("bend") or ([0.0, 0.0], [1.0, 0.0])]
        wob = copy.deepcopy(o.get("wobble") or [])
        u0, u1 = at0
        j0, j1 = run_points(pts, u0, u1)
        j = j0 if start else j1
        if j in (0, len(pts) - 1):
            run = self.wobble_run({"wobble": wob}, u0, u1)
            u = min(u1, max(0.0, u)) if start else min(1.0, max(u0, u))
            if (u1 - u if start else u - u0) > ON_POINT and abs(u - (u0 if start else u1)) > ON_POINT:
                on = next((p[0] for p in pts if abs(p[0] - u) < ON_POINT), None)
                if on is None:
                    pts = put_point(pts, u)[0]
                    on = round(u, 9)
                run["at"] = [on, u1] if start else [u0, on]
            n["bend"] = pts
            at = list(run["at"])
        else:
            old = pts[j][0]
            pts[j][0] = round(min(pts[j + 1][0], max(pts[j - 1][0], u)), 9)
            at = [pts[j][0], u1] if start else [u0, pts[j][0]]
            if at[1] - at[0] <= ON_POINT:  # (onto its other end: no wobble left, so it stops short, as it was)
                return None
            moved = {"bend": pts}
            follow_wobble(moved, wob, j, old)  # (this wobble's end on it follows: the start's last point, the end's
            n["bend"], wob = pts, moved.get("wobble") or wob  # first; a wobble on its other side too)
        n["wobble"] = sorted(wob, key=lambda w: w["at"])
        return at

    def sketch_widen(self, d, a, b, shift):
        """A click (no drag) on piece a..b of note d["i"] (stage D): picks it, or the whole wobble running over it;
        Shift with a stretch of the same note picked: the picked stretch grows to take it in, and a wobble on it
        grows with it (the wobbles it takes in become one, the picked one's settings; one undo step). The stretch
        always takes in whole wobbles (they run point to point). True when it made the undo step."""
        i = d["i"]
        n = self.tones[i]
        old = self.picked_piece() if shift else None
        old = old if old and old[0] == i else None
        if old:
            a, b = min(a, old[2]), max(b, old[3])
        runs = n.get("wobble") or []
        grown = True
        while grown:  # (every wobble it touches, whole)
            grown = False
            for w in runs:
                if w["at"][0] < b - ON_POINT and w["at"][1] > a + ON_POINT and (
                        w["at"][0] < a - ON_POINT or w["at"][1] > b + ON_POINT):
                    a, b, grown = min(a, w["at"][0]), max(b, w["at"][1]), True
        self.sketch_pick = (n.get("id"), a, b)
        inside = [w for w in runs if w["at"][0] < b - ON_POINT and w["at"][1] > a + ON_POINT]
        if not old or not inside or len(inside) == 1 and self.wobble_run(n, a, b) is inside[0]:
            return False
        keep = self.wobble_run(n, old[2], old[3]) or min(inside, key=lambda w: w["at"][0])
        merged = dict(keep, at=[a, b])
        if keep["at"][0] > a + ON_POINT:  # (it starts sooner now: on its line, not going on from one before)
            merged.pop("phase", None)
        n["wobble"] = sorted([w for w in runs if not any(w is v for v in inside)] + [merged], key=lambda w: w["at"])
        self.spread_pick(i, (a, b))
        self.bend_done(d, tr("hz.step_wobble_widen"))
        return True

    def spread_pick(self, i, at, gone=False):
        """Several notes selected (stage D 2026-11-13, the plugin's way): the other selected notes get note i's wobble
        over `at` too (gone: theirs there go), replacing theirs there: over their whole note when it's note i's whole
        note, else over the same point numbers on notes with as many points (others left alone); as many waves each
        ("Same cycles", default) or the same waves a beat ("Same speed", the wobble menu's tick). [(tone number, u0,
        u1), ...] each got."""
        if len(self.sel) < 2:
            return []
        n = self.tones[i]
        pts = n.get("bend") or ([0.0, 0.0], [1.0, 0.0])
        got = run_points(pts, *at)
        if not got:
            return []
        whole = at[0] < ON_POINT and at[1] > 1.0 - ON_POINT
        run = None if gone else self.wobble_run(n, *at)
        beats = (at[1] - at[0]) * n["len"]
        out = []
        for k in sorted(self.sel):
            if k == i or k >= len(self.tones):
                continue
            m = self.tones[k]
            mp = m.get("bend") or ([0.0, 0.0], [1.0, 0.0])
            if whole:
                a, b = 0.0, 1.0
            elif len(mp) == len(pts) and mp[got[1]][0] - mp[got[0]][0] > ON_POINT:
                a, b = mp[got[0]][0], mp[got[1]][0]
            else:
                continue
            for u in (a, b):  # (a longer one of theirs: cut around the stretch, the rest kept; hunt 2026-11-13)
                split_wobbles(m, u)
            kept = [w for w in m.get("wobble") or () if not (w["at"][0] < b - ON_POINT and w["at"][1] > a + ON_POINT)]
            if run:
                kept.append(run_copy(run, beats, a, b, m["len"], self.wobble_same == "speed"))
            m["wobble"] = sorted(kept, key=lambda w: w["at"])
            tidy_bend(m, True)
            out.append((k, a, b))
        return out

    def sketch_depth_gone(self, d):
        """Delete while the depth line is held: the piece's wobble goes."""
        n = self.tones[d["i"]]
        was = self.wobble_run(n, *d["at"])
        n["wobble"] = [w for w in n.get("wobble") or () if w is not was]

    def sketch_wobble_menu(self, e, i):
        """Right-click on the picked piece's depth line / speed post: the wave's shape (WOBBLE_SHAPES), Upside down,
        Bake into points (bake_wobble), Remove wobble. False when the piece has no wobble (the usual menu)."""
        _, _, u0, u1 = self.picked_piece()
        run = self.wobble_run(self.tones[i], u0, u1)
        if not run:
            return False
        shape = tk.StringVar(self, value=run.get("shape", "sine"))
        flip = tk.BooleanVar(self, value=bool(run.get("flip")))
        menu = tk.Menu(self, tearoff=0)
        for kind in WOBBLE_SHAPES:
            menu.add_radiobutton(label=tr(f"hz.wobble_{kind}"), variable=shape, value=kind,
                                 command=lambda kind=kind: self.wobble_edit(i, u0, u1, "shape", kind))
        menu.add_checkbutton(label=tr("hz.wobble_flip"), variable=flip,
                             command=lambda: self.wobble_edit(i, u0, u1, "flip", flip.get()))
        menu.add_separator()
        for key, step in (("hz.wobble_phase_back", -PHASE_STEP), ("hz.wobble_phase_on", PHASE_STEP)):
            menu.add_command(label=tr(key), command=lambda step=step: self.wobble_edit(i, u0, u1, "nudge", step))
        if run.get("speed"):  # (its speed line gone: full speed all along again)
            menu.add_command(label=tr("hz.wobble_even"), command=lambda: self.wobble_edit(i, u0, u1, "even", None))
        menu.add_separator()  # (stage D: over the whole note; several selected notes' wobbles: same speed or not)
        menu.add_command(label=tr("hz.wobble_whole"), command=lambda: self.wobble_edit(i, u0, u1, "whole", None),
                         state="normal" if u0 > ON_POINT or u1 < 1.0 - ON_POINT else "disabled")
        same = tk.BooleanVar(self, value=self.wobble_same == "speed")
        menu.add_checkbutton(label=tr("hz.wobble_same_speed"), variable=same,
                             command=lambda: setattr(self, "wobble_same", "speed" if same.get() else "cycles"))
        menu.add_separator()
        menu.add_command(label=tr("hz.wobble_bake"), command=lambda: self.wobble_edit(i, u0, u1, "bake", None))
        menu.add_command(label=tr("hz.wobble_remove"), command=lambda: self.wobble_edit(i, u0, u1, "remove", None))
        menu.vars = (shape, flip, same)  # (kept while the menu shows)
        menu.tk_popup(e.x_root, e.y_root)
        return True

    def wobble_edit(self, i, u0, u1, what, value):
        """The wobble on note i's stretch u0..u1: its "shape" / "flip" set to value, "bake"d into bend points,
        "remove"d, over the "whole" note (stage D: the note's other wobbles taken in, this one's settings)...; the other
        selected notes' the same (spread_pick). One undo step."""
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        n = self.tones[i]
        run = self.wobble_run(n, u0, u1)
        if run is None:
            return
        name = "hz.step_wobble_shape"
        if what == "bake":  # (the other selected notes get it first, then each is baked)
            for k, a, b in self.spread_pick(i, (u0, u1)):
                m = self.tones[k]
                fit_wobbles(m)  # (as it would be once made: whole half waves)
                w = self.wobble_run(m, a, b)
                if w:
                    m["wobble"] = [x for x in m["wobble"] if x is not w]
                    m["bend"] = bake_wobble(m, w)
                    tidy_bend(m, True)
        elif what == "remove":
            self.spread_pick(i, (u0, u1), gone=True)
        if what in ("bake", "remove"):
            n["wobble"] = [w for w in n["wobble"] if w is not run]
            if what == "bake":
                n["bend"] = bake_wobble(n, run)
                self.sketch_pick = None  # (the piece is many now)
            name = f"hz.step_wobble_{what}"
        elif what == "whole":  # (over the whole note: one wobble, this one's settings)
            n["wobble"] = [dict(run, at=[0.0, 1.0])]
            if u0 > ON_POINT:
                n["wobble"][0].pop("phase", None)
            self.sketch_pick = (n.get("id"), 0.0, 1.0)
            u0, u1 = 0.0, 1.0
            name = "hz.step_wobble_widen"
        elif what == "preset":  # (the depth line's shape (DEPTH_PRESETS), as deep as its deepest spot was)
            most = depth_most(run)
            set_depth(run, [[p[0], round(p[1] * most, 4), *p[2:]] for p in DEPTH_PRESETS[value]])
            name = "hz.step_wobble_preset"
        elif what == "lock":  # (that end of the speed line locked / unlocked: "start" + "end" = "both")
            ends = {e for e in ("start", "end") if self.locked(run, e)} ^ {value}
            run.pop("lock", None)
            if ends:
                run["lock"] = "both" if len(ends) == 2 else ends.pop()
            name = "hz.step_wobble_lock"
        elif what == "even":
            run.pop("speed", None)
            name = "hz.step_wobble_speed_line"
        elif what == "nudge":  # (within one wave, like the other shapes: 8 steps = as it was; a Random seed untouched)
            run["nudge"] = round((run.get("nudge", 0.0) + value) % 1.0, 9) % 1.0
            if not run["nudge"]:
                del run["nudge"]
            name = "hz.step_wobble_phase"
        elif what == "shape" and value == "sine":
            run.pop("shape", None)
        elif what == "shape" and value == "random" and "seed" not in run:
            run.update(shape="random", seed=random.randrange(1, 1 << 14) * WOBBLE_SEED_STEP)
        elif what == "flip" and not value:
            run.pop("flip", None)
        else:
            run[what] = value
        if what not in ("bake", "remove"):
            self.spread_pick(i, (u0, u1))
        self.bend_done({"i": i, "before": before}, tr(name))

    def sketching(self):
        return self.tool.get() == "sketch"

    @staticmethod
    def curving(state):
        """True when a press / the mouse with these keys held curves a piece: Alt, or Ctrl+Shift (CURVE_KEYS)."""
        return bool(state & ALT) or state & CURVE_KEYS == CURVE_KEYS

    def sketch_near(self, n, x, y, reach, most):
        """True when (x, y) is within reach px of note n's line AS DRAWN: its pieces, a jump's upright stroke and a
        steep slope too (hunt 2026-10-28: only up / down at the mouse's beat counted)."""
        if bends(n):
            beats, keys = self.bend_line(n, most)
            xs, ys = np.asarray(self.x_of(beats), float), np.asarray(self.pitch_y(keys), float)
        else:
            y0 = self.pitch_y(pitch(n))
            xs, ys = np.array([self.x_of(n["t"]), self.x_of(n["t"] + n["len"])]), np.array([y0, y0])
        ax, ay, dx, dy = xs[:-1], ys[:-1], np.diff(xs), np.diff(ys)
        long = dx * dx + dy * dy
        f = np.clip(((x - ax) * dx + (y - ay) * dy) / np.where(long > 0, long, 1.0), 0.0, 1.0)
        return bool((np.hypot(ax + f * dx - x, ay + f * dy - y) <= reach).any())

    def sketch_alt_to(self, held):
        """Alt pressed / let go (a key or the mouse's state): the squares shown on every piece while it's held."""
        if bool(held) != self.sketch_alt:
            self.sketch_alt = bool(held)
            if self.sketching() and not self.drag:
                self.redraw()

    def sketch_curve_menu(self, e, i, j):
        """Right-click on piece j's curve square (note i): Single curve / S-curve (like a slide's) and Straight."""
        p = self.tones[i]["bend"][j]
        kind = tk.StringVar(self, value="double" if len(p) > 3 and p[3] == "s" else "single")
        menu = tk.Menu(self, tearoff=0)
        curved = "normal" if bend_of(p) else "disabled"  # (a straight piece: no curve to make one or an S, hunt)
        menu.add_radiobutton(label=tr("hz.curve_single"), variable=kind, value="single", state=curved,
                             command=lambda: self.sketch_curve_kind(i, j, False))
        menu.add_radiobutton(label=tr("hz.curve_double"), variable=kind, value="double", state=curved,
                             command=lambda: self.sketch_curve_kind(i, j, True))
        menu.add_command(label=tr("hz.sketch_straight"), command=lambda: self.sketch_curve_kind(i, j, None))
        menu.kind = kind  # (kept while the menu shows)
        menu.tk_popup(e.x_root, e.y_root)

    def sketch_curve_kind(self, i, j, s):
        """Piece j of note i: an S (s True), one curve (False), straight (None). One undo step."""
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        pts = self.tones[i]["bend"]
        set_curve(pts, j, 0.0 if s is None else float(pts[j][2]) if len(pts[j]) > 2 else 0.0, bool(s))
        self.bend_done({"i": i, "before": before}, tr("hz.step_note_bend_curve"))

    def sketch_new_to(self, d, n, e):
        """A new note being drawn (drag d, note n): it starts where the press was and ends at the grid line nearest
        the mouse (Shift: any tick), at least one step long. A click without dragging keeps the last length used."""
        if not d.get("moved") and abs(e.x - d["x"]) < 4:
            return
        d["moved"] = True
        n["len"] = max(self.shortest(e), self.snap(self.beat_at(e.x), e) - n["t"])

    def sketch_wide(self):
        """How thick a note's line is (px)."""
        return max(SKETCH_THIN * self.s, min(SKETCH_THICK * self.s, SKETCH_WIDE * self.sy))

    def sketch_reach(self):
        """How near a note's line (px, up / down) a press takes the note."""
        return max(LINE_NEAR * self.s, self.sketch_wide() / 2 + 2 * self.s)

    def sketch_double(self, e, hit):
        """A double-click on a note's line ("note", tone): a point there (on the grid, on the line as it is, so
        nothing changes yet), held like a pressed one; on a point ("point", tone, point): it goes (never the
        first / last: the note's start and end). One undo step each."""
        self.drop_drag()
        before = copy.deepcopy(self.tones)
        self.sel_before = (before, self.sel_state())
        i, n = hit[1], self.tones[hit[1]]
        self.sel, self.box_kept = {i}, None
        if hit[0] == "point":
            if 0 < hit[2] < len(n.get("bend") or "..") - 1:
                drop_point(n, hit[2])  # (its wobbles merged, hunt 2026-10-28)
                self.bend_done({"i": i, "before": before}, tr("hz.step_note_bend_delete"))
                self.point_again()
            else:
                self.redraw()
            return True
        u = self.bend_spot(n, e)[0]
        pts = [list(p) for p in n.get("bend") or ([0.0, 0.0], [1.0, 0.0])]
        if any(abs(p[0] - u) < 1e-9 for p in pts):  # (one there already)
            self.redraw()
            return True
        j = sum(p[0] <= u for p in pts)
        a, b = pts[j - 1], pts[min(j, len(pts) - 1)]
        curves = (split_curve(bend_of(a), (u - a[0]) / (b[0] - a[0]))
                  if 0 < j < len(pts) and bend_of(a) and a[1] != b[1] and b[0] > a[0] else None)
        if 0 < j < len(pts) and b[0] > a[0] and bez_piece(a, b):  # (a Bézier piece: split exactly, stage H)
            put_at(pts, j, u, 6)
        elif curves:  # (a curved piece: each part gets the curve nearest the old one's there, hunt 2026-10-28)
            pts[j - 1] = a = with_handles(a[:2] + ([curves[0]] if curves[0] else []), handles(a))
            pts.insert(j, [u, a[1] + (b[1] - a[1]) * curves[2]] + ([curves[1]] if curves[1] else []))
        else:
            pts.insert(j, [u, round(float(line_at(pts, u)), 2)])
        n["bend"] = pts  # (a wobble across it rides it: still one, stage D 2026-11-13; it was split in two before)
        self.drag = {"kind": "bendpt", "i": i, "j": j, "before": before, "x": e.x, "y": e.y, "moved": False,
                     "pinned": False, "name": tr("hz.step_note_bend"), "old": u,
                     "wob": copy.deepcopy(n.get("wobble") or [])}
        self.redraw()
        self.show_status(e)
        return True

    def sketch_piece(self, e, i, before):
        """A press on note i's line between two points: that piece is held (sketch_piece_to). A line with no points
        between its ends is all one piece: the whole note goes up / down (its key)."""
        n = self.tones[i]
        pts = n.get("bend")
        u = min(1.0, max(0.0, (self.beat_at(e.x) - n["t"]) / max(n["len"], 1e-12)))
        if self.curving(e.state):  # (Alt: the piece curved, like its square dragged, at any width; a flat / jump
            # piece: nothing to curve)
            j = min(len(pts) - 2, max(0, sum(p[0] <= u for p in pts) - 1)) if pts else 0
            square = self.piece_square(i, j, room=False) if pts else None
            if square:
                self.drag = {"kind": "bendcurve", "i": i, "j": square[3], "before": before, "x": e.x, "y": e.y,
                             "moved": False, "square": square, "name": tr("hz.step_note_bend_curve")}
            self.redraw()
            return True
        j = None if not pts or len(pts) <= 2 else min(len(pts) - 2, max(0, sum(p[0] <= u for p in pts) - 1))
        d = self.drag = {"kind": "sketchpiece", "i": i, "j": j, "before": before, "x": e.x, "y": e.y, "moved": False,
                         "orig": copy.deepcopy(pts), "key": n["key"], "p0": self.pitch_at(e.y),
                         "name": tr("hz.step_move") if j is None else tr("hz.step_note_bend")}
        self.after(HOLD_MS, lambda: self.sketch_grab(d))
        self.redraw()
        self.show_status(e)
        return True

    def sketch_grab(self, d):
        """The piece pressed in drag d held still for HOLD_MS: the whole note is grabbed instead, it moves like the
        Pencil's (hz_mouse's "note" drag: in time and key, on the grid)."""
        if self.drag is not d or d["moved"] or not self.winfo_exists():
            return
        self.drag = {"kind": "note", "i": d["i"], "before": d["before"], "beat": self.beat_at(d["x"]),
                     "key": self.key_at(d["y"]), "orig": copy.deepcopy(self.tones), "x": d["x"], "y": d["y"],
                     "moved": False, "slide": None, "whole": False, "name": tr("hz.step_move"), "grabbed": True}
        # (the 8-handle box shows round the note(s) held from now on, while they stay the selection: hz_box.py)
        self.box8_held = frozenset(self.tones[i]["id"] for i in self.sel if i < len(self.tones))
        self.canvas.config(cursor="fleur")
        self.redraw()

    def sketch_piece_to(self, d, n, e):
        """The piece held in drag d up / down: both its points by as many keys as the mouse moved (whole keys;
        Shift: to the cent), no further than the Range; the whole note: its key."""
        k = self.pitch_at(e.y) - d["p0"]  # (keys under the mouse: a wheel turn mid-drag moves it too, hunt 2026-10-28)
        if d["j"] is None:
            n["key"] = max(0, min(TOP_KEY, d["key"] + round(k)))
        else:
            k = round(k, 2) if e.state & SHIFT else float(round(k))
            most = self.bend_most()
            for j in (d["j"], d["j"] + 1):
                n["bend"][j][1] = max(-most, min(most, d["orig"][j][1] + k))
        self.redraw()
        self.show_status(e)
        return True

    def sketch_end_hit(self, x, y):
        """The note whose ">" (just after its end) is at (x, y), or None. It wins over the end dot right of the
        dot's middle (they're close: the dot is grabbed from its left half)."""
        near, most = NEAR * self.s, self.bend_most()
        for i in range(len(self.tones) - 1, -1, -1):
            n = self.tones[i]
            end = n["t"] + n["len"]
            x1 = self.x_of(end)
            ex, ey = x1 + END_GAP * self.s, self.pitch_y(self.bent_pitch(n, end, most))
            if x1 < x <= ex + near and abs(y - ey) <= near:
                return i
        return None

    def sketch_end_to(self, d, n, e):
        """The ">" held in drag d dragged sideways: the note ends at the grid line nearest the mouse (Shift: any
        tick), at least one step long. Its points stay on their beats: longer = the old end stays a point and a flat
        piece goes on from it (a plain note too: its line made, like the plugin's); shorter = the line is cut there."""
        was = d["orig"][d["i"]]
        n["len"] = max(self.shortest(e), self.snap(self.beat_at(e.x), e) - was["t"])
        pts = was.get("bend") or (n["len"] > was["len"] + 1e-12 and [[0.0, 0.0], [1.0, 0.0]])
        n.pop("bend", None)  # (made again from the note as it was: dragged back, a plain note stays plain)
        if pts and abs(n["len"] - was["len"]) > 1e-12:
            cut = bend_part(was, was["t"], n["len"])
            if cut is None:  # (flat all along: its pieces kept, on their beats)
                cut = [[p[0] * was["len"] / n["len"], 0.0] for p in pts if p[0] * was["len"] < n["len"] - 1e-9]
                cut.append([1.0, 0.0])
            n["bend"] = cut
        elif pts:
            n["bend"] = copy.deepcopy(pts)
        if was.get("wobble"):  # (its wobbles stay on their beats too)
            n["wobble"] = wobble_part(was, was["t"], n["len"]) or []
            if not n["wobble"]:
                del n["wobble"]
        self.keep_leads(d["i"], d["orig"])
        self.redraw()
        self.show_status(e)
        return True

    def sketch_label(self):
        """(x, y, text) of the label beside what's held: the key it's at + how far from the note's own ("A2 +2.0
        st"); None when no point / piece is held."""
        d = self.drag
        if d and d["kind"] in ("wobbledepth", "wobblerate", "wobblespeed") and d.get("moved"):  # (the depth:
            got = self.depth_line()  # "Wobble 2.0 st" / a fade "2.0 → 0.5"; the speed; the speed line)
            n = self.tones[d["i"]]
            run = self.wobble_run(n, *d["at"])
            if got and run and d["kind"] == "wobblespeed" and self.speed_strip():
                pts, spots, line = self.speed_shape()
                if d["which"][0] == "spt":
                    j = d["which"][1]
                    return spots[j][0], spots[j][1] - 8 * self.s, tr("hz.wobble_speed_pt",
                                                                    pct=f"{pts[j][1] * 100:.0f}")
                return ((got[0] + got[2]) / 2, min(y for _, y, _ in line) - 6 * self.s,
                        tr("hz.wobble_speed_line", a=f"{pts[0][1] * 100:.0f}", b=f"{pts[-1][1] * 100:.0f}"))
            if got and run and d["kind"] != "wobblespeed":
                if d["kind"] == "wobblerate":
                    waves = round(run["rate"] * (run["at"][1] - run["at"][0]) * n["len"] * speed_mean(run), 2)
                    text = trn("hz.wobble_speed", waves, rate=f"{run['rate']:.2f}".rstrip("0").rstrip("."),
                               waves=f"{waves:g}")
                    return (got[0] + got[2]) / 2, self.speed_post()[2] - 6 * self.s, text
                pts, spots, line = self.depth_shape()
                top = min(y for _, y, _ in line) - 6 * self.s
                if d["which"][0] == "pt":  # (a point: its depth, over it)
                    j = d["which"][1]
                    return spots[j][0], spots[j][1] - 8 * self.s, tr("hz.wobble_depth", depth=f"{pts[j][1]:.1f}")
                a, b = pts[0][1], pts[-1][1]
                text = (tr("hz.wobble_depth", depth=f"{a:.1f}") if all(abs(p[1] - a) < 1e-9 for p in pts) else
                        tr("hz.wobble_fade", a=f"{a:.1f}", b=f"{b:.1f}") if len(pts) == 2 else
                        tr("hz.wobble_peak", depth=f"{max(p[1] for p in pts):.1f}"))
                return (got[0] + got[2]) / 2, top, text
        if not d or d["kind"] not in ("bendpt", "sketchpiece") or not d.get("moved"):
            return None
        n = self.tones[d["i"]]
        if d["kind"] == "sketchpiece" and d["j"] is None:
            k, u = n["key"] - d["key"], 0.5
        else:
            j = d["j"]
            pts = n["bend"]
            k = pts[j][1]
            u = pts[j][0] if d["kind"] == "bendpt" else (pts[j][0] + pts[j + 1][0]) / 2
        at = pitch(n) + (k if d["kind"] == "bendpt" or d["j"] is not None else 0.0)
        text = tr("hz.sketch_label", key=note_name(round(at)), keys=("+" if k > 0 else "") + f"{k:.1f}")
        return self.x_of(n["t"] + u * n["len"]), self.pitch_y(at) - 14 * self.s, text

    def draw_speed_strip(self, c, s):
        """The open speed strip (stage C): a box (top = full speed, bottom = stopped, a faint middle line), its
        speed line (blue) through its points (a square each), a lock just outside each end (shut = locked)."""
        strip = self.speed_strip()
        if not strip:
            return
        x0, top, x1, bottom = strip
        c.create_rectangle(x0, top, x1, bottom, fill=look.HZ_LABEL_BOX, outline=look.HZ_SPEED)
        c.create_line(x0, (top + bottom) / 2, x1, (top + bottom) / 2, fill=look.HZ_SPEED, dash=(1, 3))
        _, spots, line = self.speed_shape()
        c.create_line(*[v for x, y, _ in line for v in (x, y)], fill=look.HZ_SPEED, width=max(1, round(2 * s)))
        d = self.drag
        held = d["which"][1] if d and d["kind"] == "wobblespeed" and d["which"][0] == "spt" else None
        h = 3 * s
        for k, (hx, hy) in enumerate(spots):
            c.create_rectangle(hx - h, hy - h, hx + h, hy + h, outline=look.HZ_SPEED,
                               fill=look.HZ_DOT if held == k else look.HZ_SPEED)
        piece = self.picked_piece()
        run = self.wobble_run(self.tones[piece[0]], piece[2], piece[3])
        for end, lx0, ly0, lx1, ly1 in self.speed_locks():  # (a padlock: body + shackle, shut / open)
            shut = self.locked(run, end)
            body = ly0 + (ly1 - ly0) * 0.45
            c.create_rectangle(lx0, body, lx1, ly1, outline=look.HZ_SPEED,
                               fill=look.HZ_SPEED if shut else look.HZ_LABEL_BOX, width=max(1, round(s)))
            a, b = lx0 + (lx1 - lx0) * 0.25, lx1 - (lx1 - lx0) * 0.25
            lift = 0 if shut else (ly1 - ly0) * 0.25
            c.create_line(a, body, a, ly0 - lift + 2 * s, (a + b) / 2, ly0 - lift, b, ly0 - lift + 2 * s,
                          b, body if shut else body - (ly1 - ly0) * 0.2, fill=look.HZ_SPEED,
                          width=max(1, round(1.5 * s)), smooth=True)

    def draw_sketch_notes(self, colours, selected):
        """The notes in view as lines (Sketch tool), each in its colours (fill, edge; selected ones in `selected`)."""
        c, s, most, w = self.paint, self.s, self.bend_most(), self.canvas.winfo_width()
        wide = self.sketch_wide()
        grabbed = self.drag and self.drag.get("grabbed")
        r = 3.5 * s
        for i, n in enumerate(self.tones):
            x0, x1 = self.x_of(n["t"]), self.x_of(n["t"] + n["len"])
            if x1 < self.kb_w or x0 > w:
                continue
            fill, edge = selected if i in self.sel else colours
            if bends(n):
                y = self.pitch_y(pitch(n))
                c.create_line(x0, y, x1, y, fill=edge, dash=(2, 4))
                beats, keys = self.bend_line(n, most)
                ys = self.pitch_y(keys)
                xy = np.column_stack([self.x_of(beats), ys]).ravel().tolist()
            else:
                y = self.pitch_y(pitch(n))
                xy, ys = [x0, y, max(x1, x0 + 1), y], [y, y]
            c.create_line(*xy, fill=edge, width=wide + 2 * max(1, round(s)), capstyle="round", joinstyle="round")
            c.create_line(*xy, fill=fill, width=wide, capstyle="round", joinstyle="round")
            if grabbed and i in self.sel:  # (held still: the whole note moves, a dashed box round it)
                pad = wide / 2 + 4 * s
                c.create_rectangle(x0 - pad, min(ys) - pad, x1 + pad, max(ys) + pad, outline=BEND, dash=(4, 3))
            for x, y in ((x0, ys[0]), (x1, ys[-1])):
                c.create_oval(x - r, y - r, x + r, y + r, fill=BEND, outline=BEND)
            x, y, a = x1 + END_GAP * s, ys[-1], 3 * s  # (">": the end, where a drag makes it longer / shorter)
            c.create_line(x - a / 2, y - a, x + a / 2, y, x - a / 2, y + a, fill=BEND, width=max(1, round(1.5 * s)))
        self.draw_box8(c, s)  # (the 8-handle box round the selected notes: hz_box.py)
        got = self.depth_line()
        if got:  # (the picked piece's wobble depth: a line above it through its points (a handle each); dashed =
            x0, _, x1, _, base = got  # depth 0)
            _, spots, line = self.depth_shape()
            if max(base - y for _, y, _ in line) > 0.5:
                c.create_line(x0, base, x1, base, fill=look.HZ_WOBBLE, dash=(2, 3))
            c.create_line(*[v for x, y, _ in line for v in (x, y)], fill=look.HZ_WOBBLE, width=max(1, round(2 * s)))
            d = self.drag
            held = d["which"][1] if d and d["kind"] == "wobbledepth" and d["which"][0] == "pt" else None
            h = 3 * s
            for k, (hx, hy) in enumerate(spots):
                c.create_rectangle(hx - h, hy - h, hx + h, hy + h, outline=look.HZ_WOBBLE,
                                   fill=look.HZ_DOT if held == k else look.HZ_WOBBLE)
            for name, bx0, by0, bx1, by1 in self.preset_boxes():  # (the depth presets: a small picture each)
                c.create_rectangle(bx0, by0, bx1, by1, fill=look.HZ_LABEL_BOX, outline=look.HZ_WOBBLE)
                pad = 2 * s
                f = np.linspace(0.0, 1.0, 9)
                shape = DEPTH_PRESETS[name]
                v = line_at(shape, f)
                xs, ys = bx0 + pad + (bx1 - bx0 - 2 * pad) * f, by1 - pad - (by1 - by0 - 2 * pad) * v
                c.create_line(*np.column_stack([xs, ys]).ravel().tolist(), fill=look.HZ_WOBBLE,
                              width=max(1, round(1.5 * s)))
            held = d["end"] if d and d["kind"] == "wobbleend" else None
            for end, px, py in self.end_posts():  # (the end posts: a short stick down from each end, a dot at its foot)
                c.create_line(px, base, px, py, fill=look.HZ_WOBBLE, width=max(1, round(1.5 * s)))
                r = 3.5 * s
                c.create_oval(px - r, py - r, px + r, py + r, outline=look.HZ_WOBBLE,
                              fill=look.HZ_DOT if held == end else look.HZ_WOBBLE)
            post = self.speed_post()
            if post:  # (the speed post: an upright stick, a diamond on top, dragged sideways)
                px, foot, top = post
                c.create_line(px, foot, px, top, fill=look.HZ_WOBBLE, width=max(1, round(2 * s)))
                h = 4 * s
                c.create_polygon(px, top - h, px + h, top, px, top + h, px - h, top, fill=look.HZ_WOBBLE,
                                 outline=look.HZ_WOBBLE)
            self.draw_speed_strip(c, s)
        label = self.sketch_label()
        if label:
            x, y, text = label
            how = dict(text=text, anchor="s", fill=look.LABEL, font=look.font(8, "bold"))
            real = self.canvas  # (its size measured on the canvas itself, then drawn like everything else)
            t = real.create_text(x, y, **how)
            box = real.bbox(t)
            real.delete(t)
            under = self.ruler_h + 2 * s - box[1]  # (never under the bar numbers: a top-key note, hunt 2026-10-28)
            if under > 0:
                y += under
                box = (box[0], box[1] + under, box[2], box[3] + under)
            c.create_rectangle(box[0] - 3 * s, box[1] - s, box[2] + 3 * s, box[3] + s, fill=look.HZ_LABEL_BOX,
                               outline=BEND)
            c.create_text(x, y, **how)
