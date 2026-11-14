"""A loaded MIDI moved by Change visual time signature (PLAN.md 2026-11-14, user picked "visual only until it gets
exported", like Keep timing): the file itself never changes; app.loaded_map says where each of its ticks is SHOWN,
and every reader (drawing, playback, Extract...) moves the file's ticks through map_ticks; Export MIDI writes the
very same moves into the file (midi_scan.rewrite's map). Screen and export must match PIXEL FOR PIXEL (user), so the
one rule here IS rewrite's (and its compiled twin's): anchors exactly, between two anchors the same share of the
way rounded with rint (round half to even), never back onto the anchor before when there's room.

app.loaded_map = a list of LINKS, one per change, oldest first ([] = none); a link = {"n": text, "f": text}: its
anchors (old ticks, strictly rising) and where they go (never falling), each int64 array delta-packed + zlib +
base64 (JSON-safe: undo steps, the project file). A later change's link works on the ticks the earlier ones made
(the chain), so stacked changes are exact without ever merging them. links() turns the texts into arrays
(remembered by text)."""

import base64
import zlib

import numpy as np

MOST_ANCHORS = 1 << 24  # (a link's anchors: 16 M = 128 MB a side; a by-sound span gives one a tick)
_known = {}  # (text -> array, the last few: undo steps share the very same texts)
_KNOWN_MOST = 64


def pack(a):
    """int64 array -> text (deltas, zlib, base64)."""
    a = np.ascontiguousarray(a, np.int64)
    d = np.diff(a, prepend=np.int64(0)) if len(a) else a
    return base64.b64encode(zlib.compress(d.tobytes(), 6)).decode("ascii")


def unpack(text):
    """pack's text -> int64 array; None when it can't be read (damaged, too big)."""
    got = _known.get(text)
    if got is not None:
        return got
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
        z = zlib.decompressobj()
        data = z.decompress(raw, MOST_ANCHORS * 8 + 1)
        if z.unconsumed_tail or len(data) > MOST_ANCHORS * 8 or len(data) % 8:
            return None
    except (ValueError, zlib.error, UnicodeEncodeError, AttributeError):
        return None
    a = np.cumsum(np.frombuffer(data, np.int64), dtype=np.int64)
    a.flags.writeable = False
    if len(_known) >= _KNOWN_MOST:
        _known.pop(next(iter(_known)))
    _known[text] = a
    return a


def make_link(n, f):
    """Anchors (n -> f) as a saved link."""
    return {"n": pack(n), "f": pack(f)}


def _good(n, f):
    return (n is not None and f is not None and len(n) == len(f) and len(n) >= 1 and int(n[0]) >= 0
            and bool(np.all(n[1:] > n[:-1])) and bool(np.all(f[1:] >= f[:-1])) and int(f[0]) >= 0)


def clean(raw):
    """A project's / undo step's "loaded_map" as read -> the list of good links (a damaged one: all dropped, they
    only work as a chain; None / anything else = [])."""
    if not isinstance(raw, list):
        return []
    out = []
    for ln in raw:
        if not (isinstance(ln, dict) and isinstance(ln.get("n"), str) and isinstance(ln.get("f"), str)):
            return []
        if not _good(unpack(ln["n"]), unpack(ln["f"])):
            return []
        out.append({"n": ln["n"], "f": ln["f"]})
    return out


def links(saved):
    """app.loaded_map -> [(n, f)] int64 arrays, oldest first."""
    return [(unpack(ln["n"]), unpack(ln["f"])) for ln in saved or ()]


def apply_link(n, f, ticks):
    """ticks through one link: EXACTLY midi_scan.rewrite's map (Python + compiled): the anchor at or before the
    tick (the first one for ticks before it), its own move; between two anchors that don't move alike, the same
    share of the way, rint((t - n_i) * fd / gd) (the product in whole numbers, then a float divide), never onto the
    anchor before (then one tick on, when the next anchor leaves room). Past the last anchor: its move."""
    t = np.asarray(ticks, np.int64)
    i = np.maximum(np.searchsorted(n, t, "right") - 1, 0)
    ni, fi = n[i], f[i]
    out = fi + (t - ni)
    j = np.minimum(i + 1, len(n) - 1)
    gd, fd = n[j] - ni, f[j] - fi
    mid = (j > i) & (t > ni) & (gd > 0) & (fd != gd)
    if np.any(mid):
        share = fi + np.rint((t - ni) * fd / np.maximum(gd, 1)).astype(np.int64)
        share = np.where(share == fi, fi + np.minimum(fd, 1), share)
        out = np.where(mid, share, out)
    return out


