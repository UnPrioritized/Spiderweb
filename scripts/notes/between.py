"""Add between: in-between shapes ("steps") that change step by step from one open line into another.

Every shape of a group carries sh["between"] = {"id": the group, "role": "first" / "last" / "step" / "key", ...}:
- the first shape keeps the group's settings in "set" = {"steps": how many in-between shapes, "graph": [[u, y]]
  (u = the step's place from the first shape to the last, y = how far it has changed: 0 = like the first, 1 = like
  the last), "rev": the last shape's points paired the other way round, "colours": each shape its own channel colour,
  taking turns over this many (0 = off), "smooth": keys gone through without a corner (Steps)};
- the first, last shape and keys may have "push" = [x, y]: which way and how hard the steps leave it (push_of);
- steps and keys have "at" = their place along the group (0 = the first shape, 1 = the last);
- a step has "sig" = a fingerprint of how it was made: a step changed by hand (it no longer matches) becomes a "key"
  shape the steps around it change towards (user). Keys are never remade; "Not a key any more" makes one a step again.
  A step whose velocity alone was changed stays a step with its own velocity, "vel" = {vel0, vel1, vel_env?} (user).
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
_made = {}  # remembered steps: (the pair, f) -> (the step, its fingerprint)


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
    out["smooth"] = s.get("smooth") is True  # (off for groups saved before it: they keep their look)
    return out


def clean_between(b):
    """A shape's sh["between"] from a file made valid, or None."""
    if not isinstance(b, dict) or b.get("role") not in ROLES or not isinstance(b.get("id"), str) or not b["id"]:
        return None
    out = {"id": b["id"], "role": b["role"]}
    if b["role"] != "step" and _clean_push(b.get("push")):
        out["push"] = _clean_push(b["push"])
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
        if b["role"] == "step" and isinstance(b.get("vel"), dict) and _clean_vel(b["vel"]):
            out["vel"] = _clean_vel(b["vel"])
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


class Pair:
    """Two shapes ready to be blended: their points matched ONCE, then each step only moves them part of the way
    (matching long curves for every step made 500 steps take half a minute)."""

    def __init__(self, a, b, rev):
        self.a, self.b, self.rev = a, b, rev
        plain = not (a.get("shape") or b.get("shape"))
        pa, pb = a["pts"], b["pts"][::-1] if rev else b["pts"]
        if plain and a["kind"] == b["kind"] and len(pa) == len(pb) and a["kind"] in ("line", "poly", "arc"):
            self.kind, self.sharp = a["kind"], []
        else:
            from notes.joined import reversed_bezier
            ca, cb = _bezier(a), _bezier(b)
            if rev:
                cb = reversed_bezier(*cb)
            ca, cb = matched(ca, cb)
            pa, pb = ca[0], cb[0]
            self.kind, self.sharp = "curve", sorted(set(ca[1]) | set(cb[1]))
        self.pa = np.asarray(pa, float).reshape(-1, 2)
        self.d = np.asarray(pb, float).reshape(-1, 2) - self.pa

    def geometry(self, f):
        """The step's kind and points (and sharp anchors / k)."""
        a, b = self.a, self.b
        out = {"kind": self.kind, "pts": (self.pa + self.d * f).tolist()}
        if self.kind == "arc":
            out["k"] = a.get("k", 1.0) + (b.get("k", 1.0) - a.get("k", 1.0)) * f
        if self.sharp:
            out["sharp"] = list(self.sharp)
        return out

    def at(self, f):
        """The shape f of the way from a to b (see blend)."""
        from notes.engine import clean_shape
        a, b, rev = self.a, self.b, self.rev
        out = self.geometry(f)
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


PATTERN_SAME = ("formula", "along", "loop", "preset", "sym", "mirror")
# (on the other side: shrinks to nothing, then grows on the other side, like a tumour: user)


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
    return Pair(a, b, rev).at(f)


# ---------------------------------------------------------------- groups

def _plain(sh):
    return {k: v for k, v in sh.items() if k != "between"}


def fingerprint(sh):
    return hashlib.md5(json.dumps(_plain(sh), sort_keys=True).encode()).hexdigest()[:16]


VEL_KEYS = ("vel0", "vel1", "vel_env")


def _no_vel(sh):
    return {k: v for k, v in sh.items() if k not in VEL_KEYS and k != "between"}


