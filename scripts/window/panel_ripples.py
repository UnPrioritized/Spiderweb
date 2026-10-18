"""The side panel's rows for Inside fill "Ripples" (notes/ripples.py): Notes Fill / Spam, Steps, Colours, Turn,
Spacing, Grow to + Auto, Drawn shapes are ring edges, Every other one empty, Inside them. Mixed into App (beside
NestedPanel, whose change_nested makes the undo steps)."""

import copy
import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.mathexpr import calc, fmt
from notes.custom import frame_to_uv
from notes.ripples import (INSIDES, MOST_COLOURS, MOST_GROW, MOST_STEPS, OTHER, RIPPLES_DEFAULTS, framed,
                           handle_spots, set_source, source_middles, source_turn)
from window import look
from window.panel_nested import NOTES_CHOICES, drop_row
from window.widgets import Scrub, Tooltip, bad, good, leave_box, unchanged

# the number boxes: (setting, label, unit, lowest, highest, Scrub steps, setting -> box, box -> setting); two a row
BOXES = [
    [("steps", "ripples_steps", "", 2, MOST_STEPS, (1, 5, 1), int, int),
     ("colours", "ripples_colours", "", 1, MOST_COLOURS, (1, 1, 1), int, int)],
    [("spacing", "ripples_spacing", "", -100, 100, (1, 10, 1), int, lambda x: int(round(x)))],
    [("turn", "ripples_turn", "ripples_turn_unit", -180, 180, (1, 10, 0.1), float, float)],
    [("grow", "ripples_grow", "%", 100, MOST_GROW * 100, (10, 100, 1), lambda v: round(v * 100, 6),
      lambda x: x / 100)],
]
TICKS = [("trim", "ripples_trim"), ("edge", "ripples_edge"), ("skip", "ripples_skip")]
TURN_WAYS = ("same", "other", "own")  # (the picked source's Turn dropdown)
PICKED_LIMITS = {"turn": (-180, 180, (1, 10, 0.1)), "u": (-1000, 1000, (1, 10, 0.1)), "v": (-1000, 1000, (1, 10, 0.1))}
RIPPLES_UNDO = tr("panel_custom.ripples")


def ripples_on(t):
    """Custom shape (or the settings for new ones) t with a Ripples fill (Nested / Tiles win if it has one too)."""
    return (bool(t.get("ripples")) and t.get("fill") in ("fill", "spam") and not t.get("text") and "notes" not in t
            and not t.get("hz") and not t.get("nested") and not t.get("tiles"))


