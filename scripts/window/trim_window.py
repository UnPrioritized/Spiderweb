"""The Trim shape window (trim.py: Trim rows; wrap.py: Wrapped slices): each key row's runs of touching notes cut down to a share of their length, or to
a number of ticks, shown live on the piano roll (the shared part: tool_window.py). One number (Keep [n] [% / ticks
of each row], a slider for the %) and which end stays."""

import math
import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr
from files.mathexpr import calc, fmt
from files.own_tracks import into_ids
from notes.areas import most_colours
from notes.engine import tagged_notes
from window import big_ask
from window.join_split import without10
from notes.fx import before_page
from notes.trim import BY, MAX_LENGTH, TRIM_DEFAULTS, clean_trim, length_ticks, longest_run
from notes.wrap import GAPS, MOST_SLICES, WRAP_DEFAULTS
from window import look
from window.tool_window import Knob, ToolWindow
from window.widgets import Scrub, Tooltip, bad, good, read_number

SLIDER_PX = 220


def plain_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


class Hold:
    """The slider while its handle is held, as a knob would be (ToolWindow.knob_pressed / undo_key): Ctrl+Z while
    held puts back how it was at the press, and the slider follows the mouse no more until let go."""

    def __init__(self):
        self.down = self.dead = False

    def held(self):
        return self.down

    def call_off(self):
        self.dead = True


