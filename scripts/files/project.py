"""Project files: saving / opening projects, the autosave, and writing the MIDI file."""

import copy
import errno
import json
import math
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc
from notes.custom import ALIGNS, ENDS, CUSTOM_DEFAULTS, CUSTOM_FLAGS, FILLS, clean_cycle, notes_shape, unpack_notes
from notes.engine import (CHANNEL_MODES, OWN0, SHAPE_DEFAULTS, SPLITS, auto_count, clean_basics, clean_kept_patterns, clean_shape,
                          channels_of, merges_unmade, one_voice, shape_notes_tracks, slot_port, slot_ports,
                          slot_track_channel, track_ids)
from notes.hzbass import clean_hz
from notes.loaded import (clean_link, conductor_line, extract_groups, extract_ports, file_tempos, own_hits, retime_for,
                          same_conductor, tempo_new, tempo_tracks)
from notes.merge import has_colours
from notes.hz_cc import slot_events
from notes.hz_fine import fine_owners, finer_notes, swapped
from notes.passengers import STAY, USUAL, event_kind, midi_messages, pack_cc, placed, unpack_cc
from notes.mask import as_used, picture_like, slot_rule
from notes.mask import MASK_DEFAULTS, THICKNESS, clean_mask
from notes.nested import clean_nested
from notes.tiles import clean_tiles
from notes.ripples import clean_ripples
from notes.funnel import FUNNEL_DEFAULTS, clean_funnel
from notes.paths import KEYS
from notes.picture import paths_for_file
from notes.polygon import POLYGON_DEFAULTS, clean_polygon
from notes.sliced import pack_wholes, unpack_wholes
from notes.smooth import SMOOTH_DEFAULT, clean_level
from notes.gliss import clean_gliss, for_new
from notes.tempo import clamp_bpm, clean_tempo, empty as empty_tempo, has_tempo
from notes.meter import DEFAULT as DEFAULT_SIG, clean_sigs, sigs_of
from notes.tempo_file import bar_line, bar_ticks, file_plan, finer_parts
from notes.text import TEXT_DEFAULTS, clean_text
from notes import engine  # (engine.edo_lanes: 31-EDO "Overlaps keep their pitch")
from files import clipboard, notes_clip
from files.domino_clip import (DOMINO_STARTS, GATE_MAX, LONGEST, MOST_NOTES, clip_data, clip_span, cut_gates, dms_data,
                               conductor_clip, dms_path, get_from_clipboard, put_on_clipboard, read_notes,
                               tempo_items)
from files.own_tracks import ID_MOST, part_key, clean_file_gone, clean_file_order, clean_file_props, clean_file_tracks, clean_own_tracks, new_id
from files.midi_out import (MAX_DELTA, MAX_TRACKS, PPQ_WARN, long_silences, note_inserts, note_tracks, own_events,
                            track_slots, write_midi)
from files.own_tracks import FILE0
from files.playback import OLD_NAMES, keep_saved
from files.about import BUILD, HERE, USER, VERSION
from files.errors import write_log
from files.midi_copy import Added, Retime
from files.midi_read import M_CUT, M_RMID, Cancelled, FileChanged, MidiError
from files.safefile import write_bytes, write_text
from files.settings import SPACE_BARS, SPACE_KEYS, save_app_settings, with_app_settings
from files.snap import clean_snap
from files.unzip import allowance
from files.update_check import version_tuple
from window.big_ask import ask as ask_big, ask_drums, trouble as big_trouble
from window.tempo_pane import ask_domino
from roll.roll_draw import GHOST_FADE, STACK_MODES

AUTOSAVE = os.path.join(USER, "autosave.json")
OUTPUT_DIR = os.path.join(HERE, "output")


def domino_sigs(sigs, first, ppq):
    """The time signatures a Conductor copy for Domino takes from tick first on (user 2026-11-06: none were left out
    unexpectedly): every change inside; the one in force at first only when first is on a bar line (put mid-bar,
    Domino would start a new bar there and shift every bar line after it). Key signatures all go (no bars)."""
    if bar_line(sigs, first, ppq) == first:
        return sigs
    return [s for s in sigs if s[0] > first]


def backup_path(autosave):
    """autosave.json -> autosave-backup.json (an ordinary project file, so Open project can open it too)."""
    return os.path.splitext(autosave)[0] + "-backup.json"


PROJECT_EXT = ".spwb"  # (the project's JSON packed with gzip; old projects are plain .json, autosave stays .json)
PACKED = b"\x1f\x8b"  # (a packed project's first bytes: told by them, whatever the file is called)
MOST_UNPACKED = 1 << 30  # (a packed file that doesn't start like a project and unpacks to more: refused, never
#                           fills the memory; a project's own start = no limit: Spiderweb opens all it saves)
PROJECT_MARKS = {"shapes", "version", "app_version"}  # (a JSON file without any = not a project: shape files...)
HEAD = 1 << 16


def is_packed_name(path):
    return os.path.splitext(path)[1].lower() == PROJECT_EXT


def as_packed_name(path):
    """Save as…'s suggestion: an old .json project offered as .spwb (user 2026-10-29: Ctrl+S keeps the .json)."""
    stem, ext = os.path.splitext(path)
    return stem + PROJECT_EXT if ext.lower() == ".json" else path


def unpack(raw):
    """A packed project's bytes -> its text bytes (ValueError when damaged or far too big)."""
    import zlib
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        head = d.decompress(raw, HEAD)
        ours = head.lstrip(b"\xef\xbb\xbf \r\n\t").startswith(b"{") and (b'"app_version"' in head
                                                                         or b'"version"' in head)
        out = head + (d.decompress(d.unconsumed_tail) if ours else d.decompress(d.unconsumed_tail, MOST_UNPACKED + 1))
    except zlib.error:
        raise ValueError(tr("project.packed_damaged")) from None
    if not ours and len(out) > MOST_UNPACKED:
        raise ValueError(tr("project.packed_too_big"))
    if not d.eof:
        raise ValueError(tr("project.packed_damaged"))
    return out


def read_project(path):
    """A project file's contents and its text's size, packed (.spwb) or plain (a BOM, which some text editors put
    at the start, is skipped)."""
    with open(path, "rb") as f:
        packed = f.read(2) == PACKED
    try:
        if not packed:  # (plain: read as text, as before .spwb: no extra copy in memory)
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
        else:
            with open(path, "rb") as f:
                raw = unpack(f.read())
            text = raw.decode("utf-8-sig")
            del raw  # (the bytes go before the JSON is read: big projects)
    except UnicodeDecodeError:
        raise ValueError(tr("project.not_a_spiderweb_project")) from None
    size = len(text)
    data = json.loads(text)
    del text
    if not isinstance(data, dict):
        raise ValueError(tr("project.not_a_spiderweb_project"))
    return data, size


def read_json(path):
    return read_project(path)[0]


def open_types():
    return [(tr("project.spiderweb_project"), "*" + PROJECT_EXT + " *.json"), (tr("project.all_files"), "*.*")]


def save_types():
    return [(tr("project.spiderweb_project"), "*" + PROJECT_EXT), (tr("project.spiderweb_project_plain"), "*.json")]


def keep_aside(path, name, move=False):
    """A copy of the file (or the file itself, moved) as <name>-<date>-<time>.json in its folder, never replacing an
    earlier one. Returns its path."""
    stamp, n = time.strftime(f"{name}-%Y%m%d-%H%M%S"), 1
    kept = os.path.join(os.path.dirname(path), stamp + ".json")
    while os.path.exists(kept):
        n += 1
        kept = os.path.join(os.path.dirname(path), f"{stamp}-{n}.json")
    if move:
        os.replace(path, kept)
    else:
        with open(path, "rb") as f:
            write_bytes(kept, f.read())
    return kept


def couldnt_save(e):
    """Why a file couldn't be saved: in plain words when the reason is a common one, then Windows' own message."""
    win = getattr(e, "winerror", None)
    if isinstance(e, PermissionError):
        why = "project.save_locked"
    elif win in (123, 161, 267) or e.errno == errno.EINVAL:
        why = "project.save_bad_name"
    elif win in (39, 112) or e.errno == errno.ENOSPC:
        why = "project.save_disk_full"
    elif win in (3, 15, 21) or isinstance(e, FileNotFoundError):
        why = "project.save_no_folder"
    else:
        return tr("project.couldn_t_save", e=e)
    return tr("project.couldn_t_save_why", why=tr(why), e=e)


def output_path(typed):
    """The MIDI file the Output file box means, as a full path: empty = spiderweb.mid in Spiderweb's output folder;
    a name or path without a drive counts from that folder too (not from wherever Windows started Spiderweb); a
    folder = spiderweb.mid inside it; .mid added when it has no .mid / .midi ending."""
    path = typed.strip() or "spiderweb.mid"
    if not os.path.isabs(path):
        path = os.path.join(OUTPUT_DIR, path)
    if path.endswith(("/", "\\")) or os.path.isdir(path):
        path = os.path.join(path, "spiderweb.mid")
    path = os.path.normpath(path)
    return path if path.lower().endswith((".mid", ".midi")) else path + ".mid"


def short_num(x, digits=12):
    """A float with the noise digits dropped (4.0 -> 4, 0.1000000000000001 -> 0.1)."""
    if not isinstance(x, float):
        return x
    x = float(f"{x:.{digits}g}")
    return int(x) if x.is_integer() else x


def short_env(env):
    """Envelope for saving: velocities to 4 decimals, u to 12 digits unless that would merge two different
    points (a jump is two points 1e-9 beats apart, and which side a note lands on matters)."""
    out = []
    for u, v in env:
        su = short_num(u)
        if out and su == out[-1][0] and u != prev:
            su = u
        out.append([su, short_num(round(v, 4))])
        prev = u
    return out


def short_stroke(st):
    if st["kind"] == "ellipse":
        return dict(st, box=[short_num(a) for a in st["box"]])
    return dict(st, pts=[[short_num(a) for a in p] for p in st["pts"]])  # a curve keeps its corners / symmetry


def short_tumour(tm):
    return {a: {g: [[short_num(x) for x in p] for p in pts] for g, pts in b.items()} if a == "graphs"
            else short_num(b) for a, b in tm.items()}


def short_shape(sh):
    def short(k, v):
        if k == "vel_env":
            return short_env(v)
        if k == "cut":  # (what a piece remembers of itself, trimmed as it is: still the same after loading)
            return {a: {n: short(n, x) for n, x in b.items()} if a in ("was", "vel", "gate") and b else b
                    for a, b in v.items()}
        if k == "pts":
            return [[short_num(a) for a in p] for p in v]
        if k == "strokes":
            return [short_stroke(st) for st in v]
        if k == "starts":
            return [{"line": st.get("line", 0), "at": short_num(st["at"]),
                     "ends": [c and dict(c, pts=[[short_num(a) for a in uf] for uf in c["pts"]]) for c in st["ends"]]}
                    for st in v]
        if k == "tumour":
            return short_tumour(v)
        if k == "tumours":
            return [short_tumour(t) if t else None for t in v]
        if k in ("pattern", "shape"):
            return {a: {n: short_num(x) for n, x in b.items()} if a == "vars" else short_num(b) for a, b in v.items()}
        if k == "polygon":
            return {a: short("pattern", b) if a == "pattern" else short_num(b) for a, b in v.items()}
        if k == "text":
            return {a: [short_num(x) for x in b] if a == "bbox" else short_num(b) for a, b in v.items()}
        return short_num(v) if k in ("gate", "gate0", "gate1", "k") else v
    # (a Gate sensitive merge's notes are made again from its recipe when loaded: merge.py)
    return json.dumps({k: short(k, v) for k, v in sh.items() if not (k == "notes" and "merge" in sh)})


def slot_lanes(notes, use10):
    """Each note's channel x 256 + key as exported (a slot's track has one channel: slot_track_channel)."""
    slots = notes[:, 4]
    if not len(slots):
        return slots.copy()
    us = np.unique(slots)
    chans = np.array([slot_track_channel(int(k), use10)[1] for k in us.tolist()], np.int64)
    return chans[np.searchsorted(us, slots)] * 256 + notes[:, 2]


def same_rows(a, b):
    """Which rows of a (n x 3 whole numbers) are also rows of b."""
    if not len(a) or not len(b):
        return np.zeros(len(a), bool)
    v = lambda x: np.ascontiguousarray(x, np.int64).view(np.dtype((np.void, 24))).ravel()
    return np.isin(v(a), v(b))


def timed_ticks(plan, notes, cc):
    """(notes, cc) moved to the file's ticks by file_plan's map (Keep timing parts)."""
    moved = notes.copy()
    moved[:, 0], moved[:, 1] = plan["map"](notes[:, 0]), plan["map"](notes[:, 1])
    return moved, {s: [(t, *m[1:]) for t, m in zip(plan["map"]([m[0] for m in ms]).tolist(), ms)]
                   for s, ms in cc.items()}


def project_json(data):
    """The project as JSON with one line per setting and one line per shape (plain json.dump puts every
    number on its own line)."""
    lines = []
    for k, v in data.items():
        if k == "shapes":
            v, wholes = pack_wholes(v)  # (sliced pieces: one copy of the shape they were cut from)
            body = ",\n  ".join(short_shape(sh) for sh in v)
            lines.append(f'"shapes": [\n  {body}\n ]' if v else '"shapes": []')
            if wholes:
                lines.append('"wholes": [\n  ' + ",\n  ".join(short_shape(w) for w in wholes) + "\n ]")
        else:
            if isinstance(v, dict):
                v = {a: short_num(b) for a, b in v.items()}  # (12 digits like shapes: a long default gate stays exact)
            lines.append(f"{json.dumps(k)}: {json.dumps(v)}")
    return "{\n " + ",\n ".join(lines) + "\n}\n"


