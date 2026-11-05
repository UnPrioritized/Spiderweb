"""Shapes remembered inside Domino (Draw on Domino, user 2026-11-06): which of Domino's notes came from which
Spiderweb shape, found again from Domino's notes alone (read from its memory, files/domino_view.track_notes).

Domino moves notes one by one and makes NEW note objects when it moves them, so a shape is recognised by its
PATTERN: a record = the shape + the notes that landed (track, tick, gate, key, velocity). It is ALIVE while every
one of those notes is in Domino exactly, all shifted by one time + key amount (dt, dk: the shape follows), and
ASLEEP otherwise (single notes edited, part deleted, taken out: plain Domino notes). Asleep records wake up when
the notes come back where they were (Domino's Ctrl+Z). Moved ones are looked for among Domino's SELECTED notes
only (Domino keeps what it moved / pasted selected; hunt 2026-11-06: a 1-note shape grabbed the user's own notes).
A whole shape copied in Domino = a new record, only right after notes were added to its track (a paste /
duplicate; Select All never makes copies). Two records never claim the same notes; the newest claims first.
Forgotten only when baked (window/domino_companion).
Plain Python + NumPy, no windows: the "finding" part, so another way of finding (native code) can replace it."""

import base64
import copy
import itertools
import json
import os
import zlib

import numpy as np

_ids = itertools.count(1)
MAX_TRIES = 400  # candidate offsets tried per record per look (among the selected notes)
ASLEEP_BUDGET_S = 0.1  # a look's time for searching moved asleep records (the rest: at the next change)


def new_record(shape, notes):
    """notes = (track, tick, gate, key, velocity) as they landed in Domino."""
    notes = np.asarray(notes, np.int64).reshape(-1, 5)
    return {"id": next(_ids), "shape": shape, "notes": notes, "dt": 0, "dk": 0, "alive": len(notes) > 0}


def _code(track, tick, key):
    return (track << 47) | ((tick & 0x7FFFFFFF) << 8) | (key & 0xFF)


