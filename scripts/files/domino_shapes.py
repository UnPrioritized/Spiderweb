"""Shapes remembered inside Domino (Draw on Domino, user 2026-11-06): which of Domino's notes came from which
Spiderweb shape, found again from Domino's notes alone (read from its memory, files/domino_view.track_notes).

Domino moves notes one by one and makes NEW note objects when it moves them, so a shape is recognised by its
PATTERN: a record = the shape + the notes that landed (track, tick, gate, key, velocity). It is ALIVE while every
one of those notes is in Domino exactly, all shifted by one time + key amount (dt, dk: the shape follows), and
ASLEEP otherwise (single notes edited, part deleted: plain Domino notes). Asleep records wake up when the notes
come back (Domino's Ctrl+Z). A whole shape copied in Domino (the copy selected, the original still there) = a new
record. Two records never claim the same notes. Forgotten only when baked (window/domino_companion).
Plain Python + NumPy, no windows: the "finding" part, so another way of finding (native code) can replace it."""

import copy
import itertools

import numpy as np

_ids = itertools.count(1)
MAX_TRIES = 4000  # candidate offsets tried per record per look (a pattern of very common notes)


def new_record(shape, notes):
    """notes = (track, tick, gate, key, velocity) as they landed in Domino."""
    notes = np.asarray(notes, np.int64).reshape(-1, 5)
    return {"id": next(_ids), "shape": shape, "notes": notes, "dt": 0, "dk": 0, "alive": len(notes) > 0}


def _code(track, tick, key):
    return (track << 47) | ((tick & 0x7FFFFFFF) << 8) | (key & 0xFF)


class Notes:
    """Domino's notes of the tracks looked at, ready for "is this exact note there?" (track -> array of tick,
    gate, key, velocity, selected)."""

    def __init__(self, tracks):
        parts = [np.column_stack([np.full(len(a), t, np.int64), a[:, :5]]) for t, a in tracks.items() if len(a)]
        rows = np.concatenate(parts) if parts else np.zeros((0, 6), np.int64)
        code = _code(rows[:, 0], rows[:, 1], rows[:, 3])
        order = np.argsort(code, kind="stable")
        self.rows, self.code = rows[order], code[order]
        self.claimed = np.zeros(len(self.rows), bool)
        sel = self.rows[:, 5] != 0
        self.selected = np.flatnonzero(sel)

    def find(self, notes, dt, dk):
        """Index of each note shifted by (dt, dk) among Domino's (exact tick, gate, key, velocity), or None if any
        is missing / already claimed."""
        if not len(notes):
            return None
        tick, key = notes[:, 1] + dt, notes[:, 3] + dk
        if tick.min() < 0 or key.min() < 0 or key.max() > 255:
            return None
        c = _code(notes[:, 0], tick, key)
        at = np.searchsorted(self.code, c)
        if at.max() >= len(self.code):
            return None
        # (two notes on one tick + key in Domino: look at the next one too)
        for _ in range(3):
            ok = self.code[at] == c
            ok &= (self.rows[at, 2] == notes[:, 2]) & (self.rows[at, 4] == notes[:, 4]) & ~self.claimed[at]
            if ok.all():
                return at
            bad = ~ok & (at + 1 < len(self.code))
            if not bad.any() or not (self.code[np.minimum(at + 1, len(self.code) - 1)][bad] == c[bad]).any():
                return None
            at = np.where(bad, np.minimum(at + 1, len(self.code) - 1), at)
        return None

    def candidates(self, notes, only_selected):
        """(dt, dk) offsets that put the pattern's rarest note on a Domino note just like it."""
        pick = self.selected if only_selected else np.arange(len(self.rows))
        if not len(pick) or not len(notes):
            return []
        pool = self.rows[pick]
        # the record's note whose (track, gate, velocity) Domino has fewest of
        best, best_n, best_hit = None, None, None
        for i in range(min(len(notes), 64)):
            n = notes[i]
            hit = (pool[:, 0] == n[0]) & (pool[:, 2] == n[2]) & (pool[:, 4] == n[4])
            cnt = int(hit.sum())
            if best_n is None or cnt < best_n:
                best, best_n, best_hit = i, cnt, hit
                if cnt <= 1:
                    break
        if not best_n:
            return []
        n = notes[best]
        got = pool[best_hit]
        offs = np.column_stack([got[:, 1] - n[1], got[:, 3] - n[3]])
        return [tuple(map(int, o)) for o in np.unique(offs, axis=0)[:MAX_TRIES]]


def look(records, tracks):
    """Match the records against Domino's notes (tracks: track -> array of tick, gate, key, velocity, selected).
    Records are changed in place (dt, dk, alive); returns (events, new records): events = (what, record) with what
    "follow" (moved), "sleep", "wake"; new records = whole shapes copied inside Domino (also in events as "copy")."""
    dom = Notes(tracks)
    events, made = [], []
    todo = [r for r in records if len(r["notes"])]
    # 1: records still exactly where they were claim their notes first
    rest = []
    for r in todo:
        at = dom.find(r["notes"], r["dt"], r["dk"])
        if at is None:
            rest.append(r)
            continue
        dom.claimed[at] = True
        if not r["alive"]:
            r["alive"] = True
            events.append(("wake", r))
    # 2: the others: moved (the selected notes first: Domino keeps what it moved selected), else asleep
    for r in rest:
        found = None
        for only_sel in (True, False):
            offs = dom.candidates(r["notes"], only_sel)
            offs.sort(key=lambda o: abs(o[0] - r["dt"]) + abs(o[1] - r["dk"]) * 10)
            for o in offs:
                at = dom.find(r["notes"], *o)
                if at is not None:
                    found = (o, at)
                    break
            if found:
                break
        if found:
            (r["dt"], r["dk"]), at = found
            dom.claimed[at] = True
            events.append(("wake" if not r["alive"] else "follow", r))
            r["alive"] = True
        elif r["alive"]:
            r["alive"] = False
            events.append(("sleep", r))
    # 3: whole copies made in Domino: selected notes forming another alive record's pattern
    if len(dom.selected):
        for r in [r for r in todo if r["alive"]]:
            for o in dom.candidates(r["notes"], True):
                if o == (r["dt"], r["dk"]):
                    continue
                at = dom.find(r["notes"], *o)
                if at is not None and dom.rows[at, 5].all():
                    dom.claimed[at] = True
                    c = new_record(copy.deepcopy(r["shape"]), r["notes"])
                    c["dt"], c["dk"] = o
                    made.append(c)
                    events.append(("copy", c))
    return events, made


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
    n = record["notes"]
    tick, key = n[:, 1] + record["dt"], n[:, 3] + record["dk"]
    at = np.searchsorted(dom.code, _code(n[:, 0], tick, key))
    at = np.minimum(at, max(0, len(dom.code) - 1))
    if not len(dom.code):
        return False
    same = (dom.code[at] == _code(n[:, 0], tick, key)) & (dom.rows[at, 4] == n[:, 4])
    if not same.all():
        return False
    new = dom.rows[at, 2]
    if (new == n[:, 2]).all():
        return False
    record["notes"] = n.copy()
    record["notes"][:, 2] = new
    return True
