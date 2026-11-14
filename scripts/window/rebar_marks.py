"""The Conductor pane's EDITABLE time signature changes (PLAN.md stage C, user 2026-11-14; the record: rebar_edit.py).

No row of their own (user: "a whole row feels like a waste"): an editable change's time signature mark itself is a
dark purple line over the whole change, |—6/4————|, like the piano roll ruler's BPM-change lines, with a short
upright bar at each end (no arrows; stage D drags them). Pointed at: the line thicker, a tip ("Bar 5: 4/4 → 6/4 ·
tempo × 1.5"), the piano roll tinted over its bars and the ORIGINAL tempo dashed grey inside ("was 120").
Right-click: Edit this change… (the usual box, its numbers filled in; numbers only, user) / Remove this change /
Make permanent (a plain mark again). Double-click = Edit. The piano roll draws its two bar lines faint purple
(roll_draw.grid_cols). Lines / ends close together: the one NEAREST the mouse is the one (user)."""

import bisect
import math

import numpy as np

from files.lang import tr, trn
from notes.meter import at_beat, bar_len, bar_start, parts, sig_text, sigs_of, where
from notes.tempo import bpm_text, drawn_bpm, keep_points, tempo_key
from window import look
from window.conductor_marks import MARK_LEN, ask_rebar

