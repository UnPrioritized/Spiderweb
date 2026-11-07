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
from files.system import ALT
from notes import liquify as lq
from files.speed import key_sort
from notes.recolour import (KEEP, before_liquify, box_of, can_recolour, centre_at, clean_recolour, new_seed, rule_of,
                            turned_map, with_most, without_recolour)
from roll.roll_shared import CTRL, SHIFT
from window import look

LIVE_MS = 30  # (every note made again at most this often while brushing (not shown live); longer when it takes longer)
SPACING = 0.2  # (a dab every this much of the brush's radius along its path)
HOLD_MS = 40  # (holding still: a dab this often, at Rate % of the pressure; 25 a second)
CATCH_REST = 0.08  # (s the mouse rests before a trailing (Smoothing) brush starts catching up with it)
CATCH_SHARE = 0.3  # (of the way left, every HOLD_MS: eases in, ~0.4 s for a long string)
SNAP_ANGLE = math.radians(15)  # (Shift+Ctrl+click: a straight stroke's way snapped to every 15 degrees, user 11-07)
ARROW_EVERY = 70  # (px between the direction arrows on a straight stroke's line)
MASK_STEP = 3  # (the freeze mask's red tint worked out every this many screen pixels, then stretched: it's soft)
MASK_ALPHA = 0.45  # (fully frozen: this see-through)


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
        app = self.app
        if not hit:  # (on another shape (or none was selected): that one picked and brushed, never moved: user
            # 11-07, "liquify should always do the brush, not moving the shape itself"; empty space: as usual)
            i = self.shape_at(e.x, e.y, prefer_selected=False)
            if i is None or not can_recolour(app.shapes[i]):
                return False
            app.select(i)
            hit = self.liquify_shapes_at(e.x, e.y, r)
            if not hit:
                return False
        if e.state & SHIFT and self.liquify_start() is None:  # (Shift+click with no start yet (nothing brushed on
            self.liquify_set_start(e.x, e.y)  # these shapes, Esc): it only places the start, user 11-07: a straight
            self.draw_liquify_brush()  # stroke's start "seems like it's randomly decided")
            return True
        tool = app.liquify_window.tool.get()
        app.push_undo(name=tr("liquify.undo_mask" if tool in lq.MASK_TOOLS else "liquify.undo"))
        if tool not in lq.MASK_TOOLS:
            hit = self.liquify_shown_again(hit)
        jobs = []
        for sh, made in hit:
            got = (clean_recolour(sh.get("recolour")) or {}).get("liquify")
            if got:
                a, m = np.array(lq.map_of(got)), lq.mask_of(got)
                m = None if m is None else np.array(m)
            else:
                x0, y0, x1, y1 = self.liquify_box(made)
                a, m = lq.empty(lq.new_grid(x1 - x0, y1 - y0)), None
            jobs.append({"sh": sh, "made": made, "a": a, "m": m, "box": self.liquify_box(made)})
        self.lq = {"jobs": jobs, "p": (e.x, e.y), "mouse": (e.x, e.y), "job": None, "changed": False, "took": 0.0,
                   "masked": False, "mask_job": None, "alt": bool(e.state & ALT), "dabbed": time.perf_counter(),
                   "way": self.liquify_way(jobs) if tool not in lq.MASK_TOOLS else "mask"}
        self.drag = ("liquify",)
        end = self.liquify_start()
        if e.state & SHIFT and end:  # (Shift+click: a straight stroke from where the last one ended; + Ctrl: its
            q = self.liquify_aim(end, (e.x, e.y), e.state)  # way snapped to every 15 degrees)
            self.liquify_stroke(end, q)
            self.lq["p"] = self.lq["mouse"] = q
        else:  # (a click alone works too: Twirl / Pinch / Bulge / Freeze... at the spot, like the editors)
            self.liquify_dabs([(e.x, e.y)], (e.x, e.y))
        self.lq["hold"] = self.after(HOLD_MS, self.liquify_hold)
        self.draw_liquify_brush()
        return True

    def liquify_shown_again(self, hit):
        """Brushing a shape whose Liquify is hidden (the Recolour row's tick off) shows it again first, in the
        stroke's undo step (my pick: else the brush works blind); its notes made now. hit again, made anew."""
        hidden = []
        for sh, _ in hit:
            rc = clean_recolour(sh.get("recolour"))
            got = (rc or {}).get("liquify")
            if got and got.get("on") is False:
                sh["recolour"] = clean_recolour(dict(rc, liquify={k: v for k, v in got.items() if k != "on"}))
                hidden.append(sh)
        if not hidden:
            return hit
        self.app.shapes_changed(now=True)
        self.app.sync_recolour()
        self.redraw()
        return [(sh, self.stir_made(sh, "liquify") or made) for sh, made in hit]

    def liquify_tool(self):
        """The tool a dab uses now and Twirl's way round (+1 / -1): Alt held = the opposite (Twirl turns back, Pinch
        <-> Bulge, Freeze <-> Thaw; like the editors' Alt)."""
        w = self.app.liquify_window
        tool = w.tool.get()
        turn = w.turn()
        if (self.lq or {}).get("alt"):
            tool = lq.OPPOSITE.get(tool, tool)
            turn = -turn
        return tool, turn

    def liquify_alt(self, e, down):
        """Alt pressed / let go while brushing (holding still too: the hold-still dabs follow it at once)."""
        st = getattr(self, "lq", None)
        if st:
            st["alt"] = down
            return "break"  # (Alt was for the brush: not the menu bar's)
        return None

    def liquify_hold(self):
        """While the button is held: the brush still = dabs at its spot (Rate %, like the editors: Twirl, Pinch,
        Bulge, Restore, Smooth, Freeze / Thaw keep working; Push needs moving)."""
        st = getattr(self, "lq", None)
        w = self.app.liquify_window
        if not st or not w:
            return
        st["hold"] = self.after(HOLD_MS, self.liquify_hold)
        p, m = st["p"], st["mouse"]
        if time.perf_counter() - st.get("moved", 0.0) > CATCH_REST and math.hypot(m[0] - p[0], m[1] - p[1]) > 0.5:
            # (the mouse rests: a smoothing brush catches up with it, image editors' "stroke catch-up"; user 11-07:
            # the circle stayed behind once the mouse stopped)
            k = 1.0 if math.hypot(m[0] - p[0], m[1] - p[1]) < 2 else CATCH_SHARE
            q = (p[0] + (m[0] - p[0]) * k, p[1] + (m[1] - p[1]) * k)
            self.liquify_stroke(p, q)
            st["p"] = q
            self.draw_liquify_brush()
            return
        rate = w.brush["rate"]
        tool = self.liquify_tool()[0]
        if rate <= 0 or tool == "push" or time.perf_counter() - st["dabbed"] < HOLD_MS / 1000 * 0.9:
            return
        self.liquify_dabs([st["p"]], st["p"], rate / 100.0)

    def liquify_drag(self, e):
        st = getattr(self, "lq", None)
        if not st:
            return
        st["mouse"] = (e.x, e.y)
        st["moved"] = time.perf_counter()
        st["alt"] = bool(e.state & ALT)
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
        for k in ("hold", "mask_job"):
            if st.get(k):
                self.after_cancel(st[k])
                st[k] = None
        if st["job"]:
            self.after_cancel(st["job"])
            st["job"] = None
            if st["way"] == "live":  # (the last mouse moves shown first: they came with the let go, user 11-07:
                self.liquify_live()  # "every stroke shifts its pixels once I let go")
        if st["changed"] or st["masked"]:
            # (shown live: the picture stays until the real notes are made, the very same colours; a mask stroke
            # alone makes no notes (engine.made_from leaves the mask out): nothing made again, not even the others)
            self.liquify_apply(now=st["way"] == "live", notes=st["changed"])
            self.app.sync_recolour()
            self.app.schedule_autosave()
        self.liquify_set_start(*st["p"])
        self.lq = None
        self.draw_liquify_brush()

    # ---- straight strokes (Shift+click): their start, shown while Shift is held

    def liquify_set_start(self, x, y):
        """The next straight stroke starts here (where a stroke ended / a Shift+click placed it): kept in beats and
        keys (it stays on the notes when the view moves), for the shapes selected now."""
        b, p = self.from_xy(x, y)
        self.lq_end = (b, p, frozenset(self.app.sels))

    def liquify_start(self):
        """The straight stroke's start on screen, or None: none placed, or other shapes selected since."""
        end = getattr(self, "lq_end", None)
        if not end or end[2] != frozenset(self.app.sels):
            return None
        return self.to_xy((end[0], end[1]))

    def liquify_forget(self, e=None):
        """Esc / the window closed or opened: no start for a straight stroke any more."""
        if getattr(self, "lq_end", None) and not getattr(self, "lq", None):
            self.lq_end = None
            self.draw_liquify_brush()

    @staticmethod
    def liquify_aim(a, m, state):
        """Where a straight stroke from a towards the mouse m ends: m, or with Ctrl held its way snapped to the
        nearest 15 degrees (the mouse's distance along that way; user 11-07)."""
        if not state & CTRL:
            return m
        dx, dy = m[0] - a[0], m[1] - a[1]
        if not (dx or dy):
            return m
        ang = round(math.atan2(dy, dx) / SNAP_ANGLE) * SNAP_ANGLE
        ux, uy = math.cos(ang), math.sin(ang)
        d = dx * ux + dy * uy
        return a[0] + ux * d, a[1] + uy * d

    def liquify_keys(self, e, down):
        """Shift / Ctrl pressed or let go over the piano roll: the straight stroke's line shown / snapped again."""
        if self.liquify_on() and getattr(self, "lq_mouse", None):
            bit = SHIFT if e.keysym.startswith("Shift") else CTRL
            self.lq_state = (getattr(self, "lq_state", 0) | bit) if down else (getattr(self, "lq_state", 0) & ~bit)
            self.draw_liquify_brush()

    def liquify_stroke(self, a, b):
        """Dabs from a to b (screen px; a left out) on every shape of the stroke, then their notes (soon)."""
        w = self.app.liquify_window
        if not w:
            return
        self.liquify_dabs(lq.straight(a, b, max(1.0, w.size_px() / 2 * SPACING)), a)

    def liquify_dabs(self, spots, prev, scale=1.0):
        """A dab at each spot (screen px; Push: pushed by the move from the one before, prev first) on every shape
        of the stroke under it, pressure x scale; then their colours (soon) / the mask shown again."""
        st = self.lq
        w = self.app.liquify_window
        if not w or not spots:
            return
        r = w.size_px() / 2
        tool, turn = self.liquify_tool()
        pressure, density = w.brush["pressure"] * scale, w.brush["density"]
        mask = tool in lq.MASK_TOOLS
        st["dabbed"] = time.perf_counter()
        for q in spots:
            vec = (q[0] - prev[0], q[1] - prev[1])
            prev = q
            for job in st["jobs"]:
                x0, y0, x1, y1 = job["box"]
                if not (x0 - r <= q[0] <= x1 + r and y0 - r <= q[1] <= y1 + r):
                    continue
                c = self.liquify_unit(job["made"], *q)
                J = self.liquify_jac(job["made"], *q, at=c)
                if mask:
                    m = job["m"] if job["m"] is not None else np.zeros(job["a"].shape[:2])
                    job["m"] = lq.mask_dab(m, c, r, J, pressure, density, freeze=tool == "freeze")
                    st["masked"] = True
                else:
                    job["a"] = lq.dab(job["a"], tool, c, r, J, pressure, density, vec=vec, turn=turn,
                                      frozen=job["m"])
                    st["changed"] = True
        if mask:
            if st["masked"] and not st["mask_job"]:
                st["mask_job"] = self.after_idle(self.liquify_mask_shown)
            return
        if not st["changed"]:
            return
        if st["way"] == "mask":  # (a mask tool's stroke went on with another tool: Alt can't, the window can)
            st["way"] = "full"
        if st["way"] == "live":
            if time.perf_counter() - st.get("shown", 0.0) > LIVE_MS / 1000:  # (mouse moves never stopping coming
                if st["job"]:                                                 # in: no idle time, shown now)
                    self.after_cancel(st["job"])
                self.liquify_live()
            elif not st["job"]:  # (when the mouse moves stop coming in: every waiting one is in the map by then)
                st["job"] = self.after_idle(self.liquify_live)
        elif st["way"] == "later":  # (the notes made once, at the first real push, then live: on a timer a quick
            if st["job"]:  # first stroke showed nothing until let go, E's find)
                self.after_cancel(st["job"])
                st["job"] = None
            self.liquify_live()
        elif not st["job"]:
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
            if not any(job["a"].any() for job in st["jobs"]):  # (nothing pushed yet (a Push's press): no Recolour
                return  # to give it yet, else it stayed "later" and every note was made from then on)
            self.liquify_apply(now=True)
            self.redraw()  # (the new picture now: the live painting goes on it)
            for job in st["jobs"]:
                job["made"] = self.stir_made(job["sh"], "liquify") or job["made"]
            st["way"] = self.liquify_way(st["jobs"])
            if st["way"] == "later":
                st["way"] = "full"
        elif st["way"] == "live":
            if all(self.liquify_show(job) for job in st["jobs"]):
                st["shown"] = time.perf_counter()
                st["took"] = st["shown"] - t0
                return
            st["way"] = "full"  # (something it can't follow: every note made from here on)
            self.liquify_apply()
        else:
            self.liquify_apply()
        st["took"] = time.perf_counter() - t0

    def liquify_apply(self, now=False, notes=True):
        """Every shape of the stroke gets its map (and freeze mask) as it is now; their notes made again (now: here,
        not in the background; notes False: only the mask changed, nothing to make)."""
        for job in self.lq["jobs"]:
            sh = job["sh"]
            old = clean_recolour(sh.get("recolour"))
            base = dict(old) if old else {"mode": KEEP, "seed": new_seed()}
            w = self.app.liquify_window
            # (a shape's first Liquify: At the edges as the window shows it)
            settings = base.get("liquify") or ({"edge": w.edge} if w else {})
            got = lq.with_arrays(settings, job["a"], job["m"])
            if got:
                base["liquify"] = got
            else:
                base.pop("liquify", None)
            new = clean_recolour(base)
            if new:
                sh["recolour"] = new
            else:
                sh.pop("recolour", None)
        if notes:
            self.app.shapes_changed(now=now)
        else:
            self.draw_liquify_mask()

    # ---- the real colours while brushing, painted only where they changed

    def liquify_live_start(self, job):
        """What showing a shape's real new colours while brushing needs (notes/liquify.Live: the colours worked out
        only where the map changed, exactly as on let go; its notes on the piano roll's picture repainted only
        there), or None: the notes are made whole while brushing instead (few notes, or something this can't
        follow: then making them is the only exact way). "later": the shape gets its Recolour with this stroke (its
        channels may move with it): its notes are made once first, then this."""
        app = self.app
        sh = job["sh"]
        from notes.engine import is_gate_flipped, is_pasted_flipped, made_from, past_edges, pasted_notes
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
        # (as its notes are made: without the freeze mask (engine.made_from); only a mask = no Recolour yet)
        old = clean_recolour(made_from(sh).get("recolour"))
        if old is None:
            return "later"
        _, notes, own, _ = job["made"]
        _, tracks = app.notes_tracks(without_recolour(sh))
        base = dict(old)
        settings = {k: v for k, v in (base.get("liquify") or {}).items() if k not in ("grid", "map", "frozen")}
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
        own_rows = drawn[:, 5] == idx
        if own_rows.all():  # (the only shape: every row, no gathering; stage F, 7.7 M notes)
            mine = so
        else:
            mine = so[own_rows[so]]
        faint = drawn[mine, 3] < -1  # (faint copies of other tracks: painted under everything, left alone)
        if faint.any():
            mine = mine[~faint]
        mn, mt = app.notes_tracks(sh)
        if not len(mine) or mt is None or not len(mn):
            return None
        # (this shape's rows of the piano roll's notes in start order: "rows" into drawn (never changed), their
        # channel slots in "S" (changed as the colours change; stage F: a whole copy of the rows took ~0.2 s))
        S = np.ascontiguousarray(drawn[mine, 4], np.int64)
        R0 = np.ascontiguousarray(drawn[mine, 0], np.int64)
        R2 = np.ascontiguousarray(drawn[mine, 2], np.int64)
        code = R0 * 512 + R2
        order = None if (np.diff(code) >= 0).all() else key_sort(R0, R2)
        # (the rows by key, then start: painting looks up only the key rows it repaints, stage F: at 7.7 M notes every
        # key's notes in the brush's time took most of an update)
        by_key = key_sort(R2, R0)
        key_at = np.searchsorted(R2[by_key], np.arange(app.keys + 1))
        return {"drawn": drawn, "rows": mine, "S": S, "by_key": by_key, "key_at": key_at,
                "key_starts": np.ascontiguousarray(R0[by_key]),
                "code": code if order is None else code[order], "order": order, "n": n, "lap": lap,
                "turn": turned_map(tw) if tw else None, "img": self._img, "made": (mn, np.asarray(mt, np.int64)),
                "slots": {}, "longest": int((drawn[mine, 1] - R0).max()),
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
        got = live["slots"][track] = int(live["S"][p[0]]) if len(p) and p[0] >= 0 else None
        return got

    @staticmethod
    def liquify_rows(live, notes):
        """Each note's row in live["rows"] (the same start and key), -1 when it isn't on the piano roll."""
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
        S = live["S"]
        ch = S[p] != slot
        if not ch.any():
            return True
        p = p[ch]
        S[p] = slot[ch]
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
        drawn, rows = live["drawn"], live["rows"]
        kb, top = int(self.kb_w), int(self.ruler_h)
        w, h = self.winfo_width(), self.winfo_height()
        ax, bx = self.sx / ppq, self.kb_w - self.view_t * self.sx + self.note_x_shift
        ay, by = -self.sy, self.ruler_h + self.view_top * self.sy
        P = drawn[rows[p]]
        x0 = np.round(P[:, 0] * ax + bx)
        x1 = np.round(P[:, 1] * ax + bx)
        kp = P[:, 2]
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
        longest = live["longest"]
        # (each shown key row's notes in that time, from the rows by key; back in start order (R's): painting order)
        ks = np.flatnonzero(shown)
        key_at, key_starts = live["key_at"], live["key_starts"]
        a0, a1 = key_at[ks], key_at[ks + 1]
        lo_t, hi_t = math.ceil(t_lo - longest), math.floor(t_hi)
        parts = [live["by_key"][int(a + np.searchsorted(key_starts[a:b], lo_t, "left")):
                                int(a + np.searchsorted(key_starts[a:b], hi_t, "right"))]
                 for a, b in zip(a0.tolist(), a1.tolist()) if b > a]
        idx = np.sort(np.concatenate(parts)) if parts else np.zeros(0, np.int64)
        notes = drawn[rows[idx]]
        notes[:, 4] = live["S"][idx]  # (their channels as brushed so far)
        # (note_rects' screen(): the same sums, order and colour numbers)
        nx0, nx1 = np.round(notes[:, 0] * ax + bx), np.round(notes[:, 1] * ax + bx)
        on = ~((nx1 < kb) | (nx0 > w)) & shown[notes[:, 2]]
        on &= ~((nx1 < X0) | (nx0 >= X1))
        on = np.flatnonzero(on)
        rule = stack_rule(app)
        if len(on) and rule[0] == "track":  # (one shape's notes: only the track places order them, each slot's
            # place = stack_keys' with every slot counted (live["every"] holds every slot here); a table, stage F)
            if live.get("place") is None:
                from roll.roll_draw import track_ranks
                live["place"] = track_ranks(live["every"], *rule[1:])[0]
            key_of = live["place"][np.searchsorted(live["every"], notes[on, 4])]
            on = on[np.argsort(key_of, kind="stable")]
        elif len(on) and rule[0] != "shape":  # ("shape": one shape's notes all alike, kept as they are)
            key_of = stack_keys(notes[on], list(self.on_top()), getattr(app, "behind_owner", None), rule,
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

    # ---- the freeze mask

    def liquify_mask_shown(self):
        st = getattr(self, "lq", None)
        if st:
            st["mask_job"] = None
            st["mask_n"] = st.get("mask_n", 0) + 1  # (a new look of the mask: its picture made again)
        self.draw_liquify_mask()

    def draw_liquify_mask(self):
        """Each selected shape's freeze mask as a see-through red tint over its colour box (while the Liquify window
        is open and Show mask is ticked; image editors' red mask). Worked out every MASK_STEP pixels and stretched
        (it's soft anyway); its pictures kept while nothing they show changed."""
        self.delete("lqmask")
        w = getattr(self.app, "liquify_window", None)
        if not w or not w.show_mask.get() or self.sx is None:
            self._lq_masks = {}
            return
        st = getattr(self, "lq", None)
        stroke = {id(j["sh"]): j for j in (st or {}).get("jobs", ())}
        kept, self._lq_masks = getattr(self, "_lq_masks", {}), {}
        W, H = self.winfo_width(), self.winfo_height()
        view = (self.sx, self.sy, self.view_t, self.view_top, self.kb_w, self.ruler_h, W, H)
        for sh in self.app.recolour_targets():
            job = stroke.get(id(sh))
            if job is not None:
                m, made = job["m"], job["made"]
                mark = ("stroke", st.get("mask_n", 0))
            else:
                m = lq.mask_of((clean_recolour(sh.get("recolour")) or {}).get("liquify"))
                if m is None:
                    continue
                old = kept.get(id(sh))
                # (its notes' box: looked up again only after a change of the shapes (a whole note pass on a big one))
                made = old[2] if old and old[3] == self.app.shapes_gen else self.stir_made(sh, "liquify")
                mark = (sh["recolour"]["liquify"]["frozen"],)
            if m is None or made is None:
                continue
            key = (id(made[1]), mark, view)
            got = kept.get(id(sh))
            if got is None or got[0] != key:
                got = (key, self.liquify_mask_pic(made, m, W, H))
            got = got[:2] + (made, self.app.shapes_gen)
            self._lq_masks[id(sh)] = got
            if got[1]:
                pic, x, y = got[1]
                self.create_image(x, y, image=pic, anchor="nw", tags="lqmask")
        self.tag_raise("lqmask")
        self.tag_raise("lqbrush")

    def liquify_mask_pic(self, made, m, W, H):
        """(picture, x, y) of the red tint of mask m over a shape on screen, or None (nothing frozen in view)."""
        from roll.tiles import see_through
        x0, y0, x1, y1 = self.liquify_box(made)
        X0, Y0 = max(int(math.floor(x0)), int(math.ceil(self.kb_w))), max(int(math.floor(y0)), int(math.ceil(self.ruler_h)))
        X1, Y1 = min(int(math.ceil(x1)), W), min(int(math.ceil(y1)), H)
        if X1 <= X0 or Y1 <= Y0:
            return None
        s = MASK_STEP
        nx, ny = -(-(X1 - X0) // s), -(-(Y1 - Y0) // s)
        # (the colour box is the screen's, turned / flipped / stretched: straight lines stay straight, so 3 spots tell
        # every pixel's place)
        u0 = self.liquify_unit(made, X0 + s / 2, Y0 + s / 2)
        J = self.liquify_jac(made, X0 + s / 2, Y0 + s / 2, at=u0)
        i, j = np.meshgrid(np.arange(nx) * s, np.arange(ny) * s)
        u = u0[0] + J[0][0] * i + J[0][1] * j
        v = u0[1] + J[1][0] * i + J[1][1] * j
        inside = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
        val = lq.sample(m[..., None], u, v)[..., 0] * inside
        if not (val > 1 / 255).any():
            return None
        rgba = np.empty((ny, nx, 4), np.uint8)
        rgba[..., :3] = look.rgb(look.LIQUIFY_FREEZE)
        rgba[..., 3] = np.clip(val * MASK_ALPHA * 255, 0, 255).astype(np.uint8)
        small = see_through(self, rgba)
        import tkinter as tk
        pic = tk.PhotoImage(master=self, width=X1 - X0, height=Y1 - Y0)
        pic.tk.call(pic, "copy", small, "-zoom", s, s)
        return pic, X0, Y0

    def liquify_all(self, kind):
        """The window's buttons, on every selected shape at once (one undo step): "restore" = Restore All (every
        bend gone, the mask kept; the editors' Restore All), "freeze" / "thaw" = the whole shape frozen / thawed,
        "invert" = the mask turned inside out."""
        app = self.app
        names = {"restore": "liquify.undo_restore_all", "freeze": "liquify.undo_mask", "thaw": "liquify.undo_mask",
                 "invert": "liquify.undo_mask"}

        def change(old, sh):
            got = (old or {}).get("liquify")
            if got:
                a, m = lq.map_of(got), lq.mask_of(got)
            else:
                if kind in ("restore", "thaw"):
                    return old
                made = self.stir_made(sh, "liquify")
                if made is None:
                    return old
                x0, y0, x1, y1 = self.liquify_box(made)
                a, m = lq.empty(lq.new_grid(x1 - x0, y1 - y0)), None
            zeros = np.zeros(a.shape[:2])
            if kind == "restore":
                a = zeros[..., None].repeat(2, 2)
            elif kind == "freeze":
                m = zeros + 1.0
            elif kind == "thaw":
                m = None
            else:
                m = 1.0 - (zeros if m is None else m)
            w = app.liquify_window
            new = lq.with_arrays(got or ({"edge": w.edge} if w else {}), a, m)
            base = dict(old) if old else {"mode": KEEP, "seed": new_seed()}
            if new:
                base["liquify"] = new
            else:
                base.pop("liquify", None)
            return base
        if kind == "restore":
            app.change_recolour(change, tr(names[kind]))
        else:  # (only the mask: no notes made again (engine.made_from), the others not painted again either)
            tgts = app.recolour_targets()
            olds = [clean_recolour(t.get("recolour")) for t in tgts]
            news = [clean_recolour(change(o, t)) for o, t in zip(olds, tgts)]
            if any(o != n for o, n in zip(olds, news)):
                app.push_undo(name=tr(names[kind]))
                for t, r in zip(tgts, news):
                    if r:
                        t["recolour"] = r
                    else:
                        t.pop("recolour", None)
                app.sync_recolour()
                app.schedule_autosave()
        self.draw_liquify_mask()

    # ---- the brush on screen

    def liquify_motion(self, e):
        if self.liquify_on() or getattr(self, "lq_mouse", None):
            self.lq_mouse = (e.x, e.y)
            self.lq_state = e.state & (SHIFT | CTRL)
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
        a = self.liquify_start() if not st and getattr(self, "lq_state", 0) & SHIFT else None
        if a:  # (Shift held: where a Shift+click strokes from (its circle) and to, arrows along the way)
            x, y = self.liquify_aim(a, m, self.lq_state)
            self.draw_liquify_way(a, (x, y), r, line, band)
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

    def draw_liquify_way(self, a, b, r, line, band):
        """A straight stroke's preview: its start's circle (dashed) and a line from it to b with arrows showing the
        way it pushes (user 11-07)."""
        ax, ay = a
        self.create_oval(ax - r, ay - r, ax + r, ay + r, outline=look.ROLL_BG, width=band, tags="lqbrush")
        self.create_oval(ax - r, ay - r, ax + r, ay + r, outline=look.LIQUIFY_BRUSH, width=line, dash=(4, 3),
                         tags="lqbrush")
        d = math.hypot(b[0] - ax, b[1] - ay)
        if d < 2:
            return
        ux, uy = (b[0] - ax) / d, (b[1] - ay) / d
        size = 7 * max(1, round(self.scale))
        for width, col in ((band, look.ROLL_BG), (line, look.LIQUIFY_BRUSH)):
            self.create_line(ax, ay, b[0], b[1], fill=col, width=width, tags="lqbrush")
        n = max(1, int(d // ARROW_EVERY))
        for i in range(n):  # (evenly along it)
            t = d * (i + 1) / (n + 1)
            cx, cy = ax + ux * t, ay + uy * t
            pts = (cx - ux * size - uy * size, cy - uy * size + ux * size, cx, cy,
                   cx - ux * size + uy * size, cy - uy * size - ux * size)
            for width, col in ((band, look.ROLL_BG), (line, look.LIQUIFY_BRUSH)):
                self.create_line(*pts, fill=col, width=width, capstyle="round", joinstyle="round", tags="lqbrush")
