"""Change visual time signature on the project (user 2026-11-12; maths: notes/rebar.py): the tempo line, signatures
and every shape changed in ONE undo step so the song sounds the same with other bar lines.

Shapes (user 2026-11-12):
- wholly before the stretch: untouched; wholly after it: moved along (as the track list's box move).
- wholly inside one run (bars with one factor): stretched by it (App.stretched + the spam gate, Range, outline
  gate, snap step in beats). A safety net compares the notes it makes with the old ones moved: off by more than
  rounding (or another count) = it becomes plain notes instead (counted on the status line).
- sticking out of the stretch (or over bars of different factors), a Hz bass inside (stage E): CUT AT THE EDGES like
  the track list's box move, every part plain notes, each note moved / stretched exactly; pasted CC events too.
A mask that isn't wholly before / after / inside one run: refused (its notes elsewhere would change).
A LOADED MIDI reaching the bars (PLAN.md 2026-11-14, user: "visual only until it gets exported"): the file stays as
it is; the change adds one link to app.loaded_map (notes/loaded_map.py) in the same undo step, so its notes are shown,
heard and exported moved exactly like a plain notes shape's. Its note ticks (LoadedMidi.anchors, as SHOWN) count with
the shapes' notes for "under a tick apart" and the short BPM changes (user: same as shapes) and the marks after.
Extract / Delete loaded notes boxes move with the bars (their ticks are SHOWN ones)."""

import copy
import json
import math

import numpy as np

from notes import loaded_map
from notes import rebar as R
from notes.tempo import changed as tempo_changed_now
from notes.custom import notes_shape, unpack_notes, block_shift
from notes.engine import pasted_notes
from notes.hz_settings import stretched_hz
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
    out = Kinds()
    reasons = out.reasons
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
        elif s0 >= a_t and e1 <= b_t and R._inside_run(pl, b0, b1):
            why = plain_why(sh)
            out[i] = ("run", R._run(pl, b0)) if why is None else "cut"
            if why:
                reasons[i] = why
        else:
            if is_mask(sh):
                return None, {"why": "mask", "at": b0}
            out[i] = "cut"
            reasons[i] = "edge"
    return out, None


def plain_why(sh):
    """Why a shape inside the bars becomes plain notes rather than stretched (user 2026-11-12): "gliss" (its grids
    are note lengths), "fixed" (a Hz bass with Fixed gates: whole ticks, rounded again = off pitch), else None."""
    if sh.get("kind") == "gliss":
        return "gliss"
    hz = sh.get("hz")
    if hz:
        from notes.hz_settings import layers_of
        if hz.get("fixed") or any(n.get("gate") == "fixed" for l in layers_of(hz) for n in l.get("tones") or ()):
            return "fixed"
    return None


def becomes_plain(sh):
    """Is the shape something else than plain notes now (a plain / pasted notes shape cut stays notes)?"""
    return "notes" not in sh


def forecast(app, pl):
    """{reason: shapes} that will become plain notes (cheap: no notes made; a test run finds the rest) or a refusal
    {"why", "at"}. Reasons: "edge" (sticking out of the bars), "gliss", "fixed"."""
    if app.notes_late:
        app.shapes_changed(now=True)
    kinds, why = classify(app, pl)
    if why:
        return why
    out = {}
    for i, r in kinds.reasons.items():
        if becomes_plain(app.shapes[i]):
            out[r] = out.get(r, 0) + 1
    ppq, r = app.ppq, app.rendered  # (notes the change brings under a tick apart: "dense", counted apart)
    a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
    starts = with_loaded(app, r[(r[:, 0] >= a_t) & (r[:, 0] < b_t), 0], a_t, b_t)
    groups = R.dense_spots(pl, starts, ppq)
    if groups:
        out["dense"] = n = int(np.isin(starts, np.concatenate([np.array(g, np.int64) for g in groups])).sum())
        if n >= DENSE_MOST * len(starts):  # (user 2026-11-12: a 1-tick spam squashed = short BPM changes everywhere,
            out["dense_most"] = ppq_times(pl, starts, ppq)  # undoing the change: say a higher PPQ is needed instead)
    n = between_count(app, pl)
    if n:  # (user 2026-11-14: notes landing between ticks, their own line + tick)
        out["between"] = n
    return out


def between_ticks(app, pl):
    """(The shapes' notes in the bars as rows, every note start / end there + the loaded MIDI's): between_fix's
    ticks."""
    ppq, r = app.ppq, app.rendered
    a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
    rows = np.asarray(r[(r[:, 0] >= a_t) & (r[:, 0] < b_t), :2], np.int64)
    own = np.unique(rows)
    return rows, with_loaded(app, own[(own >= a_t) & (own < b_t)], a_t, b_t)


