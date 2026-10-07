"""Add between: in-between shapes ("steps") that change step by step from one open line into another.

Every shape of a group carries sh["between"] = {"id": the group, "role": "first" / "last" / "step" / "key", ...}:
- the first shape keeps the group's settings in "set" = {"steps": how many in-between shapes, "graph": [[u, y]]
  (u = the step's place from the first shape to the last, y = how far it has changed: 0 = like the first, 1 = like
  the last), "rev": the last shape's points paired the other way round, "colours": each shape its own channel colour,
  taking turns over this many (0 = off)};
- steps and keys have "at" = their place along the group (0 = the first shape, 1 = the last);
- a step has "sig" = a fingerprint of how it was made: a step changed by hand (it no longer matches) becomes a "key"
  shape the steps around it change towards (user). Keys are never remade; "Not a key any more" makes one a step again.
The first and last shape (and the keys) are the user's own shapes; the steps are remade from them whenever they
change (sync_groups), keeping their place in the shape list. A group whose first or last shape is gone is unlinked:
the rest become plain shapes.

How a step is made (blend): the two shapes as Bézier curves (joined.to_bezier; a shape formula is baked in), anchors
added to the one with fewer at the other's places along it (so the look doesn't change), then every point moved
part of the way. Two lines / two polylines with as many points / two arcs stay that kind. Velocities, tumours, the
pattern formula and note pages change step by step too: a tumour / pattern only one has, or two that can't change
into each other, shrink to nothing by the middle step and the other one grows from there (user). Settings that are
only on or off switch at the middle step."""

import hashlib
import json
import math
import uuid

import numpy as np

ROLES = ("first", "last", "step", "key")
KINDS = ("line", "poly", "curve", "arc")  # the shapes that can be used (user: open lines, no freehand / funnels)
STEPS_DEFAULT = 10
MOST_STEPS = 500
STRAIGHT = [[0.0, 0.0], [1.0, 1.0]]
SETTINGS_DEFAULT = {"steps": STEPS_DEFAULT, "graph": STRAIGHT, "rev": False, "colours": 15}
COLOURS_MOST = 15
_made = {}  # remembered steps: (anchors, settings, place) -> the step


# ---------------------------------------------------------------- reading from a file

def clean_settings(s):
    s = s if isinstance(s, dict) else {}
    out = json.loads(json.dumps(SETTINGS_DEFAULT))
    try:
        out["steps"] = max(1, min(MOST_STEPS, int(s.get("steps", STEPS_DEFAULT))))
    except (TypeError, ValueError):
        pass
    try:
        g = [[min(1.0, max(0.0, float(u))), min(1.0, max(0.0, float(y)))] for u, y in s.get("graph", STRAIGHT)]
        if len(g) >= 2 and all(math.isfinite(a) for p in g for a in p):
            g[0][0], g[-1][0] = 0.0, 1.0
            g[0][1], g[-1][1] = 0.0, 1.0  # (the ends are the first and last shapes themselves)
            for a, b in zip(g, g[1:]):
                b[0] = max(b[0], a[0])
            out["graph"] = g
    except (TypeError, ValueError):
        pass
    out["rev"] = s.get("rev") is True
    try:
        out["colours"] = max(0, min(COLOURS_MOST, int(s.get("colours", 15))))
    except (TypeError, ValueError):
        pass
    return out


def clean_between(b):
    """A shape's sh["between"] from a file made valid, or None."""
    if not isinstance(b, dict) or b.get("role") not in ROLES or not isinstance(b.get("id"), str) or not b["id"]:
        return None
    out = {"id": b["id"], "role": b["role"]}
    if b["role"] == "first":
        out["set"] = clean_settings(b.get("set"))
    elif b["role"] in ("step", "key"):
        try:
            at = float(b.get("at"))
        except (TypeError, ValueError):
            return None
        if not 0 < at < 1:
            return None
        out["at"] = at
        if b["role"] == "step" and isinstance(b.get("sig"), str):
            out["sig"] = b["sig"]
    return out


# ---------------------------------------------------------------- which shapes

def closed(sh):
    pts = sh["pts"]
    if sh["kind"] == "arc":
        return _same(pts[0], pts[2])
    if sh["kind"] in ("poly", "curve"):
        return len(pts) > 2 and _same(pts[0], pts[-1])
    return False


def _same(p, q):
    return abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9