def map_ticks(chain, ticks):
    """File ticks -> where they're SHOWN (and exported): every link in turn (chain = links()). Scalar in = int out.
    No links = the ticks as they are."""
    scalar = np.ndim(ticks) == 0
    t = np.asarray(ticks, np.int64)
    for n, f in chain:
        t = apply_link(n, f, t)
    if scalar:
        return int(t)
    return t


def changed_spans(chain):
    """[(a, b)] SHOWN ticks (after the whole chain) where some link doesn't just shift the ticks (stretched,
    squeezed, by sound, dense): the bars a change made. Sorted, joined."""
    spans = []
    for k, (n, f) in enumerate(chain):
        gd, fd = np.diff(n), np.diff(f)
        bad = np.flatnonzero(gd != fd)
        if not len(bad):
            continue
        # (rows next to each other joined: a by-sound span gives one a tick)
        cut = np.flatnonzero(np.diff(bad) > 1) + 1
        lo = bad[np.concatenate([[0], cut])]
        hi = bad[np.concatenate([cut - 1, [len(bad) - 1]])] + 1
        a, b = f[lo], f[hi]
        for n2, f2 in chain[k + 1:]:
            a, b = apply_link(n2, f2, a), apply_link(n2, f2, b)
        spans += list(zip(a.tolist(), b.tolist()))
    spans.sort()
    out = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def keep_clash(saved, tempo, ppq):
    """A Keep timing part lying (even partly) in bars a change moved over the loaded MIDI: its notes can't keep
    the time they have in the file exactly there (the file's tempo, moved, isn't whole microseconds a tick), so the
    change is REFUSED (user 2026-11-14: refuse the change, never the export). Returns {"why": "keep_loaded", "at":
    beat} or None. saved = the links the change would leave (old ones + its own)."""
    from notes import tempo as T
    spans = changed_spans(links(saved))
    if not spans:
        return None
    pts = tempo.get("points") or []
    starts = [a for a, _ in spans]
    for i in T.keep_points(pts):
        lo = int(round(pts[i][0] * ppq))
        hi = int(round(pts[i + 1][0] * ppq)) if i + 1 < len(pts) else 1 << 62
        k = int(np.searchsorted(starts, hi, "left")) - 1
        if k >= 0 and spans[k][1] > lo:
            return {"why": "keep_loaded", "at": pts[i][0]}
    return None


def heard(saved, file_tempo):
    """The loaded MIDI's own tempo [(tick, us)] as SHOWN: each change moved to its shown tick; in a stretched /
    squeezed piece its microseconds a tick scaled by the piece's old / new ticks (rounded: only near, which is why
    keep_clash refuses Keep timing there). No links = file_tempo itself."""
    chain = links(saved)
    if not chain or not file_tempo:
        return file_tempo
    t = np.array([x for x, _ in file_tempo], np.int64)
    us = np.array([u for _, u in file_tempo], np.float64)
    for n, f in chain:
        i = np.maximum(np.searchsorted(n, t, "right") - 1, 0)
        j = np.minimum(i + 1, len(n) - 1)
        gd, fd = (n[j] - n[i]).astype(np.float64), (f[j] - f[i]).astype(np.float64)
        scale = np.where((j > i) & (gd > 0) & (fd > 0), gd / np.maximum(fd, 1), 1.0)
        us = us * scale
        t = apply_link(n, f, t)
    out = []
    for x, u in zip(t.tolist(), np.rint(us).astype(np.int64).tolist()):
        u = max(1, min(u, 0xFFFFFF))
        if out and out[-1][0] == x:
            out[-1] = (x, u)
        elif not out or out[-1][1] != u:
            out.append((x, u))
    return out