def between_count(app, pl):
    """How many notes will land between two ticks (a note counted once, start or end; a loaded MIDI's by its
    ticks)."""
    rows, ticks = between_ticks(app, pl)
    offs = R.between_spots(pl, ticks, app.ppq)
    if not len(offs):
        return 0
    n = int((np.isin(rows[:, 0], offs) | np.isin(rows[:, 1], offs)).sum()) if len(rows) else 0
    return n + int(len(np.setdiff1d(offs, rows.ravel())))


DENSE_MOST = 0.5  # (share of the bars' notes under a tick apart from which the box says "most notes": my pick)


def moves_loaded(app, a_t):
    """Does the change move a loaded MIDI's notes (some shown at the bars' start or later)? A project whose MIDI
    is missing (Go on without it) counts: its notes can't be seen, so the bars move them anyway."""
    lm = app.loaded
    if lm is None:
        return getattr(app, "midi_link", None) is not None
    if not lm.notes:
        return False
    from notes.loaded_shown import shown_end
    return shown_end(app) > a_t


def loaded_ticks(app, a_t, b_t):
    """The loaded MIDI's note ticks (every start / end: LoadedMidi.anchors, read first by ConductorMarks.rebar) as
    SHOWN in a_t .. b_t - 1; empty without one (or not read)."""
    lm = app.loaded
    if lm is None or not lm.notes or lm._anchors is None or not moves_loaded(app, a_t):
        return np.zeros(0, np.int64)
    from notes.loaded_shown import anchors_shown
    t = anchors_shown(lm, app.loaded_map)[0]
    lo, hi = np.searchsorted(t, [a_t, b_t])
    return t[lo:hi]


def with_loaded(app, starts, a_t, b_t):
    """The shapes' note starts in the bars + the loaded MIDI's note ticks there (each tick once more at most: an
    Extract's notes are in both)."""
    more = loaded_ticks(app, a_t, b_t)
    if not len(more):
        return starts
    return np.concatenate([np.asarray(starts, np.int64), np.setdiff1d(more, starts)])


def ppq_times(pl, starts, ppq, most=16):
    """How many times the PPQ the change needs so no note comes under a tick apart (2 = twice), or 0 (more than
    `most`)."""
    u = np.unique(np.asarray(starts, np.int64))
    pl = dict(pl, sound=None)  # (the beat map only: by-sound spans are in this PPQ's ticks)
    for k in range(2, most + 1):
        if not R.dense_spots(pl, u * k, ppq * k):
            return k
    return 0


class Kinds(dict):
    """classify's answer: {shape: kind} + .reasons {shape: why it becomes plain notes}."""

    def __init__(self):
        super().__init__()
        self.reasons = {}


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
    if new.get("hz"):  # (a Hz bass: its notes, lines, rates and tempo with it, so it stays in tune)
        new["hz"] = stretched_hz(new["hz"], f)
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


def box_ticks(pl, ppq, link, ticks):
    """Shown ticks moved by the change the way the loaded notes are (its link: rewrite's rule), else the plan's."""
    t = np.asarray(ticks, np.int64)
    if link is None:
        return R.map_ticks(pl, t, ppq)
    return loaded_map.apply_link(loaded_map.unpack(link["n"]), loaded_map.unpack(link["f"]), t)


def moved_groups(groups, pl, ppq, link):
    """Extract-like groups ({"boxes": [[t0, t1, k0, k1]], ...}, SHOWN ticks) on the same loaded notes after the
    change: t0 -> its new tick, t1 -> past the new tick of t1 - 1 (a note on the box's last tick stays in when a
    squeeze puts it with t1). A box left with nothing between = one tick."""
    out = []
    for g in groups:
        b = np.array(g["boxes"], np.int64).reshape(-1, 4)
        if not len(b):
            out.append(g)
            continue
        t0 = box_ticks(pl, ppq, link, b[:, 0])
        t1 = np.maximum(box_ticks(pl, ppq, link, b[:, 1]), box_ticks(pl, ppq, link, b[:, 1] - 1) + 1)
        t1 = np.maximum(t1, t0 + 1)
        nb = np.column_stack([t0, t1, b[:, 2], b[:, 3]])
        out.append(dict(g, boxes=nb.tolist()))
    return out


