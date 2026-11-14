"""The Conductor pane's EDITABLE time signature changes (PLAN.md stage C, user 2026-11-14; the record: rebar_edit.py).

No row of their own (user: "a whole row feels like a waste"): an editable change's time signature mark itself is a
dark purple line over the whole change, |—6/4————|, like the piano roll ruler's BPM-change lines, with a short
upright bar at each end (no arrows; stage D drags them). Pointed at: the line thicker, a tip ("Bar 5: 4/4 → 6/4 ·
tempo × 1.5"), the piano roll tinted over its bars and the ORIGINAL tempo dashed grey inside ("was 120").
Right-click: Edit this change… (the usual box, its numbers filled in; numbers only, user) / Remove this change /
Make permanent (a plain mark again). Double-click = Edit. The piano roll draws its two bar lines faint purple
(roll_draw.grid_cols). Lines / ends close together: the one NEAREST the mouse is the one (user)."""

import numpy as np

from files.lang import tr, trn
from notes.meter import at_beat, sig_text, sigs_of
from notes.tempo import bpm_text, drawn_bpm, tempo_key
from window import look
from window.conductor_marks import MARK_LEN, ask_rebar

REACH = 5  # px (x scale): how far off an end bar the mouse may be
GRAB_DOWN = 3  # px (x scale) the line's row reaches below its marks (easier to hit)


