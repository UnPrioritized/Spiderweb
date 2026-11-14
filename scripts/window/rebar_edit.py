"""Editable visual time signature changes (PLAN.md 2026-11-14, user: "not baked in" after OK): each change made by
rebar_work.apply keeps a RECORD of what it replaced, so it can later be taken back exactly, edited (other numbers)
or moved (other bars), each one undo step. No window here (stage C draws them, stage D drags them).

app.rebar = Rebar((changes, groups)), never changed in place (undo steps hold it by reference, slot 18 of
sel_state; saved in the project as {"changes": [...], "groups": [...]}).
- A CHANGE: {"id", "seq" (the order the changes were made in: an edited one is made again = the newest), "sig",
  "dense", "between", "ppq", "pl" (its plan as numbers: where it is now, in beats; its bars are worked out from them
  each time (bars_now): a time signature edited before it renumbers the bars, never its beats),
  "tempo" ({list: {"m0", "m1", "s1", "pb"}}: the tempo line's points / catch-ups / signatures / key signatures it
  replaced (m0) and made (m1); the rest of the line is the same before and after, moved), "link" (its link in
  app.loaded_map or None), "del" ([[box now, box before]]: Delete loaded notes' boxes it moved other than along)}.
- A GROUP: shapes a change made out of others: {"recs": [change ids, oldest first], "before": [the shapes as they
  were before any of them], "base": the plain notes shape they were cut from (None: a drawn shape stretched, not
  cut), "whole": a paste kept as one shape, "made": [the shapes now in the project]}. A shape reaching over the bars
  of two changes (a song-long paste) is ONE group of both: taking one back makes the others' work again from the
  base (replay, bars in order: changes on other bars give the same notes in either order).
- Everything after a change's bars just moves: taken back = moved back (not recorded, so a song of shapes after
  the bars doesn't double the project file); shapes added after the bars later move back too.
- Overlapping changes: refused (user 2026-11-14: two never overlap).
- TOUCHING CHANGES (bars right next to each other) share the bar line between them: the newer one replaced the
  older one's tempo point / signature there. So a change is taken back the way undo would: the newer changes
  touching it (in a row) taken back first, newest first, then made again on their bars (rebuild).
Making permanent (stage B): bake() forgets a change (and the ones sharing a shape with it)."""

import bisect
import copy
import json
import math

import numpy as np

from notes import loaded_map, meter
from notes import rebar as R
from notes import tempo as T

EPS = 1e-9
LISTS = ("points", "catch", "sigs", "keysigs")


class Rebar(tuple):
    """(changes, groups): app.rebar. A tuple, never changed in place."""

    @property
    def changes(self):
        return self[0] if self else ()

    @property
    def groups(self):
        return self[1] if self else ()


EMPTY = Rebar(((), ()))


def of(app):
    got = getattr(app, "rebar", None)
    return got if isinstance(got, Rebar) else EMPTY


def change_of(app, cid):
    return next((c for c in of(app).changes if c["id"] == cid), None)


def changes(app):
    """The editable changes, in bar order."""
    return sorted(of(app).changes, key=lambda c: c["pl"]["a"])


def seq(c):
    return c.get("seq", c["id"])


def bars_now(app, c, sigs=None):
    """[first, last] bar (from 0) a change is on now: from its beats (each old bar stayed one bar, so taking changes
    back / making them again never renumbers bars; a time signature edited before it does)."""
    sigs = meter.sigs_of(app.tempo) if sigs is None else sigs
    first = meter.where(sigs, c["pl"]["a"] + EPS)[0]
    return [first, first + c["pl"]["count"] - 1]


def touching(x, y):
    """Two changes on bars right next to each other (one's end bar line = the other's start)."""
    return abs(x["pl"]["new_b"] - y["pl"]["a"]) <= 1e-6 or abs(y["pl"]["new_b"] - x["pl"]["a"]) <= 1e-6


def linked(rb, c):
    """The changes newer than c touching it, or touching one of those (in a row), oldest first: they replaced c's
    bar-line items, so they are taken back before it and made again after."""
    out, todo = [], [c]
    while todo:
        x = todo.pop()
        for y in rb.changes:
            if y is not c and seq(y) > seq(c) and all(y is not z for z in out) and touching(x, y):
                out.append(y)
                todo.append(y)
    return sorted(out, key=seq)


# ------------------------------------------------------------------ the plan as numbers (JSON) and moved along

def plan_data(pl):
    """What of a plan its record keeps (where it is, its runs, ticks placed by sound / kept apart)."""
    dm = pl.get("dense_map")
    snd = pl.get("sound")
    return {"a": pl["a"], "b": pl["b"], "new_b": pl["new_b"], "shift": pl["shift"],
            "runs": [list(r) for r in pl["runs"]], "bars": list(pl["bars"]), "count": pl["count"],
            "sig": list(pl["sig"]), "factors": list(pl.get("factors") or []),
            "sound_spans": [[int(a), int(b)] for a, b in pl.get("sound_spans") or ()],
            "sound": [[[int(t), int(u)] for t, u in side] for side in snd] if snd else None,
            "dense_map": [np.asarray(dm[0], np.int64).tolist(), np.asarray(dm[1], np.int64).tolist()] if dm else None}


def plan_of(d):
    """plan_data back as a plan map_ticks / cut_mapped can use."""
    pl = {k: copy.deepcopy(v) for k, v in d.items() if k not in ("dense_map", "sound", "runs", "sound_spans")}
    pl["runs"] = [tuple(r) for r in d["runs"]]
    pl["sound_spans"] = [tuple(s) for s in d.get("sound_spans") or ()]
    if d.get("sound"):
        pl["sound"] = tuple([tuple(x) for x in side] for side in d["sound"])
    dm = d.get("dense_map")
    pl["dense_map"] = (np.array(dm[0], np.int64), np.array(dm[1], np.int64)) if dm else None
    return pl


