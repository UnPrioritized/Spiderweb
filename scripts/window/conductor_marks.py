"""The Conductor pane's time + key signatures (user 2026-10-22; notes/meter.py keeps them in the tempo line).

Each one = a SHORT thin line from the pane's top edge with its text beside it ("3/4"; key signatures one row lower,
"Ab Maj"), no extra row. Right-click in the pane: "Add time signature here" / "Add key signature here" (separate
items and boxes, user: like Domino's separate windows), put on the bar line at or before the click; one there
already = that one is changed. Right-click on a mark: Change... / Remove (RIGHT-CLICK NEVER DELETES by itself, the
pane's rule). The first time signature (beat 0) can't be removed, only changed. Each change = one undo step."""

import bisect
import tkinter as tk
from tkinter import ttk

from files.lang import tr, trn
from notes.meter import BOTTOMS, TOPS, at_beat, bar_at, bar_line, key_name, on_bars, sig_text, sigs_of, where
from window import look
from window.widgets import Tooltip

MARK_LEN = 10  # px (x the window's scale): a mark's line down from the pane's top edge
ROW = 12  # px (x scale) between the time signature row and the key signature row
REACH = 5  # px (x scale) a right-click may be off a mark's line
REACH_DOWN = 6  # px (x scale) the key row's marks reach below their line


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
    body.ok_button = ttk.Button(btns, text=tr("conductor.ok"), command=ok)  # (a body may rename it)
    body.ok_button.pack(side="right", padx=(0, 6))
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
        hint = ttk.Label(f, text="", foreground=look.WARN)
        hint.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        def get():
            try:
                n = int(t.get().strip())
            except ValueError:
                n = None
            if n is not None and TOPS[0] <= n <= TOPS[1]:
                return n, int(b.get())
            # (says why OK did nothing, hunt 2026-10-23; the box back to its last good number, like number boxes)
            hint.config(text=tr("conductor.top_range", lo=TOPS[0], hi=TOPS[1]))
            t.set(str(top))
            e.focus_set()
            e.selection_range(0, "end")
            return None
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