def with_vel(step, vel):
    """A made step with a step's own velocity (one changed by hand, e.g. a velocity line drawn over the group: it
    stays a step, user)."""
    out = {k: v for k, v in step.items() if k != "vel_env"}
    out.update(json.loads(json.dumps(vel)))
    return out


def _clean_vel(v):
    from notes.engine import clean_basics
    try:
        out = {k: x for k, x in clean_basics(v).items() if k in ("vel0", "vel1")}
        if v.get("vel_env"):
            out["vel_env"] = [[float(u), max(1.0, min(127.0, float(y)))] for u, y in v["vel_env"]]
        return out
    except (TypeError, ValueError, AttributeError):
        return None


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


PUSH_REACH = 4.0  # a full push (pad at its edge) = the path leaving this many times the group's size per whole group
PUSH_FLOOR = (1.0, 12.0)  # (the group's size counts as at least 1 beat wide and 12 keys high: a flat group can still
# be pushed up / down)


def push_of(sh):
    """A shape's push [x, y] (each -1..1, at most 1 long; [0, 0] = none): which way and how hard the steps leave it."""
    return _clean_push((sh.get("between") or {}).get("push")) or [0.0, 0.0]


def _clean_push(p):
    """A push from a file made valid, or None when there is none."""
    try:
        x, y = float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)) or math.hypot(x, y) < 1e-9:
        return None
    n = math.hypot(x, y)
    return [x / n, y / n] if n > 1 else [x, y]


def _h10(f):
    return f * f * f - 2 * f * f + f


def _h11(f):
    return f * f * f - f * f


class Steps:
    """A group's steps as its anchors (first shape, keys, last shape) and settings make them now: each pair of
    anchors written out once, not once per step (it was slow with many steps of long curves).

    Push (user): each anchor can push the steps a way (push_of); the steps then travel on a curve, not straight. The
    curve of every point is a Hermite curve whose speed at an anchor is its straight speed plus the push, so the
    pushes only move each step as a whole (a shift, worked out once per step): no pushes = exactly the straight
    steps. A key with a push (or every key, with "Smooth through keys" on) is gone through without a corner: the
    speeds on its two sides are made the same (their average) before the push is added."""

    def __init__(self, shapes, gid, s):
        self.marks, self.s, self.keys = anchors(shapes, gid), s, {}
        self.bends = self._bends()

    def _part(self, i):
        """Anchors i and i + 1: (the pair's key for made, the part's length in the graph's change)."""
        (u0, a), (u1, b) = self.marks[i], self.marks[i + 1]
        rev = self.s["rev"] and b["between"]["role"] == "last"
        # (a key runs the way the first shape does, like the steps it was made from: Reverse pairs the last shape
        # the other way round with the first shape AND with a key)
        k = (id(a), id(b), rev)
        if k not in self.keys:
            self.keys[k] = json.dumps([_plain(a), _plain(b), bool(rev)], sort_keys=True)
        graph = self.s["graph"]
        dy = change_at(graph, u1) - change_at(graph, u0)
        return self.keys[k], (dy if abs(dy) > 1e-12 else u1 - u0)

    def _bends(self):
        """Each part's (extra speed leaving its first anchor, extra speed arriving at its last) as [x, y], or None
        when no part bends (the steps straight, as before pushes)."""
        marks = self.marks
        pushes = [push_of(sh) for _, sh in marks]
        smooth = self.s.get("smooth") and len(marks) > 2
        if not smooth and not any(p != [0.0, 0.0] for p in pushes):
            return None
        pts = np.asarray([p for _, sh in marks for p in sh["pts"]], float).reshape(-1, 2)
        size = np.maximum(pts.max(0) - pts.min(0), PUSH_FLOOR) * PUSH_REACH
        push = [np.asarray(p) * size for p in pushes]
        parts = [self._part(i) for i in range(len(marks) - 1)]
        speeds = []  # (each part's straight speed of the shape as a whole: its middle's move per change)
        for key, d in parts:
            pair = _pair(key)
            speeds.append(pair.d.mean(0) / d if abs(d) > 1e-12 else np.zeros(2))
        out = [[push[i].copy(), push[i + 1].copy()] for i in range(len(parts))]
        for j in range(1, len(marks) - 1):  # (the keys)
            if not (smooth or pushes[j] != [0.0, 0.0]):
                continue
            mid = (speeds[j - 1] + speeds[j]) / 2
            out[j - 1][1] += mid - speeds[j - 1]
            out[j][0] += mid - speeds[j]
        return out

    def at(self, at):
        """(the step at place `at` between the anchors around it, its fingerprint): remembered, don't change it."""
        marks = self.marks
        lo = min(max([i for i in range(len(marks)) if marks[i][0] <= at] or [0]), len(marks) - 2)
        u0, u1 = marks[lo][0], marks[lo + 1][0]
        if u1 - u0 <= 1e-12:
            return made(self._part(lo)[0], 0.0)
        graph = self.s["graph"]
        y, y0, y1 = change_at(graph, at), change_at(graph, u0), change_at(graph, u1)
        f = (y - y0) / (y1 - y0) if abs(y1 - y0) > 1e-12 else (at - u0) / (u1 - u0)
        key, d = self._part(lo)
        step, sig = made(key, f)
        if self.bends is None:
            return step, sig
        e0, e1 = self.bends[lo]
        off = d * (_h10(f) * e0 + _h11(f) * e1)
        if not off.any():
            return step, sig
        step = dict(step, pts=(np.asarray(step["pts"], float) + off).tolist())
        return step, fingerprint(step)