def slid_plan(d, db, dt):
    """plan_data moved db beats / dt ticks along."""
    out = dict(d)
    for k in ("a", "b", "new_b"):
        out[k] = d[k] + db
    out["runs"] = [[r[0] + db, r[1] + db, r[2] + db, r[3] + db, r[4]] for r in d["runs"]]
    out["sound_spans"] = [[a + dt, b + dt] for a, b in d.get("sound_spans") or ()]
    if d.get("sound"):
        out["sound"] = [[[t + dt, u] for t, u in side] for side in d["sound"]]
        for side in out["sound"]:  # (the tempo before the first entry: the first one's, as heard_tempo starts at 0)
            if side and side[0][0] > 0:
                side.insert(0, [0, side[0][1]])
    if d.get("dense_map"):
        out["dense_map"] = [[t + dt for t in d["dense_map"][0]], [t + dt for t in d["dense_map"][1]]]
    return out


def shift_t(d, ppq):
    return round(d["new_b"] * ppq) - round(d["b"] * ppq)


# ------------------------------------------------------------------ shapes moved along / back

def _boxes_shift(sh, dt):
    """sh with its Extract boxes (merge parts' too) moved dt ticks; sh itself when it holds none."""
    from notes.loaded import extract_groups
    new = None
    if sh.get("extract") is not None and dt:
        groups = extract_groups(sh["extract"])
        moved = [dict(g, boxes=[[b[0] + dt, b[1] + dt, b[2], b[3]] for b in g["boxes"]]) for g in groups]
        new = dict(sh)
        new["extract"] = moved[0] if isinstance(sh["extract"], dict) and len(moved) == 1 else moved
    for k in ("merge", "unmerge"):
        m = sh.get(k)
        if isinstance(m, dict) and isinstance(m.get("parts"), list):
            parts = [_boxes_shift(p, dt) if isinstance(p, dict) else p for p in m["parts"]]
            if any(a is not b for a, b in zip(parts, m["parts"])):
                new = new if new is not None else dict(sh)
                new[k] = dict(m, parts=parts)
    return sh if new is None else new


def slide(sh, db, dt):
    """A shape after a change's bars as the change moves it: rebar_work.moved + its Extract boxes along."""
    from window.rebar_work import moved
    return _boxes_shift(moved(sh, db), dt)


def _back_beat(y, d):
    """The beat x with round(x + d, 9) + 0.0 == y (moved's own rounding), nearest to y - d."""
    x = round(y - d, 9) + 0.0
    if round(x + d, 9) + 0.0 == y:
        return x
    for k in (1, -1, 2, -2):
        c = round(x + k * 1e-9, 9) + 0.0
        if round(c + d, 9) + 0.0 == y:
            return c
    return x


def slide_back(sh, db, dt, ppq=None):
    """slide's way back: every point back where it was (moved rounds to 9 places: the beat that rounds to it). A
    plain notes shape on whole ticks (a piece cut at another change's edges: tick / PPQ, not 9 places) = on the
    tick dt before, as a piece cut there is made (else its notes are no longer "only moved")."""
    new = copy.deepcopy(sh)
    if ppq and "notes" in sh and dt and all(abs(b * ppq - round(b * ppq)) <= 1e-6 for b, _ in new["pts"]):
        new["pts"] = [[(round(b * ppq) - dt) / ppq, p] for b, p in new["pts"]]
    else:
        new["pts"] = [[_back_beat(b, db), p] for b, p in new["pts"]]
    return _boxes_shift(new, -dt)


def plain(x):
    """Lists for tuples (a shape after JSON / an undo step), strings shared."""
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    return x


def same(a, b):
    """The same shape (one may have been through JSON / a project file: floats trimmed like a save)."""
    if a is b:
        return True
    if a.keys() != b.keys() or a.get("kind") != b.get("kind"):
        return False
    if plain(a) == plain(b):
        return True
    from files.project import short_shape
    return short_shape(a) == short_shape(b)


def find(shapes, sh, taken=()):
    """The index of sh in shapes (itself, else the same one), None."""
    for i, s in enumerate(shapes):
        if s is sh and i not in taken:
            return i
    p0 = sh.get("pts")[0] if sh.get("pts") else None
    for i, s in enumerate(shapes):
        if i in taken:
            continue
        q = s.get("pts")[0] if s.get("pts") else None
        if (p0 is None) != (q is None) or p0 is not None and (abs(p0[0] - q[0]) > 1e-6 or abs(p0[1] - q[1]) > 1e-6):
            continue
        if same(s, sh):
            return i
    return None


# ------------------------------------------------------------------ the tempo line's pieces

def _beat(kind, item):
    return item[0]


def _fwd(kind, item, pl):
    """An item after a change's bars as tempo_of moves it (map_beat; signatures + shift)."""
    it = copy.deepcopy(item)
    if kind == "sigs":
        it[0] = item[0] + pl["shift"]
        return it
    it[0] = R.map_beat(pl, item[0])
    if kind == "points":
        for q in T.heard_line(it):
            q[0] = R.map_beat(pl, q[0])
    elif kind == "catch":
        for k in (3, 4):
            v = T.catch_get(it, k)
            if v is not None:
                T.catch_set(it, k, R.map_beat(pl, v))
    return it


def _inv(y, f, guess):
    """x with f(x) == y, nearest to guess (a few steps of the last digit), else guess."""
    if f(guess) == y:
        return guess
    up = dn = guess
    for _ in range(4):
        up, dn = math.nextafter(up, math.inf), math.nextafter(dn, -math.inf)
        if f(up) == y:
            return up
        if f(dn) == y:
            return dn
    return guess


def _unmap(pl, y):
    """map_beat's way back, about (_inv finds the exact beat): before the stretch the same, after it moved back, inside
    it through its run (a ramp's step a touching change held just before its edge lies in there)."""
    if y < pl["a"] - EPS:
        return y
    if y >= pl["new_b"] - EPS:
        return pl["b"] + (y - pl["new_b"])
    for oa, ob, na, nb, f in pl["runs"]:
        if y < nb - EPS:
            return oa + (y - na) / f
    return pl["b"]


