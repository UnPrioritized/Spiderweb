"""Kestrel (GPU) and a loaded MIDI's densest parts (user 2026-10-27: live as close to their 2B-voice render as can
be). Where the file's bytes come faster than the live reader keeps up (DENSE_MBS a song second, worked out from every
track's checkpoints: Shinki's beats 1123-1126 = its run-up + wall, 4.6 GB in 1.2 s; there the reader fell 2.8 s
behind and 142 M notes came too late), the part is read ONCE ahead, in the background, its exact copies collapsed as
midi_play.stamp does live (Shinki's beats 1123-1126: 1.2 billion events -> 10.6 M, ~14 s on 4 threads), and the
Kestrel feeder plays it from that (midi_play.stamp_pre; play_loaded's reader steps over it). Kept with the loaded MIDI
(lm.dense) while its hidden notes, removed tracks and track channels stay as they were. Muted tracks: that play reads
the parts live as before (the read-ahead holds every track)."""

import json
import os
import threading
import time

import numpy as np

from files import midi_play, speed

DENSE_MBS = 100  # MB of the file a song second past which a part is read ahead (2026-10-28: the live feeder falls
# behind from ~100: Shinki's spam before its wall, 120-200, reached Kestrel in late lumps; was 200)
PART_MB = 600  # MB of the file a part holds at most (cut there: the spam and the wall joined went past PART_MOST)
DENSE_SHARE = 0.25  # of the free memory when it starts, the read-ahead keeps at most this (user 2026-10-28: "capped";
# the densest parts first, the rest read live as before)
JOIN_BEATS = 1.0  # dense stretches closer than this are one part
EARLY_BEATS = 0.25  # each part starts this much before its first dense stretch
PART_MOST = 1 << 26  # events a part may keep after collapsing (~1.2 GB): more = that part read live as before
GROUP_TRACKS = 64  # one tick holding more than a reading window: its tracks read this many at a time
SLICE = 1 << 22  # events collapsed at a time (room kept for this many more)
SAVED_VER = 3  # (a saved read-ahead: how parts are read / collapsed changed = a new number, old ones read again)
SAVED_END = ".dense.npz"  # (beside the saved index: <index name>.dense.npz)
DECLINED_END = ".dense.no"  # (beside it: the user said No to keeping it)
PART_ARRAYS = ("ts", "ms", "tr", "cp", "s0_q", "s0_n")  # (each part's, saved as <name><part number>)


def stretches(lm, tmap):
    """[(t0, t1)] ticks of the parts to read ahead (plan's)."""
    return plan(lm, tmap)[0]