def usable(sh):
    """A shape Add between can start from: an open line, polyline, curve (joined: only one continuous piece) or arc,
    not in a group already."""
    return (sh["kind"] in KINDS and not sh.get("gaps") and not closed(sh) and "between" not in sh
            and len(sh["pts"]) >= 2)


def group_of(sh):
    return (sh.get("between") or {}).get("id")


def members(shapes, gid):
    """The numbers of the shapes in group gid."""
    return [i for i, sh in enumerate(shapes) if group_of(sh) == gid]


def ordered(shapes, gid):
    """The group's shape numbers from the first shape, through its steps / keys by place, to the last."""
    got = members(shapes, gid)
    rank = {"first": -1.0, "last": 2.0}
    return sorted(got, key=lambda i: rank.get(shapes[i]["between"]["role"], shapes[i]["between"].get("at", 0.5)))


def first_of(shapes, gid):
    return next((sh for sh in shapes if group_of(sh) == gid and sh["between"]["role"] == "first"), None)


def settings_of(shapes, gid):
    sh = first_of(shapes, gid)
    return clean_settings(sh["between"].get("set") if sh else None)


def new_id():
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- blending two shapes

def _bezier(sh):
    """(curve points, sharp anchors) of a shape (its shape formula baked in, not its pattern: that one changes as a
    setting)."""
    from notes.joined import to_bezier
    plain = {k: v for k, v in sh.items() if k != "pattern"}
    pts, sharp = to_bezier(plain, 1.0)
    return [list(map(float, p)) for p in pts], list(sharp)


def _seg_lengths(pts, n=32):
    from notes.bezier import sample, segments
    out = []
    for seg in segments(pts):
        a = np.asarray(sample(seg, n), float)
        out.append(float(np.hypot(*np.diff(a, axis=0).T).sum()))
    return out


def _fractions(pts):
    """Where each anchor is along the curve, 0 .. 1 (by length; evenly when it has none)."""
    ln = _seg_lengths(pts)
    total = sum(ln)
    if total <= 1e-12:
        n = len(ln)
        return [i / n for i in range(n + 1)]
    return [0.0] + (np.cumsum(ln) / total).tolist()


def _t_at(seg, share, n=64):
    """The curve parameter on one segment at `share` (0..1) of its length."""
    from notes.bezier import sample
    a = np.asarray(sample(seg, n), float)
    ln = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(a, axis=0).T))])
    if ln[-1] <= 1e-12:
        return share
    return float(np.interp(share * ln[-1], ln, np.linspace(0.0, 1.0, n + 1)))


def _split_at(pts, sharp, fracs, at):
    """The curve with anchors added at the places `at` (fractions along it, as in fracs = its anchors' places)."""
    from notes.bezier import split
    out, new_sharp, offset = [list(pts[0])], [], 0
    segs = len(fracs) - 1
    for s in range(segs):
        f0, f1 = fracs[s], fracs[s + 1]
        inside = [f for f in at if f0 + 1e-9 < f < f1 - 1e-9]
        seg = [list(p) for p in pts[3 * s:3 * s + 4]]
        done = 0.0  # (the part of the segment already split off, in its curve parameter)
        for f in inside:
            t = _t_at([tuple(p) for p in pts[3 * s:3 * s + 4]], (f - f0) / (f1 - f0) if f1 > f0 else 0.5)
            local = (t - done) / (1 - done) if done < 1 else 0.0
            two = split(seg, 0, min(1.0, max(0.0, local)))
            out += two[1:4]
            seg = two[3:]
            done = t
            offset += 1
        out += seg[1:]
        if s + 1 < segs and (s + 1) in sharp:
            new_sharp.append(s + 1 + offset)
    return out, new_sharp


def matched(a, b):
    """Two curves (pts, sharp) given the same number of anchors: each gets the other's at its places along it."""
    na, nb = (len(a[0]) - 1) // 3, (len(b[0]) - 1) // 3
    if na == nb:
        return a, b
    fa, fb = _fractions(a[0]), _fractions(b[0])
    a = _split_at(a[0], set(a[1]), fa, fb[1:-1])
    b = _split_at(b[0], set(b[1]), fb, fa[1:-1])
    na, nb = (len(a[0]) - 1) // 3, (len(b[0]) - 1) // 3
    if na != nb:  # (two places too close together to tell apart: matched by count instead)
        while na < nb:
            a = _split_at(a[0], set(a[1]), _fractions(a[0]), [_longest(a[0])])
            na += 1
        while nb < na:
            b = _split_at(b[0], set(b[1]), _fractions(b[0]), [_longest(b[0])])
            nb += 1
    return a, b