def _back(kind, item, pl):
    """_fwd's way back (the very beat that moves onto it, when there is one). A new item (only what holds beats
    copied deeply: a long line's points one by one were the slow part)."""
    it = list(item)
    if kind == "points" and T.heard_of(it):
        it[7] = copy.deepcopy(it[7])
    if kind == "sigs":
        d = pl["shift"]
        it[0] = _inv(item[0], lambda x: x + d, item[0] - d)
        return it

    def one(y):
        return _inv(y, lambda x: R.map_beat(pl, x), _unmap(pl, y))
    it[0] = one(item[0])
    if kind == "points":
        for q in T.heard_line(it):
            q[0] = one(q[0])
    elif kind == "catch":
        for k in (3, 4):
            v = T.catch_get(it, k)
            if v is not None:
                T.catch_set(it, k, one(v))
    return it


def _round(x):
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, (list, tuple)):
        return [_round(v) for v in x]
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    return x


def _close(a, b):
    """The same item, floats within a millionth (moved along by other changes; read back from a file)."""
    return a == b or _round(plain(a)) == _round(plain(b))


def tempo_pieces(old, new, pl):
    """{list: {"m0", "m1", "s1", "pb"}}: of each tempo list, the part the change replaced (m0) by what (m1); before
    it the lists are the same, after it new = old moved (exactly: _back of new = old). s1: the first item after it
    (where m1 goes when it's empty), pb: the last item before it's beat."""
    out = {}
    for kind in LISTS:
        a, b = list(old.get(kind) or []), list(new.get(kind) or [])
        p = 0
        while p < len(a) and p < len(b) and a[p] == b[p]:
            p += 1
        s = 0
        while s < len(a) - p and s < len(b) - p and _back(kind, b[len(b) - 1 - s], pl) == a[len(a) - 1 - s]:
            s += 1
        out[kind] = {"m0": copy.deepcopy(a[p:len(a) - s]), "m1": copy.deepcopy(b[p:len(b) - s]),
                     "s1": copy.deepcopy(b[len(b) - s]) if s else None,
                     "pb": _beat(kind, b[p - 1]) if p else None}
    return out


def _locate(kind, items, piece, pl):
    """Where piece's m1 is in items now: its index, or None (changed since)."""
    m1, s1 = piece["m1"], piece["s1"]
    anchor = m1[0] if m1 else s1
    beats = [_beat(kind, x) for x in items]
    if anchor is None:  # (nothing made, nothing after: right after the last item before it)
        pb = piece["pb"]
        return 0 if pb is None else bisect.bisect_right(beats, pb + 1e-7)
    at = _beat(kind, anchor)
    i = bisect.bisect_left(beats, at - 1e-6)
    while i < len(items) and beats[i] <= at + 1e-6:
        if _close(items[i], anchor) and all(_close(x, y) for x, y in zip(items[i:i + len(m1)], m1)) \
                and len(items) - i >= len(m1):
            return i
        i += 1
    return None


def tempo_back(tempo, pieces, pl):
    """The tempo line with a change taken back (a new dict), or None when a piece isn't there any more."""
    out = dict(tempo)
    for kind in LISTS:
        piece = pieces.get(kind)
        if piece is None:
            continue
        items = list(tempo.get(kind) or [])
        i = _locate(kind, items, piece, pl)
        if i is None:
            return None
        rest = [_back(kind, x, pl) for x in items[i + len(piece["m1"]):]]
        out[kind] = items[:i] + copy.deepcopy(piece["m0"]) + rest
    return out


def tempo_slid(pieces, pl, fwd=True):
    """A change's tempo pieces moved by another change before it (fwd: made; else taken back)."""
    out = {}
    for kind, p in pieces.items():
        f = (lambda x: _fwd(kind, x, pl)) if fwd else (lambda x: _back(kind, x, pl))
        out[kind] = {"m0": [f(x) for x in p["m0"]], "m1": [f(x) for x in p["m1"]],
                     "s1": f(p["s1"]) if p["s1"] is not None else None,
                     "pb": p["pb"] if p["pb"] is None else _slid_beat(kind, p["pb"], pl, fwd)}
    return out


def _slid_beat(kind, x, pl, fwd):
    return _fwd(kind, [x], pl)[0] if fwd else _back(kind, [x], pl)[0]


# ------------------------------------------------------------------ the loaded MIDI's link taken out of the chain

def _start(n, f):
    """A link's first moving tick (its stretch's start) in the ticks it is given."""
    gd, fd = np.diff(n), np.diff(f)
    bad = np.flatnonzero(gd != fd)
    return int(n[bad[0]]) if len(bad) else int(n[-1])


def link_out(saved, link):
    """The links with one change's link taken out: later links over ticks after it are moved back by what it
    added (they were made on its moved ticks). Returns (new list, {old text: new link}) or None (not there)."""
    k = next((i for i, ln in enumerate(saved or []) if ln["n"] == link["n"] and ln["f"] == link["f"]), None)
    if k is None:
        return None
    chain = loaded_map.links(saved)
    xn, xf = chain[k]
    s = _start(xn, xf)
    d = int(xf[-1]) - int(xn[-1])
    out, renamed = list(saved[:k]), {}
    for ln, (n, f) in zip(saved[k + 1:], chain[k + 1:]):
        if _start(n, f) >= s:  # (after it: its anchors there moved back)
            on = n >= s
            nn, ff = n.copy(), f.copy()
            nn[on] -= d
            ff[on] -= d
            new = loaded_map.make_link(nn, ff)
            renamed[ln["n"] + "|" + ln["f"]] = new
            out.append(new)
        else:  # (before it: it moves where the change starts)
            s = int(loaded_map.apply_link(n, f, s))
            out.append(ln)
    return out, renamed


# ------------------------------------------------------------------ made / slid / forgotten with the project

def overlap(app, pl, but=None):
    """A change already on these bars (pl's old stretch, beats now): {"why": "overlap", "at"} (user 2026-11-14: two
    never overlap), else None. but: a change id left out (the one being moved)."""
    for c in of(app).changes:
        if c["id"] != but and c["pl"]["a"] < pl["b"] - 1e-6 and pl["a"] < c["pl"]["new_b"] - 1e-6:
            return {"why": "overlap", "at": c["pl"]["a"]}
    return None


def _after(c, end):
    """Change c lies after beat end (a change's end)."""
    return c["pl"]["a"] >= end - 1e-6


def _group(recs, before, base, whole, made):
    """A group; "base_same": its base is its one shape before (a paste only moved): saved once, not twice."""
    same_base = base is not None and len(before) == 1 and same(
        {k: v for k, v in base.items() if k != "rebar_whole"}, before[0])
    return {"recs": list(recs), "before": list(before), "base": base, "whole": bool(whole), "made": list(made),
            "base_same": same_base}


