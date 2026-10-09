"""The synth window's presets (window/hz_synth.py; user 2026-10-05: a bar at the top left, like a synth's): the
preset's name (click it: the list, ours first, then the user's), ◀ ▶ to step through them, Save… and Delete; a *
after the name once the sound has been changed. A preset is the whole sound: every effect's line and setting
(HzWindow.fx_settings), not the notes. Picking one = one undo step. The user's are kept in spiderweb/hz_presets.json."""

import copy
import json
import os
import types
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from files.about import HERE
from files.lang import tr
from files.safefile import write_text
from window.hz_knobs import adsr_line, pitch_line, sweep_line, vibrato_line
from window.synth_look import dark_menu
from window.widgets import Tooltip

PRESETS_FILE = os.path.join(HERE, "hz_presets.json")


def volume(attack, decay, sustain, release):
    """The Volume line of an ADSR envelope, as the knobs make it."""
    pts, at, every = adsr_line(attack, decay, sustain, release)
    return {"fx": {"volume": pts}, "loop": {"volume": every}, "from": {"volume": "note"}, "sustain": {"volume": at}}


def per_note(name, made):
    """A line made by the knobs ((points, length; None = all along)), once per note when it has a length."""
    pts, every = made
    return {"fx": {name: pts}, **({"loop": {name: every}, "from": {name: "note"}} if every else {})}


def flat(name, value):
    return {"fx": {name: [[0.0, value]]}}


def joined(*parts):
    out = {}
    for part in parts:
        for k, v in part.items():
            out[k] = {**out.get(k, {}), **v}
    return out


OURS = {  # our own presets: id -> the sound (made as the knobs make it)
    "init": {},
    "pluck": joined(volume(0.0, 0.3, 0.0, 0.2), per_note("sweep", sweep_line(1.0, 0.0, 0.3))),
    "soft_pad": joined(volume(1.0, 0.0, 0.8, 1.5), flat("sine", 1.0), per_note("vibrato", vibrato_line(0.3, 1.0))),
    "buzz": joined(volume(0.05, 0.2, 0.7, 0.3), flat("saw", 1.0)),
    "laser": joined(volume(0.0, 0.0, 1.0, 0.1), per_note("pitch", pitch_line(12, 0.25))),
    "drop": joined(volume(0.0, 0.8, 0.3, 0.5), per_note("pitch", pitch_line(7, 0.5)), flat("octave", 0.3)),
    "wobble": joined(flat("tremolo", 0.5), flat("wah", 0.3)),
    "wide": joined(flat("slant", 0.3), flat("offpitch", 0.3)),
}


def read_file():
    """The file's entries as they are ([] without a file), None when it can't be read (broken)."""
    if not os.path.exists(PRESETS_FILE):
        return []
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            items = json.load(f).get("presets", [])
        return items if isinstance(items, list) else None
    except (OSError, ValueError, AttributeError):
        return None


def good(p):
    return isinstance(p, dict) and str(p.get("name", "")).strip() and isinstance(p.get("sound"), dict)


def load_presets():
    """The user's saved presets [{"name", "sound"}] (a broken file or entry: left out)."""
    return [{"name": str(p["name"]), "sound": dict(p["sound"])} for p in read_file() or () if good(p)]


def same_name(p, name):
    """A file entry with this name (capitals or not: one preset)."""
    return isinstance(p, dict) and str(p.get("name", "")).strip().casefold() == name.casefold()


def change_presets(name, sound, parent):
    """The user's preset `name` replaced by `sound` (None: deleted), every other entry in the file kept as it is. A
    file that can't be read is kept aside first (hz_presets-broken.json, said), so nothing in it is lost. False (and
    said) when the file can't be written."""
    items = read_file()
    try:
        if items is None:
            broken = os.path.splitext(PRESETS_FILE)[0] + "-broken.json"
            os.replace(PRESETS_FILE, broken)
            messagebox.showwarning(tr("hz.preset_save_title"), tr("hz.preset_file_broken", path=broken),
                                   parent=parent)
            items = []
        items = [p for p in items if not same_name(p, name)]
        if sound is not None:
            items.append({"name": name, "sound": sound})
        write_text(PRESETS_FILE, json.dumps({"presets": items}, indent=1))
    except OSError as e:
        messagebox.showerror(tr("hz.preset_save_title"), tr("hz.preset_save_failed", path=PRESETS_FILE, e=e),
                             parent=parent)
        return False
    return True


