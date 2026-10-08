"""Gate sensitive merge (user, 2026-10-08, first try: plain notes): two shapes' notes become one. The shape that
stays keeps its notes; each key row of the other one slides sideways in time on its own until its notes meet the
staying shape's note on that key (gate end = next start), lengths kept. Rows with no note of the staying shape on
their key ("leftovers") slide by the smallest slide a row made and are handed back apart (user: their own shape,
to delete or keep)."""

import json
import math

import numpy as np


def comes_from_left(stay, slide):
    """True if slide's middle is before stay's."""
    return np.asarray(slide)[:, :2].mean() < np.asarray(stay)[:, :2].mean()


def gate_merge(stay, slide, from_left=None, rest_slides=True):
    """stay, slide: (start, end, pitch, velocity[, track]) rows in ticks. Returns (stay + slide's rows that met,
    slide's leftover rows), or None when no row meets. Each row comes in from far away on that side (from_left;
    None = the side slide's middle is on), so it meets the staying shape's outer edge there: a row inside a hollow
    shape or overlapping it goes out to that edge. rest_slides: the leftover rows slide by the smallest slide (off:
    they stay where they are)."""
    got = _merge(stay, slide, from_left, rest_slides)
    return got and got[:2]


def _merge(stay, slide, from_left, rest_slides=True):
    """gate_merge, and also the smallest slide and how much later everything went (to stay after tick 0)."""
    stay = np.asarray(stay, np.int64)
    moved = np.array(slide, np.int64, copy=True)
    if not len(stay) or not len(moved):
        return None
    if from_left is None:
        from_left = comes_from_left(stay, moved)
    met = np.zeros(len(moved), bool)
    shifts = []
    for key in np.unique(moved[:, 2]):
        here = stay[stay[:, 2] == key]
        if not len(here):
            continue
        rows = moved[:, 2] == key
        if from_left:  # its last note's end meets the staying shape's first note on this key
            shift = here[:, 0].min() - moved[rows, 1].max()
        else:  # its first note's start meets the staying shape's last note's end
            shift = here[:, 1].max() - moved[rows, 0].min()
        moved[rows, :2] += shift
        met |= rows
        shifts.append(int(shift))
    if not shifts:
        return None
    least = min(shifts, key=abs)
    if rest_slides:
        moved[~met, :2] += least
    out, rest = np.concatenate([stay, moved[met]]), moved[~met]
    first = min(out[:, 0].min(), rest[:, 0].min() if len(rest) else 0)
    late = max(0, -int(first))
    if late:  # (slid before the song's start: everything waits)
        out[:, :2] += late
        rest[:, :2] += late
    return out, rest, least, late


# THE RECIPE (user: saved as both shapes + the way, not the notes): the merged shape is a custom shape of plain
# notes with sh["merge"] = {"parts": [the two shapes as they were], "right": Merge to right?, "ppq", "keys",
# "apart": keys whose rows became the "Merge leftovers" shape}. Project files leave its "notes" out; loading makes
# them again from the recipe.

def part_rows(parts, ppq, keys):
    """Each shape's notes as (start, end, pitch, velocity, track) rows: its own colours (areas, outline colours,
    Colours turns) kept, the second shape's after the first's (user: each keeps its colours; with Multi channel
    they stay apart like the shapes were, as Turn into live shape does)."""
    from notes.engine import shape_notes_tracks
    out, first = [], 0
    for p in parts:
        notes, tracks = shape_notes_tracks(p, ppq, keys)
        notes = np.asarray(notes, np.int64).reshape(-1, 4)
        tracks = np.zeros(len(notes), np.int64) if tracks is None or not len(tracks) else np.asarray(tracks, np.int64)
        tracks = tracks - (tracks.min() if len(tracks) else 0)
        out.append(np.column_stack([notes, tracks + first]))
        first += int(tracks.max()) + 1 if len(tracks) else 1
    return out


def merged(parts, right, ppq, keys):
    """gate_merge of two shapes' notes (each (start, end, pitch, velocity[, track]) rows): parts = [left, right] by
    their middles; right = the left one slides right."""
    a, b = (np.asarray(p, np.int64).reshape(len(p), -1) for p in parts)
    if len(a) and len(b) and comes_from_left(a, b):  # (a = the one on the left)
        a, b = b, a
    return gate_merge(b, a, True) if right else gate_merge(a, b, False)


def recipe_notes(m):
    """The merged shape's packed notes (custom.notes_shape) made from its recipe, or None. Leftover rows stay in
    it (user: never removed without the user's say)."""
    got = recipe_shape(m)
    return got and got["notes"]