def ask_rebar(parent, sigs, lo, hi, bpm_at, why_of, forecast_of=None, test_of=None, dense=True):
    """Change visual time signature's box (user 2026-11-12): the new signature for the bars lo..hi grow to, with
    which bars and what the tempo becomes shown as it's typed. bpm_at(beat) = the tempo there; why_of(plan) = a
    refusal text or None. forecast_of(plan) = {reason: shapes} that will become plain notes (cheap, shown as typed),
    test_of(plan) = the same found by a quiet test run on OK (more = the box stays open, OK becomes "Change anyway";
    user 2026-11-12) or a refusal text. dense: "Short BPM changes for exact timing" ticked to start with (shown only
    when the change brings notes under a tick apart, forecast_of's "dense"). The plan (rebar.plan, + "dense": the
    tick) to do, or None (cancelled)."""
    from notes import rebar as R
    now = at_beat(sigs, bar_line(sigs, lo))

    def body(f):
        t, b = tk.StringVar(value=str(now[1])), tk.StringVar(value=str(now[2]))
        ttk.Label(f, text=tr("conductor.rebar_sig")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        e = ttk.Spinbox(f, from_=TOPS[0], to=TOPS[1], textvariable=t, width=5)
        e.grid(row=1, column=0)
        ttk.Label(f, text="/").grid(row=1, column=1, padx=4)
        ttk.Combobox(f, values=[str(v) for v in BOTTOMS], textvariable=b, state="readonly", width=5).grid(row=1,
                                                                                                          column=2)
        where = ttk.Label(f, text="")
        where.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        bpm = ttk.Label(f, text="")
        bpm.grid(row=3, column=0, columnspan=3, sticky="w")
        plain = ttk.Label(f, text="", foreground=look.WARN, wraplength=300 * look.WIDE, justify="left")
        plain.grid(row=4, column=0, columnspan=3, sticky="w")
        close = ttk.Label(f, text="", foreground=look.WARN, wraplength=300 * look.WIDE, justify="left")
        keep = tk.BooleanVar(value=dense)  # (user 2026-11-12: under the warning line, "these" = those notes)
        tick = ttk.Checkbutton(f, text=tr("conductor.rebar_dense_tick"), variable=keep)
        Tooltip(tick, tr("conductor.rebar_dense_tip"))
        ttk.Label(f, text=tr("conductor.rebar_tip"), foreground=look.HINT, wraplength=300 * look.WIDE,
                  justify="left").grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))
        hint = ttk.Label(f, text="", foreground=look.WARN, wraplength=300 * look.WIDE, justify="left")
        hint.grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))
        state = {"anyway": None}  # (the signature "Change anyway" stands for)

        def say(counts, sig=None):
            counts = dict(counts)
            near = counts.pop("dense", 0)
            most = counts.pop("dense_most", None)
            n = sum(counts.values())
            whys = ", ".join(trn("conductor.rebar_plain_" + k, v, n=v) for k, v in counts.items() if v)
            plain.config(text=trn("conductor.rebar_will", n, n=n, why=whys) if n else "")
            if near and most is not None:  # (user 2026-11-12: most notes under a tick apart = short BPM changes
                # everywhere would undo the change: say what PPQ it needs, no tick box; OK rounds them)
                s = sig_text(sig) if sig else ""
                close.config(text=tr("conductor.rebar_dense_most_2", sig=s) if most == 2 else
                             tr("conductor.rebar_dense_most", sig=s, k=most) if most else
                             tr("conductor.rebar_dense_most_far", sig=s))
                close.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
                tick.grid_remove()
            elif near:  # (shown only when some notes would come under a tick apart)
                close.config(text=trn("conductor.rebar_dense", near, n=near))
                close.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
                tick.grid(row=6, column=0, columnspan=3, sticky="w")
            else:
                close.grid_remove()
                tick.grid_remove()

        def ok_text(anyway):
            state["anyway"] = anyway
            button = getattr(f, "ok_button", None)
            if button is not None:
                button.config(text=tr("conductor.rebar_anyway" if anyway else "conductor.ok"))

        def top():
            try:
                n = int(t.get().strip())
            except ValueError:
                return None
            return n if TOPS[0] <= n <= TOPS[1] else None

        def plan():
            n = top()
            return None if n is None else R.plan(sigs, lo, hi, n, int(b.get()))

        def show(*_):
            pl = plan()
            if pl is None:
                return
            first, last = pl["bars"][0] + 1, pl["bars"][1] + 1
            where.config(text=trn("conductor.rebar_bars", pl["count"], first=first, last=last))
            fs = [r[4] for r in pl["runs"]]
            if len(fs) == 1:
                old = bpm_at(pl["a"])
                bpm.config(text=tr("conductor.rebar_bpm", old=f"{old:g}", new=f"{old * fs[0]:.6g}"))
            else:
                bpm.config(text=tr("conductor.rebar_times", times=", ".join(f"{f:.4g}" for f in fs)))
            hint.config(text="")
            got = forecast_of(pl) if forecast_of else {}
            say({} if "why" in got else got, pl["sig"])
            if state["anyway"] is not None and state["anyway"] != (pl["a"], pl["b"], tuple(pl["sig"])):
                ok_text(None)
        t.trace_add("write", show)
        b.trace_add("write", show)
        show()
        e.focus_set()
        e.selection_range(0, "end")

        def get():
            pl = plan()
            if pl is None:
                hint.config(text=tr("conductor.top_range", lo=TOPS[0], hi=TOPS[1]))
                t.set(str(now[1]))
                e.focus_set()
                e.selection_range(0, "end")
                return None
            if all(abs(r[4] - 1) < 1e-12 for r in pl["runs"]) and all(
                    at_beat(sigs, s)[1:] == pl["sig"] for s, _ in R.old_bars(sigs, pl["a"], pl["b"])):
                hint.config(text=tr("conductor.rebar_same"))
                return None
            why = why_of(pl)
            if why:
                hint.config(text=why)
                return None
            fore = (forecast_of(pl) or {}) if forecast_of else {}
            fore = {} if "why" in fore else fore
            pl["dense"] = bool(keep.get()) and "dense_most" not in fore  # (most notes: rounded, tick not shown)
            pl["dense_most"] = "dense_most" in fore
            key = (pl["a"], pl["b"], tuple(pl["sig"]))
            if test_of and state["anyway"] != key:
                ahead = {k: v for k, v in fore.items() if k not in ("dense", "dense_most")}
                got = test_of(pl)
                if isinstance(got, str):  # (refused: a mask over the edge...)
                    hint.config(text=got)
                    return None
                if sum(got.values()) > sum(ahead.values()):
                    say(dict(got, **{k: v for k, v in fore.items() if k in ("dense", "dense_most")}), pl["sig"])
                    hint.config(text=tr("conductor.rebar_found_more"))
                    ok_text(key)
                    return None
            return pl
        return get
    return _dialog(parent, tr("conductor.rebar_title"), body)


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
        """The signature mark (its list, row) the mouse at x, y is on, or None: its line or its text, the key row
        reaching a little below its text (user 2026-10-26: easier to hit)."""
        s, roll = self.scale, self.app.roll
        row = min(1, int(y // (ROW * s)))  # (the key row reaches on below its own ROW)
        if y < 0 or y > row * ROW * s + MARK_LEN * s + (2 if row == 0 else REACH_DOWN) * s:
            return None
        font = look.font(7, "bold")
        got = [(mx, m, r) for mx, r, text, m in ((roll.t2x(b), r, text, m) for b, r, text, m in
                                                  self.sig_marks() if r == row)
               if -REACH * s <= x - mx <= 300 * s  # (its text measured only near it: every mouse move asks)
               and x - mx <= 5 * s + self.tk.call("font", "measure", font, text)]
        return max(got, key=lambda g: g[0])[1:] if got else None  # (the last one starting before the mouse)

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
                menu.add_command(label=tr("conductor.change_key"), command=lambda: self.edit_key_sig(mark[0], mark))
                menu.add_command(label=tr("conductor.remove_key"), command=lambda: self.remove_sig(mark, 1))
            return True
        menu.add_command(label=tr("conductor.add_time"), command=lambda: self.edit_time_sig(max(0.0, beat)))
        menu.add_command(label=tr("conductor.add_key"), command=lambda: self.edit_key_sig(max(0.0, beat)))
        return False

    def rebar_item(self, menu):
        """The right-click menu's "Change visual time signature…" (inside a kept Select box; user 2026-11-12: only
        from the Conductor pane's box). Greyed with a loaded MIDI holding notes after the box's start; the status
        line says why while it's pointed at."""
        lo, hi = self.bands_reach()
        lm = self.app.loaded
        busy = lm is not None and lm.notes and lm.end / lm.ppq > bar_line(sigs_of(self.app.tempo), lo) + 1e-9
        menu.add_command(label=tr("conductor.rebar"), command=lambda: self.rebar(lo, hi),
                         state="disabled" if busy else "normal")
        if busy:
            k = menu.index("end")

            def said(e):
                try:
                    on = menu.index("active") == k
                except tk.TclError:
                    on = False
                if on:
                    self.app.status.config(text=tr("conductor.rebar_loaded"))
            menu.bind("<<MenuSelect>>", said, add="+")

    def rebar_why(self, got):
        """A refusal ({"why", "at"}) as the text the box / status line shows."""
        bar = where(sigs_of(self.app.tempo), max(0.0, got["at"]))[0] + 1
        return tr("conductor.rebar_why_" + got["why"], bar=bar)

    def rebar(self, lo, hi):
        """Change visual time signature on the bars lo..hi grow to (asked in a small box; one undo step)."""
        from notes import rebar as R
        from notes.tempo import drawn_bpm
        from window import rebar_work
        tempo, base = self.app.tempo, self.base()

        def why_of(pl):
            got = R.tempo_of(tempo, base, pl)
            return self.rebar_why(got) if "why" in got else None
        seen = {}

        def forecast_of(pl):  # (shapes sorted once per stretch + factors: typing the same again is free)
            key = (pl["a"], pl["b"], tuple((r[0], r[1], r[4]) for r in pl["runs"]))  # (factors too: notes under a tick)
            if key not in seen:
                seen[key] = rebar_work.forecast(self.app, pl)
            return seen[key]

        def test_of(pl):
            got = rebar_work.apply(self.app, pl, tr("conductor.step_rebar"), dry=True, dense=pl.get("dense", True))
            return self.rebar_why(got) if "why" in got else got["whys"]
        pl = ask_rebar(self, sigs_of(tempo), lo, hi, lambda b: round(float(drawn_bpm(tempo, base, [b])[0]), 3),
                       why_of, forecast_of, test_of, getattr(self, "rebar_dense", True))
        if pl is None:
            return
        if not pl.get("dense_most"):  # (the tick hidden for "most notes": its choice kept as it was)
            self.rebar_dense = pl.get("dense", True)  # (the tick remembered: saved with the window, project.py)
            self.app.schedule_autosave()
        got = rebar_work.apply(self.app, pl, tr("conductor.step_rebar"), dense=pl.get("dense", True))
        if "why" in got:
            self.app.status.config(text=self.rebar_why(got))
            return
        self.bands = []
        first, last = pl["bars"][0] + 1, pl["bars"][1] + 1
        text = tr("conductor.rebar_done", bars=trn("conductor.rebar_bars", pl["count"], first=first, last=last),
                  sig=sig_text(pl["sig"]))
        n = sum(got["whys"].values())
        if n:
            text += " " + trn("conductor.rebar_plain", n, n=n)
        self.app.status.config(text=text)
        self.request_redraw()
        self.app.tips.show("rebar", wait=True)  # (the first time: what it did)

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
        # (other marks always stay, a repeat of the one before too: changing bar 1 there and back deleted the later
        # ones, hunt 2026-10-23)
        tempo["sigs"] = on_bars(sigs)  # (later ones now mid-bar: back to their bar's start, like Domino)
        self.keys_on_bars()
        self.app.meter_changed()

    def edit_key_sig(self, beat, mark=None):
        """Add / change the key signature on the bar line at or before beat. mark: the one clicked (Change...): that
        one is changed, moved onto its bar line (hunt 2026-10-23: one left mid-bar got a second mark instead)."""
        tempo = self.app.tempo
        at = bar_line(sigs_of(tempo), beat)
        now = mark if mark is not None else at_beat(tempo.get("keysigs") or [], at)
        got = ask_key_sig(self, now[1] if now else 0, now[2] if now else False)
        if got is not None:
            self.set_key_sig(at, *got, was=mark)

    def set_key_sig(self, at, sharps, minor, was=None):
        tempo = self.app.tempo
        keys = [list(k) for k in tempo.get("keysigs") or () if k is not was]
        same = [k for k in keys if abs(k[0] - at) < 1e-9]
        if (same and same[0][1:] == [sharps, minor] or not same and was is not None and
                abs(was[0] - at) < 1e-9 and was[1:] == [sharps, minor]):
            return
        self.app.push_undo(name=tr("conductor.step_key"))
        if same:
            same[0][1:] = [sharps, minor]
        else:
            keys.append([round(at, 9), sharps, minor])
        tempo["keysigs"] = sorted(keys, key=lambda k: k[0])
        self.app.meter_changed()

    def put_domino_sigs(self, sigs, keys, at, length, how, backs=None):
        """Paste from Domino's time / key signatures ([(beat from the paste's start, top, bottom)], [(beat, sharps,
        minor)]) from beat at, in the same undo step as the paste. Each goes on the project's BAR LINE at or before
        its spot (hunt 2026-10-23: one at a mid-bar play line cut the bar before short; Domino only has them on bar
        lines), the time signatures one by one (each moves the bar lines after it). "replace": the project's inside
        the pasted stretch go, and what was in effect at its end comes back on the next bar line when it differs
        (Domino ADDS a pasted Conductor's items, so this is what the song looks like after); "add": among them (one
        on the same beat: replaced). The first time signature stays on beat 0. How many were put. backs: {key: the
        mark to come back at the end, or None for none} in place of the one in effect there now (a stretch moved
        in the track list: worked out before its old place was emptied, track_tempo.move)."""
        tempo = self.app.tempo
        end = at + length
        cur = [list(m) for m in sigs_of(tempo)]
        n = 0
        for key, new in (("sigs", sigs), ("keysigs", keys)):
            if not new:
                continue
            old = [list(m) for m in cur] if key == "sigs" else [list(m) for m in tempo.get(key) or []]
            back = backs[key] if backs is not None else at_beat(old, end)
            if how == "replace":
                old = [m for m in old if not at - 1e-9 <= m[0] <= end + 1e-9 or key == "sigs" and m[0] == 0]
            # (one pass, each mark found by halving: a list made again per mark froze thousands, hunt 2026-11-05)
            cur_starts, starts = [m[0] for m in cur], [m[0] for m in old]
            for b, *rest in sorted(new, key=lambda m: m[0]):
                pos = round(bar_at(cur, cur_starts, at + b), 9)
                j = bisect.bisect_left(starts, pos - 1e-9)
                if j < len(old) and abs(old[j][0] - pos) < 1e-9:
                    old[j] = [pos, *rest]
                    starts[j] = pos
                elif j and old[j - 1][1:] == rest:  # (a repeat of the one before: not added)
                    continue
                else:
                    old.insert(j, [pos, *rest])
                    starts.insert(j, pos)
                if key == "sigs":
                    cur, cur_starts = old, starts
            if how == "replace" and back is not None and not any(abs(m[0] - end) < 1e-9 for m in old) \
                    and (at_beat(old, end) or [None])[1:] != back[1:]:
                old = sorted(old + [[round(end, 9), *back[1:]]], key=lambda m: m[0])
            if key == "sigs":  # (mid-bar = back to its bar's start, Domino's rule)
                old = on_bars(old)
                cur = old
            tempo[key] = old
            n += len(new)
        if n:
            self.keys_on_bars()
            self.app.meter_changed()
        return n

    def keys_on_bars(self):
        """Every key signature onto the bar line at or before it (after the time signatures changed: user 2026-10-23,
        never one left mid-bar; files put it on its bar's start anyway). Two on one bar line: the later one."""
        tempo = self.app.tempo
        keys = tempo.get("keysigs")
        if not keys:
            return
        sigs = sigs_of(tempo)
        starts = [m[0] for m in sigs]  # (bar_at: no bar list made per key, hunt 2026-11-05)
        on = {}
        for k in keys:
            on[round(bar_at(sigs, starts, k[0]), 9)] = k[1:]
        tempo["keysigs"] = [[b, *rest] for b, rest in sorted(on.items())]

    def remove_sig(self, mark, row):
        tempo = self.app.tempo
        key = "sigs" if row == 0 else "keysigs"
        marks = tempo.get(key) or []
        if not any(m is mark for m in marks) or row == 0 and mark[0] == 0:
            return
        self.app.push_undo(name=tr("conductor.step_remove_time" if row == 0 else "conductor.step_remove_key"))
        tempo[key] = [m for m in marks if m is not mark]
        if row == 0:  # (later ones now mid-bar back to their bar's start, like Domino; key signatures too)
            tempo[key] = on_bars(tempo[key])
            self.keys_on_bars()
        self.app.meter_changed()
