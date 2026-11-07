"""The piano roll's part of Recolour's Liquify (notes/liquify.py, window/liquify_window.py; user 2026-11-07, PLAN.md):
while the Liquify window is open, a left drag over a selected shape (any tool) brushes its colours; elsewhere the
piano roll works as usual. The brush is a circle on screen (its size in pixels, zoom never changes it), drawn at the
mouse; with Smoothing it trails behind on a string (a "lazy mouse": it moves only when the string is pulled tight).
Shift+click = a straight stroke from where the last one ended. One undo step a stroke. Each shape's map is changed
dab by dab along the brush's path; its notes are made again at most every LIVE_MS while dragging (all of them on
let go)."""

import math
import time

import numpy as np

from files.lang import tr
from notes import liquify as lq
from notes.recolour import KEEP, box_of, centre_at, clean_recolour, new_seed
from roll.roll_shared import SHIFT
from window import look

LIVE_MS = 30  # (the notes made again at most this often while brushing; longer when making them takes longer)
LIVE_SLOW = 0.15  # (s: making the notes takes longer = the picture is bent while brushing, the notes made on let go)
SPACING = 0.2  # (a dab every this much of the brush's radius along its path)


class LiquifyBrush:
    def liquify_on(self):
        return getattr(self.app, "liquify_window", None) is not None

    def liquify_radius(self):
        return self.app.liquify_window.size_px() / 2

    # ---- where things are

    def liquify_box(self, made):
        """A shape's notes' box on screen: (x0, y0, x1, y1)."""
        _, notes, _, _ = made
        t0, t1, k0, k1 = box_of(notes)
        ppq = self.app.ppq
        x0, x1 = self.t2x(t0 / ppq), self.t2x(t1 / ppq)
        y0, y1 = self.p2y(k1 + 0.5), self.p2y(k0 - 0.5)
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def liquify_unit(self, made, x, y):
        """Screen spot -> (x, y) in the shape's colour box (recolour.unit_xy's 0..1 box)."""
        rc, notes, own, _ = made
        b, p = self.from_xy(x, y)
        c = centre_at(rc, notes, own, b * self.app.ppq, p)
        return c[0] / 100.0, c[1] / 100.0

    def liquify_jac(self, made, x, y, at=None):
        """The colour box's units per screen pixel at (x, y): [[dx/dpx, dx/dpy], [dy/dpx, dy/dpy]]."""
        u0 = at or self.liquify_unit(made, x, y)
        ux, uy = self.liquify_unit(made, x + 1, y), self.liquify_unit(made, x, y + 1)
        return [[ux[0] - u0[0], uy[0] - u0[0]], [ux[1] - u0[1], uy[1] - u0[1]]]

    def liquify_shapes_at(self, x, y, r):
        """[(shape, made)] of the selected shapes Liquify can brush whose box (grown by the brush) holds (x, y)."""
        out = []
        for sh in self.app.recolour_targets():
            made = self.stir_made(sh, "liquify")
            if made is None:
                continue
            x0, y0, x1, y1 = self.liquify_box(made)
            if x0 - r <= x <= x1 + r and y0 - r <= y <= y1 + r:
                out.append((sh, made))
        return out

    # ---- the mouse

    def liquify_press(self, e):
        """A left press while the Liquify window is open: on / by a selected shape = a stroke starts (True)."""
        if not self.liquify_on():
            return False
        r = self.liquify_radius()
        hit = self.liquify_shapes_at(e.x, e.y, r)
        if not hit:
            return False
        app = self.app
        app.push_undo(name=tr("liquify.undo"))
        jobs = []
        for sh, made in hit:
            old = clean_recolour(sh.get("recolour"))
            got = (old or {}).get("liquify")
            if got:
                a = np.array(lq.decode(got["grid"], got["map"]))
            else:
                x0, y0, x1, y1 = self.liquify_box(made)
                a = lq.empty(lq.new_grid(x1 - x0, y1 - y0))
            jobs.append({"sh": sh, "made": made, "a": a, "box": self.liquify_box(made)})
        self.lq = {"jobs": jobs, "p": (e.x, e.y), "mouse": (e.x, e.y), "job": None, "changed": False, "took": 0.0,
                   "pv": self.liquify_preview_start(jobs)}
        self.drag = ("liquify",)
        end = getattr(self, "lq_end", None)
        if e.state & SHIFT and end:  # (Shift+click: a straight stroke from where the last one ended)
            self.liquify_stroke(end, (e.x, e.y))
            self.lq["p"] = (e.x, e.y)
        self.draw_liquify_brush()
        return True

    def liquify_drag(self, e):
        st = getattr(self, "lq", None)
        if not st:
            return
        st["mouse"] = (e.x, e.y)
        p = st["p"]
        w = self.app.liquify_window
        string = w.brush["smoothing"] / 100.0 * w.size_px() if w else 0.0
        dx, dy = e.x - p[0], e.y - p[1]
        d = math.hypot(dx, dy)
        if d > string:  # (the lazy mouse: the brush moves only once the string is pulled tight)
            k = (d - string) / d
            q = (p[0] + dx * k, p[1] + dy * k)
            self.liquify_stroke(p, q)
            st["p"] = q
        self.draw_liquify_brush()

    def liquify_release(self, e):
        st = getattr(self, "lq", None)
        self.drag = None
        if not st:
            return
        if st["job"]:
            self.after_cancel(st["job"])
            st["job"] = None
        if st["changed"]:
            self.liquify_apply(now=st["pv"] is not None)  # (the bent picture stays until the real notes are made)
            self.app.sync_recolour()
            self.app.schedule_autosave()
        self.lq_end = st["p"]
        self.lq = None
        self.draw_liquify_brush()

    def liquify_stroke(self, a, b):
        """Dabs from a to b (screen px; a left out) on every shape of the stroke, then their notes (soon)."""
        st = self.lq
        w = self.app.liquify_window
        if not w:
            return
        r = w.size_px() / 2
        tool, pressure, density = w.tool.get(), w.brush["pressure"], w.brush["density"]
        spots = lq.straight(a, b, max(1.0, r * SPACING))
        prev = a
        for q in spots:
            vec = (q[0] - prev[0], q[1] - prev[1])
            prev = q
            for job in st["jobs"]:
                x0, y0, x1, y1 = job["box"]
                if not (x0 - r <= q[0] <= x1 + r and y0 - r <= q[1] <= y1 + r):
                    continue
                c = self.liquify_unit(job["made"], *q)
                J = self.liquify_jac(job["made"], *q, at=c)
                job["a"] = lq.dab(job["a"], tool, c, r, J, pressure, density, vec=vec)
                st["changed"] = True
        if st["changed"] and not st["job"]:
            st["job"] = self.after(max(LIVE_MS, int(st["took"] * 1000)), self.liquify_live)

    def liquify_live(self):
        st = getattr(self, "lq", None)
        if not st:
            return
        st["job"] = None
        t0 = time.perf_counter()
        if st["pv"] is not None and self.liquify_preview(st["pv"]):
            st["took"] = time.perf_counter() - t0
            return
        st["pv"] = None
        self.liquify_apply()
        st["took"] = time.perf_counter() - t0

    def liquify_apply(self, now=False):
        """Every shape of the stroke gets its map as it is now; their notes made again (now: here, not in the
        background)."""
        for job in self.lq["jobs"]:
            sh = job["sh"]
            old = clean_recolour(sh.get("recolour"))
            base = dict(old) if old else {"mode": KEEP, "seed": new_seed()}
            keep = {k: v for k, v in (base.get("liquify") or {}).items() if k not in ("grid", "map")}
            a = job["a"]
            base["liquify"] = dict(keep, grid=[a.shape[1], a.shape[0]], map=lq.encode(a))
            new = clean_recolour(base)
            if new:
                sh["recolour"] = new
            else:
                sh.pop("recolour", None)
        self.app.shapes_changed(now=now)

    # ---- big shapes: the piano roll's picture bent while brushing

    def liquify_preview_start(self, jobs):
        """While making the notes is slow (App.late_ok's measure) and the piano roll shows them as one picture: what
        bending that picture needs, per shape (its notes' pixels on screen, each one's spot in the colour box), or
        None (the real notes are made while brushing instead)."""
        app = self.app
        if app._make_time <= LIVE_SLOW or self._tiles is None or self._img is None:
            return None
        kb, top = int(self.kb_w), int(self.ruler_h)
        ih, iw = self._img.shape[:2]
        if (self.winfo_width() - kb, self.winfo_height() - top) != (iw, ih):
            return None
        out = []
        for job in jobs:
            got = self.liquify_pixels(job["made"], kb, top, iw, ih)
            if got is not None:
                out.append((job, got))
        return {"base": self._img.copy(), "shapes": out}

    def liquify_pixels(self, made, kb, top, iw, ih):
        """The screen pixels (inside the picture) a shape's notes cover: {mask (ih, iw), x, y (picture pixels), u, v
        (colour box), Ji (screen px per colour box unit)}, or None (none on screen)."""
        rc, notes, own, _ = made
        ppq = self.app.ppq
        n = np.asarray(notes)
        x0 = np.floor(self.t2x(n[:, 0] / ppq)).astype(np.int64) - kb
        x1 = np.floor(self.t2x(n[:, 1] / ppq)).astype(np.int64) - kb
        k = n[:, 2].astype(np.int64)
        keep = (x1 > 0) & (x0 < iw)
        if not keep.any():
            return None
        x0, x1, k = np.clip(x0[keep], 0, iw), np.clip(x1[keep], 0, iw), k[keep]
        x1 = np.maximum(x1, x0 + 1)
        lo, hi = int(k.min()), int(k.max())
        # (each key row's covered columns: +1 at a start, -1 at an end, added up along the row; bincount: np.add.at
        # took ~0.15 s on 3 M notes)
        size = (hi - lo + 1) * (iw + 1)
        row0 = (k - lo) * (iw + 1)
        rows = (np.bincount(row0 + x0, minlength=size) - np.bincount(row0 + x1, minlength=size)).reshape(-1, iw + 1)
        rows = np.cumsum(rows, axis=1)[:, :iw] > 0
        ys = np.arange(ih) + top
        key = np.floor(self.y2p(ys) + 0.5).astype(np.int64)  # (the key row each picture row shows)
        on = (key >= lo) & (key <= hi)
        mask = np.zeros((ih, iw), bool)
        mask[on] = rows[key[on] - lo]
        py, px = np.nonzero(mask)
        if not len(px):
            return None
        # (screen -> colour box is a straight (affine) map: one spot + J give every pixel's, no lookup per pixel)
        mx, my = (iw / 2 + kb, ih / 2 + top)
        c0 = self.liquify_unit(made, mx, my)
        J = np.asarray(self.liquify_jac(made, mx, my, at=c0), np.float64)
        try:
            Ji = np.linalg.inv(J)
        except np.linalg.LinAlgError:
            return None
        dx, dy = px + kb + 0.5 - mx, py + top + 0.5 - my
        u = c0[0] + J[0, 0] * dx + J[0, 1] * dy
        v = c0[1] + J[1, 0] * dx + J[1, 1] * dy
        return {"mask": mask, "x": px, "y": py, "u": u, "v": v, "Ji": Ji}

    def liquify_preview(self, pv):
        """The picture as it was at the press, each shape's pixels taking the colour of the pixel its map fetches
        from (a pixel off the shape's notes keeps its own); shown. False: the picture can't be used any more."""
        if self._tiles is None or self._img is None or self._img.shape != pv["base"].shape:
            return False
        base = pv["base"]
        img = base.copy()
        ih, iw = base.shape[:2]
        for job, g in pv["shapes"]:
            a = job["a"]
            gy, gx = a.shape[:2]
            hot = lq.touched(a, g["u"], g["v"])
            if not hot.any():
                continue
            u, v = g["u"][hot], g["v"][hot]
            d = lq.sample(a, u, v) * (self.liquify_strength(job["sh"]) / 100)
            Ji = g["Ji"]
            sx = np.rint(g["x"][hot] + Ji[0, 0] * d[:, 0] + Ji[0, 1] * d[:, 1]).astype(np.int64)
            sy = np.rint(g["y"][hot] + Ji[1, 0] * d[:, 0] + Ji[1, 1] * d[:, 1]).astype(np.int64)
            ok = (sx >= 0) & (sx < iw) & (sy >= 0) & (sy < ih)
            ok[ok] = g["mask"][sy[ok], sx[ok]]
            ty, tx = g["y"][hot][ok], g["x"][hot][ok]
            img[ty, tx] = base[sy[ok], sx[ok]]
        self._img = img
        self.show_image()
        return True

    @staticmethod
    def liquify_strength(sh):
        got = (clean_recolour(sh.get("recolour")) or {}).get("liquify") or {}
        return got.get("strength", 100.0) if got.get("on", True) else 0.0

    # ---- the brush on screen

    def liquify_motion(self, e):
        if self.liquify_on() or getattr(self, "lq_mouse", None):
            self.lq_mouse = (e.x, e.y)
            self.draw_liquify_brush()

    def liquify_leave(self, e=None):
        if getattr(self, "lq_mouse", None):
            self.lq_mouse = None
            self.draw_liquify_brush()

    def draw_liquify_brush(self):
        """The brush circle (and the inner one where its full push ends: Density) at the brush's spot; while
        smoothing trails it, a thin line to the mouse (the string)."""
        self.delete("lqbrush")
        w = getattr(self.app, "liquify_window", None)
        st = getattr(self, "lq", None)
        m = (st or {}).get("mouse") or getattr(self, "lq_mouse", None)
        if not w or not m or self.sx is None or m[0] < self.kb_w or m[1] < self.ruler_h:
            return
        x, y = st["p"] if st else m
        r = w.size_px() / 2
        line = max(1, round(self.scale))
        band = line + 2 * max(1, round(self.scale))
        for width, col in ((band, look.ROLL_BG), (line, look.LIQUIFY_BRUSH)):
            self.create_oval(x - r, y - r, x + r, y + r, outline=col, width=width, tags="lqbrush")
        h = r * w.brush["density"] / 100.0
        if 2 < h < r - 2:
            self.create_oval(x - h, y - h, x + h, y + h, outline=look.LIQUIFY_BRUSH, width=line, dash=(2, 3),
                             tags="lqbrush")
        if st and math.hypot(m[0] - x, m[1] - y) > 1:
            for width, col in ((band, look.ROLL_BG), (line, look.LIQUIFY_BRUSH)):
                self.create_line(x, y, m[0], m[1], fill=col, width=width, tags="lqbrush")
        self.tag_raise("lqbrush")