def recipe_shape(m):
    """custom.notes_shape of the recipe's notes ("notes" packed, "pts" = their box where they were made), or None
    (no notes at all). No row meeting any more (a part's settings changed): both as they are, nothing slid."""
    from notes.custom import notes_shape
    parts = part_rows(m["parts"], m["ppq"], m["keys"])
    got = merged(parts, m["right"], m["ppq"], m["keys"])
    if got is None:
        out = np.concatenate(parts)
    else:
        out, rest = got
        out = np.concatenate([out, rest[~np.isin(rest[:, 2], m["apart"])]])  # (not the ones made "Merge leftovers")
    if not len(out):
        return None
    notes = np.column_stack([out[:, 0], out[:, 1] - out[:, 0], out[:, 2], out[:, 3], out[:, 4]])
    return notes_shape(notes, m["ppq"], "")


# PANEL SETTINGS (user: the merged shape acts as one custom shape, but its outline is never edited, it's made of
# two shapes): the custom shape panel changes both parts (App.custom_targets), then refit makes the notes again.

BIG_KEYS = ("strokes", "pts", "areas", "polygon", "from", "cut", "text", "notes", "picture", "merge")


def settings_key(m):
    """The parts' settings (not their drawings) as one string: changed = refit."""
    return json.dumps([{k: v for k, v in p.items() if k not in BIG_KEYS} for p in m["parts"]], sort_keys=True)


def refit(sh):
    """A merged shape made again from its parts after their settings changed: new notes, its box fitted to them,
    still moved / turned / slanted / stretched as it was."""
    m = sh["merge"]
    got = recipe_shape(m)
    if not got:
        return
    was = m.get("box")
    mp = box_map(was, sh["pts"]) if was else None
    if mp is None:  # (an old recipe: only moved)
        at = m.get("at") or sh["pts"][0]
        mp = np.eye(2), np.asarray(sh["pts"][0], float) - np.asarray(at, float)
    L, o = mp
    made = [[float(b), float(p)] for b, p in got["pts"]]
    sh["notes"] = got["notes"]
    sh["pts"] = [[float(x) for x in L @ pt + o] for pt in made]
    m["box"], m["at"] = made, list(made[0])


# TURNED / SLANTED / STRETCHED (user: like one custom shape, gates kept): both parts get the same turn / slant /
# stretch as the merged shape's box ("box" = its corners when made), then they merge again. The sliding part first
# goes as far as its smallest slide (where it is on screen), so turning moves it round the right spot. Moved only:
# the notes kept (block_notes). A part that can't be turned this way (pasted notes, text, Hz bass, funnel...): the
# merged notes sampled like a turned image (turned_notes).

def made_sides(m):
    """(sliding part's index, from the left?, its smallest slide, how much later all went) as the recipe was made,
    ticks at m["ppq"]; None = nothing met."""
    from notes.engine import shape_notes
    a, b = (shape_notes(p, m["ppq"], m["keys"]) for p in m["parts"])
    left = 1 if len(a) and len(b) and comes_from_left(a, b) else 0
    slide = left if m["right"] else 1 - left
    got = _merge((a, b)[1 - slide], (a, b)[slide], m["right"])
    return got and (slide, m["right"], got[2], got[3])


def box_map(was, now):
    """The 2x2 matrix L and the offset o taking the box was to the box now (beats, keys): x -> L x + o; None when
    was is flat."""
    w, n = np.asarray(was, float), np.asarray(now, float)
    a = np.column_stack([w[1] - w[0], w[2] - w[0]])
    if abs(np.linalg.det(a)) < 1e-12:
        return None
    L = np.column_stack([n[1] - n[0], n[2] - n[0]]) @ np.linalg.inv(a)
    return L, n[0] - L @ w[0]


def mapped_part(p, f, L=None):
    """Shape p with every point through f(beat, key) -> [beat, key], or None if it can't be turned that way. L (the
    map's 2x2 matrix): what a Flip / Turn 90° does to a shape's settings is done too (flipped_settings)."""
    import copy
    from notes.engine import cached_arrays
    from notes.convert import has_tumours
    from notes.tumour import LINE_KINDS
    if p.get("cut") or isinstance(p.get("glue"), list):
        return None  # (notes from the shape it was cut from / glue boxes fixed in the song)
    q = copy.deepcopy(p)
    if p["kind"] == "custom":
        if "notes" in p or p.get("text") or p.get("hz") or p.get("merge"):
            return None
    elif p["kind"] not in LINE_KINDS:
        return None
    elif p["kind"] == "arc" or has_tumours(p) or p.get("shape") or p.get("pattern"):
        paths = cached_arrays(p)  # (as drawn, made into points: an arc turned or slanted isn't an arc)
        if len(paths) != 1:
            return None
        for k in ("tumour", "tumours", "shape", "pattern", "k", "smooth"):
            q.pop(k, None)
        q.update(kind="poly", pts=[f(b, k) for b, k in paths[0]])
        return flipped_settings(q, L)
    q["pts"] = [f(b, k) for b, k in p["pts"]]
    return flipped_settings(q, L)