class PresetBar:
    """The preset bar of SynthWindow (needs hz, fx, show_knobs)."""

    def build_presets(self, row):
        self.preset = None  # the preset picked last: (key, its name, its sound as fx_settings, in JSON)
        self.preset_mark = None  # ... picked for: (the shape's number, the undo step it's picked after)
        self.preset_canon = {}  # a preset's key -> its sound as fx_settings would have it (JSON)
        self.yours = load_presets()  # (read again when the list opens)
        top = ttk.Frame(row, style="Synth.TFrame")  # (the name and ◀ ▶ on top, Save… and Delete under them)
        top.pack(fill="x")
        under = ttk.Frame(row, style="Synth.TFrame")
        under.pack(fill="x", pady=(4, 0))
        self.preset_var = tk.StringVar()
        mb = ttk.Menubutton(top, textvariable=self.preset_var, width=26, style="Synth.TMenubutton")
        mb.pack(side="left", fill="x", expand=True)
        self.preset_menu = tk.Menu(mb, tearoff=0, postcommand=self.fill_presets)
        dark_menu(self.preset_menu)
        mb["menu"] = self.preset_menu
        Tooltip(mb, tr("hz.preset_tip"))
        for text, d, tip in (("◀", -1, "hz.preset_prev"), ("▶", 1, "hz.preset_next")):
            b = ttk.Button(top, text=text, width=3, command=lambda d=d: self.step_preset(d), takefocus=False,
                           style="Synth.TButton")
            b.pack(side="left", padx=(4 if d < 0 else 2, 0))
            Tooltip(b, tr(tip))
        b = ttk.Button(under, text=tr("hz.preset_save"), command=self.save_preset, takefocus=False,
                       style="Synth.TButton")
        b.pack(side="left")
        Tooltip(b, tr("hz.preset_save_tip"))
        self.preset_del = ttk.Button(under, text=tr("hz.preset_delete"), command=self.delete_preset, takefocus=False,
                                     style="Synth.TButton")
        self.preset_del.pack(side="left", padx=(4, 0))
        Tooltip(self.preset_del, tr("hz.preset_delete_tip"))

    def presets(self):
        """Every preset in the list's order: [(key, name, sound)], ours first."""
        return ([(("ours", k), tr(f"hz.preset_{k}"), v) for k, v in OURS.items()] +
                [(("yours", p["name"]), p["name"], p["sound"]) for p in self.yours])

    def canon(self, key, sound):
        """A preset's sound as the window's own settings would be (JSON), to tell whether the sound is it."""
        got = self.preset_canon.get(key)
        if got is None:
            win = type(self.hz)
            ns = types.SimpleNamespace()
            win.set_fx(ns, sound)
            got = self.preset_canon[key] = json.dumps(win.fx_settings(ns), sort_keys=True)
        return got

    def fill_presets(self):
        m = self.preset_menu
        m.delete(0, "end")
        self.yours = load_presets()  # (saved in another Spiderweb meanwhile)
        self.preset_canon = {k: v for k, v in self.preset_canon.items() if k[0] == "ours"}
        items = self.presets()
        for i, (key, name, sound) in enumerate(items):
            if i and key[0] == "yours" and items[i - 1][0][0] == "ours":
                m.add_separator()
            m.add_command(label=name, command=lambda p=(key, name, sound): self.pick_preset(*p))

    def pick_preset(self, key, name, sound):
        """A preset picked: the sound becomes it (one undo step)."""
        hz = self.hz
        before = self.fx.state()
        hz.set_fx(copy.deepcopy(sound))
        picked = (key, name, self.canon(key, sound))
        self.preset, self.preset_mark = picked, None  # (none while it's being put in: nothing shown changes it)
        if self.fx.now() != before:
            hz.fx.tidy()
            hz.commit(tr("hz.step_preset", name=name), copy.deepcopy(hz.tones), before)
        called_off = json.dumps(hz.fx_settings(), sort_keys=True) != picked[2]  # (too many notes: No)
        self.preset = None if called_off else picked
        self.mark_preset()
        self.redraw()
        self.show_knobs()

    def step_preset(self, d):
        """◀ ▶: the preset before / after the one picked (round the list)."""
        items = self.presets()
        keys = [k for k, _, _ in items]
        i = keys.index(self.preset[0]) if self.preset and self.preset[0] in keys else (-1 if d > 0 else 0)
        self.pick_preset(*items[(i + d) % len(items)])

    def save_preset(self):
        """The sound kept as a preset of the user's, under a name (asked; one of ours is asked again)."""
        ours = {tr(f"hz.preset_{k}").casefold() for k in OURS}
        prompt = tr("hz.preset_save_prompt")
        name = self.preset[1] if self.preset and self.preset[0][0] == "yours" else ""
        while True:
            name = simpledialog.askstring(tr("hz.preset_save_title"), prompt, parent=self, initialvalue=name)
            if not name or not name.strip():
                return
            name = name.strip()
            if name.casefold() not in ours:
                break
            prompt = tr("hz.preset_name_taken", name=name)
        sound = self.hz.fx_settings()
        if not change_presets(name, sound, self):
            return
        self.yours = load_presets()
        key = ("yours", name)
        self.preset_canon.pop(key, None)
        self.preset = (key, name, self.canon(key, sound))
        self.mark_preset()
        self.show_preset()

    def mark_preset(self):
        """The preset shown is for this shape's sound as it is after the last undo step (see show_preset)."""
        stack = self.hz.app.undo_stack
        self.preset_mark = (self.shape_number(), stack[-1] if stack else None)

    def forget_preset(self):
        """Layers deleted / moved: the layer number the preset was picked for may name another layer now."""
        if self.preset_mark is not None:
            self.preset_mark = ((None, -1), None)

    def shape_number(self):
        """(the shape's number, its layer picked): a preset names one layer's sound."""
        sh = self.hz.target()
        return (next((i for i, s in enumerate(self.hz.app.shapes) if s is sh), None),
                ((sh or {}).get("hz") or {}).get("layer", 0))

    def preset_holds(self):
        """The preset picked still names this sound (changed or not): the same shape (by number: undo makes it anew),
        and not undone past the step it was picked after."""
        if self.preset_mark is None:  # (being picked)
            return True
        where, step = self.preset_mark
        return where == self.shape_number() and (step is None or any(s is step for s in self.hz.app.undo_stack))

    def delete_preset(self):
        if not self.preset or self.preset[0][0] != "yours":
            return
        name = self.preset[1]
        if not messagebox.askyesno(tr("hz.preset_delete_title"), tr("hz.preset_delete_ask", name=name), parent=self):
            return
        if not change_presets(name, None, self):
            return
        self.yours = load_presets()
        self.preset = None
        self.show_preset()

    def show_preset(self):
        """The name shown: the preset picked (a * once the sound has changed), else one the sound is the same as,
        else none."""
        now = json.dumps(self.hz.fx_settings(), sort_keys=True)
        if self.preset is not None and not self.preset_holds():  # (another shape, or undone past picking it: its
            self.preset = None  # name would say this sound came from it, user)
        if self.preset is None or self.preset[2] != now:
            same = next(((k, n, s) for k, n, s in self.presets() if self.canon(k, s) == now), None)
            if same:
                self.preset = (same[0], same[1], now)
                self.mark_preset()
        if self.preset is None:
            text = tr("hz.preset_none")
        else:
            text = self.preset[1] + ("" if self.preset[2] == now else " *")
        if self.preset_var.get() != text:
            self.preset_var.set(text)
        yours = self.preset is not None and self.preset[0][0] == "yours"
        self.preset_del.state(["!disabled"] if yours else ["disabled"])
