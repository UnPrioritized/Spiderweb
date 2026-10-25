"""Saved indexes of loaded MIDIs (Load MIDI group E step 12; user 2026-10-23: asked first, user/midi-index/).
Loading reads a whole file once to learn where everything is (Shinki: ~30 s, the disk); the index it leaves
(checkpoints, meta events, small pictures, the overview: Shinki ~130 MB) can be kept, so opening the same file
again ("Read it from the MIDI file") takes a moment. Never the notes themselves.

One .npz per MIDI (name from its path + size), its "head" = JSON: the MIDI's path, size, time, the layout of its
chunks, pairing rule, index version and every track's numbers; the arrays of all tracks put end to end per kind.
Used only while the MIDI's size + time and chunk layout are the same as when it was saved (else it's left alone,
read the usual way, and offered again). declined.json = the files the user said not to ask about again."""

import hashlib
import io
import json
import os

import numpy as np

from files.about import USER
from files.safefile import write_bytes, write_text

FOLDER = os.path.join(USER, "midi-index")
INDEX_VER = 4  # (2: the overview's edges, 3-4: instruments in the counts, 2026-10-25; older = doesn't fit, offered again)
SLOW_S = 10.0  # a load taking longer than this offers to save its index
DECLINED = "declined.json"
ARRAYS = ("chn", "cp_off", "cp_tick", "cp_run", "cp_ent", "e_q", "e_cnt", "e_end", "e_vel", "m_tick", "m_type",
          "m_len", "m_data", "pic")
SCALARS = ("number", "notes", "zero", "hang", "orphan", "long", "klo", "khi", "first", "end", "last_tick", "channel",
           "name", "pic_klo", "pic_glo", "pic_pf", "pic_cols", "limit", "stopped")


def index_file(path, size):
    """Where the index of the MIDI at path (size bytes) is kept."""
    key = f"{os.path.normcase(os.path.abspath(path))}|{size}".encode("utf-8", "surrogatepass")
    return os.path.join(FOLDER, hashlib.blake2b(key, digest_size=10).hexdigest() + ".npz")


def _plain(v):
    """A TrackInfo number as JSON takes it."""
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    return int(v) if isinstance(v, np.integer) else v


def _layout(mf):
    return [[t.head, t.start, t.size, t.declared] for t in mf.tracks]


def save_index(lm):
    """Writes lm's index (a loaded MIDI read from its file). Returns its size in bytes."""
    from notes.loaded import PAIRING
    mf = lm.mf
    head = {"ver": INDEX_VER, "pairing": PAIRING, "path": mf.path, "size": mf.size, "mtime": mf.mtime,
            "layout": _layout(mf), "G": lm.G, "seconds": lm.seconds, "raw_hash": mf.raw_hash,
            "marks": sorted(mf.marks), "resynced": list(mf.resynced),
            "tracks": [{"flags": int(t.flags), "counts": t.counts.tolist() if t.counts is not None else None,
                        "last_tick": int(t.last_tick), "after": int(t.after), "hash": t.hash} for t in mf.tracks],
            "infos": [{k: _plain(getattr(i, k)) for k in SCALARS} for i in lm.tracks],
            "shapes": {k: [list(getattr(i, k).shape) for i in lm.tracks] for k in ARRAYS}}
    level0 = lm._level0 if lm._level0 is not None else lm.levels[0]  # (as loaded: never the Extract one)
    arrays = {"head": np.frombuffer(json.dumps(head).encode("utf-8"), np.uint8), "cnt": level0[0], "top": level0[1],
              "edges": np.concatenate([e.reshape(-1) for e in lm.edges])}  # (every level: made from a grid not kept)
    for k in ARRAYS:
        parts = [getattr(i, k).reshape(-1) for i in lm.tracks]
        arrays[k] = np.concatenate(parts) if parts else np.zeros(0, np.int64)
    buf = io.BytesIO()
    np.savez(buf, **arrays)
    data = buf.getvalue()
    os.makedirs(FOLDER, exist_ok=True)
    write_bytes(index_file(mf.path, mf.size), data)
    return len(data)


def _fits(head, mf):
    """A saved index's head is for mf as it is now."""
    from notes.loaded import PAIRING
    return (head.get("ver") == INDEX_VER and head.get("pairing") == PAIRING and head.get("size") == mf.size
            and head.get("mtime") == mf.mtime and head.get("layout") == _layout(mf))