def flipped_settings(q, L):
    """The settings a Flip / Turn 90° changes along with the drawing (fx.flip_shape / turn_shape: velocities flip
    sideways, a gate Range turns / flips), done as the map L does: mostly a quarter turn = turned that way, then
    mirrored (det < 0) = flipped, sideways when time runs backwards. Changes q."""
    from notes.gaterange import flipped_range, turned_range
    if L is None:
        return q
    if abs(L[0, 0]) < abs(L[1, 0]):  # (time now runs mostly along the keys: a quarter turn, clockwise = down)
        cw = L[1, 0] < 0
        for k in ("range", "range_kept"):
            if q.get(k):
                q[k] = turned_range(q[k], cw)
        L = np.array([[0, -1], [1, 0]] if cw else [[0, 1], [-1, 0]], float) @ L  # (the rest after turning back)
    if np.linalg.det(L) < 0:
        sideways = L[0, 0] < 0
        if sideways:
            if q.get("vel_env"):
                q["vel_env"] = [[1 - u, v] for u, v in reversed(q["vel_env"])]
            if "vel0" in q and "vel1" in q:
                q["vel0"], q["vel1"] = q["vel1"], q["vel0"]
        for k in ("range", "range_kept"):
            if q.get(k):
                q[k] = flipped_range(q[k], sideways)
    return q


def reshaped_parts(sh):
    """(the two parts turned / slanted / stretched with the merged shape's box, the sliding one at its smallest
    slide; sliding part's index; from the left?; (L, o, smallest slide in beats)) or None (only moved, an old
    recipe, nothing met, a part that can't be turned)."""
    m = sh["merge"]
    got = m.get("box") and box_map(m["box"], sh["pts"])
    if not got or np.allclose(got[0], np.eye(2), atol=1e-9):
        return None
    L, o = got
    sides = made_sides(m)
    if not sides:
        return None
    slide, from_left, least, late = sides
    parts = []
    for i, p in enumerate(m["parts"]):
        d = (late + (least if i == slide else 0)) / m["ppq"]
        q = mapped_part(p, lambda b, k, d=d: [float(x) for x in L @ (b + d, k) + o], L)
        if q is None:
            return None
        parts.append(q)
    if abs(L[0, 0]) > 1e-9:  # (time flipped: it comes from the other side; turned upright: by the middles)
        from_left = from_left == (L[0, 0] > 0)
    else:
        from_left = None
    return parts, slide, from_left, (L, o, least / m["ppq"])


def reshaped_notes(sh, ppq, keys):
    """The merged shape's notes, (start, end, pitch, velocity, track) rows, when its box was turned / slanted /
    stretched: the parts made again that way and merged again (rows that newly meet nothing stay in it, user), or
    None (see reshaped_parts)."""
    got = reshaped_parts(sh)
    if not got:
        return None
    parts, slide, from_left, (L, o, least) = got
    rows = part_rows(parts, ppq, keys)
    stay, moved = rows[1 - slide], rows[slide]
    apart = sh["merge"]["apart"]
    if len(moved) and apart:  # (rows made "Merge leftovers": their notes stay out, found where they were drawn)
        mid = np.column_stack([(moved[:, 0] + moved[:, 1]) / (2 * ppq), moved[:, 2] + 0.5])
        was = (mid - o) @ np.linalg.inv(L).T
        moved = moved[~np.isin(np.floor(was[:, 1]).astype(np.int64), apart)]
    # (rows with nothing to meet stay where they are: the sliding part already sits at its slide; a quarter turn: no
    # key row runs along the old ones, nothing merged again)
    upright = abs(L[0, 0]) <= 1e-9 * max(1.0, abs(L[1, 0]))
    merged_rows = (gate_merge(stay, moved, from_left, rest_slides=False) if len(stay) and len(moved) and not upright
                   else None)
    return np.concatenate(merged_rows) if merged_rows else np.concatenate([stay, moved])