def _slid_change(c, d, ppq, fwd=True):
    """Another change after this one's bars, moved with them (fwd) or back."""
    dt = shift_t(d, ppq)
    db = d["shift"]
    out = dict(c)
    out["pl"] = slid_plan(c["pl"], db if fwd else -db, dt if fwd else -dt)
    out["tempo"] = tempo_slid(c["tempo"], plan_of(d), fwd)
    sgn = 1 if fwd else -1
    out["del"] = [[[a[0] + sgn * dt, a[1] + sgn * dt, a[2], a[3]], [b[0] + sgn * dt, b[1] + sgn * dt, b[2], b[3]]]
                  for a, b in c.get("del") or ()]
    return out


def record(app, pl, kinds, old, owners, bases, old_tempo, link, old_deleted, dense, between):
    """After rebar_work._apply made the change on the project: its record added to app.rebar, the others after its
    bars moved along, groups it touched joined. old = the shapes before, owners = for each shape added at the end
    its old index; bases = {old index: the plain notes shape it was cut from}."""
    from window.rebar_work import moved
    ppq = app.ppq
    d = plan_data(pl)
    dt = shift_t(d, ppq)
    rb = of(app)
    cid = max((c["id"] for c in rb.changes), default=0) + 1
    n = len(old)
    outs = {i: [app.shapes[i]] for i in range(n)}
    for k, i in enumerate(owners):
        outs[i].append(app.shapes[n + k])
    status = {}  # (old index: "same" / "moved" (only along, the way taking back moves it back) / "changed")
    for i in range(n):
        if len(outs[i]) == 1 and outs[i][0] is old[i]:
            status[i] = "same"
        elif kinds.get(i) == "after" and len(outs[i]) == 1 and same(outs[i][0], slide(old[i], pl["shift"], dt)):
            status[i] = "moved"
        else:
            status[i] = "changed"
    found = []  # (each group: its shapes' old indexes (None: not in the project any more), changed by this one)
    for g in rb.groups:
        idx = [find(old, m) for m in g["made"]]
        st = [status[i] for i in idx] if all(i is not None for i in idx) else None
        found.append((idx, st, st is not None and not all(s == "same" for s in st)
                      and not all(s == "moved" for s in st)))
    # (a drawn shape (no base) a change stretched, changed by this one: that change becomes permanent, with every
    # change sharing a group with it; this one keeps the shapes as they are now as its own "before")
    bake = {r for g, (idx, st, chg) in zip(rb.groups, found) if chg and g["base"] is None for r in g["recs"]}
    while bake:
        more = {r for g in rb.groups if bake & set(g["recs"]) for r in g["recs"]} - bake
        if not more:
            break
        bake |= more
    groups, claimed = [], set()
    for g, (idx, st, chg) in zip(rb.groups, found):
        if st is None:  # (not in the project any more: an edit Stage B makes permanent)
            groups.append(g)
            continue
        claimed.update(idx)
        made = [s for i in idx for s in outs[i]]
        if bake & set(g["recs"]):
            if chg:  # (those are permanent: this change's own group, from the shapes as they were just now)
                groups.append(_group([cid], [old[i] for i in idx], None, False, made))
            continue  # (else: forgotten with them)
        if not chg:
            if all(s == "same" for s in st):
                groups.append(g)
            else:  # (wholly after the bars: its shapes as they were move along too)
                groups.append(dict(_group(g["recs"], [slide(s, pl["shift"], dt) for s in g["before"]],
                                          g["base"] and moved(g["base"], pl["shift"]), g.get("whole"), made)))
        else:  # (cut again: made again from the base by both when one is taken back)
            groups.append(dict(g, recs=list(g["recs"]) + [cid], made=made))
    for i in range(n):
        if i in claimed or status[i] != "changed":
            continue
        base = bases.get(i)
        groups.append(_group([cid], [old[i]], base, base and base.get("rebar_whole"), outs[i]))
    chs = [_slid_change(c, d, ppq) if _after(c, pl["b"]) else c for c in rb.changes]
    pieces = tempo_pieces(old_tempo, app.tempo, pl)
    dels = []
    for go, gn in zip(old_deleted or [], app.deleted or []):
        for bo, bn in zip(go.get("boxes") or [], gn.get("boxes") or []):
            if list(bn) != list(bo) and not (bo[0] >= round(pl["b"] * ppq) and list(bn) == [bo[0] + dt, bo[1] + dt,
                                                                                           bo[2], bo[3]]):
                dels.append([list(bn), list(bo)])
    chs.append({"id": cid, "seq": max((seq(c) for c in rb.changes), default=0) + 1, "sig": list(pl["sig"]),
                "dense": bool(dense), "between": bool(between), "ppq": ppq, "pl": d, "tempo": pieces, "link": link,
                "del": dels})
    app.rebar = Rebar((tuple(chs), tuple(groups)))
    if bake:
        bake_ids(app, bake)
    return cid


def bake_ids(app, ids):
    """Changes made permanent (forgotten: their work stays), with every change sharing a group with them."""
    rb = of(app)
    ids = set(ids)
    while True:
        more = {r for g in rb.groups if ids & set(g["recs"]) for r in g["recs"]} - ids
        if not more:
            break
        ids |= more
    chs = tuple(c for c in rb.changes if c["id"] not in ids)
    groups = tuple(g for g in rb.groups if not ids & set(g["recs"]))
    app.rebar = Rebar((chs, groups))
    return ids


def bake(app, cid):
    """Make a change permanent (stage B). The ids made permanent."""
    return bake_ids(app, {cid})


# ------------------------------------------------------------------ taking a change back

def _plain_rows(sh, ppq):
    from window.rebar_work import _abs_rows
    got = _abs_rows(sh, ppq)
    return None if got is None else got[0]