class RebarMarks:
    """Mixed into TempoPane (with ConductorMarks)."""

    # ------------------------------------------------------------ where they are

    def rebar_spans(self):
        """[(change, x0, x1)] of the editable changes, in bar order (pixels: its first and end bar lines)."""
        from window import rebar_edit
        roll = self.app.roll
        return [(c, roll.t2x(c["pl"]["a"]), roll.t2x(c["pl"]["new_b"])) for c in rebar_edit.changes(self.app)]

    def rebar_at(self, x, y):
        """(change id, "start" / "end" / "line") the mouse at x, y is on, or None. Near an end bar = that end; two
        ends close together (touching changes) = the NEAREST one (user), on a tie the one the mouse is inside of."""
        s = self.scale
        if y < 0 or y > (MARK_LEN + GRAB_DOWN) * s or self.app.roll.sx is None:
            return None
        reach = REACH * s
        best = None
        for c, x0, x1 in self.rebar_spans():
            if x < x0 - reach or x > x1 + reach:
                continue
            for part, ex, inside in (("start", x0, x >= x0), ("end", x1, x <= x1)):
                d = abs(x - ex)
                if d <= reach:
                    key = (d, 0 if inside else 1)
                    if best is None or key < best[0]:
                        best = (key, c["id"], part)
            if best is None and x0 <= x <= x1:
                best = ((reach + 1, 0), c["id"], "line")
        return None if best is None else best[1:]

    # ------------------------------------------------------------ drawn

    def rebar_mark_beats(self):
        """The beats of the time signature marks an editable change draws itself (its own start's mark)."""
        from window import rebar_edit
        return {round(c["pl"]["a"], 6) for c in rebar_edit.changes(self.app)}

    def draw_rebar_marks(self):
        """Each editable change's mark: the purple line over its bars, a bar at each end, its signature at the
        left; the one pointed at thicker, with its tip, the original tempo dashed inside."""
        roll, s = self.app.roll, self.scale
        kb, w = roll.kb_w, self.winfo_width()
        hot = getattr(self, "_rebar_hot", None)
        font = look.font(7, "bold")
        lw = max(1, round(s))
        self._rebar_tip_at = None
        for c, x0, x1 in self.rebar_spans():
            if x1 < kb - 1 or x0 > w + 1:
                continue
            on = hot is not None and hot[0] == c["id"]
            width = lw * (3 if on else 2)
            y0, y1, mid = 0, MARK_LEN * s, MARK_LEN * s / 2
            if x0 >= kb - 1:
                self.create_line(x0, y0, x0, y1, fill=look.REBAR_MARK, width=width)
            if x1 <= w + 1:
                self.create_line(x1, y0, x1, y1, fill=look.REBAR_MARK, width=width)
            text = sig_text(c["sig"])
            tx = max(x0, kb) + 3 * s
            tw = self.tk.call("font", "measure", font, text)
            if tx + tw < x1 - 2 * s:  # (the text fits: the line goes on after it)
                self.create_text(tx, mid, text=text, anchor="w", fill=look.REBAR_MARK, font=font)
                a = tx + tw + 3 * s
            else:
                a = max(x0, kb)
            if x1 - a > 1:
                self.create_line(a, mid, min(x1, w + 2), mid, fill=look.REBAR_MARK, width=width)
            if on:
                self.draw_rebar_was(c, x0, x1, kb, w, lw)
                self._rebar_tip_at = (c, x0)

    def draw_rebar_tip(self):
        """The tip under the marks (drawn last, over the tempo line): which bars, old → new signature, the tempo
        factor."""
        got = getattr(self, "_rebar_tip_at", None)
        if got is None:
            return
        c, x0 = got
        kb, w = self.app.roll.kb_w, self.winfo_width()
        s = self.scale
        text = self.rebar_tip_text(c)
        font = look.font(8)
        tw = self.tk.call("font", "measure", font, text)
        x = min(max(x0, kb) + 2 * s, w - tw - 10 * s)
        x = max(x, kb + 2 * s)
        y = (MARK_LEN * 2 + 6) * s
        item = self.create_text(x + 5 * s, y + 4 * s, text=text, anchor="nw", fill=look.LABEL, font=font,
                                tags="rebar_tip")
        box = self.bbox(item)
        if box:
            bg = self.create_rectangle(box[0] - 5 * s, box[1] - 3 * s, box[2] + 5 * s, box[3] + 3 * s,
                                       fill=look.TIP_BG, outline=look.SIG_MARK, tags="rebar_tip")
            self.tag_lower(bg, item)

    def rebar_tip_text(self, c):
        """"Bar 5: 4/4 → 6/4 · tempo × 1.5" (several bars: "Bars 5–6"; mixed old signatures / factors listed)."""
        from window import rebar_edit
        first, last = rebar_edit.bars_now(self.app, c)
        orig = self.rebar_original(c)
        olds = []
        if orig is not None:
            from notes import rebar as R
            osigs = sigs_of(orig[0])
            for a, _ in R.old_bars(osigs, c["pl"]["a"], c["pl"]["b"]):
                t = sig_text(at_beat(osigs, a))
                if t not in olds:
                    olds.append(t)
        else:  # (a newer touching change hid the old line: each run's old bar from its factor)
            top, bottom = c["sig"]
            for r in c["pl"]["runs"]:
                quarters = top * 4 / bottom / r[4]
                for b in (bottom, 4, 8, 16, 2, 1, 32):
                    n = quarters * b / 4
                    if abs(n - round(n)) < 1e-9 and round(n) >= 1:
                        t = sig_text([0, int(round(n)), b])
                        break
                else:
                    t = None
                if t is None:
                    olds = []
                    break
                if t not in olds:
                    olds.append(t)
        times = []
        for r in c["pl"]["runs"]:
            t = f"{r[4]:.4g}"
            if t not in times:
                times.append(t)
        key = ("conductor.rebar_hover" if olds else "conductor.rebar_hover_now") + ("_one" if first == last else "")
        return tr(key, first=first + 1, last=last + 1, old=", ".join(olds), new=sig_text(c["sig"]),
                  times=", ".join(times))

    def rebar_original(self, c):
        """(the tempo line as it was before change c, its plan) or None (a newer touching change replaced its
        bar-line items: not found). Cached per change + record + line."""
        from window import rebar_edit
        key = (c["id"], id(rebar_edit.of(self.app)), tempo_key(self.app.tempo))
        got = getattr(self, "_rebar_orig", None)
        if got is not None and got[0] == key:
            return got[1]
        pl = rebar_edit.plan_of(c["pl"])
        try:
            back = rebar_edit.tempo_back(self.app.tempo, c["tempo"], pl)
        except (KeyError, IndexError, TypeError, ValueError):
            back = None
        out = None if back is None else (back, pl)
        self._rebar_orig = (key, out)
        return out

    def draw_rebar_was(self, c, x0, x1, kb, w, lw):
        """The tempo the bars had before the change, dashed grey inside them ("was 120"): each pixel's beat taken
        back through the change. A newer touching change hid it: the line now scaled back by its runs' factors."""
        from notes import rebar as R
        from window import rebar_edit
        roll = self.app.roll
        xa, xb = max(kb, int(round(x0))), min(w, int(round(x1)))
        if xb - xa < 2:
            return
        xs = np.arange(xa, xb, dtype=float)  # (its end bar line belongs to what comes after)
        beats = np.array([roll.x2t(x) for x in xs])
        orig = self.rebar_original(c)
        base = self.base()
        pl = rebar_edit.plan_of(c["pl"])
        olds = np.array([rebar_edit._unmap(pl, float(b)) for b in beats])
        if orig is not None:
            vs = drawn_bpm(orig[0], base, olds)
        else:
            fs = np.array([R.factor_at(pl, float(b)) for b in olds])
            vs = drawn_bpm(self.app.tempo, base, beats) / np.where(fs > 0, fs, 1.0)
        ys = np.asarray(self.b2y(vs), float)
        pts = np.column_stack([xs, ys]).ravel().tolist()
        self.create_line(*pts, fill=look.REBAR_WAS, dash=(5, 3), width=lw + 1, tags="rebar_was")
        s, y = self.scale, float(ys[0])
        below = y + 16 * s < self.winfo_height()  # (under the line: the tip sits above it)
        self.create_text(xa + 4 * s, y + 3 * s if below else y - 3 * s, anchor="nw" if below else "sw",
                         text=tr("conductor.rebar_was", bpm=bpm_text(vs[0])), fill=look.REBAR_WAS,
                         font=look.font(7, "bold"), tags="rebar_was")

    # ------------------------------------------------------------ pointed at

    def rebar_hover(self, e):
        """On a mouse move: the change under the mouse becomes the one pointed at (redrawn only on a change).
        Returns rebar_at's answer."""
        got = self.rebar_at(e.x, e.y) if e is not None else None
        hot = getattr(self, "_rebar_hot", None)
        if (got and got[0]) != (hot and hot[0]):
            self._rebar_hot = got
            self.rebar_tint(got[0] if got else None)
            self.request_redraw()
        else:
            self._rebar_hot = got
        return got

    def rebar_tint(self, cid):
        """The piano roll tinted over change cid's bars (None: none)."""
        from window import rebar_edit
        roll = self.app.roll
        c = rebar_edit.change_of(self.app, cid) if cid is not None else None
        roll.rebar_span = (c["pl"]["a"], c["pl"]["new_b"]) if c is not None else None
        if hasattr(roll, "draw_rebar_tint"):
            roll.draw_rebar_tint()

    # ------------------------------------------------------------ the right-click menu and its actions

    def rebar_menu_items(self, menu, got):
        """Edit this change… / Remove this change / Make permanent for got = rebar_at's answer."""
        cid = got[0]
        menu.add_command(label=tr("conductor.rebar_edit"), command=lambda: self.rebar_edit(cid))
        menu.add_command(label=tr("conductor.rebar_remove"), command=lambda: self.rebar_remove(cid))
        menu.add_separator()
        menu.add_command(label=tr("conductor.rebar_make_permanent"), command=lambda: self.rebar_make_permanent(cid))

    def rebar_where(self, cid):
        from window import rebar_edit
        c = rebar_edit.change_of(self.app, cid)
        if c is None:
            return ""
        first, last = rebar_edit.bars_now(self.app, c)
        return trn("conductor.rebar_bars", last - first + 1, first=first + 1, last=last + 1)

    def rebar_done(self):
        self._rebar_hot = None
        self.rebar_tint(None)
        self.request_redraw()
        self.app.roll.request_redraw()  # (its purple bar lines)
        self.app.vel.request_redraw()

    def rebar_edit(self, cid):
        """Edit this change…: the usual box with its numbers filled in (numbers only: its bars are dragged, user);
        OK takes it back and makes it again with the new numbers (one undo step). The box's test run is the edit's
        own (dry), so the plain-notes count is the real one."""
        from window import rebar_edit
        app = self.app
        c = rebar_edit.change_of(app, cid)
        if c is None:
            return
        sigs = sigs_of(app.tempo)
        lo, hi = c["pl"]["a"], c["pl"]["new_b"] - 1e-9
        name = tr("conductor.step_rebar_edit")

        def test_of(pl):
            got = rebar_edit.edit(app, cid, pl["sig"][0], pl["sig"][1], name, dense=pl.get("dense", c["dense"]),
                                  between=pl.get("between", c["between"]), dry=True)
            return self.rebar_why_any(got) if "why" in got else got.get("whys", {})
        from notes.tempo import drawn_bpm as bpm_of
        tempo, base = app.tempo, self.base()
        pl = ask_rebar(self, sigs, lo, hi, lambda b: round(float(bpm_of(tempo, base, [b])[0]), 3),
                       lambda pl: None, None, test_of, c["dense"], c["between"])
        if pl is None:
            return
        got = rebar_edit.edit(app, cid, pl["sig"][0], pl["sig"][1], name, dense=pl.get("dense", c["dense"]),
                              between=pl.get("between", c["between"]))
        if "why" in got:
            app.status.config(text=self.rebar_why_any(got))
            return
        text = tr("conductor.rebar_done", bars=self.rebar_where(cid), sig=sig_text(pl["sig"]))
        n = sum((got.get("whys") or {}).values())
        if n:
            text += " " + trn("conductor.rebar_plain", n, n=n)
        self.rebar_done()
        app.status.config(text=text)

    def rebar_why_any(self, got):
        """A refusal's text (one without a place: the plain "can't" text)."""
        if "at" in got:
            return self.rebar_why(got)
        return tr("conductor.rebar_why_gone")

    def rebar_remove(self, cid):
        """Remove this change: its bars back as they were (one undo step)."""
        from window import rebar_edit
        where = rebar_edit.bars_text(self.app, {cid})
        got = rebar_edit.remove(self.app, cid, tr("conductor.step_rebar_remove"))
        if "why" in got:
            self.app.status.config(text=self.rebar_why_any(got))
            return
        self.rebar_done()
        self.app.status.config(text=tr("conductor.rebar_removed", bars=where))

    def rebar_make_permanent(self, cid):
        """Make permanent: a plain time signature again, its record forgotten (one undo step)."""
        from window import rebar_edit
        if rebar_edit.change_of(self.app, cid) is None:
            return
        where = rebar_edit.bars_text(self.app, rebar_edit._sharing(rebar_edit.of(self.app), {cid}))
        self.app.push_undo(name=tr("conductor.step_rebar_permanent"))
        ids = rebar_edit.bake(self.app, cid)
        self.app.sync_history()
        self.rebar_done()
        self.app.status.config(text=tr("conductor.rebar_baked" if len(ids) > 1 else "conductor.rebar_baked_one",
                                       bars=where))