def with_boxes_moved(sh, pl, ppq, link):
    """sh with its Extract boxes (and its merge parts' at any depth) moved like the loaded notes; sh itself when it
    holds none (no copy)."""
    from notes.loaded import extract_groups
    new = None
    if sh.get("extract") is not None:
        groups = extract_groups(sh["extract"])
        moved_g = moved_groups(groups, pl, ppq, link)
        if any(a["boxes"] != b["boxes"] for a, b in zip(groups, moved_g)):  # (before the bars: kept as it is)
            new = dict(sh)
            new["extract"] = moved_g[0] if isinstance(sh["extract"], dict) and len(moved_g) == 1 else moved_g
    for k in ("merge", "unmerge"):
        m = sh.get(k)
        if isinstance(m, dict) and isinstance(m.get("parts"), list):
            parts = [with_boxes_moved(p, pl, ppq, link) if isinstance(p, dict) else p for p in m["parts"]]
            if any(a is not b for a, b in zip(parts, m["parts"])):
                new = new if new is not None else dict(sh)
                new[k] = dict(m, parts=parts)
    return sh if new is None else new


def rows_by_owner(r):
    order = np.argsort(r[:, 5], kind="stable")
    whose = r[order, 5]
    return lambda i: r[order[slice(*np.searchsorted(whose, [i, i + 1]))]]


def same_notes(old, new, pl, ppq, f=1.0):
    """Do new rows (start, end, key, velocity) sound as old ones moved by the change? Each start / end within ONE
    tick of where it exactly belongs now (user 2026-11-12: every note within a tick of when it sounded; the exact
    place, not the rounded one, else a shape's own rounding added another tick), the same count, keys, velocities."""
    if len(old) != len(new):
        return False
    if not len(old):
        return True
    m = np.column_stack([old[:, :4].astype(float), np.ones((len(old), 2))])  # (+ each end's leeway in ticks)
    for c in (0, 1):
        exact = R.map_beats(pl, old[:, c] / ppq) * ppq
        if pl.get("sound"):  # (ramps placed by when they sound: there the sound's own nearest tick)
            snd = np.zeros(len(old), bool)
            for lo, hi in pl["sound_spans"]:
                snd |= (old[:, c] >= lo) & (old[:, c] < hi)
            if snd.any():
                from notes.tempo_file import seconds_at, ticks_at
                ro, rn = pl["sound"]
                exact[snd] = ticks_at(rn, ppq, seconds_at(ro, ppq, old[snd, c].astype(float)))
        done = pl.get("between_done")
        if done is not None and len(done):  # (kept exact in time by short BPM changes on THAT tick: exactly there,
            hit = np.isin(old[:, c], done)  # a shape's own rounding a tick off would sound off: plain notes)
            exact[hit] = R.map_ticks(pl, old[hit, c], ppq)
            m[hit, 4 + c] = 0.0
        dm = pl.get("dense_map")
        if dm:  # (kept apart by short BPM changes: exactly there)
            i = np.minimum(np.searchsorted(dm[0], old[:, c]), len(dm[0]) - 1)
            hit = dm[0][i] == old[:, c]
            exact[hit] = dm[1][i[hit]]
            m[hit, 4 + c] = 0.0  # (its own tick exactly: a shape's rounding put it back on its neighbour's)
        m[:, c] = exact
    a = m[np.lexsort((m[:, 3], m[:, 0], m[:, 2]))]
    n = np.asarray(new[:, :4], np.int64)
    b = n[np.lexsort((n[:, 3], n[:, 0], n[:, 2]))]
    return bool((a[:, 2] == b[:, 2]).all() and (a[:, 3] == b[:, 3]).all()
                and (np.abs(a[:, :2] - b[:, :2]) <= a[:, 4:6] + 1e-6).all())


def kept_sound(sh, old, new, pl, ppq, f):
    """same_notes, or for a Hz bass: its repeats are made again at the stretched gates, so their count and ticks may
    differ by rounding (Fixed gates are whole ticks: rounded again). Kept when its notes cover the same time (first
    start, last end within rounding), as many within 2 %, on the same keys."""
    if same_notes(old, new, pl, ppq, f):
        return True
    if not sh.get("hz") or not len(old) or not len(new):
        return False
    tol = math.ceil(f / 2) + 1
    a0, a1 = R.map_ticks(pl, int(old[:, 0].min()), ppq), R.map_ticks(pl, int(old[:, 1].max()), ppq)
    return (abs(len(new) - len(old)) <= 0.02 * len(old) and abs(int(new[:, 0].min()) - a0) <= tol
            and abs(int(new[:, 1].max()) - a1) <= tol and set(old[:, 2].tolist()) == set(new[:, 2].tolist()))


