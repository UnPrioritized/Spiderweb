"""Editable visual time signature changes (PLAN.md 2026-11-14, user: "not baked in" after OK): each change made by
rebar_work.apply keeps a RECORD of what it replaced, so it can later be taken back exactly, edited (other numbers)
or moved (other bars), each one undo step. No window here (stage C draws them, stage D drags them).

app.rebar = Rebar((changes, groups)), never changed in place (undo steps hold it by reference, slot 18 of
sel_state; saved in the project as {"changes": [...], "groups": [...]}).
- A CHANGE: {"id", "bars": [first, last] (bar numbers from 0: each old bar stays ONE bar, so its bar numbers never
  change, whatever other changes do), "sig", "dense", "between", "ppq", "pl" (its plan as numbers: where it is now),
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
    return sorted(of(app).changes, key=lambda c: c["bars"][0])


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
        return _inv(y, lambda x: R.map_beat(pl, x), pl["b"] + (y - pl["new_b"]))
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

def overlap(app, pl):
    """A change already on these bars: {"why": "overlap", "at"} (user 2026-11-14: two never overlap), else None."""
    lo, hi = pl["bars"]
    for c in of(app).changes:
        if c["bars"][0] <= hi and lo <= c["bars"][1]:
            return {"why": "overlap", "at": c["pl"]["a"]}
    return None


def _after(c, bars):
    return c["bars"][0] > bars[1]


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
    groups, claimed, bake = [], set(), set()
    for g in rb.groups:
        idx = [find(old, m) for m in g["made"]]
        if any(i is None for i in idx):  # (not in the project any more: an edit Stage B makes permanent)
            groups.append(g)
            continue
        claimed.update(idx)
        st = [status[i] for i in idx]
        made = [s for i in idx for s in outs[i]]
        if all(s == "same" for s in st):
            groups.append(g)
        elif all(s == "moved" for s in st):  # (wholly after the bars: its shapes as they were move along too)
            groups.append(dict(g, made=made, before=[slide(s, pl["shift"], dt) for s in g["before"]],
                               base=g["base"] and moved(g["base"], pl["shift"])))
        elif g["base"] is not None:  # (cut again: made again from the base by both when one is taken back)
            groups.append(dict(g, recs=list(g["recs"]) + [cid], made=made))
        else:  # (a drawn shape a change stretched, changed by this one: that one becomes permanent)
            bake.update(g["recs"])
            groups.append(dict(g, recs=list(g["recs"]) + [cid], made=made, gone=True))
    for i in range(n):
        if i in claimed or status[i] != "changed":
            continue
        base = bases.get(i)
        groups.append({"recs": [cid], "before": [old[i]], "base": base, "whole": bool(base and base.get("rebar_whole")),
                       "made": outs[i]})
    chs = [_slid_change(c, d, ppq) if _after(c, pl["bars"]) else c for c in rb.changes]
    pieces = tempo_pieces(old_tempo, app.tempo, pl)
    dels = []
    for go, gn in zip(old_deleted or [], app.deleted or []):
        for bo, bn in zip(go.get("boxes") or [], gn.get("boxes") or []):
            if list(bn) != list(bo) and not (bo[0] >= round(pl["b"] * ppq) and list(bn) == [bo[0] + dt, bo[1] + dt,
                                                                                           bo[2], bo[3]]):
                dels.append([list(bn), list(bo)])
    chs.append({"id": cid, "bars": list(pl["bars"]), "sig": list(pl["sig"]), "dense": bool(dense),
                "between": bool(between), "ppq": ppq, "pl": d, "tempo": pieces, "link": link, "del": dels})
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
    groups = tuple(g for g in rb.groups if not ids & set(g["recs"]) and not g.get("gone"))
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
    chs = [_slid_change(x, d, ppq, fwd=False) if _after(x, c["bars"]) else x for x in rb.changes if x is not c]
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
                    order = sorted(left, key=lambda r: next(x["bars"][0] for x in chs if x["id"] == r))
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
                groups.append(dict(g, made=made, before=[slide_back(s, db, dt, ppq) for s in g["before"]],
                                   base=g["base"] and slide_back(g["base"], db, dt, ppq)))
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
    got = take_back(app, cid)
    if "why" in got:
        return got
    first, last = bars or c["bars"]
    top, bottom = sig or c["sig"]
    dense = c["dense"] if dense is None else dense
    between = c["between"] if between is None else between
    kept = (list(app.shapes), json.loads(json.dumps(app.tempo)), list(app.loaded_map or []), app.deleted, of(app),
            app.playhead, app.sels, app.sel)
    if not dry:
        app.push_undo(name=name)
    put(app, got)
    sigs = meter.sigs_of(app.tempo)
    lo, hi = bars_beats(sigs, first, last)
    pl = R.plan(sigs, lo, hi, top, bottom)
    out = W.apply(app, pl, name, dense=dense, between=between, push=False)
    if "id" in out and out["id"] != cid:  # (the same change, edited: it keeps its id)
        rb, new = of(app), out["id"]
        app.rebar = Rebar((tuple(dict(c, id=cid) if c["id"] == new else c for c in rb.changes),
                           tuple(dict(g, recs=[cid if r == new else r for r in g["recs"]]) for g in rb.groups)))
        out["id"] = cid
    if dry or "why" in out:  # (a refusal / a test run: all as it was, the step it pushed gone)
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
        if not dry:
            app.drop_empty_step(undo_state(app.shapes))
            app.sync_history()
    return out


def edit(app, cid, top, bottom, name, dense=None, between=None, dry=False):
    """Change cid with another signature on the same bars (one undo step)."""
    return redo_as(app, cid, name, sig=(top, bottom), dense=dense, between=between, dry=dry)


def move(app, cid, first, last, name, dry=False):
    """Change cid moved to bars first..last (from 0; the same before and after any change: one old bar = one new
    bar), same signature (one undo step). Another change there: refused."""
    rb = of(app)
    for x in rb.changes:
        if x["id"] != cid and x["bars"][0] <= last and first <= x["bars"][1]:
            return {"why": "overlap", "at": x["pl"]["a"]}
    return redo_as(app, cid, name, bars=(first, last), dry=dry)


# ------------------------------------------------------------------ saved in the project

def saved(app):
    """app.rebar as the project file keeps it (None: nothing)."""
    rb = of(app)
    if not rb.changes:
        return None
    return {"changes": [plain(c) for c in rb.changes], "groups": [plain(g) for g in rb.groups]}


def loaded(data):
    """A project's "rebar" read back -> Rebar (anything damaged: none of it, the changes stay as they are)."""
    from notes.engine import clean_shape
    try:
        if not isinstance(data, dict):
            return EMPTY
        chs = []
        for c in data.get("changes") or []:
            d = c["pl"]
            plan_of(d)
            assert isinstance(c["id"], int) and len(c["bars"]) == 2 and len(c["sig"]) == 2
            assert all(k in c["tempo"] for k in LISTS)
            chs.append(c)
        groups = []
        for g in data.get("groups") or []:
            sh = lambda s: clean_shape(s)
            groups.append({"recs": [int(r) for r in g["recs"]], "before": [sh(s) for s in g["before"]],
                           "base": sh(g["base"]) if g.get("base") else None, "whole": bool(g.get("whole")),
                           "made": [sh(s) for s in g["made"]]})
            if any(s is None for s in groups[-1]["before"] + groups[-1]["made"]):
                return EMPTY
        return Rebar((tuple(chs), tuple(groups)))
    except Exception:
        return EMPTY