def replay(base, plans, whole, ppq):
    """base cut by these changes (bar order) the way rebar_work.apply does a plain notes shape: wholly before = as
    it is, wholly after = moved along, else cut at the edges (a paste kept whole: one shape, its notes moved). None
    when it can't be (not plain notes only moved)."""
    from window.rebar_work import cut_mapped, moved
    pieces = [base]
    for pl in plans:
        out = []
        a_t, b_t = round(pl["a"] * ppq), round(pl["b"] * ppq)
        for p in pieces:
            rows = _plain_rows(p, ppq)
            if rows is None:
                return None
            if not len(rows) or int(rows[:, 1].max()) <= a_t:
                out.append(p)
            elif int(rows[:, 0].min()) >= b_t:
                out.append(moved(p, pl["shift"]))
            else:
                out += cut_mapped(p, pl, ppq, whole=whole)
        pieces = out
    return [{k: v for k, v in p.items() if k != "rebar_whole"} for p in pieces]


def take_back(app, cid):
    """The project with change cid taken back, as {"shapes", "tempo", "loaded_map", "deleted", "rebar", "playhead"}
    (nothing changed yet), or {"why": code}: "gone" (no such change), "ppq" (made at another PPQ), "edited" (what it
    made isn't there as made: Stage B makes that permanent first)."""
    rb = of(app)
    c = next((x for x in rb.changes if x["id"] == cid), None)
    if c is None:
        return {"why": "gone"}
    ppq = app.ppq
    if c["ppq"] != ppq:
        return {"why": "ppq"}
    if app.notes_late:
        app.shapes_changed(now=True)
    d = c["pl"]
    pl = plan_of(d)
    dt = shift_t(d, ppq)
    db = d["shift"]
    nb_t = round(d["new_b"] * ppq)
    # (the others after it: back first, so a group's replay places them where they are without this one)
    chs = [_slid_change(x, d, ppq, fwd=False) if _after(x, d["new_b"]) else x for x in rb.changes if x is not c]
    plans = {x["id"]: plan_of(x["pl"]) for x in chs}
    shapes = list(app.shapes)
    taken, puts, groups = set(), {}, []
    for g in rb.groups:
        idx = []
        for m in g["made"]:
            i = find(shapes, m, taken)
            if i is None:
                if cid in g["recs"]:
                    return {"why": "edited"}
                break
            idx.append(i)
            taken.add(i)
        else:
            if cid in g["recs"]:
                left = [r for r in g["recs"] if r != cid]
                if not left:
                    new = list(g["before"])
                else:
                    order = sorted(left, key=lambda r: next(x["pl"]["a"] for x in chs if x["id"] == r))
                    new = replay(g["base"], [plans[r] for r in order], g.get("whole", False), ppq)
                    if new is None:
                        return {"why": "edited"}
                    groups.append(dict(g, recs=left, made=new))
                puts[idx[0]] = new
                for i in idx[1:]:
                    puts[i] = []
                continue
            if all(_starts(app, shapes[i], ppq) >= nb_t for i in idx):  # (wholly after it: back along)
                made = [slide_back(shapes[i], db, dt, ppq) for i in idx]
                for i, s in zip(idx, made):
                    puts[i] = [s]
                groups.append(_group(g["recs"], [slide_back(s, db, dt, ppq) for s in g["before"]],
                                     g["base"] and slide_back(g["base"], db, dt, ppq), g.get("whole"), made))
            else:
                groups.append(g)
            continue
        for i in idx:
            taken.discard(i)
        groups.append(g)
    r = np.asarray(app.rendered, np.int64)
    for i, sh in enumerate(shapes):  # (everything after the bars, shapes made since too: back along)
        if i in puts or i in taken:
            continue
        if _starts(app, sh, ppq, r, i) >= nb_t:
            puts[i] = [slide_back(sh, db, dt, ppq)]
    new_shapes = []
    for i, sh in enumerate(shapes):
        new_shapes += puts.get(i, [sh])
    tempo = tempo_back(app.tempo, c["tempo"], pl)
    if tempo is None:
        return {"why": "edited"}
    lmap = list(app.loaded_map or [])
    if c.get("link"):
        got = link_out(lmap, c["link"])
        if got is not None:
            lmap, renamed = got
            chs = [dict(x, link=renamed.get(x["link"]["n"] + "|" + x["link"]["f"], x["link"])) if x.get("link")
                   else x for x in chs]
    pairs = {json.dumps(a): b for a, b in c.get("del") or ()}
    deleted = []
    for g in app.deleted or []:
        boxes = []
        for b in g.get("boxes") or []:
            key = json.dumps(list(b))
            if key in pairs:
                boxes.append(list(pairs[key]))
            elif b[0] >= nb_t:
                boxes.append([b[0] - dt, b[1] - dt, b[2], b[3]])
            else:
                boxes.append(list(b))
        deleted.append(dict(g, boxes=boxes))
    ph = app.playhead
    if ph >= d["new_b"] - EPS:
        ph = ph - db
    elif ph > d["a"]:
        ph = d["a"] + (ph - d["a"]) * (d["b"] - d["a"]) / max(EPS, d["new_b"] - d["a"])
    return {"shapes": new_shapes, "tempo": tempo, "loaded_map": lmap, "deleted": deleted,
            "rebar": Rebar((tuple(chs), tuple(groups))), "playhead": max(0.0, ph), "change": c}


def _starts(app, sh, ppq, r=None, i=None):
    """The first tick a shape's notes start on (as heard and its own), else its first point's."""
    own = np.asarray(app.notes_of(sh), np.int64).reshape(-1, 4)[:, 0]
    if r is not None and i is not None:
        heard = r[r[:, 5] == i, 0]
        own = np.concatenate([own, heard])
    if len(own):
        return int(own.min())
    bs = [b for b, _ in sh.get("pts") or ()]
    return round(min(bs) * ppq) if bs else -1


def put(app, got):
    """take_back's project put in (no undo step: the caller pushed it)."""
    from notes.tempo import changed as tempo_changed_now
    from window import track_tempo
    track_tempo.visual(app)
    app.roll.end_typing()
    app.roll.cancel_draft()
    app.shapes[:] = got["shapes"]
    app.tempo.clear()
    app.tempo.update(got["tempo"])
    tempo_changed_now()
    if got["loaded_map"] != app.loaded_map:
        app.loaded_map = got["loaded_map"]
    if got["deleted"] != app.deleted:
        app.deleted = got["deleted"]
    app.rebar = got["rebar"]
    app.select_many([], None)
    app.shapes_changed(now=True)
    app.playhead = got["playhead"]
    app.roll.show_playhead()
    app.tempo_changed()
    app.meter_changed()