def _rounded(pl, ticks, ppq):
    """Of ticks inside the bars: (their new ticks, which ones land between ticks: rounded to the nearest)."""
    t = np.asarray(ticks, np.int64)
    ta = np.array([round(r[0] * ppq) for r in pl["runs"]], np.int64)
    f = np.array([r[4] for r in pl["runs"]], float)
    i = np.maximum(np.searchsorted(ta, t, "right") - 1, 0)
    exact = (t - ta[i]) * f[i]
    return R.map_ticks(pl, t, ppq), np.abs(exact - np.rint(exact)) > 1e-6


def spots_of(app, pl, old_r):
    """Where the change can't be exact (user 2026-11-12: shown like Keep timing's limit texts on the changed bars
    until the next step): {"rebar_round": beats of notes moved to the nearest tick, "rebar_dense": notes a tick or
    more apart now on one tick, "rebar_cc": pasted CC events moved to the nearest tick}, new beats."""
    ppq = app.ppq
    a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
    on = (old_r[:, 0] >= a_t) & (old_r[:, 0] < b_t)
    # (+ the loaded MIDI's note starts / ends there: they move the same way)
    ticks = np.unique(np.concatenate([old_r[on, 0], old_r[on, 1], loaded_ticks(app, a_t, b_t + 1)]))
    ticks = ticks[(ticks >= a_t) & (ticks <= b_t)]
    out = {"rebar_round": [], "rebar_dense": [], "rebar_cc": []}
    if len(ticks):
        new, off = _rounded(pl, ticks, ppq)
        done = pl.get("between_done")
        if done is not None and len(done):  # (kept exact by short BPM changes: not moved in time)
            off &= ~np.isin(ticks, done)
        out["rebar_round"] = (np.unique(new[off]) / ppq).tolist()
        same = np.flatnonzero(new[1:] == new[:-1])  # (two old ticks on one new tick)
        out["rebar_dense"] = (np.unique(new[same]) / ppq).tolist()
    from notes.passengers import placed
    cc = [t for sh in app.shapes if sh.get("cc") and "notes" in sh
          for t, *_ in placed(sh, unpack_notes(sh["notes"]), ppq) if a_t <= t < b_t]
    if cc:
        new, off = _rounded(pl, np.array(cc, np.int64), ppq)
        out["rebar_cc"] = (np.unique(new[off]) / ppq).tolist()
    return out


def apply(app, pl, name, dry=False, dense=True, between=False):
    """The change done on the project in one undo step: {"ok": True, "plain": shapes made plain notes by the safety
    net, "cut": shapes cut at the edges, "off": shapes whose notes still differ} or {"why", "at"} (nothing changed).
    dry: a test run (OK first, user 2026-11-12) taken back from its OWN copy, never through undo / redo, so Cancel
    leaves the History, Ctrl+Y, a half-drawn shape and the tool windows' tries exactly as they were. dense /
    between: "Short BPM changes for exact timing" for notes under a tick apart / landing between ticks."""
    if not dry:
        return _apply(app, pl, name, False, dense, between)
    kept = (list(app.shapes), json.dumps(app.tempo))
    try:
        return _apply(app, pl, name, True, dense, between)
    finally:
        if (len(app.shapes) != len(kept[0]) or any(a is not b for a, b in zip(app.shapes, kept[0]))
                or json.dumps(app.tempo) != kept[1]):
            app.shapes[:] = kept[0]
            app.tempo.clear()
            app.tempo.update(json.loads(kept[1]))
            tempo_changed_now()
            app.shapes_changed(now=True)