_pairs = {}  # remembered Pairs: their two shapes + Reverse as JSON -> Pair


def _pair(key):
    pair = _pairs.get(key)
    if pair is None:
        if len(_pairs) > 64:
            _pairs.clear()
        a, b, rev = json.loads(key)
        pair = _pairs[key] = Pair(a, b, rev)
    return pair


def made(key, f):
    """(blend, its fingerprint) of the pair `key` (Steps.at) at f, remembered (dragging an end remakes every step at
    every mouse move)."""
    k = (key, round(f, 12))
    got = _made.get(k)
    if got is None:
        pair = _pair(key)
        if len(_made) > 4000:
            _made.clear()
        step = pair.at(f)
        got = _made[k] = (step, fingerprint(step))
    return got


def unlink(shapes, gid):
    """The group's shapes become plain shapes."""
    for sh in shapes:
        if group_of(sh) == gid:
            del sh["between"]


def sync_groups(shapes):
    """Every group's steps remade from its first / last shape and keys, in place (the shapes keep their numbers). A
    step changed by hand becomes a key (only its velocity changed: it keeps that velocity and stays a step); a
    group missing its first or last shape is unlinked. True if anything changed."""
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
        now = Steps(shapes, gid, s)
        prints = [fingerprint(sh) for sh in steps]
        keyed = False
        for sh, sig in zip(steps, prints):  # changed by hand since it was made (not by moving the whole group)
            b = sh["between"]
            if not b.get("sig") or b["sig"] == sig:
                continue
            want = now.at(b["at"])[0]
            if b.get("vel"):
                want = with_vel(want, b["vel"])
            mine = _plain(sh)
            if close(mine, want):
                continue
            if close(_no_vel(mine), _no_vel(want)):  # (only its velocity: a step with a velocity of its own, user)
                b["vel"] = {k: sh[k] for k in VEL_KEYS if k in sh}
                continue
            b["role"] = "key"
            for k in ("sig", "vel"):
                b.pop(k, None)
            changed = keyed = True
        if keyed:
            now = Steps(shapes, gid, s)
        for sh, old in zip(steps, prints):
            b = sh["between"]
            if b["role"] != "step":
                continue
            new, sig = now.at(b["at"])
            if b.get("vel"):
                new = with_vel(new, b["vel"])
                sig = fingerprint(new)
            if b.get("sig") == sig and old == sig:
                continue
            sh.clear()
            sh.update(json.loads(json.dumps(new)))
            sh["between"] = dict({"id": gid, "role": "step", "at": b["at"], "sig": sig},
                                 **({"vel": b["vel"]} if b.get("vel") else {}))
            changed = True
    return changed


def slots(n):
    """The places of n steps, from the first shape (0) to the last (1)."""
    return [i / (n + 1) for i in range(1, n + 1)]