def remove(app, cid, name):
    """Remove a change: everything as it was before it (one undo step). {"ok": True} or {"why"}."""
    c = change_of(app, cid)
    if c is not None and linked(of(app), c):  # (newer touching ones: taken back first, made again after)
        return _rebuild(app, cid, name)
    got = take_back(app, cid)
    if "why" in got:
        return got
    app.push_undo(name=name)
    put(app, got)
    return {"ok": True}


def bars_beats(sigs, first, last):
    """(lo, hi) beats of bars first..last (from 0)."""
    return meter.bar_start(sigs, first), meter.bar_start(sigs, last + 1)


def redo_as(app, cid, name, bars=None, sig=None, dense=None, between=None, dry=False):
    """Change cid taken back and made again on other bars and / or with another signature (one undo step):
    rebar_work.apply's answer, or {"why"} (a refusal: nothing changed, no step). dry: a test run (the box's OK
    first), everything put back."""
    from window import rebar_work as W
    c = change_of(app, cid)
    if c is None:
        return {"why": "gone"}
    sigs = meter.sigs_of(app.tempo)
    now = bars_now(app, c, sigs)
    new = (list(bars) if bars else now, list(sig) if sig else list(c["sig"]),
           c["dense"] if dense is None else dense, c["between"] if between is None else between)
    if new[0] == now and new[1] == list(c["sig"]):  # (the numbers it has: nothing would change)
        return {"why": "same", "at": c["pl"]["a"]}
    return _rebuild(app, cid, name, new, dry)


def _same_lengths(sigs, pl):
    """The bars are that long already (the box's own check, conductor.rebar_same)."""
    return all(abs(r[4] - 1) < 1e-12 for r in pl["runs"]) and all(
        meter.at_beat(sigs, s)[1:] == pl["sig"] for s, _ in R.old_bars(sigs, pl["a"], pl["b"]))


def _rebuild(app, cid, name, new=None, dry=False):
    """Change cid taken back (new None) or made again as new = (bars, sig, dense, between), one undo step. The newer
    changes touching it (linked) are taken back first, newest first (undo's order: each one's record was made on
    the line the newer ones then changed), and made again on their bars afterwards, oldest first, keeping their ids
    (bar numbers stay: one old bar = one new bar). {"ok"} / apply's answer, or {"why"}: nothing changed, no step.
    dry: a test run, everything put back (its marks too)."""
    from window import rebar_work as W
    rb = of(app)
    c = change_of(app, cid)
    if c is None:
        return {"why": "gone"}
    sigs = meter.sigs_of(app.tempo)
    later = linked(rb, c)
    specs = [(x["id"], bars_now(app, x, sigs), list(x["sig"]), x["dense"], x["between"]) for x in later]
    if new is not None:
        specs.append((cid, *new))
    pane = app.tempo_pane
    kept = (list(app.shapes), json.loads(json.dumps(app.tempo)), list(app.loaded_map or []), app.deleted, rb,
            app.playhead, app.sels, app.sel, getattr(pane, "_rebar", None))
    if not dry:
        app.push_undo(name=name)

    def back(out):  # (a refusal / a test run: all as it was, the step pushed gone)
        from notes.tempo import changed as tempo_changed_now
        from window.history import undo_state
        app.shapes[:] = kept[0]
        app.tempo.clear()
        app.tempo.update(kept[1])
        tempo_changed_now()
        app.loaded_map = kept[2]
        app.deleted = kept[3]
        app.rebar = kept[4]
        app.playhead = kept[5]
        app.select_many(sorted(kept[6]), kept[7])
        app.shapes_changed(now=True)
        app.tempo_changed()
        app.meter_changed()
        pane._rebar = kept[8]
        pane.request_redraw()
        if not dry:
            app.drop_empty_step(undo_state(app.shapes))
            app.sync_history()
        return out

    for x in sorted(later + [c], key=seq, reverse=True):
        got = take_back(app, x["id"])
        if "why" in got:
            return back(got)
        put(app, got)
    out = {"ok": True}
    for sid, (first, last), (top, bottom), dense, between in specs:
        sigs = meter.sigs_of(app.tempo)
        lo, hi = bars_beats(sigs, first, last)
        pl = R.plan(sigs, lo, hi, top, bottom)
        if sid == cid and _same_lengths(sigs, pl):  # (edited back to what the bars were: refused like the box)
            return back({"why": "same", "at": pl["a"]})
        got = W.apply(app, pl, name, dense=dense, between=between, push=False)
        if "why" in got:
            return back(got)
        if got["id"] != sid:  # (the same change made again: it keeps its id)
            rb, made = of(app), got["id"]
            app.rebar = Rebar((tuple(dict(x, id=sid) if x["id"] == made else x for x in rb.changes),
                               tuple(dict(g, recs=[sid if r == made else r for r in g["recs"]]) for g in rb.groups)))
            got["id"] = sid
        if sid == cid:
            out = got
    if new is None and later:  # (the marks of the ones made again aren't this step's news)
        pane._rebar = None
        pane.request_redraw()
    if dry:
        return back(out)
    return out


def edit(app, cid, top, bottom, name, dense=None, between=None, dry=False):
    """Change cid with another signature on the same bars (one undo step)."""
    return redo_as(app, cid, name, sig=(top, bottom), dense=dense, between=between, dry=dry)


def move(app, cid, first, last, name, dry=False):
    """Change cid moved to bars first..last (from 0, as numbered now; the same before and after any change: one old
    bar = one new bar), same signature (one undo step). Another change there: refused."""
    c = change_of(app, cid)
    if c is None:
        return {"why": "gone"}
    sigs = meter.sigs_of(app.tempo)
    lo, hi = bars_beats(sigs, first, last)
    clash = overlap(app, {"a": lo, "b": hi}, but=cid)
    if clash:
        return clash
    return redo_as(app, cid, name, bars=(first, last), dry=dry)


