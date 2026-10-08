"""The question before something big: lots of notes, or more memory than this PC has free (user, 2026-10-04).
Yes / No + "Don't ask again until Spiderweb is closed", remembered PER ACTION (App.big_skip, forgotten at a
restart). Something that won't fit in the free memory at all is always asked, ticked or not.
Actions: "notes" = making a shape with more than BIG notes (App.confirm_big, Range window), "midi" = Generate
MIDI (memory only), "domino" = Copy to Domino (more than BIG notes: the program it's pasted into gets slow).
The same window warns before Copy to Domino fills a 10th track (ask_drums, "drums") and before an image with more
than a million grid cells is made (image_window.big_ok, "image")."""

import tkinter as tk
from tkinter import ttk

from files.lang import tr
from files.system import free_memory

BIG = 1_000_000  # notes: asked past this (making shapes, Copy to Domino)
# memory each note takes while it's done (measured: 20 M notes in the window ~6 GB in all, saving a MIDI file
# 1.4 GB more, copying 4 M notes 0.57 GB)
PER_NOTE = {"notes": 300, "midi": 70, "domino": 150}
DRUM_TRACK = 10  # Copy to Domino: asked when the copy fills this many tracks (ask_drums)


def gb(n):
    return f"{n / 1e9:.1f}"


def trouble(app, action, notes):
    """(lines saying what's big, strong) for the question; no lines = nothing to ask (or not asked again)."""
    need, free = notes * PER_NOTE[action], free_memory()
    lines, strong = [], False
    if action in ("notes", "domino") and notes > BIG:
        lines.append(tr(f"big_ask.{action}", n=notes))
    if free is not None and need > free:
        strong = True
        lines.append(tr("big_ask.wont_fit", need=gb(need), free=gb(free)))
    elif free is not None and need > free / 2:
        lines.append(tr("big_ask.memory", need=gb(need), free=gb(free)))
    return ([] if action in app.big_skip and not strong else lines), strong


def ask(app, action, notes, parent=None):
    """True = go ahead. notes = how many notes the action makes / handles. Asked when there are more than BIG
    notes (not "midi") or the memory needed is more than half of what's free."""
    lines, strong = trouble(app, action, notes)
    if not lines:
        return True
    go, skip = dialog(parent or app, tr("big_ask.title"), "\n\n".join(lines + [tr("big_ask.go")]), strong)
    if go and skip:
        app.big_skip.add(action)
    return go


def ask_drums(app, tracks):
    """Copy to Domino with DRUM_TRACK tracks or more (user, 2026-10-04): they fill the highlighted track and the
    ones below it, so the 10th may be one on channel 10, which most players play as drums (Generate MIDI skips that
    channel, but which track the user pastes into is up to them). True = go ahead."""
    if tracks < DRUM_TRACK or "drums" in app.big_skip:
        return True
    go, skip = dialog(app, tr("big_ask.title"), tr("big_ask.drums", tracks=tracks), False)
    if go and skip:
        app.big_skip.add("drums")
    return go


def dialog(parent, title, text, strong):
    """(Yes pressed, tick box ticked). strong = No is the default button (it may not fit in memory)."""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.resizable(False, False)
    win.transient(parent.winfo_toplevel())
    body = ttk.Frame(win, padding=(14, 14, 14, 8))
    body.pack(fill="both", expand=True)
    ttk.Label(body, image="::tk::icons::warning").grid(row=0, column=0, sticky="n", padx=(0, 12))
    ttk.Label(body, text=text, wraplength=420, justify="left").grid(row=0, column=1, sticky="w")
    skip = tk.BooleanVar(win, value=False)
    ttk.Checkbutton(body, text=tr("big_ask.dont_ask"), variable=skip).grid(row=1, column=1, sticky="w", pady=(12, 0))
    btns = ttk.Frame(win, padding=(14, 0, 14, 12))
    btns.pack(fill="x")
    answer = [False]

    def close(go):
        answer[0] = go
        win.destroy()
    no = ttk.Button(btns, text=tr("big_ask.no"), command=lambda: close(False))
    no.pack(side="right")
    yes = ttk.Button(btns, text=tr("big_ask.yes"), command=lambda: close(True))
    yes.pack(side="right", padx=(0, 6))
    first = no if strong else yes
    win.bind("<Return>", lambda e: close(first is yes))
    win.bind("<Escape>", lambda e: close(False))
    win.protocol("WM_DELETE_WINDOW", lambda: close(False))
    win.update_idletasks()
    top = parent.winfo_toplevel()
    x = top.winfo_rootx() + (top.winfo_width() - win.winfo_reqwidth()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    from window.widgets import remember_place
    remember_place(win, "big_ask")
    win.deiconify()
    win.grab_set()
    first.focus_set()
    win.bell()
    win.wait_window()
    return answer[0], skip.get()
