"""The piano roll's part of Recolour's Liquify (notes/liquify.py, window/liquify_window.py; user 2026-11-07, PLAN.md):
while the Liquify window is open, a left drag over a selected shape (any tool) brushes its colours; elsewhere the
piano roll works as usual. The brush is a circle on screen (its size in pixels, zoom never changes it), drawn at the
mouse; with Smoothing it trails behind on a string (a "lazy mouse": it moves only when the string is pulled tight).
Shift+click = a straight stroke from where the last one ended. One undo step a stroke. Each shape's map is changed
dab by dab along the brush's path. While dragging, the REAL new colours show (B check-in 2026-11-07: a bent picture
of the piano roll looked nothing like the result, and making every note again was slow): only the notes whose
colour changed are worked out (notes/liquify.Live) and painted again on the piano roll's picture, pixel for pixel
what letting go gives (test liquify_live); the notes are made on let go. Where that can't follow (few notes = no
picture, Single channel, masks, sliced pieces...), every note is made again at most every LIVE_MS instead."""

import math
import time

import numpy as np

from files.lang import tr
from notes import liquify as lq
from files.speed import key_sort
from notes.recolour import (KEEP, before_liquify, box_of, centre_at, clean_recolour, new_seed, rule_of, turned_map,
                            with_most, without_recolour)
from roll.roll_shared import SHIFT
from window import look