class RipplesPanel:
    """Mixed into App."""

    def _build_ripples(self, opts):
        """The Ripples rows, put in self.custom_rows (shown by sync_custom while Ripples is the Inside fill)."""
        self.ripples_rows, self.ripples_boxes, self.ripples_ticks = [], {}, {}
        row, self.ripples_notes_box = drop_row(self, opts, "nested_notes", [t for _, t in NOTES_CHOICES])
        self.ripples_rows.append(row)
        self.ripples_notes_box.bind("<<ComboboxSelected>>", lambda e: (
            self.set_ripples_fill(NOTES_CHOICES[e.widget.current()][0]), self.roll.focus_set()))
        for line in BOXES:
            row = ttk.Frame(opts)
            for n, (key, text, unit, lo, hi, steps, _, _) in enumerate(line):
                lb = ttk.Label(row, text=tr("panel_custom." + text))
                lb.grid(row=0, column=3 * n, sticky="w", padx=(12 if n else 0, 0))
                var = tk.StringVar()
                e = ttk.Entry(row, textvariable=var, width=5)
                e.grid(row=0, column=3 * n + 1, sticky="w", padx=4)
                if unit:
                    ttk.Label(row, text=unit if unit == "%" else tr("panel_custom." + unit),
                              foreground=look.HINT).grid(row=0, column=3 * n + 2, sticky="w")
                apply = (lambda left=False, key=key: self.on_ripples_box(key, left))
                e.bind("<Return>", lambda ev, apply=apply: apply())
                leave_box(self, e, var, apply)
                Scrub(self, [(e, var, apply)], steps, lo, hi, label=lb)
                for w in (lb, e):
                    Tooltip(w, tr("panel_custom." + text + "_tip"))
                self.ripples_boxes[key] = (e, var)
            if line[0][0] == "grow":  # Grow to [500] % [x] Auto
                self.ripples_auto_var = tk.BooleanVar(value=True)
                b = ttk.Checkbutton(row, text=tr("panel_custom.ripples_auto"), variable=self.ripples_auto_var,
                                    command=lambda: self.set_ripples("auto", self.ripples_auto_var.get()))
                b.grid(row=0, column=3, columnspan=3, sticky="w", padx=(12, 0))
                Tooltip(b, tr("panel_custom.ripples_auto_tip"))
                self.ripples_auto_tick = b
            self.ripples_rows.append(row)
        self.ripples_table = list(self.ripples_rows)  # (lined up with the Nested / Tiles ones, panel_custom)
        for key, text in TICKS:
            var = tk.BooleanVar(value=False)
            b = ttk.Checkbutton(opts, text=tr("panel_custom." + text), variable=var,
                                command=lambda key=key, var=var: self.set_ripples(key, var.get()))
            Tooltip(b, tr("panel_custom." + text + "_tip"))
            self.ripples_rows.append(b)
            self.ripples_ticks[key] = var
            if key == "trim":
                self.ripples_trim_tick = b
        texts = [tr("panel_custom.ripples_inside_" + v) for v in INSIDES]
        row, self.ripples_inside_box = drop_row(self, opts, "ripples_inside", texts)
        self.ripples_inside_box.bind("<<ComboboxSelected>>", lambda e: (
            self.set_ripples("inside", INSIDES[e.widget.current()]), self.roll.focus_set()))
        self.ripples_rows.append(row)
        self.ripples_table.append(row)
        # nothing drawn inside the outline: no rings (ripples.parts)
        self.ripples_warn = ttk.Label(opts, text=tr("panel_custom.ripples_none"), foreground=look.WARN,
                                      font=look.font(8), wraplength=int(260 * self.scale), justify="left")
        self.ripples_rows.append(self.ripples_warn)
        self._build_picked(opts)
        for row in self.ripples_rows:
            self.custom_rows.append((row, dict(anchor="w", padx=(20, 0), pady=(1, 0))))

    def _build_picked(self, opts):
        """The picked source's rows (a centre point clicked on the piano roll): "Picked source (2 of 3)", Turn
        Same as all / Other way / Own [x], Centre u / v %."""
        self.ripple_pick = None  # (the picked source's number: app.pick_source)
        self.picked_title = ttk.Label(opts, font=look.font(9, "bold"))
        Tooltip(self.picked_title, tr("panel_custom.ripples_picked_tip"))
        self.picked_boxes, labels = {}, {}
        turn_row, centre_row = ttk.Frame(opts), ttk.Frame(opts)
        lb = ttk.Label(turn_row, text=tr("panel_custom.ripples_picked_turn"))
        lb.grid(row=0, column=0, sticky="w")
        self.picked_turn_box = ttk.Combobox(turn_row, state="readonly", width=12,
                                            values=[tr("panel_custom.ripples_turn_" + w) for w in TURN_WAYS])
        self.picked_turn_box.grid(row=0, column=1, sticky="w", padx=4)
        self.picked_turn_box.bind("<<ComboboxSelected>>", lambda e: (
            self.on_picked_way(TURN_WAYS[e.widget.current()]), self.roll.focus_set()))
        for w in (lb, self.picked_turn_box):
            Tooltip(w, tr("panel_custom.ripples_picked_turn_tip"))
        spots = [("turn", turn_row, None, 2, "ripples_turn_unit"),
                 ("u", centre_row, "ripples_picked_u", 1, "%"), ("v", centre_row, "ripples_picked_v", 4, "%")]
        ttk.Label(centre_row, text=tr("panel_custom.ripples_picked_centre")).grid(row=0, column=0, sticky="w")
        for key, row, text, col, unit in spots:
            lb = labels[key] = ttk.Label(row, text=tr("panel_custom." + text)) if text else None
            if lb:
                lb.grid(row=0, column=col, sticky="w", padx=(8, 0))
            var = tk.StringVar()
            box = ttk.Entry(row, textvariable=var, width=6 if text else 5)
            box.grid(row=0, column=col + 1, sticky="w", padx=4)
            ttk.Label(row, text=unit if unit == "%" else tr("panel_custom." + unit),
                      foreground=look.HINT).grid(row=0, column=col + 2, sticky="w")
            self.picked_boxes[key] = (box, var)
            apply = (lambda left=False, key=key: self.on_picked_box(key, left))
            box.bind("<Return>", lambda ev, apply=apply: apply())
            leave_box(self, box, var, apply)
            lo, hi, steps = PICKED_LIMITS[key]
            Scrub(self, [(box, var, apply)], steps, lo, hi, label=lb)
            for w in (lb, box):
                if w:
                    Tooltip(w, tr("panel_custom.ripples_picked_" + ("turn" if key == "turn" else "centre") + "_tip"))
        self.picked_rows = [self.picked_title, turn_row, centre_row]
        self.ripples_rows += self.picked_rows

    def picked_source(self):
        """(the shape, its picked source's number, how many sources) while one placed Ripples shape is selected and
        one of its centre points is picked, else None."""
        n, tgts = self.ripple_pick, self.ripples_targets()
        if n is None or len(tgts) != 1 or tgts[0] is self.custom_defaults:
            return None
        count = len(handle_spots(tgts[0]))
        return (tgts[0], n, count) if n < count else None

    def pick_source(self, n):
        """Ripples source n of the selected shape picked (its centre point clicked; None = none)."""
        if n != self.ripple_pick:
            self.ripple_pick = n
            self.sync_custom()
            self.roll.request_redraw()

    def set_source(self, n, key, value, name):
        """Source n's own centre point ("at") or turn ("turns") of the selected shape; None = as all (one undo
        step)."""
        tgts = [t for t in self.ripples_targets() if t is not self.custom_defaults]
        if len(tgts) == 1:
            self.ripple_pick = n
            now = tgts[0]["ripples"].get(key) or []
            if (now[n] if n < len(now) else None) == value:  # (nothing changes: no undo step, Redo kept, hunt)
                return
            self.change_nested(tgts, lambda t: set_source(t, key, n, value), name)

    def sync_picked(self):
        """The picked source's rows show its turn and centre point."""
        got = self.picked_source()
        if not got:
            return
        t, n, count = got
        self.picked_title.config(text=tr("panel_custom.ripples_picked", n=n + 1, count=count))
        turns = t["ripples"].get("turns") or []
        own = turns[n] if n < len(turns) else None
        self.picked_turn_box.current(0 if own is None else 1 if own == OTHER else 2)
        e, var = self.picked_boxes["turn"]
        var.set(fmt(source_turn(t["ripples"], n)))
        e.config(state="normal" if own not in (None, OTHER) else "disabled", style="TEntry")
        to_uv = frame_to_uv(t["pts"])
        uv = to_uv(*handle_spots(t)[n]) if to_uv else (0.5, 0.5)
        for key, x in zip(("u", "v"), uv):
            e, var = self.picked_boxes[key]
            var.set(fmt(round(x * 100, 4)))
            e.config(style="TEntry")

    def on_picked_way(self, way):
        """Turn Same as all / Other way / Own picked for the picked source (Own starts at the turn it has now)."""
        got = self.picked_source()
        if self._loading or not got:
            return
        t, n, _ = got
        value = {"same": None, "other": OTHER}.get(way, source_turn(t["ripples"], n))
        self.set_source(n, "turns", value, tr("panel_custom.ripples_picked_turn"))

    def on_picked_box(self, key, left=False):
        """The picked source's Turn or Centre u / v box entered (left: only left, nothing unless changed)."""
        e, var = self.picked_boxes[key]
        got = self.picked_source()
        if self._loading or not got or (left or key != "turn") and unchanged(e):  # (an untouched Centre box on
            return  # Enter too: else it pins the point where it is, hunt)
        t, n, _ = got
        lo, hi, _ = PICKED_LIMITS[key]
        try:
            x = calc(var.get())
            if not lo <= x <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        good(e)
        if key == "turn":
            return self.set_source(n, "turns", float(x), tr("panel_custom.ripples_picked_turn"))
        to_uv = frame_to_uv(t["pts"])
        if not to_uv:
            return
        uv = list(to_uv(*handle_spots(t)[n]))  # (a sliced piece's too: kept in its own box, sliced.source)
        uv["uv".index(key)] = x / 100
        self.set_source(n, "at", [round(w, 6) for w in uv], tr("panel_custom.ripples_picked_centre"))

    def sync_ripples(self, t):
        """The Ripples rows show t's settings (the first target's)."""
        cfg = t.get("ripples") or RIPPLES_DEFAULTS
        self._loading = True
        self.ripples_notes_box.current(1 if t.get("fill") == "spam" else 0)
        for line in BOXES:
            for key, _, _, _, _, _, shown, _ in line:
                e, var = self.ripples_boxes[key]
                var.set(fmt(shown(cfg.get(key, RIPPLES_DEFAULTS[key]))))
                e.config(style="TEntry")
        auto = cfg.get("auto", True)
        self.ripples_auto_var.set(auto)
        # (Auto fills a frame the rings are cut to: none drawn, or Cut at the outline off = Grow to is used)
        # (Cut at the outline: only with a frame drawn round the sources, hunt)
        frame = t is self.custom_defaults or not t.get("ripples") or framed(t)
        usable = cfg.get("trim", True) and frame
        self.ripples_auto_tick.config(state="normal" if usable else "disabled")
        self.ripples_boxes["grow"][0].config(state="disabled" if auto and usable else "normal")
        for key, var in self.ripples_ticks.items():
            var.set(bool(cfg.get(key, RIPPLES_DEFAULTS[key])))
        self.ripples_trim_tick.config(state="normal" if frame else "disabled")
        self.ripples_inside_box.current(INSIDES.index(cfg.get("inside", "rings")))
        self.sync_picked()
        self._loading = False

    def ripples_row_states(self, t, on):
        """{row: shown} for sync_custom's show_custom_rows (on: Ripples is the Inside fill shown); the warning only
        while t (a placed shape) has nothing drawn inside its outline, the picked source's rows only while one is
        picked."""
        warn = on and t is not self.custom_defaults and not source_middles(t)
        picked = on and self.picked_source() is not None
        return {w: on and (w is not self.ripples_warn or warn) and (w not in self.picked_rows or picked)
                for w in self.ripples_rows}

    def ripples_targets(self):
        return [t for t in self.custom_targets() if ripples_on(t)]

    def pick_ripples(self):
        """Ripples picked as the Inside fill (their own settings kept from before, else the defaults; Notes from
        their Fill / Spam, else Fill). Not text, pasted notes or Hz bass."""
        tgts = [t for t in self.custom_targets() if not t.get("text") and "notes" not in t]
        if any(t.get("hz") for t in tgts):  # (they don't mix: the others change, after a warning)
            tgts = self.skip_hz(tgts)
        tgts = [t for t in tgts if not ripples_on(t)]

        def put(t):
            if t["fill"] not in ("fill", "spam"):
                t["fill"] = "fill"
            t.pop("nested", None)  # (one Inside pattern at a time)
            t.pop("tiles", None)
            if not t.get("ripples"):
                t["ripples"] = copy.deepcopy(RIPPLES_DEFAULTS)

        self.change_nested(tgts, put, RIPPLES_UNDO)
        self.tips.show("fill", wait=True)

    def set_ripples_fill(self, fill):
        """Notes Fill / Spam of the Ripples targets."""
        if self._loading:
            return
        tgts = [t for t in self.ripples_targets() if t["fill"] != fill]
        self.change_nested(tgts, lambda t: t.update(fill=fill), tr("panel_custom.nested_notes"))

    def set_ripples(self, key, value):
        """One Ripples setting of every Ripples target."""
        if self._loading:
            return
        tgts = [t for t in self.ripples_targets() if t["ripples"].get(key, RIPPLES_DEFAULTS.get(key)) != value]

        def put(t):
            t["ripples"] = dict(t["ripples"], **{key: copy.deepcopy(value)})

        self.change_nested(tgts, put, RIPPLES_UNDO)

    def on_ripples_box(self, key, left=False):
        """A Ripples number box entered (left: only left, nothing unless changed)."""
        e, var = self.ripples_boxes[key]
        if self._loading or left and unchanged(e):
            return
        _, _, _, lo, hi, _, _, to_setting = next(b for line in BOXES for b in line if b[0] == key)
        try:
            x = calc(var.get())
            if not lo <= x <= hi:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            bad(e)
            return
        self.set_ripples(key, to_setting(x))
        good(e)
