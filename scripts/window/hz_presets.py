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


def load_presets():
    """The user's saved presets [{"name", "sound"}] (a broken file or entry: left out)."""
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [{"name": str(p["name"]), "sound": dict(p["sound"])} for p in data.get("presets", [])
                if isinstance(p, dict) and str(p.get("name", "")).strip() and isinstance(p.get("sound"), dict)]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def save_presets(items):
    write_text(PRESETS_FILE, json.dumps({"presets": items}, indent=1))


class PresetBar:
    """The preset bar of SynthWindow (needs hz, fx, show_knobs)."""

    def build_presets(self, row):
        self.preset = None  # the preset picked last: (key, its name, its sound as fx_settings, in JSON)
        self.preset_canon = {}  # a preset's key -> its sound as fx_settings would have it (JSON)
        self.yours = load_presets()  # (read again when the list opens)
        ttk.Label(row, text=tr("hz.preset")).pack(side="left", padx=(0, 4))
        self.preset_var = tk.StringVar()
        mb = ttk.Menubutton(row, textvariable=self.preset_var, width=22)
        mb.pack(side="left")
        self.preset_menu = tk.Menu(mb, tearoff=0, postcommand=self.fill_presets)
        mb["menu"] = self.preset_menu
        Tooltip(mb, tr("hz.preset_tip"))
        for text, d, tip in (("◀", -1, "hz.preset_prev"), ("▶", 1, "hz.preset_next")):
            b = ttk.Button(row, text=text, width=3, command=lambda d=d: self.step_preset(d), takefocus=False)
            b.pack(side="left", padx=(4 if d < 0 else 0, 0))
            Tooltip(b, tr(tip))
        b = ttk.Button(row, text=tr("hz.preset_save"), command=self.save_preset, takefocus=False)
        b.pack(side="left", padx=(8, 0))
        Tooltip(b, tr("hz.preset_save_tip"))
        self.preset_del = ttk.Button(row, text=tr("hz.preset_delete"), command=self.delete_preset, takefocus=False)
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
        self.preset = (key, name, self.canon(key, sound))
        if self.fx.now() != before:
            hz.fx.tidy()
            hz.commit(tr("hz.step_preset", name=name), copy.deepcopy(hz.tones), before)
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
        items = [p for p in load_presets() if p["name"] != name]
        items.append({"name": name, "sound": sound})
        save_presets(items)
        self.yours = items
        key = ("yours", name)
        self.preset_canon.pop(key, None)
        self.preset = (key, name, self.canon(key, sound))
        self.show_preset()

    def delete_preset(self):
        if not self.preset or self.preset[0][0] != "yours":
            return
        name = self.preset[1]
        if not messagebox.askyesno(tr("hz.preset_delete_title"), tr("hz.preset_delete_ask", name=name), parent=self):
            return
        self.yours = [p for p in load_presets() if p["name"] != name]
        save_presets(self.yours)
        self.preset = None
        self.show_preset()

    def show_preset(self):
        """The name shown: the preset picked (a * once the sound has changed), else one the sound is the same as,
        else none."""
        now = json.dumps(self.hz.fx_settings(), sort_keys=True)
        if self.preset is None or self.preset[2] != now:
            same = next(((k, n, s) for k, n, s in self.presets() if self.canon(k, s) == now), None)
            if same:
                self.preset = (same[0], same[1], now)
        if self.preset is None:
            text = tr("hz.preset_none")
        else:
            text = self.preset[1] + ("" if self.preset[2] == now else " *")
        if self.preset_var.get() != text:
            self.preset_var.set(text)
        yours = self.preset is not None and self.preset[0][0] == "yours"
        self.preset_del.state(["!disabled"] if yours else ["disabled"])