def plan(lm, tmap):
    """([(t0, t1)] ticks of the parts to read ahead, [MB of file a song second each]): bytes of each track between
    its checkpoints (64 KB apart) spread evenly over their ticks, summed per 1/16 beat, over DENSE_MBS a song
    second; stretches closer than JOIN_BEATS joined, then cut so none holds more than PART_MB."""
    G = max(1, lm.ppq // 16)
    nb = lm.last_tick // G + 2
    dens = np.zeros(nb + 1, np.float64)
    for n, tr in enumerate(lm.tracks):
        if n in lm.gone or not len(tr.cp_tick):
            continue
        ticks = np.append(tr.cp_tick, tr.last_tick + 1).astype(np.int64)
        offs = np.append(tr.cp_off, tr.limit).astype(np.float64)
        a = ticks[:-1] // G
        b = np.maximum(ticks[1:] // G, a + 1)
        per = (offs[1:] - offs[:-1]) / (b - a)
        np.add.at(dens, np.minimum(a, nb), per)
        np.add.at(dens, np.minimum(b, nb), -per)
    per_g = np.cumsum(dens)[:nb]
    edges = np.array([tmap.sec(int(k) * G) for k in range(nb + 1)])
    over = np.flatnonzero(per_g / np.maximum(np.diff(edges), 1e-9) > DENSE_MBS * 1e6)
    out = []
    for k in over.tolist():
        t0, t1 = k * G, (k + 1) * G
        if out and t0 - out[-1][1] <= JOIN_BEATS * lm.ppq:
            out[-1][1] = t1
        else:
            out.append([t0, t1])
    early = int(EARLY_BEATS * lm.ppq)
    parts, rates = [], []
    for a, b in out:
        a = max(0, a - early)
        k0, k1 = a // G, max(a // G + 1, b // G)
        cum = np.cumsum(per_g[k0:k1])
        start, used = a, 0.0
        for j in range(k1 - k0):
            if cum[j] - used > PART_MB * 1e6 and (k0 + j) * G > start:  # (cut before this 1/16 beat)
                cut = (k0 + j) * G
                parts.append((start, cut))
                start, used = cut, cum[j - 1] if j else 0.0
        parts.append((start, b))
    for a, b in parts:
        k0, k1 = a // G, max(a // G + 1, b // G)
        rates.append(float(per_g[k0:k1].sum()) / 1e6 / max(1e-9, float(edges[k1] - edges[k0])))
    return parts, rates


def key_of(lm):
    """What a read-ahead depends on besides the file."""
    return lm.hide_ver, lm.gone, None if lm.rechannel is None else tuple(lm.rechannel)


class Part:
    """One part read ahead: ticks t0..t1; ts (int32, from t0), ms (message, port in the top byte), tr (the note
    budget's channel + port), cp (copies) as condense made them, every note-off kept (the feeder's line leaves out
    the ones of notes it left out); s0_q / s0_n = the file's notes sounding at t0 (their offs are in it)."""

    def __init__(self, t0, t1, ts, ms, tr, cp, s0=None, s0_q=None, s0_n=None):
        self.t0, self.t1 = t0, t1
        self.ts, self.ms, self.tr, self.cp = ts, ms, tr, cp
        self.s0_q = np.flatnonzero(s0) if s0 is not None else s0_q
        self.s0_n = s0[self.s0_q] if s0 is not None else s0_n

    def unheard(self, tick):
        """Notes sounding at tick inside the part (SKIP places): the file's from before t0 still on, then its own
        (copies counted). Playing starts at tick: these offs are left out (hunt 2026-10-27: counting only its own,
        the earlier notes' offs came through and ended other notes early)."""
        from files.play_loaded import SKIP
        skip = np.zeros(SKIP, np.int64)
        skip[self.s0_q] = self.s0_n
        line = midi_play.Line(midi_play.OPEN + 1)  # (dry: every note left out, a key's runs one node)
        line.add(skip)
        i = int(np.searchsorted(self.ts, tick - self.t0, "left"))
        if i:
            _stamp_pre()(self.ts, self.ms, self.tr, self.cp, self.t0, 0, i, np.zeros(1, np.int64),
                         np.zeros(1, np.float64), np.zeros(1, np.float64), 0.0, 0.0, 0.0, np.zeros(0, np.uint64),
                         np.zeros(0, np.uint32), np.zeros(0, np.uint16), np.zeros(0, np.uint32),
                         np.zeros(3, np.int64), True, *line.arrays())
        return line.left_out()


def _stamp_pre():
    fast = speed.loops()
    return fast.play_stamp_pre if fast is not None else midi_play.stamp_pre


def _condense():
    fast = speed.loops()
    return fast.play_condense if fast is not None else midi_play.condense


class Dense:
    """The read-ahead of one loaded MIDI's dense parts, on its own thread; `parts` grows in song order as each is
    done (read by the player's reader: whole parts only, never one half made)."""

    def __init__(self, lm, tmap):
        self.lm, self.key = lm, key_of(lm)
        self.todo, self.rates = plan(lm, tmap)
        self.parts = []
        self.failed = 0  # (parts left to be read live: too many events kept, reading failed, or over the memory cap)
        self.capped = 0  # (... of them over the memory cap)
        self.error = None
        self.unsaved = False  # (all read, not kept on disk yet, may be offered: the user is asked first)
        self.took = 0.0  # (seconds the reading took)
        self.stale = False  # (read here, not from a kept one: a kept file beside the index is out of date)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="kestrel-dense")
        self.thread.start()

    def done(self):
        return not self.thread.is_alive()

    def stop(self):
        self._stop.set()

    def part_at(self, tick):
        """The part read ahead holding tick, else None."""
        for p in tuple(self.parts):
            if p.t0 <= tick < p.t1:
                return p
        return None

    def start_in(self, t0, t1):
        """The first part read ahead starting after t0, before t1 (a reading window stops there), else None."""
        for p in tuple(self.parts):
            if t0 < p.t0 < t1:
                return p.t0
        return None

    def _run(self):
        from concurrent.futures import ThreadPoolExecutor
        from files.play_loaded import WINDOW_MOST, LoadedPlayer, _Scratch, helper_count
        lm = self.lm
        began = time.perf_counter()
        saved = _load(lm, self.todo)
        if saved is not None:  # (read ahead on an earlier day, kept beside the MIDI's saved index)
            self.failed = saved[1]
            self.parts = saved[0]
            return
        self.stale = True  # (one kept beside it, if any, can't be used: may_keep offers it again)
        rd = LoadedPlayer(lm, None, None, None, "kestrel")
        path = lm.path if lm.mode != "memory" else None
        try:
            rd._sc = _Scratch(path)
            rd._file = rd._sc.file
            rd._helpers = [_Scratch(path) for _ in range(helper_count())]
            rd._pool_x = ThreadPoolExecutor(max(1, len(rd._helpers)), thread_name_prefix="kestrel-dense")
            rd._table()
            from files.system import free_memory
            free = free_memory()
            cap = DENSE_SHARE * free if free else float("inf")  # (not known: no cap, as before)
            used = 0
            # the densest first (the wall before the spam leading to it): the memory cap leaves the lighter ones live
            for k in sorted(range(len(self.todo)), key=lambda k: -self.rates[k]):
                t0, t1 = self.todo[k]
                if self._stop.is_set():
                    return
                if used >= cap:
                    self.failed += 1
                    self.capped += 1
                    continue
                part = self._part(rd, t0, t1, WINDOW_MOST)
                if part is None:
                    self.failed += 1
                else:
                    used += sum(a.nbytes for a in (part.ts, part.ms, part.tr, part.cp))
                    # (in song order; a whole new list: the player's reader never sees one half made)
                    self.parts = sorted(self.parts + [part], key=lambda p: p.t0)
            # (kept on disk only when the user says so: App.ask_keep_ahead; it wrote 3 GB unasked, user 2026-10-28)
            self.took = time.perf_counter() - began
            self.unsaved = not self._stop.is_set() and may_keep(self)
        except Exception as e:  # (a file gone / changed: the parts not done are read live as before)
            self.error = e
            from files.errors import write_log
            import sys
            write_log(*sys.exc_info(), "(Kestrel: reading a dense part ahead)")
        finally:
            if rd._pool_x is not None:
                rd._pool_x.shutdown(wait=True)
            for sc in [rd._sc] + rd._helpers:
                sc.close()

    def _part(self, rd, t0, t1, most):
        """One part read and collapsed (None: stopped, or too much kept)."""
        from files.play_loaded import SKIP
        condense = _condense()
        s0, _ = rd._place(t0)  # (the notes sounding at t0: only for a play starting inside the part, unheard)
        last_on = np.full(SKIP, -1, np.int64)
        counts = np.array([0, 0, 0, -1], np.int64)
        tab = (np.zeros(midi_play.COPY_TABLE, np.uint32), np.zeros(midi_play.COPY_TABLE, np.int64),
               np.zeros(midi_play.COPY_TABLE, np.int64))
        cap = 1 << 22
        out = (np.empty(cap, np.int64), np.empty(cap, np.uint32), np.empty(cap, np.uint16), np.empty(cap, np.uint32))
        n = 0
        span = max(1, rd.lm.ppq // 8)
        w0 = t0

        def room(n, more):
            nonlocal out, cap
            if n + more <= cap:
                return True
            if n + more > PART_MOST:
                return False
            cap = max(n + more, 2 * cap)
            out = tuple(np.concatenate([a[:n], np.empty(cap - n, a.dtype)]) for a in out)
            return True

        def take(ts, ms, ks):
            """Events collapsed into out, a slice at a time (a wall's read is far bigger than what it leaves)."""
            nonlocal n
            for s in range(0, len(ts), SLICE):
                z = min(SLICE, len(ts) - s)
                if not room(n, z):
                    return False
                n = condense(ts[s:s + z], ms[s:s + z], ks[s:s + z], rd.tport, n, *out, *tab, counts, last_on)
            return True
        while w0 < t1:
            if self._stop.is_set():
                return None
            w1 = min(w0 + span, t1)
            todo = np.flatnonzero(~rd.done & (rd.nxt < w1))
            kept = [a.copy() for a in (rd.pos, rd.tick, rd.run, rd.nxt, rd.want, rd.done, rd.sk_i)]
            got = rd._read_side(todo, w1, most)
            if got is None:
                rd.pos, rd.tick, rd.run, rd.nxt, rd.want, rd.done, rd.sk_i = kept
                if w1 - w0 > 1:
                    span = max(1, (w1 - w0) // 4)
                    continue
                # one tick holding more than a window: its tracks a group at a time, each group's events in track
                # order, all collapsed into the same table (counts[3] keeps the tick)
                for lo in range(0, len(todo), GROUP_TRACKS):
                    if self._stop.is_set():
                        return None
                    for ts, ms, ks in rd._read_side(todo[lo:lo + GROUP_TRACKS], w1, 1 << 62):
                        if not take(ts, ms, ks):
                            return None
                w0 = w1
                continue
            ts, ms, ks, arrays = rd._sorted(got, w0, w1)
            if not take(ts, ms, ks):
                return None
            rd._pool[:] = [arrays]  # (the next window sorts into the same arrays)
            span = w1 - w0
            if len(ts) > 2 * 4_000_000:
                span = max(1, span // 2)
            elif len(ts) < 1_000_000 and span < (1 << 21):
                span *= 2
            w0 = w1
        return Part(t0, t1, (out[0][:n] - t0).astype(np.int32), out[1][:n].copy(), out[2][:n].copy(),
                    out[3][:n].copy(), s0)


def saved_file(index):
    """The saved read-ahead kept beside a MIDI's saved index file (user 2026-10-27: read once per MIDI, not once
    each time it's loaded; Shinki ~20 s, ~210 MB)."""
    return index[:-4] + SAVED_END


def declined_file(index):
    """Beside a saved index: the user said No to keeping its read-ahead (never asked again for it)."""
    return index[:-4] + DECLINED_END


def may_keep(d):
    """d (all read) may be offered for keeping: lm as loaded with a saved index, none kept yet, not declined."""
    lm = d.lm
    try:
        from files import midi_saved
        if d.error is not None or not d.parts or not _as_loaded(lm) or d.key != key_of(lm) \
                or not midi_saved.has_index(lm.mf):
            return False
        index = midi_saved.index_file(lm.mf.path, lm.mf.size)
        # (one kept that couldn't be used, an older format: offered again, kept over it, hunt 2026-10-28)
        kept = os.path.exists(saved_file(index)) and not getattr(d, "stale", False)
        return not kept and not os.path.exists(declined_file(index))
    except Exception:
        return False


def decline(d):
    """No to keeping d's read-ahead: not asked again for this MIDI's saved index."""
    d.unsaved = False
    try:
        from files import midi_saved
        from files.safefile import write_text
        write_text(declined_file(midi_saved.index_file(d.lm.mf.path, d.lm.mf.size)), "")
    except Exception:
        pass


def _as_loaded(lm):
    """Nothing hidden, removed or put on other channels since loading (only then is a read-ahead saved / used)."""
    return lm.mode == "file" and key_of(lm) == (0, frozenset(), None)


def _saved_head(lm):
    from files.midi_saved import _layout
    mf = lm.mf
    return {"ver": SAVED_VER, "size": mf.size, "mtime": mf.mtime, "layout": _layout(mf),
            "rules": [DENSE_MBS, JOIN_BEATS, EARLY_BEATS, PART_MOST, PART_MB]}


def kept_bytes(lm):
    """About the bytes Save Index also writes for lm's read-ahead (all read, as loaded), else 0 (hunt 2026-10-28: the
    question showed the index alone, ~120 MB of Shinki's ~3.1 GB)."""
    d = getattr(lm, "dense", None)
    if d is None or not d.done() or d.error is not None or not _as_loaded(lm) or d.key != key_of(lm):
        return 0
    return sum(getattr(p, k).nbytes for p in d.parts for k in PART_ARRAYS)


def leftovers(folder):
    """Half-written read-aheads (Spiderweb closed while keeping one, up to 3 GB, hidden from Settings' list) deleted,
    not ones being written now (hunt 2026-10-28)."""
    try:
        names = [n for n in os.listdir(folder) if n.endswith(SAVED_END + ".tmp")]
    except OSError:
        return
    for n in names:
        f = os.path.join(folder, n)
        if f not in SAVING:
            try:
                os.remove(f)
            except OSError:
                pass


SAVING = set()  # (temp files being written now: never deleted as leftovers)


def save(d):
    """Keeps d (all read) beside lm's saved index, when it has one and lm is as loaded. True when kept; quiet when it
    can't (errors.log)."""
    lm = d.lm
    try:
        from files import midi_saved
        if not _as_loaded(lm) or d.key != key_of(lm) or not midi_saved.has_index(lm.mf):
            return False
        ps = d.parts
        head = dict(_saved_head(lm), todo=[list(map(int, t)) for t in d.todo], failed=d.failed,
                    parts=[[int(p.t0), int(p.t1), len(p.ts), len(p.s0_q)] for p in ps])
        arrays = {"head": np.frombuffer(json.dumps(head).encode("utf-8"), np.uint8)}
        for i, p in enumerate(ps):  # (each part's own arrays: never all of them joined into one more copy)
            for k in PART_ARRAYS:
                arrays[f"{k}{i}"] = getattr(p, k)
        # straight into a file beside it, then put in place (safefile's way): built in memory first, a 3 GB
        # read-ahead took two more copies and the PC ran out of memory (2026-10-28)
        index = midi_saved.index_file(lm.mf.path, lm.mf.size)
        path = saved_file(index)
        tmp = path + ".tmp"
        SAVING.add(tmp)
        try:
            with open(tmp, "wb") as f:
                np.savez(f, **arrays)
                f.flush()
                os.fsync(f.fileno())
            if not os.path.exists(index):  # (its index deleted meanwhile: kept, it was a hidden 3 GB, hunt 10-28)
                os.remove(tmp)
                return False
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        finally:
            SAVING.discard(tmp)
        return True
    except Exception:  # (disk full, the folder gone: read again next time)
        import sys
        from files.errors import write_log
        write_log(*sys.exc_info(), "(Kestrel: saving a read-ahead)")
        return False


def _load(lm, todo):
    """(parts, failed) saved for lm as it is now (its parts `todo`), else None."""
    if not _as_loaded(lm):
        return None
    from files import midi_saved
    try:
        with np.load(saved_file(midi_saved.index_file(lm.mf.path, lm.mf.size)), allow_pickle=False) as z:
            head = json.loads(bytes(z["head"]).decode("utf-8"))
            if {k: head.get(k) for k in ("ver", "size", "mtime", "layout", "rules")} != \
                    json.loads(json.dumps(_saved_head(lm))) or head.get("todo") != [list(map(int, t)) for t in todo]:
                return None
            parts = []
            for i, (t0, t1, n, k) in enumerate(head["parts"]):
                a = {key: z[f"{key}{i}"] for key in PART_ARRAYS}
                if any(len(a[key]) != n for key in ("ts", "ms", "tr", "cp")) or len(a["s0_q"]) != k:
                    return None
                parts.append(Part(t0, t1, a["ts"], a["ms"], a["tr"], a["cp"], s0_q=a["s0_q"], s0_n=a["s0_n"]))
    except Exception:  # (none, or damaged: read again)
        return None
    return parts, head["failed"]


def ensure(lm):
    """lm's read-ahead (started now if it has none, or its hidden notes / tracks / channels changed since; taken
    from beside its saved index when kept there). The parts are found by the file's own tempo (Conductor changes
    move them a little: still the densest bytes)."""
    d = lm.dense
    if d is not None and d.key == key_of(lm) and (d.error is None or not d.done()):
        return d
    if d is not None:
        d.stop()
    from files.playback import TimeMap
    from notes.loaded import conductor_line, file_tempos
    from notes.tempo_file import heard_tempo
    tmap = TimeMap(lm.ppq, heard_tempo(conductor_line(lm), 120, lm.ppq, lm.end + 1, file_tempos(lm)))
    lm.dense = Dense(lm, tmap)
    return lm.dense
