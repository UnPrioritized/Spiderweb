"""Project files: saving / opening projects, the autosave, and writing the MIDI file."""

import json
import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np

from notes.custom import ALIGNS, CUSTOM_DEFAULTS, FILLS
from notes.engine import CHANNEL_MODES, SHAPE_DEFAULTS, SPLITS, clean_shape
from notes.funnel import FUNNEL_DEFAULTS, clean_funnel
from notes.smooth import SMOOTH_DEFAULT, clean_level
from notes.text import TEXT_DEFAULTS, clean_text
from files.domino_clip import clip_data, put_on_clipboard
from files.midi_out import PPQ_WARN, write_midi
from files.about import HERE, VERSION
from files.safefile import write_bytes, write_text

AUTOSAVE = os.path.join(HERE, "autosave.json")
OUTPUT_DIR = os.path.join(HERE, "output")
SNAPS = ["Off", "1/1", "1/2", "1/4", "1/8", "1/16", "1/32", "1/64", "1/128", "1/6", "1/12", "1/24", "1/48"]


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
            return {a: short_num(b) for a, b in v.items()}
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
            "snap": self.snap.get(), "defaults": self.defaults,
            "custom_defaults": dict(self.custom_defaults, shape=self.custom_shape),
            "funnel_defaults": self.funnel_defaults, "text_defaults": self.text_defaults,
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
                messagebox.showerror("Spiderweb", f"Couldn't open project:\n{e}")
            return False
        for key in ("ppq", "bpm", "beats", "output"):
            if key in data:
                self.pvar[key].set(str(data[key]))
        mode = data.get("channel_mode", "auto" if data.get("auto_channels") else "single")
        self.channel_mode.set(mode if mode in CHANNEL_MODES else "single")
        split = data.get("channel_split")
        self.split_box.current(SPLITS.index(split) if split in SPLITS else 0)
        if data.get("snap") in SNAPS:
            self.snap.set(data["snap"])
        self.defaults = defaults
        custom = data.get("custom_defaults") or {}
        if isinstance(custom, dict):
            if custom.get("fill") in FILLS:
                self.custom_defaults["fill"] = custom["fill"]
            if custom.get("align") in ALIGNS:
                self.custom_defaults["align"] = custom["align"]
            try:
                self.custom_defaults["gate"] = max(1e-6, float(custom.get("gate", CUSTOM_DEFAULTS["gate"])))
            except (TypeError, ValueError):
                pass
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
        self.on_project_change()
        return True

    def write_json(self, path, window=False):
        data = self.project_data()
        if window:  # only the autosave remembers the window
            data["window"] = {"geometry": self._normal_geometry or self.wm_geometry(),
                              "maximized": self.wm_state() == "zoomed",
                              "velocity": self.show_velocity.get(),
                              "velocity_height": self.velocity_height() / self.scale,
                              "midi_device": self.midi_device.get(), "live": self.live.get(), **self.tips.state()}
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
            kept = f"It was kept as:\n{broken}\n\n"
        except OSError:
            kept = ""
        if os.path.exists(backup) and opens(backup):
            what = ("Opened the backup instead: your work as it was when you last started Spiderweb "
                    f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(backup)))}).")
        else:
            what = "There's no usable backup either, so Spiderweb starts with an empty project."
        self.after(300, lambda: messagebox.showwarning(
            "Spiderweb", f"The autosave (your last session) couldn't be opened — it's damaged.\n\n{kept}{what}",
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
        path = filedialog.askopenfilename(title="Open project", initialdir=OUTPUT_DIR,
                                          filetypes=[("Spiderweb project", "*.json"), ("All files", "*.*")])
        if path and self.load_file(path):
            self.sync_panel()

    def save_project(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(title="Save project", initialdir=OUTPUT_DIR,
                                            defaultextension=".json", filetypes=[("Spiderweb project", "*.json")])
        if not path:
            return
        try:
            self.write_json(path)
        except OSError as e:
            messagebox.showerror("Spiderweb", f"Couldn't save:\n{e}")

    def browse_output(self):
        cur = self.pvar["output"].get()
        path = filedialog.asksaveasfilename(title="MIDI output", initialdir=os.path.dirname(cur) or HERE,
                                            initialfile=os.path.basename(cur), defaultextension=".mid",
                                            filetypes=[("MIDI file", "*.mid *.midi")])
        if path:
            self.pvar["output"].set(os.path.normpath(path))

    def generate(self):
        try:
            ppq, bpm, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror("Spiderweb", str(e))
            return
        if not len(self.rendered):
            messagebox.showerror("Spiderweb", "No notes yet — draw something inside the 0–127 pitch range first.")
            return
        path = self.pvar["output"].get().strip() or os.path.join(OUTPUT_DIR, "spiderweb.mid")
        if not path.lower().endswith((".mid", ".midi")):
            path += ".mid"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            write_midi(path, ppq, bpm, beats, self.rendered)
        except OSError as e:
            messagebox.showerror("Spiderweb", f"Couldn't save:\n{e}")
            return
        channels = self.slot_count if self.channel_mode.get() == "auto" else 1
        note = (f"\n\nPPQ {ppq}: many MIDI programs can't open this file (it needs a PPQ below {PPQ_WARN})."
                if ppq >= PPQ_WARN else "")
        messagebox.showinfo("Spiderweb", f"Saved {len(self.rendered):,} notes on {channels} track(s), one channel "
                                         f"each:\n{path}{note}")

    def copy_to_domino(self):
        """Ctrl+Shift+C: the selected shapes' notes (all notes when nothing is selected) on the clipboard, for
        Ctrl+V in Domino. One track per channel that has notes; the copy starts at the bar line before the first
        note."""
        try:
            ppq, _, beats = self.read_project()
        except ValueError as e:
            messagebox.showerror("Spiderweb", str(e))
            return
        notes = self.rendered
        if self.sels:
            notes = notes[np.isin(notes[:, 5], sorted(self.sels))]
        if not len(notes):
            messagebox.showerror("Spiderweb", "No notes to copy — draw something inside the 0–127 pitch range first."
                                 if not self.sels else "The selected shapes have no notes.")
            return
        if not put_on_clipboard(clip_data(notes, ppq, beats * ppq)):
            messagebox.showerror("Spiderweb", "Couldn't use the clipboard (another program has it open). Try again.")
            return
        what = f"{len(notes):,} note{'s' * (len(notes) != 1)}" if self.sels else f"all {len(notes):,} notes"
        tracks = len(np.unique(notes[:, 4]))
        where = "a track" if tracks == 1 else f"the first of {tracks} tracks"
        self.status.config(text=f"Copied {what} for Domino (PPQ {ppq}) — highlight {where} there, put the play "
                                "cursor on a bar line and press Ctrl+V")