def link_of(pl, ppq):
    """A rebar plan (notes/rebar.py plan(), after tempo_of: its "sound_spans" / "dense_map" filled in) as one link
    over the loaded MIDI's ticks (the SHOWN ticks before this change). Anchors: tick 0, each run's start, the bars'
    end (moving on by the ticks the bars grew); every tick of a span placed by when it sounds; each note a dense fix
    gave its own tick (with the ticks either side of it kept on the run's own rule, so the rest of the run isn't
    bent). Between anchors rewrite's rule = the run's stretch."""
    from notes.rebar import map_ticks as plan_ticks
    rn = [0] + [round(r[0] * ppq) for r in pl["runs"]] + [round(pl["b"] * ppq)]
    rf = [0] + [round(r[2] * ppq) for r in pl["runs"]] + [round(pl["new_b"] * ppq)]
    base_n, base_f = _rising(*_firsts(np.array(rn, np.int64), np.array(rf, np.int64)))
    extra = []
    for lo, hi in pl.get("sound_spans") or ():  # (a ramp placed by sound: every tick its own anchor)
        if hi > lo:
            extra.append(np.arange(int(lo), int(hi), dtype=np.int64))
    dm = pl.get("dense_map")
    keys = np.asarray(dm[0], np.int64) if dm else np.zeros(0, np.int64)
    if extra or len(keys):
        own = np.unique(np.concatenate(extra + [keys]))
        own_f = np.asarray(plan_ticks(pl, own, ppq), np.int64)
        near = np.setdiff1d(np.concatenate([keys - 1, keys + 1]), np.concatenate([own, base_n]))
        near = near[near >= 0]
        near_f = apply_link(base_n, base_f, near)
        n = np.concatenate([base_n, own, near])
        f = np.concatenate([base_f, own_f, near_f])
        # (on one tick: the dense / by-sound place wins over the run's own rule)
        prio = np.concatenate([np.zeros(len(base_n)), np.full(len(own), 2), np.ones(len(near))])
        order = np.lexsort((-prio, n))
        base_n, base_f = _rising(*_firsts(n[order], f[order]))
    return make_link(base_n, base_f)


def _firsts(n, f):
    """Sorted anchors with one per tick (the first of each)."""
    first = np.concatenate([[True], n[1:] != n[:-1]])
    return n[first], f[first]


def _rising(n, f):
    """Anchors kept never falling (a rounding nearer the old place can't put a later tick before an earlier one)."""
    return n, np.maximum.accumulate(f)


def is_shift(n, f):
    """The link only moves ticks along (no anchors stretching / squeezing): nothing to do."""
    return bool(np.all(np.diff(n) == np.diff(f))) and int(f[0]) == int(n[0])


def add(saved, link):
    """The links after one more change (a new list: undo keeps the old one). A link that moves nothing = none."""
    n, f = unpack(link["n"]), unpack(link["f"])
    if is_shift(n, f):
        return list(saved or [])
    return list(saved or []) + [link]


def export_bp(saved, keep_bp=None, ticks=None, loose=None):
    """The one map Export MIDI hands midi_scan.rewrite (Retime.bp): file ticks -> written ticks. keep_bp = Keep
    timing's (file_plan "bp": shown -> written; None = none). One link and no Keep timing = that link, exact for
    every event. Stacked links / Keep timing on top: anchors at every given file tick (ticks = the notes' starts /
    ends, loose = CC messages: LoadedMidi.anchors) + the first link's own, each sent through the whole chain (+ Keep
    timing), so every note and CC lands exactly where the screen shows it (other events in between: the same
    share of the way, a tick or so off at most). None = nothing moves."""
    chain = links(saved)
    if not chain:
        return keep_bp
    if len(chain) == 1 and keep_bp is None:
        return chain[0]
    parts = [chain[0][0]]
    for a in (ticks, loose):
        if a is not None and len(a):
            parts.append(np.asarray(a, np.int64))
    x = np.unique(np.concatenate(parts))
    x = x[x >= 0]
    y = map_ticks(chain, x)
    if keep_bp is not None:
        y = apply_link(np.asarray(keep_bp[0], np.int64), np.asarray(keep_bp[1], np.int64), y)
    return x, np.maximum.accumulate(y)
