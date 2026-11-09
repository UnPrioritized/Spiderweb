"""Our own compiled loops (files/native.py's Loops) for the loops past the first three: each takes the same arguments
and gives the same as its fastloops twin. The extension function fills arrays made here (outputs: their room worked
out first when it isn't known) and returns a count; below 0 = it didn't run, that call takes the fastloops loop.
NEEDS: each loop's extension functions (a library without them: fastloops' own loop is kept)."""

import numpy as np

NEEDS = {
    "paint_order": ("paint_order",), "order_screen": ("screen_count", "order_screen"),
    "order_rects": ("screen_count", "order_rects"), "last_on_pixels": ("last_on_pixels",), "note_top": ("note_top",),
    "paint_notes": ("paint_notes",),
    "overlap_order": ("overlap_order",), "overlap_sweep": ("overlap_sweep",), "note_stretches": ("note_stretches",),
    "radix_order": ("radix_order",), "ring_runs": ("ring_runs", "ring_runs_int"),
    "clash_pairs": ("clash_pairs", "take2"), "clash_levels": ("clash_levels",), "any_overlap": ("any_overlap",),
    "turn_sample": ("turn_sample",), "turn_layers": ("turn_layers",),
    "stir_nearest": ("stir_nearest",), "stir_nearest_one": ("stir_nearest",), "stir_wrap": ("stir_wrap",),
    "stir_wrap_one": ("stir_wrap",), "liquify_sample": ("liquify_sample",), "liquify_sample_one": ("liquify_sample",),
    "dice_mix": ("dice_mix",), "recolour_apart": ("recolour_apart",),
    "warp_newton": ("warp_newton",), "warp_soft": ("warp_soft",),
    "midi_events": ("midi_events", "take_bytes"), "midi_track": ("midi_track", "take_bytes"), "nearest": ("nearest",),
    "spread_line": ("spread_line",), "area_spots": ("area_spots",), "smooth_sor": ("smooth_sor",),
    "dc_block": ("dc_block",),
    "wave_fill": ("wave_fill",), "note_ends": ("note_ends",), "hz_expand": ("hz_expand",), "hz_late": ("hz_late",),
    "hz_order": ("hz_order",), "hz_finish": ("hz_finish", "hz_finish_i"),
    "gap_ticks": ("gap_ticks", "gap_room", "gap_ticks_into"), "gap_plan": ("gap_plan",),
    "midi_walk": ("midi_walk",), "midi_rewrite": ("midi_rewrite",), "midi_note_ticks": ("midi_note_ticks",),
    "midi_busy_hits": ("midi_busy_hits",), "midi_gather": ("midi_gather",),
    "midi_index": ("midi_index",), "midi_close": ("midi_close",), "midi_grid": ("midi_grid",),
    "midi_halve": ("midi_halve",), "midi_cover": ("midi_cover",), "midi_merge_loud": ("midi_merge_loud",),
    "midi_cover_many": ("midi_cover_many",), "loaded_pixels": ("loaded_pixels",), "all_tops": ("all_tops",),
    "play_read": ("play_read",), "play_programs": ("play_programs",), "play_order": ("play_order",),
    "play_order_count": ("play_order_count",), "play_order_place": ("play_order_place",), "play_hist": ("play_hist",),
    "play_stamp": ("play_stamp",), "play_condense": ("play_condense",), "play_stamp_pre": ("play_stamp_pre",),
    "play_emit": ("play_emit",),
    "piece_track": ("piece_track",), "piece_held_ons": ("piece_held_ons",), "piece_held_head": ("piece_held_head",),
    "piece_note_ons": ("piece_note_ons",), "piece_runs_sort": ("piece_runs_sort",),
    "piece_runs_merge": ("piece_runs_merge",), "piece_counted_track": ("piece_counted_track",),
    "piece_order_ok": ("piece_order_ok",), "piece_open_at": ("piece_open_at",),
}
NOT_IN_ORDER = -10  # (note_stretches: the notes aren't in key / start order; Numba's -1)
BIG_GAPS = 4096  # (gap_ticks: anchors past this = written into arrays made in Python)


def rows_of(out, m):
    """The first m of each row of out as separate arrays: views of out when little room is left unused (copying
    them costs as much as the loop on big pictures), else copies so a big mostly-empty out isn't kept alive."""
    if out.shape[1] - m <= max(out.shape[1] >> 3, 65536):
        return tuple(out[r, :m] for r in range(out.shape[0]))
    o = out[:, :m].copy()
    return tuple(o[r] for r in range(o.shape[0]))


