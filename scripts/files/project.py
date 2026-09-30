"""Project files: saving / opening projects, the autosave, and writing the MIDI file."""

import json
import os
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np

from files.lang import tr
from notes.custom import ALIGNS, ENDS,CUSTOM_DEFAULTS, CUSTOM_FLAGS, FILLS, clean_hz, notes_shape
from notes.engine import CHANNEL_MODES, SHAPE_DEFAULTS, SPLITS, clean_shape
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
        return {"kind": "ellipse", "box": [short_num(a) for a in st["box"]]}
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
            "funnel_defaults": self.funnel_defaults, "text_defaults": self.text_defaults,
            "polygon_defaults": self.polygon_defaults,
            "free_smooth": self.free_smooth, "shapes": self.shapes,
            "view": self.roll.view_state(), "playhead": self.playhead,
        }

    def load_file(self, path, quiet=False):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            shapes = [s for s in (clean_shape(sh) for sh in data.get("shapes", [])) if s]
            defaults = dict(SHAPE_DEFAULTS)
            defaults.update({k: type(SHAPE_DEFAULTS[k])(v) for k, v in data.get("defaults", {}).items()
                             if k in SHAPE_DEFAULTS})
        except (OSError, ValueError, TypeError, AttributeError) as e:
            if not quiet:
                messagebox.showerror(tr("project.spiderweb"), tr("project.couldn_t_open_project", e=e))
            return False
        for key in ("ppq", "bpm", "beats", "output"):
            if key in data:
                self.pvar[key].set(str(data[key]))
        mode = data.get("channel_mode", "auto" if data.get("auto_channels") else "single")
        self.channel_mode.set(mode if mode in CHANNEL_MODES else "single")
        self.keys_var.set(str(KEYS[1] if data.get("keys") == KEYS[1] else KEYS[0]))
        split = data.get("channel_split")
        self.split_box.current(SPLITS.index(split) if split in SPLITS else 0)
        starts = [v for v, _ in DOMINO_STARTS]
        if data.get("domino_start") in starts:
            self.domino_box.current(starts.index(data["domino_start"]))
        if "snap" in data:
            self.snap.set(clean_snap(data["snap"]))
        self.defaults = defaults
        custom = data.get("custom_defaults") or {}
        if isinstance(custom, dict):
            if custom.get("fill") in FILLS:
                self.custom_defaults["fill"] = custom["fill"]
            if custom.get("align") in ALIGNS:
                self.custom_defaults["align"] = custom["align"]
            if custom.get("ends") in ENDS:
                self.custom_defaults["ends"] = custom["ends"]
            for key in CUSTOM_FLAGS:
                self.custom_defaults[key] = bool(custom.get(key))
            try:
                self.custom_defaults["gate"] = max(1e-6, float(custom.get("gate", CUSTOM_DEFAULTS["gate"])))
            except (TypeError, ValueError):
                pass
            self.custom_defaults.pop("hz", None)
            if clean_hz(custom.get("hz")):
                self.custom_defaults["hz"] = clean_hz(custom["hz"])
            if custom.get("shape"):
                self.custom_shape = str(custom["shape"])
        funnel = data.get("funnel_defaults")
        if isinstance(funnel, dict):
            try:
                self.funnel_defaults = {k: v for k, v in clean_funnel(funnel).items() if k in FUNNEL_DEFAULTS}
            except (TypeError, ValueError):
                pass
        text = clean_text(dict(data.get("text_defaults") or {}, bbox=[0, 0, 1, 1])) if isinstance(
            data.get("text_defaults"), dict) else None
        if text:
            self.text_defaults = {k: text[k] for k in TEXT_DEFAULTS}
        self.free_smooth = clean_level(data.get("free_smooth", SMOOTH_DEFAULT))
        pg = clean_polygon(data.get("polygon_defaults"))
        if pg:
            self.polygon_defaults = {k: pg[k] for k in POLYGON_DEFAULTS}
        self.roll.cancel_draft()
        self.shapes = shapes
        self.roll.set_view(data.get("view"))
        self.stop_play()
        try:
            self.playhead = max(0.0, float(data.get("playhead", 0)))
        except (TypeError, ValueError):
            self.playhead = 0.0
        self.sel = None
        self.sels = set()
        self.parts = set()
        self.stroke = None
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._redo_kept = None
        self.on_project_change()
        return True

    def write_json(self, path, window=False):
        data = self.project_data()
        if window:  # only the autosave remembers the window
            data["window"] = {"geometry": self._normal_geometry or self.wm_geometry(),
                              "maximized": self.wm_state() == "zoomed",
                              "velocity": self.show_velocity.get(), "history": self.show_history.get(),
                              "velocity_height": self.velocity_height() / self.scale,
                              "midi_device": self.midi_device.get(), "live": self.live.get(),
                              "tumour_window": self.tumour_pos, "graph_window": self.graph_pos,
                              "claw_window": self.claw_pos,
                              "history_window": self.history_pos, "history_undocked": self.history_undocked,
                              **self.tips.state(), **self.updates.state()}
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
            try:  # this start's autosave becomes the backup
                with open(path, "rb") as f:
                    write_bytes(backup, f.read())
            except OSError:
                pass
            return
        stamp, n = time.strftime("autosave-broken-%Y%m%d-%H%M%S"), 1
        broken = os.path.join(os.path.dirname(path), stamp + ".json")
        while os.path.exists(broken):  # never replace an earlier damaged one
            n += 1
            broken = os.path.join(os.path.dirname(path), f"{stamp}-{n}.json")
        try:
            os.replace(path, broken)
            kept = tr("project.it_was_kept_as", broken=broken)
        except OSError:
            kept = ""
        if os.path.exists(backup) and opens(backup):
            what = (tr("project.opened_the_backup_instead_your_work",
                       strftime=time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(backup)))))
        else:
            what = tr("project.there_s_no_usable_backup_either")
        self.after(300, lambda: messagebox.showwarning(
            tr("project.spiderweb"), tr("project.the_autosave_your_last_session_couldn", kept=kept, what=what),
            parent=self))

    def restore_window(self):
        try:
            try:
                with open(self.autosave_path, encoding="utf-8") as f:
                    win = json.load(f).get("window") or {}
            except (OSError, ValueError):  # damaged: load_autosave opens the backup, so take its window
                with open(backup_path(self.autosave_path), encoding="utf-8") as f:
                    win = json.load(f).get("window") or {}
            self.tips.restore(win)
            self.updates.restore(win)
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
                              ("claw_window", "claw_pos")):
                pos = win.get(key)
                if isinstance(pos, str) and re.fullmatch(r"\+-?\d+\+-?\d+", pos):
                    setattr(self, attr, pos)
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
