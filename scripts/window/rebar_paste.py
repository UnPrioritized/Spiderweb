"""Time signatures in the Conductor pane's copies (user 2026-11-15): a signature is copied ONLY when its own mark (the
start of its bar) is inside what's copied (so no unexpected signature comes along); an editable time signature
change copied that way pastes as a NEW EDITABLE CHANGE made fresh on the bars it lands on (the target's notes
stretch, the usual refusals: nothing of the paste happens then, the status line says why). Landing on another
editable change: the heads-up first ("Make ... permanent?"), then pasted on top; Cancel = nothing pasted. One undo
step a paste.

A copy holds, besides the tempo (TempoPane.clip_of): "sigs" / "keys" [[beat from the copy's start, ...]] (plain
marks, put_domino_sigs' "replace" rule: the track list's Conductor row boxes do the same) and "rebar" [{"a", "b"
(its start / end beats from the copy's start, as it is now), "n" (bars), "sig", "dense", "between"}]. The copied
tempo INSIDE a change's bars is left out (my pick): the change makes its own from the bars it lands on, so the
target's own tempo there gets stretched the same way (else it would count twice)."""

import copy

from files.lang import tr, trn
from notes import meter
from notes import rebar as R
from notes.tempo import catch_get, catch_set, shift_heard

EPS = 1e-9


# ------------------------------------------------------------------ what a copy holds

def extras(app, spans):
    """{"sigs", "keys", "rebar"} of the spans [(lo, hi)] (beats, joined), beats from spans[0][0]. A mark counts when
    its beat is in [lo, hi) of a span (its bar starts inside); an editable change by its start (its own mark)."""
    from window import rebar_edit
    lo = spans[0][0]
    inside = lambda x: any(a - EPS <= x < b - EPS for a, b in spans)
    out_rb, own = [], set()
    for c in rebar_edit.changes(app):
        a = c["pl"]["a"]
        if inside(a):
            out_rb.append({"a": a - lo, "b": c["pl"]["new_b"] - lo, "n": c["pl"]["count"], "sig": list(c["sig"]),
                           "dense": bool(c["dense"]), "between": bool(c["between"])})
            own.add(round(a, 6))
    tempo = app.tempo
    sigs = [[m[0] - lo, *m[1:]] for m in tempo.get("sigs") or []  # (real marks only: no made-up 4/4 at beat 0)
            if inside(m[0]) and round(m[0], 6) not in own]
    keys = [[m[0] - lo, *m[1:]] for m in tempo.get("keysigs") or [] if inside(m[0])]
    return {"sigs": sigs, "keys": keys, "rebar": out_rb}


def count(clip):
    """Signatures + changes a copy holds (the status line's second number)."""
    return sum(len(clip.get(k) or ()) for k in ("sigs", "keys", "rebar"))


# ------------------------------------------------------------------ pasting