def _apply(app, pl, name, dry, dense, between=False):
    new_tempo = R.tempo_of(app.tempo, app.tempo_pane.base(), pl, app.ppq)
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
    a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
    starts = with_loaded(app, old_r[(old_r[:, 0] >= a_t) & (old_r[:, 0] < b_t), 0], a_t, b_t)
    pl["dense_map"] = None  # (user 2026-11-12: "Short BPM changes for exact timing" ticked = notes kept apart)
    pl["between_done"] = None  # (user 2026-11-14: the same for notes landing between ticks; first: dense_fix keeps
    between_n = (R.between_fix(app.tempo, new_tempo, app.tempo_pane.base(), ppq, pl,  # the times it finds there)
                               between_ticks(app, pl)[1]) if between else 0)
    dense_n = R.dense_fix(app.tempo, new_tempo, app.tempo_pane.base(), ppq, pl, starts) if dense else 0
    link, new_map = None, app.loaded_map
    was_map = list(app.loaded_map or [])
    if moves_loaded(app, a_t):  # (the loaded MIDI's notes: shown moved through one more link, same undo step)
        link = loaded_map.link_of(pl, ppq, loaded_ticks(app, a_t, b_t + 1))  # (each of its notes exactly placed)
        new_map = loaded_map.add(app.loaded_map, link)
    if new_map:  # (a Keep timing part in bars changed over the loaded MIDI: refused, user 2026-11-14)
        clash = loaded_map.keep_clash(new_map, new_tempo, ppq)
        if clash:
            return clash
    spots = None if dry else spots_of(app, pl, old_r)
    for i, k in kinds.items():  # (a shape after the bars whose notes hang on a grid from the song's start (spam
        if k == "after":         # Aligned, Chop "from the song's start", a Hz bass without tones...) and change when
            sh = app.shapes[i]   # moved: plain notes, moved, so it keeps its sound)
            was = np.asarray(app.notes_of(sh), np.int64).reshape(-1, 4)
            if len(was) and not same_notes(was, np.asarray(app.notes_of(moved(sh, pl["shift"])), np.int64)
                                           .reshape(-1, 4), pl, ppq):
                kinds[i] = "cut"
                kinds.reasons[i] = "grid"
    loose = [i for i, k in kinds.items() if k == "cut" or isinstance(k, tuple)]
    plains = app.plain_copies(set(loose)) if loose else {}  # (as they sound now: the cut pieces + the net's way back)
    bases = {i: base_of(app, i, plains.get(i), pl) for i in loose}
    if not dry:
        from window import track_tempo
        track_tempo.visual(app)
        app.roll.end_typing()
        app.roll.cancel_draft()  # (a half-drawn shape goes, as with undo: the shapes change under it)
        app.push_undo(name=name)
        if new_map is not app.loaded_map:
            app.loaded_map = new_map  # (sync_extract, from shapes_changed below, reads the boxes again under it)
        if app.deleted:  # (Delete loaded notes' boxes: on the same notes)
            app.deleted = moved_groups(app.deleted, pl, ppq, link)
    shapes, extra, cut = list(app.shapes), [], 0
    was_plain = {i: not becomes_plain(sh) for i, sh in enumerate(app.shapes)}
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
    if not dry:  # (Extract boxes: on the same loaded notes; a test run changes no notes by them)
        app.shapes[:] = [with_boxes_moved(sh, pl, ppq, link) for sh in app.shapes]
    app.tempo.clear()
    app.tempo.update(new_tempo)
    tempo_changed_now()
    if not dry:  # (a test run only adds shapes at the end: the picks still point at the same ones)
        app.select_many([], None)
    app.shapes_changed(now=True)
    # the safety net: a stretched shape whose notes aren't the old ones moved = plain notes, cut and moved
    news = rows_by_owner(np.array(app.rendered[:, :6], np.int64))
    back = []
    for i, k in kinds.items():
        if isinstance(k, tuple) and not kept_sound(app.shapes[i], olds(i), news(i), pl, ppq, pl["runs"][k[1]][4]):
            back.append(i)
    plain_n = 0
    if back:
        more = []
        for i in back:
            if bases[i] is not None:
                pieces = cut_mapped(bases[i], pl, ppq, whole=bases[i].get("rebar_whole", False))
                if not dry:
                    pieces = [with_boxes_moved(p, pl, ppq, link) for p in pieces]
                app.shapes[i] = pieces[0]
                more += pieces[1:]
                plain_n += 1
                kinds.reasons[i] = "net"
        app.shapes.extend(more)
        app.shapes_changed(now=True)
    off = 0
    news = rows_by_owner(np.array(app.rendered[:, :6], np.int64))
    for i, k in kinds.items():
        if k in ("after",) or isinstance(k, tuple):
            off += not kept_sound(app.shapes[i], olds(i), news(i), pl, ppq, max(r[4] for r in pl["runs"]))
    whys = {}
    for i, r in kinds.reasons.items():  # (shapes that were something else than notes, by why)
        if not was_plain[i] and (r != "net" or i in back):
            whys[r] = whys.get(r, 0) + 1
    got = {"ok": True, "plain": plain_n, "cut": cut, "off": off, "whys": whys, "dense": dense_n, "between": between_n,
           "loaded": link is not None and len(new_map) > len(was_map)}
    if dry:  # (a test run: put back by apply)
        return got
    app.playhead = max(0.0, R.map_beat(pl, app.playhead))  # (on the same music)
    app.roll.show_playhead()
    app.tempo_changed()
    app.meter_changed()
    app.tempo_pane.rebar_marks(spots, pl["a"], pl["new_b"])
    return got
