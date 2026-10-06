"""
Spiderweb
Draw lines, polylines, freehand strokes, curves, arcs and text on a piano roll and turn them into MIDI notes
(the black MIDI "spiderweb" technique).

Files (the scripts live in scripts/, one subfolder per group):
  window/            the main window and its side panel
    app.py           main window: toolbar, side panel, shapes, playback, undo
    panel_custom.py  the panel's custom shape settings, opening the drawer
    panel_colours.py the panel's Colours row (notes taking turns over channels)
    panel_funnel.py  the panel's funnel settings
    panel_tumour.py  the panel's tumour line (summary + button)
    panel_pattern.py the panel's pattern numbers (a formula along a curve) + the Formula menu's actions
    panel_polygon.py the panel's polygon settings (points, polygon / star / crossing star)
    pattern_dialog.py the Custom... windows for a curve's shape and pattern (presets, formulas, edited by hand)
    formula_host.py  where formulas go (piano roll curves, drawer strokes, funnel curves) + the Formula menu
    tumour_window.py the tumour window: the tumour settings (bumps along lines)
    history.py       the History list (named undo steps; docked in the side panel or its own window)
    graph_window.py  a tumour setting's graph (the number changing along the line)
    join_split.py    Join (shapes -> one curve) and Split (cut in two, pieces -> separate shapes)
    panel_freehand.py  the panel's Straighten setting for freehand strokes
    panel_text.py    the panel's text settings (font, size, spacing, threshold, grow)
    font_dialog.py   the font window (type a font's name or pick one)
    widgets.py       tooltips
    snap_picker.py   the Snap dropdown (note pictures) and the Customised snap window
    tool_picker.py   the drawing tools' button: the tool picked last, its list and pinned tools
    help_texts.py    every help text: tips, the Help window, tool tooltips, the side panel's help
    help.py          the tip popups and the Help window (F1, searchable)
    updates.py       update checks: the "how often?" question, the update popup, What's new after an update
    drawer.py        the custom shape drawer window and the shape library (shapes/*.json)
    sticky.py        the drawer's sticky lines: points stick to other strokes' points, crossings and lines
    velocity.py      the velocity pane under the piano roll
    hz_window.py     the Hz bass window: a small piano roll where its notes are placed
    hz_effects.py    the effects pane under the Hz bass window's notes (lines with points)
    hz_preview.py    the Hz bass window's Preview: its sound made ahead (greyed until made) and played
    hz_live.py       the Hz bass live keys: a key held = its Hz bass, played with the quick sound
    hz_synth.py      the Hz bass synth window: the effects' lines for one note + a keyboard for the live keys
    hz_knobs.py      its Knobs tab: boxes of knobs (like a synth's) that make the effects' lines
    hz_rack.py       its Effects tab: a rack of effects (chorus, echo, reverb look-alike)
    hz_presets.py    its preset bar: ready-made sounds and the user's own (hz_presets.json)
    synth_look.py    its dark look: colours, boxes with a header strip, the big tabs, dark ttk styles
    preview_settings.py  its Preview settings window (soundfont, voice limit, reverb, volume, live keys' memory)
    velocity_formula.py the velocity pane's Formula tool settings (pattern, loops, its numbers)
    tool_window.py   what the Claw machine and Strum windows share (live preview, undo, the Knob dial)
    claw_window.py   the Claw machine window
    strum_window.py  the Strum window (Start / End panels of knobs, each with its number box)
    range_window.py  the spam gate Range's graph window
    chop_window.py   the Chop window (rhythm list, a strip to draw rhythms, saved rhythms) + quick chop (Ctrl+U)
  roll/              the piano roll
    pianoroll.py     the piano roll canvas: view, hit testing, mouse editing
    roll_draw.py     painting the grid, notes, handles, keyboard, ruler
    roll_curve.py    editing curves (anchors + handles, symmetric halves)
    roll_custom.py   the selected custom shape's box (resize, turn, skew)
    roll_funnel.py   editing funnels (curve starts, bends, extra lines, highlighted lines / curves)
    roll_live.py     live drawing (strokes straight into one custom shape), picking / editing its strokes
    roll_menu.py     the right-click menu
    roll_text.py     the Text tool: typing on the roll, the caret
    roll_hz.py       the Hz bass tool: picking where a Hz bass starts
    roll_shared.py   colours, key names, modifier keys, outline caches
    zoombar.py       the piano roll's scrollbars (drag an end to zoom) and zoom buttons
  notes/             shapes -> notes
    engine.py        shapes -> notes, overlap handling, channel assignment
    paths.py         lines / polylines / freehand / curves -> notes
    custom.py        custom shapes: outlines, fill, spam, outline spam
    shrink.py        the outline gate's even band: a custom shape shrunk inward
    faces.py         the areas a custom shape's lines close in, and how many lines deep each is (what Fill fills)
    text.py          text: letters laid out in a font -> a custom shape, threshold, grow
    hzbass.py        Hz bass: spam gates from tones (placed notes, slides, chords)
    fonts.py         letter outlines from the fonts installed in Windows
    funnel.py        funnels: curves, note grid, gates
    bezier.py        Bezier curves (anchors + handles) for curves and funnel curves, symmetry, fitting to points
    arc.py           arcs: pieces of a perfect circle through three points
    tumour.py        tumours: bumps along lines
    pattern.py       a curve's formulas: its shape (circle, spiral...) and a pattern along it (wave...)
    polygon.py       polygons and stars made by the Polygon tool (a pattern along every side)
    joined.py        joining shapes into one curve (pieces, their tumours) and splitting shapes
    convert.py       Turn into live shape: shapes -> one custom shape (and back)
    envelope.py      velocity envelopes
    slice.py         Slice tool maths: where a line crosses shapes, a custom shape cut in two halves
    gaterange.py     spam gate Range: the gate going from one to another across a shape, along a graph
    chop.py          chop: notes cut into a repeating rhythm (after the glue, before the claw)
    glue.py          glue: touching notes on a key made one long note (all of a shape or in boxes), first
    claw.py          the claw machine: a shape's notes thinned out / cut / bent after they're made
    strum.py         strum: each chord's notes start one after another (and end, velocity), after the claw
    smooth.py        freehand made perfect: straight lines, smooth curves, perfect shapes
    picture.py       image to notes: a picture file -> a grid of up to 16 colours (picking, blending, details)
  files/            saving, MIDI, sound
    project.py       project files, autosave (+ its backup), MIDI export
    safefile.py      saving without half-written files
    errors.py        errors.log and the "something went wrong" message
    about.py         version number, the program's folder
    update_check.py  asking GitHub for newer versions (and their notes)
    midi_out.py      MIDI file writer
    domino_clip.py   Copy to / Paste from Domino (notes on the clipboard in Domino's own format)
    clipboard.py     the Windows clipboard (bytes in any format, and text)
    share.py         shapes / drawings as one line of text to share (packed, checked when pasted back)
    playback.py      playing through Windows MIDI out
    synth.py         the built-in synth (Hz bass preview): BASS + BASSMIDI DLLs in scripts/bass/
    quicksound.py    the quick sound: single soundfont notes recorded once, copies laid at each note (live keys)
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


_lock = None  # the open "Spiderweb is running" handle, kept until the program ends


def folder_tag():
    """This folder's name for the lock and the main window's label. The folder's true full path (links, subst
    drives and short names like BLACKM~1 all lead to the same one), so one folder always gets one name."""
    import hashlib
    from files.about import HERE
    return "Spiderweb-" + hashlib.md5(os.path.normcase(os.path.realpath(HERE)).encode("utf-8")).hexdigest()


def label_window(app):
    """Puts this folder's label on the main window, so a second start finds this window (not another folder's,
    whatever the title says)."""
    try:
        app.update_idletasks()
        ctypes.windll.user32.SetPropW(ctypes.c_void_p(int(app.wm_frame(), 16)), folder_tag(), ctypes.c_void_p(1))
    except (AttributeError, OSError, ValueError):
        pass


def first_instance():
    """Only one Spiderweb per folder (they'd share the autosave and settings): True when none is open yet. Otherwise
    the open one's window is brought to the front and False. Copies in other folders don't count."""
    global _lock
    from ctypes import wintypes
    k, u = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.windll.user32
    k.CreateMutexW.restype = wintypes.HANDLE
    u.GetPropW.restype = wintypes.HANDLE
    u.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    tag = folder_tag()
    _lock = k.CreateMutexW(None, False, "Local\\" + tag)
    if not _lock or ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return True
    found = []

    def look(h, _):
        if u.IsWindowVisible(h) and u.GetPropW(h, tag):
            found.append(h)
        return not found
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(look), 0)
    if found:
        if u.IsIconic(found[0]):
            u.ShowWindow(found[0], 9)  # SW_RESTORE
        u.SetForegroundWindow(found[0])
    return False


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
    try:
        if not first_instance():
            sys.exit(0)
    except (AttributeError, OSError):  # not Windows: no lock
        pass
    if not check_numpy():
        sys.exit(1)
    try:
        from window.app import App
        app = App()
        label_window(app)
        app.mainloop()
    except Exception:  # couldn't start (errors inside the running window are caught by errors.install)
        errors.report(*sys.exc_info(), "(starting up)", fatal=True)
