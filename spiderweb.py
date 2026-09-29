"""
Spiderweb
Draw lines, polylines, freehand strokes, curves, arcs and text on a piano roll and turn them into MIDI notes
(the black MIDI "spiderweb" technique).

Files (the scripts live in scripts/, one subfolder per group):
  window/            the main window and its side panel
    app.py           main window: toolbar, side panel, shapes, playback, undo
    panel_custom.py  the panel's custom shape settings, opening the drawer
    panel_funnel.py  the panel's funnel settings
    panel_tumour.py  the panel's tumour line (summary + button)
    panel_pattern.py the panel's pattern numbers (a formula along a curve) + the Formula menu's actions
    tumour_window.py the tumour window: the tumour settings (bumps along lines)
    history.py       the History list (named undo steps; docked in the side panel or its own window)
    graph_window.py  a tumour setting's graph (the number changing along the line)
    join_split.py    Join (shapes -> one curve) and Split (cut in two, pieces -> separate shapes)
    panel_freehand.py  the panel's Straighten setting for freehand strokes
    panel_text.py    the panel's text settings (font, size, spacing, threshold, grow)
    font_dialog.py   the font window (type a font's name or pick one)
    widgets.py       tooltips
    snap_picker.py   the Snap dropdown (note pictures) and the Customised snap window
    help_texts.py    every help text: tips, the Help window, tool tooltips, the side panel's help
    help.py          the tip popups and the Help window (F1, searchable)
    curve_dialog.py  the custom curve formula window, saved formulas (curves.json)
    drawer.py        the custom shape drawer window and the shape library (shapes/*.json)
    velocity.py      the velocity pane under the piano roll
  roll/              the piano roll
    pianoroll.py     the piano roll canvas: view, hit testing, mouse editing
    roll_draw.py     painting the grid, notes, handles, keyboard, ruler
    roll_curve.py    editing curves (anchors + handles, symmetric halves)
    roll_custom.py   the selected custom shape's box (resize, turn, skew)
    roll_funnel.py   editing funnels (curve starts, bends, extra lines, highlighted lines / curves)
    roll_live.py     live drawing (strokes straight into one custom shape), picking / editing its strokes
    roll_menu.py     the right-click menu
    roll_text.py     the Text tool: typing on the roll, the caret
    roll_shared.py   colours, key names, modifier keys, outline caches
  notes/             shapes -> notes
    engine.py        shapes -> notes, overlap handling, channel assignment
    paths.py         lines / polylines / freehand / curves -> notes
    custom.py        custom shapes: outlines, fill, spam, outline spam
    text.py          text: letters laid out in a font -> a custom shape, threshold, grow
    fonts.py         letter outlines from the fonts installed in Windows
    funnel.py        funnels: curves, note grid, gates
    bezier.py        Bezier curves (anchors + handles) for curves and funnel curves, symmetry, fitting to points
    arc.py           arcs: pieces of a perfect circle through three points
    tumour.py        tumours: bumps along lines
    pattern.py       patterns along curves: a formula (wave, zigzag...) laid along the curve
    joined.py        joining shapes into one curve (pieces, their tumours) and splitting shapes
    convert.py       Turn into live shape: shapes -> one custom shape (and back)
    envelope.py      velocity envelopes
    smooth.py        freehand made perfect: straight lines, smooth curves, perfect shapes
  files/             saving, MIDI, sound
    project.py       project files, autosave (+ its backup), MIDI export
    safefile.py      saving without half-written files
    errors.py        errors.log and the "something went wrong" message
    about.py         version number, the program's folder
    midi_out.py      MIDI file writer
    domino_clip.py   Copy to / Paste from Domino (notes on the clipboard in Domino's own format)
    playback.py      playing through Windows MIDI out
    mathexpr.py      math in number boxes (960*4 etc.)
    snap.py          the snap choices (bar, note lengths, custom ones) and their length in beats
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

from files import errors  # noqa: E402 (needs the path above)


def check_numpy():
    """NumPy does the heavy note maths. Run from source without it: offer to install it (pip), True when it's there."""
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        pass
    import subprocess
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    command = f'"{sys.executable.replace("pythonw", "python")}" -m pip install numpy'
    if not messagebox.askyesno("Spiderweb", "Spiderweb needs NumPy, a free Python package for fast maths on lots of "
                               "notes. It isn't installed yet.\n\nInstall it now? (needs the internet, takes a "
                               "minute)"):
        messagebox.showinfo("Spiderweb", "To install it yourself, open a command prompt and type:\n\n" + command)
        root.destroy()
        return False
    root.config(cursor="watch")
    root.update()
    try:
        done = subprocess.run([sys.executable, "-m", "pip", "install", "numpy"], capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok, output = done.returncode == 0, (done.stdout + done.stderr).strip()
    except OSError as e:
        ok, output = False, str(e)
    if ok:
        import importlib
        importlib.invalidate_caches()
        try:
            import numpy  # noqa: F401,F811
        except ImportError:
            ok = False
    if not ok:
        messagebox.showerror("Spiderweb", "Installing NumPy didn't work:\n\n" + output[-800:] + "\n\nTo try it "
                             "yourself, open a command prompt and type:\n\n" + command)
    root.destroy()
    return ok

if __name__ == "__main__":
    # Tell Windows we handle display scaling ourselves, so the window isn't stretched (blurry)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    # Its own taskbar button with its own icon (run from Spiderweb.bat it would count as Python otherwise)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Spiderweb")
    except (AttributeError, OSError):
        pass
    if not check_numpy():
        sys.exit(1)
    try:
        from window.app import App
        App().mainloop()
    except Exception:  # couldn't start (errors inside the running window are caught by errors.install)
        errors.report(*sys.exc_info(), "(starting up)", fatal=True)