def _longest(pts):
    """The middle of the longest segment, as a place along the curve."""
    fr = _fractions(pts)
    s = max(range(len(fr) - 1), key=lambda i: fr[i + 1] - fr[i])
    return (fr[s] + fr[s + 1]) / 2


def _lerp_pts(p, q, f):
    return [[a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f] for a, b in zip(p, q)]


def _geometry(a, b, f, rev):
    """The step's kind and points (and sharp anchors / k)."""
    plain = not (a.get("shape") or b.get("shape"))
    pa, pb = a["pts"], b["pts"][::-1] if rev else b["pts"]
    if plain and a["kind"] == b["kind"] and len(pa) == len(pb) and a["kind"] in ("line", "poly", "arc"):
        out = {"kind": a["kind"], "pts": _lerp_pts(pa, pb, f)}
        if a["kind"] == "arc":
            out["k"] = a.get("k", 1.0) + (b.get("k", 1.0) - a.get("k", 1.0)) * f
        return out
    from notes.joined import reversed_bezier
    ca, cb = _bezier(a), _bezier(b)
    if rev:
        cb = reversed_bezier(*cb)
    ca, cb = matched(ca, cb)
    out = {"kind": "curve", "pts": _lerp_pts(ca[0], cb[0], f)}
    sharp = sorted(set(ca[1]) | set(cb[1]))
    if sharp:
        out["sharp"] = sharp
    return out


def mix(a, b, f):
    """Any two settings f of the way from a to b: numbers in between (whole numbers stay whole), the same lists /
    dicts number by number, anything else (or things that don't match) switches at the middle."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return a if f < 0.5 else b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        v = a + (b - a) * f
        return round(v) if isinstance(a, int) and isinstance(b, int) else v
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [mix(x, y, f) for x, y in zip(a, b)]
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return {k: mix(a[k], b[k], f) for k in a}
    return a if f < 0.5 else b


def _form(x):
    """x with every number as 0: two settings of the same form change into each other number by number."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return 0
    if isinstance(x, list):
        return [_form(v) for v in x]
    if isinstance(x, dict):
        return {k: _form(v) for k, v in x.items()}
    return x


def alike(a, b):
    return json.dumps(_form(a), sort_keys=True) == json.dumps(_form(b), sort_keys=True)


def _env(sh):
    from notes.envelope import velocity_env
    return [[float(u), float(v)] for u, v in velocity_env(sh)]


def _velocity(a, b, f):
    if not (a.get("vel_env") or b.get("vel_env")):
        return {"vel0": a["vel0"] + (b["vel0"] - a["vel0"]) * f, "vel1": a["vel1"] + (b["vel1"] - a["vel1"]) * f}
    from notes.envelope import env_values
    ea, eb = _env(a), _env(b)
    us = sorted({round(u, 9) for u, _ in ea + eb})
    va, vb = env_values(ea, np.array(us)), env_values(eb, np.array(us))
    env = [[u, float(x + (y - x) * f)] for u, x, y in zip(us, va, vb)]
    return {"vel0": env[0][1], "vel1": env[-1][1], "vel_env": env}


def _grown(tm, key, share):
    """A copy of a tumour / pattern setting with its size (key) times share."""
    tm = json.loads(json.dumps(tm))
    tm[key] = tm[key] * share
    return tm


def _swap(x, y, f, key):
    """x shrinking to nothing by the middle step, then y growing from there (either may be None)."""
    if f < 0.5:
        return _grown(x, key, 1 - 2 * f) if x else None
    return _grown(y, key, 2 * f - 1) if y else None


TUMOUR_SAME = ("shape", "side", "wrap", "fit", "mirror")  # tumours that differ in these can't change into each other


def _tumour(a, b, f, rev):
    from notes.joined import reversed_tumour, shown_tumour
    ta, tb = shown_tumour(a), shown_tumour(b)
    ta = ta if ta and ta.get("on", True) else None
    tb = tb if tb and tb.get("on", True) else None
    if tb and rev:
        tb = reversed_tumour(tb)
    if ta and tb and all(ta.get(k) == tb.get(k) for k in TUMOUR_SAME) and alike(ta.get("graphs"), tb.get("graphs")):
        out = mix(ta, tb, f)
        out["seed"] = ta.get("seed", 1)
        return out
    return _swap(ta, tb, f, "size")


