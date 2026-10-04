"""Project files: saving / opening projects, the autosave, and writing the MIDI file."""

import json
import math
import os
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np

from files.lang import tr
from notes.custom import ALIGNS, ENDS, CUSTOM_DEFAULTS, CUSTOM_FLAGS, FILLS, clean_cycle, notes_shape
from notes.engine import CHANNEL_MODES, SHAPE_DEFAULTS, SPLITS, clean_basics, clean_shape
from notes.hzbass import clean_hz
from notes.funnel import FUNNEL_DEFAULTS, clean_funnel
from notes.paths import KEYS
from notes.polygon import POLYGON_DEFAULTS, clean_polygon
from notes.smooth import SMOOTH_DEFAULT, clean_level
from notes.text import TEXT_DEFAULTS, clean_text
from files.domino_clip import DOMINO_STARTS, clip_data, get_from_clipboard, put_on_clipboard, read_notes
from files.midi_out import PPQ_WARN, write_midi
from files.about import HERE, VERSION
from files.safefile import write_bytes, write_text
from files.snap import clean_snap

AUTOSAVE = os.path.join(HERE, "autosave.json")
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
    return json.dumps({k: short(k, v) for k, v in sh.items()})


def project_json(data):
    """The project as JSON with one line per setting and one line per shape (plain json.dump puts every
    number on its own line)."""
    lines = []
    for k, v in data.items():
        if k == "shapes":
            body = ",\n  ".join(short_shape(sh) for sh in v)
            lines.append(f'"shapes": [\n  {body}\n ]' if v else '"shapes": []')
        else:
            if isinstance(v, dict):
                v = {a: short_num(b, 6) for a, b in v.items()}
            lines.append(f"{json.dumps(k)}: {json.dumps(v)}")
    return "{\n " + ",\n ".join(lines) + "\n}\n"