# ------------------------------------------------------------------ made permanent by an edit inside (stage B)
# (user 2026-11-14: editing notes / shapes / the tempo INSIDE a change, or drawing a new shape there, makes it
# permanent after a heads-up; its tick lasts until Spiderweb restarts; Cancel = the edit doesn't happen. Checked
# ONCE per new undo step, after the mouse lets go: the step's state before vs now)

def keep_clash(app, spans):
    """The first bar (from 1) of an editable change that one of spans [(first beat, end beat)] (Keep timing parts)
    reaches into, else None (user 2026-11-14: Keep timing and a change never share bars)."""
    for c in changes(app):
        a, b = c["pl"]["a"], c["pl"]["new_b"]
        if any(lo < b - 1e-9 and hi > a + 1e-9 for lo, hi in spans):
            return bars_now(app, c)[0] + 1
    return None


def _shape_key(sh):
    """A shape as text, its big texts (pasted notes) as their string's identity: the same in an undo step's copy
    (load_state shares the very strings) and in the project."""
    from window.history import _big_texts
    places = _big_texts(sh)
    texts = [d[k] for d, k in places]
    for (d, k), t in zip(places, texts):
        d[k] = "\x00big" + str(id(t))
    try:
        return json.dumps(sh, sort_keys=True)
    finally:
        for (d, k), t in zip(places, texts):
            d[k] = t


def _shape_span(app, sh, ppq):
    """(first, last) tick a shape reaches: its notes, its points, its Extract boxes. None: nothing."""
    lo, hi = [], []
    try:
        notes = np.asarray(app.notes_of(sh), np.int64).reshape(-1, 4)
    except Exception:  # (a shape the program can't make: its points still say where it is)
        notes = np.zeros((0, 4), np.int64)
    if len(notes):
        lo.append(int(notes[:, 0].min()))
        hi.append(int(notes[:, 1].max()))
    bs = [p[0] for p in sh.get("pts") or () if isinstance(p, (list, tuple)) and p]
    if bs:
        lo.append(math.floor(min(bs) * ppq))
        hi.append(math.ceil(max(bs) * ppq))
    if sh.get("extract") is not None:
        from notes.loaded import extract_groups
        for g in extract_groups(sh["extract"]):
            for b in g.get("boxes") or ():
                lo.append(int(b[0]))
                hi.append(int(b[1]))
    return (min(lo), max(hi)) if lo else None


def _tempo_beats(kind, item):
    out = [item[0]]
    if kind == "points":
        out += [q[0] for q in T.heard_line(item)]
    elif kind == "catch":
        for k in (3, 4):
            v = T.catch_get(item, k)
            if v is not None:
                out.append(v)
    return out


def _tempo_edits(before, now):
    """The beats of the tempo line's items an edit changed / added / removed (each list: the same start and end
    left out, what's between them counted)."""
    out = []
    for kind in LISTS:
        a, b = list(before.get(kind) or []), list(now.get(kind) or [])
        if a == b:
            continue
        p = 0
        while p < len(a) and p < len(b) and a[p] == b[p]:
            p += 1
        s = 0
        while s < len(a) - p and s < len(b) - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
            s += 1
        for x in a[p:len(a) - s] + b[p:len(b) - s]:
            out += _tempo_beats(kind, x)
    return out


def inside_ids(app, step):
    """The editable changes the newest edit (undo step `step` = the project before it) touched: something in their
    bars changed (shapes / notes, a new shape there, tempo points, catch-ups, signatures, Delete loaded notes'
    boxes), or a shape one of them made (a piece of a cut paste, anywhere: its record would be stale). Set of ids."""
    from window.history import as_state, load_state, undo_state
    from notes.tempo import snap_line, tempo_snap
    rb = of(app)
    if not rb.changes or app.step_rebar(step) is not rb:  # (no changes, or the step made / took back one itself)
        return set()
    ppq = app.ppq
    spans = [(c["id"], c["pl"]["a"], c["pl"]["new_b"]) for c in rb.changes]
    hit = set()

    def beats_in(xs):
        for cid, a, b in spans:
            if any(a - 1e-6 <= x <= b + 1e-6 for x in xs):
                hit.add(cid)

    def ticks_in(lo, hi):  # (a shape / box reaching from tick lo to hi)
        for cid, a, b in spans:
            if hi > round(a * ppq) and lo < round(b * ppq):
                hit.add(cid)
    was, now = app.step_tempo(step), tempo_snap(app.tempo)
    if was != now:  # (both read back the same way: items compared as the step keeps them)
        beats_in(_tempo_edits(snap_line(was), snap_line(now)))
    if app.step_deleted(step) != json.dumps(app.deleted):
        from notes.loaded import extract_groups
        old = {json.dumps(list(b)) for g in extract_groups(json.loads(app.step_deleted(step))) for b in g.get("boxes")
               or ()}
        new = {json.dumps(list(b)) for g in app.deleted or () for b in g.get("boxes") or ()}
        for t in old ^ new:
            b = json.loads(t)
            ticks_in(b[0], b[1])
    before = as_state(step[0], app.shapes)
    if before != undo_state(app.shapes):
        from collections import Counter
        old = load_state(before)
        ko, kn = [_shape_key(s) for s in old], [_shape_key(s) for s in app.shapes]
        gone, came = Counter(ko) - Counter(kn), Counter(kn) - Counter(ko)
        changed = set(gone) | set(came)
        for g in rb.groups:  # (a shape a change made, edited anywhere: that change)
            if any(m is not GONE and _shape_key(m) in changed for m in g["made"]):
                hit.update(g["recs"])
        for keys, shapes, extra in ((ko, old, gone), (kn, app.shapes, came)):
            left = dict(extra)
            for k, sh in zip(keys, shapes):
                if left.get(k):
                    left[k] -= 1
                    span = _shape_span(app, sh, ppq)
                    if span is not None:
                        ticks_in(*span)
    return hit


def busy(app):
    """The mouse holds something (a drag, a box being drawn, a held number box), or a pop-up holds the window: the
    check waits for it to end (asked once, after the edit, never mid-drag)."""
    tl = getattr(app, "track_list", None)
    sc = getattr(app, "_scrub", None)
    return bool(app.roll.drag or getattr(app.tempo_pane, "drag", None) or app.vel.edit or app.held_scrub
                or (sc and sc.get("active")) or (tl is not None and app.tracks_shown() and tl.busy())
                or app.grabbed_elsewhere())