PATTERN_SAME = ("formula", "along", "loop", "preset", "sym")


def _pattern(a, b, f, rev):
    pa, pb = a.get("pattern"), b.get("pattern")
    if pb and rev:  # (the line runs the other way: the pattern stays on the same side)
        pb = dict(pb, mirror=not pb["mirror"])
    if pa and pb and all(pa.get(k) == pb.get(k) for k in PATTERN_SAME) and alike(pa["vars"], pb["vars"]):
        return mix(pa, pb, f)
    return _swap(pa, pb, f, "scale")


def blend(a, b, f, rev=False):
    """The shape f of the way from shape a to shape b (0 = a, 1 = b; the points of b paired the other way round when
    rev). A plain shape (no "between"), cleaned like one read from a file."""
    from notes.engine import clean_shape
    out = _geometry(a, b, f, rev)
    out.update(_velocity(a, b, f))
    out["end_dot"] = a.get("end_dot", False) if f < 0.5 else b.get("end_dot", False)
    tm = _tumour(a, b, f, rev)
    if tm:
        out["tumour"] = tm
    if out["kind"] in ("line", "poly", "arc", "curve"):
        p = _pattern(a, b, f, rev)
        if p:
            out["pattern"] = p
    for key in ("glue", "fx", "cycle"):  # (only when both have the same: the numbers change step by step, user)
        x, y = a.get(key), b.get(key)
        if x is not None and y is not None and alike(x, y):
            out[key] = mix(x, y, f)
    return clean_shape(out)


# ---------------------------------------------------------------- groups

def fingerprint(sh):
    plain = {k: v for k, v in sh.items() if k != "between"}
    return hashlib.md5(json.dumps(plain, sort_keys=True).encode()).hexdigest()[:16]


def close(a, b, tol=1e-6):
    """a and b (shapes / settings) the same, numbers within tol."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k], tol) for k in a)
    return a == b


def change_at(graph, u):
    """How far a step at place u has changed from the first shape (0) to the last (1)."""
    return float(np.interp(u, [p[0] for p in graph], [p[1] for p in graph]))


def anchors(shapes, gid):
    """[(place, shape)]: the first shape, the keys by place, the last shape."""
    got = [(0.0 if shapes[i]["between"]["role"] == "first" else 1.0 if shapes[i]["between"]["role"] == "last"
            else shapes[i]["between"]["at"], shapes[i]) for i in ordered(shapes, gid)
           if shapes[i]["between"]["role"] != "step"]
    return got


def step_at(anchor_list, at, s):
    """The step at place `at` between the anchors around it."""
    lo = max((p for p in anchor_list if p[0] <= at), key=lambda p: p[0])
    hi = min((p for p in anchor_list if p[0] >= at and p is not lo), key=lambda p: p[0], default=lo)
    graph = s["graph"]
    y, y0, y1 = change_at(graph, at), change_at(graph, lo[0]), change_at(graph, hi[0])
    if hi is lo:
        f = 0.0
    elif abs(y1 - y0) > 1e-12:
        f = (y - y0) / (y1 - y0)
    else:
        f = (at - lo[0]) / (hi[0] - lo[0])
    rev = s["rev"] and lo[1]["between"]["role"] == "first" and hi[1]["between"]["role"] == "last"
    # (keys are the user's own: Reverse pairs only the first shape with the last, a key's points go as they are)
    return made(lo[1], hi[1], f, rev)


def made(a, b, f, rev):
    """blend, remembered (dragging an end remakes every step at every mouse move)."""
    key = json.dumps([{k: v for k, v in a.items() if k != "between"}, {k: v for k, v in b.items() if k != "between"},
                      round(f, 12), rev], sort_keys=True)
    got = _made.get(key)
    if got is None:
        if len(_made) > 4000:
            _made.clear()
        got = _made[key] = blend(a, b, f, rev)
    return json.loads(json.dumps(got))


def unlink(shapes, gid):
    """The group's shapes become plain shapes."""
    for sh in shapes:
        if group_of(sh) == gid:
            del sh["between"]


