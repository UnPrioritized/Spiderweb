"""A path drawn in the Hz bass window (its Line / Polyline / Freehand / Curve / Arc tools) turned into notes joined by
slides that follow it (user, 2026-10-09: "curve to notes").

The path is cut where it turns from going up to going down (a slide only goes one way), then each piece is fitted
with one slide (one arc or an S, hz_glide.slide_part) and cut again where that's more than TOL keys off. Every cut is
a note at the path's pitch there (key + its own tune in cents, so it can sit between keys); the note reaches from the
middle of the piece before to the middle of the piece after, and the slides leave each note at its cut and reach the
next at its cut, so the tone glides the whole way, one chain of slides (no new attack)."""

import numpy as np

from notes.hz_glide import next_id
from notes.hz_settings import SLIDE_BEND, TUNE

TOL = 0.05  # keys: how far a slide may be off the path (5 cents)
TURN = 0.02  # keys: a path going back this far the other way has turned (a peak or a dip)
BENDS = sorted(np.linspace(-SLIDE_BEND, SLIDE_BEND, 39), key=abs)  # (straight first: it wins a tie)
SAMPLES = 256  # a path with fewer points: straight between them, this many to fit
KINDS = ("single", "double")


def bent(u, b):
    """hz_glide.bent_one for an array u (0..1) and one bend b."""
    m = (1.0 + b) / 2.0
    if b > 0:
        return 1.0 - (1.0 - u) ** (np.log(1.0 - m) / np.log(0.5))
    if b < 0:
        return u ** (np.log(m) / np.log(0.5))
    return u


def part(u, b, kind):
    """hz_glide.slide_part with its own bend b, for an array u."""
    if kind == "double":
        return np.where(u < 0.5, 0.5 * bent(np.minimum(2.0 * u, 1.0), b),
                        1.0 - 0.5 * bent(np.clip(2.0 - 2.0 * u, 0.0, 1.0), b))
    return bent(u, b)


def best_slide(u, f):
    """(how far off at most, in parts of the way, bend, kind): the slide nearest the points (u, f), both 0..1."""
    best = (np.inf, 0.0, "single")
    for kind in KINDS:
        for b in BENDS:
            off = float(np.max(np.abs(part(u, b, kind) - f)))
            if off < best[0]:
                best = (off, float(b), kind)
    off, b0, kind = best
    for b in np.clip(b0 + np.linspace(-0.05, 0.05, 21), -SLIDE_BEND, SLIDE_BEND):  # (finer, around it)
        o = float(np.max(np.abs(part(u, b, kind) - f)))
        if o < off:
            off, best = o, (o, float(b), kind)
    return best


def turns(p, turn=TURN):
    """The points where the path turns (peaks and dips: going back by more than `turn` keys), the ends left
    out."""
    out, way, ext = [], 0, 0  # (way: 1 going up, -1 down, 0 not known yet; ext: the highest / lowest point since)
    for i in range(1, len(p)):
        if way == 0:
            if abs(p[i] - p[0]) > turn:
                way = 1 if p[i] > p[0] else -1
                ext = int(np.argmax(p[:i + 1]) if way > 0 else np.argmin(p[:i + 1]))
        elif (p[i] - p[ext]) * way >= 0:
            ext = i
        elif abs(p[i] - p[ext]) > turn:
            out.append(ext)
            way, ext = -way, i
    return out


def path_tones(path, ppq, tones=(), tol=TOL, turn=None):
    """New tones (with their slides) for path [(beat, pitch in keys), ...] (beats going on; one going back is held
    where it was). tones = the notes already there (the new ones get the next numbers). turn = how far it must go
    back to be a peak / dip (TURN; a freehand path: its tolerance, so a hand's wobble isn't one). None when it's too
    short to make two notes (under 2 ticks)."""
    tick = 1.0 / ppq
    t = np.maximum.accumulate(np.asarray([b for b, _ in path], float))
    p = np.clip(np.asarray([k for _, k in path], float), 0.0, 127.0)
    if len(t) < SAMPLES and t[-1] > t[0]:  # (a few points: straight between them, filled in to be fitted)
        u = np.unique(np.concatenate([t, np.linspace(t[0], t[-1], SAMPLES)]))
        t, p = u, np.interp(u, t, p)
    t = np.round(np.maximum(t, 0.0) / tick) * tick
    if t[-1] - t[0] < 2 * tick - 1e-9:
        return None
    pieces = []

    def fit(i, j):
        t0, t1, p0, p1 = t[i], t[j], p[i], p[j]
        u = (t[i:j + 1] - t0) / (t1 - t0)
        if abs(p1 - p0) < 1e-9:
            off, b, kind = float(np.max(np.abs(p[i:j + 1] - p0))), 0.0, "single"
        else:
            off, b, kind = best_slide(u, (p[i:j + 1] - p0) / (p1 - p0))
            off *= abs(p1 - p0)
        if off > tol and j - i >= 2:  # cut where it's furthest off, when both halves stay 2 ticks long
            fitted = p0 + (p1 - p0) * part(u, b, kind) if abs(p1 - p0) >= 1e-9 else np.full(len(u), p0)
            k = i + int(np.argmax(np.abs(fitted - p[i:j + 1])))
            if not (i < k < j and t[k] - t0 >= 2 * tick - 1e-9 and t1 - t[k] >= 2 * tick - 1e-9):
                k = next((m for m in range(i + 1, j) if t[m] - t0 >= 2 * tick - 1e-9
                          and t1 - t[m] >= 2 * tick - 1e-9), None)
            if k is not None:
                fit(i, k)
                fit(k, j)
                return
        pieces.append((i, j, b, kind))

    cuts = [0]
    for k in turns(p, turn or TURN) + [len(p) - 1]:  # (a turn too near the last cut or the end: left out)
        if t[k] - t[cuts[-1]] >= 2 * tick - 1e-9 and (k == len(p) - 1 or t[-1] - t[k] >= 2 * tick - 1e-9):
            cuts.append(k)
    if cuts[-1] != len(p) - 1:
        cuts[-1] = len(p) - 1
    for i, j in zip(cuts, cuts[1:]):
        fit(i, j)
    at = [t[i] for i, _, _, _ in pieces] + [t[pieces[-1][1]]]  # the cuts' beats and pitches
    keys = [p[i] for i, _, _, _ in pieces] + [p[pieces[-1][1]]]
    mids = [max(a + tick, min(b - tick, round((a + b) / 2 / tick) * tick)) for a, b in zip(at, at[1:])]
    starts, ends = [at[0]] + mids, mids + [at[-1]]
    first = next_id(tones)
    out = []
    for k, (s, e, pk) in enumerate(zip(starts, ends, keys)):
        key = int(min(127, max(0, round(pk))))
        cents = float(min(TUNE, max(-TUNE, round((pk - key) * 100.0, 3))))
        out.append({"t": float(s), "len": float(e - s), "key": key, "cents": cents + 0.0, "id": first + k, "to": []})
    for k, (_, _, b, kind) in enumerate(pieces):
        a, nb = out[k], out[k + 1]
        s = {"id": nb["id"], "out": float(a["t"] + a["len"] - at[k]), "in": float(at[k + 1] - nb["t"]),
             "bend": round(b, 6)}
        if kind == "double":
            s["kind"] = "double"
        a["to"].append(s)
    return out