class ProjectFiles:
    """Mixed into App."""

    def project_data(self):
        return {
            "version": 2, "app_version": VERSION,  # the file format, and the Spiderweb that saved it
            "ppq": self.pvar["ppq"].get(), "bpm": self.pvar["bpm"].get(), "beats": self.pvar["beats"].get(),
            "output": self.pvar["output"].get(), "channel_mode": self.channel_mode.get(), "channel_split": self.channel_split,
            "keys": self.keys, "domino_start": self.domino_start(),
            "snap": self.snap.get(), "defaults": self.defaults,
            "custom_defaults": dict(self.custom_defaults, shape=self.custom_shape),
            "hz_defaults": self.hz_defaults, "hz_snap": self.hz_snap.get(),
            "hz_line": bool(self.hz_line.get()),
            "hz_fx": bool(self.hz_fx.get()),
            "funnel_defaults": self.funnel_defaults, "text_defaults": self.text_defaults,
            "polygon_defaults": self.polygon_defaults,
            "free_smooth": self.free_smooth, "shapes": self.shapes,
            "view": self.roll.view_state(), "playhead": self.playhead,
        }

    def load_file(self, path, quiet=False):
        """Opens a project. Everything in it is read and checked before anything changes: a file that can't be
        used leaves the project as it was; a broken setting keeps the one there is now; a shape that can't be read
        is left out and self.load_notes says so (tell_load_notes)."""
        self.load_notes = []
        try:
            data = read_json(path)
            if not isinstance(data.get("shapes", []), list):
                raise ValueError(tr("project.not_a_spiderweb_project"))
        except (OSError, ValueError) as e:
            if not quiet:
                messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_open_project", e=e))
            return False
        shapes = []
        for sh in data.get("shapes", []):
            try:
                sh = clean_shape(sh) if isinstance(sh, dict) else None
            except Exception:  # (damaged, or written in a way this Spiderweb doesn't know)
                sh = None
            if sh:
                shapes.append(sh)
        lost = len(data.get("shapes", [])) - len(shapes)
        if lost:
            self.load_notes.append(tr("project.shapes_left_out", n=lost))

        def get(fn):  # one setting read from the file: None if it's broken
            try:
                return fn()
            except Exception:
                return None

        def text(key):  # (a setting that's always text: anything else counts as missing)
            return data[key] if isinstance(data.get(key), str) else None

        def table(key):
            return data[key] if isinstance(data.get(key), dict) else {}

        boxes = {key: str(data[key]) for key in ("ppq", "bpm", "beats") if isinstance(data.get(key), (int, float, str))
                 and not isinstance(data[key], bool)}
        if text("output") is not None:
            boxes["output"] = data["output"]
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
        funnel = get(lambda: {k: v for k, v in clean_funnel(table("funnel_defaults")).items() if k in FUNNEL_DEFAULTS}
                     if table("funnel_defaults") else None)
        tx = get(lambda: clean_text(dict(table("text_defaults"), bbox=[0, 0, 1, 1])) if table("text_defaults") else None)
        pg = get(lambda: clean_polygon(data.get("polygon_defaults")))
        playhead = get(lambda: float(data.get("playhead", 0)))

        for key, value in boxes.items():
            self.pvar[key].set(value)
        self.channel_mode.set(mode if mode in CHANNEL_MODES else "single")
        self.keys_var.set(str(KEYS[1] if data.get("keys") == KEYS[1] else KEYS[0]))
        self.split_box.current(SPLITS.index(split) if split in SPLITS else 0)
        if start in starts:
            self.domino_box.current(starts.index(start))
        if "snap" in data:
            self.snap.set(clean_snap(data["snap"]))
        if "hz_snap" in data:
            self.hz_snap.set(clean_snap(data["hz_snap"]))
        self.hz_line.set(data.get("hz_line") is not False)
        self.hz_fx.set(data.get("hz_fx") is True)
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
        self.shapes = shapes
        self.roll.set_view(data.get("view"))
        self.stop_play()
        self.playhead = max(0.0, playhead) if playhead is not None and math.isfinite(playhead) else 0.0
        self.sel = None
        self.sels = set()
        self.parts = set()
        self.stroke = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._redo_kept = None
        self.on_project_change()
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
        out.pop("hz", None)
        if clean_hz(custom.get("hz")):
            out["hz"] = clean_hz(custom["hz"])
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
                              "velocity_height": self.velocity_height() / self.scale,
                              "midi_device": self.midi_device.get(), "live": self.live.get(),
                              "tumour_window": self.tumour_pos, "graph_window": self.graph_pos,
                              "claw_window": self.claw_pos, "strum_window": self.strum_pos, "chop_window": self.chop_pos, "hz_window": self.hz_pos, "hz_fx_height": self.hz_fx_h, "hz_preview": self.hz_preview,
                              "history_window": self.history_pos, "history_undocked": self.history_undocked,
                              **self.tips.state(), **self.updates.state(), **self.tool_picker.state()}
        write_text(path, project_json(data))

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
                if self.load_notes:  # something was left out: the file as it was is kept aside, never overwritten
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
                win = read_json(self.autosave_path).get("window") or {}
            except (OSError, ValueError):  # damaged: load_autosave opens the backup, so take its window
                win = read_json(backup_path(self.autosave_path)).get("window") or {}
            self.tips.restore(win)
            self.updates.restore(win)
            self.tool_picker.restore(win)
            geo = win.get("geometry", "")
            if geo:
                self.geometry(geo)
            if win.get("maximized"):
                self.update_idletasks()
                self.state("zoomed")
            if "velocity_height" in win:
                self._vel_height = max(int(50 * self.scale), int(float(win["velocity_height"]) * self.scale))
            if win.get("midi_device"):
                self.midi_device.set(str(win["midi_device"]))
            self.live.set(win.get("live") is True)
            for key, attr in (("tumour_window", "tumour_pos"), ("graph_window", "graph_pos"),
                              ("claw_window", "claw_pos"), ("strum_window", "strum_pos"), ("chop_window", "chop_pos")):
                pos = win.get(key)
                if isinstance(pos, str) and re.fullmatch(r"\+-?\d+\+-?\d+", pos):
                    setattr(self, attr, pos)
            pos = win.get("hz_window")
            if isinstance(pos, str) and re.fullmatch(r"\d+x\d+\+-?\d+\+-?\d+", pos):
                self.hz_pos = pos
            h = win.get("hz_fx_height")
            if isinstance(h, int) and not isinstance(h, bool) and 0 < h < 10000:
                self.hz_fx_h = h
            from window.hz_preview import clean_settings
            self.hz_preview = clean_settings(win.get("hz_preview"))
            pos = win.get("history_window")
            if isinstance(pos, str) and re.fullmatch(r"(\d+x\d+)?\+-?\d+\+-?\d+", pos):
                self.history_pos = pos
            self.history_undocked = win.get("history_undocked") is True
            if win.get("history") is True:  # it starts off; on again if it was on last time
                self.show_history.set(True)
                self.toggle_history(tip=False)
            if win.get("velocity") is True:  # it starts off; on again if it was on last time
                self.show_velocity.set(True)
                self.toggle_velocity(tip=False)
        except (OSError, ValueError, AttributeError, TypeError, tk.TclError):
            pass

    def schedule_autosave(self):
        if self._autosave_job:
            self.after_cancel(self._autosave_job)
        self._autosave_job = self.after(1500, self.autosave)

    def autosave(self):
        self._autosave_job = None
        try:
            self.write_json(self.autosave_path, window=True)
        except OSError:
            pass

    def open_project(self):
        path = filedialog.askopenfilename(title=tr("project.open_project"), initialdir=OUTPUT_DIR,
                                          filetypes=[(tr("project.spiderweb_project"), "*.json"),
                                                     (tr("project.all_files"), "*.*")])
        if path and self.load_file(path):
            self.sync_panel()
            self.tell_load_notes()

    def save_project(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(title=tr("project.save_project"), initialdir=OUTPUT_DIR,
                                            defaultextension=".json",
                                            filetypes=[(tr("project.spiderweb_project"), "*.json")])
        if not path:
            return
        try:
            self.write_json(path)
        except OSError as e:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_save", e=e))

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
        path = self.pvar["output"].get().strip() or os.path.join(OUTPUT_DIR, "spiderweb.mid")
        if not path.lower().endswith((".mid", ".midi")):
            path += ".mid"
        if os.path.exists(path) and not messagebox.askyesno(  # (asked like the Output file dialog does)
                tr("project.confirm_save_as"), tr("project.already_exists_do_you_want_to",
                                                  basename=os.path.basename(path)),
                icon="warning", default="no"):
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            write_midi(path, ppq, bpm, beats, self.rendered)
        except OSError as e:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_save", e=e))
            return
        channels = self.slot_count if self.channel_mode.get() == "auto" else 1
        note = (tr("project.ppq_many_midi_programs_can_t", ppq=ppq, PPQ_WARN=PPQ_WARN)
                if ppq >= PPQ_WARN else "")
        if (self.rendered[:, 2] > 127).any():
            note += tr("project.it_has_keys_above_127_256")
        messagebox.showinfo(tr("project.spiderweb"),
                            tr("project.saved_notes_on_track_s_one", n=len(self.rendered), channels=channels, path=path,
                               note=note))

    def copy_to_domino(self):
        """Ctrl+Shift+C: the selected shapes' notes (all notes when nothing is selected) on the clipboard, for
        Ctrl+V in Domino. One track per channel that has notes; the copy starts on the first note or at the bar
        line before it (domino_start)."""
        try:
            ppq, _, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), str(e))
            return
        notes = self.rendered
        if self.sels:
            notes = notes[np.isin(notes[:, 5], sorted(self.sels))]
        high = int((notes[:, 2] > 127).sum())  # (256 keys: Domino only has 128)
        notes = notes[notes[:, 2] <= 127]
        if not len(notes):
            messagebox.showerror(tr("project.spiderweb"), tr("project.no_notes_to_copy_draw_something")
                                 if not self.sels or high else tr("project.the_selected_shapes_have_no_notes"))
            return
        if not put_on_clipboard(clip_data(notes, ppq, beats * ppq, self.domino_start())):
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_use_the_clipboard_another"))
            return
        what = ((tr("project.one_note") if len(notes) == 1 else tr("project.n_notes", n=len(notes))) if self.sels else
                tr("project.all_notes", n=len(notes)))
        tracks = len(np.unique(notes[:, 4]))
        where = tr("project.the_track") if tracks == 1 else tr("project.the_first_of_tracks", tracks=tracks)
        how = (tr("project.paste_at_the_cursor") if self.domino_start() == "note" else
               tr("project.double_click_a_bar_line_to"))
        self.status.config(text=tr("project.copied_for_domino_ppq_in_domino", what=what, ppq=ppq, where=where, how=how)
                                + (tr("project.notes_above_key_127_left_out", high=high) if high else ""))
        self.tips.show("domino", wait=True)

    def domino_start(self):
        """The start dropdown above the Domino buttons: "note" (first note at tick 0) or "bar" (from the bar line)."""
        return DOMINO_STARTS[max(self.domino_box.current(), 0)][0]

    def paste_from_domino(self):
        """Ctrl+Shift+V: the notes copied in Domino as one shape (custom.py's pasted notes), placed like Domino
        pastes: the start of what was copied (or its first note, see domino_start) on the play line (snapped to the
        grid). Every track's notes go into
        the one shape; controllers and other events are left out. Ticks are taken as they are (same PPQ)."""
        raw = get_from_clipboard()
        if raw is None:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_use_the_clipboard_another"))
            return
        try:
            notes, their_ppq = read_notes(raw) if raw else (None, None)
        except ValueError as e:
            messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_read_the_notes_on", e=e))
            return
        if notes is None or not len(notes):
            messagebox.showerror(tr("project.spiderweb"),
                                 tr("project.no_notes_from_domino_on_the") if notes is None else
                                 tr("project.what_was_copied_in_domino_has"))
            return
        if self.domino_start() == "note":  # the first note on the play line, without the copy's empty lead
            notes[:, 0] -= notes[:, 0].min()
        sh = clean_shape({**SHAPE_DEFAULTS, **self.defaults,
                          **notes_shape(notes, self.ppq, tr("project.pasted_notes"))})
        if not self.confirm_big([sh]):
            return
        at, sb = self.playhead, self.snap_beats()
        if sb:
            at = round(at / sb) * sb
        self.roll.cancel_draft()
        self.add_copies([sh], at, tr("project.paste_from_domino"))
        n = len(notes)
        note = (tr("project.they_were_copied_at_ppq_ticks", their_ppq=their_ppq) if their_ppq and their_ppq != self.ppq
                else "")
        self.status.config(text=tr("project.pasted_one_note_from_domino", note=note) if n == 1 else
                           tr("project.pasted_note_from_domino_as_one", n=n, note=note))
        self.tips.show("domino", wait=True)