def put(app, clip, at, name, push=True):
    """clip laid from beat at (TempoPane.paste / a Ctrl+drag let go / the track list's paste). push: its own undo
    step; False = the caller's step is pushed already (Cancel / a refusal then takes it back). Returns
    {"ok"} / {"why"} / {"wait"} (the loaded MIDI's note ticks read first: done afterwards)."""
    pane = app.tempo_pane
    if not clip.get("rebar"):
        tempo_part = clip["points"] or clip["catch"]
        if push and not tempo_part:
            app.push_undo(name=name)
        if tempo_part:
            pane.put_copies(clip, at, name, push=push)
        if clip.get("sigs") or clip.get("keys"):
            pane.put_domino_sigs(clip.get("sigs") or [], clip.get("keys") or [], at, clip["length"], "replace")
        return {"ok": True}
    from window import rebar_edit
    items = sorted(clip["rebar"], key=lambda r: r["a"])
    sigs = meter.sigs_of(app.tempo)
    s = max(0.0, at + items[0]["a"])  # (the first change on the bar line nearest where it lands: the whole paste
    a, b = meter.bar_line(sigs, s), meter.bar_line(sigs, s, after=True)  # moves with it, the track list's sig_dt)
    s = a if s - a <= b - s else b
    at = s - items[0]["a"]
    while at < -EPS:  # (the copy's start before the song's: the next bar line on)
        s = meter.bar_line(sigs, s + 1e-6, after=True)
        at = s - items[0]["a"]
    end = at + max(clip["length"], max(r["b"] for r in items))
    hit = {c["id"] for c in rebar_edit.changes(app) if c["pl"]["a"] < end - 1e-6 and at < c["pl"]["new_b"] - 1e-6}
    if hit:
        hit = rebar_edit._sharing(rebar_edit.of(app), hit)
        if not getattr(app, "rebar_bake_skip", False):
            from window import big_ask
            where = rebar_edit.bars_text(app, hit)
            q = tr("conductor.rebar_bake" if len(hit) > 1 else "conductor.rebar_bake_one", bars=where)
            go, skip = big_ask.dialog(app, tr("big_ask.title"), q + "\n\n" + tr("conductor.rebar_bake_text"), False,
                                      tick=tr("conductor.rebar_bake_tick"), yes=tr("conductor.rebar_paste_yes"),
                                      no=tr("conductor.rebar_bake_no"))
            if not go:
                if not push:
                    rebar_edit.undo_edit(app)
                app.status.config(text=tr("conductor.rebar_paste_cancel"))
                return {"why": "cancel"}
            if skip:
                app.rebar_bake_skip = True  # (this session only, as the edits' heads-up)
    if pane.loaded_ticks_wait(at, end - EPS, then=lambda: _said(app, _lay(app, clip, at, name, push, hit))):
        return {"wait": True}
    return _said(app, _lay(app, clip, at, name, push, hit))


def _said(app, got):
    """The status line after a paste with changes."""
    pane = app.tempo_pane
    if "why" in got:
        app.status.config(text=tr("conductor.rebar_paste_refused", why=pane.rebar_why_any(got)))
    else:
        n = got.get("n", 0)
        if n:
            app.status.config(text=trn("conductor.rebar_pasted", n, n=n))
    return got


def _segment(items, x):
    """(k, inside): the changes items[:k] lie before beat x (copy beats); inside = x inside items[k]'s bars."""
    k = 0
    for r in items:
        if x >= r["b"] - EPS:
            k += 1
        elif x > r["a"] + EPS:
            return k, True
        else:
            break
    return k, False