def places(n, keys):
    """The places of the steps when n in-between shapes are wanted and some are keys (at their own places, in
    keys): the parts between the keys share the steps by their length, each part's steps evenly spread (user)."""
    ks = sorted(k for k in keys if 0 < k < 1)
    free = max(0, n - len(ks))
    edges = [0.0] + ks + [1.0]
    want = [free * (b - a) for a, b in zip(edges, edges[1:])]
    got = [math.floor(w + 1e-9) for w in want]
    for i in sorted(range(len(got)), key=lambda i: (got[i] - want[i], i))[:free - sum(got)]:
        got[i] += 1
    return [a + (b - a) * j / (c + 1) for a, b, c in zip(edges, edges[1:], got) for j in range(1, c + 1)]


def rebuild(shapes, gid, s, remake=None):
    """Settings s put on the group (on its first shape) and its steps made again: only remade from scratch when
    the step count changed (or remake): keys stay at their places, the steps spread between them (places);
    otherwise the steps there are stay (a deleted one stays gone, user). Returns the new shape list (new steps right
    after the first shape) and the numbers of the group's shapes in it."""
    first = first_of(shapes, gid)
    old = clean_settings(first["between"].get("set"))["steps"]
    first["between"]["set"] = clean_settings(s)
    s = first["between"]["set"]
    if remake is None:
        remake = s["steps"] != old
    if not remake:
        out = list(shapes)
        sync_groups(out)
        return out, ordered(out, gid)
    keys = [sh["between"]["at"] for sh in shapes if group_of(sh) == gid and sh["between"]["role"] == "key"]
    out = [sh for sh in shapes if not (group_of(sh) == gid and sh["between"]["role"] == "step")]
    at = next(i for i, sh in enumerate(out) if sh is first) + 1
    out[at:at] = [_blank(gid, u) for u in places(s["steps"], keys)]
    sync_groups(out)
    return out, ordered(out, gid)


def _blank(gid, at):
    """A step still to be made (sync_groups)."""
    return {"kind": "line", "pts": [[0, 0], [1, 1]], "between": {"id": gid, "role": "step", "at": at}}


def start_group(shapes, i, j):
    """Shapes i and j become the first and last shape of a new group (the one starting earlier first; Reverse on
    when pairing the ends the other way round travels less). Returns (new shape list, the group's id)."""
    a, b = shapes[i], shapes[j]
    if min(p[0] for p in b["pts"]) < min(p[0] for p in a["pts"]):
        a, b = b, a
    s = clean_settings(None)
    s["rev"] = ends_cross(a, b)
    s["smooth"] = True
    gid = new_id()
    a["between"] = {"id": gid, "role": "first", "set": s}
    b["between"] = {"id": gid, "role": "last"}
    return rebuild(shapes, gid, s, remake=True)[0], gid


def ends_cross(a, b):
    """True when a's start is nearer b's end than b's start (pairing them the other way round travels less)."""
    a0, a1, b0, b1 = a["pts"][0], a["pts"][-1], b["pts"][0], b["pts"][-1]
    return math.dist(a0, b1) + math.dist(a1, b0) < math.dist(a0, b0) + math.dist(a1, b1) - 1e-9


def turns(shapes):
    """Each shape's (group, colour turn) when its group gives each shape its own colour, else None. A shape with
    Colours on keeps its own Colours (user: Colours always comes first)."""
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
                if not shapes[i].get("cycle"):
                    out[i] = (gid, k % n)
    return out


def copied(shapes, start=0):
    """shapes[start:] are copies (pasted / duplicated): a group copied with its first and last shape stays a group
    (a new one; copied without any of its steps: they're made again, added at the end); otherwise the copies are
    plain shapes (user)."""
    copies = shapes[start:]
    gids = {group_of(sh) for sh in copies} - {None}
    count = len(shapes)
    for gid in sorted(gids):
        roles = [sh["between"]["role"] for sh in copies if group_of(sh) == gid]
        if "first" in roles and "last" in roles:
            fresh = new_id()
            for sh in copies:
                if group_of(sh) == gid:
                    sh["between"]["id"] = fresh
            if not any(r in ("step", "key") for r in roles):
                shapes += [_blank(fresh, u) for u in slots(settings_of(copies, fresh)["steps"])]
        else:
            for sh in copies:
                if group_of(sh) == gid:
                    del sh["between"]
    if len(shapes) > count:
        sync_groups(shapes)
