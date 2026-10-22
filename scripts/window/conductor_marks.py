"""The Conductor pane's time + key signatures (user 2026-10-22; notes/meter.py keeps them in the tempo line).

Each one = a SHORT thin line from the pane's top edge with its text beside it ("3/4"; key signatures one row lower,
"Ab Maj"), no extra row. Right-click in the pane: "Add time signature here" / "Add key signature here" (separate
items and boxes, user: like Domino's separate windows), put on the bar line at or before the click; one there
already = that one is changed. Right-click on a mark: Change... / Remove (RIGHT-CLICK NEVER DELETES by itself, the
pane's rule). The first time signature (beat 0) can't be removed, only changed. Each change = one undo step."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr, trn
from notes.meter import BOTTOMS, TOPS, at_beat, bar_line, key_name, sig_text, sigs_of
from window import look

MARK_LEN = 10  # px (x the window's scale): a mark's line down from the pane's top edge
ROW = 12  # px (x scale) between the time signature row and the key signature row
REACH = 5  # px (x scale) a right-click may be off a mark's line


def _dialog(parent, title, body_fn):
    """A small modal box: body_fn(frame) -> a function giving the answer (None = not good); OK / Cancel. The
    answer, or None when cancelled."""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.resizable(False, False)
    win.transient(parent.winfo_toplevel())
    body = ttk.Frame(win, padding=(14, 12, 14, 6))
    body.pack(fill="both", expand=True)
    get = body_fn(body)
    btns = ttk.Frame(win, padding=(14, 4, 14, 12))
    btns.pack(fill="x")
    answer = [None]

    def ok():
        got = get()
        if got is not None:
            answer[0] = got
            win.destroy()

    ttk.Button(btns, text=tr("conductor.cancel"), command=win.destroy).pack(side="right")
    ttk.Button(btns, text=tr("conductor.ok"), command=ok).pack(side="right", padx=(0, 6))
    win.bind("<Return>", lambda e: ok())
    win.bind("<Escape>", lambda e: win.destroy())
    win.update_idletasks()
    top = parent.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - win.winfo_reqwidth()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.deiconify()
    win.grab_set()
    win.wait_window()
    return answer[0]


def ask_time_sig(parent, top, bottom):
    """(top, bottom) typed in a small box, or None (cancelled)."""
    def body(f):
        t, b = tk.StringVar(value=str(top)), tk.StringVar(value=str(bottom))
        ttk.Label(f, text=tr("conductor.time_sig")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        e = ttk.Spinbox(f, from_=TOPS[0], to=TOPS[1], textvariable=t, width=5)
        e.grid(row=1, column=0)
        ttk.Label(f, text="/").grid(row=1, column=1, padx=4)
        ttk.Combobox(f, values=[str(v) for v in BOTTOMS], textvariable=b, state="readonly", width=5).grid(row=1,
                                                                                                          column=2)
        e.focus_set()
        e.selection_range(0, "end")

        def get():
            try:
                n = int(t.get().strip())
            except ValueError:
                return None
            return (n, int(b.get())) if TOPS[0] <= n <= TOPS[1] else None
        return get
    return _dialog(parent, tr("conductor.time_sig_title"), body)


def ask_key_sig(parent, sharps, minor):
    """(sharps -7..7, minor) picked in a small box, or None (cancelled)."""
    def body(f):
        ways = list(range(-7, 8))
        names = [key_name(s, False) + "  /  " + key_name(s, True) + "  (" + _accidentals(s) + ")" for s in ways]
        pick = tk.StringVar(value=names[ways.index(sharps)])
        mode = tk.BooleanVar(value=minor)
        ttk.Label(f, text=tr("conductor.key_sig")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Combobox(f, values=names, textvariable=pick, state="readonly", width=28).grid(row=1, column=0,
                                                                                          columnspan=2, sticky="w")
        ttk.Radiobutton(f, text=tr("conductor.major"), variable=mode, value=False).grid(row=2, column=0, sticky="w",
                                                                                         pady=(6, 0))
        ttk.Radiobutton(f, text=tr("conductor.minor"), variable=mode, value=True).grid(row=2, column=1, sticky="w",
                                                                                        pady=(6, 0))
        return lambda: (ways[names.index(pick.get())], bool(mode.get()))
    return _dialog(parent, tr("conductor.key_sig_title"), body)


def _accidentals(s):
    return tr("conductor.no_accidentals") if s == 0 else (trn("conductor.sharps", s, n=s) if s > 0
                                                          else trn("conductor.flats", -s, n=-s))


class ConductorMarks:
    """Mixed into TempoPane."""

    def sig_marks(self):
        """[(beat, row 0 = time / 1 = key, text, the mark's list)] of every signature."""
        tempo = self.app.tempo
        out = [(s[0], 0, sig_text(s), s) for s in sigs_of(tempo)]
        out += [(k[0], 1, key_name(k[1], k[2]), k) for k in tempo.get("keysigs") or ()]
        return out

    def draw_sig_marks(self):
        roll, s = self.app.roll, self.scale
        kb, w = roll.kb_w, self.winfo_width()
        font = look.font(7, "bold")
        for beat, row, text, _ in self.sig_marks():
            x = roll.t2x(beat)
            if x < kb - 1 or x > w:
                continue
            y0 = row * ROW * s
            self.create_line(x, y0, x, y0 + MARK_LEN * s, fill=look.SIG_MARK, width=max(1, round(s)))
            self.create_text(x + 3 * s, y0 + MARK_LEN * s / 2, text=text, anchor="w", fill=look.SIG_MARK, font=font)

    def sig_mark_at(self, x, y):
        """The signature mark (its list, row) a right-click at x, y is on, or None."""
        s, roll = self.scale, self.app.roll
        row = int(y // (ROW * s))
        if row > 1 or y > row * ROW * s + MARK_LEN * s + 2 * s:
            return None
        got = [(abs(roll.t2x(b) - x), m, r) for b, r, _, m in self.sig_marks() if r == row
               and -REACH * s <= x - roll.t2x(b) <= 40 * s]  # (on its line or its text)
        return min(got, key=lambda g: g[0])[1:] if got else None

    def sig_menu_items(self, menu, e):
        """The right-click menu's signature items: a mark's Change / Remove, else Add ... here."""
        got = self.sig_mark_at(e.x, e.y)
        beat = self.app.roll.x2t(e.x)
        if got is not None:
            mark, row = got
            if row == 0:
                menu.add_command(label=tr("conductor.change_time"), command=lambda: self.edit_time_sig(mark[0]))
                if mark[0] > 0:
                    menu.add_command(label=tr("conductor.remove_time"), command=lambda: self.remove_sig(mark, 0))
            else:
                menu.add_command(label=tr("conductor.change_key"), command=lambda: self.edit_key_sig(mark[0]))
                menu.add_command(label=tr("conductor.remove_key"), command=lambda: self.remove_sig(mark, 1))
            return True
        menu.add_command(label=tr("conductor.add_time"), command=lambda: self.edit_time_sig(max(0.0, beat)))
        menu.add_command(label=tr("conductor.add_key"), command=lambda: self.edit_key_sig(max(0.0, beat)))
        return False

    def edit_time_sig(self, beat):
        """Add / change the time signature on the bar line at or before beat (asked in a small box)."""
        sigs = sigs_of(self.app.tempo)
        at = bar_line(sigs, beat)
        now = at_beat(sigs, at)
        got = ask_time_sig(self, now[1], now[2])
        if got is not None:
            self.set_time_sig(at, *got)

    def set_time_sig(self, at, top, bottom):
        """The time signature from beat at (one undo step; the same as the one before = that one, nothing)."""
        tempo = self.app.tempo
        sigs = [list(s) for s in sigs_of(tempo)]
        same = [s for s in sigs if abs(s[0] - at) < 1e-9]
        before = at_beat([s for s in sigs if s[0] < at - 1e-9], at)
        if same and same[0][1:] == [top, bottom] or not same and before and before[1:] == [top, bottom]:
            return
        self.app.push_undo(name=tr("conductor.step_time"))
        if same:
            same[0][1:] = [top, bottom]
        else:
            sigs.append([round(at, 9), top, bottom])
        sigs.sort(key=lambda s: s[0])
        tempo["sigs"] = [s for i, s in enumerate(sigs) if i == 0 or s[1:] != sigs[i - 1][1:]]
        self.app.meter_changed()

    def edit_key_sig(self, beat):
        """Add / change the key signature on the bar line at or before beat."""
        tempo = self.app.tempo
        at = bar_line(sigs_of(tempo), beat)
        now = at_beat(tempo.get("keysigs") or [], at)
        got = ask_key_sig(self, now[1] if now else 0, now[2] if now else False)
        if got is not None:
            self.set_key_sig(at, *got)

    def set_key_sig(self, at, sharps, minor):
        tempo = self.app.tempo
        keys = [list(k) for k in tempo.get("keysigs") or ()]
        same = [k for k in keys if abs(k[0] - at) < 1e-9]
        if same and same[0][1:] == [sharps, minor]:
            return
        self.app.push_undo(name=tr("conductor.step_key"))
        if same:
            same[0][1:] = [sharps, minor]
        else:
            keys.append([round(at, 9), sharps, minor])
        tempo["keysigs"] = sorted(keys, key=lambda k: k[0])
        self.app.meter_changed()

    def put_domino_sigs(self, sigs, keys, at, length, how):
        """Paste from Domino's time / key signatures ([(beat from the paste's start, top, bottom)], [(beat, sharps,
        minor)]) at beat at, in the same undo step as the paste. "replace": the project's inside the pasted stretch
        go, and what was in effect at its end comes back there when it differs (Domino ADDS a pasted Conductor's
        items, so this is what the song looks like after); "add": among them (one on the same beat: replaced).
        The first time signature stays on beat 0. How many were put."""
        tempo = self.app.tempo
        end = at + length
        n = 0
        for key, new in (("sigs", sigs), ("keysigs", keys)):
            if not new:
                continue
            old = [list(m) for m in (sigs_of(tempo) if key == "sigs" else tempo.get(key) or [])]
            put = [[round(at + b, 9), *rest] for b, *rest in new]
            if how == "replace":
                back = at_beat(old, end)
                old = [m for m in old if not at - 1e-9 <= m[0] <= end + 1e-9 or key == "sigs" and m[0] == 0]
                if back is not None and not any(abs(m[0] - end) < 1e-9 for m in put):
                    old.append([round(end, 9), *back[1:]])
            beats = {round(m[0], 9) for m in put}
            got = sorted([m for m in old if round(m[0], 9) not in beats] + put, key=lambda m: m[0])
            if key == "sigs":
                if got[0][0] > 0:  # (pasted later than beat 0: the first stays)
                    got.insert(0, list(sigs_of(tempo)[0]))
                got = [m for i, m in enumerate(got) if i == 0 or m[1:] != got[i - 1][1:]]
            else:
                got = [m for i, m in enumerate(got) if i == 0 or m[1:] != got[i - 1][1:]]
            tempo[key] = got
            n += len(new)
        if n:
            self.app.meter_changed()
        return n

    def remove_sig(self, mark, row):
        tempo = self.app.tempo
        key = "sigs" if row == 0 else "keysigs"
        marks = tempo.get(key) or []
        if not any(m is mark for m in marks) or row == 0 and mark[0] == 0:
            return
        self.app.push_undo(name=tr("conductor.step_remove_time" if row == 0 else "conductor.step_remove_key"))
        tempo[key] = [m for m in marks if m is not mark]
        if row == 0:  # (the same on both sides now: one)
            sigs = tempo["sigs"]
            tempo["sigs"] = [s for i, s in enumerate(sigs) if i == 0 or s[1:] != sigs[i - 1][1:]]
        self.app.meter_changed()