class ProjectFiles:
    """Mixed into App."""

    def project_data(self):
        link = self.loaded.link() if self.loaded is not None else self.midi_link
        return {
            # the file format (3: a loaded MIDI's link; older Spiderwebs say a newer one made it), and the
            # Spiderweb that saved it
            "version": 3 if link else 2, "app_version": VERSION, **({"build": BUILD} if BUILD else {}),
            **({"loaded": dict(link)} if link else {}),
            "ppq": self.pvar["ppq"].get(), "bpm": self.pvar["bpm"].get(),
            "output": self.pvar["output"].get(), "channel_mode": self.channel_mode.get(), "channel_split": self.channel_split,
            "use_ch10": bool(self.use_ch10.get()), "ch10_order": bool(self.ch10_order.get()), "new_rounds": bool(self.new_rounds.get()), "pad_rounds": bool(self.pad_rounds.get()),"keys": self.keys, "edo31": bool(self.edo31.get()), "edo31_audio": bool(self.edo31_audio.get()), "edo31_loaded": bool(self.edo31_loaded.get()), "edo31_lanes": bool(self.edo31_lanes.get()),
            # (overlap fixing, user 2026-11-06: only when on, so files without them stay as they were)
            **{k: True for k, v in (("single_split", self.single_split), ("multi_cut", self.multi_cut),
                                    ("multi_later", self.multi_later), ("multi_stretch", self.multi_stretch))
               if v.get()},
            "domino_start": self.domino_start(),
            "snap": self.snap.get(), "defaults": self.defaults,
            "custom_defaults": dict(self.custom_defaults, shape=self.custom_shape),
            "hz_defaults": self.hz_defaults, "hz_snap": self.hz_snap.get(),
            "hz_line": bool(self.hz_line.get()),
            "hz_fx": bool(self.hz_fx.get()), "hz_layers": bool(self.hz_layers.get()),
            "hz_loud": bool(self.hz_loud.get()),
            "funnel_defaults": self.funnel_defaults, "text_defaults": self.text_defaults,
            "polygon_defaults": self.polygon_defaults,
            "free_smooth": self.free_smooth, "gliss_defaults": self.gliss_defaults,
            # (whenever anything in it isn't the default: a 3/4 with no tempo point too, hunt 2026-10-24; a plain
            # one is left out, so files without a tempo line stay as they were)
            **({"tempo": self.tempo} if self.tempo != empty_tempo() else {}),
            **({"own_tracks": self.own_tracks} if self.own_tracks else {}),
            **({"own_next": self.own_next} if self.own_next else {}),
            **({"start_track": True} if self.start_track else {}),  # (a new project's empty Track 1, not removed)
            **({"deleted": self.deleted} if self.deleted else {}),  # (Delete loaded notes' boxes)
            **({"file_order": self.file_order} if self.file_order else {}),  # (the loaded tracks as dragged)
            **({"file_gone": self.file_gone} if self.file_gone else {}),  # (the loaded tracks removed)
            **({"file_props": {str(n): v for n, v in self.file_props.items()}} if self.file_props else {}),
            # (the loaded MIDI's tracks shapes are drawn into: their name + channel, for opening without it)
            **({"file_tracks": {str(n): self.file_tracks()[n] for n in self.file_into()}} if self.file_into() else {}),
            "shapes": self.shapes, "view": self.roll.view_state(), "playhead": self.playhead,
        }

    def load_file(self, path, quiet=False, text=None):
        """Opens a project. Everything in it is read and checked before anything changes: a file that can't be
        used leaves the project as it was; a broken setting keeps the one there is now; a shape that can't be read
        is left out and self.load_notes says so (tell_load_notes). Running out of memory (or any other error) while
        its notes are made puts the project as it was back (user 2026-10-20: a shape stuck failing at every redraw).
        text: the project as text instead of the file at path (put_back)."""
        from notes.recolour import forget_kept
        forget_kept()  # (quick Stir changes' kept work, hunt 11-07)
        self.load_notes = []
        try:
            data, size = read_project(path) if text is None else (json.loads(text), len(text))
            if not isinstance(data.get("shapes", []), list) or not PROJECT_MARKS & data.keys():  # (a shape file, palette...)
                raise ValueError(tr("project.not_a_spiderweb_project"))
        except (OSError, ValueError) as e:
            if not quiet:
                messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_open_project", e=e))
            return False
        except MemoryError:
            if not quiet:
                messagebox.showerror(tr("project.spiderweb"), tr("big_ask.out_of_memory"))
            return False
        wholes = data["wholes"] if isinstance(data.get("wholes"), list) else []

        cc_lost = [0]  # (pasted notes kept whose CCs couldn't be read: said, hunt 2026-10-22)

        def read():
            out = []
            cc_lost[0] = 0
            with allowance(size):  # (pasted notes made to unpack far bigger than the file: left out)
                for raw in data.get("shapes", []):
                    try:
                        sh = clean_shape(unpack_wholes(raw, wholes)) if isinstance(raw, dict) else None
                    except Exception:  # (damaged, or written in a way this Spiderweb doesn't know)
                        sh = None
                    if sh:
                        out.append(sh)
                        cc_lost[0] += isinstance(raw.get("cc"), str) and "cc" not in sh
            return out
        try:
            with merges_unmade():  # (merged shapes' notes made only once the count is OK'd: they can be millions)
                shapes = read()
            if not quiet and not self.fits_memory(shapes, data.get("ppq")):
                return False
            if any("merge" in sh for sh in shapes):
                shapes = read()
        except MemoryError:
            if not quiet:
                messagebox.showerror(tr("project.spiderweb"), tr("big_ask.out_of_memory"))
            return False
        folder = os.path.dirname(os.path.abspath(path)) if path else HERE
        shapes = paths_for_file(shapes, folder, False)  # (pictures' files)
        made = version_tuple(data.get("app_version")) if isinstance(data.get("app_version"), str) else None
        newer = made and made > version_tuple(VERSION) or type(data.get("version")) is int and data["version"] > 3
        if newer:  # (what that version added is left out)
            self.load_notes.append(tr("project.made_by_a_newer_spiderweb", made=data.get("app_version") or "?",
                                      VERSION=VERSION))
        lost = len(data.get("shapes", [])) - len(shapes)
        if lost:
            self.load_notes.append(trn("project.shapes_left_out", lost, n=lost))
        if cc_lost[0]:
            self.load_notes.append(trn("project.cc_lost", cc_lost[0], n=cc_lost[0]))
        self.load_lost = bool(newer or lost or cc_lost[0])  # (the autosave as it was is worth keeping aside)

        def get(fn):  # one setting read from the file: None if it's broken
            try:
                return fn()
            except Exception:
                return None

        def text(key):  # (a setting that's always text: anything else counts as missing)
            return data[key] if isinstance(data.get(key), str) else None

        def table(key):
            return data[key] if isinstance(data.get(key), dict) else {}

        boxes = {key: str(data[key]) for key in ("ppq",) if isinstance(data.get(key), (int, float, str))
                 and not isinstance(data[key], bool)}
        boxes["bpm"] = "120"  # (no BPM box, user 2026-10-23: before the tempo line's first point = MIDI's 120)
        old_bpm = get(lambda: calc(str(data["bpm"]))) if isinstance(data.get("bpm"), (int, float, str)) else None
        out = text("output")
        if out is not None and out.strip():
            folder = os.path.dirname(output_path(out))  # (as Export reads it: not from wherever Spiderweb started)
            if not os.path.isdir(folder) and os.path.normcase(folder) != os.path.normcase(os.path.abspath(OUTPUT_DIR)):
                # (made on another PC, or the folder is gone): Spiderweb's own output folder, same file name
                out = os.path.join(OUTPUT_DIR, os.path.basename(out) or "spiderweb.mid")
                self.load_notes.append(tr("project.output_folder_missing", folder=folder, path=out))
        if out is not None:
            boxes["output"] = out
        defaults = clean_basics(table("defaults"))
        cycle = get(lambda: clean_cycle(table("defaults").get("cycle")))
        if cycle:  # "Colours" for new shapes (custom.py)
            defaults["cycle"] = cycle
        mode = text("channel_mode") or ("auto" if data.get("auto_channels") else "single")
        split = text("channel_split")
        starts = [v for v, _ in DOMINO_STARTS]
        start = text("domino_start")
        keys = table("hz_defaults")
        hz_keys = ({"lo": keys["lo"], "hi": keys["hi"]} if all(type(keys.get(k)) is int for k in ("lo", "hi"))
                   and 0 <= keys["lo"] <= keys["hi"] <= 255 else None)
        custom = get(lambda: self.read_custom_defaults(table("custom_defaults")))
        funnel = get(lambda: {k: v for k, v in clean_funnel(table("funnel_defaults")).items()
                              if k in FUNNEL_DEFAULTS or k in ("range", "range_kept")}
                     if table("funnel_defaults") else None)
        tx = get(lambda: clean_text(dict(table("text_defaults"), bbox=[0, 0, 1, 1])) if table("text_defaults") else None)
        pg = get(lambda: clean_polygon(data.get("polygon_defaults")))
        playhead = get(lambda: float(data.get("playhead", 0)))
        tempo = clean_tempo(data.get("tempo"))
        if not isinstance(table("tempo").get("sigs"), list):  # (saved before time signatures: its Beats per bar)
            tempo["sigs"] = clean_sigs(None, beats=get(lambda: int(float(data.get("beats", 4)))))
        if (isinstance(old_bpm, (int, float)) and 4 <= old_bpm <= 100000 and old_bpm != 120
                and not (tempo["points"] and tempo["points"][0][0] <= 1e-9)):
            # (saved with the BPM box: its BPM a point on beat 0, so it plays as it did, user 2026-10-23)
            tempo["points"].insert(0, [0.0, clamp_bpm(float(old_bpm)), "hold", "", "lin", False, [0.5, 0.5]])
        link = clean_link(data.get("loaded"))  # (its MIDI: loaded again by find_loaded, after this)
        own = clean_own_tracks(data.get("own_tracks"))

        was = project_json(self.project_data()), self.undo_stack[:], self.redo_stack[:], self._redo_kept
        was_loaded = self.loaded
        self._opening = True  # (each box set would make the notes again: once, below)
        try:
            for key, value in boxes.items():
                self.pvar[key].set(value)
        finally:
            self._opening = False
        self.channel_mode.set(mode if mode in CHANNEL_MODES else "single")
        self.use_ch10.set(data.get("use_ch10") is True)
        self.ch10_order.set(data.get("ch10_order") is True)
        self.new_rounds.set(data.get("new_rounds") is True)
        self.pad_rounds.set(data.get("pad_rounds") is not False)
        for k in ("single_split", "multi_cut", "multi_later", "multi_stretch"):
            getattr(self, k).set(data.get(k) is True)
        self.keys_var.set(str(KEYS[1] if data.get("keys") == KEYS[1] else KEYS[0]))
        self.edo31.set(data.get("edo31") is True)
        self.edo31_audio.set(data.get("edo31_audio") is True)
        self.edo31_loaded.set(data.get("edo31_loaded") is True)
        self.edo31_lanes.set(data.get("edo31_lanes") is not False)  # (default on: files saved without it too)
        self.split_box.current(SPLITS.index(split) if split in SPLITS else 0)
        self.sync_channels_fold()
        if start in starts:
            self.domino_box.current(starts.index(start))
        if "snap" in data:
            self.snap.set(clean_snap(data["snap"]))
        if "hz_snap" in data:
            self.hz_snap.set(clean_snap(data["hz_snap"]))
        self.hz_line.set(data.get("hz_line") is not False)
        self.hz_fx.set(data.get("hz_fx") is True)
        self.hz_layers.set(data.get("hz_layers") is True)
        self.hz_loud.set(data.get("hz_loud") is True)
        self.defaults = defaults
        if hz_keys:
            self.hz_defaults = hz_keys
        if custom:
            self.custom_defaults.clear()
            self.custom_defaults.update(custom[0])
            self.custom_shape = custom[1]
        if funnel:
            self.funnel_defaults = funnel
        if tx:
            self.text_defaults = {k: tx[k] for k in TEXT_DEFAULTS}
        self.free_smooth = get(lambda: clean_level(data.get("free_smooth", SMOOTH_DEFAULT))) or SMOOTH_DEFAULT
        self.gliss_defaults = for_new(clean_gliss(data.get("gliss_defaults")))
        if pg:
            self.polygon_defaults = {k: pg[k] for k in POLYGON_DEFAULTS}
        self.roll.cancel_draft()
        self.roll.drop_lasso()  # (a waiting loop would cut the opened project, hunt 2026-10-23)
        self.shapes = shapes
        self.stop_loaded()  # (still playing: its MIDI stops, the shapes play on)
        self.drop_read_ahead()  # (its read-ahead stopped and let go: hunt 2026-10-28, read on for a minute)
        self.loaded = None  # (another project: its loaded MIDI goes; attach_midi puts a new one in after this)
        self.ahead_play.mark_dirty()  # (Rendered ahead: that MIDI's made sound let go too)
        self.midi_link = link
        self.midi_missing = False  # (looked for now: "not found" only once that fails, load_midi.said_without)
        self.drop_finding()
        self.roll.forget_loaded()
        self.tempo = tempo
        self.own_tracks = own
        self.start_track = data.get("start_track") is True  # (projects from before: their tracks as they were)
        self.file_saved = clean_file_tracks(data.get("file_tracks"))
        self.deleted = extract_groups(data.get("deleted"))
        self.file_order = clean_file_order(data.get("file_order"))
        self.file_gone = clean_file_gone(data.get("file_gone"))
        self.file_props = clean_file_props(data.get("file_props"))
        nxt =data.get("own_next")  # (ids never used twice in a project: own_tracks.new_id)
        self.own_next = new_id(own, nxt if type(nxt) is int and 0 <= nxt < ID_MOST else 0)
        if not quiet:  # (another project: copies from the one before aren't laid on these tracks)
            self.project_token = os.urandom(8).hex()
        # (only an autosave has them: the project file it belonged to, and whether it had unsaved changes)
        self._file_extra = (data.get("project_file"), data.get("unsaved"))
        if hasattr(self, "tempo_pane"):  # (the old project's Select box / picks / catch-up being added go)
            self.tempo_pane.forget()
        self.tempo_changed()
        self.roll.set_view(data.get("view"))
        self.stop_play()
        self.playhead = max(0.0, playhead) if playhead is not None and math.isfinite(playhead) else 0.0
        self.sel = None
        self.sels = set()
        if getattr(self, "colour_edit", None) is not None:  # (a colour step edited in the project before: forgotten)
            self.colour_edit = None
            self.props_edit_ended()
            self.after_idle(self.sync_edit_lines)
        self.roll.box_kept = None  # (an empty Select box / ruler band too)
        self.parts = set()
        self.stroke = self.ripple_pick = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._redo_kept = None
        if hasattr(self, "roll"):  # (another project: no Warp point picked)
            self.roll.warp_drop_pick()
        if not quiet and hasattr(self, "track_list"):  # (another song: every track heard and shown, hunt 2026-10-23)
            self.track_list.forget()
        try:
            self.on_project_change()
        except Exception as e:  # (its notes couldn't be made: the project as it was, its undo steps too)
            notes = self.load_notes
            self.load_file(path, quiet=True, text=was[0])
            self.undo_stack[:], self.redo_stack[:], self._redo_kept = was[1:]
            self.loaded = was_loaded  # (its MIDI too, as it was)
            self.load_notes = notes
            self.sync_history()
            if not isinstance(e, MemoryError):
                raise
            if not quiet:
                messagebox.showerror(tr("project.spiderweb"), tr("big_ask.out_of_memory"))
            return False
        self.show_colour_limit()  # (its "Use channel 10" in the open drawer / Image window)
        return True

    def read_custom_defaults(self, custom):
        """custom_defaults from a file -> (new custom_defaults, custom shape name); a broken one keeps what's
        there now."""
        out, name = dict(self.custom_defaults), self.custom_shape
        if custom.get("fill") in FILLS:
            out["fill"] = custom["fill"]
        if custom.get("align") in ALIGNS:
            out["align"] = custom["align"]
        if custom.get("ends") in ENDS:
            out["ends"] = custom["ends"]
        for key in CUSTOM_FLAGS:
            out[key] = bool(custom.get(key))
        try:
            gate = float(custom.get("gate", CUSTOM_DEFAULTS["gate"]))
            if math.isfinite(gate):
                out["gate"] = max(1e-6, gate)
        except (TypeError, ValueError):
            pass
        out.pop("edge", None)
        try:  # the smallest outline gate (custom.grow_inward)
            edge = float(custom.get("edge") or 0)
            if math.isfinite(edge) and edge > 0:
                out["edge"] = edge
        except (TypeError, ValueError):
            pass
        out.pop("edge_mode", None)
        if custom.get("edge_mode") == "sideways":
            out["edge_mode"] = "sideways"
        out.pop("nested", None)
        nested = clean_nested(custom.get("nested"))
        if nested and out["fill"] in ("fill", "spam"):  # (Inside fill Nested)
            nested.pop("at", None)
            out["nested"] = nested
        out.pop("tiles", None)
        tiles = clean_tiles(custom.get("tiles"))
        if tiles and not nested and out["fill"] in ("fill", "spam"):  # (Inside fill Tiles)
            tiles.pop("at", None)
            out["tiles"] = tiles
        out.pop("ripples", None)
        ripples = clean_ripples(custom.get("ripples"))
        if ripples and not nested and not tiles and out["fill"] in ("fill", "spam"):  # (Inside fill Ripples)
            ripples.pop("at", None)
            ripples.pop("turns", None)
            out["ripples"] = ripples
        out.pop("patterns_kept", None)
        kept = clean_kept_patterns(custom.get("patterns_kept"))  # (the patterns switched off: settings kept)
        kept.get("ripples", {}).pop("turns", None)
        if kept:
            out["patterns_kept"] = kept
        out.pop("hz", None)
        if clean_hz(custom.get("hz")):
            out["hz"] = clean_hz(custom["hz"])
        out.pop("before_hz", None)
        was = custom.get("before_hz")  # (older versions gave new spam shapes the Hz bass tool's gate: theirs back)
        try:
            gate = float(was["gate"]) if isinstance(was, dict) and out.get("hz") else math.nan
            if math.isfinite(gate):
                out["gate"] = max(1e-6, gate)
        except (KeyError, TypeError, ValueError):
            pass
        if isinstance(custom.get("shape"), str) and custom["shape"]:
            name = custom["shape"]
        return out, name

    def tell_load_notes(self, intro="", outro=""):
        """What load_file had to leave out or change, in a message (nothing when all went well)."""
        if self.load_notes:
            messagebox.showwarning(tr("project.spiderweb"), intro + "\n\n".join(self.load_notes) + outro,
                                   parent=self)

    def write_json(self, path, window=False, midi=None, data=None):
        """data: the project as project_data() gave it earlier (Save a Copy for Sharing: as it was when picked)."""
        data = self.project_data() if data is None else data
        if window:  # only the autosave remembers the window
            data["window"] = {"geometry": self._normal_geometry or self.wm_geometry(),
                              "maximized": self.wm_state() == "zoomed",
                              "velocity": self.show_velocity.get(), "history": self.show_history.get(),
                              "show_lines": self.show_lines.get(), "show_notes": self.show_notes.get(),
                              "channels_folded": self.channels_folded.get(),
                              "stir_open": sorted(self.stir_open),
                              "props_open": sorted(self.props_open),
                              "velocity_height": self.velocity_height() / self.scale,
                              "tempo": self.show_tempo.get(), "tempo_height": self.tempo_height() / self.scale,
                              "tempo_view": list(self.tempo_pane.view) if self.tempo_pane.view else None,
                              "tempo_log": self.tempo_pane.log,
                              "midi_device": self.midi_device.get(), "play_voices": self.play_voices,
                              "play_voice_guard": self.play_guard.get(), "play_limiter": self.play_limiter.get(),
                              "edo10_piano": self.edo10_piano.get(),
                              "loaded_synths": self.loaded_synths,
                              **{"kestrel_" + k: v for k, v in self.kestrel_opts.items()},
                              "kestrel_slow_down": self.slow_kestrel.get(), "play_slow_down": self.slow_builtin.get(),
                              "ahead_engine": self.ahead_engine.get(), "ahead_seconds": self.ahead_seconds,
                              "ahead_slow_down": self.slow_ahead.get(),
                              **{"ahead_" + k: v for k, v in self.ahead_opts.items()},
                              "domino_256": self.domino256.get(), "copy_domino": self.copy_domino.get(),
                              "copy_notes": self.copy_notes.get(),
                              "keep_colours": self.keep_colours.get(), "stacked_notes": self.stack_order.get(),
                              "loud_on_top": self.stack_order.get() == "loud",
                              "hidden_shade": self.hidden_shade.get(),
                              "space_keys": self.space_keys.get(), "space_bars": self.space_bars.get(),
                              "keep_offrange": self.keep_offrange.get(),
                              "offrange_opacity": self.offrange_opacity.get(),
                              "wrap_offrange": self.wrap_offrange.get(),
                              "show_wrapped": self.show_wrapped.get(),
                              "live": (getattr(getattr(self, "stir_window", None), "drawing", None)
                                       or {"live": self.live.get()})["live"],  # (a Stir area being drawn turned
                              # Live shape off for a while: the user's own tick is saved, hunt 11-04 4)
                              "mask": self.mask.get(), "mask_defaults": self.mask_defaults,
                              "mask_preview": self.mask_preview.get(), "mask_fade": GHOST_FADE[0],
                              "tracks": self.track_list.state(),
                              "tumour_window": self.tumour_pos, "graph_window": self.graph_pos,
                              "claw_window": self.claw_pos, "strum_window": self.strum_pos, "chop_window": self.chop_pos, "trim_window": self.trim_pos, "path_window": self.path_pos, "hz_window": self.hz_pos, "hz_fx_height": self.hz_fx_h, "hz_loud_height": self.hz_loud_h,"hz_preview": self.hz_preview, "hz_tools": self.hz_window.tools_state() if self.hz_window else self.hz_tools,
                              "hz_view": self.hz_window.view_state() if self.hz_window else self.hz_view,
                              "history_window": self.history_pos, "history_undocked": self.history_undocked,
                              "places": getattr(self, "window_places", {}),
                              **self.tips.state(), **self.updates.state(), **self.tool_picker.state()}
            save_app_settings(data["window"])  # (sound / updates / tips go to user/settings.ini instead)
            data["project_file"], data["unsaved"] = self.project_file, self.is_dirty()  # (put back at start)
        # pictures in the project's folder (or one inside it) are saved relative to it: they move together
        folder = os.path.dirname(os.path.abspath(path))
        data["shapes"] = paths_for_file(data["shapes"], folder, True)
        if midi is not None:  # (Save a copy for sharing: the MIDI's copy beside it)
            data["loaded"]["path"] = midi
        if "loaded" in data:  # (the MIDI beside / under the project is found there if both are moved)
            data["loaded"].pop("rel", None)
            if not window:
                try:
                    rel = os.path.relpath(data["loaded"]["path"], folder)
                    if not rel.startswith(".."):
                        data["loaded"]["rel"] = rel
                except ValueError:  # (another drive)
                    pass
        text = project_json(data)
        if is_packed_name(path):
            import gzip
            write_bytes(path, gzip.compress(text.encode("utf-8"), compresslevel=1, mtime=0))
        else:
            write_text(path, text)

    def fits_memory(self, shapes, ppq):
        """Open project: asked only when its notes won't fit in the free memory (user 2026-10-20: never just for
        many notes, the user's own big projects open as before). ppq = the file's. True = go ahead."""
        try:
            ppq = int(ppq) if 1 <= int(ppq) <= 65535 else None
        except (TypeError, ValueError):
            ppq = None
        return ask_big(self, "open", sum(self.notes_about(sh, ppq) for sh in shapes))

    def load_autosave(self):
        """At start: the autosave. If it won't open it's kept aside (renamed, never overwritten) and the backup
        (the autosave as it was the last time the program started) opens instead; either way the user is told."""
        path, backup = self.autosave_path, backup_path(self.autosave_path)
        def opens(p):
            try:
                return self.load_file(p, quiet=True)
            except Exception:  # anything odd inside counts as damaged too, rather than not starting at all
                return False

        if not os.path.exists(path):
            return
        if opens(path):
            self.find_loaded_later()
            kept = ""
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                if self.load_lost:  # something was left out: the file as it was is kept aside, never overwritten
                    kept = keep_aside(path, "autosave-kept")
                    kept = tr("project.the_autosave_as_it_was_is_kept", kept=kept)
                write_bytes(backup, raw)  # this start's autosave becomes the backup
            except OSError:
                pass
            if self.load_notes:
                self.after(300, lambda: self.tell_load_notes(tr("project.opening_your_last_session"), kept))
            return
        try:
            kept = tr("project.it_was_kept_as", broken=keep_aside(path, "autosave-broken", move=True))
        except OSError:
            kept = ""
        if os.path.exists(backup) and opens(backup):
            self.find_loaded_later()
            what = (tr("project.opened_the_backup_instead_your_work",
                       strftime=time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(backup)))))
            what += "".join("\n\n" + note for note in self.load_notes)
        else:
            what = tr("project.there_s_no_usable_backup_either")
        self.after(300, lambda: messagebox.showwarning(
            tr("project.spiderweb"), tr("project.the_autosave_your_last_session_couldn", kept=kept, what=what),
            parent=self))

    def restore_window(self):
        try:
            try:
                data = read_json(self.autosave_path)
            except (OSError, ValueError):  # damaged: load_autosave opens the backup, so take its window
                data = read_json(backup_path(self.autosave_path))
            win = data.get("window") or {}
        except (OSError, ValueError, AttributeError):
            data, win = {}, {}
        win = with_app_settings(win)  # (user/settings.ini's sound / updates / tips)
        if win and not win.get("seen_version") and isinstance(data.get("app_version"), str):
            win["seen_version"] = data["app_version"]  # (settings.ini deleted: no What's new for the same version)
        seen = win.get("seen_version")
        if win and seen and str(seen) != VERSION:  # (an update's first start: the old settings copied first)
            of, mark, build = str(win.get("seen_build") or "").partition("|")  # ("1.7.3|2b8a0dc, 2026-11-09")
            if not mark or of != str(seen):  # (another version's build: an older Spiderweb started in between)
                build = ""
            build = build or (data.get("build") if data.get("app_version") == seen else "")
            try:
                from window import settings_backup
                made = settings_backup.make(seen, build or "")
                if made:
                    self.updates.backup = (made, settings_backup.label(seen, build or ""))
            except Exception:  # (only a safety copy: never stops Spiderweb from starting)
                write_log(*sys.exc_info(), "(backing up the older version's settings)")

        def kept(group):
            """One group put back; a broken one is skipped and logged, the rest still come back."""
            try:
                group()
            except (OSError, ValueError, AttributeError, TypeError, tk.TclError):
                write_log(*sys.exc_info(), "(putting the window back as it was)")

        kept(lambda: self.tips.restore(win))
        kept(lambda: self.updates.restore(win))
        kept(lambda: self.tool_picker.restore(win))
        if isinstance(win.get("hz_tools"), dict):  # (the Hz bass window's tools button: window/hz_draw.py)
            self.hz_tools = win["hz_tools"]
        if isinstance(win.get("hz_view"), dict):  # (its zoom + scroll: checked when the window opens)
            self.hz_view = win["hz_view"]

        def size():
            from window.widgets import fit_screen, placed
            geo = placed(self, win.get("geometry", "") if isinstance(win.get("geometry"), str) else "")
            if geo:
                self.geometry(geo)
            fit_screen(self)  # (saved on a bigger screen / at a smaller scaling; the first size grows with it too)
            if win.get("maximized"):
                self.update_idletasks()
                self.state("zoomed")
        kept(size)

        def velocity_height():
            if "velocity_height" in win:
                self._vel_height = max(int(50 * self.scale), int(float(win["velocity_height"]) * self.scale))
        kept(velocity_height)

        def tempo_height():
            if "tempo_height" in win:
                self._tempo_height = max(int(60 * self.scale), int(float(win["tempo_height"]) * self.scale))
            pane = self.tempo_pane
            pane.log = win.get("tempo_log") is True  # (linear unless exponential was picked)
            pane.box.graph.current(1 if pane.log else 0)
            view = win.get("tempo_view")
            if (isinstance(view, list) and len(view) == 2 and all(isinstance(v, (int, float)) for v in view)
                    and all(math.isfinite(v) for v in view) and view[0] < view[1]):
                pane.set_view(float(view[0]), float(view[1]))
        kept(tempo_height)

        def ticks():
            if win.get("midi_device") and keep_saved(str(win["midi_device"])):
                self.midi_device.set(OLD_NAMES.get(str(win["midi_device"]), str(win["midi_device"])))
            self.live.set(win.get("live") is True)
            self.mask.set(win.get("mask") is True)
            self.mask_preview.set(win.get("mask_preview") is not False)
            fade = win.get("mask_fade")
            if type(fade) is int and 0 <= fade <= 100:
                GHOST_FADE[0] = fade
                self.mask_fade_var.set(str(fade))
            self.track_list.set_state(win.get("tracks"))
            got = clean_mask(win.get("mask_defaults")) or MASK_DEFAULTS
            self.mask_defaults = dict(MASK_DEFAULTS, **{k: got[k] for k in THICKNESS})
            self.show_lines.set(win.get("show_lines") is not False)
            self.show_notes.set(win.get("show_notes") is not False)
            self.channels_folded.set(win.get("channels_folded") is True)
            got = win.get("stir_open")
            self.stir_open = {p for p in got if p in ("bends", "area", "flips")} if isinstance(got, list) else set()
            got = win.get("props_open")
            self.props_open = ({p for p in got if p in ("shape", "place", "gate", "colours")}
                               if isinstance(got, list) else set())
        kept(ticks)

        def places():
            for key, attr in (("tumour_window", "tumour_pos"), ("graph_window", "graph_pos"),
                              ("claw_window", "claw_pos"), ("strum_window", "strum_pos"), ("chop_window", "chop_pos"),
                              ("trim_window", "trim_pos"),
                              ("path_window", "path_pos")):
                pos = win.get(key)
                if isinstance(pos, str) and re.fullmatch(r"\+-?\d+\+-?\d+", pos):
                    setattr(self, attr, pos)
            from window.widgets import PLACE  # (every other window's place: widgets.remember_place)
            saved = win.get("places") if isinstance(win.get("places"), dict) else {}
            self.window_places = {k: v for k, v in list(saved.items())[:100] if isinstance(k, str) and len(k) <= 40
                                  and isinstance(v, str) and re.fullmatch(PLACE, v)}
            pos = win.get("hz_window")
            if isinstance(pos, str) and re.fullmatch(r"\d+x\d+\+-?\d+\+-?\d+", pos):
                self.hz_pos = pos
            h = win.get("hz_fx_height")
            if isinstance(h, int) and not isinstance(h, bool) and 0 < h < 10000:
                self.hz_fx_h = h
            h = win.get("hz_loud_height")
            if isinstance(h, int) and not isinstance(h, bool) and 0 < h < 10000:
                self.hz_loud_h = h
        kept(places)

        def sound():
            from window.hz_preview import VOICES, clean_settings
            self.hz_preview = clean_settings(win.get("hz_preview"))
            self.ring_check(quiet=True)  # (Auto's CC run-on remembered for its soundfont)
            v = win.get("play_voices")
            if isinstance(v, int) and not isinstance(v, bool) and VOICES[0] <= v <= VOICES[1]:
                self.play_voices = v
                self.voices_var.set(str(v))
            self.play_guard.set(win.get("play_voice_guard") is True)
            self.play_limiter.set(win.get("play_limiter") is True)
            self.edo10_piano.set(win.get("edo10_piano") is True)
            self.out.edo_drums = not self.edo10_piano.get()
            v = win.get("loaded_synths")
            if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 16:
                self.loaded_synths = v
                self.synths_var.set(str(v))
            self.take_kestrel_opts({k: win.get("kestrel_" + k) for k in self.kestrel_opts})
            self.slow_kestrel.set(win.get("kestrel_slow_down") is True)
            self.slow_builtin.set(win.get("play_slow_down") is True)
            from files.render_ahead import ENGINES, SECONDS
            if win.get("ahead_engine") in ENGINES:  # (kept as saved even when it can't play here: engine_used)
                self.ahead_engine.set(win["ahead_engine"])
            v = win.get("ahead_seconds")
            if isinstance(v, int) and not isinstance(v, bool) and SECONDS[0] <= v <= SECONDS[1]:
                self.ahead_seconds = v
                self.ahead_var.set(str(v))
            self.slow_ahead.set(win.get("ahead_slow_down") is True)
            self.take_ahead_opts({k: win.get("ahead_" + k) for k in self.ahead_opts})
            self.domino256.set(win.get("domino_256") is True)
            self.copy_domino.set(win.get("copy_domino") is True)
            self.copy_notes.set(win.get("copy_notes") is True)
            self.keep_colours.set(win.get("keep_colours") is True)
            stack = win.get("stacked_notes")  # (none yet: the old "Louder notes on top" tick's)
            self.stack_order.set(stack if stack in STACK_MODES else "loud" if win.get("loud_on_top") is True
                                 else STACK_MODES[0])
            self.hidden_shade.set(win.get("hidden_shade") is True)
            for key, steps in (("space_keys", SPACE_KEYS), ("space_bars", SPACE_BARS)):
                v = win.get(key)
                getattr(self, key).set(v if v in steps and not isinstance(v, bool) else 0)
            self.keep_offrange.set(win.get("keep_offrange") is True)
            self.wrap_offrange.set(win.get("wrap_offrange") is True)
            from files.settings import OFFRANGE_OPACITY, WRAP_SHOWN
            shown = win.get("show_wrapped")
            self.show_wrapped.set(shown if shown in WRAP_SHOWN else WRAP_SHOWN[0])
            from roll.roll_draw import OFF_FADE
            v = win.get("offrange_opacity")
            lo, hi, default = OFFRANGE_OPACITY
            OFF_FADE[0] = v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else default
            self.offrange_opacity.set(OFF_FADE[0])
            from notes import engine  # (shapes over the top key: made whole only with the room on, engine.SPACE_KEYS)
            if engine.SPACE_KEYS[0] != (self.space_keys.get() > 0):
                engine.SPACE_KEYS[0] = self.space_keys.get() > 0
                self._notes_cache.clear()
            self.sync_builtin()
        kept(sound)

        def history():
            pos = win.get("history_window")
            if isinstance(pos, str) and re.fullmatch(r"(\d+x\d+)?\+-?\d+\+-?\d+", pos):
                self.history_pos = pos
            self.history_undocked = win.get("history_undocked") is True
            if win.get("history") is True:  # it starts off; on again if it was on last time
                self.show_history.set(True)
                self.toggle_history(tip=False)
        kept(history)

        def velocity():
            if win.get("velocity") is True:  # it starts off; on again if it was on last time
                self.show_velocity.set(True)
                self.toggle_velocity(tip=False)
        kept(velocity)

        def tempo():
            if win.get("tempo") is True:  # (after the velocity pane: it goes under it)
                self.show_tempo.set(True)
                self.toggle_tempo(tip=False)
        kept(tempo)

    def schedule_autosave(self):
        self.sync_window_title()
        if self._autosave_job:
            self.after_cancel(self._autosave_job)
        self._autosave_job = self.after(1500, self.autosave)

    def autosave(self):
        self._autosave_job = None
        if getattr(self, "companion", None) is not None:  # (drawing on Domino: the project's PPQ is Domino's for now;
            return                                         # saved once back, window/domino_companion.py, hunt 11-06)
        if getattr(self, "fold_window", None):  # (Fold tracks' preview isn't the project yet: saved once it closes)
            self._autosave_job = self.after(1500, self.autosave)
            return
        try:
            self.write_json(self.autosave_path, window=True)
            self._autosave_failed = False
        except Exception as e:  # (not only the disk: any failure is told the same way)
            if not isinstance(e, OSError):
                write_log(*sys.exc_info(), "(autosave)")
            if not self._autosave_failed:  # (told once, again only if it worked in between)
                self._autosave_failed = True
                self.after_idle(lambda e=e: self.tell_autosave_failed(e))

    def tell_autosave_failed(self, e):
        if self.roll.drag:  # (not in the middle of a drag)
            self.after(500, lambda: self.tell_autosave_failed(e))
            return
        messagebox.showwarning(tr("project.spiderweb"), tr("project.autosave_failed", path=self.autosave_path, e=e),
                               parent=self)

    def close_autosave(self):
        """The last autosave, as the window closes. False = don't close: it couldn't be saved and the user chose to
        stay (or to save it as a project file, then cancelled that)."""
        if self._autosave_job:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        try:
            self.write_json(self.autosave_path, window=True)
            return True
        except Exception as e:
            if not isinstance(e, OSError):
                write_log(*sys.exc_info(), "(autosave on closing)")
            answer = messagebox.askyesnocancel(tr("project.spiderweb"),
                                               tr("project.autosave_failed_closing", path=self.autosave_path, e=e),
                                               icon="warning", default="yes", parent=self)
        return answer is False or answer is True and self.save_project()

    def open_project(self, path=None):
        """path = a file dropped / opened with Spiderweb (window/open_files.py); None = asks."""
        if path is None:
            path = filedialog.askopenfilename(title=tr("project.open_project"), initialdir=OUTPUT_DIR,
                                              filetypes=open_types())
        from window.drawer import offer_shape_file
        if path and offer_shape_file(self, path):  # (a library shape's file: offered into the library instead)
            return
        if not path or not self.may_replace(os.path.basename(path)):
            return
        if self.load_file(path):
            self._saved_shapes = self.saved_key()
            self.project_file = None if self.own_file(path) else os.path.normpath(path)
            self.mark_saved()
            self.sync_panel()
            self.tell_load_notes()
            self.find_loaded_later()

    def attach_midi(self, lm):
        """Load MIDI's end: a NEW project (user 2026-10-23: always one) with the file's PPQ (and 256 keys for a 256
        keys MIDI), its notes drawn under the shapes. Not an undo step: like Open, the old project was asked about
        first (may_replace). The view keeps its zoom and goes to the song's start (user 2026-10-24: not Fit view)."""
        r = self.roll
        zoom = r.sx, r.sy, r.view_top
        data = self.project_data()
        data.update(shapes=[], view=None, playhead=0.0, ppq=str(lm.ppq), bpm="120")
        data["tempo"] = conductor_line(lm)  # (the file's tempos + signatures, group C step 8)
        data.pop("loaded", None)
        data.pop("own_tracks", None)
        data.pop("own_next", None)
        data.pop("start_track", None)  # (the file's tracks instead of an empty Track 1)
        data.pop("file_tracks", None)
        data.pop("deleted", None)
        data.pop("file_order", None)
        data.pop("file_gone", None)
        data.pop("file_props", None)
        if lm.keys > 128:
            data["keys"] = KEYS[1]
        self.load_file(None, quiet=True, text=json.dumps(data))
        self.project_token = os.urandom(8).hex()  # (a new project: copies from before aren't laid on its tracks)
        self.loaded, self.midi_link = lm, lm.link()
        self.ahead_play.mark_dirty()  # (Rendered ahead: the old MIDI's made sound let go, the new one made)
        self.roll.forget_loaded()
        self.project_file = None
        self._saved_shapes = None
        self._saved_mark = None
        if hasattr(self, "track_list"):
            self.track_list.forget()
            self.track_list.view_t = 0.0
            self.track_list.request_redraw()
        self.sync_window_title()
        self.sync_panel()
        if zoom[0] is not None:
            r.sx, r.sy, r.view_top = zoom
            r.view_t = 0.0
            r.clamp_view()
        else:
            r.fit_view()
        r.request_redraw()
        self.schedule_autosave()
        self.read_ahead()

    def reattach_midi(self, lm, retempo=False):
        """An opened project's MIDI loaded again (window/load_midi.find_loaded): drawn under its shapes as when it was
        saved. A file of another PPQ (the changed file was picked) gives the project its PPQ (shapes are in beats);
        retempo (the changed file picked): its tempos + signatures replace the Conductor line."""
        self.loaded, self.midi_link = lm, lm.link()
        # (Rendered ahead, hunt 2026-11-03: made while stopped before the MIDI came in = without it; built again)
        self.ahead_play.mark_dirty()
        self.kick_ahead()
        if retempo and not same_conductor(self.tempo, conductor_line(lm)):
            self.tempo = conductor_line(lm)
            self.tempo_pane.forget()
            self.tempo_changed()
        self.roll.forget_loaded()
        from window.load_extract import sync_extract
        sync_extract(self)  # (the project's extracted shapes: their notes hidden again)
        if str(lm.ppq) != self.pvar["ppq"].get():
            self.pvar["ppq"].set(str(lm.ppq))
        if lm.keys > 128 and self.keys == KEYS[0]:
            self.keys_var.set(str(KEYS[1]))
        if hasattr(self, "track_list"):
            self.track_list.request_redraw()
        self.sync_window_title()
        self.sync_panel()
        self.roll.request_redraw()
        self.schedule_autosave()
        self.read_ahead()

    def drop_finding(self):
        """Another project / New: a MIDI still being loaded again for the one before stops (hunt 2026-10-24)."""
        running = getattr(self, "_finding", None)
        if running is not None and running.link is not self.midi_link:
            running.drop()
        from window.load_extract import stop_extract
        stop_extract(self)  # (an Extract still reading the MIDI before: stopped)
        job = getattr(self, "_box_reading", None)
        if job:
            job["cancel"] = True

    def find_loaded_later(self):
        """After a project with a MIDI link opened: the MIDI is loaded again (asked about when it's missing)."""
        if self.midi_link is not None and self.loaded is None:
            from window import open_files
            if open_files.hold_reload(self):  # (started with a file: the last session's MIDI waits, hunt 2026-10-29)
                return
            from window.load_midi import find_loaded
            self.after(300, lambda: find_loaded(self))

    def own_file(self, path):
        """The autosave (or its backup): never Save's file, Save asks for a name instead (user)."""
        same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        return same(path, self.autosave_path) or same(path, backup_path(self.autosave_path))

    def reattach_file(self):
        """At start: the project file the autosave's work belonged to comes back (user 2026-10-22), unsaved or not
        as it was."""
        name, unsaved = getattr(self, "_file_extra", (None, None))
        self.project_file = name if isinstance(name, str) and name and not self.own_file(name) else None
        self._saved_shapes = None if unsaved is not False or not self.project_file else self.saved_key()
        self._saved_mark = None
        if unsaved is False and self.project_file:
            self.mark_saved()
        self.sync_window_title()

    def file_key(self):
        """What a save keeps besides the shapes and the tempo line that changes the notes (the * in the title;
        zoom, scrolling and the play line don't count, user)."""
        return (tuple(v.get() for k, v in sorted(self.pvar.items())), self.keys_var.get(), self.edo31.get(),
                self.channel_mode.get(), str(self.channel_split), self.use_ch10.get(), self.ch10_order.get(),
                self.new_rounds.get(), self.pad_rounds.get(), self.single_split.get(), self.multi_cut.get(),
                self.multi_later.get(), self.multi_stretch.get())

    def is_dirty(self):
        """Changed since the last save / open: another newest undo step (undo back to it = saved again) or other
        Project boxes."""
        mark = self._saved_mark
        top = self.undo_stack[-1] if getattr(self, "undo_stack", None) else None
        return mark is None or mark[0] is not top or mark[1] != self.file_key()

    def changed_in_step(self):
        """A change put into the newest undo step (a live line's end square dragged, its Loops changed): when that
        step is the saved one, the project is unsaved now (hunt 10-27: no * came back)."""
        mark = self._saved_mark
        if mark is not None and self.undo_stack and mark[0] is self.undo_stack[-1]:
            self._saved_mark = None
        self.sync_window_title()

    def mark_saved(self):
        self._saved_mark = (self.undo_stack[-1] if self.undo_stack else None, self.file_key())
        self.sync_window_title()

    def sync_window_title(self):
        """The window's title: "song.json - Spiderweb 1.6.2", a * while unsaved, "Unsaved project" with no file; the
        project's MIDI after it in brackets (user 2026-10-26), "not found" while it's missing."""
        if not hasattr(self, "pvar"):
            return
        link = getattr(self, "midi_link", None)
        midi = ""
        if link is not None and link.get("path"):
            name = os.path.basename(link["path"])
            missing = self.loaded is None and getattr(self, "midi_missing", False)
            midi = tr("app.title_midi_missing" if missing else "app.title_midi", name=name)
        if self.project_file:
            title = tr("app.title_file", name=os.path.basename(self.project_file),
                       star=tr("app.title_star") if self.is_dirty() else "", midi=midi, VERSION=VERSION)
        else:
            title = tr("app.title_unsaved", midi=midi, VERSION=VERSION)
        if title != self._title:
            self._title = title
            self.title(title)

    def saved_key(self):
        """What Open project compares with the last save / open: the shapes and the tempo line."""
        return json.dumps([self.shapes, self.tempo if self.tempo != empty_tempo() else None]
                          + ([self.own_tracks] if self.own_tracks else []) + ([self.deleted] if self.deleted else [])
                          + ([{"start_track": True}] if self.start_track else [])
                          + ([{"file_order": self.file_order}] if self.file_order else [])
                          + ([{"file_gone": self.file_gone}] if self.file_gone else [])
                          + ([{"file_props": {str(n): v for n, v in self.file_props.items()}}] if self.file_props else []))

    def may_replace(self, name):
        """Before Open project replaces the shapes (Undo can't bring them back): if they (or the tempo line, the own
        tracks) aren't saved in a project file as they are, asks to save them first. False = don't open."""
        if (not self.shapes and self.tempo == empty_tempo() and not self.own_tracks
                or self.saved_key() == self._saved_shapes):
            return True
        answer = messagebox.askyesnocancel(tr("project.open_project"), tr("project.save_before_open", name=name),
                                           icon="warning", default="yes", parent=self)
        return answer is False or answer is True and self.save_project()

    def save_project(self, ask=False):
        """Into the project file opened / saved last; none yet (or ask: Save as…) = asks where. True if saved."""
        self.colour_edit_leave()  # (a colour step being edited: its later steps shown again, PLAN.md stage C)
        path = self.project_file
        if ask or not path:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            path = filedialog.asksaveasfilename(title=tr("project.save_project"),
                                                initialdir=os.path.dirname(path) if path else OUTPUT_DIR,
                                                initialfile=os.path.basename(as_packed_name(path)) if path else "",
                                                defaultextension=PROJECT_EXT, filetypes=save_types())
        if not path:
            return False
        try:
            self.write_json(path)
        except OSError as e:
            messagebox.showerror(tr("project.spiderweb"), couldnt_save(e))
            return False
        self._saved_shapes = self.saved_key()
        self.project_file = None if self.own_file(path) else os.path.normpath(path)
        self.mark_saved()
        self.schedule_autosave()  # (it remembers the file and that it's saved)
        return True

    def save_project_as(self):
        return self.save_project(ask=True)

    def browse_output(self):
        cur = self.pvar["output"].get()
        path = filedialog.asksaveasfilename(title=tr("project.midi_output"), initialdir=os.path.dirname(cur) or HERE,
                                            initialfile=os.path.basename(cur), defaultextension=".mid",
                                            filetypes=[(tr("project.midi_file"), "*.mid *.midi")])
        if path:
            self.pvar["output"].set(os.path.normpath(path))

    def copy_props(self):
        """midi_copy's props: Track Properties' (app.file_props) + each PART (PLAN #11 D) as its own entry (its
        channels alone, no meta events, its track's name) and its track without those channels."""
        props, names = {}, self.file_tracks()
        for n, p in self.file_props.items():
            parts = p.get("parts") or ()
            if p.get("name") is None and p.get("ch") is None and p.get("port") is None and not parts:
                continue  # (only its Ch rows' order: the track copied as it is)
            props[n] = {"name": p.get("name"), "ch": p.get("ch"), "port": p.get("port")}
            if parts:
                props[n]["keep"] = 0xFFFF & ~sum(1 << c for q in parts for c in q["chs"])
            for q in parts:
                props[part_key(n, q["id"])] = {"name": names.get(n, {}).get("name") or "", "ch": None,
                                               "keep": sum(1 << c for c in q["chs"]), "meta": 0}
        lm = self.loaded
        if lm is not None:  # (once any track is on another port, the file's tracks without a port event say port A:
            from notes.loaded import PORT, track_port  # some players carry a port over from the track before, hunt
            if any(t.get("port") for t in self.own_tracks) or any(p.get("port") for p in props.values()) \
                    or any(track_port(t) for t in lm.tracks):  # 2026-10-30; midi_out's rule for your own tracks)
                for n, t in enumerate(lm.tracks):
                    if t.notes and not t.metas(PORT) and props.get(n, {}).get("port") is None:
                        props.setdefault(n, {"name": None, "ch": None, "port": None, "said": True})["port"] = 0
        return props

    def export_loaded(self):
        """Export MIDI with a loaded MIDI: the file written out from itself (midi_copy: every byte copied and
        checked). The Conductor line still the file's own = exactly as it was loaded; changed (group C step 8) =
        the file's tempo events left out and the new list put into the track that had the first one (or each track
        that had a list: asked when there were several), Keep timing moving every event by the file's own note
        ticks. Spiderweb's own shapes = their own new tracks after the file's last one (plan step 10), moved by
        Keep timing like the file's notes; the message counts those sounding on a loaded note's channel + key. In
        the background (a 17 GB file takes a minute), the status line says how far."""
        lm = self.loaded
        if getattr(self, "_exporting", None):
            return
        if getattr(self, "_box_reading", None):  # (extracted notes not hidden yet: they'd be written twice, hunt)
            self.status.config(text=tr("load.export_reading"))
            return
        try:  # (Keep in memory too: the copy is made from the file, hunt 2026-10-24)
            lm.mf.same()
        except MidiError:
            messagebox.showerror(tr("project.spiderweb"), tr("load.export_gone", path=lm.path), parent=self)
            return
        retimed = not same_conductor(self.tempo, conductor_line(lm))
        if retimed and lm.mf.damaged():  # (its tempo events can't all be read: written as they are or not at all)
            messagebox.showerror(tr("project.spiderweb"), tr("load.tempo_damaged", tracks=", ".join(
                str(t.number + 1) for t in lm.mf.damaged()[:10])), parent=self)
            return
        out = self.outgoing()  # (+ wrapped kept notes)
        notes, cc, why, own = out[:0], {}, None, []
        written = len(self.file_rows())  # (the file's tracks written: a part = one more, removed ones fewer)
        if written > MAX_TRACKS + 1:
            messagebox.showerror(tr("project.spiderweb"), tr("project.too_many_tracks", tracks=written,
                                                              most=MAX_TRACKS + 1), parent=self)
            return
        if len(out) or self.own_written():  # (own tracks without notes are written too)
            why = ("load.shapes_damaged" if lm.mf.damaged() or M_CUT in lm.mf.marks else
                   "load.shapes_format" if lm.mf.format > 1 or M_RMID in lm.mf.marks else None)
            tracks = len(track_slots(out, self.pad_tracks())) + len(self.own_written())
            if why is None and written + tracks > MAX_TRACKS + 1:
                messagebox.showerror(tr("project.spiderweb"), tr("project.too_many_tracks", tracks=written + tracks,
                                                                  most=MAX_TRACKS + 1), parent=self)
                return
            if why is not None:
                if not messagebox.askyesno(tr("project.spiderweb"), tr("load.shapes_cant", why=tr(why)),
                                           icon="warning", parent=self):
                    return
            else:
                if not ask_big(self, "midi", len(out)):
                    return
                if long_silences(out) and not messagebox.askokcancel(
                        tr("project.spiderweb"), tr("project.long_silences", ppq=lm.ppq, beats=MAX_DELTA // lm.ppq),
                        icon="warning"):
                    return
                notes, cc = out.copy(), self.cc_messages(lm.ppq, self.read_project()[1])
                own = [dict(t) for t in self.own_written()]
        targets = []
        if retimed and tempo_new(lm, self.tempo):  # (signatures changed alone: the tempo stays as it is, no question)
            lists = tempo_tracks(lm)
            targets = lists[:1] or [0]  # (no tempo in the file: the first track gets it)
            if len(lists) > 1:  # (a list copied into several tracks, user 2026-10-23: warned + picked)
                from window.load_midi import ask_choice
                got = ask_choice(self, tr("project.spiderweb"), tr("load.tempo_lists", n=len(lists), tracks=", ".join(
                    str(n + 1) for n in lists[:10]) + (", …" if len(lists) > 10 else ""), first=lists[0] + 1),
                    [("one", tr("load.tempo_one", first=lists[0] + 1)), ("every", tr("load.tempo_every")), ("cancel", tr("load.cancel"))])
                if got == "cancel":
                    return
                targets = lists if got == "every" else lists[:1]
        path = output_path(self.pvar["output"].get())
        if os.path.exists(path) and not messagebox.askyesno(
                tr("project.confirm_save_as"), tr("project.already_exists_do_you_want_to",
                                                  basename=os.path.basename(path)), icon="warning", default="no"):
            return
        from files.midi_copy import copy_through
        job = self._exporting = {"done": 0, "total": lm.mf.size, "error": None, "finished": False, "cancel": False,
                                 "plan": None, "step": "load.exporting", "hits": 0, "tracks": 0,
                                 # (the track list's order: Remove overlaps' emptied tracks still counted)
                                 "filled": self.filled_slots(out), "least": self.least_tracks()}
        use10, pad = self.picture_use10, self.pad_tracks()
        # (the extracted notes as they are NOW: an undo / Extract while it runs changes nothing in it, hunt
        # 2026-10-25; the shapes left out = nothing hidden either, the file as it was)
        # (an Extract left as it was: its notes stay in the file as they are, byte for byte; user 2026-10-26)
        from window.load_extract import untouched
        spare, kept = untouched(self.shapes, notes) if why is None and len(notes) else ([], [])
        if kept:
            notes = notes[~np.isin(notes[:, 5], kept)]
        hid = lm.frozen(hide=why is None, spare=spare)
        left = lm.notes - len(hid.h_s)  # (the file's notes written: extracted ones left out)
        # (extracted notes keep their port: only their tracks on another port get one, the rest stay as ever)
        took = [i for i, sh in enumerate(self.shapes) if extract_ports([sh])]
        ports = {s for s in np.unique(notes[np.isin(notes[:, 5], took), 4]).tolist()
                 if slot_port(s, use10)} if took else None
        lanes = slot_lanes(notes, use10)
        # (own notes exactly where the file had them, extracted and left alone: an overlap the file already had)
        mine = ~same_rows(np.column_stack([notes[:, 0], notes[:, 1], lanes]),
                          np.column_stack([hid.h_s, hid.h_e, hid.h_lane]))
        before = notes
        order = self.file_rows()  # (the file's tracks as the track list shows them: dragged, D4)
        props = self.copy_props()  # (Track Properties' new names / channels: those tracks written again; parts)
        mixed = self.track_list.mixed_note()  # (tracks holding several channels by your doing, PLAN #11 E1)
        tempo, ppq, beats = copy.deepcopy(self.tempo), lm.ppq, self.beats
        heard = file_tempos(lm)  # (Keep timing: the notes keep the time they have in the file, user 2026-10-24)
        keeps = any(len(p) > 5 and p[5] is True for p in tempo["points"])

        def run():
            nonlocal notes, cc
            try:
                retime = None
                if retimed:
                    ticks = loose = np.zeros(0, np.int64)
                    if keeps:  # (Keep timing: every note start / end of the file keeps its time, one read first)
                        job["step"] = "load.reading_ticks"
                        ticks, loose = lm.anchors(cancel=lambda: job["cancel"],
                                                  progress=lambda d, n: job.update(done=d, total=n))
                        # (the shapes' notes keep their time too: anchors of their own)
                        ticks = np.union1d(ticks, np.concatenate([notes[:, 0], notes[:, 1]]))
                        loose = np.union1d(loose, np.array([m[0] for ms in cc.values() for m in ms], np.int64))
                    job.update(step="project.tempo_working", done=0, total=0)  # (no progress inside: a busy bar)
                    plan = job["plan"] = file_plan(tempo, 120, ppq, beats, ticks, ticks, None, loose, heard)
                    retime = retime_for(lm, tempo, plan, targets)
                    if keeps and len(notes):
                        notes, cc = timed_ticks(plan, notes, cc)
                added = None
                if len(notes) or own:
                    job["step"] = "load.checking_own"
                    # (the ticks before Keep timing: the loaded notes are read as they are in the file, and both
                    # move alike, so the same notes overlap; hunt 2026-10-25)
                    if len(notes):
                        job["hits"] = own_hits(hid, before[mine, 0], before[mine, 1], lanes[mine],
                                               cancel=lambda: job["cancel"],
                                               progress=lambda d, n: job.update(done=d, total=n))
                    # (shapes drawn into the file's own tracks, group D: their notes go INTO those tracks, only those
                    # written again)
                    tid = track_ids(notes[:, 4])
                    inside = tid < 0
                    if inside.any():
                        part, tid = notes[inside], tid[inside]
                        retime = (retime or Retime(None, (), {}, ())).add(
                            {-1 - k: note_inserts(part[tid == k], *own_events(cc, FILE0 + k))  # (+ CCs laid there)
                             for k in np.unique(tid).tolist()})
                        job["inside"] = len(part)
                        notes = notes[~inside]
                    if len(notes) or own:
                        pieces, job["tracks"], between = note_tracks(notes, use10, pad, cc, ports, own,
                                                                     len(order),  # (removed tracks: not written)
                                                                     filled=job["filled"], least=job["least"])
                        added = Added(pieces, job["tracks"], 16 * len(notes), between)
                job["step"] = "load.exporting"
                os.makedirs(os.path.dirname(path), exist_ok=True)
                copy_through(lm.mf, path, progress=lambda d, n: job.update(done=d, total=n),
                             cancel=lambda: job["cancel"], retime=retime, added=added,
                             hidden=hid.skip_of, order=order, props=props)  # (closing Spiderweb: its temp file goes)
            except BaseException as e:  # (said below)
                job["error"] = e
            job["finished"] = True

        def own_note():  # (the message's lines about the shapes added)
            inside = job.get("inside", 0)
            if not len(notes) and not own and not inside:
                return ""
            out = tr("load.own_added", notes=trn("n.notes", len(notes), n=len(notes)),
                     tracks=trn("n.tracks", job["tracks"], n=job["tracks"])) if len(notes) or own else ""
            if inside:
                out += tr("load.own_inside", notes=trn("n.notes", inside, n=inside))
            if job["hits"]:
                out += trn("load.own_hits", job["hits"], n=job["hits"])
            if len(hid.h_s):
                out += trn("load.own_extracted", len(hid.h_s), n=len(hid.h_s))
            return out + self.cc_note(cc, True)

        def poll():
            if not job["finished"]:
                win.refresh()  # (its own window now, not the status line: user 2026-10-29)
                self.after(win_poll, poll)
                return
            self._exporting = None
            self.after_idle(self.lanes_after_export)  # (31-EDO lanes changed while it ran: taken now)
            if win.winfo_exists():
                win.destroy()
            # (tracks dragged, renamed, re-channelled, split or removed: not "exactly as loaded", 10-29 measure)
            listed = order != list(range(len(lm.mf.tracks))) or any(not p.get("said") for p in props.values())
            e = job["error"]
            if isinstance(e, Cancelled):
                self.status.config(text=tr("export.cancelled"))
                return
            if isinstance(e, FileChanged):
                messagebox.showerror(tr("project.spiderweb"), tr("load.export_gone", path=lm.path), parent=self)
            elif e is None and job["plan"] is not None:
                messagebox.showinfo(tr("project.spiderweb"), tr(
                    "load.exported_tempo" if targets else "load.exported_sigs", path=path, notes=trn("n.notes", left, n=left),
                    tracks=", ".join(str(n + 1) for n in targets[:10]) + (", …" if len(targets) > 10 else ""),
                    note=self.tempo_notes(job["plan"]) + own_note() + mixed)
                                    + (tr("load.exported_tracks_too") if listed else ""))
            elif e is None:
                messagebox.showinfo(tr("project.spiderweb"), tr(
                    ("load.exported_own_extracted" if len(hid.h_s) else "load.exported_own") if len(notes) else
                    "load.exported_tracks" if listed else "load.exported", path=path, notes=trn("n.notes", left, n=left))
                                    + (tr("load.exported_tracks_too") if listed and len(notes) else "")
                                    + own_note() + mixed)
            elif isinstance(e, MidiError):
                messagebox.showerror(tr("project.spiderweb"), tr(e.key, **e.info))
            elif isinstance(e, OSError):
                messagebox.showerror(tr("project.spiderweb"), couldnt_save(e))
            else:
                write_log(type(e), e, e.__traceback__, "(exporting a loaded MIDI)")
                messagebox.showerror(tr("project.spiderweb"), tr("load.export_failed", e=e))
        from window.export_window import POLL_MS as win_poll, ExportWindow
        steps = (["load.reading_ticks"] if retimed and keeps else []) + (["project.tempo_working"] if retimed else []) \
            + (["load.checking_own"] if len(notes) or own else []) + ["load.exporting"]
        win = ExportWindow(self, job, path, steps)  # (steps, bar, Cancel; user 2026-10-29)
        threading.Thread(target=run, daemon=True, name="export loaded midi").start()
        poll()

    def save_sharing_copy(self):
        """File → Save a copy for sharing…: the project saved where picked, its MIDI copied beside it (asked first,
        with the size), so the two travel together; the project in use keeps its own file and MIDI."""
        lm = self.loaded
        if lm is None or getattr(self, "_exporting", None):
            return
        path = filedialog.asksaveasfilename(title=tr("load.share_title"), defaultextension=PROJECT_EXT,
                                            initialdir=os.path.dirname(self.project_file or "") or OUTPUT_DIR,
                                            initialfile=os.path.basename(as_packed_name(self.project_file or ""))
                                            or "song" + PROJECT_EXT, filetypes=save_types())
        if not path:
            return
        midi = os.path.join(os.path.dirname(os.path.abspath(path)), os.path.basename(lm.path))
        from files.midi_copy import same_file
        if same_file(path, lm.path) or same_file(path, midi):  # (the project would be written over the MIDI)
            messagebox.showerror(tr("project.spiderweb"), tr("load.share_same", name=os.path.basename(path)),
                                 parent=self)
            return
        there = same_file(lm.path, midi)  # (the project goes beside its MIDI: nothing to copy)
        if not there:
            text = tr("load.share_ask", size=f"{lm.mf.size / 1e6:,.1f}", name=os.path.basename(midi),
                      folder=os.path.dirname(midi))
            if os.path.exists(midi):
                text += "\n\n" + tr("load.share_replace", name=os.path.basename(midi))
            if not messagebox.askyesno(tr("project.spiderweb"), text, parent=self):
                return
            try:
                lm.mf.same()
            except MidiError:
                messagebox.showerror(tr("project.spiderweb"), tr("load.export_gone", path=lm.path), parent=self)
                return
        # (the project as it is NOW: New / Open while the copy runs must not change what's shared, hunt 2026-10-24)
        data = json.loads(json.dumps(self.project_data()))
        job = self._exporting = {"done": 0, "total": lm.mf.size, "error": None, "finished": there, "cancel": False}

        def run():
            tmp = midi + ".tmp"
            try:
                with open(lm.path, "rb") as src, open(tmp, "wb") as dst:
                    while True:
                        if job["cancel"]:
                            raise Cancelled()
                        piece = src.read(16 << 20)
                        if not piece:
                            break
                        dst.write(piece)
                        job["done"] += len(piece)
                lm.mf.same()  # (changed while it was copied: the copy isn't kept)
                if job["done"] != lm.mf.size:
                    raise FileChanged()
                os.replace(tmp, midi)
            except BaseException as e:  # (said below)
                job["error"] = e
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            job["finished"] = True

        def poll():
            if not job["finished"]:
                self.busy(tr("load.sharing", pct=int(100 * job["done"] / max(1, job["total"]))))
                self.after(100, poll)
                return
            self._exporting = None
            self.after_idle(self.lanes_after_export)
            self.busy(None)
            e = job["error"]
            if isinstance(e, Cancelled):
                return
            if e is None:
                try:
                    self.write_json(path, midi=midi, data=data)
                except OSError as e2:
                    e = e2
            if isinstance(e, FileChanged):
                messagebox.showerror(tr("project.spiderweb"), tr("load.export_gone", path=lm.path), parent=self)
            elif isinstance(e, OSError):
                messagebox.showerror(tr("project.spiderweb"), couldnt_save(e))
            elif e is not None:
                write_log(type(e), e, e.__traceback__, "(saving a copy for sharing)")
                messagebox.showerror(tr("project.spiderweb"), tr("load.export_failed", e=e))
            else:
                messagebox.showinfo(tr("project.spiderweb"), tr("load.shared", path=path, name=os.path.basename(midi)),
                                    parent=self)
        if not there:
            threading.Thread(target=run, daemon=True, name="copy loaded midi").start()
        poll()

    def generate(self):
        self.colour_edit_leave()  # (a colour step being edited: the whole result written, PLAN.md stage C)
        if self.loaded is not None:
            return self.export_loaded()
        if self.midi_link is not None:  # (its MIDI isn't loaded: the file holds the notes, nothing to export without it)
            if messagebox.askyesno(tr("project.spiderweb"), tr("load.export_missing", path=self.midi_link["path"]),
                                   parent=self):
                from window.load_midi import find_loaded
                find_loaded(self)
            return
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), str(e))
            return
        out = self.outgoing()  # (+ wrapped kept notes)
        if not len(out):
            messagebox.showerror(tr("project.spiderweb"),
                                 tr("project.no_notes_yet_draw_something_inside", keys=self.keys - 1))
            return
        tracks = len(track_slots(out, self.pad_tracks(), self.least_tracks())) + len(self.with_file_tracks())
        if tracks > MAX_TRACKS:
            messagebox.showerror(tr("project.spiderweb"), tr("project.too_many_tracks", tracks=tracks, most=MAX_TRACKS))
            return
        if not ask_big(self, "midi", len(out)):
            return
        path = output_path(self.pvar["output"].get())
        if os.path.exists(path) and not messagebox.askyesno(  # (asked like the Output file dialog does)
                tr("project.confirm_save_as"), tr("project.already_exists_do_you_want_to",
                                                  basename=os.path.basename(path)),
                icon="warning", default="no"):
            return
        if long_silences(out) and not messagebox.askokcancel(
                tr("project.spiderweb"), tr("project.long_silences", ppq=ppq, beats=MAX_DELTA // ppq), icon="warning"):
            return
        cc = self.cc_messages(ppq, bpm)
        notes, cc, plan = self.tempo_timed(ppq, bpm, beats, cc)
        if not self.write_out(path, "project.saving_midi",  # (millions of notes take a few seconds)
                              lambda: write_midi(path, ppq, bpm, beats, notes, self.picture_use10,
                                                 self.pad_tracks(), cc, plan["events"], plan["sigs"], plan["keys"],
                                                 own=self.with_file_tracks(), least=self.least_tracks(),
                                                 filled=self.filled_slots(notes))):
            return
        channels = tracks  # (the tracks written, in every mode: pastes / your tracks make several in Single too)
        note = (tr("project.ppq_many_midi_programs_can_t", ppq=ppq, PPQ_WARN=PPQ_WARN)
                if ppq >= PPQ_WARN else "")
        if (out[:, 2] > 127).any():
            note += tr("project.it_has_keys_above_127_256")
        if slot_ports(auto_count(out[:, 4]), cc, self.picture_use10):  # (as write_midi decides)
            note += tr("project.ports_midi_edo" if engine.edo_lanes else "project.ports_midi")
        mixed = self.track_list.mixed_note()  # (tracks holding several channels: no longer one each, PLAN #11 E1)
        note += mixed + self.tempo_notes(plan) + self.cc_note(cc, True)
        messagebox.showinfo(tr("project.spiderweb"),
                            tr("project.saved_notes_mixed" if mixed else "project.saved_notes_on_track_s_one", notes=trn("n.notes", len(notes),
                                                                               n=len(notes)),  # (as written)
                               tracks=trn("n.tracks", channels, n=channels), path=path, note=note))

    def tempo_notes(self, plan):
        """The export message's lines about the Tempo pane (file_plan's warnings)."""
        note = ""
        if plan["bad"]:
            note += tr("project.tempo_catch_failed", where=", ".join(self.bar_text(b) for b in plan["bad"]))
        if plan["jumps"]:
            note += tr("project.tempo_jump_notes", where=", ".join(self.bar_text(b) for b in plan["jumps"][:5]))
        if plan["hz"]:
            note += tr("project.tempo_hz", where=self.bar_text(plan["hz"][0]))
        if plan["dense"]:
            note += tr("project.tempo_dense", where=self.bar_text(plan["dense"][0]))
        if plan.get("unfit"):
            note += tr("project.tempo_unfit", where=self.bar_text(plan["unfit"][0]))
        if plan.get("moved"):  # (bar carry-over, user 2026-10-24: no surprises in the export)
            note += tr("project.tempo_moved", where=self.bar_text(plan["moved"][0]))
        if plan.get("gave"):  # (a heads-up only, user 2026-10-23)
            n = plan.get("gave_n") or len(plan["gave"])
            note += trn("project.tempo_cc_gave", n, n=n, where=self.bar_text(plan["gave"][0]))
        return note

    def tempo_timed(self, ppq, bpm, beats, cc, notes=None, upto=None):
        """(notes, cc, file_plan) for the MIDI file: the Tempo pane's changes, notes and CC messages in Keep timing
        parts moved to the ticks that keep their time (notes/tempo_file.py). notes: the ones going out (default:
        every note); upto: only the ticks up to there count (Copy to Domino: the song up to the copy's end)."""
        notes = self.outgoing() if notes is None else notes  # (+ wrapped kept notes: App.outgoing)
        # (Hz basses with finer gates: inside Keep timing parts drawn faster, made again on the file's ticks after
        # the other notes lay them out, hz_fine.py)
        fine = fine_owners(self.shapes) if len(notes) else []
        parts = finer_parts(self.tempo, bpm, ppq, int(notes[:, 1].max())) if fine else []
        anchors = notes[~swapped(notes, fine, parts)] if parts else notes
        ticks = np.concatenate([anchors[:, 0], anchors[:, 1]])
        loose = np.concatenate([np.zeros(0, np.int64)] + [np.array([m[0] for m in ms], np.int64)
                                                         for ms in cc.values()])  # (CC: give way, user)
        if upto is not None:
            ticks, loose = ticks[ticks <= upto], loose[loose <= upto]
        if has_tempo(self.tempo):  # (a long Keep timing part of many notes takes a moment)
            self.busy(tr("project.tempo_working"))
        try:
            plan = file_plan(self.tempo, bpm, ppq, beats, ticks, anchors[:, 0], None, loose)
        finally:
            if has_tempo(self.tempo):
                self.busy(None)
        # (a Hz bass follows the tempo it was made for, user 2026-10-23: the warning = one made for another tempo
        # than it's under now, from its first note going out)
        if plan["gave"]:  # (messages, not ticks: 15 tracks' CC on one tick are 15, hunt 2026-10-24)
            plan["gave_n"] = int(np.isin(loose, np.rint(np.array(plan["gave"]) * ppq).astype(np.int64)).sum())
        stale = [i for i, sh in enumerate(self.shapes) if sh.get("hz") and self.hz_out_of_tune(sh)]
        on = notes[np.isin(notes[:, 5], stale), 0] if stale else ()
        plan["hz"] = [int(on.min()) / ppq] if len(on) else []
        if self.tempo.get("points") and any(len(p) > 5 and p[5] is True for p in self.tempo["points"]):
            moved, cc = timed_ticks(plan, notes, cc)
            if parts:
                mode = self.channel_mode.get()

                def made(i, p):  # (shape i's notes + tracks as the piano roll makes them, all_notes: at PPQ p)
                    sh = self.shapes[i] if mode == "auto" else one_voice(self.shapes[i])
                    if self.rounds_apart() and has_colours(sh):
                        sh = dict(sh, _rounds=True)
                    return shape_notes_tracks(sh, p, self.keys)
                moved = finer_notes(fine, notes, moved, parts, self.tempo, bpm, ppq, plan["events"], made, mode)
            notes = moved
        return notes, cc, plan

    def cc_messages(self, ppq, bpm, only=None, plain=False):
        """The Hz basses' CC lines as MIDI messages on the slots (tracks) their notes are on: {slot: [(tick, status,
        data 1, data 2)]} (hz_cc.slot_events; only = the shapes' numbers to take, None: all). Pasted notes' passengers
        too (Multi channel only: each track's on the slot its notes got), each as its MIDI messages with a 5th part:
        (tag, body) = Domino's event to write back as it was (on the last of its messages; status None when it has
        no MIDI message), False = a MIDI-only message (passengers.midi_messages)."""
        # (the export's own layout: wrapped kept notes can move channels, App.outgoing_layout; plain = the piano
        # roll's, for copies of the notes as shown: the track list's boxes)
        laid, voice_slots = (self.rendered, self.voice_slots) if plain else self.outgoing_layout()
        out = slot_events(self.shapes, laid, ppq, bpm, only, voice_slots)
        added = set()
        auto = self.channel_mode.get() == "auto"
        groups = {}  # (a paste (or a shape alone) -> ([its shapes], latest values: cc_put_back))
        for i, sh in enumerate(self.shapes):
            slots = voice_slots.get(i) if "cc" in sh and (only is None or i in only) else None
            if slots is None and auto and "cc" in sh and sh.get("own_slots") and (only is None or i in only):
                # (a copy whose tracks ARE its slots, made with the channel rule of then: moved like its notes, hunt)
                ks = sorted({e[1] for e in unpack_cc(sh["cc"])})
                slots = dict(zip(ks, np.asarray(as_used(np.asarray(ks, np.int64), slot_rule(sh),
                                                        self.picture_use10)).tolist()))
            if not slots:
                continue
            paste = sh.get("paste")
            if (paste or ("one", i)) not in groups:  # (every piece of one paste counts as it, CCs or not: hunt)
                groups[paste or ("one", i)] = ([j for j, s in enumerate(self.shapes) if s.get("paste") == paste]
                                               if paste else [i], {})
            group = groups[paste or ("one", i)]

            def add(slot, t, tag, body):
                msgs = midi_messages(tag, body) or [(None, 0, 0)]
                out.setdefault(int(slot), []).extend(
                    (t, s, a, b, (tag, body) if k == len(msgs) - 1 else False) for k, (s, a, b) in enumerate(msgs))
                added.add(int(slot))
                for s, a, b in msgs:  # (each CC's / the bend's latest value on the slot: (tick, value))
                    k = a if s == 0xB0 else "bend" if s == 0xE0 else None
                    if k is not None and t >= group[1].get((int(slot), k), (-1,))[0]:
                        group[1][int(slot), k] = (t, b if s == 0xB0 else a | b << 7)

            events = placed(sh, unpack_notes(sh["notes"]), ppq)
            for t, track, tag, body in events:
                slot = slots.get(track)
                if slot is not None:  # (a track without notes here: no channel to go on)
                    add(slot, t, tag, body)
            strips = voice_slots.get(("strips", i))
            if strips:  # (Channel per strip: each later strip's channel gets its track's values in effect at its
                # start, then its events while it sounds, hunt 2026-10-23)
                for track, slot in slots.items():
                    mine = [e for e in events if e[1] == track]
                    for s, s0, s1 in (strips.get(int(slot)) or ())[1:]:
                        before = {}  # (the latest of each kind before the strip starts, kept in their order)
                        for e in mine:
                            if e[0] >= s0:
                                break
                            k = event_kind(e[2], e[3])
                            if k is not None:
                                before.pop(k, None)
                                before[k] = e
                        for e in before.values():
                            add(s, s0, e[2], e[3])
                        for e in mine:
                            if s0 <= e[0] <= s1:
                                add(s, e[0], e[2], e[3])
        for us, last in groups.values():
            self.cc_put_back(us, last, out, only)
        for slot in added:
            out[slot].sort(key=lambda e: e[0])
        return out

    def shown_cc(self):
        """cc_messages for showing them (the track list's CC marks, the ruler's CC row; user 2026-10-29: CC events
        never silent), worked out once per notes change; {} with no shape sending any."""
        key = (id(self.rendered), self.ppq, self.channel_mode.get())
        got = getattr(self, "_shown_cc", None)
        if got is None or got[0] != key:
            from notes.hzbass import has_cc
            sends = any("cc" in sh or sh.get("kind") is not None and has_cc(sh.get("hz") or {}) for sh in self.shapes)
            got = self._shown_cc = (key, self.cc_messages(self.ppq, self.tempo_pane.base()) if sends else {})
        return got[1]

    def cc_put_back(self, us, last, out, only=None):
        """At the end of pasted shapes us (one paste, its pieces) on each of their slots, every CC they left changed
        set back to its usual value (the pitch bend to its middle), only when other notes going out (only: these
        shapes' numbers, None: all) use that channel after it (user 2026-10-22: the channel is reused later; alone, a
        round trip stays exactly as it was). last = {(slot, CC number / "bend"): (tick, value)}."""
        r = self.rendered
        if only is not None:  # (a copy of some shapes: only what's in it counts, hunt)
            r = r[np.isin(r[:, 5], sorted(set(only) | set(us)))]
        for (slot, k), (_, v) in sorted(last.items(), key=lambda e: str(e[0])):
            on = r[r[:, 4] == slot]
            ours = np.isin(on[:, 5], us)
            if not ours.any():
                continue
            end = int(on[ours, 1].max())
            if not (~ours & (on[:, 0] >= end)).any():
                continue
            if k == "bend":
                if v != 8192:
                    out[slot].append((end, 0xE0, 0, 64))
            elif k not in STAY and k in USUAL and v != USUAL[k]:
                out[slot].append((end, 0xB0, k, USUAL[k]))

    def cc_note(self, cc, midi=False, shapes=None):
        """The export message's line about pasted notes' CCs (passengers.py): not written outside Multi channel;
        midi: Domino's own events (no MIDI message) left out of the MIDI file. shapes: the ones going out."""
        mine = [sh for sh in (self.shapes if shapes is None else shapes) if "cc" in sh]
        if not mine:
            return ""
        laid = {id(self.shapes[i]) for i in self.voice_slots if isinstance(i, int) and i < len(self.shapes)}
        if self.channel_mode.get() != "auto" and any(id(sh) not in laid for sh in mine):  # (laid on tracks: written)
            return tr("project.cc_single_channel")
        own = sum(1 for ms in cc.values() for e in ms if e[1] is None) if midi else 0
        return trn("project.cc_left_out_midi", own, n=own) if own else ""

    def write_out(self, path, busy, write):
        """write() makes the file at path (its folder made first), with the busy text key showing. False when it
        couldn't (said in plain words)."""
        self.busy(tr(busy))
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write()
        except OSError as e:
            messagebox.showerror(tr("project.spiderweb"), couldnt_save(e))
            return False
        except MemoryError:
            messagebox.showerror(tr("project.spiderweb"), tr("big_ask.out_of_memory"))
            return False
        finally:
            self.busy(None)
        return True

    def export_dms(self):
        """Export to Domino: every note in a .dms file (Domino's own song file, PPQ up to 65535) next to the MIDI
        file the Output file box names, with the same name. Asked before replacing, like Export MIDI."""
        self.colour_edit_leave()  # (a colour step being edited: the whole result written, PLAN.md stage C)
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), str(e))
            return
        self.catch_up_notes(late=False)
        notes = self.outgoing()  # (+ wrapped kept notes)
        high = int((notes[:, 2] > 127).sum()) if self.keys > 128 else 0  # (256 keys: Domino only has 128)
        mod = high and self.domino256.get()  # (kept: only Domino's 256 keys mod opens them)
        if not len(notes) or high == len(notes) and not mod:
            messagebox.showerror(tr("project.spiderweb"),
                                 tr("project.no_notes_yet_draw_something_inside", keys=min(self.keys, 128) - 1)
                                 + self.loaded_left_out())
            return
        # (worked out with every note, as Export MIDI does, so the files line up tick for tick; then the keys
        # Domino can't hold left out)
        notes, cc, plan = self.tempo_timed(ppq, bpm, beats, self.cc_messages(ppq, bpm))
        if high and not mod:
            notes = notes[notes[:, 2] <= 127]
        if int(notes[:, 1].max()) > LONGEST:
            messagebox.showerror(tr("project.spiderweb"), tr("project.too_long_for_domino", longest=LONGEST))
            return
        if len(notes) > MOST_NOTES:
            messagebox.showerror(tr("project.spiderweb"), tr("project.too_many_for_dms", notes=len(notes), most=MOST_NOTES))
            return
        if not ask_big(self, "dms", len(notes)):
            return
        notes, long = cut_gates(notes)
        path = dms_path(output_path(self.pvar["output"].get()))
        if os.path.exists(path) and not messagebox.askyesno(
                tr("project.confirm_save_as"), tr("project.already_exists_do_you_want_to",
                                                  basename=os.path.basename(path)),
                icon="warning", default="no"):
            return
        # (changes past the song's end make no sound: left out, Domino's ticks stop; signatures too without a tempo
        # line: one far past the song failed the export, hunt 2026-10-23)
        last = min(max([int(notes[:, 1].max())] + [ms[-1][0] for ms in cc.values() if ms]) + beats * ppq, LONGEST)
        tempo = [e for e in plan["events"] if e[0] <= last] if has_tempo(self.tempo) else None
        sigs = [s for s in plan["sigs"] if s[0] <= last]
        keys = [k for k in plan["keys"] if k[0] <= last]
        if not self.write_out(path, "project.saving_dms", lambda: write_bytes(
                path, dms_data(notes, ppq, bpm, beats, self.picture_use10, drums=not self.all16(),
                               pad=self.pad_tracks(), cc=cc, tempo=tempo, sigs=sigs, keys=keys,
                               own=self.with_file_tracks(), least=self.least_tracks(),
                               typed=plan.get("typed"), filled=self.filled_slots(notes)))):
            return
        tracks = len(track_slots(notes, self.pad_tracks(), self.least_tracks())) + len(self.with_file_tracks())
        ports = slot_ports(auto_count(notes[:, 4]), cc, self.picture_use10)  # (as dms_data decides)
        text = (tr("project.saved_dms", notes=trn("n.notes", len(notes), n=len(notes)),
                   tracks=trn("n.tracks", tracks, n=tracks), path=path)
                + (tr("project.dms_keys_256") if mod else
                   trn("project.notes_above_key_127_left_out", high, high=high) if high else "")
                + (trn("project.dms_long_gates", long, long=long, most=GATE_MAX) if long else "")
                + (tr("project.ports_dms_edo" if engine.edo_lanes else "project.ports_dms") if ports else "") + self.track_list.mixed_note(domino=True)
                + self.loaded_left_out())
        slow = tempo_items(tempo)[1] if tempo else 0
        warn = self.tempo_notes(plan) + (tr("project.dms_tempo_slow") if slow else "") + self.cc_note(cc)
        (messagebox.showwarning if long or ports or warn else messagebox.showinfo)(tr("project.spiderweb"),
                                                                                    text + warn)

    def loaded_left_out(self, short=False):
        """The Domino messages' line when a MIDI is loaded: its notes never go into a Domino copy / .dms (plan step
        10), only the shapes."""
        if self.loaded is None and self.midi_link is None:
            return ""
        return tr("load.domino_short" if short else "load.domino_left_out")

    def list_boxed(self):
        """The track list showing with Select boxes kept: which of rendered's notes start inside them (Copy to
        Domino copies only those, user 2026-10-26: "copy only the selected area"); else None."""
        from window.track_tempo import note_boxes
        if not self.tracks_shown() or not note_boxes(self.track_list.boxes):
            return None
        from window.track_moves import boxed
        return boxed(self, note_boxes(self.track_list.boxes))

    def copied_whole(self):
        """True when a copy for Domino takes the tempo along: what's copied was picked by Ctrl+A (every shape) and
        nothing since (user 2026-11-01), or the track list's boxes hold the Conductor (a box from its row, a ruler
        band: user 2026-11-05, like Domino's)."""
        tl = getattr(self, "track_list", None)
        if self.tracks_shown() and tl is not None and tl.boxes:
            from window.track_tempo import tempo_spans
            return bool(tempo_spans(tl.boxes))
        return bool(self.sels) and getattr(self, "picked_all", None) == frozenset(self.sels)

    def tempo_for_domino(self, lo, hi):
        """The Conductor pane's copied stretch (beats lo..hi) as Domino's clipboard bytes: a Conductor track alone
        (user 2026-11-01), times as Export MIDI writes them -> (raw, status text); time / key signatures only when
        the copy starts on a bar line (off it, Domino would put one mid-bar)."""
        ppq, bpm, beats = self.read_project()
        top = round(max(hi, lo) * ppq)
        plan = self.tempo_timed(ppq, bpm, beats, {}, upto=top)[2]
        first, end = (int(t) for t in plan["map"](np.array([round(lo * ppq), top], np.int64)).tolist())
        end = max(end, first + 1)
        items, slow = tempo_items(plan["events"], domino_sigs(plan["sigs"], first, ppq), first, end,
                                  plan["keys"], plan.get("typed"))
        return conductor_clip(ppq, items, end - first), slow

    def tempo_to_domino(self, clip, quiet):
        """The Conductor pane's copy (clip = app.tempo_clip) on the clipboard for Domino: Ctrl+C there with "Ctrl+C
        also copies for Domino" (quiet: said in the status line only) or Copy to Domino after clicking the pane."""
        if not clipboard.RAW:
            if not quiet:
                messagebox.showinfo(tr("project.spiderweb"), tr("project.notes_clipboard_windows_only"))
            return
        try:
            raw, slow = self.tempo_for_domino(clip["from"], clip["from"] + clip["length"])
            copied = put_on_clipboard(raw)
        except (ValueError, MemoryError) as e:
            copied, why = False, (str(e) if isinstance(e, ValueError) else tr("big_ask.out_of_memory"))
        else:
            why = tr("project.couldn_t_use_the_clipboard_another")
        if not copied:
            if quiet:
                self.status.config(text=self.status.cget("text") + tr("app.not_copied_for_domino"))
            else:
                messagebox.showerror(tr("project.spiderweb"), why)
            return
        self.remember_clip()  # (the piano roll's Ctrl+V doesn't take it for Domino's notes)
        if quiet:
            self.status.config(text=self.status.cget("text") + tr("app.copied_for_domino_too"))
            return
        self.status.config(text=tr("tempo.copied_for_domino"))
        if slow:
            messagebox.showwarning(tr("project.spiderweb"), tr("tempo.copied_for_domino") + tr("project.dms_tempo_slow"))

    def copy_to_domino(self):
        """Ctrl+Shift+C: the selected shapes' notes (all notes when nothing is selected; the track list's Select
        boxes kept: the notes starting in them) on the clipboard, for
        Ctrl+V in Domino. One track per channel that has notes; the copy starts on the first note or at the bar
        line before it (domino_start). The tempo goes along only after Ctrl+A (copied_whole). The Conductor pane
        clicked last: its copy, the tempo alone (tempo_to_domino)."""
        if getattr(self, "key_pane", None) is self.tempo_pane:
            self.tempo_pane.copy(domino=True)
            return
        self.colour_edit_leave()  # (a colour step being edited: the whole result copied, PLAN.md stage C)
        tl = self.track_list
        if self.tracks_shown() and tl.boxes:  # (boxes on the Conductor row alone: its stretch alone, user 11-05)
            from window.track_tempo import note_boxes, tempo_spans
            spans = tempo_spans(tl.boxes)
            if spans and not note_boxes(tl.boxes):
                self.tempo_to_domino({"from": spans[0][0], "length": spans[-1][1] - spans[0][0]}, quiet=False)
                return
        got = self.domino_copy()
        if got is None:
            return
        raw, text, warn = got
        try:
            copied = put_on_clipboard(raw)
        except MemoryError:
            messagebox.showerror(tr("project.spiderweb"), tr("big_ask.out_of_memory"))
            return
        if not copied:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_use_the_clipboard_another"))
            return
        self.own_domino_copy = clipboard.copy_count()  # (pasted straight back: its tempo isn't asked about)
        self.status.config(text=text)
        if warn:
            messagebox.showwarning(tr("project.spiderweb"), text + warn)
        self.tips.show("domino", wait=True)

    def copied_music(self, ppq, bpm, beats):
        """What a copy out (Copy to Domino, Spiderweb Notes) holds, as Export MIDI would write it: {"notes" (rows,
        file ticks), "cc" {slot: messages}, "plan" (file_plan, None without a tempo line / signatures), "sels" (the
        shapes copied, [] = all), "heard" (first start, last end in beats), "time" (the time selection's (start,
        end) in file ticks, or None)}; None when the track list's boxes hold none of Spiderweb's notes."""
        self.catch_up_notes(late=False)  # (a shape dragged in a big project: its notes wait for the mouse to rest)
        boxed = self.list_boxed()
        if boxed is not None and not boxed.any():
            return None
        # (+ wrapped kept notes, App.outgoing: the track list's boxes too, worked out on the notes as they go out,
        # user 2026-11-08: every way out wraps)
        notes = base = self.outgoing()
        if boxed is not None and notes is not self.rendered:
            from window.track_moves import boxed as boxed_in
            from window.track_tempo import note_boxes as boxes_of
            boxed = boxed_in(self, boxes_of(self.track_list.boxes), notes)
            if not boxed.any():
                return None
        sels = sorted({int(i) for i in np.unique(notes[boxed, 5])}) if boxed is not None else sorted(self.sels)
        only = set(sels) if sels else None
        time, by_time = None, boxed is not None or (bool(sels) and self.time_picked())
        if boxed is not None:
            from window.track_tempo import note_boxes
            boxes = note_boxes(self.track_list.boxes)
            time = min(b["t0"] for b in boxes), max(b["t1"] for b in boxes)
        elif by_time and self.roll.box_kept:
            areas = self.roll.box_kept[0]  # (ruler bands: (beat, top, beat, bottom))
            time = min(min(a[0], a[2]) for a in areas), max(max(a[0], a[2]) for a in areas)
        if sels and not by_time:  # (shapes picked by clicking / a Select box: the pasted CC
            # events stay behind, Domino's way, user 2026-10-29; your Hz bass CC lines go along, user 2026-10-21)
            laid, voice_slots = self.outgoing_layout() if boxed is None else (self.rendered, self.voice_slots)
            cc = slot_events(self.shapes, laid, ppq, bpm, only, voice_slots)
        else:
            cc = self.cc_messages(ppq, bpm, only)  # (the export's layout: the notes copied are App.outgoing's)
        if time is not None:
            time = round(time[0] * ppq), round(time[1] * ppq)
        if boxed is not None:  # (their CC lines: only on a box's rows inside its time, hunt 2026-10-29: a box
            # over one track took the shape's other tracks' controllers too)
            spans = [(round(b["t0"] * ppq), round(b["t1"] * ppq), b["rows"]) for b in boxes]
            slots = sorted(cc)
            rows = dict(zip(slots, self.track_list.rows_of(slots).tolist())) if slots else {}
            cc = {s: [m for m in ms if any(t0 <= m[0] < t1 and (r is None or r[0] <= rows[s] <= r[1])
                                           for t0, t1, r in spans)] for s, ms in cc.items()}
            cc = {s: ms for s, ms in cc.items() if ms}
        plan = None
        if boxed is not None:
            notes = notes[boxed]
        elif sels:
            notes = notes[np.isin(notes[:, 5], sels)]
        heard = (int(notes[:, 0].min()) / ppq, int(notes[:, 1].max()) / ppq) if len(notes) else (0, 0)
        if (has_tempo(self.tempo) or sigs_of(self.tempo) != [DEFAULT_SIG] or self.tempo.get("keysigs")) \
                and len(notes):
            # (the tempo line + time signatures come too: Keep timing worked out on the whole song up to the copy's
            # last note, so it lines up with Export MIDI)
            upto = max([int(notes[:, 1].max())] + [ms[-1][0] for ms in cc.values() if ms])
            # (... to the end of that bar: bar fitting spreads ticks over the whole bar, the copy's last bar came out
            # a few ticks off from Export MIDI, hunt 2026-10-24)
            lines = bar_ticks(self.tempo, ppq, upto)
            upto = int(lines[np.searchsorted(lines, upto)]) if upto < lines[-1] else upto
            timed, _, plan = self.tempo_timed(ppq, bpm, beats, self.cc_messages(ppq, bpm), notes=base, upto=upto)
            cc = timed_ticks(plan, notes[:0], cc)[1]  # (the notes as the file has them: finer Hz basses too)
            if boxed is not None and len(timed) == len(boxed):
                notes = timed[boxed]
            else:  # (Hz basses made again with finer gates: whole shapes)
                notes = timed[np.isin(timed[:, 5], sels)] if sels else timed
            if time is not None:
                time = tuple(int(t) for t in plan["map"](np.array(time, np.int64)).tolist())
        return {"notes": notes, "cc": cc, "plan": plan, "sels": sels, "heard": heard, "time": time}

    def domino_copy(self, quiet=False, got=False):
        """Copy to Domino's clipboard bytes of what it copies -> (raw, status text, warning text), or None when
        there's nothing to put on (said why: message boxes / questions). quiet (Ctrl+C with "Ctrl+C also copies for
        Domino", App.put_clipboard): never a message box nor a question; a copy that would ask (big_ask) or can't be
        made gives "skipped" in place of None ("" = nothing worth saying, e.g. no notes). got: copied_music's
        answer worked out already (Ctrl+C with Spiderweb Notes too: worked out once, hunt 2026-10-29)."""
        if not got:
            self.colour_edit_leave()  # (a colour step being edited: the whole result copied, PLAN.md stage C)

        def fail(text, box=messagebox.showerror):
            if quiet:
                return "skipped"
            box(tr("project.spiderweb"), text)
        if not clipboard.RAW:
            return "" if quiet else fail(tr("project.notes_clipboard_windows_only"), messagebox.showinfo)
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            return fail(str(e))
        got = self.copied_music(ppq, bpm, beats) if got is False else got
        if got is None:  # (a loaded MIDI's notes in the track list's boxes: never copied, said why)
            if quiet:
                return ""
            self.status.config(text=tr("tracks.box_empty") + self.loaded_left_out(short=True))
            return
        notes, cc, plan, sels, heard = got["notes"], got["cc"], got["plan"], got["sels"], got["heard"]
        high = int((notes[:, 2] > 127).sum())  # (256 keys: Domino only has 128)
        mod = high and self.domino256.get()  # (kept: only Domino's 256 keys mod opens them)
        if not mod:
            notes = notes[notes[:, 2] <= 127]
        if not len(notes):
            if quiet:
                return fail(None) if high else ""
            messagebox.showerror(tr("project.spiderweb"), (tr("project.no_notes_to_copy_draw_something")
                                 if not sels or high else tr("project.the_selected_shapes_have_no_notes"))
                                 + self.loaded_left_out())
            return
        span = conductor = None
        # (CCs before the first note: the copy starts at the earliest, so a lead-in keeps its timing, hunt 2026-10-22)
        early = min([int(notes[:, 0].min())] + [ms[0][0] for ms in cc.values() if ms])
        if plan:  # (bar lines from the file's time signatures; only "from the bar line" takes them along)
            bar = self.domino_start() == "bar"
            lo, hi = early, int(notes[:, 1].max())
            first = bar_line(plan["sigs"], lo, ppq) if bar else lo
            span = first, max((bar_line(plan["sigs"], hi, ppq, True) if bar else hi) - first, 1)
            # (the tempo goes along only with everything picked by Ctrl+A, user 2026-11-01: a Conductor track
            # first pushed the notes one track down in Domino)
            if self.copied_whole():
                conductor = tempo_items(plan["events"], domino_sigs(plan["sigs"], first, ppq), first,
                                        first + span[1], plan["keys"], plan.get("typed"))[0]
        elif early < int(notes[:, 0].min()):
            first, length = clip_span(notes, beats * ppq, self.domino_start())
            start = early if self.domino_start() == "note" else early // (beats * ppq) * beats * ppq
            span = start, length + first - start
        first = span[0] if span else clip_span(notes, beats * ppq, self.domino_start())[0]
        if int(notes[:, 1].max()) - first > LONGEST:  # (pasting it back would say "damaged")
            return fail(tr("project.too_long_to_copy_for_domino", longest=LONGEST))
        if len(notes) > MOST_NOTES:
            return fail(tr("project.too_many_to_copy_for_domino", notes=len(notes), most=MOST_NOTES))
        tracks = len(np.unique(notes[:, 4]))
        pad_from = None
        auto = notes[:, 4][notes[:, 4] < OWN0]  # (not the tracks added by hand: their own tracks, never padded)
        if self.rows_apart() and len(auto):  # (shapes moved a row of channels on: the empty tracks between keep them)
            step = 16 if self.picture_use10 else 15
            pad_from = int(auto.min()) // step * step
            tracks = int(auto.max()) + 1 - pad_from + tracks - len(np.unique(auto))
        if quiet:  # (a question would be asked: left to Copy to Domino; no drums question, the hint says it)
            if big_trouble(self, "domino", len(notes))[0]:
                return fail(None)
        # (no channel 10 question when the project uses it on purpose: "Use channel 10")
        elif not ask_big(self, "domino", len(notes)) or not (self.all16() or ask_drums(self, tracks)):
            return
        self.busy(tr("project.copying_for_domino"))  # (millions of notes take a few seconds)
        notes, long = cut_gates(notes)
        try:
            raw = clip_data(notes, ppq, beats * ppq, self.domino_start(), pad_from, cc, span, conductor,
                            self.with_file_tracks(), self.filled_slots(notes))
        except MemoryError:
            return fail(tr("big_ask.out_of_memory"))
        finally:
            self.busy(None)
        what =((tr("project.one_note") if len(notes) == 1 else tr("project.n_notes", n=len(notes))) if sels else
                trn("project.all_notes", len(notes), n=len(notes)))
        where = (tr("project.the_conductor_track") if conductor is not None else
                 tr("project.the_track") if tracks == 1 else tr("project.the_first_of_tracks", tracks=tracks))
        how = (tr("project.paste_at_the_cursor") if self.domino_start() == "note" else
               tr("project.double_click_a_bar_line_to"))
        text = (tr("project.copied_for_domino_ppq_in_domino", what=what, ppq=ppq, where=where, how=how)
                + (tr("project.copied_keys_256") if mod else
                   trn("project.notes_above_key_127_left_out", high, high=high) if high else "")
                + (trn("project.long_gates_cut", long, long=long, most=GATE_MAX) if long else "")
                + (tr("project.cc_not_written") if self.cc_note({}, shapes=[self.shapes[i] for i in sels]
                                                                 if sels else None) else "")
                + self.loaded_left_out(short=True))
        warn = ""
        if plan and conductor is not None:  # (the tempo warnings as Export to Domino gives them; places only
            # inside what was copied)
            lo, hi = heard
            inside = {k: [b for b in plan[k] if lo <= b <= hi] for k in ("jumps", "hz", "dense", "gave")}
            warn = self.tempo_notes(dict(plan, **inside))
            if conductor and tempo_items(plan["events"])[1]:
                warn += tr("project.dms_tempo_slow")
        return raw, text, warn

    def notes_copy(self, got=False):
        """What's copied as Spiderweb Notes (files/notes_clip.py; Settings → General "Ctrl+C also copies as
        Spiderweb Notes") -> (bytes or None, why: "" nothing to copy / "windows" off Windows / "long" spread too far
        (notes_clip.LONGEST) / "memory" out of memory; hunt 2026-10-29: said in the status line). Never asks
        anything. As Export MIDI writes it (copied_music; got = its answer worked out already); tick 0 = the time
        selection's start, else the first note; tracks numbered from the copy's top track list row down, the rows
        between kept (empty). Mid-drag with the notes left for later: none (making them now froze big projects)."""
        if not clipboard.RAW:
            return None, "windows"
        if self.roll.drag and (self.notes_late or self._late_notes):
            return None, ""
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError:
            return None, ""
        got = self.copied_music(ppq, bpm, beats) if got is False else got
        if got is None:
            return None, ""
        notes, cc, plan, time = got["notes"], got["cc"], got["plan"], got["time"]
        notes = notes[(notes[:, 1] > notes[:, 0]) & (notes[:, 2] >= 0) & (notes[:, 2] <= 255)]
        if not len(notes):
            return None, ""
        first, end = int(notes[:, 0].min()), int(notes[:, 1].max())
        if time is not None:
            first, end = min(first, time[0]), max(end, time[1])
        try:
            raw = notes_clip.pack(*self._notes_parts(notes, cc, plan, first, end, ppq))
        except MemoryError:
            return None, "memory"
        return raw, "" if raw is not None else "long"

    def copy_music(self):
        """copied_music for App.put_clipboard (both Ctrl+C extras share it); False when the project's numbers can't be
        read (each copy then says so its own way)."""
        self.colour_edit_leave()  # (a colour step being edited: the whole result copied, PLAN.md stage C)
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError:
            return False
        return self.copied_music(ppq, bpm, beats)

    def _notes_parts(self, notes, cc, plan, first, end, ppq):
        """notes_copy's pack() arguments."""
        use10, tl = self.picture_use10, self.track_list
        ev = [(int(m[0]), int(s), m[1], m[2], m[3]) for s, ms in cc.items() for m in ms if m[1] is not None]
        slots = np.unique(np.concatenate([notes[:, 4], np.array([e[1] for e in ev], np.int64)]))
        rows = tl.rows_of(slots)
        known = rows[rows >= 0]
        top = int(known.min()) if len(known) else 0
        spare = int(known.max()) + 1 if len(known) else 0
        for k in np.flatnonzero(rows < 0).tolist():  # (not listed: after the others, a track each)
            rows[k], spare = spare, spare + 1
        # (per slot, then looked up for every note at once: per-track loops over every slot froze Ctrl+C on
        # thousands of tracks, hunt 2026-10-29)
        slot_track, slot_ch = rows - top, channels_of(slots, use10)
        count = int(rows.max()) - top + 1
        listed, ids, files = tl.row_chans(), tl.tracks(), self.file_tracks()
        first_ch = np.full(count, -1, np.int64)
        lead = np.unique(slot_track, return_index=True)  # (each track's lowest slot)
        first_ch[lead[0]] = slot_ch[lead[1]]
        chans, names, row_ports = [], {}, [0] * count
        for t in range(count):  # (each track's usual channel: the list's first, else its first note's, else (an
            row = top + t        # empty row between) the track's own)
            shown = listed[row] if row < len(listed) else ()
            s = int(ids[row]) if row < len(ids) else None
            own = tl.own_track(s)
            row_ports[t] = tl.port_of(s) if s is not None else 0  # (Track Properties' port)
            if len(shown) or first_ch[t] >= 0:
                chans.append(int(shown[0]) if len(shown) else int(first_ch[t]))
            else:
                chans.append(own["ch"] if own else (tl.part_channel(s) or 0) if s is not None and s < 0 else
                             int(channels_of([s or 0], use10)[0]))
            n = tl.file_at(row) if s is not None else None
            name = own["name"] if own else files.get(n, {}).get("name") if n is not None else None
            if name:
                names[t] = name
        at = np.searchsorted(slots, notes[:, 4])
        tracks, ch = slot_track[at], slot_ch[at]
        ch = np.where(ch == np.array(chans, np.int64)[tracks], notes_clip.OWN_CHANNEL, ch)
        shapes = np.unique(notes[:, 5])
        rows_out = np.stack([notes[:, 0] - first, notes[:, 1] - notes[:, 0], notes[:, 2],
                             np.clip(notes[:, 3], 1, 127), tracks, np.searchsorted(shapes, notes[:, 5]), ch], 1)
        track_of = dict(zip(slots.tolist(), slot_track.tolist()))
        ch_of = dict(zip(slots.tolist(), slot_ch.tolist()))
        pal, colours = self.slots_pal(), {}
        if pal:  # (Image to notes: each picture track's colour)
            pics = [i for i in shapes.tolist() if picture_like(self.shapes[i])]
            for s in np.unique(notes[np.isin(notes[:, 5], pics), 4]).tolist():
                if s < len(pal):
                    colours[track_of[s]] = pal[s]
        from roll.roll_shared import COLOURS_16, SLOT_COLORS
        channel_colours = [SLOT_COLORS[int(COLOURS_16[c])][0] for c in range(16)]
        sigs = plan["sigs"] if plan else [(0, 4, 2)]
        tempo = plan["events"] if plan else [(0, round(60_000_000 / 120))]  # (no tempo line: MIDI's 120 BPM)

        def stretch(rows_in):  # (the one in force at first on tick 0, then the changes inside the copy)
            rows_in = sorted(rows_in)
            now = [r for r in rows_in if r[0] <= first][-1:]
            return [(0,) + tuple(r[1:]) for r in now] + [(r[0] - first,) + tuple(r[1:]) for r in rows_in
                                                         if first < r[0] < end]
        ev.sort(key=lambda e: max(e[0] - first, 0))  # (stable: each track's own order kept on a tick)
        events = (np.array([max(e[0] - first, 0) for e in ev], np.int64),
                  np.array([track_of[e[1]] for e in ev], np.int64),
                  [bytes([e[2] & 0xF0 | ch_of[e[1]], e[3] & 127]) if e[2] & 0xE0 == 0xC0 else
                   bytes([e[2] & 0xF0 | ch_of[e[1]], e[3] & 127, e[4] & 127]) for e in ev]) if ev else None
        ports = None
        if slot_ports(auto_count(notes[:, 4]), cc, use10):  # (as the MIDI export / Copy to Domino decide: CC lines
            ports = [0] * count                            # past one round of channels, hunt 2026-10-29)
            for s, t in zip(slots.tolist(), slot_track.tolist()):
                ports[t] = ports[t] or slot_port(s, use10)
        if any(row_ports):
            ports = [p or q for p, q in zip(ports or [0] * count, row_ports)]
        return (rows_out, ppq, chans, first - bar_line(sigs, first, ppq), end - first, names, colours,
                channel_colours, stretch(tempo), [(t, top_, 2 ** p) for t, top_, p in stretch(sigs)],
                stretch(plan["keys"]) if plan else [], events, ports)

    def busy(self, text):
        """text: the status line says it and the busy cursor shows until busy(None) (the status line back)."""
        if text is None:
            self.config(cursor="")
            self.update_status()
            self.status.release()  # (the busy text isn't a message to keep up)
            return
        self.status.config(text=text)
        self.config(cursor="watch")
        self.update_idletasks()

    def domino_start(self):
        """The start dropdown above the Domino buttons: "note" (first note at tick 0) or "bar" (from the bar line)."""
        return DOMINO_STARTS[max(self.domino_box.current(), 0)][0]

    def paste_from_domino(self, at=None):
        """Ctrl+V (app.paste, when they were copied last): the notes copied in Domino as one shape (custom.py's pasted notes), placed like Domino
        pastes: the start of what was copied (or its first note, see domino_start) on the play line (snapped to the
        grid), or at beat `at` (a Select double-click: the mouse, snapped already). Every track's notes go into
        the one shape; controllers and other events are left out. Ticks are taken as they are (same PPQ).
        A copy holding Domino's Conductor track brings its tempo changes into the Tempo pane too, from the same
        place (each held to the next, as in Domino; user 2026-10-22): an empty pane takes them as they are; else
        asked (ask_domino): replace that stretch / add among the points / notes only. Not when it's Spiderweb's
        own Copy to Domino coming back (its tempo is the pane's already)."""
        if not clipboard.RAW:
            messagebox.showinfo(tr("project.spiderweb"), tr("project.notes_clipboard_windows_only"))
            return
        self.busy(tr("project.pasting_from_domino"))  # (millions of notes take a few seconds)
        cond, shift, sh, from_bar, rides = {}, 0, None, False, []
        try:
            raw = get_from_clipboard()
            notes, their_ppq = read_notes(raw, 255, cond, rides) if raw else (None, None)
            tempos = [] if clipboard.copy_count() == self.own_domino_copy else cond.get("tempo", [])
            high = 0
            if notes is not None and not (self.domino256.get() and self.keys > 128):  # (the mod's keys above 127)
                high = int((notes[:, 2] > 127).sum())
                notes = notes[notes[:, 2] <= 127]
            if notes is not None and (len(notes) or tempos):
                if self.domino_start() == "note":  # the first note on the play line, without the copy's empty lead
                    shift = int(notes[:, 0].min()) if len(notes) else tempos[0][0]
                    if (tempos and tempos[0][0] < shift  # (tempo changes / CCs before it: from the copy's start,
                            or rides and min(r[0] for r in rides) < shift):  # as with "From the bar line", none
                        shift, from_bar = 0, True                            # lost; user 2026-10-22)
                    notes[:, 0] -= shift
            if notes is not None and len(notes):
                made = notes_shape(notes, self.ppq, tr("project.pasted_notes"))
                tracks = np.unique(notes[:, 4])  # (its tracks' columns, gaps kept: laid from a highlighted track)
                made["cols"] = (tracks - tracks.min()).tolist()
                if rides:  # (the tracks' CCs and other events ride along: passengers.py; ticks from the first note)
                    t0 = int(notes[:, 0].min()) + shift
                    made["cc"] = pack_cc([(t - t0, k, tag, body) for t, k, tag, body in rides])
                sh = clean_shape({**SHAPE_DEFAULTS, **self.defaults, **made})
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_read_the_notes_on", e=e))
            return
        except MemoryError:
            messagebox.showerror(tr("project.spiderweb"), tr("big_ask.out_of_memory"))
            return
        finally:
            self.busy(None)
        if raw is None:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_use_the_clipboard_another"))
            return
        if notes is None or not (len(notes) or tempos):
            messagebox.showerror(tr("project.spiderweb"),
                                 tr("project.no_notes_from_domino_on_the") if notes is None else
                                 tr("project.what_was_copied_in_domino_has"))
            return
        if sh is not None and not self.confirm_big([sh]):
            return
        how = None
        if tempos:  # (from the paste's start, beats; one before it = what's in effect there, on its start)
            before = [(t, v) for t, v in tempos if t <= shift][-1:]
            tempos = [(0, v) for _, v in before] + [(t - shift, v) for t, v in tempos if t > shift]
            tempos = [(t / self.ppq, clamp_bpm(float(f"{v:.7g}"))) for k, (t, v) in enumerate(tempos)
                      if k == 0 or v != tempos[k - 1][1]]  # (Domino's 32-bit BPM: 133.33, not 133.330002)
        own = clipboard.copy_count() == self.own_domino_copy  # (Spiderweb's own copy: its signatures are ours)
        sigs, keys = ([], []) if own else [  # (beats from the paste's start; the one in effect there on it)
            [(max(0, t - shift) / self.ppq, *rest) for k, (t, *rest) in enumerate(rows)
             if t > shift or k + 1 == len(rows) or rows[k + 1][0] > shift] for rows in (cond.get("sigs", []),
                                                                                       cond.get("keys", []))]
        if tempos or sigs or keys:
            # asked when the pane has its own points OR signatures (hunt 2026-10-23: a paste replaced the user's
            # signatures without a word)
            marks = sigs_of(self.tempo) != [list(DEFAULT_SIG)] or bool(self.tempo.get("keysigs"))
            if self.tempo["points"] or marks:
                how = ask_domino(self, len(tempos), sh is not None, marks=not (tempos and self.tempo["points"]))
            else:
                how = "replace"
            if how is None:
                return
            if how == "notes":
                if sh is None:
                    return
                tempos = []
        ends = [int((notes[:, 0] + notes[:, 1]).max())] if len(notes) else []
        length = max([(cond.get("length", 0) - shift) / self.ppq, 1 / self.ppq] + [e / self.ppq for e in ends]
                     + [b for b, _ in tempos])  # (the stretch Replace takes: the copy's, past its notes / tempo)
        if at is None:
            at, sb = self.playhead, self.snap_beats()
            if sb:
                at = round(at / sb) * sb
        self.roll.cancel_draft()
        self.busy(tr("project.pasting_from_domino"))
        try:
            if sh is not None:  # (into the highlighted track added by hand: several tracks fill down, like Domino)
                self.add_copies([sh], at, tr("project.paste_from_domino"))
            if tempos:  # (no points of its own: straight in, whatever was picked for the signatures)
                self.tempo_pane.put_domino(tempos, at, length, how if self.tempo["points"] else "replace", sh is None)
            if how != "notes" and (sigs or keys):  # (time / key signatures along, in the same undo step)
                self.tempo_pane.put_domino_sigs(sigs, keys, at, length, how or "replace")
        finally:
            self.busy(None)
        n = len(notes)
        note = (tr("project.they_were_copied_at_ppq_ticks", their_ppq=their_ppq) if their_ppq and their_ppq != self.ppq
                else "") + (trn("project.notes_above_key_127_left_out", high, high=high) if high else "") + (
            tr("project.pasted_from_copy_start") if from_bar else "") + (
            tr("project.cc_not_written") if sh is not None and "cc" in sh and self.cc_note({}, shapes=[sh]) else "")
        if sh is None:
            self.status.config(text=trn("project.pasted_tempo_from_domino", len(tempos), n=len(tempos)) + note)
        else:
            tempo = trn("project.pasted_tempo_too", len(tempos), n=len(tempos)) if tempos else (
                tr("project.pasted_tempo_left_out") if how == "notes" else "")
            self.status.config(text=(tr("project.pasted_one_note_from_domino", note=note) if n == 1 else
                                     tr("project.pasted_note_from_domino_as_one", n=n, note=note)) + tempo)
        self.tips.show("domino", wait=True)