MOST_SAMPLES = 20_000_000  # (key rows x time steps, like a turned picture's)


def turned_notes(sh, ppq):
    """A turned / slanted merged shape's notes, (start, end, pitch, velocity, track) rows (user: it turns as the one
    shape seen on screen, like a turned image, not chopped into staircases). Every key row crossing the box is
    sampled one tick at a time (fewer past MOST_SAMPLES): the note of the merged notes under each spot, so each
    stretch of one note = a flat note; touching notes stay apart."""
    from notes.custom import unpack_notes
    rows = unpack_notes(sh["notes"])  # (start, end, key, velocity, track), from the box's corner
    (b0, p0), (b1, p1), (b2, p2) = sh["pts"]
    ub, up, vb, vp = b1 - b0, p1 - p0, b2 - b0, p2 - p0
    det = ub * vp - up * vb
    if abs(det) < 1e-12 or not len(rows):
        return np.zeros((0, 5), np.int64)
    t_all, k_all = float(rows[:, 1].max()), float(rows[:, 2].max() + 1)
    cb, cp = [b0, b1, b2, b1 + b2 - b0], [p0, p1, p2, p1 + p2 - p0]
    lo_k, hi_k = int(np.ceil(min(cp))), int(np.floor(max(cp)))
    t0, span = min(cb), max(cb) - min(cb)
    n = max(1, int(np.ceil(span * ppq)))
    if hi_k < lo_k:
        return np.zeros((0, 5), np.int64)
    n = min(n, max(1, MOST_SAMPLES // (hi_k - lo_k + 1)))
    step = span / n
    kk = np.arange(lo_k, hi_k + 1, dtype=float)[:, None]
    bb = t0 + (np.arange(n) + 0.5)[None, :] * step
    u = ((bb - b0) * vp - (kk - p0) * vb) / det
    v = ((kk - p0) * ub - up * (bb - b0)) / det
    inside = (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
    key = np.clip((v * k_all).astype(np.int64), 0, int(k_all) - 1)
    tick = u * t_all
    order = np.lexsort((rows[:, 0], rows[:, 2]))  # by key, then start (notes on one key don't overlap)
    srt = rows[order]
    flat = srt[:, 2] * (t_all + 1) + srt[:, 0]  # (one sorted number per note start)
    at = np.searchsorted(flat, key * (t_all + 1) + tick, side="right") - 1
    ok = inside & (at >= 0)
    at = np.clip(at, 0, len(srt) - 1)
    ok &= (srt[at, 2] == key) & (tick < srt[at, 1])
    a = np.where(ok, at, -1)  # key rows x time steps: which note, -1 = none
    change = np.ones(a.shape, bool)
    change[:, 1:] = a[:, 1:] != a[:, :-1]
    ks, ts = np.nonzero(change)
    ends = np.full(len(ts), n)
    ends[:-1] = np.where(ks[1:] == ks[:-1], ts[1:], n)
    val = a[ks, ts]
    on = val >= 0
    start = np.round((t0 + ts * step) * ppq).astype(np.int64)
    end = np.round((t0 + ends * step) * ppq).astype(np.int64)
    got = srt[np.maximum(val, 0)]
    out = np.column_stack([start, end, ks + lo_k, got[:, 3], got[:, 4]])[on]
    return out[out[:, 1] > out[:, 0]]


def clean_merge(m, clean_shape):
    """A recipe from a file -> valid, or None."""
    try:
        parts = [clean_shape(p) if isinstance(p, dict) else None for p in m["parts"]]
        ppq, keys = int(m["ppq"]), int(m["keys"])
        apart = sorted({int(k) for k in m.get("apart", [])})
        at = [float(x) for x in m["at"]] if m.get("at") is not None else None
    except (KeyError, TypeError, ValueError):
        return None
    if len(parts) != 2 or not all(parts) or not 1 <= ppq <= 65535 or keys not in (128, 256):
        return None
    out = {"parts": parts, "right": m.get("right") is True, "ppq": ppq, "keys": keys, "apart": apart}
    if at and len(at) == 2 and all(map(math.isfinite, at)):
        out["at"] = at  # (its box's first corner when made: Split moves the parts as far as it moved)
    try:
        box = [[float(b), float(p)] for b, p in m["box"]] if m.get("box") is not None else None
    except (TypeError, ValueError):
        box = None
    if box and len(box) == 3 and all(math.isfinite(x) for pt in box for x in pt):
        out["box"] = box  # (its box when made: turned / slanted / stretched since = the parts too, merged again)
    return out
