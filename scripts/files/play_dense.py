"""Kestrel (GPU) and a loaded MIDI's densest parts (user 2026-10-27: live as close to their 2B-voice render as can
be). Where the file's bytes come faster than the live reader keeps up (DENSE_MBS a song second, worked out from every
track's checkpoints: Shinki's beats 1123-1126 = its run-up + wall, 4.6 GB in 1.2 s; there the reader fell 2.8 s
behind and 142 M notes came too late), the part is read ONCE ahead, in the background, its exact copies collapsed as
midi_play.stamp does live (Shinki's beats 1123-1126: 1.2 billion events -> 10.6 M, ~14 s on 4 threads), and the
Kestrel feeder plays it from that (midi_play.stamp_pre; play_loaded's reader steps over it). Kept with the loaded MIDI
(lm.dense) while its hidden notes, removed tracks and track channels stay as they were. Muted tracks: that play reads
the parts live as before (the read-ahead holds every track)."""

import io
import json
import threading

import numpy as np

from files import midi_play, speed

DENSE_MBS = 200  # MB of the file a song second past which a part is read ahead (the live reader fell behind ~250)
JOIN_BEATS = 1.0  # dense stretches closer than this are one part
EARLY_BEATS = 0.25  # each part starts this much before its first dense stretch
PART_MOST = 1 << 26  # events a part may keep after collapsing (~1.2 GB): more = that part read live as before
GROUP_TRACKS = 64  # one tick holding more than a reading window: its tracks read this many at a time
SLICE = 1 << 22  # events collapsed at a time (room kept for this many more)
SAVED_VER = 1  # (a saved read-ahead: how parts are read / collapsed changed = a new number, old ones read again)
SAVED_END = ".dense.npz"  # (beside the saved index: <index name>.dense.npz)


def stretches(lm, tmap):
    """[(t0, t1)] ticks of the parts to read ahead: bytes of each track between its checkpoints (64 KB apart)
    spread evenly over their ticks, summed per 1/16 beat, over DENSE_MBS a song second."""
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
    return [(max(0, a - early), b) for a, b in out]


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
        self.todo = stretches(lm, tmap)
        self.parts = []
        self.failed = 0  # (parts left to be read live: too many events kept, or reading failed)
        self.error = None
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
        saved = _load(lm, self.todo)
        if saved is not None:  # (read ahead on an earlier day, kept beside the MIDI's saved index)
            self.failed = saved[1]
            self.parts = saved[0]
            return
        rd = LoadedPlayer(lm, None, None, None, "kestrel")
        path = lm.path if lm.mode != "memory" else None
        try:
            rd._sc = _Scratch(path)
            rd._file = rd._sc.file
            rd._helpers = [_Scratch(path) for _ in range(helper_count())]
            rd._pool_x = ThreadPoolExecutor(max(1, len(rd._helpers)), thread_name_prefix="kestrel-dense")
            rd._table()
            for t0, t1 in self.todo:
                if self._stop.is_set():
                    return
                part = self._part(rd, t0, t1, WINDOW_MOST)
                if part is None:
                    self.failed += 1
                else:
                    self.parts.append(part)
            if not self._stop.is_set():
                save(self)
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


def _as_loaded(lm):
    """Nothing hidden, removed or put on other channels since loading (only then is a read-ahead saved / used)."""
    return lm.mode == "file" and key_of(lm) == (0, frozenset(), None)


def _saved_head(lm):
    from files.midi_saved import _layout
    mf = lm.mf
    return {"ver": SAVED_VER, "size": mf.size, "mtime": mf.mtime, "layout": _layout(mf),
            "rules": [DENSE_MBS, JOIN_BEATS, EARLY_BEATS, PART_MOST]}


def save(d):
    """Keeps d (all read) beside lm's saved index, when it has one and lm is as loaded. Quiet when it can't."""
    lm = d.lm
    try:
        from files import midi_saved
        if not _as_loaded(lm) or d.key != key_of(lm) or not midi_saved.has_index(lm.mf):
            return
        ps = d.parts
        head = dict(_saved_head(lm), todo=[list(map(int, t)) for t in d.todo], failed=d.failed,
                    parts=[[int(p.t0), int(p.t1), len(p.ts), len(p.s0_q)] for p in ps])
        cat = lambda k, dt: np.concatenate([getattr(p, k) for p in ps]) if ps else np.zeros(0, dt)  # noqa: E731
        arrays = {"head": np.frombuffer(json.dumps(head).encode("utf-8"), np.uint8),
                  "ts": cat("ts", np.int32), "ms": cat("ms", np.uint32), "tr": cat("tr", np.uint16),
                  "cp": cat("cp", np.uint32), "s0_q": cat("s0_q", np.int64), "s0_n": cat("s0_n", np.int64)}
        buf = io.BytesIO()
        np.savez(buf, **arrays)
        from files.safefile import write_bytes
        write_bytes(saved_file(midi_saved.index_file(lm.mf.path, lm.mf.size)), buf.getvalue())
    except Exception:  # (disk full, the folder gone: read again next time)
        import sys
        from files.errors import write_log
        write_log(*sys.exc_info(), "(Kestrel: saving a read-ahead)")


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
            a = {k: z[k] for k in ("ts", "ms", "tr", "cp", "s0_q", "s0_n")}
    except Exception:  # (none, or damaged: read again)
        return None
    parts, at, s = [], 0, 0
    for t0, t1, n, k in head["parts"]:
        parts.append(Part(t0, t1, a["ts"][at:at + n], a["ms"][at:at + n], a["tr"][at:at + n], a["cp"][at:at + n],
                          s0_q=a["s0_q"][s:s + k], s0_n=a["s0_n"][s:s + k]))
        at, s = at + n, s + k
    if at != len(a["ts"]) or s != len(a["s0_q"]):
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