REACH = 5  # px (x scale): how far off an end bar the mouse may be
GRAB_DOWN = 3  # px (x scale) the line's row reaches below its marks (easier to hit)
DRAG_PX = 4  # px (x scale) the mouse moves before a press on a line becomes a drag (a click / double-click: none)
FAR = 10 ** 9  # (a bar past any song: a Keep timing part on to the song's end)


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
        self._rebar_was_label = None
        self.draw_rebar_ghost(lw)
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
        lab = getattr(self, "_rebar_was_label", None)
        lb = self.bbox(lab) if lab is not None else None
        pad = 6 * s
        if box and lb and box[0] - pad < lb[2] and lb[0] < box[2] + pad and box[1] - pad < lb[3] and lb[1] < box[3] + pad:
            # (the tip would hide the "was 120" label, C's picture: below the label, else right of it)
            dy = lb[3] + pad - box[1] + 3 * s
            if box[3] + dy + 4 * s < self.winfo_height():
                self.move(item, 0, dy)
            else:
                self.move(item, lb[2] + pad - box[0] + 5 * s, 0)
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
        # (user 2026-11-15: DOTTED in the change's purple, like Keep timing's heard line; was dashed grey)
        self.create_line(*pts, fill=look.REBAR_WAS, dash=(2, 3), width=lw + 1, tags="rebar_was")
        s, y = self.scale, float(ys[0])
        below = y + 16 * s < self.winfo_height()  # (under the line: the tip sits above it)
        self._rebar_was_label = self.create_text(
            xa + 4 * s, y + 3 * s if below else y - 3 * s, anchor="nw" if below else "sw",
            text=tr("conductor.rebar_was", bpm=bpm_text(vs[0])), fill=look.REBAR_WAS, font=look.font(7, "bold"),
            tags="rebar_was")

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

    # ------------------------------------------------------------ dragged (stage D, user 2026-11-14)
    # An END bar: the change covers more / fewer bars (snapped to bar lines), stopping at the next change or a bar
    # with a Keep timing part, never under one bar. The LINE: the whole change moved, its length in bars kept;
    # blocked = waits pressed against what's in the way, dragged on until it fits wholly in free bars past it =
    # jumps there. Only a ghost moves while held (the piano roll tinted where it would go); let go = made again
    # there (rebar_edit.move, one undo step; a refusal = the status line, nothing changed); Esc = left as it was.

    def rebar_press(self, got, e):
        """A press on change got[0]'s line / end (rebar_at's answer): the drag (TempoPane.drag) it starts once the
        mouse moves DRAG_PX. Bars are counted on the line as it is now (one old bar = one new bar, so the same
        numbers hold once the change is taken back: rebar_edit.move)."""
        from window import rebar_edit
        c = rebar_edit.change_of(self.app, got[0])
        if c is None:
            return None
        sigs = sigs_of(self.app.tempo)
        bars = tuple(rebar_edit.bars_now(self.app, c, sigs))
        return {"kind": "rebar", "cid": c["id"], "part": got[1], "x": e.x, "moved": False, "sigs": sigs,
                "bars": bars, "to": bars, "grab": self.rebar_bar(sigs, e.x), "walls": self.rebar_walls(sigs, c)}

    def rebar_bar(self, sigs, x):
        """The bar (from 0) at pixel x."""
        return where(sigs, self.app.roll.x2t(x))[0]

    def rebar_line_at(self, sigs, x):
        """The bar line nearest pixel x, as the number of the bar it starts."""
        bar, into, _, sig = where(sigs, self.app.roll.x2t(x))
        return bar + (1 if into > bar_len(*sig) / 2 else 0)

    def rebar_walls(self, sigs, c):
        """[(first, last bar)] a dragged change c can't reach into: the other editable changes (user: never two on
        the same bars) and the bars of Keep timing parts (user: they stop it too). A part starting right on a bar
        line also takes the bar before it (a change can't end where a Keep timing part starts: rebar.tempo_of
        "keep"); one running to the song's end takes every bar after."""
        from window import rebar_edit
        ps = parts(sigs)  # (once: where() per change rebuilt it = all-against-all, 12x at 4x the changes)
        starts = [p[0] for p in ps]

        def at(beat):  # (= where(sigs, beat)[:2])
            start, n, first, _ = ps[max(0, bisect.bisect_right(starts, beat + 1e-9) - 1)]
            k = math.floor((beat - start) / n + 1e-9)
            return first + k, beat - (start + k * n)
        out = []
        for x in rebar_edit.changes(self.app):
            if x["id"] != c["id"]:
                first = at(x["pl"]["a"] + rebar_edit.EPS)[0]  # (= rebar_edit.bars_now)
                out.append((first, first + x["pl"]["count"] - 1))
        pts = self.app.tempo["points"]
        for i in keep_points(pts):
            lo = pts[i][0]
            bar, into = at(lo)
            first = bar - 1 if into <= 1e-6 else bar
            last = at(pts[i + 1][0] - 1e-6)[0] if i + 1 < len(pts) else FAR
            if last >= first:
                out.append((first, last))
        return out

    def rebar_drag(self, d, e):
        """The mouse moved with a change held: where it would go now (d["to"]), the ghost redrawn on a change."""
        if not d["moved"]:
            if abs(e.x - d["x"]) < DRAG_PX * self.scale:
                return
            d["moved"] = True
            self._rebar_hot = None  # (no tip / thick line while held: the ghost shows where it goes)
        sigs, (f, l), walls = d["sigs"], d["bars"], d["walls"]
        if d["part"] == "start":
            low = max([b for a, b in walls if b < f], default=-1) + 1
            to = (min(max(self.rebar_line_at(sigs, e.x), low, 0), l), l)
        elif d["part"] == "end":
            high = min([a for a, b in walls if a > l], default=FAR) - 1
            to = (f, max(min(self.rebar_line_at(sigs, e.x) - 1, high), f))
        else:
            to = self.rebar_moved_to(d, self.rebar_bar(sigs, e.x) - d["grab"])
        if to != d["to"]:
            d["to"] = to
            self.rebar_ghost_moved(d)

    def rebar_moved_to(self, d, shift):
        """The bars a moved change lands on with the mouse shift bars on: there when wholly free, else the free
        place nearest it on the way back to where it is now (= pressed against what blocks it; user: it jumps once
        it fits past it, a change too long for a gap keeps going)."""
        (f, l), walls, prev = d["bars"], d["walls"], d["to"][0]
        n = l - f

        def free(x):
            return x >= 0 and not any(a <= x + n and x <= b for a, b in walls)
        x = f + shift
        step = 1 if prev > x else -1
        while x != prev and not free(x):
            x += step
        if not free(x):
            x = prev
        return x, x + n

    def rebar_ghost_moved(self, d):
        """The ghost + the piano roll's tint at d["to"], the status line saying where."""
        from window import rebar_edit
        sigs, (f, l) = d["sigs"], d["to"]
        roll = self.app.roll
        roll.rebar_span = (bar_start(sigs, f), bar_start(sigs, l + 1))
        if hasattr(roll, "draw_rebar_tint"):
            roll.draw_rebar_tint()
        where_to = tr("conductor.rebar_bars_at_one", first=f + 1) if f == l else \
            tr("conductor.rebar_bars_at", first=f + 1, last=l + 1)
        self.app.status.config(text=tr("conductor.rebar_drag_to", bars=where_to))
        self.request_redraw()

    def draw_rebar_ghost(self, lw):
        """A held change's place (stippled purple over the marks' row, a bar at each end)."""
        d = getattr(self, "drag", None)
        if not d or d.get("kind") != "rebar" or not d["moved"]:
            return
        roll, s = self.app.roll, self.scale
        sigs, (f, l) = d["sigs"], d["to"]
        x0, x1 = roll.t2x(bar_start(sigs, f)), roll.t2x(bar_start(sigs, l + 1))
        y1 = MARK_LEN * s
        self.create_rectangle(x0, 0, x1, y1, fill=look.REBAR_MARK, stipple="gray25", outline="", tags="rebar_ghost")
        for x in (x0, x1):
            self.create_line(x, 0, x, y1, fill=look.REBAR_MARK, width=lw * 3, tags="rebar_ghost")

    def rebar_let_go(self, d):
        """The mouse let go of a held change: made again on its new bars (the loaded MIDI's note ticks read on a
        thread first when needed, ConductorMarks.loaded_ticks_wait)."""
        to = d["to"]
        self.rebar_tint(None)
        if not d["moved"] or to == d["bars"]:
            self.app.status.config(text="")
            self.request_redraw()
            return
        sigs = d["sigs"]
        self.request_redraw()
        if self.loaded_ticks_wait(bar_start(sigs, to[0]), bar_start(sigs, to[1] + 1) - 1e-9,
                                  then=lambda: self.rebar_put(d)):
            return
        self.rebar_put(d)

    def rebar_put(self, d):
        """A dragged change made again on d["to"] (one undo step: "Move ..." for the line, "Change the bars ..."
        for an end). Refused (another change, a catch-up / Keep timing part over an edge, ...) = the status line
        says why, nothing changed (my pick: no question after a drag, Undo is there)."""
        from window import rebar_edit
        cid, (first, last) = d["cid"], d["to"]
        name = tr("conductor.step_rebar_move" if d["part"] == "line" else "conductor.step_rebar_bars")
        got = rebar_edit.move(self.app, cid, first, last, name)
        self.rebar_done()
        if "why" in got:
            self.app.status.config(text=self.rebar_why_any(got))
            return
        c = rebar_edit.change_of(self.app, cid)
        text = tr("conductor.rebar_done", bars=self.rebar_where(cid), sig=sig_text(c["sig"]) if c else "")
        n = sum((got.get("whys") or {}).values())
        if n:
            text += " " + trn("conductor.rebar_plain", n, n=n)
        self.app.status.config(text=text)

    def rebar_escape(self, e=None):
        """Esc while a change is held: dropped where it was."""
        d = getattr(self, "drag", None)
        if not d or d.get("kind") != "rebar":
            return
        self.drag = None
        self.rebar_tint(None)
        self.app.status.config(text="")
        self.request_redraw()
        return "break"

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