class Notes:
    """Domino's notes of the tracks looked at, ready for "is this exact note there?" (track -> array of tick,
    gate, key, velocity, selected). fresh: track -> True for each note new since the last look (a paste, a
    duplicate, a drag: Domino makes new notes; measured 2026-11-06: its paste does NOT select what it pasted)."""

    def __init__(self, tracks, fresh=None):
        parts = []
        for t, a in tracks.items():
            if len(a):
                f = (fresh or {}).get(t)
                f = np.zeros(len(a), np.int64) if f is None or len(f) != len(a) else np.asarray(f, np.int64)
                parts.append(np.column_stack([np.full(len(a), t, np.int64), a[:, :5], f]))
        rows = np.concatenate(parts) if parts else np.zeros((0, 7), np.int64)
        code = _code(rows[:, 0], rows[:, 1], rows[:, 3])
        order = np.argsort(code, kind="stable")
        self.rows, self.code = rows[order], code[order]
        self.claimed = np.zeros(len(self.rows), bool)
        self.pool = {"moved": (self.rows[:, 5] != 0) | (self.rows[:, 6] != 0), "fresh": self.rows[:, 6] != 0}
        self._by_kind = {}

    def by_kind(self, which):
        """(track, gate, velocity) -> the notes of that pool like that (made once a look)."""
        got = self._by_kind.get(which)
        if got is None:
            got = self._by_kind[which] = {}
            idx = np.flatnonzero(self.pool[which])
            if len(idx):
                r = self.rows[idx]
                order = np.lexsort((r[:, 4], r[:, 2], r[:, 0]))
                keys = r[order][:, [0, 2, 4]]
                cut = np.flatnonzero(np.any(np.diff(keys, axis=0) != 0, axis=1)) + 1
                for part in np.split(order, cut):
                    k = r[part[0]]
                    got[(int(k[0]), int(k[2]), int(k[4]))] = idx[part]
        return got

    def find(self, notes, dt, dk):
        """Index of each note shifted by (dt, dk) among Domino's (exact tick, gate, key, velocity), or None if any
        is missing / already claimed."""
        if not len(notes) or not len(self.code):
            return None
        tick, key = notes[:, 1] + dt, notes[:, 3] + dk
        if tick.min() < 0 or key.min() < 0 or key.max() > 255:
            return None
        c = _code(notes[:, 0], tick, key)
        lo, hi = np.searchsorted(self.code, c, "left"), np.searchsorted(self.code, c, "right")
        if (lo == hi).any():
            return None
        at = lo.copy()
        for _ in range(4):  # (two notes on one tick + key in Domino: the next one too)
            ok = (self.rows[at, 2] == notes[:, 2]) & (self.rows[at, 4] == notes[:, 4]) & ~self.claimed[at]
            if ok.all():
                return at if len(np.unique(at)) == len(at) else None
            at = np.where(ok, at, at + 1)
            if (at >= hi).any():
                return None
        return None

    def candidates(self, notes, which):
        """(dt, dk) offsets that put the whole pattern on notes of the pool which ("moved" = selected or new since
        the last look; "fresh" = new only), checked note by note for every offset at once."""
        kinds = self.by_kind(which)
        if not kinds or not len(notes):
            return []
        best = None
        for n in notes[:64]:
            got = kinds.get((int(n[0]), int(n[2]), int(n[4])))
            if got is None:
                return []  # (a note with no look-alike in the pool: the pattern can't be there)
            if best is None or len(got) < len(best[1]):
                best = (n, got)
        n, got = best
        rows = self.rows[got]
        offs = np.column_stack([rows[:, 1] - n[1], rows[:, 3] - n[3]])
        pool = self.pool[which]
        for m in notes[:32]:  # (Select All on a dense track: thousands of look-alikes, hunt 2026-11-06)
            if not len(offs):
                break
            c = _code(np.full(len(offs), m[0]), m[1] + offs[:, 0], m[3] + offs[:, 1])
            at = np.minimum(np.searchsorted(self.code, c), max(len(self.code) - 1, 0))
            ok = (self.code[at] == c) & (self.rows[at, 2] == m[2]) & (self.rows[at, 4] == m[4]) & pool[at] \
                & (m[1] + offs[:, 0] >= 0)
            offs = offs[ok]
        if len(offs) > 1:
            offs = np.unique(offs, axis=0)
        return [tuple(map(int, o)) for o in offs]


def look(records, tracks, fresh=None):
    """Match the records against Domino's notes (tracks: track -> array of tick, gate, key, velocity, selected;
    fresh: track -> notes new since the last look). Records are changed in place (dt, dk, alive). Returns
    (events, new records): events = (what, record), what = "follow" (moved), "sleep", "wake", "copy" (a whole
    shape pasted / duplicated in Domino: a new record, made from new notes only, never by Select All)."""
    import time
    dom = Notes(tracks, fresh)
    events, made = [], []
    todo = [r for r in records if len(r["notes"])]
    # 1: records exactly where they were claim their notes first, the newest first (a shape taken out and sent
    # back unchanged: the new record keeps the notes, the old one stays asleep)
    rest = []
    for r in reversed(todo):
        at = dom.find(r["notes"], r["dt"], r["dk"])
        if at is None:
            rest.append(r)
            continue
        dom.claimed[at] = True
        if not r["alive"]:
            r["alive"] = True
            events.append(("wake", r))
    # 2: moved: among the selected / new notes only; else asleep. The awake ones first; asleep ones (they mostly
    # wake where they were, step 1) only while ASLEEP_BUDGET_S lasts (Select All on a dense track, many asleep)
    t0 = time.perf_counter()
    rest.sort(key=lambda r: not r["alive"])
    for r in rest:
        if not r["alive"] and time.perf_counter() - t0 > ASLEEP_BUDGET_S:
            break
        found = None
        offs = dom.candidates(r["notes"], "moved")
        offs.sort(key=lambda o: abs(o[0] - r["dt"]) + abs(o[1] - r["dk"]) * 10)
        for o in offs[:MAX_TRIES]:
            at = dom.find(r["notes"], *o)
            if at is not None and dom.pool["moved"][at].all():
                found = (o, at)
                break
        if found:
            (r["dt"], r["dk"]), at = found
            dom.claimed[at] = True
            events.append(("wake" if not r["alive"] else "follow", r))
            r["alive"] = True
        elif r["alive"]:
            r["alive"] = False
            events.append(("sleep", r))
    # 3: whole copies made in Domino just now: NEW notes forming an alive record's pattern
    if dom.pool["fresh"].any():
        for r in [r for r in todo if r["alive"]]:
            for o in dom.candidates(r["notes"], "fresh")[:MAX_TRIES]:
                if o == (r["dt"], r["dk"]):
                    continue
                at = dom.find(r["notes"], *o)
                if at is not None and dom.pool["fresh"][at].all():
                    dom.claimed[at] = True
                    c = new_record(copy.deepcopy(r["shape"]), r["notes"])
                    c["dt"], c["dk"] = o
                    for k in ("pid", "ppq", "song", "tptr"):
                        if k in r:
                            c[k] = r[k]
                    made.append(c)
                    events.append(("copy", c))
    return events, made