class TrimWindow(ToolWindow):
    KEY, ATTR, POS = "trim", "trim_window", "trim_pos"
    DEFAULTS = dict(TRIM_DEFAULTS, mode="rows", **WRAP_DEFAULTS)  # (a page holds only its mode's: clean_trim)
    TEST_PAGES = ({"keep": 50.0}, {"keep": 50.0, "by": "ticks"}, {"mode": "wrap"})  # (pages that trim:
    # tool_window.checked)

    def clean(self, cfg):
        return clean_trim(cfg)

    @classmethod
    def describe_default(cls, v, ppq):
        """Settings → User set defaults: the mode; how much each row keeps and which end stays, or the slices."""
        v = dict(cls.DEFAULTS, **v)
        if v["mode"] == "wrap":
            return (f"{tr('trim.mode_wrap')}, {tr('trim.slices')} {v['slices']}, {tr('trim.colours')} "
                    f"{v['colours']}, {tr('trim.angle')} {fmt(v['angle'])}°")
        if v["by"] == "ticks":
            amount = f"{fmt(round(v['length'] * ppq))} {tr('trim.ticks_of_each_row')}"
        else:
            amount = f"{fmt(round(v['keep'], 3))}{tr('trim.of_each_row')}"
        return f"{tr('trim.keep')} {amount}, {tr('trim.' + v['side'])}"

    def build(self, box):
        self.typed = None  # (value, text typed for it): the box keeps showing it while that value is in use
        top = ttk.Frame(box)
        top.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(top, text=tr("trim.mode")).pack(side="left")
        self.mode = ttk.Combobox(top, state="readonly", width=18,
                                 values=[tr("trim.mode_rows"), tr("trim.mode_wrap")])
        self.mode.pack(side="left", padx=(6, 0))
        self.mode.bind("<<ComboboxSelected>>", lambda e: self.on_mode())
        Tooltip(self.mode, tr("trim.tip_mode"))
        rows = self.rows_box = ttk.Frame(box)  # (the row trim's settings; Wrapped slices' take their place)
        rows.grid(row=2, column=0, columnspan=2, sticky="nw")
        self.wrap_box = self.build_wrap(box)
        self.build_rows(rows)
        self.update_idletasks()  # (the place fits either mode's settings: the window keeps its size)
        box.rowconfigure(2, minsize=max(f.winfo_reqheight() for f in (rows, self.wrap_box)))
        box.columnconfigure(0, minsize=max(f.winfo_reqwidth() for f in (rows, self.wrap_box)))
        self.shown_mode = None

        buttons = ttk.Frame(box)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        reset = ttk.Button(buttons, text=tr("trim.reset"), command=self.reset)
        reset.pack(side="left")
        self.reset_tip = Tooltip(reset, tr("trim.tip_reset"))
        self.accept_row(buttons, tr("trim.accept"))

    def build_rows(self, box):
        row = ttk.Frame(box)
        row.grid(row=1, column=0, columnspan=2, sticky="w")
        label = ttk.Label(row, text=tr("trim.keep"))
        label.pack(side="left")
        self.var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.var, width=8, justify="center")
        self.entry.pack(side="left", padx=4)
        self.entry.bind("<Return>", lambda e: (self.on_entry(enter=True), "break")[1])
        self.entry.bind("<FocusOut>", lambda e: self.on_entry())
        self.unit = ttk.Combobox(row, state="readonly", values=[tr("trim.of_each_row"), tr("trim.ticks_of_each_row")])
        self.unit.pack(side="left")
        self.unit.bind("<<ComboboxSelected>>", lambda e: self.on_unit())
        Tooltip(self.unit, tr("trim.tip_unit"))
        self.scrub = Scrub(self.app, [(self.entry, self.var, lambda: self.on_entry(False))], self.steps, 0, 100,
                           label=label)
        self.scrub.read = lambda text: self.value() if text == self.shown_text() else calc(text)
        for w in (label, self.entry):
            Tooltip(w, tr("trim.tip_keep"))
        self.slider_var = tk.DoubleVar(value=100.0)
        self.slider = ttk.Scale(box, from_=0, to=100, variable=self.slider_var,
                                length=round(SLIDER_PX * self.app.scale), command=lambda v: self.on_slider())
        self.slider.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for b in (1, 3):  # (Tk's own: the right button jumps there and drags too)
            self.slider.bind(f"<ButtonPress-{b}>", self.slider_press, add="+")
            self.slider.bind(f"<ButtonRelease-{b}>", lambda e: self.slider_release(), add="+")
        # (THE CONTROL SCHEME, user 2026-11-23: middle-click = back to its default, 100 % = nothing cut)
        self.slider.bind("<ButtonPress-2>", self.slider_middle)
        self.slider.bind("<B2-Motion>", lambda e: "break")
        self.slider.bind("<ButtonRelease-2>", lambda e: "break")
        Tooltip(self.slider, tr("trim.tip_slider"))
        self.hold = Hold()
        self.pressed_at = None  # where the slider was when pressed
        self.showing = False  # (show() setting the slider: its command fires too)

        side = ttk.Frame(box)
        side.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(side, text=tr("trim.count_from")).pack(anchor="w")
        self.side = tk.StringVar(value="left")
        for value in ("left", "right"):
            b = ttk.Radiobutton(side, text=tr(f"trim.{value}"), variable=self.side, value=value,
                                command=lambda: self.put("side", self.side.get()))
            b.pack(anchor="w", padx=(12, 0))
            Tooltip(b, tr(f"trim.tip_{value}"))
        ttk.Label(box, text=tr("trim.hint"), foreground=look.HINT, wraplength=round(SLIDER_PX * self.app.scale),
                  justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

    # ---- Wrapped slices (wrap.py)

    def build_wrap(self, box):
        s = self.app.scale
        f = ttk.Frame(box)
        f.grid(row=2, column=0, columnspan=2, sticky="nw")
        f.columnconfigure(0, minsize=round(80 * s))
        self.wvars, self.wentries = {}, {}
        for r, key, tip, steps in ((0, "slices", "trim.tip_slices", (1, 10, 1)),
                                   (1, "colours", "trim.tip_colours", (1, 5, 1))):
            lb = ttk.Label(f, text=tr(f"trim.{key}"))
            lb.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=2)
            var = self.wvars[key] = tk.StringVar()
            e = self.wentries[key] = ttk.Entry(f, textvariable=var, width=7)
            e.grid(row=r, column=1, sticky="w", pady=2)
            self.entry_keys(e, key)
            Scrub(self.app, [(e, var, lambda key=key: self.on_wentry(key, False))], steps, *self.limits(key),
                  label=lb)
            for w in (lb, e):
                Tooltip(w, tr(tip))
        lb = ttk.Label(f, text=tr("trim.angle"))
        lb.grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
        dial = ttk.Frame(f)
        dial.grid(row=2, column=1, sticky="w", pady=4)
        self.knob = Knob(dial, s, self.on_knob, pressed=self.knob_pressed)  # (pointing at a point of the ball)
        self.knob.pack(side="left")
        var = self.wvars["angle"] = tk.StringVar()
        e = self.wentries["angle"] = ttk.Entry(dial, textvariable=var, width=6, justify="center")
        e.pack(side="left", padx=(10, 0))
        self.entry_keys(e, "angle")
        Scrub(self.app, [(e, var, lambda: self.on_wentry("angle", False))], (1, 15, 0.1), -180, 180, label=lb)
        ttk.Label(dial, text="°", foreground=look.HINT).pack(side="left", padx=(2, 0))
        for w in (lb, self.knob, e):
            Tooltip(w, tr("trim.tip_angle"))
        lb = ttk.Label(f, text=tr("trim.roundness"))
        lb.grid(row=3, column=0, sticky="e", padx=(0, 8), pady=2)
        held = ttk.Frame(f)
        held.grid(row=3, column=1, sticky="w", pady=2)
        self.round_var = tk.DoubleVar(value=100.0)
        self.round_slider = ttk.Scale(held, from_=0, to=100, variable=self.round_var, length=round(130 * s),
                                      command=lambda v: self.on_round())
        self.round_slider.pack(side="left")
        for b in (1, 3):
            self.round_slider.bind(f"<ButtonPress-{b}>", self.round_press, add="+")
            self.round_slider.bind(f"<ButtonRelease-{b}>", lambda e: self.round_release(), add="+")
        self.round_slider.bind("<ButtonPress-2>", self.round_middle)  # (middle-click = its default, 100 %)
        self.round_slider.bind("<B2-Motion>", lambda e: "break")
        self.round_slider.bind("<ButtonRelease-2>", lambda e: "break")
        self.round_text = ttk.Label(held, text="", width=6)
        self.round_text.pack(side="left", padx=(6, 0))
        for w in (lb, self.round_slider):
            Tooltip(w, tr("trim.tip_roundness"))
        self.round_hold = Hold()
        self.round_at = None
        lb = ttk.Label(f, text=tr("trim.gaps"))
        lb.grid(row=4, column=0, sticky="e", padx=(0, 8), pady=2)
        self.gaps = ttk.Combobox(f, state="readonly", width=12, values=[tr("trim.gaps_whole"), tr("trim.gaps_each")])
        self.gaps.grid(row=4, column=1, sticky="w", pady=2)
        self.gaps.bind("<<ComboboxSelected>>", lambda e: self.put("gaps", GAPS[self.gaps.current()]))
        for w in (lb, self.gaps):
            Tooltip(w, tr("trim.tip_gaps"))
        self.own = tk.BooleanVar()
        b = ttk.Checkbutton(f, text=tr("trim.own_shapes"), variable=self.own,
                            command=lambda: self.put("own", self.own.get()))
        b.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        Tooltip(b, tr("trim.tip_own_shapes"))
        return f

    def entry_keys(self, e, key):
        e.bind("<Return>", lambda ev: (self.on_wentry(key), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.on_wentry(key))

    def most(self):
        return most_colours(self.app.all16())

    def limits(self, key):
        return {"slices": (2, MOST_SLICES), "colours": (1, self.most()), "angle": (-180, 180)}[key]

    def wshown(self, key):
        return fmt(self.cfg[key])

    def on_wentry(self, key, done=True):
        e, var = self.wentries[key], self.wvars[key]
        text = var.get().strip()
        if text == self.wshown(key):
            good(e)
            return
        lo, hi = self.limits(key)
        v = read_number(text, lo - 1e-9, hi + 1e-9)
        if v is None or key != "angle" and v != int(v):
            e.good_text = self.wshown(key)
            bad(e)
            return
        good(e)
        v = round(v, 3) if key == "angle" else int(v)
        if v != self.cfg[key]:
            self.put(key, v, done)

    def on_mode(self):
        mode = ("rows", "wrap")[self.mode.current()]
        if mode != self.cfg.get("mode", "rows"):
            self.undo.key = None
            self.put("mode", mode)

    def on_knob(self, value, done):
        """The Angle dial turned: its pointer = the angle (-100..100 = -180..180 degrees)."""
        self.cfg["angle"] = round(value * 1.8, 3)
        self.wvars["angle"].set(self.wshown("angle"))
        self.wentries["angle"].config(style="TEntry")
        self.preview(done)
        self.undo.mark("angle")
        if done:
            self.undo.key = None

    def round_press(self, e):
        if self.round_hold.down:
            return
        self.knob_pressed(self.round_hold)
        self.round_hold.down, self.round_hold.dead = True, False
        self.round_at = self.round_var.get()

    def round_middle(self, e):
        if not self.round_hold.down and self.cfg["round"] != 100:
            self.undo.key = None
            self.put("round", 100.0, True)
        return "break"

    def round_release(self):
        held, self.round_hold.down = self.round_hold.down, False
        if not held:
            return
        dead, self.round_hold.dead = self.round_hold.dead, False
        if dead or abs(self.round_var.get() - self.round_at) < 1e-9:
            self.show()
            if not dead and self.late:
                self.catch_up()
            return
        self.put("round", self.round_now(), True)

    def round_now(self):
        return float(math.floor(self.round_var.get() + 0.5))

    def on_round(self):
        if self.showing:
            return
        if self.round_hold.down:
            if self.round_hold.dead:
                self.show()
            elif self.round_now() != self.cfg["round"]:
                self.put("round", self.round_now(), False)
            return
        v = self.round_now()
        if v != self.cfg["round"]:
            self.undo.key = None
            self.put("round", v, True)

    def page_cfg(self, i, cfg):
        """A Wrapped slices page holds how many colours the project has (15, 16 with "Use channel 10")."""
        if cfg and cfg.get("mode") == "wrap":
            m = self.most()
            return dict(cfg, most=m, colours=min(cfg["colours"], m))
        return cfg

    def accept(self):
        """OK. "Each slice becomes its own shape" ticked on a Wrapped slices page: the slices first."""
        self.catch_up()
        if not (self.cfg.get("mode") == "wrap" and self.cfg.get("own") and self.at is not None
                and clean_trim(self.cfg) and self.own_shapes()):
            super().accept()

    def own_shapes(self):
        """Each slice of the page shown becomes a shape of its own notes, exactly as shown (the steps after the page
        already done on it, like Copy along a path's Turn into plain shapes), in place of its shape (Ctrl+Z gives it
        back). False = nothing done (no notes, or "no" to many shapes)."""
        from window.path_window import PLAIN_ASK, PathWindow
        app, ppq = self.app, self.app.ppq
        app.colour_edit_leave()
        if app.notes_late:
            app.shapes_changed(now=True)
        use10, shown = app.multi_rendered()
        made = {}
        for i in self.targets:
            sh = app.shapes[i]
            fx, k = sh.get("fx") or [], self.at.get(i)
            if k is None or k >= len(fx) or fx[k].get("off"):
                continue
            notes, tracks = tagged_notes(sh, k, ppq, app.keys)
            if not len(notes) or notes.shape[1] < 5:
                continue
            tag = notes[:, -1]
            into = app.in_own_track(sh)
            if into:
                slots = (np.unique(np.asarray(tracks, np.int64), return_inverse=True)[1].ravel()
                         if tracks is not None else np.zeros(len(notes), np.int64))
            else:
                slots = PathWindow.shown_slots(self, notes, tag, tracks, i, shown)
            order = np.argsort(tag, kind="stable")
            cuts = np.flatnonzero(np.diff(tag[order])) + 1
            news = []
            for x in np.split(order, cuts):
                if not len(x):
                    continue
                new = without10(app.copied(np.column_stack([notes[x, :4], slots[x], np.full(len(x), i)]), False,
                                           True), use10)
                new["made"] = "plain"
                new["made_by"] = ["wrap", app.shape_label(sh)]
                new["name"] = tr("trim.slice_name", shape=app.shape_label(sh), n=int(tag[x[0]]) + 1)
                if into:
                    for key in ("own_slots", "slots10"):
                        new.pop(key, None)
                    new["into"] = into_ids(sh)
                    if "slots10" in sh:
                        new["slots10"] = sh["slots10"]
                news.append(new)
            if news:
                made[i] = news
        total = sum(len(v) for v in made.values())
        if not total:
            return False
        if total > PLAIN_ASK and "plain_wrap" not in app.big_skip:
            go, skip = big_ask.dialog(self, tr("big_ask.title"), tr("trim.many_shapes", n=f"{total:,}") + "\n\n" +
                                      tr("big_ask.go"), False)
            if go and skip:
                app.big_skip.add("plain_wrap")
            if not go:
                return True  # (the window stays open, nothing changed)
        self.tidy()
        self.settle()
        app.roll.cancel_draft()
        app.push_undo(name=tr("trim.own_shapes"))
        out, picked = [], []
        for i, sh in enumerate(app.shapes):
            if i not in made:
                out.append(sh)
                continue
            picked += range(len(out), len(out) + len(made[i]))
            out += made[i]
        self.close()
        app.shapes[:] = out
        app.select_many(picked, picked[0])
        app.shapes_changed()
        return True

    def reset(self):
        """Back to the starting settings of the mode shown (the mode stays)."""
        mode = self.cfg.get("mode", "rows")
        self.cfg = dict(self.DEFAULTS, mode=mode)
        self.show()
        self.preview()
        self.undo.mark()

    def show_wrap(self):
        c = self.cfg
        self.wentries["colours"].config(style="TEntry")
        for key, var in self.wvars.items():
            if var.get() != self.wshown(key):
                var.set(self.wshown(key))
            self.wentries[key].config(style="TEntry")
        if not self.knob.drag and abs(self.knob.value - c["angle"] / 1.8) > 1e-9:
            self.knob.set(c["angle"] / 1.8)
        if abs(self.round_var.get() - c["round"]) > 1e-9:
            self.showing = True
            self.round_var.set(c["round"])
            self.showing = False
        text = f"{fmt(c['round'])} %"
        if self.round_text.cget("text") != text:
            self.round_text.config(text=text)
        if self.gaps.current() != GAPS.index(c["gaps"]):
            self.gaps.current(GAPS.index(c["gaps"]))
        if self.own.get() != c["own"]:
            self.own.set(c["own"])

    # ---- the number: keep % (up to 3 decimals) or ticks (whole, kept in beats)

    def ticks(self):
        return self.cfg["by"] == "ticks"

    def steps(self):
        return (10, 100, 1) if self.ticks() else (1, 10, 0.1)

    def value(self):
        """The number in use, as the box shows it (% or ticks)."""
        return length_ticks(self.cfg["length"], self.app.ppq) if self.ticks() else self.cfg["keep"]

    def exact(self):
        """Is the number in use exactly what the box shows? (Not after a PPQ change put a length between ticks.)"""
        return not self.ticks() or abs(self.cfg["length"] * self.app.ppq - self.value()) < 1e-9

    def shown_text(self):
        v = self.value()
        typed = self.typed
        return typed[2] if typed and typed[:2] == (self.cfg["by"], v) else fmt(v)

    def on_entry(self, done=True, enter=False):
        text = self.var.get().strip()
        if text == self.shown_text() and (self.exact() or not enter):  # (left unchanged: the exact value stays,
            # not the box's shortened one; Enter on a length a PPQ change left between ticks makes it whole)
            good(self.entry)
            return
        try:
            v = float(calc(text))
            if self.ticks():
                if not 1 <= v <= MAX_LENGTH * self.app.ppq or v != int(v):
                    raise ValueError
                v = int(v)
            elif not 0 <= v <= 100:
                raise ValueError
            else:
                v = round(v, 3)
        except (ValueError, ZeroDivisionError, OverflowError):
            self.entry.good_text = self.shown_text()
            bad(self.entry)
            return
        good(self.entry)
        if done and fmt(v) != text and not plain_number(text):
            self.typed = (self.cfg["by"], v, text)  # (e.g. "100/3": the box keeps showing it; 12.3456 shows 12.346)
        if v != self.value() or not self.exact():
            if self.ticks():
                self.put("length", v / self.app.ppq, done)
            else:
                self.put("keep", v, done)
        else:
            self.show()

    def on_unit(self):
        """% of each row / ticks of each row picked."""
        by = BY[self.unit.current()]
        if by == self.cfg["by"]:
            return
        if by == "ticks":  # (starts at the longest row: nothing cut until a number is set, like 100 %)
            self.cfg["length"] = max(1, self.longest()) / self.app.ppq
        if self.at is None:  # ("+": no page yet, as the number changes nothing)
            self.cfg["by"] = by
            self.show()
            return
        self.undo.key = None
        self.put("by", by)

    def longest(self):
        """The longest row of touching notes in the selected shapes, in ticks, before the page shown (or all their
        pages, on "+")."""
        most = 0
        for i in self.targets:
            sh = self.app.shapes[i]
            fx = sh.get("fx") or []
            k = len(fx) if self.at is None else self.at[i]
            most = max(most, longest_run(self.app.notes_of(before_page(sh, k))))
        return most

    # ---- the slider (whole percents; greyed while counting ticks)

    def slider_press(self, e):
        if self.ticks() or self.hold.down:  # (a second button while one is held: the first press counts)
            return
        self.knob_pressed(self.hold)  # (a step of its own; Ctrl+Z while held: back to the press)
        self.hold.down, self.hold.dead = True, False
        self.pressed_at = self.slider_var.get()

    def slider_middle(self, e):
        """Middle-click the slider: Keep back to 100 % (one step; nothing while another button holds it)."""
        if not self.ticks() and not self.hold.down and self.cfg["keep"] != 100:
            self.undo.key = None
            self.put("keep", 100.0, True)
        return "break"

    def slider_release(self):
        held, self.hold.down = self.hold.down, False
        if not held:
            return
        dead, self.hold.dead = self.hold.dead, False
        if dead or abs(self.slider_var.get() - self.pressed_at) < 1e-9:  # (a click that didn't move it: nothing)
            self.show()
            if not dead and self.late:
                self.catch_up()
            return
        self.put("keep", self.slider_now(), True)

    def slider_now(self):
        """The slider's whole percent (halves up, like the trim's ticks)."""
        return float(math.floor(self.slider_var.get() + 0.5))

    def on_slider(self):
        if self.showing or self.ticks():
            return
        if self.hold.down:
            if self.hold.dead:  # (Ctrl+Z while held: it stays where it was put back)
                self.show()
            elif self.slider_now() != self.cfg["keep"]:
                self.put("keep", self.slider_now(), False)
            return
        v = self.slider_now()  # (moved without the left button: a jump, the keys: a step of its own)
        if v != self.cfg["keep"]:
            self.undo.key = None
            self.put("keep", v, True)

    def show(self):
        """The window shows self.cfg (only what differs is changed, so dragging stays quick)."""
        c = self.cfg
        mode = c.get("mode", "rows")
        if mode != self.shown_mode:
            self.shown_mode = mode
            (self.rows_box if mode == "wrap" else self.wrap_box).grid_remove()
            (self.wrap_box if mode == "wrap" else self.rows_box).grid()
            self.mode.current(("rows", "wrap").index(mode))
            self.reset_tip.set_text(tr("trim.tip_reset_wrap" if mode == "wrap" else "trim.tip_reset"))
        self.show_wrap()
        text = self.shown_text()
        if self.var.get() != text:
            self.var.set(text)
        if self.unit.current() != BY.index(c["by"]):
            self.unit.current(BY.index(c["by"]))
        self.scrub.lo, self.scrub.hi = (1, None) if self.ticks() else (0, 100)
        if abs(self.slider_var.get() - c["keep"]) > 1e-9:
            self.showing = True  # (setting it calls its command)
            self.slider_var.set(c["keep"])
            self.showing = False
        self.slider.state(["disabled"] if self.ticks() else ["!disabled"])
        if self.side.get() != c["side"]:
            self.side.set(c["side"])


open_trim = TrimWindow.open
