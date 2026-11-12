"""Change visual time signature on the project (user 2026-11-12; maths: notes/rebar.py): the tempo line, signatures
and every shape changed in ONE undo step so the song sounds the same with other bar lines.

Shapes (user 2026-11-12):
- wholly before the stretch: untouched; wholly after it: moved along (as the track list's box move).
- wholly inside one run (bars with one factor): stretched by it (App.stretched + the spam gate, Range, outline
  gate, snap step in beats). A safety net compares the notes it makes with the old ones moved: off by more than
  rounding (or another count) = it becomes plain notes instead (counted on the status line).
- sticking out of the stretch (or over bars of different factors), a Hz bass inside (stage E): CUT AT THE EDGES like
  the track list's box move, every part plain notes, each note moved / stretched exactly; pasted CC events too.
A mask that isn't wholly before / after / inside one run: refused (its notes elsewhere would change)."""

import copy
import math

import numpy as np

from notes import rebar as R
from notes.custom import notes_shape, unpack_notes, block_shift
from notes.engine import pasted_notes
from notes.mask import is_mask
from notes.passengers import pack_cc, rides_of

TIME_KEYS = ("gate", "edge", "by_snap")  # (settings in beats: stretched with the shape)


def classify(app, pl):
    """{shape index: "before" / "after" / ("run", run index) / "cut"} by the shape's notes as they sound (rendered;
    a shape without notes by its drawing). None + reason for a refusal."""
    ppq = app.ppq
    r = app.rendered
    a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
    order = np.argsort(r[:, 5], kind="stable")
    whose = r[order, 5]
    out = {}
    for i, sh in enumerate(app.shapes):
        lo, hi = np.searchsorted(whose, [i, i + 1])
        # (its own notes too: another shape's note can hide one of them, or stretch one over the edge)
        own = np.asarray(app.notes_of(sh), np.int64).reshape(-1, 4)[:, :2]
        rows = np.concatenate([r[order[lo:hi], :2], own])
        if len(rows):
            s0, e1 = int(rows[:, 0].min()), int(rows[:, 1].max())
            b0, b1 = s0 / ppq, e1 / ppq
        else:
            bs = [b for b, _ in sh["pts"]]
            b0, b1 = min(bs), max(bs)
            s0, e1 = round(b0 * ppq), round(b1 * ppq)
        if e1 <= a_t and b1 <= pl["a"] + 1e-9:
            out[i] = "before"
        elif s0 >= b_t:
            out[i] = "after"
        elif s0 >= a_t and e1 <= b_t and R._inside_run(pl, b0, b1) and not sh.get("hz"):
            out[i] = ("run", R._run(pl, b0))
        else:
            if is_mask(sh):
                return None, {"why": "mask", "at": b0}
            out[i] = "cut"
    return out, None


def scaled(sh, f):
    """sh's settings in beats stretched f times (the spam gate, Range's second gate, the outline gate, Notes by
    snap's step; Hz bass's kept gate)."""
    for k in TIME_KEYS:
        if isinstance(sh.get(k), (int, float)) and not isinstance(sh.get(k), bool):
            sh[k] = sh[k] * f
    sh.pop("by_snap_name", None)  # (a note length typed: no longer that length)
    for k in ("range", "range_kept"):
        if isinstance(sh.get(k), dict) and isinstance(sh[k].get("to"), (int, float)):
            sh[k]["to"] = sh[k]["to"] * f
    bh = sh.get("before_hz")
    if isinstance(bh, dict):
        scaled(bh, f)
    for st in sh.get("fx") or ():  # (note tool pages: their lengths in beats)
        _times(st.get("cfg"), PAGE_KEYS.get(st.get("tool"), ()), f)
        if st.get("tool") == "chop" and isinstance(st.get("cfg"), dict):
            st["cfg"]["snap"] = ""  # (picked as a note length: no longer that length)
        if st.get("tool") == "path" and isinstance(st.get("cfg"), dict):
            _path(st["cfg"], f)
        if st.get("tool") == "turn" and isinstance(st.get("r"), (int, float)):
            st["r"] = st["r"] * f  # (beats per key on screen then: the same look stretched)
    _times(sh.get("sustain"), ("gap", "most", "every", "from"), f)
    for k in ("subtract", "extend"):
        g = sh.get(k)
        if isinstance(g, dict) and not (k == "extend" and g.get("unit") == "pct"):
            _times(g, ("by", "to"), f)
            _times(g.get("start"), ("by", "to"), f)
    return sh


PAGE_KEYS = {"chop": ("len",), "claw": ("period",), "strum": ("time", "end_time"), "trim": ("length",),
             "path": ("every", "k")}  # (each page's settings in beats)


def _times(d, keys, f):
    if not isinstance(d, dict):
        return
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            d[k] = v * f


def _path(c, f):
    """Copy along a path's points / middle points / group box: their beats stretched."""
    c["pts"] = [[b * f, k] for b, k in c.get("pts") or ()]
    c["mids"] = [None if m is None else [m[0] * f, m[1]] for m in c.get("mids") or ()]
    g = c.get("grp")
    if isinstance(g, list) and len(g) == 4:
        c["grp"] = [g[0] * f, g[1] * f, g[2], g[3]]


