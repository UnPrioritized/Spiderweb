"""Saved indexes of loaded MIDIs (Load MIDI group E step 12; user 2026-10-23: asked first, user/midi-index/).
Loading reads a whole file once to learn where everything is (Shinki: ~30 s, the disk); the index it leaves
(checkpoints, meta events, small pictures, the overview: Shinki ~7.5 MB compressed, 121 MB not) can be kept, so opening the same file
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
INDEX_VER = 6  # (2: the overview's edges, 3-4: instruments in the counts, 2026-10-25, 5: CC events 2026-10-29,
# 6: midi_read.TrackHash fingerprints 2026-11-25; older = doesn't fit, offered again)
SLOW_S = 10.0  # a load taking longer than this offers to save its index
DECLINED = "declined.json"
ARRAYS = ("chn", "cp_off", "cp_tick", "cp_run", "cp_ent", "e_q", "e_cnt", "e_end", "e_vel", "m_tick", "m_type",
          "m_len", "m_data", "pic", "c_tick", "c_code", "c_kinds")
SCALARS = ("number", "notes", "zero", "hang", "orphan", "long", "klo", "khi", "first", "end", "last_tick", "channel",
           "name", "pic_klo", "pic_glo", "pic_pf", "pic_cols", "limit", "stopped", "cc_over")


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


# (a size guess compresses this many slices of this many bytes a stream, measured on Shinki: its index's numbers
# change a lot along the song = many small slices (7.7 MB guessed, 7.5 real; 1 MB slices said 11.8); its read-ahead
# has long repeats = big slices (292 MB guessed, 292 real; 16 KB slices said 471))
INDEX_SLICES, AHEAD_SLICES = (256, 1 << 14), (16, 1 << 20)


PACK_PIECE = 4 << 20  # (bytes of an array compressed as one piece on its own thread)


def write_npz(f, items, workers=None):
    """What np.savez_compressed writes (a zip of .npy files, deflated; np.load reads it) into the open file f, each
    array's PACK_PIECE pieces compressed side by side on half the cores (user 2026-11-25: Kestrel's read-ahead ~13.5 s
    on one). items = [(name, make)], make() = the array (made one at a time: byte rows, never all at once). The pieces
    join into one deflate stream (each ends on a byte boundary, Z_SYNC_FLUSH; the last finishes it): a few bytes more
    per piece. Every size in the zip64 form."""
    import struct
    import zlib
    from concurrent.futures import ThreadPoolExecutor
    workers = workers or max(1, (os.cpu_count() or 2) // 2)
    when = (0, (0 << 9) | (1 << 5) | 1)  # (1 Jan 1980 as np.savez wrote: the clock never read; a PC dated before
    # 1980 / after 2107 couldn't save, hunt 2026-11-25)

    def pack(piece, last):
        c = zlib.compressobj(6, zlib.DEFLATED, -15)
        return c.compress(piece) + c.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)
    central = []
    start = f.tell()
    with ThreadPoolExecutor(workers, thread_name_prefix="npz-pack") as pool:
        for name, make in items:
            a = np.ascontiguousarray(make())
            hb = io.BytesIO()
            np.lib.format.write_array_header_1_0(hb, np.lib.format.header_data_from_array_1_0(a))
            raw = [hb.getvalue()]
            body = memoryview(a.reshape(-1).view(np.uint8)) if a.size else memoryview(b"")
            raw += [body[i:i + PACK_PIECE] for i in range(0, len(body), PACK_PIECE)]
            jobs = [pool.submit(pack, p, k == len(raw) - 1) for k, p in enumerate(raw)]
            crc = 0
            for p in raw:  # (meanwhile, on this thread)
                crc = zlib.crc32(p, crc)
            packed = [j.result() for j in jobs]
            plain, size = sum(len(p) for p in raw), sum(len(p) for p in packed)
            nm = (name + ".npy").encode("utf-8")
            at = f.tell() - start
            f.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 45, 0, 8, when[0], when[1], crc, 0xFFFFFFFF, 0xFFFFFFFF,
                                len(nm), 20) + nm + struct.pack("<HHQQ", 1, 16, plain, size))
            for p in packed:
                f.write(p)
            central.append(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 45, 45, 0, 8, when[0], when[1], crc,
                                       0xFFFFFFFF, 0xFFFFFFFF, len(nm), 28, 0, 0, 0, 0, 0xFFFFFFFF) + nm
                           + struct.pack("<HHQQQ", 1, 24, plain, size, at))
            del a, raw, packed, body
    cd_at = f.tell() - start
    for c in central:
        f.write(c)
    cd_size = f.tell() - start - cd_at
    end64 = f.tell() - start
    n = len(central)
    f.write(struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, n, n, cd_size, cd_at))
    f.write(struct.pack("<IIQI", 0x07064B50, 0, end64, 1))
    f.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, min(n, 0xFFFF), min(n, 0xFFFF), 0xFFFFFFFF, 0xFFFFFFFF, 0))


def byte_rows(a):
    """a's numbers as rows of bytes (every number's 1st byte, then every 2nd...): Kestrel's read-ahead messages + copy
    counts compress far better so (PLAN stage C 2026-11-25, Shinki 292 -> 178 MB, quicker too)."""
    a = np.ascontiguousarray(a).reshape(-1)
    return np.ascontiguousarray(a.view(np.uint8).reshape(-1, a.itemsize).T)


def from_rows(r, dtype):
    """byte_rows back."""
    return np.ascontiguousarray(r.T).view(dtype).reshape(-1)


def packed_guess(streams, slices=INDEX_SLICES, rows=()):
    """About the bytes streams (lists of arrays, each list read as one run of bytes) take compressed as np.savez_
    compressed writes them: small ones compressed whole, big ones from slices (how many, bytes) spread over them
    (Shinki's read-ahead: 3 GB, ~290 MB compressed; the question asks before saving, user 2026-11-25: "about").
    rows = places in streams saved as byte_rows."""
    import zlib
    GUESS_SLICES, GUESS_SLICE = slices
    total = 0
    work = []  # (byte rows: slices whole numbers, split each; parts of each number size apart: a read-ahead's copies
    # are 2 or 4 bytes a part, hunt 2026-11-25 = a crash when the first part's size was taken for all)
    for s, parts in enumerate(streams):
        if s in rows and parts:
            work += [([a for a in parts if a.itemsize == w], w) for w in sorted({a.itemsize for a in parts})]
        else:
            work.append((parts, 1))
    for parts, w in work:
        parts = [np.ascontiguousarray(a).reshape(-1).view(np.uint8) for a in parts]
        size = sum(len(p) for p in parts)
        if size <= GUESS_SLICES * GUESS_SLICE:  # (whole, as one run: saved joined)
            total += len(zlib.compress(b"".join((p.reshape(-1, w).T if w > 1 else p).tobytes() for p in parts), 6))
            continue
        if len(parts) > 1 and size // len(parts) < GUESS_SLICE:  # (many small pieces, thousands of tracks: joined
            # as saved; slices cut at each piece's end guessed 30x high, hunt 2026-11-25)
            parts = [np.concatenate(parts)]
        ends = np.cumsum([len(p) for p in parts])
        packed = taken = 0
        for at in np.linspace(0, size - GUESS_SLICE, GUESS_SLICES).astype(np.int64).tolist():
            n = int(np.searchsorted(ends, at, "right"))  # (the part holding at; a slice stops at its end)
            p = parts[n]
            a = at - (int(ends[n - 1]) if n else 0)
            a -= a % w
            piece = p[a:a + GUESS_SLICE - GUESS_SLICE % w]
            if w > 1:
                piece = np.ascontiguousarray(piece[:len(piece) - len(piece) % w].reshape(-1, w).T)
            packed += len(zlib.compress(piece, 6))
            taken += piece.size
        total += int(size * packed / max(1, taken))
    return total


def _arrays(lm):
    """The arrays an index is saved as."""
    level0 = lm._level0 if lm._level0 is not None else lm.levels[0]  # (as loaded: never the Extract one)
    arrays = {"head": _head_bytes(lm), "cnt": level0[0], "top": level0[1],
              "edges": np.concatenate([e.reshape(-1) for e in lm.edges])}  # (every level: made from a grid not kept)
    for k in ARRAYS:
        parts = [getattr(i, k).reshape(-1) for i in lm.tracks]
        arrays[k] = np.concatenate(parts) if parts else np.zeros(0, np.int64)
    return arrays


def disk_guess(lm, ahead=True):
    """About the bytes Save Index writes for lm: its index compressed (+ Kestrel's read-ahead with it, asked first)."""
    from files.play_dense import kept_bytes
    level0 = lm._level0 if lm._level0 is not None else lm.levels[0]
    streams = [[level0[0]], [level0[1]], list(lm.edges)] + [[getattr(i, k) for i in lm.tracks] for k in ARRAYS]
    # (+ the head: its per-track text is most of it past thousands of tracks, hunt 2026-11-25)
    return packed_guess(streams + [[_head_bytes(lm)]]) + (kept_bytes(lm) if ahead else 0)


def _head_bytes(lm):
    """The index's head (JSON) as saved."""
    from notes.loaded import PAIRING
    mf = lm.mf
    head = {"ver": INDEX_VER, "pairing": PAIRING, "path": mf.path, "size": mf.size, "mtime": mf.mtime,
            "layout": _layout(mf), "G": lm.G, "seconds": lm.seconds, "raw_hash": mf.raw_hash,
            "marks": sorted(mf.marks), "resynced": list(mf.resynced),
            "tracks": [{"flags": int(t.flags), "counts": t.counts.tolist() if t.counts is not None else None,
                        "last_tick": int(t.last_tick), "after": int(t.after), "hash": t.hash} for t in mf.tracks],
            "infos": [{k: _plain(getattr(i, k)) for k in SCALARS} for i in lm.tracks],
            "shapes": {k: [list(getattr(i, k).shape) for i in lm.tracks] for k in ARRAYS}}
    return np.frombuffer(json.dumps(head).encode("utf-8"), np.uint8)


def save_index(lm, ahead=False):
    """Writes lm's index (a loaded MIDI read from its file), compressed (user 2026-11-25: Shinki 121 -> 7.5 MB, saved
    and read as quickly). ahead = the question said Kestrel's read-ahead's size too: it's written with it, in the
    background (lm.dense_job = [thread, [bytes written or False] once done, index bytes]); else a read-ahead all read since is ASKED about on
    its own (hunt 2026-11-25: written unasked). Returns the index's size (+ the read-ahead's guess when written)."""
    data = io.BytesIO()
    write_npz(data, [(k, lambda a=a: a) for k, a in _arrays(lm).items()])  # (older uncompressed ones open the
    # same way: kept, user)
    data = data.getvalue()
    os.makedirs(FOLDER, exist_ok=True)
    write_bytes(index_file(lm.mf.path, lm.mf.size), data)
    from files.play_dense import kept_bytes, may_keep, save
    d = getattr(lm, "dense", None)
    more = kept_bytes(lm) if ahead else 0
    lm.dense_job = None
    if more:  # (all read already, written in the background: Shinki's ~3 GB held the window, hunt 2026-10-28)
        import threading
        d.unsaved = False
        got = []
        th = threading.Thread(target=lambda: got.append(save(d)), daemon=True, name="kestrel-dense-save")
        lm.dense_job = [th, got, len(data)]
        th.start()
    elif d is not None and d.done() and may_keep(d):
        d.unsaved = True  # (App.watch_ahead asks: "Keep it / Don't keep it / Not now")
    return len(data) + more


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
    from notes.loaded import LoadedMidi, TrackInfo, halved_levels
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
    levels = halved_levels(cnt, top)
    cut = np.cumsum([0] + [t.size for c, t in levels])
    if cut[-1] != edges.size:  # (damaged: read the usual way)
        return None
    edges = [edges[a:b].reshape(t.shape) for a, b, (c, t) in zip(cut[:-1], cut[1:], levels)]
    return LoadedMidi(mf, "file", head["G"], infos, levels, head["seconds"], edges)


def saved_list():
    """[(MIDI path, index file, bytes)] of every saved index, newest first."""
    from files.play_dense import SAVED_END, leftovers, saved_file
    leftovers(FOLDER)
    out = []
    try:
        names = [n for n in os.listdir(FOLDER) if n.endswith(".npz") and not n.endswith(SAVED_END)]
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
            size = os.path.getsize(f)
            if os.path.exists(saved_file(f)):  # (Kestrel's read-ahead kept with it: counted in its size)
                size += os.path.getsize(saved_file(f))
            out.append((os.path.getmtime(f), path, f, size))
        except OSError:
            continue
    return [(p, f, s) for _, p, f, s in sorted(out, reverse=True)]


def delete_index(f):
    from files.play_dense import SAVING, declined_file, saved_file
    half = saved_file(f) + ".tmp"  # (one being written now: its save drops it once it sees the index gone)
    for g in (f, saved_file(f), declined_file(f)) + (() if half in SAVING else (half,)):  # (its read-ahead too)
        try:
            os.remove(g)
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