def _lay(app, clip, at, name, push, bake):
    """The paste done: plain marks, the tempo outside the changes' bars, then each change made fresh (oldest bars
    first). Laid first where the bars are BEFORE the changes (each change's bars as long as the target's own), so
    each change, once made, puts what comes after it back on the copy's own beats. A refusal: all as it was."""
    from notes.tempo import changed as tempo_changed_now
    from window import rebar_edit, rebar_work as W
    from window.history import undo_state
    pane = app.tempo_pane
    items = sorted(clip["rebar"], key=lambda r: r["a"])
    kept = (list(app.shapes), copy.deepcopy(app.tempo), list(app.loaded_map or []), app.deleted, rebar_edit.of(app),
            app.playhead, app.sels, app.sel, pane.bands, pane.sel)
    if push:
        app.push_undo(name=name)

    def back(out):
        app.shapes[:] = kept[0]
        app.tempo.clear()
        app.tempo.update(kept[1])
        tempo_changed_now()
        app.loaded_map = kept[2]
        app.deleted = kept[3]
        app.rebar = kept[4]
        app.playhead = kept[5]
        app.select_many(sorted(kept[6]), kept[7])
        pane.bands, pane.sel = kept[8], [s for s in kept[9]]
        app.shapes_changed(now=True)
        app.tempo_changed()
        app.meter_changed()
        pane.request_redraw()
        app.drop_empty_step(undo_state(app.shapes))
        app.sync_history()
        return out

    if bake:
        rebar_edit.bake_ids(app, bake)
    # 1. plain marks, segment by segment, and how much longer each change's bars are where they land
    deltas, d, lo, firsts = [], 0.0, 0.0, []
    csigs, ckeys = clip.get("sigs") or [], clip.get("keys") or []
    for r in items + [None]:
        hi = r["a"] if r is not None else max(clip["length"], items[-1]["b"])
        seg_s = [[m[0] - lo, *m[1:]] for m in csigs if lo - EPS <= m[0] < hi - EPS]
        seg_k = [[m[0] - lo, *m[1:]] for m in ckeys if lo - EPS <= m[0] < hi - EPS]
        if seg_s or seg_k:
            pane.put_domino_sigs(seg_s, seg_k, at + lo + d, hi - lo, "replace")
        if r is None:
            break
        sigs = meter.sigs_of(app.tempo)
        s = at + r["a"] + d
        if abs(meter.bar_line(sigs, s + EPS) - s) > 1e-6:
            return back({"why": "paste_bar", "at": s})
        first = meter.where(sigs, s + EPS)[0]
        length = meter.bar_start(sigs, first + r["n"]) - s
        deltas.append(d)
        d += length - (r["b"] - r["a"])
        deltas.append(d)
        lo = r["b"]
    # 2. the tempo outside the changes' bars, each part where it lands before they're made
    shift = lambda x: deltas[2 * _segment(items, x)[0] - 1] if _segment(items, x)[0] else 0.0
    pts, by = [], {}
    for p in clip["points"]:
        by.setdefault(round(p[0], 9), []).append(p)
    for p in clip["points"]:
        x = p[0]
        k, inside = _segment(items, x)
        if inside:
            continue
        on_a = [r for r in items if abs(x - r["a"]) <= EPS]
        on_b = [r for r in items if abs(x - r["b"]) <= EPS]
        same = by[round(x, 9)]
        if on_a and (len(same) < 2 or p is not same[0]):  # (the change's own tempo leaving its start: it makes it)
            continue
        if on_b and not on_a and (p is not same[-1] or len(same) < 2 and x >= clip["length"] - EPS):
            continue  # (what arrives at its end: the change's own; the tempo leaving it stays)
        q = copy.deepcopy(p)
        dx = shift(x + EPS) if on_b else shift(x)
        q[0] = x + dx
        shift_heard(q, dx, None)
        pts.append(q)
    cs = []
    for c in clip["catch"]:
        xs = [c[0]] + [catch_get(c, k) for k in (3, 4) if catch_get(c, k) is not None]
        segs = {_segment(items, x) for x in xs}
        if len(segs) != 1 or next(iter(segs))[1]:  # (reaching into / over a change: left out)
            continue
        dx = shift(c[0])
        item = [c[0] + dx] + list(c[1:3])
        for k in (3, 4):
            v = catch_get(c, k)
            catch_set(item, k, None if v is None else v + dx)
        for k in (5, 6, 7):
            catch_set(item, k, catch_get(c, k))
        cs.append(item)
    spans = []
    for a, b in clip.get("spans") or [(0.0, clip["length"])]:
        cuts = [a]
        for r in items:
            if r["a"] < b - EPS and r["b"] > a + EPS:
                cuts += [max(a, r["a"]), min(b, r["b"])]
        cuts.append(b)
        for x0, x1 in zip(cuts[::2], cuts[1::2]):
            if x1 - x0 > EPS or b - a <= EPS:
                dx = shift(x0 + EPS) if any(abs(x0 - r["b"]) <= EPS for r in items) else shift(x0)
                spans.append((x0 + dx, x1 + dx))
    if pts or cs:
        pane.put_copies(dict(clip, points=pts, catch=cs, spans=spans), at, name, push=False, teal=False)
    # 3. the changes, made fresh on the bars where they now start (each puts what's after it on the copy's beats)
    made = 0
    for r in items:
        sigs = meter.sigs_of(app.tempo)
        s = at + r["a"]
        if abs(meter.bar_line(sigs, s + EPS) - s) > 1e-6:
            return back({"why": "paste_bar", "at": s})
        first = meter.where(sigs, s + EPS)[0]
        lo_b, hi_b = rebar_edit.bars_beats(sigs, first, first + r["n"] - 1)
        pl = R.plan(sigs, lo_b, hi_b, *r["sig"])
        if rebar_edit._same_lengths(sigs, pl):  # (those bars are that long already: nothing to make)
            continue
        got = W.apply(app, pl, name, dense=r["dense"], between=r["between"], push=False)
        if "why" in got:
            return back(got)
        made += 1
    pane.bands = [(at + a, at + b) for a, b in clip.get("spans") or [(0.0, clip["length"])] if b > a]
    pane.sel, pane.part = [], None
    app.tempo_changed()
    app.meter_changed()
    pane.rebar_done()
    return {"ok": True, "n": made}