def stretched(app, sh, pl, k):
    """A copy of sh stretched by run k's factor, moved onto the run's new place."""
    oa, ob, na, nb, f = pl["runs"][k]
    new = app.stretched(sh, oa, f, 0.0, 1.0)
    new["pts"] = [[round(b + na - oa, 9) + 0.0, p] for b, p in new["pts"]]
    return scaled(new, f)


def moved(sh, d):
    new = copy.deepcopy(sh)
    new["pts"] = [[round(b + d, 9) + 0.0, p] for b, p in new["pts"]]
    return new


def _abs_rows(sh, ppq):
    """A plain notes shape only moved since made: its notes (start, end, key, velocity, track) where they are + its
    CC events [(tick, track, tag, body)] at their ticks; None when its box isn't simply moved."""
    rows = unpack_notes(sh["notes"])
    got = block_shift(sh, ppq, rows)
    if got is None:
        return None
    T0, K0 = got
    out = np.column_stack([rows[:, 0] + T0, rows[:, 1] + T0, rows[:, 2] + K0, rows[:, 3], rows[:, 4]])
    cc = [(t + T0, k, tag, body) for t, k, tag, body in rides_of(sh["cc"]).events] if sh.get("cc") else []
    return out, cc


def _piece(base, rows, cc, ppq):
    """base (a plain notes shape) holding these notes (absolute rows) and CC events instead."""
    made = notes_shape(np.column_stack([rows[:, 0], rows[:, 1] - rows[:, 0], rows[:, 2], rows[:, 3], rows[:, 4]]),
                       ppq, base.get("name") or "")
    new = copy.deepcopy({k: v for k, v in base.items() if k not in ("notes", "pts", "cc", "rebar_whole")})
    new["notes"], new["pts"] = made["notes"], made["pts"]
    if isinstance(new.get("own_slots"), list):  # (its notes' channels kept while its first point is there: here now)
        new["own_slots"] = [float(v) for v in made["pts"][0]]
    if cc:
        t0 = int(rows[:, 0].min())
        new["cc"] = pack_cc([(max(0, t - t0), k, tag, body) for t, k, tag, body in cc])
    return new


def base_of(app, i, plain, pl):
    """What shape i is moved / stretched from: a Domino paste (only moved) = itself, kept ONE shape with its stored
    notes moved, when the notes it makes then are its old ones moved (nothing else changing them: its own overlaps
    fixed the same way); its tracks keep taking channels together (a plain copy or pieces outside Multi took other
    channels, and Single cut notes against them). Else its plain copy (as it sounds). None when neither works."""
    sh, ppq = app.shapes[i], app.ppq
    if pasted_notes(sh) and not sh.get("picture") and _abs_rows(sh, ppq) is not None:
        base = copy.deepcopy(sh)
        base["rebar_whole"] = True
        made = cut_mapped(base, pl, ppq, whole=True)[0]
        if same_notes(np.asarray(app.notes_of(sh), np.int64).reshape(-1, 4),
                      np.asarray(app.notes_of(made), np.int64).reshape(-1, 4), pl, ppq):
            return base
    if plain is not None and _abs_rows(plain, ppq) is not None:
        if app.channel_mode.get() != "auto":  # (pinned, it joined the pastes' channel pass outside Multi: a paste's
            plain = dict(plain)               # track took another channel and Single cut notes against it; the
            plain.pop("own_slots", None)      # drawn shape it was sat on the one channel, as this does)
        return plain
    return None


def cut_mapped(plain, pl, ppq, whole=False):
    """A plain notes shape (only moved) cut at the stretch's edges by note start, every note's start and end moved
    by the change: [pieces] (each piece's CC events go with the piece playing their track there)."""
    got = _abs_rows(plain, ppq)
    rows, cc = got
    a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
    new = rows.copy()
    new[:, 0] = R.map_ticks(pl, rows[:, 0], ppq)
    new[:, 1] = np.maximum(new[:, 0] + 1, R.map_ticks(pl, rows[:, 1], ppq))
    part = np.where(rows[:, 0] < a_t, 0, np.where(rows[:, 0] < b_t, 1, 2))
    if whole:
        part[:] = 0
    ev_t = np.array([t for t, *_ in cc], np.int64)
    ev_new = R.map_ticks(pl, ev_t, ppq) if len(cc) else ev_t
    ev_k = np.array([k for _, k, *_ in cc], np.int64)
    ev_to = np.full(len(cc), -1, np.int64)
    present = sorted(set(part.tolist()))
    for k in np.unique(ev_k).tolist() if len(cc) else []:  # (to the piece playing that track there: its latest
        at = np.flatnonzero(ev_k == k)                        # note start at or before the event, else its first)
        mine = rows[:, 4] == k
        if not mine.any():
            ev_to[at] = present[0]
            continue
        starts, parts = rows[mine, 0], part[mine]
        o = np.argsort(starts, kind="stable")
        starts, parts = starts[o], parts[o]
        i = np.searchsorted(starts, ev_t[at], "right") - 1
        ev_to[at] = np.where(i >= 0, parts[np.maximum(i, 0)], parts[0])
    out = []
    for p in present:
        m = part == p
        mine = [(int(ev_new[j]), *cc[j][1:]) for j in np.flatnonzero(ev_to == p).tolist()]
        out.append(_piece(plain, new[m], mine, ppq))
    return out


