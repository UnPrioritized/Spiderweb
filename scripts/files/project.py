"""Project files: saving / opening projects, the autosave, and writing the MIDI file."""

import errno
import json
import math
import os
import re
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc
from notes.custom import ALIGNS, ENDS, CUSTOM_DEFAULTS, CUSTOM_FLAGS, FILLS, clean_cycle, notes_shape, unpack_notes
from notes.engine import (CHANNEL_MODES, SHAPE_DEFAULTS, SPLITS, clean_basics, clean_kept_patterns, clean_shape,
                          merges_unmade, slot_ports)
from notes.hzbass import clean_hz
from notes.hz_cc import slot_events
from notes.hz_fine import fine_owners, finer_notes, swapped
from notes.passengers import STAY, USUAL, event_kind, midi_messages, pack_cc, placed, unpack_cc
from notes.mask import as_used, slot_rule
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
from notes.tempo import clamp_bpm, clean_tempo, has_tempo
from notes.meter import DEFAULT as DEFAULT_SIG, clean_sigs, sigs_of
from notes.tempo_file import bar_line, file_plan, finer_parts
from notes.text import TEXT_DEFAULTS, clean_text
from files import clipboard
from files.domino_clip import (DOMINO_STARTS, GATE_MAX, LONGEST, MOST_NOTES, clip_data, clip_span, cut_gates, dms_data,
                               dms_path, get_from_clipboard, put_on_clipboard, read_notes, tempo_items)
from files.midi_out import MAX_DELTA, MAX_TRACKS, PPQ_WARN, long_silences, track_slots, write_midi
from files.playback import keep_saved
from files.about import HERE, USER, VERSION
from files.errors import write_log
from files.safefile import write_bytes, write_text
from files.settings import save_app_settings, with_app_settings
from files.snap import clean_snap
from files.unzip import allowance
from files.update_check import version_tuple
from window.big_ask import ask as ask_big, ask_drums
from window.tempo_pane import ask_domino

AUTOSAVE = os.path.join(USER, "autosave.json")
OUTPUT_DIR = os.path.join(HERE, "output")


def backup_path(autosave):
    """autosave.json -> autosave-backup.json (an ordinary project file, so Open project can open it too)."""
    return os.path.splitext(autosave)[0] + "-backup.json"