def bars_text(app, ids):
    """"bar 5" / "bars 5–6, bar 9": where these changes are now."""
    from files.lang import tr
    sigs = meter.sigs_of(app.tempo)
    out = []
    for c in changes(app):
        if c["id"] in ids:
            first, last = bars_now(app, c, sigs)
            out.append(tr("conductor.rebar_bars_at_one", first=first + 1) if first == last else
                       tr("conductor.rebar_bars_at", first=first + 1, last=last + 1))
    return ", ".join(out)


def check_edit(app):
    """After an edit (App.rebar_watch, at idle): the changes it touched made permanent after a heads-up; Cancel =
    the edit taken back (not kept for redo). Changes made at another PPQ: permanent at once (their ticks no longer
    fit; Undo brings them back with the old PPQ)."""
    rb = of(app)
    if not rb.changes:
        return
    odd = {c["id"] for c in rb.changes if c["ppq"] != app.ppq}
    if odd:
        bake_ids(app, odd)
        rb = of(app)
    step = app.last_step()
    if step is None or step is getattr(app, "_rebar_seen", None):
        return
    if busy(app):
        app.rebar_watch(later=True)
        return
    app._rebar_seen = step
    ids = inside_ids(app, step)
    if not ids:
        return
    from files.lang import tr
    ids = _sharing(rb, ids)
    where = bars_text(app, ids)
    many = len(ids) > 1
    if not getattr(app, "rebar_bake_skip", False):
        from window import big_ask
        q = tr("conductor.rebar_bake" if many else "conductor.rebar_bake_one", bars=where)
        go, skip = big_ask.dialog(app, tr("big_ask.title"), q + "\n\n" + tr("conductor.rebar_bake_text"), False,
                                  tick=tr("conductor.rebar_bake_tick"), yes=tr("conductor.rebar_bake_yes"),
                                  no=tr("conductor.rebar_bake_no"))
        if not go:
            undo_edit(app)
            return
        if skip:
            app.rebar_bake_skip = True  # (this session only, user 2026-11-14: back after a restart)
    bake_ids(app, ids)
    app.tempo_pane.request_redraw()
    app.status.config(text=tr("conductor.rebar_baked" if many else "conductor.rebar_baked_one", bars=where))


def _sharing(rb, ids):
    """ids with every change sharing a group with them (made permanent together, bake_ids)."""
    ids = set(ids)
    while True:
        more = {r for g in rb.groups if ids & set(g["recs"]) for r in g["recs"]} - ids
        if not more:
            return ids
        ids |= more


def undo_edit(app):
    """The newest step taken back and forgotten (not kept for redo): the steps undone before it and the oldest ones
    the 300 limit dropped come back (App.take_back_scrub's way)."""
    if not app.undo_stack:
        return
    kept, trimmed = getattr(app, "_redo_kept", None), getattr(app, "_trimmed", None)
    at = len(app.undo_stack)
    app._restore(app.undo_stack, app.redo_stack)
    app.redo_stack.pop()
    if kept and kept[1] == at:
        app.redo_stack[:] = kept[0]
    if trimmed:
        app.undo_stack[:0] = trimmed
    app._edit_key = app._scrub = None
    app._rebar_seen = app.last_step()
    app.sync_history()


# ------------------------------------------------------------------ saved in the project

GONE ={"kind": "_gone"}  # (a group's shape not found in the project when read back: that change can't be taken back)


def _key(sh):
    """A shape's kind + first point: checks a saved place still holds it (a damaged shape left out shifts them)."""
    p = (sh.get("pts") or [[None, None]])[0]
    return [sh.get("kind"), None if p[0] is None else round(p[0], 4), None if p[1] is None else round(p[1], 4)]


def saved(app):
    """app.rebar as the project file keeps it (None: nothing). A group's made shapes ARE the project's: saved as
    their places (+ a check), and its base only when it isn't its one shape before (a paste: saved once, not 3x)."""
    rb = of(app)
    if not rb.changes:
        return None
    pos = {id(s): i for i, s in enumerate(app.shapes)}
    groups = []
    for g in rb.groups:
        at = []
        for m in g["made"]:
            i = pos.get(id(m))
            if i is None:
                i = find(app.shapes, m)
            at.append(-1 if i is None else i)
        out = {"recs": list(g["recs"]), "before": plain(g["before"]), "whole": bool(g.get("whole")),
               "made_at": at, "made_key": [_key(m) for m in g["made"]]}
        if g["base"] is not None:
            out["base"] = "before" if g.get("base_same") else plain(g["base"])
        groups.append(out)
    return {"changes": [plain(c) for c in rb.changes], "groups": groups}


def loaded(data, shapes=()):
    """A project's "rebar" read back -> Rebar (anything damaged: none of it, the changes stay as they are). shapes:
    the project's, read just before (the groups' made shapes are places in it)."""
    from notes.engine import clean_shape
    try:
        if not isinstance(data, dict):
            return EMPTY
        chs = []
        for c in data.get("changes") or []:
            d = c["pl"]
            plan_of(d)
            assert isinstance(c["id"], int) and len(c["sig"]) == 2
            assert all(k in c["tempo"] for k in LISTS)
            chs.append(c)
        groups = []
        for g in data.get("groups") or []:
            before = [clean_shape(s) for s in g["before"]]
            if any(s is None for s in before):
                return EMPTY
            whole = bool(g.get("whole"))
            base = g.get("base")
            if base == "before":
                base = dict(before[0], rebar_whole=True) if whole else dict(before[0])
            elif base:
                base = clean_shape(base)
                if base is None:
                    return EMPTY
                if whole:
                    base["rebar_whole"] = True
            else:
                base = None
            made = []
            for i, k in zip(g["made_at"], g["made_key"]):
                ok = isinstance(i, int) and 0 <= i < len(shapes) and _key(shapes[i]) == k
                made.append(shapes[i] if ok else GONE)
            groups.append(_group([int(r) for r in g["recs"]], before, base, whole, made))
        return Rebar((tuple(chs), tuple(groups)))
    except Exception:
        return EMPTY