def has_index(mf):
    """mf (laid out) has a saved index it can be opened from (only its head is read: the load window says so)."""
    try:
        with np.load(index_file(mf.path, mf.size), allow_pickle=False) as z:
            return _fits(json.loads(bytes(z["head"]).decode("utf-8")), mf)
    except Exception:  # (none, or a damaged file: BadZipFile, EOFError... = none, hunt 2026-10-25)
        return False


def load_index(mf):
    """The LoadedMidi of mf (laid out, not scanned) from its saved index, or None (none, or not this file as it is
    now: then the usual read)."""
    from notes.loaded import LoadedMidi, TrackInfo
    from files import midi_index, speed
    path = index_file(mf.path, mf.size)
    try:
        with np.load(path, allow_pickle=False) as z:
            head = json.loads(bytes(z["head"]).decode("utf-8"))
            if not _fits(head, mf):
                return None
            arrays = {k: z[k] for k in ARRAYS}
            cnt, top, edges = z["cnt"], z["top"], z["edges"]
    except Exception:  # (damaged too: read the usual way)
        return None
    for t, s in zip(mf.tracks, head["tracks"]):
        t.flags, t.last_tick, t.after, t.hash = s["flags"], s["last_tick"], s["after"], s["hash"]
        t.counts = np.array(s["counts"], np.int64) if s["counts"] is not None else None
    mf.raw_hash, mf.resynced = head["raw_hash"], list(head["resynced"])
    mf.marks |= set(head["marks"])
    mf.scanned = True
    infos = []
    at = {k: 0 for k in ARRAYS}
    for n, sc in enumerate(head["infos"]):
        i = TrackInfo()
        for k in SCALARS:
            setattr(i, k, sc[k])
        for k in ARRAYS:
            shape = head["shapes"][k][n]
            size = int(np.prod(shape)) if shape else 1
            setattr(i, k, arrays[k][at[k]:at[k] + size].reshape(shape))
            at[k] += size
        i.data = None
        infos.append(i)
    loops = speed.loops()
    halve = loops.midi_halve if loops else midi_index.halve
    levels = [(cnt, top)]
    while cnt.shape[1] > 1:
        n2 = (cnt.shape[1] + 1) // 2
        c2, t2 = np.zeros((cnt.shape[0], n2), np.int32), np.zeros((cnt.shape[0], n2), np.int32)
        halve(cnt, top, c2, t2)
        levels.append((c2, t2))
        cnt, top = c2, t2
    cut = np.cumsum([0] + [t.size for c, t in levels])
    if cut[-1] != edges.size:  # (damaged: read the usual way)
        return None
    edges = [edges[a:b].reshape(t.shape) for a, b, (c, t) in zip(cut[:-1], cut[1:], levels)]
    return LoadedMidi(mf, "file", head["G"], infos, levels, head["seconds"], edges)


def saved_list():
    """[(MIDI path, index file, bytes)] of every saved index, newest first."""
    out = []
    try:
        names = [n for n in os.listdir(FOLDER) if n.endswith(".npz")]
    except OSError:
        return out
    for n in names:
        f = os.path.join(FOLDER, n)
        try:
            with np.load(f, allow_pickle=False) as z:
                path = json.loads(bytes(z["head"]).decode("utf-8")).get("path", "")
        except Exception:  # (damaged: listed by its own name, so it can be deleted)
            path = f
        try:
            out.append((os.path.getmtime(f), path, f, os.path.getsize(f)))
        except OSError:
            continue
    return [(p, f, s) for _, p, f, s in sorted(out, reverse=True)]


def delete_index(f):
    try:
        os.remove(f)
    except OSError:
        pass


def _declined():
    try:
        with open(os.path.join(FOLDER, DECLINED), encoding="utf-8") as fh:
            got = json.load(fh)
        return set(got) if isinstance(got, list) else set()
    except (OSError, ValueError):
        return set()


def offer(lm):
    """After a load: offer to save its index? (read from the file, slow, none saved that fits it now: an old one
    for the file before it changed is saved over, hunt 2026-10-25; not declined)"""
    return (lm.mode == "file" and lm.seconds > SLOW_S and not has_index(lm.mf)
            and os.path.normcase(os.path.abspath(lm.path)) not in _declined())


def decline(path):
    """Never offered again for this MIDI ("Don't ask again for this file")."""
    got = _declined() | {os.path.normcase(os.path.abspath(path))}
    os.makedirs(FOLDER, exist_ok=True)
    write_text(os.path.join(FOLDER, DECLINED), json.dumps(sorted(got)))