class Wraps:
    """native.Loops' loops (self.x = the extension module, self.base = fastloops, self._again = the second try)."""

    # ---- the piano roll's note pictures (roll_draw)

    def paint_order(self, notes, by_start, rank, nranks, pics, cols, picgroup, ghost):
        n = len(by_start)
        s, e, key, color = np.empty(n, np.int64), np.empty(n, np.int64), np.empty(n, np.int16), np.empty(n, np.int32)
        first = np.empty(nranks + 1, np.int64)
        args = (notes, by_start, rank, nranks, pics, cols, picgroup, ghost, s, e, key, color, first)
        got = self.x.paint_order(*args)
        if got < 0 and (got := self._again("paint_order", got, args)) < 0:
            return self.base.paint_order(notes, by_start, rank, nranks, pics, cols, picgroup, ghost)
        return s, e, key, color, first, got

    def _count(self, s, first, b0, b1, t_lo, t_hi):
        got = self.x.screen_count(s, first, b0, b1, t_lo, t_hi)
        return got if got >= 0 else self._again("screen_count", got, (s, first, b0, b1, t_lo, t_hi))

    def order_screen(self, s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2):
        cnt = self._count(s, first, b0, b1, t_lo, t_hi)
        if cnt >= 0:
            out = np.empty((4, cnt), np.int64)
            args = (s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2, out)
            m = self.x.order_screen(*args)
            if m >= 0 or (m := self._again("order_screen", m, args)) >= 0:
                return out[0, :m], out[1, :m], out[2, :m], out[3, :m]
        return self.base.order_screen(s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2)

    def order_rects(self, s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2, left, right,
                    keys, row0, row1):
        cnt = self._count(s, first, b0, b1, t_lo, t_hi)
        if cnt >= 0:
            out = np.empty((5, cnt), np.int64)
            args = (s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2, left, right, keys,
                    row0, row1, out)
            m = self.x.order_rects(*args)
            if m >= 0 or (m := self._again("order_rects", m, args)) >= 0:
                return rows_of(out, m)
        return self.base.order_rects(s, e, key, color, first, b0, b1, t_lo, t_hi, ax, bx, kb, w, shown, clip, c0, c2,
                                     left, right, keys, row0, row1)

    def last_on_pixels(self, x0, x1, key, color, left, right, keys, row0, row1):
        out = np.empty((5, len(x0)), np.int64)
        args = (x0, x1, key, color, left, right, keys, row0, row1, out)
        m = self.x.last_on_pixels(*args)
        if m >= 0 or (m := self._again("last_on_pixels", m, args)) >= 0:
            return rows_of(out, m)
        return self.base.last_on_pixels(x0, x1, key, color, left, right, keys, row0, row1)

    def note_top(self, top_px, half, kb, top, w, h, x0, y0, x1, y1, color, shade):
        args = (top_px, half, kb, top, w, h, x0, y0, x1, y1, color, shade)
        got = self.x.note_top(*args)
        if got < 0 and self._again("note_top", got, args) < 0:
            self.base.note_top(*args)

    def paint_notes(self, img, kb, top, w, h, x0, y0, x1, y1, color, rgb, shade):
        args = (img, kb, top, w, h, x0, y0, x1, y1, color, rgb, shade)
        got = self.x.paint_notes(*args)
        if got < 0 and self._again("paint_notes", got, args) < 0:
            self.base.paint_notes(*args)

    # ---- the note engine (engine.py, roll_draw.ring_parts, domino_clip)

    def overlap_order(self, notes):
        n = len(notes)
        group, order = np.empty(n, np.int64), np.empty(n, np.int64)
        args = (notes, group, order)
        got = self.x.overlap_order(*args)
        if got < 0 and (got := self._again("overlap_order", got, args)) < 0:
            return self.base.overlap_order(notes)
        return (group, order) if got else (np.zeros(0, np.int64), np.zeros(0, np.int64))

    def overlap_sweep(self, notes, order, group):
        out = np.empty((len(order), notes.shape[1]), np.int64)
        args = (notes, order, group, out)
        m = self.x.overlap_sweep(*args)
        if m < 0 and (m := self._again("overlap_sweep", m, args)) < 0:
            return self.base.overlap_sweep(notes, order, group)
        return out, m

    def note_stretches(self, notes, by_key, owner, order):
        out = np.empty((len(notes), 4), np.int64)
        args = (notes, by_key, owner, order, out)
        m = self.x.note_stretches(*args)
        if m < 0 and m != NOT_IN_ORDER and (m := self._again("note_stretches", m, args)) < 0 and m != NOT_IN_ORDER:
            return self.base.note_stretches(notes, by_key, owner, order)
        return (out[:0], -1) if m == NOT_IN_ORDER else (out, m)

    def radix_order(self, k, bits):
        n = len(k)
        order, o2 = np.arange(n, dtype=np.int64), np.empty(n, np.int64)
        args = (k, bits, order, o2)
        got = self.x.radix_order(*args)
        if got < 0 and (got := self._again("radix_order", got, args)) < 0:
            return self.base.radix_order(k, bits)
        return o2 if got else order

    def ring_runs(self, s, e, k, order, gap, big):
        out = np.empty((3, len(order)), np.int64)
        name = "ring_runs_int" if isinstance(gap, (int, np.integer)) else "ring_runs"
        args = (s, e, k, order, gap, big, out)
        m = getattr(self.x, name)(*args) if len(order) else -1
        if m < 0 and (not len(order) or (m := self._again(name, m, args)) < 0):
            return self.base.ring_runs(s, e, k, order, gap, big)
        return out[0, :m], out[1, :m], out[2, :m]

    def clash_pairs(self, items, n, most):
        got = self.x.clash_pairs(items, n, most)
        if got < 0 and (got := self._again("clash_pairs", got, (items, n, most))) < 0:
            return self.base.clash_pairs(items, n, most)
        if got == 0:
            e = np.zeros(0, np.int64)
            return e, e, False
        pa, pb = np.empty(got >> 1, np.int64), np.empty(got >> 1, np.int64)
        self.x.take2(pa, pb)
        return pa, pb, True

    def turn_sample(self, flat, skey, send, g, lo_k, hi_k, n, many):
        args = (flat, skey, send, g, lo_k, hi_k, n, many)
        got = self._tuple("turn_sample", args)
        return got if got is not None else self.base.turn_sample(*args)

    def turn_layers(self, s, e, k, first, over, lay):
        args = (s, e, k, first, over, lay)
        got = self.x.turn_layers(*args)
        if got < 0:
            got = self._again("turn_layers", got, args)
            if got < 0:
                got = self.base.turn_layers(*args)
        return got

    def any_overlap(self, notes, g, ng):
        got = self.x.any_overlap(notes, g, ng)
        if got < 0:
            got = self._again("any_overlap", got, (notes, g, ng))
        return got

    def clash_levels(self, rows, offs, out):
        args = (rows, offs, out)
        got = self.x.clash_levels(*args)
        if got < 0 and self._again("clash_levels", got, args) < 0:
            self.base.clash_levels(*args)

    # ---- Recolour / Stir / Liquify (recolour.py, liquify.py): each spot on its own, the _one ones on one core

    def _run(self, name, args, fallback):  # (fallback: the fastloops name)
        """An in-place loop: the extension's name(*args), else (it didn't run) fallback(*args[:-1])."""
        got = getattr(self.x, name)(*args)
        if got < 0 and self._again(name, got, args) < 0:
            getattr(self.base, fallback)(*args[:-1])

    def stir_nearest(self, keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own, out):
        self._run("stir_nearest", (keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own, out, True),
                  "stir_nearest")

    def stir_nearest_one(self, keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own, out):
        self._run("stir_nearest", (keys, first, last, line, ms, nk, nm, o, base, top, gap, mid, key, own, out, False),
                  "stir_nearest_one")

    def stir_wrap(self, keys, a, b, mid, key, out):
        self._run("stir_wrap", (keys, a, b, mid, key, out, True), "stir_wrap")

    def stir_wrap_one(self, keys, a, b, mid, key, out):
        self._run("stir_wrap", (keys, a, b, mid, key, out, False), "stir_wrap_one")

    def liquify_sample(self, a, x, y, out):
        self._run("liquify_sample", (a, x, y, out, True), "liquify_sample")

    def liquify_sample_one(self, a, x, y, out):
        self._run("liquify_sample", (a, x, y, out, False), "liquify_sample_one")

    def warp_newton(self, P, px, py, smooth, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best):
        self._run("warp_newton", (P, px, py, smooth, sx, sy, bounds, has_b, steps, give_step, give_far, bx, by, best,
                                  True), "warp_newton")

    def warp_soft(self, at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base, top, gap,
                  now, out):
        self._run("warp_soft", (at, idx, x, y, a, c, fp, ring, tstart, ta, tb, keys, first, last, line, ms, o, base,
                                top, gap, now, out, True), "warp_soft")

    def dice_mix(self, seed, c0, c1, c2, c3, out):
        args = (seed, c0, c1, c2, c3, out)
        got = self.x.dice_mix(*args)
        if got < 0 and self._again("dice_mix", got, args) < 0:
            self.base.dice_mix(*args)

    def recolour_apart(self, order, ss, e, old, new, pick, bstart, bsize, nc):
        args = (order, ss, e, old, new, pick, bstart, bsize, nc)
        got = self.x.recolour_apart(*args)
        if got < 0 and self._again("recolour_apart", got, args) < 0:
            self.base.recolour_apart(*args)

    # ---- MIDI bytes (midi_out), Image to notes (picture), areas (faces), Nested fill

    def _bytes(self, name, args):
        """A loop whose bytes the library keeps until asked: -> them, or None (it didn't run)."""
        n = getattr(self.x, name)(*args)
        if n < 0 and (n := self._again(name, n, args)) < 0:
            return None
        out = np.empty(n, np.uint8)
        self.x.take_bytes(out)
        return out

    def midi_events(self, notes, rows, order, ch, most):
        out = self._bytes("midi_events", (notes, rows, order, ch, most))
        return out if out is not None else self.base.midi_events(notes, rows, order, ch, most)

    def midi_track(self, notes, rows, ch, most, bits):
        out = self._bytes("midi_track", (notes, rows, ch, most, bits))
        return out if out is not None else self.base.midi_track(notes, rows, ch, most, bits)

    def nearest(self, lab, cen):
        out = np.empty(len(lab), np.int64)
        got = self.x.nearest(lab, cen, out)
        if got < 0 and self._again("nearest", got, (lab, cen, out)) < 0:
            return self.base.nearest(lab, cen)
        return out

    def spread_line(self, a0, free, pal, pal_lab, strength, idx, err, line, lab, vals, clipped):
        args = (a0, free, pal, pal_lab, strength, idx, err, line, lab, vals, clipped)
        got = self.x.spread_line(*args)
        if got < 0 and (got := self._again("spread_line", got, args)) < 0:
            return self.base.spread_line(*args)
        return got

    def area_spots(self, x, y, levels, start, off, lab, outside, edge, ex, ey, es, x0, y0, x1, y1, eps):
        out = np.empty(len(x), np.int64)
        args = (x, y, levels, start, off, lab, outside, edge, ex, ey, es, x0, y0, x1, y1, eps, out)
        got = self.x.area_spots(*args)
        if got < 0 and self._again("area_spots", got, args) < 0:
            return self.base.area_spots(*args[:-1])
        return out

    def smooth_sor(self, flat, red, black, wide, om, times):
        args = (flat, red, black, wide, om, times)
        got = self.x.smooth_sor(*args)
        if got < 0 and self._again("smooth_sor", got, args) < 0:
            self.base.smooth_sor(*args)

    def dc_block(self, x, c, z):
        got = self.x.dc_block(x, c, z)
        if got < 0 and self._again("dc_block", got, (x, c, z)) < 0:
            self.base.dc_block(x, c, z)

    # ---- Hz bass (hzbass, hz_grid.KeyGrid.made_fast)

    def _tuple(self, name, args):
        """A loop handing back new arrays: its tuple, or None (it didn't run)."""
        got = getattr(self.x, name)(*args)
        if got.__class__ is int:
            got = self._again(name, got, args)
            if got.__class__ is int:
                return None
        return got

    def wave_fill(self, starts, a, b, sq0, wo, out):
        args = (starts, a, b, sq0, wo, out)
        got = self.x.wave_fill(*args)
        if got < 0 and self._again("wave_fill", got, args) < 0:
            self.base.wave_fill(*args)

    def note_ends(self, s, e, k, stick, mode, beats, pct, pct_int, wv, wf, big):
        res = np.empty(len(s), np.int64)
        args = (s, e, k, stick, mode, beats, pct, pct_int, wv, wf, big, res)
        got = self.x.note_ends(*args)
        if got < 0 and self._again("note_ends", got, args) < 0:
            return self.base.note_ends(*args[:-1])
        return res

    def hz_expand(self, runs_sel, copy_sel, scale_c, n_run, off_run, prev_run, moves_run, pos_len, x, off_pitch, opf,
                  vf, W, S):
        args = (runs_sel, copy_sel, scale_c, n_run, off_run, prev_run, moves_run, pos_len, x, off_pitch, opf, vf, W, S)
        got = self._tuple("hz_expand", args)
        return got if got is not None else self.base.hz_expand(*args)

    def hz_late(self, part, number, src, w, s, hits, has_trace, x, slant, groups, noisy_f, P):
        args = (part, number, src, w, s, hits, has_trace, x, slant, groups, noisy_f, P)
        got = self._tuple("hz_late", args)
        if got is None:
            return self.base.hz_late(*args)
        return got if len(got) == 10 else (part, number, src, w, s) + got

    def hz_order(self, wg):
        out = np.empty(len(wg), np.int64)
        got = self.x.hz_order(wg, out)
        if got < 0 and self._again("hz_order", got, (wg, out)) < 0:
            return self.base.hz_order(wg)
        return out

    def hz_finish(self, part, number, src, w, st0, late, nsrc, noisy_p, place, drawn, until_p, gains_sel, has_loud,
                  has_wah, hv, vol_f, lev, oct_f, own_f, layer_f, lim_f, loud_sw, c1, tf, soft_thr):
        args = (part, number, src, w, st0, late, nsrc, noisy_p, place, drawn, until_p, gains_sel, has_loud, has_wah,
                hv, vol_f, lev, oct_f, own_f, layer_f, lim_f, loud_sw, c1, tf, soft_thr)
        name = "hz_finish_i" if getattr(lim_f, "dtype", None) == np.int64 else "hz_finish"
        got = self._tuple(name, args)
        return got if got is not None else self.base.hz_finish(*args)

    # ---- Keep timing's file tempos (tempo_file)

    def gap_ticks(self, a, e, ts, bs, drawn, was, u, most):
        args = (a, e, ts, bs, drawn, was, u, most)
        if len(ts) + len(bs) > BIG_GAPS and (room := self.x.gap_room(a, e, ts, bs)) > BIG_GAPS:
            # (a big part: written straight into arrays made here, as Numba does; copying them out afterwards cost
            # 1.3x Numba's time)
            n, total, k, dense = (np.empty(room, np.int64), np.empty(room - 1, np.int64),
                                  np.empty(room - 1, np.int64), np.empty(room - 1, np.int64))
            got = self.x.gap_ticks_into(*args, n, total, k, dense)
            if got >= 0:
                c, nd = got & 0xFFFFFFFF, got >> 32
                return n[:c], total[:c - 1], k[:c - 1], dense[:nd]
        got = self._tuple("gap_ticks", args)
        return got if got is not None else self.base.gap_ticks(*args)

    def gap_plan(self, j, last, a, n, total, k, drawn, pref, every, most, safe_reach, pair_reach, lens, vals, pos,
                 got):
        args = (j, last, a, n, total, k, drawn, pref, every, most, safe_reach, pair_reach, lens, vals, pos, got)
        r = self.x.gap_plan(*args)  # (-> (gaps, last, pos), made in Rust: no array a call)
        if r.__class__ is int:
            r = self._again("gap_plan", r, args)
            if r.__class__ is int:
                return self.base.gap_plan(*args)
        return r

    # ---- Load MIDI's byte loops (midi_scan): -> where each stopped

    def _at(self, name, args):
        """A loop returning a place: it, or None (it didn't run)."""
        got = getattr(self.x, name)(*args)
        if got < 0 and (got := self._again(name, got, args)) < 0:
            return None
        return got

    def midi_walk(self, b, p, end, final, rest, st, cnt):  # (straight: small calls, the helper cost ~0.1 us)
        got = self.x.midi_walk(b, p, end, final, rest, st, cnt)
        if got < 0 and (got := self._again("midi_walk", got, (b, p, end, final, rest, st, cnt))) < 0:
            return self.base.midi_walk(b, p, end, final, rest, st, cnt)
        return got

    def midi_rewrite(self, b, p, end, final, bp_n, bp_f, use_map, drop, ins_t, ins_at, ins_b, out, o, st, base, skip,
                     chan=-1, keep=0xFFFF, meta=1):
        res = np.empty(2, np.int64)
        args = (b, p, end, final, bp_n, bp_f, use_map, drop, ins_t, ins_at, ins_b, out, o, st, base, skip, chan, keep,
                meta, res)
        if self._at("midi_rewrite", args) is None:
            return self.base.midi_rewrite(*args[:-1])
        return int(res[0]), int(res[1])

    def midi_note_ticks(self, b, p, end, final, mark, base, st):
        got = self.x.midi_note_ticks(b, p, end, final, mark, base, st)
        if got < 0 and (got := self._again("midi_note_ticks", got, (b, p, end, final, mark, base, st))) < 0:
            return self.base.midi_note_ticks(b, p, end, final, mark, base, st)
        return got

    def midi_busy_hits(self, b, p, end, base, skip, t_stop, st, cnt, since, lane_at, own_s, own_e, own_top, hit):
        args = (b, p, end, base, skip, t_stop, st, cnt, since, lane_at, own_s, own_e, own_top, hit)
        got = self._at("midi_busy_hits", args)
        return got if got is not None else self.base.midi_busy_hits(*args)

    def midi_gather(self, b, p, end, base, skip, t0, t1, k0, k1, chans, st, old, newer, head, tail, nxt, o_s, o_e, o_k,
                    o_v, o_on, o_off, o_was):
        args = (b, p, end, base, skip, t0, t1, k0, k1, chans, st, old, newer, head, tail, nxt, o_s, o_e, o_k, o_v,
                o_on, o_off, o_was)
        got = self._at("midi_gather", args)
        return got if got is not None else self.base.midi_gather(*args)

    # ---- Load MIDI's index and pictures (midi_index, roll_loaded, track_list)

    def _do(self, name, args):
        """An in-place loop: True when it ran (else the caller uses fastloops')."""
        got = getattr(self.x, name)(*args)
        return got >= 0 or self._again(name, got, args) >= 0

    def midi_index(self, b, p, end, at, G, ix, q_hd, q_tl, q_rl, q_pos, q_vel, opened, pt, pn, pw, cp_off, cp_tick,
                   cp_run, cp_ent, e_q, e_cnt, e_end, e_vel, e_nx, cov, edg, chn, m_tick, m_type, m_len, m_data,
                   c_tick, c_code, ccn):
        args = (b, p, end, at, G, ix, q_hd, q_tl, q_rl, q_pos, q_vel, opened, pt, pn, pw, cp_off, cp_tick, cp_run,
                cp_ent, e_q, e_cnt, e_end, e_vel, e_nx, cov, edg, chn, m_tick, m_type, m_len, m_data, c_tick, c_code,
                ccn)
        got = self._tuple("midi_index", args)
        return got if got is not None else self.base.midi_index(*args)

    def midi_close(self, ix, G, q_hd, q_rl, opened, pt, pn, pw, e_end, e_nx, cov, edg):
        args = (ix, G, q_hd, q_rl, opened, pt, pn, pw, e_end, e_nx, cov, edg)
        if not self._do("midi_close", args):
            self.base.midi_close(*args)

    def midi_grid(self, cov, edg, glo, ghi, klo, khi, cnt, top, etop, track, pic, pf):
        args = (cov, edg, glo, ghi, klo, khi, cnt, top, etop, track, pic, pf)
        if not self._do("midi_grid", args):
            self.base.midi_grid(*args)

    def midi_halve(self, cnt, top, cnt2, top2):
        got = self.x.midi_halve(cnt, top, cnt2, top2)
        if got < 0 and self._again("midi_halve", got, (cnt, top, cnt2, top2)) < 0:
            self.base.midi_halve(cnt, top, cnt2, top2)

    def midi_cover(self, b, p, end, base, skip, st, cnt_q, first_q, vel_q, t1, close_at, ax, bx, W, k_lo, colour, tab,
                   nx, filled, marks, sq, sa, sb, sv):
        args = (b, p, end, base, skip, st, cnt_q, first_q, vel_q, t1, close_at, ax, bx, W, k_lo, colour, tab, nx,
                filled, marks, sq, sa, sb, sv)
        got = self._at("midi_cover", args)
        return got if got is not None else self.base.midi_cover(*args)

    def midi_merge_loud(self, tab, marks, ttab, tmarks, tnx, tfilled, ghost):
        args = (tab, marks, ttab, tmarks, tnx, tfilled, ghost)
        if not self._do("midi_merge_loud", args):
            self.base.midi_merge_loud(*args)

    def midi_cover_many(self, faint, klo, khi, starts, ends, e0s, e1s, cp_tick, cp_run, limits, lasts, buf, pos, eq,
                        ec, ev, skip, t1, ax, bx, W, k_lo, normal, behind, tab, nx, filled, marks, sq, sa, sb, sv,
                        cnt_q, first_q, vel_q, st, covered):
        args = (faint, klo, khi, starts, ends, e0s, e1s, cp_tick, cp_run, limits, lasts, buf, pos, eq, ec, ev, skip,
                t1, ax, bx, W, k_lo, normal, behind, tab, nx, filled, marks, sq, sa, sb, sv, cnt_q, first_q, vel_q,
                st, covered)
        got = self._at("midi_cover_many", args)
        return got if got is not None else self.base.midi_cover_many(*args)

    def loaded_pixels(self, img, x0, y0, tab, marks, c0, c1, kb, row0, row1, rgb):
        args = (img, x0, y0, tab, marks, c0, c1, kb, row0, row1, rgb)
        if not self._do("loaded_pixels", args):
            self.base.loaded_pixels(*args)

    def all_tops(self, top, data, offs, nrows, nbytes, pklo, glo, pf, cols, used, uoff, ids, x0, tpp, G):
        args = (top, data, offs, nrows, nbytes, pklo, glo, pf, cols, used, uoff, ids, x0, tpp, G)
        if not self._do("all_tops", args):
            self.base.all_tops(*args)

    # ---- playing a loaded MIDI (play_loaded, play_dense): called often, so each one straight (no helper call)

    def play_read(self, b, at, size, t1, tick, run, nxt, used, kn, out_t, out_m, out_k, n, base, sk, sk_i, sk_end,
                  count, opened, slot):
        args = (b, at, size, t1, tick, run, nxt, used, kn, out_t, out_m, out_k, n, base, sk, sk_i, sk_end, count,
                opened, slot)
        got = self.x.play_read(*args)
        if got < 0 and (got := self._again("play_read", got, args)) < 0:
            return self.base.play_read(*args)
        return got

    def play_programs(self, b, p, end, st):
        got = self.x.play_programs(b, p, end, st)
        if got < 0 and (got := self._again("play_programs", got, (b, p, end, st))) < 0:
            return self.base.play_programs(b, p, end, st)
        return got

    def play_order(self, ticks, ms, ks, base, span, out_t, out_m, out_k):
        args = (ticks, ms, ks, base, span, out_t, out_m, out_k)
        got = self.x.play_order(*args)
        if got < 0 and self._again("play_order", got, args) < 0:
            self.base.play_order(*args)

    def play_order_count(self, ticks, base, count):
        got = self.x.play_order_count(ticks, base, count)
        if got < 0 and self._again("play_order_count", got, (ticks, base, count)) < 0:
            self.base.play_order_count(ticks, base, count)

    def play_order_place(self, ticks, ms, ks, base, at, out_t, out_m, out_k):
        args = (ticks, ms, ks, base, at, out_t, out_m, out_k)
        got = self.x.play_order_place(*args)
        if got < 0 and self._again("play_order_place", got, args) < 0:
            self.base.play_order_place(*args)

    def play_hist(self, ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, h):
        args = (ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, h)
        got = self.x.play_hist(*args)
        if got < 0 and self._again("play_hist", got, args) < 0:
            self.base.play_hist(*args)

    def play_stamp(self, ms, trk, i0, i1, ticks, muted, tport, mt, ms_, mspt, base, rate, least, fr, out, tr, counts,
                   cp, tab_key, tab_gen, tab_at, head, tail, val, nxt, st):
        args = (ms, trk, i0, i1, ticks, muted, tport, mt, ms_, mspt, base, rate, least, fr, out, tr, counts, cp,
                tab_key, tab_gen, tab_at, head, tail, val, nxt, st)
        got = self.x.play_stamp(*args)
        if got < 0 and (got := self._again("play_stamp", got, args)) < 0:
            return self.base.play_stamp(*args)
        return got

    def play_condense(self, ts, ms, ks, tport, n, out_t, out_m, out_tr, out_cp, tab_key, tab_gen, tab_at, counts,
                      last_on):
        args = (ts, ms, ks, tport, n, out_t, out_m, out_tr, out_cp, tab_key, tab_gen, tab_at, counts, last_on)
        got = self.x.play_condense(*args)
        if got < 0 and (got := self._again("play_condense", got, args)) < 0:
            return self.base.play_condense(*args)
        return got

    def play_stamp_pre(self, ts, ms, trs, cps, t0, i0, i1, mt, ms_, mspt, base, rate, least, fr, out, tr, cp, counts,
                       dry, head, tail, val, nxt, st):
        args = (ts, ms, trs, cps, t0, i0, i1, mt, ms_, mspt, base, rate, least, fr, out, tr, cp, counts, dry, head,
                tail, val, nxt, st)
        got = self.x.play_stamp_pre(*args)
        if got < 0 and (got := self._again("play_stamp_pre", got, args)) < 0:
            return self.base.play_stamp_pre(*args)
        return got

    def play_emit(self, ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, thr, room, out, lens, three,
                  head, tail, val, nxt, st):
        args = (ms, trk, i0, i1, ticks, t_late, muted, tport, ports, synths, thr, room, out, lens, three, head, tail,
                val, nxt, st)
        got = self.x.play_emit(*args)
        if got < 0 and (got := self._again("play_emit", got, args)) < 0:
            return self.base.play_emit(*args)
        return got

    # ---- Rendered ahead's pieces of a loaded MIDI (ahead_loaded): one call a track a piece

    def piece_track(self, b, base, tick, run, t_pre, t_end, sk, cnt, vel, ontk, muted, newch, last, out, t_on, opn,
                    port, base_t, bars, bar_ch, ev_key, ev_sig, nev):
        args = (b, base, tick, run, t_pre, t_end, sk, cnt, vel, ontk, muted, newch, last, out, t_on, opn, port, base_t,
                bars, bar_ch, ev_key, ev_sig, nev)
        got = self.x.piece_track(*args)
        if got.__class__ is int:
            got = self._again("piece_track", got, args)
            if got.__class__ is int:
                return self.base.piece_track(*args)
        return got

    def piece_held_ons(self, b, base, tick, run, t_stop, sk, muted, need, off, tk, vl, pos):
        args = (b, base, tick, run, t_stop, sk, muted, need, off, tk, vl, pos)
        got = self.x.piece_held_ons(*args)
        if got < 0 and (got := self._again("piece_held_ons", got, args)) < 0:
            return self.base.piece_held_ons(*args)
        return got

    def piece_held_head(self, tk, qs, vl, newch, last, out):
        args = (tk, qs, vl, newch, last, out)
        got = self.x.piece_held_head(*args)
        if got.__class__ is int:
            got = self._again("piece_held_head", got, args)
            if got.__class__ is int:
                return self.base.piece_held_head(*args)
        return got

    def piece_note_ons(self, b, base, tick, run, t_stop, sk, muted, want, out_t, out_q, out_v):
        args = (b, base, tick, run, t_stop, sk, muted, want, out_t, out_q, out_v)
        got = self.x.piece_note_ons(*args)
        if got < 0 and (got := self._again("piece_note_ons", got, args)) < 0:
            return self.base.piece_note_ons(*args)
        return got

    def piece_runs_sort(self, key, sig, cnt, n, tk, ts, tc, bits):
        args = (key, sig, cnt, n, tk, ts, tc, bits)
        got = self.x.piece_runs_sort(*args)
        if got < 0 and (got := self._again("piece_runs_sort", got, args)) < 0:
            return self.base.piece_runs_sort(*args)
        return got

    def piece_runs_merge(self, ak, asg, ac, na, bk, bsg, bc, nb, ok, osg, oc):
        args = (ak, asg, ac, na, bk, bsg, bc, nb, ok, osg, oc)
        got = self.x.piece_runs_merge(*args)
        if got < 0 and (got := self._again("piece_runs_merge", got, args)) < 0:
            return self.base.piece_runs_merge(*args)
        return got

    def piece_counted_track(self, key, sig, cnt, n, port, rel, out):
        args = (key, sig, cnt, n, port, rel, out)
        got = self.x.piece_counted_track(*args)
        if got.__class__ is int:
            got = self._again("piece_counted_track", got, args)
            if got.__class__ is int:
                return self.base.piece_counted_track(*args)
        return got

    def piece_order_ok(self, key, sig, cnt, n, bar_ch):
        args = (key, sig, cnt, n, bar_ch)
        got = self.x.piece_order_ok(*args)
        if got < 0 and (got := self._again("piece_order_ok", got, args)) < 0:
            return self.base.piece_order_ok(*args)
        return got == 1

    def piece_open_at(self, key, sig, cnt, n, lim, wlim, take):
        args = (key, sig, cnt, n, lim, wlim, take)
        got = self.x.piece_open_at(*args)
        if got < 0 and (got := self._again("piece_open_at", got, args)) < 0:
            return self.base.piece_open_at(*args)
        return got