def sync_groups(shapes):
    """Every group's steps remade from its first / last shape and keys, in place (the shapes keep their numbers). A
    step changed by hand becomes a key; a group missing its first or last shape is unlinked. True if anything
    changed."""
    changed = False
    gids = []
    for sh in shapes:
        g = group_of(sh)
        if g and g not in gids:
            gids.append(g)
    for gid in gids:
        roles = [sh["between"]["role"] for sh in shapes if group_of(sh) == gid]
        if roles.count("first") != 1 or roles.count("last") != 1:
            unlink(shapes, gid)
            changed = True
            continue
        s = settings_of(shapes, gid)
        steps = [shapes[i] for i in ordered(shapes, gid) if shapes[i]["between"]["role"] == "step"]
        marks = anchors(shapes, gid)
        for sh in steps:  # changed by hand since it was made (not by moving the whole group): a key now
            b = sh["between"]
            if b.get("sig") and b["sig"] != fingerprint(sh):
                want = step_at(marks, b["at"], s)
                if not close({k: v for k, v in sh.items() if k != "between"}, want):
                    b["role"] = "key"
                    b.pop("sig", None)
                    changed = True
        marks = anchors(shapes, gid)
        for sh in steps:
            b = sh["between"]
            if b["role"] != "step":
                continue
            new = step_at(marks, b["at"], s)
            sig = fingerprint(new)
            if b.get("sig") == sig and fingerprint(sh) == sig:
                continue
            sh.clear()
            sh.update(new)
            sh["between"] = {"id": gid, "role": "step", "at": b["at"], "sig": sig}
            changed = True
    return changed


def slots(n):
    """The places of n steps, from the first shape (0) to the last (1)."""
    return [i / (n + 1) for i in range(1, n + 1)]


def rebuild(shapes, gid, s):
    """The group's steps made again for settings s (put on its first shape): keys stay at their places, the steps
    go in every other slot. Returns the new shape list (the steps right after the first shape) and the numbers of the
    group's shapes in it."""
    first = first_of(shapes, gid)
    first["between"]["set"] = clean_settings(s)
    s = first["between"]["set"]
    n = s["steps"]
    taken = {min(n, max(1, round(sh["between"]["at"] * (n + 1)))) for sh in shapes
             if group_of(sh) == gid and sh["between"]["role"] == "key"}
    out = [sh for sh in shapes if not (group_of(sh) == gid and sh["between"]["role"] == "step")]
    at = next(i for i, sh in enumerate(out) if sh is first) + 1
    new = [{"kind": "line", "pts": [[0, 0], [1, 1]],
            "between": {"id": gid, "role": "step", "at": u}} for i, u in enumerate(slots(n), 1) if i not in taken]
    out[at:at] = new
    sync_groups(out)
    return out, ordered(out, gid)


def start_group(shapes, i, j):
    """Shapes i and j become the first and last shape of a new group (the one starting earlier first; Reverse on
    when pairing the ends the other way round travels less). Returns (new shape list, the group's id)."""
    a, b = shapes[i], shapes[j]
    if min(p[0] for p in b["pts"]) < min(p[0] for p in a["pts"]):
        a, b = b, a
    s = clean_settings(None)
    s["rev"] = ends_cross(a, b)
    gid = new_id()
    a["between"] = {"id": gid, "role": "first", "set": s}
    b["between"] = {"id": gid, "role": "last"}
    return rebuild(shapes, gid, s)[0], gid


def ends_cross(a, b):
    """True when a's start is nearer b's end than b's start (pairing them the other way round travels less)."""
    a0, a1, b0, b1 = a["pts"][0], a["pts"][-1], b["pts"][0], b["pts"][-1]
    return math.dist(a0, b1) + math.dist(a1, b0) < math.dist(a0, b0) + math.dist(a1, b1) - 1e-9


def turns(shapes):
    """Each shape's (group, colour turn) when its group gives each shape its own colour, else None."""
    out = [None] * len(shapes)
    done = set()
    for sh in shapes:
        gid = group_of(sh)
        if not gid or gid in done:
            continue
        done.add(gid)
        n = settings_of(shapes, gid)["colours"]
        if n:
            for k, i in enumerate(ordered(shapes, gid)):
                out[i] = (gid, k % n)
    return out


def copied(copies):
    """Copies of shapes (pasted / duplicated): a group copied with its first and last shape stays a group (a new
    one); otherwise the copies are plain shapes (user)."""
    gids = {group_of(sh) for sh in copies} - {None}
    for gid in gids:
        roles = [sh["between"]["role"] for sh in copies if group_of(sh) == gid]
        if "first" in roles and "last" in roles:
            fresh = new_id()
            for sh in copies:
                if group_of(sh) == gid:
                    sh["between"]["id"] = fresh
        else:
            for sh in copies:
                if group_of(sh) == gid:
                    del sh["between"]