def read_json(path):
    """A project file's contents (a BOM, which some text editors put at the start, is skipped)."""
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(tr("project.not_a_spiderweb_project"))
    return data


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
        return {
            "version": 2, "app_version": VERSION,  # the file format, and the Spiderweb that saved it
            "ppq": self.pvar["ppq"].get(), "bpm": self.pvar["bpm"].get(),
            "output": self.pvar["output"].get(), "channel_mode": self.channel_mode.get(), "channel_split": self.channel_split,
            "use_ch10": bool(self.use_ch10.get()), "ch10_order": bool(self.ch10_order.get()), "new_rounds": bool(self.new_rounds.get()), "pad_rounds": bool(self.pad_rounds.get()),"keys": self.keys, "edo31": bool(self.edo31.get()), "edo31_audio": bool(self.edo31_audio.get()),
            "domino_start": self.domino_start(),
            "snap": self.snap.get(), "defaults": self.defaults,
            "custom_defaults": dict(self.custom_defaults, shape=self.custom_shape),
            "hz_defaults": self.hz_defaults, "hz_snap": self.hz_snap.get(),
            "hz_line": bool(self.hz_line.get()),
            "hz_fx": bool(self.hz_fx.get()), "hz_layers": bool(self.hz_layers.get()),
            "hz_loud": bool(self.hz_loud.get()),
            "funnel_defaults": self.funnel_defaults, "text_defaults": self.text_defaults,
            "polygon_defaults": self.polygon_defaults,
            "free_smooth": self.free_smooth,
            # (only when there is one: files without a tempo line stay as they were)
            **({"tempo": self.tempo} if self.tempo["points"] or self.tempo["catch"] else {}),
            "shapes": self.shapes, "view": self.roll.view_state(), "playhead": self.playhead,
        }

    def load_file(self, path, quiet=False, text=None):
        """Opens a project. Everything in it is read and checked before anything changes: a file that can't be
        used leaves the project as it was; a broken setting keeps the one there is now; a shape that can't be read
        is left out and self.load_notes says so (tell_load_notes). Running out of memory (or any other error) while
        its notes are made puts the project as it was back (user 2026-10-20: a shape stuck failing at every redraw).
        text: the project as text instead of the file at path (put_back)."""
        self.load_notes = []
        try:
            data, size = (read_json(path), os.path.getsize(path)) if text is None else (json.loads(text), len(text))
            if not isinstance(data.get("shapes", []), list):
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
        newer = made and made > version_tuple(VERSION) or type(data.get("version")) is int and data["version"] > 2
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
            folder = os.path.dirname(os.path.abspath(out))
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

        was = project_json(self.project_data()), self.undo_stack[:], self.redo_stack[:], self._redo_kept
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
        self.keys_var.set(str(KEYS[1] if data.get("keys") == KEYS[1] else KEYS[0]))
        self.edo31.set(data.get("edo31") is True)
        self.edo31_audio.set(data.get("edo31_audio") is True)
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
        if pg:
            self.polygon_defaults = {k: pg[k] for k in POLYGON_DEFAULTS}
        self.roll.cancel_draft()
        self.roll.drop_lasso()  # (a waiting loop would cut the opened project, hunt 2026-10-23)
        self.shapes = shapes
        self.tempo = tempo
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
        self.roll.box_kept = None  # (an empty Select box / ruler band too)
        self.parts = set()
        self.stroke = self.ripple_pick = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._redo_kept = None
        try:
            self.on_project_change()
        except Exception as e:  # (its notes couldn't be made: the project as it was, its undo steps too)
            notes = self.load_notes
            self.load_file(path, quiet=True, text=was[0])
            self.undo_stack[:], self.redo_stack[:], self._redo_kept = was[1:]
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

    def write_json(self, path, window=False):
        data = self.project_data()
        if window:  # only the autosave remembers the window
            data["window"] = {"geometry": self._normal_geometry or self.wm_geometry(),
                              "maximized": self.wm_state() == "zoomed",
                              "velocity": self.show_velocity.get(), "history": self.show_history.get(),
                              "show_lines": self.show_lines.get(), "show_notes": self.show_notes.get(),
                              "channels_folded": self.channels_folded.get(),
                              "velocity_height": self.velocity_height() / self.scale,
                              "tempo": self.show_tempo.get(), "tempo_height": self.tempo_height() / self.scale,
                              "tempo_view": list(self.tempo_pane.view) if self.tempo_pane.view else None,
                              "tempo_log": self.tempo_pane.log,
                              "midi_device": self.midi_device.get(), "play_voices": self.play_voices,
                              "play_voice_guard": self.play_guard.get(), "play_limiter": self.play_limiter.get(),
                              "domino_256": self.domino256.get(), "keep_colours": self.keep_colours.get(),
                              "live": self.live.get(), "mask": self.mask.get(), "mask_defaults": self.mask_defaults,
                              "mask_preview": self.mask_preview.get(),
                              "tumour_window": self.tumour_pos, "graph_window": self.graph_pos,
                              "claw_window": self.claw_pos, "strum_window": self.strum_pos, "chop_window": self.chop_pos, "path_window": self.path_pos, "hz_window": self.hz_pos, "hz_fx_height": self.hz_fx_h, "hz_loud_height": self.hz_loud_h,"hz_preview": self.hz_preview, "hz_tools": self.hz_window.tools_state() if self.hz_window else self.hz_tools,
                              "hz_view": self.hz_window.view_state() if self.hz_window else self.hz_view,
                              "history_window": self.history_pos, "history_undocked": self.history_undocked,
                              "places": getattr(self, "window_places", {}),
                              **self.tips.state(), **self.updates.state(), **self.tool_picker.state()}
            save_app_settings(data["window"])  # (sound / updates / tips go to user/settings.ini instead)
            data["project_file"], data["unsaved"] = self.project_file, self.is_dirty()  # (put back at start)
        # pictures in the project's folder (or one inside it) are saved relative to it: they move together
        data["shapes"] = paths_for_file(data["shapes"], os.path.dirname(os.path.abspath(path)), True)
        write_text(path, project_json(data))

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
                self.midi_device.set(str(win["midi_device"]))
            self.live.set(win.get("live") is True)
            self.mask.set(win.get("mask") is True)
            self.mask_preview.set(win.get("mask_preview") is not False)
            got = clean_mask(win.get("mask_defaults")) or MASK_DEFAULTS
            self.mask_defaults = dict(MASK_DEFAULTS, **{k: got[k] for k in THICKNESS})
            self.show_lines.set(win.get("show_lines") is not False)
            self.show_notes.set(win.get("show_notes") is not False)
            self.channels_folded.set(win.get("channels_folded") is True)
        kept(ticks)

        def places():
            for key, attr in (("tumour_window", "tumour_pos"), ("graph_window", "graph_pos"),
                              ("claw_window", "claw_pos"), ("strum_window", "strum_pos"), ("chop_window", "chop_pos"),
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
            self.domino256.set(win.get("domino_256") is True)
            self.keep_colours.set(win.get("keep_colours") is True)
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

    def open_project(self):
        path = filedialog.askopenfilename(title=tr("project.open_project"), initialdir=OUTPUT_DIR,
                                          filetypes=[(tr("project.spiderweb_project"), "*.json"),
                                                     (tr("project.all_files"), "*.*")])
        if not path or not self.may_replace(os.path.basename(path)):
            return
        if self.load_file(path):
            self._saved_shapes = self.saved_key()
            self.project_file = None if self.own_file(path) else os.path.normpath(path)
            self.mark_saved()
            self.sync_panel()
            self.tell_load_notes()

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
                self.new_rounds.get(), self.pad_rounds.get())

    def is_dirty(self):
        """Changed since the last save / open: another newest undo step (undo back to it = saved again) or other
        Project boxes."""
        mark = self._saved_mark
        top = self.undo_stack[-1] if getattr(self, "undo_stack", None) else None
        return mark is None or mark[0] is not top or mark[1] != self.file_key()

    def mark_saved(self):
        self._saved_mark = (self.undo_stack[-1] if self.undo_stack else None, self.file_key())
        self.sync_window_title()

    def sync_window_title(self):
        """The window's title: "song.json - Spiderweb 1.6.2", a * while unsaved, "Unsaved project" with no file."""
        if not hasattr(self, "pvar"):
            return
        if self.project_file:
            title = tr("app.title_file", name=os.path.basename(self.project_file),
                       star=tr("app.title_star") if self.is_dirty() else "", VERSION=VERSION)
        else:
            title = tr("app.title_unsaved", VERSION=VERSION)
        if title != self._title:
            self._title = title
            self.title(title)

    def saved_key(self):
        """What Open project compares with the last save / open: the shapes and the tempo line."""
        return json.dumps([self.shapes, self.tempo if has_tempo(self.tempo) else None])

    def may_replace(self, name):
        """Before Open project replaces the shapes (Undo can't bring them back): if they (or the tempo line) aren't
        saved in a project file as they are, asks to save them first. False = don't open."""
        if not self.shapes and not has_tempo(self.tempo) or self.saved_key() == self._saved_shapes:
            return True
        answer = messagebox.askyesnocancel(tr("project.open_project"), tr("project.save_before_open", name=name),
                                           icon="warning", default="yes", parent=self)
        return answer is False or answer is True and self.save_project()

    def save_project(self, ask=False):
        """Into the project file opened / saved last; none yet (or ask: Save as…) = asks where. True if saved."""
        path = self.project_file
        if ask or not path:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            path = filedialog.asksaveasfilename(title=tr("project.save_project"),
                                                initialdir=os.path.dirname(path) if path else OUTPUT_DIR,
                                                initialfile=os.path.basename(path) if path else "",
                                                defaultextension=".json",
                                                filetypes=[(tr("project.spiderweb_project"), "*.json")])
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

    def generate(self):
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), str(e))
            return
        if not len(self.rendered):
            messagebox.showerror(tr("project.spiderweb"),
                                 tr("project.no_notes_yet_draw_something_inside", keys=self.keys - 1))
            return
        tracks = len(track_slots(self.rendered, self.pad_tracks()))
        if tracks > MAX_TRACKS:
            messagebox.showerror(tr("project.spiderweb"), tr("project.too_many_tracks", tracks=tracks, most=MAX_TRACKS))
            return
        if not ask_big(self, "midi", len(self.rendered)):
            return
        path = output_path(self.pvar["output"].get())
        if os.path.exists(path) and not messagebox.askyesno(  # (asked like the Output file dialog does)
                tr("project.confirm_save_as"), tr("project.already_exists_do_you_want_to",
                                                  basename=os.path.basename(path)),
                icon="warning", default="no"):
            return
        if long_silences(self.rendered) and not messagebox.askokcancel(
                tr("project.spiderweb"), tr("project.long_silences", ppq=ppq, beats=MAX_DELTA // ppq), icon="warning"):
            return
        cc = self.cc_messages(ppq, bpm)
        notes, cc, plan = self.tempo_timed(ppq, bpm, beats, cc)
        if not self.write_out(path, "project.saving_midi",  # (millions of notes take a few seconds)
                              lambda: write_midi(path, ppq, bpm, beats, notes, self.picture_use10,
                                                 self.pad_tracks(), cc, plan["events"], plan["sigs"], plan["keys"])):
            return
        channels = tracks if self.channel_mode.get() == "auto" else 1
        note = (tr("project.ppq_many_midi_programs_can_t", ppq=ppq, PPQ_WARN=PPQ_WARN)
                if ppq >= PPQ_WARN else "")
        if (self.rendered[:, 2] > 127).any():
            note += tr("project.it_has_keys_above_127_256")
        if slot_ports(int(self.rendered[:, 4].max()) + 1, cc, self.picture_use10):  # (as write_midi decides)
            note += tr("project.ports_midi")
        note += self.tempo_notes(plan) + self.cc_note(cc, True)
        messagebox.showinfo(tr("project.spiderweb"),
                            tr("project.saved_notes_on_track_s_one", notes=trn("n.notes", len(self.rendered),
                                                                               n=len(self.rendered)),
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
        if plan.get("gave"):  # (a heads-up only, user 2026-10-23)
            n = len(plan["gave"])
            note += trn("project.tempo_cc_gave", n, n=n, where=self.bar_text(plan["gave"][0]))
        return note

    def tempo_timed(self, ppq, bpm, beats, cc, notes=None, upto=None):
        """(notes, cc, file_plan) for the MIDI file: the Tempo pane's changes, notes and CC messages in Keep timing
        parts moved to the ticks that keep their time (notes/tempo_file.py). notes: the ones going out (default:
        every note); upto: only the ticks up to there count (Copy to Domino: the song up to the copy's end)."""
        notes = self.rendered if notes is None else notes
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
        stale = [i for i, sh in enumerate(self.shapes) if sh.get("hz") and self.hz_out_of_tune(sh)]
        on = notes[np.isin(notes[:, 5], stale), 0] if stale else ()
        plan["hz"] = [int(on.min()) / ppq] if len(on) else []
        if self.tempo.get("points") and any(len(p) > 5 and p[5] is True for p in self.tempo["points"]):
            moved, cc = timed_ticks(plan, notes, cc)
            if parts:
                moved = finer_notes(self.shapes, fine, notes, moved, parts, self.tempo, bpm, ppq, self.keys,
                                    plan["events"])
            notes = moved
        return notes, cc, plan

    def cc_messages(self, ppq, bpm, only=None):
        """The Hz basses' CC lines as MIDI messages on the slots (tracks) their notes are on: {slot: [(tick, status,
        data 1, data 2)]} (hz_cc.slot_events; only = the shapes' numbers to take, None: all). Pasted notes' passengers
        too (Multi channel only: each track's on the slot its notes got), each as its MIDI messages with a 5th part:
        (tag, body) = Domino's event to write back as it was (on the last of its messages; status None when it has
        no MIDI message), False = a MIDI-only message (passengers.midi_messages)."""
        out = slot_events(self.shapes, self.rendered, ppq, bpm, only, self.voice_slots)
        added = set()
        auto = self.channel_mode.get() == "auto"
        groups = {}  # (a paste (or a shape alone) -> ([its shapes], latest values: cc_put_back))
        for i, sh in enumerate(self.shapes):
            slots = self.voice_slots.get(i) if "cc" in sh and (only is None or i in only) else None
            if slots is None and auto and "cc" in sh and sh.get("own_slots") and (only is None or i in only):
                # (a copy whose tracks ARE its slots, made with the channel rule of then: moved like its notes, hunt)
                ks = sorted({e[1] for e in unpack_cc(sh["cc"])})
                slots = dict(zip(ks, as_used(ks, slot_rule(sh), self.picture_use10).tolist()))
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
            strips = self.voice_slots.get(("strips", i))
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
        if self.channel_mode.get() != "auto":
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
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), str(e))
            return
        self.catch_up_notes()
        notes = self.rendered
        high = int((notes[:, 2] > 127).sum()) if self.keys > 128 else 0  # (256 keys: Domino only has 128)
        mod = high and self.domino256.get()  # (kept: only Domino's 256 keys mod opens them)
        if not len(notes) or high == len(notes) and not mod:
            messagebox.showerror(tr("project.spiderweb"),
                                 tr("project.no_notes_yet_draw_something_inside", keys=min(self.keys, 128) - 1))
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
                               pad=self.pad_tracks(), cc=cc, tempo=tempo, sigs=sigs, keys=keys))):
            return
        tracks = len(track_slots(notes, self.pad_tracks()))
        ports = slot_ports(int(notes[:, 4].max()) + 1, cc, self.picture_use10)  # (as dms_data decides)
        text = (tr("project.saved_dms", notes=trn("n.notes", len(notes), n=len(notes)),
                   tracks=trn("n.tracks", tracks, n=tracks), path=path)
                + (tr("project.dms_keys_256") if mod else
                   trn("project.notes_above_key_127_left_out", high, high=high) if high else "")
                + (trn("project.dms_long_gates", long, long=long, most=GATE_MAX) if long else "")
                + (tr("project.ports_dms") if ports else ""))
        slow = tempo_items(tempo)[1] if tempo else 0
        warn = self.tempo_notes(plan) + (tr("project.dms_tempo_slow") if slow else "") + self.cc_note(cc)
        (messagebox.showwarning if long or ports or warn else messagebox.showinfo)(tr("project.spiderweb"),
                                                                                    text + warn)

    def copy_to_domino(self):
        """Ctrl+Shift+C: the selected shapes' notes (all notes when nothing is selected) on the clipboard, for
        Ctrl+V in Domino. One track per channel that has notes; the copy starts on the first note or at the bar
        line before it (domino_start)."""
        if not clipboard.RAW:
            messagebox.showinfo(tr("project.spiderweb"), tr("project.notes_clipboard_windows_only"))
            return
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), str(e))
            return
        self.catch_up_notes()  # (a shape dragged in a big project: its notes wait for the mouse to rest)
        notes = self.rendered
        cc = self.cc_messages(ppq, bpm, set(self.sels) if self.sels else None)
        plan = None
        if self.sels:
            notes = notes[np.isin(notes[:, 5], sorted(self.sels))]
        heard = (int(notes[:, 0].min()) / ppq, int(notes[:, 1].max()) / ppq) if len(notes) else (0, 0)
        if (has_tempo(self.tempo) or sigs_of(self.tempo) != [DEFAULT_SIG] or self.tempo.get("keysigs"))                 and len(notes):
            # (the tempo line + time signatures come too: Keep timing worked out on the whole song up to the copy's
            # last note, so it lines up with Export MIDI)
            upto = max([int(notes[:, 1].max())] + [ms[-1][0] for ms in cc.values() if ms])
            timed, _, plan = self.tempo_timed(ppq, bpm, beats, self.cc_messages(ppq, bpm), upto=upto)
            cc = timed_ticks(plan, notes[:0], cc)[1]  # (the notes as the file has them: finer Hz basses too)
            notes = timed[np.isin(timed[:, 5], sorted(self.sels))] if self.sels else timed
        high = int((notes[:, 2] > 127).sum())  # (256 keys: Domino only has 128)
        mod = high and self.domino256.get()  # (kept: only Domino's 256 keys mod opens them)
        if not mod:
            notes = notes[notes[:, 2] <= 127]
        if not len(notes):
            messagebox.showerror(tr("project.spiderweb"), tr("project.no_notes_to_copy_draw_something")
                                 if not self.sels or high else tr("project.the_selected_shapes_have_no_notes"))
            return
        span = conductor = None
        # (CCs before the first note: the copy starts at the earliest, so a lead-in keeps its timing, hunt 2026-10-22)
        early = min([int(notes[:, 0].min())] + [ms[0][0] for ms in cc.values() if ms])
        if plan:  # (bar lines from the file's time signatures; only "from the bar line" takes them along)
            bar = self.domino_start() == "bar"
            lo, hi = early, int(notes[:, 1].max())
            first = bar_line(plan["sigs"], lo, ppq) if bar else lo
            span = first, max((bar_line(plan["sigs"], hi, ppq, True) if bar else hi) - first, 1)
            conductor = tempo_items(plan["events"], plan["sigs"] if bar else (), first, first + span[1],
                                    plan["keys"] if bar else ())[0]
        elif early < int(notes[:, 0].min()):
            first, length = clip_span(notes, beats * ppq, self.domino_start())
            start = early if self.domino_start() == "note" else early // (beats * ppq) * beats * ppq
            span = start, length + first - start
        first = span[0] if span else clip_span(notes, beats * ppq, self.domino_start())[0]
        if int(notes[:, 1].max()) - first > LONGEST:  # (pasting it back would say "damaged")
            messagebox.showerror(tr("project.spiderweb"), tr("project.too_long_to_copy_for_domino", longest=LONGEST))
            return
        if len(notes) > MOST_NOTES:
            messagebox.showerror(tr("project.spiderweb"),
                                 tr("project.too_many_to_copy_for_domino", notes=len(notes), most=MOST_NOTES))
            return
        tracks = len(np.unique(notes[:, 4]))
        pad_from = None
        if self.rows_apart():  # (shapes moved a row of channels on: the empty tracks between keep them there)
            step = 16 if self.picture_use10 else 15
            pad_from = int(notes[:, 4].min()) // step * step
            tracks = int(notes[:, 4].max()) + 1 - pad_from
        # (no channel 10 question when the project uses it on purpose: "Use channel 10")
        if not ask_big(self, "domino", len(notes)) or not (self.all16() or ask_drums(self, tracks)):
            return
        self.busy(tr("project.copying_for_domino"))  # (millions of notes take a few seconds)
        notes, long = cut_gates(notes)
        try:
            raw = clip_data(notes, ppq, beats * ppq, self.domino_start(), pad_from, cc, span, conductor)
            copied = put_on_clipboard(raw)
        except MemoryError:
            messagebox.showerror(tr("project.spiderweb"), tr("big_ask.out_of_memory"))
            return
        finally:
            self.busy(None)
        if not copied:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_use_the_clipboard_another"))
            return
        self.own_domino_copy = clipboard.copy_count()  # (pasted straight back: its tempo isn't asked about)
        what =((tr("project.one_note") if len(notes) == 1 else tr("project.n_notes", n=len(notes))) if self.sels else
                trn("project.all_notes", len(notes), n=len(notes)))
        where = (tr("project.the_conductor_track") if plan else
                 tr("project.the_track") if tracks == 1 else tr("project.the_first_of_tracks", tracks=tracks))
        how = (tr("project.paste_at_the_cursor") if self.domino_start() == "note" else
               tr("project.double_click_a_bar_line_to"))
        text = (tr("project.copied_for_domino_ppq_in_domino", what=what, ppq=ppq, where=where, how=how)
                + (tr("project.copied_keys_256") if mod else
                   trn("project.notes_above_key_127_left_out", high, high=high) if high else "")
                + (trn("project.long_gates_cut", long, long=long, most=GATE_MAX) if long else "")
                + (tr("project.cc_not_written") if self.cc_note({}, shapes=[self.shapes[i] for i in self.sels]
                                                                 if self.sels else None) else ""))
        self.status.config(text=text)
        warn = ""
        if plan:  # (the tempo warnings as Export to Domino gives them; places only inside what was copied)
            lo, hi = heard
            inside = {k: [b for b in plan[k] if lo <= b <= hi] for k in ("jumps", "hz", "dense", "gave")}
            warn = self.tempo_notes(dict(plan, **inside))
            if conductor and tempo_items(plan["events"])[1]:
                warn += tr("project.dms_tempo_slow")
        if warn:
            messagebox.showwarning(tr("project.spiderweb"), text + warn)
        self.tips.show("domino", wait=True)

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
            if sh is not None:
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
            tr("project.cc_not_written") if sh is not None and "cc" in sh and self.channel_mode.get() != "auto" else "")
        if sh is None:
            self.status.config(text=trn("project.pasted_tempo_from_domino", len(tempos), n=len(tempos)) + note)
        else:
            tempo = trn("project.pasted_tempo_too", len(tempos), n=len(tempos)) if tempos else (
                tr("project.pasted_tempo_left_out") if how == "notes" else "")
            self.status.config(text=(tr("project.pasted_one_note_from_domino", note=note) if n == 1 else
                                     tr("project.pasted_note_from_domino_as_one", n=n, note=note)) + tempo)
        self.tips.show("domino", wait=True)