LIVE_MS = 30  # (every note made again at most this often while brushing (not shown live); longer when it takes longer)
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
                   "way": self.liquify_way(jobs)}
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
            # (shown live: the picture stays until the real notes are made, the very same colours)
            self.liquify_apply(now=st["way"] == "live")
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
            if st["way"] == "live":  # (when the mouse moves stop coming in: every waiting one is in the map by then)
                st["job"] = self.after_idle(self.liquify_live)
            else:
                st["job"] = self.after(max(LIVE_MS, int(st["took"] * 1000)), self.liquify_live)

    def liquify_way(self, jobs):
        """How a stroke shows its colours: "live" = only the changed notes worked out and painted (Live), "later" =
        the notes made once first (a shape without a Recolour yet), then live, "full" = every note made again."""
        for job in jobs:
            job["live"] = self.liquify_live_start(job)
        if any(job["live"] is None for job in jobs):
            return "full"
        if any(job["live"] == "later" for job in jobs):
            return "later"
        self._img = self._img.copy()  # (painted on in place while brushing: no other picture kept shares it)
        self._shown = self._img
        for job in jobs:
            job["live"]["img"] = self._img
        return "live"

    def liquify_live(self):
        st = getattr(self, "lq", None)
        if not st:
            return
        st["job"] = None
        t0 = time.perf_counter()
        if st["way"] == "later":  # (its first Recolour: the notes made once, then shown live)
            self.liquify_apply(now=True)
            self.redraw()  # (the new picture now: the live painting goes on it)
            for job in st["jobs"]:
                job["made"] = self.stir_made(job["sh"], "liquify") or job["made"]
            st["way"] = self.liquify_way(st["jobs"])
            if st["way"] == "later":
                st["way"] = "full"
        elif st["way"] == "live":
            if all(self.liquify_show(job) for job in st["jobs"]):
                st["took"] = time.perf_counter() - t0
                return
            st["way"] = "full"  # (something it can't follow: every note made from here on)
            self.liquify_apply()
        else:
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

    # ---- the real colours while brushing, painted only where they changed

    def liquify_live_start(self, job):
        """What showing a shape's real new colours while brushing needs (notes/liquify.Live: the colours worked out
        only where the map changed, exactly as on let go; its notes on the piano roll's picture repainted only
        there), or None: the notes are made whole while brushing instead (few notes, or something this can't
        follow: then making them is the only exact way). "later": the shape gets its Recolour with this stroke (its
        channels may move with it): its notes are made once first, then this."""
        app = self.app
        sh = job["sh"]
        from notes.engine import is_gate_flipped, is_pasted_flipped, past_edges, pasted_notes
        from notes.mask import is_mask
        from notes.sustain import is_sustained
        if (self._img is None or self._tiles is None or app.channel_mode.get() != "auto" or len(app.sels) != 1
                or getattr(app, "loaded", None) is not None or self.hidden_shade()
                or app.mask_preview.get() and any(is_mask(s) for s in app.shapes)
                or app.notes_late or getattr(app, "_bg_notes", False) or sh.get("cut") or sh.get("colour_cut")
                or "picture" in sh or is_sustained(sh) or past_edges(sh, app.keys, app.ppq) is not None
                or getattr(self, "copy_src", None)):
            return None
        kb, top = int(self.kb_w), int(self.ruler_h)
        ih, iw = self._img.shape[:2]
        if (self.winfo_width() - kb, self.winfo_height() - top) != (iw, ih):
            return None
        idx = next((i for i, s in enumerate(app.shapes) if s is sh), None)
        if idx is None or idx not in app.sels:
            return None
        old = clean_recolour(sh.get("recolour"))
        if old is None:
            return "later"
        _, notes, own, _ = job["made"]
        _, tracks = app.notes_tracks(without_recolour(sh))
        base = dict(old)
        settings = {k: v for k, v in (base.get("liquify") or {}).items() if k not in ("grid", "map")}
        # (the rule as the notes are made with this stroke's map: any map stands in, Live is handed the real one)
        base["liquify"] = dict(settings, grid=[2, 2], map=lq.encode(np.full((2, 2, 2), 1.0 / lq.SCALE)))
        rule = rule_of(with_most(dict(sh, recolour=base), app.all16())["recolour"])
        if rule.get("stir"):
            rule["ppq"] = app.ppq
        held_ = is_sustained(sh) or sh.get("held") is True or pasted_notes(sh)
        gate = is_pasted_flipped(sh) or is_gate_flipped(sh)
        got = before_liquify(notes, tracks, rule, held_, gate, own)
        if got is None:
            return None
        rule, n, lap, tw, args = got
        # (the notes on the piano roll now: this shape's rows, in start order (painting needs it), and its notes as
        # made now (their colours -> the channel each colour is on: a table))
        drawn = app.drawn
        so = self.start_order()
        mine = so[drawn[so, 5] == idx]
        mine = mine[drawn[mine, 3] >= -1]  # (faint copies of other tracks: painted under everything, left alone)
        mn, mt = app.notes_tracks(sh)
        if not len(mine) or mt is None or not len(mn):
            return None
        R = np.array(drawn[mine])
        code = R[:, 0] * 512 + R[:, 2]
        order = None if (np.diff(code) >= 0).all() else key_sort(np.ascontiguousarray(R[:, 0]),
                                                                  np.ascontiguousarray(R[:, 2]))
        return {"R": R, "code": code if order is None else code[order], "order": order, "n": n, "lap": lap,
                "turn": turned_map(tw) if tw else None, "img": self._img, "made": (mn, np.asarray(mt, np.int64)),
                "slots": {}, "starts": np.ascontiguousarray(R[:, 0]), "longest": int((R[:, 1] - R[:, 0]).max()),
                "every": self.every_slot(), "calc": lq.Live(*args[:4], rule, *args[4:], lq.rounded(job["a"]))}

    def liquify_slot(self, live, track):
        """The channel slot a colour (track) of the shape is on now (render gives each of a shape's tracks one: its
        notes made now show it), or None: none of its notes has that colour now."""
        got = live["slots"].get(track, False)
        if got is not False:
            return got
        mn, mt = live["made"]
        at = np.flatnonzero(mt == track)[:1]
        p = self.liquify_rows(live, mn[at]) if len(at) else at
        got = live["slots"][track] = int(live["R"][p[0], 4]) if len(p) and p[0] >= 0 else None
        return got

    @staticmethod
    def liquify_rows(live, notes):
        """Each note's row in live["R"] (the same start and key), -1 when it isn't on the piano roll."""
        code = live["code"]
        want = np.asarray(notes[:, 0], np.int64) * 512 + np.asarray(notes[:, 2], np.int64)
        p = np.minimum(np.searchsorted(code, want), len(code) - 1)
        p = np.where(code[p] == want, p, -1)
        if live["order"] is not None:
            p = np.where(p >= 0, live["order"][np.maximum(p, 0)], -1)
        return p

    def liquify_show(self, job):
        """A shape's notes whose colour changed with the map: their channels in its rows, then the picture repainted
        where they are. False: it can't (a colour on a channel not known yet, the picture changed under it)."""
        live = job["live"]
        if live["img"] is not self._img:
            return False
        got = live["calc"].update(lq.rounded(job["a"]))
        if got is None:
            return True
        B, L = got
        n = live["n"]
        t = np.asarray(live["lap"][B] + (live["turn"](L) if live["turn"] else L), np.int64)
        kinds, which = np.unique(t, return_inverse=True)
        slots = [self.liquify_slot(live, v) for v in kinds.tolist()]
        if None in slots:
            return False
        slot = np.asarray(slots, np.int64)[which.reshape(-1)]
        p = self.liquify_rows(live, n[B])
        ok = p >= 0
        p, slot = p[ok], slot[ok]
        R = live["R"]
        ch = R[p, 4] != slot
        if not ch.any():
            return True
        p = p[ch]
        R[p, 4] = slot[ch]
        self.liquify_paint(live, p)
        return True

    def liquify_paint(self, live, p):
        """The picture repainted where rows p of R (one shape's notes, start order, their new channels) are: every
        note of R there painted again on top, in the piano roll's own order (the brushed shape is selected: its
        notes are on top of all others), then the red line; the same pixels note_rects + paint_rects give."""
        from roll.roll_draw import ghost_code, stack_keys, stack_rule
        from roll.roll_shared import note_colours, slot_colours
        from files.speed import loops
        app, ppq = self.app, self.app.ppq
        R = live["R"]
        kb, top = int(self.kb_w), int(self.ruler_h)
        w, h = self.winfo_width(), self.winfo_height()
        ax, bx = self.sx / ppq, self.kb_w - self.view_t * self.sx + self.note_x_shift
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        x0 = np.round(R[p, 0] * ax + bx)
        x1 = np.round(R[p, 1] * ax + bx)
        kp = R[p, 2]
        X0, X1 = max(kb, int(x0.min()) - 1), min(w, int(x1.max()) + 2)
        Y0 = max(top, int(np.round((kp.max() + 0.5) * ay + by)) - 1)
        Y1 = min(h, int(np.round((kp.min() - 0.5) * ay + by)) + 2)
        if X1 <= X0 or Y1 <= Y0:
            return
        keys = np.arange(app.keys)
        row0 = np.round((keys + 0.5) * ay + by).astype(np.int64)
        row1 = np.round((keys - 0.5) * ay + by).astype(np.int64)
        shown = ~((row1 < top) | (row0 > h))
        row1 = np.maximum(row1, row0 + 1)
        shown &= ~((row1 < Y0) | (row0 >= Y1))
        t_lo, t_hi = self.x2t(X0 - 2) * ppq, self.x2t(X1 + 2) * ppq
        longest, starts = live["longest"], live["starts"]
        lo = int(np.searchsorted(starts, math.ceil(t_lo - longest), "left"))
        hi = int(np.searchsorted(starts, math.floor(t_hi), "right"))
        notes = R[lo:hi]
        notes = notes[shown[np.clip(notes[:, 2], 0, app.keys - 1)]]
        # (note_rects' screen(): the same sums, order and colour numbers)
        nx0, nx1 = np.round(notes[:, 0] * ax + bx), np.round(notes[:, 1] * ax + bx)
        on = ~((nx1 < kb) | (nx0 > w)) & shown[notes[:, 2]]
        on &= ~((nx1 < X0) | (nx0 >= X1))
        on = np.flatnonzero(on)
        if len(on):
            key_of = stack_keys(notes[on], list(self.on_top()), getattr(app, "behind_owner", None), stack_rule(app),
                                live["every"])[0]
            on = on[np.argsort(key_of, kind="stable")]
        color = note_colours(notes[on, 4], slot_colours(getattr(app, "picture_use10", False)))
        vel = notes[on, 3]
        code = color * 32 + np.clip(vel, 0, 127) // 4
        if len(vel) and vel.min() < 0:
            code = np.where(vel < 0, ghost_code(color, vel), code)
        cols = [nx0[on].astype(np.int64), nx1[on].astype(np.int64), notes[on, 2].astype(np.int64), code]
        left, right = kb - 2, int(w) + 2
        fast = loops()
        if fast and (not len(cols[2]) or (cols[2].min() >= 0 and cols[2].max() < app.keys)):
            rects = fast.last_on_pixels(*(np.ascontiguousarray(v, np.int64) for v in cols), left, right, app.keys,
                                        row0, row1)
        else:
            a, b, key, c = cols
            a, b = np.maximum(a, left), np.minimum(b, right)
            m = len(a)
            if m > 1:
                size = b - a
                small = np.flatnonzero((size < 4) & (size >= 0))
                spot = ((a[small] - left) * app.keys + key[small]) * 4 + size[small]
                last = np.empty((right - left + 1) * app.keys * 4, np.int64)
                last[spot] = small
                keep = np.ones(m, bool)
                keep[small] = last[spot] == small
                a, b, key, c = a[keep], b[keep], key[keep], c[keep]
            rects = (a, row0[key], np.maximum(b - 1, a), row1[key] - 1, c)
        region = (X0, Y0, X1, Y1)
        sub = np.ascontiguousarray(self._img[Y0 - top:Y1 - top, X0 - kb:X1 - kb])
        self.paint_rects(sub, rects, region, True)
        self._img[Y0 - top:Y1 - top, X0 - kb:X1 - kb] = sub
        self._tiles.put(self._img, X0 - kb, Y0 - top, X1 - kb, Y1 - top)

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