def fresh_since(before, now):
    """Which notes of a track (array of tick, gate, key, velocity, ...) weren't there at the last look (before =
    that look's sorted note codes, or None: nothing counts as new) -> (mask, this look's codes)."""
    code = np.ascontiguousarray(now[:, :4], np.int64).view(np.dtype((np.void, 32))).ravel()  # (each note exact)
    mine = np.sort(code)
    if before is None or not len(before):
        return np.zeros(len(now), bool) if before is None else np.ones(len(now), bool), mine
    at = np.minimum(np.searchsorted(before, code), len(before) - 1)
    return before[at] != code, mine


def landed(sent, tracks):
    """sent = (track, tick, gate, key, velocity) as pasted -> the same notes as Domino holds them now (its overlap
    rule may have cut gates; a note on a tick + key Domino kept another one of = left out)."""
    sent = np.asarray(sent, np.int64).reshape(-1, 5)
    dom = Notes(tracks)
    if not len(sent) or not len(dom.code):
        return np.zeros((0, 5), np.int64)
    c = _code(sent[:, 0], sent[:, 1], sent[:, 3])
    lo, hi = np.searchsorted(dom.code, c, "left"), np.searchsorted(dom.code, c, "right")
    out = []
    for i in range(len(sent)):
        hits = [j for j in range(lo[i], hi[i]) if dom.rows[j, 4] == sent[i, 4] and not dom.claimed[j]]
        if hits:
            j = next((j for j in hits if dom.rows[j, 2] == sent[i, 2]), hits[0])  # (its own gate first)
            dom.claimed[j] = True
            out.append((sent[i, 0], sent[i, 1], dom.rows[j, 2], sent[i, 3], sent[i, 4]))
    return np.array(out, np.int64).reshape(-1, 5)


def trimmed(record, tracks):
    """Spiderweb's own Single channel send cut some of this record's notes short (a later shape over them): take
    the gates Domino has now at the same tick + key + velocity, so the record stays alive. Only for Spiderweb's
    own sends; the user's edits put shapes to sleep instead."""
    if not record["alive"] or not len(record["notes"]):
        return False
    dom = Notes(tracks)
    if not len(dom.code):
        return False
    n = record["notes"]
    c = _code(n[:, 0], n[:, 1] + record["dt"], n[:, 3] + record["dk"])
    at = np.minimum(np.searchsorted(dom.code, c), len(dom.code) - 1)
    same = (dom.code[at] == c) & (dom.rows[at, 4] == n[:, 4])
    if not same.all():
        return False
    new = dom.rows[at, 2]
    if (new == n[:, 2]).all():
        return False
    record["notes"] = n.copy()
    record["notes"][:, 2] = new
    return True


