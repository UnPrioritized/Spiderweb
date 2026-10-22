"""The side panel's Mask rows (mask.py): a selected shape made a mask or a shape again, and a mask's settings."""

import tkinter as tk
from tkinter import ttk

import numpy as np

from files.lang import tr, trn
from files.mathexpr import calc, fmt
from notes.custom import notes_shape
from notes.engine import SHAPE_DEFAULTS, clean_shape
from notes.sustain import is_sustained
from notes.passengers import carried, track_map
from notes.mask import (GROUP_KEYS, MASK_DEFAULTS, MODES, MOST_SIZE, SHOWS, THICKNESS, can_mask, free_colour, is_mask,
                        is_solid, loops_of, mask_units, new_group_id, slot_pal, slot_rule, tidy_groups, unit_of,
                        unmasked, worked_now)
from window import look
from window.widgets import Scrub, Tooltip, bad, good, leave_box, same_or_blank, show_mixed, unchanged, unit_word

SHOW_CHOICES = [(v, tr("mask.show_" + v)) for v in SHOWS]
MODE_CHOICES = [(v, tr("mask.mode_" + v)) for v in MODES]


class MaskPanel:
    """Mixed into App."""

    def _build_mask(self):
        """[x] Mask; for masks under it: [x] Own notes sound, Shows [Inside / Along the line] (closed shapes),
        Thickness [n] keys, [x] Invert. Shown only with shapes selected (new shapes: the toolbar's Mask tick)."""
        box = self.mask_box = ttk.Frame(self.settings)
        self.mask_on = tk.BooleanVar(self, value=False)
        self.mask_mixed = {}  # tick -> some of the picked masks have it, some not (half ticked)
        b = self.mask_tick = ttk.Checkbutton(box, text=tr("mask.panel_tick"), variable=self.mask_on,
                                             command=lambda: self.on_mask("on"))
        b.pack(anchor="w")
        Tooltip(b, tr("mask.panel_tip"))
        rows = self.mask_rows = ttk.Frame(box)
        self.mask_own = tk.BooleanVar(self, value=True)
        b = self.mask_own_tick = ttk.Checkbutton(rows, text=tr("mask.own"), variable=self.mask_own,
                                                 command=lambda: self.on_mask("own"))
        b.pack(anchor="w")
        Tooltip(b, tr("mask.own_tip"))
        c = self.mask_show_row = ttk.Frame(rows)
        c.pack(anchor="w", pady=(1, 0))
        ttk.Label(c, text=tr("mask.show")).pack(side="left")
        self.mask_show_box = ttk.Combobox(c, values=[t for _, t in SHOW_CHOICES], state="readonly",
                                          width=max(len(t) for _, t in SHOW_CHOICES))
        self.mask_show_box.pack(side="left", padx=4)
        self.mask_show_box.bind("<<ComboboxSelected>>", lambda ev: (self.on_mask("show"), self.roll.focus_set()))
        self.mask_show_tip = Tooltip(self.mask_show_box, tr("mask.show_tip"))
        c = ttk.Frame(rows)
        c.pack(anchor="w", pady=(1, 0))
        lb = ttk.Label(c, text=tr("mask.thickness"))
        lb.pack(side="left")
        self.mask_thick_tip = Tooltip(lb, tr("mask.thickness_tip"))
        self.mask_size_var = tk.StringVar()
        e = self.mask_size_entry = ttk.Entry(c, textvariable=self.mask_size_var, width=5)
        e.pack(side="left", padx=4)
        Scrub(self, [(e, self.mask_size_var, lambda: self.on_mask("size"))], (0.5, 4, 0.1), 0, MOST_SIZE, label=lb,
              drag_box=True)
        unit_word(ttk.Label(c, foreground=look.HINT), self.mask_size_var, "unit.keys").pack(side="left")
        e.bind("<Return>", lambda ev: self.on_mask("size"))
        leave_box(self, e, self.mask_size_var, lambda left, e=e: self.on_mask("size", left and e))
        self.mask_invert = tk.BooleanVar(self, value=False)
        b = self.mask_invert_tick = ttk.Checkbutton(rows, text=tr("mask.invert"), variable=self.mask_invert,
                                                    command=lambda: self.on_mask("invert"))
        b.pack(anchor="w", pady=(1, 0))
        Tooltip(b, tr("mask.invert_tip"))
        c = self.mask_mode_row = ttk.Frame(rows)  # (masks in a group only: right-click Combine masks)
        ttk.Label(c, text=tr("mask.mode")).pack(side="left")
        self.mask_mode_box = ttk.Combobox(c, values=[t for _, t in MODE_CHOICES], state="readonly",
                                          width=max(len(t) for _, t in MODE_CHOICES))
        self.mask_mode_box.pack(side="left", padx=4)
        self.mask_mode_box.bind("<<ComboboxSelected>>", lambda ev: (self.on_mask("mode"), self.roll.focus_set()))
        self.mask_mode_tip = Tooltip(self.mask_mode_box, tr("mask.mode_tip"))
        b = self.mask_apply_button = ttk.Button(rows, text=tr("mask.apply"), command=self.apply_masks)
        b.pack(anchor="w", pady=(4, 0))
        Tooltip(b, tr("mask.apply_tip"))

    def mask_targets(self):
        return [self.shapes[i] for i in sorted(self.sels) if i < len(self.shapes) and can_mask(self.shapes[i])]

    def screen_k(self):
        """Beats per key as the piano roll shows them now (a new mask's proportions, mask.py)."""
        roll = self.roll
        return roll.sy / roll.sx if roll.sx and roll.sy else MASK_DEFAULTS["k"]

    def new_mask(self):
        """A new mask's settings: the last thickness set, the piano roll's proportions now."""
        return dict(self.mask_defaults, k=self.screen_k())

    def sync_mask(self):
        """Mask tick half ticked when some picked shapes are masks and some not; the rows under it only while every
        picked shape is a mask (my pick, like Colours' rows)."""
        tgts = self.mask_targets()
        shown = bool(tgts)
        if shown != bool(self.mask_box.winfo_manager()):
            if shown:
                self.mask_box.pack(fill="x", pady=(4, 0), after=self.colours_box)
            else:
                self.mask_box.pack_forget()
        if not shown:
            return
        masks = [t["mask"] for t in tgts if t.get("mask")]
        self._loading = True
        every = len(masks) == len(tgts)
        self.mask_on.set(every)
        self.mask_tick.state(["alternate"] if masks and not every else ["!alternate"])
        self.mask_mixed = {}
        for key, var, tick in (("own", self.mask_own, self.mask_own_tick),
                               ("invert", self.mask_invert, self.mask_invert_tick)):
            values = {bool(m[key]) for m in masks}
            self.mask_mixed[key] = len(values) > 1
            var.set(values == {True})
            tick.state(["alternate"] if len(values) > 1 else ["!alternate"])
        same_or_blank(self.mask_size_entry, self.mask_size_var, [fmt(m["size"]) for m in masks])
        # Shows: closed shapes that aren't Fill / Spam only (open lines are always a band, Fill / Spam their inside)
        closed = [t for t in tgts if t.get("mask") and not is_solid(t) and loops_of(t)]
        shows = {t["mask"].get("show", "inside") for t in closed}
        if len(shows) == 1:
            self.mask_show_box.current(SHOWS.index(next(iter(shows))))
        show_mixed(self.mask_show_box, len(shows) > 1, self.mask_show_tip, tr("mask.show_tip"))
        self._loading = False
        if bool(closed) != bool(self.mask_show_row.winfo_manager()):
            if closed:
                self.mask_show_row.pack(anchor="w", pady=(1, 0), after=self.mask_own_tick)
            else:
                self.mask_show_row.pack_forget()
        # (Fill / Spam masks show their whole inside: no thickness, user)
        band = [t for t in tgts if t.get("mask") and not is_solid(t)]
        self.mask_size_entry.config(state="normal" if band else "disabled")
        self.mask_thick_tip.text = tr("mask.thickness_tip") + ("" if band else "\n\n" + tr("mask.thickness_solid"))
        if every != bool(self.mask_rows.winfo_manager()):
            if every:
                self.mask_rows.pack(anchor="w", padx=(20, 0), pady=(1, 0))
            else:
                self.mask_rows.pack_forget()
        # Mode: only while every picked mask is in a group; the first in its group always adds (greyed when only firsts)
        units = mask_units(self.shapes)
        picked = [unit_of(units, i) for i in self.mask_indices()]
        grouped = every and all(u and len(u) > 1 for u in picked)
        if grouped:
            later = [self.shapes[i]["mask"].get("mode", "add") for i, u in zip(self.mask_indices(), picked) if i != u[0]]
            modes = set(later) or {"add"}
            self._loading = True
            if len(modes) == 1:
                self.mask_mode_box.current(MODES.index(next(iter(modes))))
            show_mixed(self.mask_mode_box, len(modes) > 1, self.mask_mode_tip,
                       tr("mask.mode_tip") + ("" if later else "\n\n" + tr("mask.mode_first")))
            self._loading = False
            self.mask_mode_box.config(state="readonly" if later else "disabled")
        if grouped != bool(self.mask_mode_row.winfo_manager()):
            if grouped:
                self.mask_mode_row.pack(anchor="w", pady=(1, 0), before=self.mask_apply_button)
            else:
                self.mask_mode_row.pack_forget()

    def mask_indices(self):
        return [i for i in sorted(self.sels) if i < len(self.shapes) and is_mask(self.shapes[i])]

    def on_mask(self, what, left=None):
        """The Mask tick (what = "on"), a tick under it, Shows or the Thickness box. Only what was changed goes to each
        mask (the rest stays its own)."""
        if self._loading or left is not None and unchanged(left):
            return
        tgts = self.mask_targets()
        if not tgts:
            return
        if what == "on":
            on = self.mask_on.get() or bool(self.mask_tick.instate(["alternate"]))  # (half ticked: a click = all on)
            if all(bool(t.get("mask")) == on for t in tgts):
                return self.sync_mask()
            self.push_undo(name=tr("mask.undo_on" if on else "mask.undo_off"))
            for t in tgts:
                if on and not t.get("mask"):
                    t["mask"] = self.new_mask()
                elif not on:
                    t.pop("mask", None)
            return self.mask_done()
        masks = [t["mask"] for t in tgts if t.get("mask")]
        if not masks:
            return
        if what in ("own", "invert"):
            var = self.mask_own if what == "own" else self.mask_invert
            value = True if self.mask_mixed.get(what) else var.get()  # (half ticked: a click = all on)
            new = [dict(m, **{what: value}) for m in masks]
        elif what == "show":
            if self.mask_show_box.current() < 0:  # (still Mixed: nothing picked)
                return
            value = SHOWS[self.mask_show_box.current()]
            new = [dict(m, show=value) for m in masks]
        elif what == "mode":
            if self.mask_mode_box.current() < 0:
                return
            value = MODES[self.mask_mode_box.current()]
            units = mask_units(self.shapes)
            firsts = {id(self.shapes[u[0]]["mask"]) for u in units}  # (the first in a group always adds: left alone)
            new = [m if id(m) in firsts or "group" not in m else dict(m, mode=value) for m in masks]
        else:
            e, var = self.mask_size_entry, self.mask_size_var
            if not var.get().strip() and getattr(e, "blank_from", None):  # (Mixed, still empty)
                return
            try:
                v = float(calc(var.get()))
                ok = 0 <= v <= MOST_SIZE
            except ValueError:
                ok = False
            if not ok:
                return bad(e)
            good(e)
            new = [dict(m, size=v) for m in masks]
        if new == masks:
            return self.sync_mask()
        self.push_undo(name=tr("mask.undo_" + (what if what in ("own", "invert", "show", "mode") else "size")))
        for t, m in zip([t for t in tgts if t.get("mask")], new):
            t["mask"] = m
        # (the next mask drawn starts with this thickness; always sounding, not inverted: user)
        self.mask_defaults = dict(MASK_DEFAULTS, **{k: new[-1][k] for k in THICKNESS})
        self.mask_done()

    def apply_masks(self):
        """Apply mask (panel button / right-click): each selected mask's shown notes -> a NEW shape of plain notes,
        "Masked notes", just under the mask in the list (user: the shapes under it unchanged, the mask stays to be
        moved and applied again; its own sounding notes copied too). The notes as the preview shows them, velocities and
        channels as they sound (track = the channel each came out on). A mask in a group = the whole group, once,
        before its first member (user); holes over one shape the same (mask.worked_units). The new shapes are selected; one undo step."""
        self.commit_typing()
        masks = self.mask_indices()
        if not masks:
            return
        if self.notes_late:  # (notes still catching up with a drag: made now)
            self.shapes_changed(now=True)
        notes = unmasked(self.pattern, self.shapes)  # (as they sound without the masks' own notes)
        units, shown = worked_now(self.shapes, notes, self.ppq, self.keys, self.notes_of)
        made, count = {}, 0
        for unit in [u for u in units if set(u) & set(masks)]:
            i = unit[0]
            got = shown(unit)[1]
            if not len(got):  # (nothing shows through: no copy of the border alone)
                continue
            # its own sounding notes (the border, as its fill settings make them) come along: the copy = what you see
            # (user, 2026-10-12, after trying; silent masks have none)
            got = np.concatenate([got, self.rendered[np.isin(self.rendered[:, 5], unit)]])
            count += len(got)
            pics = np.array(["picture" in self.shapes[o] for o in got[:, 5].tolist()], bool)
            made[i] = [sh for sh in (self.copied(got[pics], True), self.copied(got[~pics], False)) if sh]
        if not made:
            self.status.config(text=tr("mask.apply_nothing"))
            return
        self.roll.cancel_draft()
        self.push_undo(name=tr("mask.apply_undo"))
        out, picked = [], []
        for i, sh in enumerate(self.shapes):
            if i in made:  # (before the mask: its dashed line and border notes stay on top)
                picked += range(len(out), len(out) + len(made[i]))
                out += made[i]
            out.append(sh)
        self.shapes[:] = out
        self.select_many(picked, picked[0])
        self.shapes_changed()
        self.status.config(text=tr("mask.apply_done", shapes=trn("n.shapes", len(picked), n=len(picked)),
                                   notes=trn("n.notes", count, n=count)))

    def track_slots_in(self, got):
        """For passengers.carried: shape o -> {its track: the slot its notes have in got}, by start + key."""
        def slots_of(o):
            mine = got[got[:, 5] == o]
            notes, tracks = self.notes_tracks(self.shapes[o])
            return track_map(notes, tracks if tracks is not None else np.zeros(len(notes), np.int64), mine)
        return slots_of

    def copied(self, got, picture, multi=None, slots_of=None):
        """Shown rows (rendered's columns) -> one "Masked notes" shape, or None. Each note keeps its colour: copied
        from a picture = its channel slot for good, painted in the picture's colours; in Multi channel its slot while
        it stays where it's made (on the shapes it came from, which would push it onto other channels), moved away =
        channels like any shape; in Single channel the shape it came from (Multi channel later: one colour each).
        Copied from a sustained shape: its overlaps kept (user: sounds as the preview). multi: as in Multi channel
        (None = the project's mode)."""
        if not len(got):
            return None
        if multi is None:
            multi = self.channel_mode.get() == "auto"
        owners = got[:, 5]
        track = got[:, 4] if picture or multi else np.unique(owners, return_inverse=True)[1].ravel()
        rows = np.column_stack([got[:, 0], got[:, 1] - got[:, 0], got[:, 2], got[:, 3], track])
        new = {**SHAPE_DEFAULTS, **self.defaults, **notes_shape(rows, self.ppq, tr("mask.applied")), "made": "mask"}
        new.pop("cycle", None)  # (Colours for new shapes: not on it, the notes keep the colours they had)
        new.pop("sustain", None)
        if picture or multi:  # (its slots keep the channels they have now, whatever comes in later: hunt)
            new["slots10"] = bool(self.picture_use10)
        if picture:
            new["own_slots"] = True
            first = self.shapes[int(owners[0])]
            new["picture_notes"] = {"pal": slot_pal(first["picture"]["set"].get("pal"), slot_rule(first),
                                                    self.picture_use10)}
        if any(is_sustained(self.shapes[o]) for o in np.unique(owners).tolist()):
            new["held"] = True
        sh = clean_shape(new)
        if multi and not picture:
            sh["own_slots"] = [float(v) for v in sh["pts"][0]]
            cc = carried(self.shapes, slots_of or self.track_slots_in(got), got, self.ppq)  # (pasted notes' CCs too)
            if cc:
                sh["cc"] = cc
        return sh

    def can_combine(self):
        """Right-click Combine masks: 2 or more masks selected, not all already one group."""
        masks = self.mask_indices()
        if len(masks) < 2:
            return False
        u = unit_of(mask_units(self.shapes), masks[0])
        return not set(masks) <= set(u)

    def can_separate(self):
        units = mask_units(self.shapes)
        return any(len(unit_of(units, i)) > 1 for i in self.mask_indices())

    def combine_masks(self):
        """Combine masks (right-click): the selected masks become one group, worked out together in the list's order,
        each by its Mode (user). Picked masks of one group already: the rest join it (its colour kept); else a new
        group, its line colour the first one no group it overlaps uses (user)."""
        if not self.can_combine():
            return
        self.commit_typing()
        masks = self.mask_indices()
        units = mask_units(self.shapes)
        old = {tuple(u) for u in (unit_of(units, i) for i in masks) if len(u) > 1}
        if len(old) == 1:
            m = self.shapes[next(iter(old))[0]]["mask"]
            gid, colour = m["group"], m.get("colour", 0)
        else:
            gid = new_group_id(self.shapes)
            colour = free_colour(self.shapes, masks, self.ppq, self.keys, self.notes_of)
        self.push_undo(name=tr("mask.combine_undo"))
        for i in masks:
            m = self.shapes[i]["mask"]
            self.shapes[i]["mask"] = dict(m, group=gid, colour=colour, mode=m.get("mode", "add") if
                                          m.get("group") == gid else "add")
        tidy_groups(self.shapes)
        self.mask_done()
        self.status.config(text=tr("mask.combine_done", n=len(unit_of(mask_units(self.shapes), masks[0]))))

    def separate_masks(self):
        """Separate masks (right-click): the selected masks leave their groups, each on its own again."""
        if not self.can_separate():
            return
        self.commit_typing()
        self.push_undo(name=tr("mask.separate_undo"))
        for i in self.mask_indices():
            self.shapes[i]["mask"] = {k: v for k, v in self.shapes[i]["mask"].items() if k not in GROUP_KEYS}
        tidy_groups(self.shapes)
        self.mask_done()
        self.status.config(text=tr("mask.separate_done"))

    def mask_done(self):
        self.shapes_changed()
        self.sync_title()
        self.sync_mask()
        self.schedule_autosave()