def rows_by_owner(r):
    order = np.argsort(r[:, 5], kind="stable")
    whose = r[order, 5]
    return lambda i: r[order[slice(*np.searchsorted(whose, [i, i + 1]))]]


def same_notes(old, new, pl, ppq, f=1.0):
    """Do new rows (start, end, key, velocity) sound as old ones moved by the change? Each start / end within
    rounding (half the factor + a tick), the same count, keys and velocities."""
    if len(old) != len(new):
        return False
    if not len(old):
        return True
    m = old[:, :4].copy()
    m[:, 0] = R.map_ticks(pl, old[:, 0], ppq)
    m[:, 1] = R.map_ticks(pl, old[:, 1], ppq)
    a = m[np.lexsort((m[:, 3], m[:, 0], m[:, 2]))]
    n = np.asarray(new[:, :4], np.int64)
    b = n[np.lexsort((n[:, 3], n[:, 0], n[:, 2]))]
    tol = math.ceil(f / 2) + 1
    return bool((a[:, 2] == b[:, 2]).all() and (a[:, 3] == b[:, 3]).all()
                and (np.abs(a[:, :2] - b[:, :2]) <= tol).all())


def apply(app, pl, name):
    """The change done on the project in one undo step: {"ok": True, "plain": shapes made plain notes by the safety
    net, "cut": shapes cut at the edges, "off": shapes whose notes still differ} or {"why", "at"} (nothing changed)."""
    new_tempo = R.tempo_of(app.tempo, app.tempo_pane.base(), pl)
    if "why" in new_tempo:
        return new_tempo
    if app.notes_late:
        app.shapes_changed(now=True)
    kinds, why = classify(app, pl)
    if why:
        return why
    ppq = app.ppq
    old_r = np.array(app.rendered[:, :6], np.int64)
    olds = rows_by_owner(old_r)
    for i, k in kinds.items():  # (a shape after the bars whose notes hang on a grid from the song's start (spam
        if k == "after":         # Aligned, Chop "from the song's start", a Hz bass without tones...) and change when
            sh = app.shapes[i]   # moved: plain notes, moved, so it keeps its sound)
            was = np.asarray(app.notes_of(sh), np.int64).reshape(-1, 4)
            if len(was) and not same_notes(was, np.asarray(app.notes_of(moved(sh, pl["shift"])), np.int64)
                                           .reshape(-1, 4), pl, ppq):
                kinds[i] = "cut"
    loose = [i for i, k in kinds.items() if k == "cut" or isinstance(k, tuple)]
    plains = app.plain_copies(set(loose)) if loose else {}  # (as they sound now: the cut pieces + the net's way back)
    bases = {i: base_of(app, i, plains.get(i), pl) for i in loose}
    from window import track_tempo
    track_tempo.visual(app)
    app.roll.end_typing()
    app.push_undo(name=name)
    shapes, extra, cut = list(app.shapes), [], 0
    for i, k in kinds.items():
        sh = app.shapes[i]
        if k == "after":
            shapes[i] = moved(sh, pl["shift"])
        elif isinstance(k, tuple):
            shapes[i] = stretched(app, sh, pl, k[1])
        elif k == "cut" and bases[i] is not None:
            pieces = cut_mapped(bases[i], pl, ppq, whole=bases[i].get("rebar_whole", False))
            shapes[i] = pieces[0]
            extra += pieces[1:]
            cut += 1
    app.shapes[:] = shapes + extra
    app.tempo.clear()
    app.tempo.update(new_tempo)
    app.select_many([], None)
    app.shapes_changed(now=True)
    # the safety net: a stretched shape whose notes aren't the old ones moved = plain notes, cut and moved
    news = rows_by_owner(np.array(app.rendered[:, :6], np.int64))
    back = []
    for i, k in kinds.items():
        if isinstance(k, tuple) and not same_notes(olds(i), news(i), pl, ppq, pl["runs"][k[1]][4]):
            back.append(i)
    plain_n = 0
    if back:
        more = []
        for i in back:
            if bases[i] is not None:
                pieces = cut_mapped(bases[i], pl, ppq, whole=bases[i].get("rebar_whole", False))
                app.shapes[i] = pieces[0]
                more += pieces[1:]
                plain_n += 1
        app.shapes.extend(more)
        app.shapes_changed(now=True)
    off = 0
    news = rows_by_owner(np.array(app.rendered[:, :6], np.int64))
    for i, k in kinds.items():
        if k in ("after",) or isinstance(k, tuple):
            off += not same_notes(olds(i), news(i), pl, ppq, max(r[4] for r in pl["runs"]))
    app.playhead = max(0.0, R.map_beat(pl, app.playhead))  # (on the same music)
    app.roll.show_playhead()
    app.tempo_changed()
    app.meter_changed()
    return {"ok": True, "plain": plain_n, "cut": cut, "off": off}