def rescale(record, ppq):
    """The song's PPQ changed (or a file made at another one): ticks, gates and the shift scaled the way Domino
    scales its notes (rounded)."""
    old = record.get("ppq")
    if not old or not ppq or old == ppq:
        record["ppq"] = ppq or old
        return False
    n = record["notes"].copy()
    n[:, 1] = np.round(n[:, 1] * ppq / old)
    n[:, 2] = np.maximum(1, np.round(n[:, 2] * ppq / old))
    record["notes"], record["dt"], record["ppq"] = n, int(round(record["dt"] * ppq / old)), ppq
    return True


def retrack(record, moved):
    """Tracks added / removed / moved above in Domino: moved = {old track number: new one} (a track gone: left
    as it was, its notes then won't be found)."""
    n = record["notes"]
    new = np.array([moved.get(int(t), int(t)) for t in n[:, 0]], np.int64)
    if (new != n[:, 0]).any():
        record["notes"] = n.copy()
        record["notes"][:, 0] = new
        return True
    return False


# --- kept beside the song (user 2026-11-06): <song file>.spiderweb ---
FILE_END = ".spiderweb"
FILE_VERSION = 2  # (2: notes packed; 1: lists, still read)


def file_for(song):
    return song + FILE_END if song else None


def _plain(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(type(o).__name__)


def _packed(r):
    """The record's notes packed (zlib'd int32 rows, base64), made once per notes array (a hunt 2026-11-06 find:
    a million-note record rewrote 24 MB of lists on every move of any shape)."""
    n = r["notes"]
    got = r.get("_packed")
    if got is None or got[0] is not n:
        got = (n, base64.b64encode(zlib.compress(n.astype(np.int32).tobytes(), 1)).decode("ascii"))
        r["_packed"] = got
    return got[1]


def save(song, records, ppq):
    """Write the song's records beside it (no records = the file removed). Raises OSError when it can't."""
    from files.safefile import write_text
    path = file_for(song)
    if not records:
        if os.path.exists(path):
            os.remove(path)
        return
    data = {"app": "Spiderweb", "what": "shapes remembered inside a Domino song", "version": FILE_VERSION,
            "ppq": ppq, "records": [{"shapes": r["shape"], "notes": _packed(r), "rows": len(r["notes"]),
                                     "dt": r["dt"], "dk": r["dk"]} for r in records]}
    write_text(path, json.dumps(data, default=_plain, separators=(",", ":")))


def load(song):
    """The records kept beside the song ([] when none / unreadable / newer): all asleep until looked at."""
    from notes.engine import clean_shape
    path = file_for(song)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("version") not in (1, 2):
        return []
    out = []
    for r in data.get("records") or []:
        try:
            shapes = [clean_shape(s) for s in r["shapes"]]
            if isinstance(r["notes"], str):
                want = int(r["rows"]) * 20  # (5 int32 a note: never unpacked past what the entry says it holds)
                if not 0 < want <= 2_000_000_000:
                    continue
                z = zlib.decompressobj()
                raw = z.decompress(base64.b64decode(r["notes"]), want)
                if len(raw) != want or z.unconsumed_tail:
                    continue
                notes = np.frombuffer(raw, np.int32).astype(np.int64).reshape(-1, 5)
            else:
                notes = np.asarray(r["notes"], np.int64).reshape(-1, 5)
            if not shapes or any(s is None for s in shapes) or not len(notes):
                continue  # (a damaged entry: a Take of its notes would bring nothing back, hunt 2026-11-06)
            rec = new_record(shapes, notes)
            rec["dt"], rec["dk"], rec["alive"] = int(r["dt"]), int(r["dk"]), False
            rec["ppq"] = data.get("ppq")
            out.append(rec)
        except (KeyError, TypeError, ValueError, zlib.error):
            continue
    return out
