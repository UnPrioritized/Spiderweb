"""Gate sensitive merge (user, 2026-10-08, first try: plain notes): two shapes' notes become one. The shape that
stays keeps its notes; each key row of the other one slides sideways in time on its own until its notes meet the
staying shape's note on that key (gate end = next start), lengths kept. Rows with no note of the staying shape on
their key ("leftovers") slide by the smallest slide a row made and are handed back apart (user: their own shape,
to delete or keep)."""

import math

import numpy as np


def comes_from_left(stay, slide):
    """True if slide's middle is before stay's."""
    return np.asarray(slide)[:, :2].mean() < np.asarray(stay)[:, :2].mean()


def gate_merge(stay, slide, from_left=None):
    """stay, slide: (start, end, pitch, velocity[, track]) rows in ticks. Returns (stay + slide's rows that met,
    slide's leftover rows), or None when no row meets. Each row comes in from far away on that side (from_left;
    None = the side slide's middle is on), so it meets the staying shape's outer edge there: a row inside a hollow
    shape or overlapping it goes out to that edge."""
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
    moved[~met, :2] += min(shifts, key=abs)
    out, rest = np.concatenate([stay, moved[met]]), moved[~met]
    first = min(out[:, 0].min(), rest[:, 0].min() if len(rest) else 0)
    if first < 0:  # (slid before the song's start: everything waits)
        out[:, :2] -= first
        rest[:, :2] -= first
    return out, rest


# THE RECIPE (user: saved as both shapes + the way, not the notes): the merged shape is a custom shape of plain
# notes with sh["merge"] = {"parts": [the two shapes as they were], "right": Merge to right?, "ppq", "keys",
# "apart": keys whose rows became the "Merge leftovers" shape}. Project files leave its "notes" out; loading makes
# them again from the recipe.

def merged(parts, right, ppq, keys):
    """gate_merge of two shapes' notes (each (start, end, pitch, velocity) rows): parts = [left, right] by their
    middles; right = the left one slides right."""
    a, b = (np.asarray(p, np.int64).reshape(-1, 4)[:, :4] for p in parts)
    if len(a) and len(b) and comes_from_left(a, b):  # (a = the one on the left)
        a, b = b, a
    return gate_merge(b, a, True) if right else gate_merge(a, b, False)


def recipe_notes(m):
    """The merged shape's packed notes (custom.notes_shape) made from its recipe, or None. Leftover rows stay in
    it (user: never removed without the user's say)."""
    from notes.custom import notes_shape
    from notes.engine import shape_notes
    got = merged([shape_notes(p, m["ppq"], m["keys"]) for p in m["parts"]], m["right"], m["ppq"], m["keys"])
    if got is None:
        return None
    out, rest = got
    out = np.concatenate([out, rest[~np.isin(rest[:, 2], m["apart"])]])  # (but not the ones made "Merge leftovers")
    notes = np.column_stack([out[:, 0], out[:, 1] - out[:, 0], out[:, 2], out[:, 3], np.zeros(len(out), np.int64)])
    return notes_shape(notes, m["ppq"], "")["notes"]


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
    return out
