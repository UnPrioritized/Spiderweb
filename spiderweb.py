"""
Spiderweb
Draw lines, polylines, freehand strokes, curves, arcs and text on a piano roll and turn them into MIDI notes
(the black MIDI "spiderweb" technique).

Files (the scripts live in scripts/, one subfolder per group):
  window/            the main window and its side panel
    app.py           main window: toolbar, side panel, shapes
    menubar.py       the menu bar (File, Edit, Shape, Tools, View, Help)
    load_midi.py     Load MIDI: the window (how to hold the notes, reading with each step, the summary)
    open_files.py    files given from outside: dropped, double-clicked, handed over by a second start
    app_playback.py  playing the notes, right-drag listening, MIDI out + Built-in BASSMIDI's settings
    app_pictures.py  placed pictures (Image to notes): placing, editing, notes made again
    panel_custom.py  the panel's custom shape settings, opening the drawer
    panel_colours.py the panel's Colours row (notes taking turns over channels)
    panel_funnel.py  the panel's funnel settings
    panel_tumour.py  the panel's tumour line (summary + button)
    panel_pattern.py the panel's pattern numbers (a formula along a curve) + the Formula menu's actions
    panel_polygon.py the panel's polygon settings (points, polygon / star / crossing star)
    pattern_dialog.py the Custom... windows for a curve's shape and pattern (presets, formulas, edited by hand)
    formula_host.py  where formulas go (piano roll curves, drawer strokes, funnel curves) + the Formula menu
    tumour_window.py the tumour window: the tumour settings (bumps along lines)
    history.py       undo / redo and the History list (named undo steps; docked in the side panel or its own window)
    graph_window.py  a tumour setting's graph (the number changing along the line)
    join_split.py    Join (shapes -> one curve) and Split (cut in two, pieces -> separate shapes)
    panel_freehand.py  the panel's Straighten setting for freehand strokes
    panel_gliss.py   the panel's Glissando settings: grids, step, thickness, note length, picked keys
    panel_text.py    the panel's text settings (font, size, spacing, threshold, grow)
    font_dialog.py   the font window (type a font's name or pick one)
    widgets.py       small shared widgets: number boxes, dropdowns, lists (and every name from the three below)
    widgets_place.py where windows open: kept on the screen, remembered places, centred over the main window
    widgets_wheel.py the mouse wheel rules: click first, Linux / Mac wheel and buttons
    widgets_tips.py  tooltips and list tips
    side_panel.py    the side panel of the main window and the drawer
    snap_picker.py   the Snap dropdown (note pictures) and the Customised snap window
    tool_picker.py   the drawing tools' button: the tool picked last, its list and pinned tools
    help_texts.py    every help text: tips, the Help window, tool tooltips, the side panel's help
    help.py          the tip popups and the Help window (F1, searchable)
    domino_overlay.py Domino companion: the see-through window lying over Domino's note grid, following it
    domino_companion.py Draw on Domino: switching app.roll to the overlay piano roll and back, notes in and out
    domino_bar.py    Draw on Domino: the floating bar (its buttons, message line, place over Domino's grid, grip)
    domino_box_watch.py Draw on Domino: the boxes the user drags in Domino's piano roll, watched + kept
    updates.py       update checks: the "how often?" question, the update popup, What's new after an update
    settings_backup.py  an update's first start: the older version's settings + presets copied to user/backups
    drawer.py        the custom shape drawer window
    drawer_library.py the drawer's shape library (shapes/*.json): names, loading, saving, renaming
    drawer_mirror.py the drawer's Mirror maths: a point's mirror copies, which points pair up
    drawer_areas.py  the drawer's Areas tool (colouring areas by hand) and colour swatches
    drawer_mouse.py  the mouse on the drawer's board (drawing, Select, sticking), copy / paste / flip / turn
    canvas_keep.py   kept canvas items: a redraw's items made the same way as last time stay (the drawer's board)
    notes_wait.py    a big project's notes made in the background after an edit: the view keeps working, other
                     clicks / keys wait for them first
    sticky.py        the drawer's sticky lines: points stick to other strokes' points, crossings and lines
    velocity.py      the velocity pane under the piano roll
    tempo_pane.py    the Tempo pane (under the velocity pane): tempo points, curves, catch-up lines
    rebar_work.py    Change visual time signature on the project: tempo, signatures + every shape, one step
    hz_window.py     the Hz bass window: a small piano roll where its notes are placed
    hz_view.py       the Hz bass window's zoom, scrolling and drawing
    hz_sound.py      the Hz bass window's sound: the key held, the preview
    hz_gates.py      the Hz bass window's gates, tune, Pitch, toggles; changes saved into the shape
    hz_mouse.py      the Hz bass window's mouse and keys on its notes (place, move, select, menus)
    hz_simplify.py   the Hz bass window's Simplify points… window (bend / loudness lines, live slider)
    hz_lfo.py        the Hz bass window's LFO… window (design a wobble live; drawn waves + the shape bank)
    hz_box.py        the Hz bass window's 8-handle box (Sketch: stretch notes in time + pitch, bends with them)
    hz_effects.py    the effects pane under the Hz bass window's notes (lines with points)
    hz_preview.py    the Hz bass window's Preview: its sound made ahead (greyed until made) and played
    hz_live.py       the Hz bass live keys: a key held = its Hz bass, played with the quick sound
    hz_synth.py      the Hz bass synth window: the effects' lines for one note + a keyboard for the live keys
    hz_knobs.py      its Knobs tab: boxes of knobs (like a synth's) that make the effects' lines
    hz_dials.py      its knob widgets (round dials, up / down knobs) and how a knob's value maps to where it points
    hz_knob_pics.py  its Knobs tab's small pictures (envelope, waves, pitch, wobbles, arpeggio...)
    hz_rack.py       its Effects tab: a rack of effects (chorus, flanger, echo, reverb, compressor look-alikes)
    hz_macros.py     its macros: big knobs that turn the knobs linked to them
    hz_presets.py    its preset bar: ready-made sounds and the user's own (hz_presets.json)
    synth_look.py    its dark look: colours, boxes with a header strip, the big tabs, dark ttk styles
    preview_settings.py  its Settings window (Pitch, Gates, PPQ, shape length; preview: soundfont, voices, volume...)
    builtin_settings.py Built-in BASSMIDI's Settings window + the overload watcher (red "Overloaded")
    velocity_formula.py the velocity pane's Formula tool settings (pattern, loops, its numbers)
    tool_window.py   what the Claw machine and Strum windows share (live preview, undo, the Knob dial)
    claw_window.py   the Claw machine window
    strum_window.py  the Strum window (Start / End panels of knobs, each with its number box)
    trim_window.py   the Gate trim window (Keep n % of each row, from the left / right)
    path_window.py   the Copy along a path window (path kind, spacing, where B is)
    range_window.py  the spam gate Range's graph window
    sustain_window.py  the Sustain window (on / off, the gap held over: Auto or typed)
    subtract_window.py the Gate subtract window (on / off, ticks, only notes that touch)
    strips_window.py the Channel per strip window (on / off, notes between, the count, strip colours)
    merge_window.py  the Gate sensitive merge window (which way, keep two shapes)
    fill_window.py   the Fill gaps window (stretch left / right / meet in the middle, same-start notes)
    melody_window.py the Fill with tempo jumps window (Conductor pane: jumps at the melody's notes / snap steps)
    jump_areas.py   Jump areas: that window's "Where I click" chain (each click an edge, two edges an area)
    shares_window.py Recolour's Shares window (how many colours, a knob + box per colour's share)
    stir_window.py   Recolour's Stir window (Turn, Twist, Centre point bend the colours there)
    between_window.py  the Add between window (preview, steps, graph) + a group in the main window (list row, menu)
    image_window.py  image to notes: the picture, its sliders, the player-look preview, placing it
    format_window.py the colour list format editor (live output, tags, codes, the user's formats)
    paste_window.py  Paste colours: colours read out of a player's settings text into the picture
    chop_window.py   the Chop window (rhythm list, a strip to draw rhythms, saved rhythms) + quick chop (Ctrl+U)
    props_text.py    the Properties window's words and numbers (what each of its lines shows)
    track_pictures.py the track list's pictures: a loaded MIDI's rows painted, the strip under the list, its picker
  roll/              the piano roll
    pianoroll.py     the piano roll canvas: view, hit testing, mouse editing
    roll_draw.py     painting the grid, notes, handles, keyboard, ruler
    roll_loaded.py   a loaded MIDI's notes on the piano roll (overview at once, exact in the background)
    roll_curve.py    editing curves (anchors + handles, symmetric halves)
    roll_custom.py   the selected custom shape's box (resize, turn, skew)
    roll_funnel.py   editing funnels (curve starts, bends, extra lines, highlighted lines / curves)
    roll_gliss.py    editing a Glissando run: its thickness handle, its notes' right ends
    roll_live.py     live drawing (strokes straight into one custom shape), picking / editing its strokes
    roll_menu.py     the right-click menu
    roll_text.py     the Text tool: typing on the roll, the caret
    roll_hz.py       the Hz bass tool: picking where a Hz bass starts
    roll_path.py     Copy along a path: the path drawn, B placed and its points dragged
    roll_centre.py   Recolour's Split: Clumps centre point dot, dragged on the piano roll
    roll_melody.py   Fill with tempo jumps: the notes it follows outlined on the piano roll
    roll_shared.py   colours, key names, modifier keys, outline caches
    tiles.py         the notes' picture on screen as tiles (moved along when the view scrolls)
    overlay_roll.py  Draw on Domino: the see-through piano roll over Domino's grid (its view = Domino's)
    zoombar.py       the piano roll's scrollbars (drag an end to zoom) and zoom buttons
  notes/             shapes -> notes
    engine.py        shapes -> notes, overlap handling, channel assignment
    engine_slots.py  which track + channel each shape's notes go on, and fixing notes that overlap
    paths.py         lines / polylines / freehand / curves -> notes
    custom.py        custom shapes: outlines, fill, spam, outline spam
    custom_chop.py   custom shapes' spam gate cutting (chop), the outline gate's even band and Outline spam
    passengers.py    pasted notes' CCs and other Domino events riding along (kept, placed, as MIDI messages)
    shrink.py        the outline gate's even band: a custom shape shrunk inward
    faces.py         the areas a custom shape's lines close in, and how many lines deep each is (what Fill fills)
    text.py          text: letters laid out in a font -> a custom shape, threshold, grow
    hzbass.py        Hz bass: spam gates from tones (placed notes, slides, chords)
    hz_settings.py   Hz bass: every setting's limits and starting value, checking saved settings
    hz_lines.py      Hz bass: effect lines' values over time, repeat shapes, envelopes
    hz_glide.py      Hz bass: slides, glides and legato chains between notes
    hz_modulate.py   Hz bass: the MOD tab's engine (LFOs / envelopes moving lines and knobs over time)
    hz_arp.py        Hz bass: the arpeggio's runs (patterns, custom steps, scales, chords)
    hz_runs.py       Hz bass: each tone's run of waves, Auto / fixed / mixed gates, the red line
    hz_grid.py       Hz bass: KeyGrid, every key's own repeats and loudness when effects are on
    hz_cc.py         Hz bass: the CC lines (Cutoff, Pitch wheel...) as MIDI messages on its channels
    hz_fine.py       Hz bass: made on the file's finer ticks in Keep timing parts (the export)
    hz_simplify.py   Hz bass: Simplify points maths (a line cut down to its biggest moves, curves fitted)
    fonts.py         letter outlines from the fonts installed in Windows
    fonts_ft.py      the same on Linux (fontconfig + FreeType)
    fonts_hb.py      the same on a Mac (font files read by uharfbuzz; untested)
    shaping.py       joined letters + right-to-left order for the Text tool (optional uharfbuzz + python-bidi)
    poly_union.py    one outline of polygons laid over each other (Turn into custom shape: joined letters)
    textfast.py      a text's letters -> notes for all letters at once (the same notes as one by one, faster)
    funnel.py        funnels: curves, note grid, gates
    bezier.py        Bezier curves (anchors + handles) for curves and funnel curves, symmetry, fitting to points
    basics.py        small shared maths: distance to a piece of line, "is this a real number" checks
    arc.py           arcs: pieces of a perfect circle through three points
    gliss.py         Glissando runs: one note per picked key along a line, one grid step each
    tumour.py        tumours: bumps along lines
    pattern.py       a curve's formulas: its shape (circle, spiral...) and a pattern along it (wave...)
    polygon.py       polygons and stars made by the Polygon tool (a pattern along every side)
    joined.py        joining shapes into one curve (pieces, their tumours) and splitting shapes
    between.py       Add between: in-between shapes changing step by step from one line into another, groups
    convert.py       Turn into live shape: shapes -> one custom shape (and back)
    envelope.py      velocity envelopes
    slice.py         Slice tool maths: where a line crosses shapes, a custom shape cut in two halves
    gaterange.py     spam gate Range: the gate going from one to another across a shape, along a graph
    chop.py          chop: notes cut into a repeating rhythm
    glue.py          glue: touching notes on a key made one long note (all of a shape or in boxes), first
    claw.py          the claw machine: a shape's notes thinned out / cut / bent after they're made
    strum.py         strum: each chord's notes start one after another (and end, velocity)
    trim.py          gate trim: each key row's runs of touching notes cut down to a share of their length
    copypath.py      copy along a path: the shape's notes copied from A to B (straight / curve / arc)
    sustain.py       sustain: each key row's notes held to their group's end (overlaps kept), done last
    subtract.py      gate subtract: every note ends a few ticks earlier (start kept), done after Sustain
    strips.py        separate strips: each strip of a pasted track guessed key by key, its own channels
    tempo.py         the project's tempo line: curves, catch-ups, exact Domino-safe tempo changes for the MIDI
    tempo_safe.py    Domino-safe tempo values: which ones survive Domino's save, whole values adding up exactly
    loaded.py        a loaded MIDI file (Load MIDI): the loading pass's results, run on several cores
    tempo_file.py    the tempo line in a MIDI file: Keep timing moves notes exactly, bar lines fitted, warnings
    melody.py        Fill with tempo jumps: where the jumps go (melody note starts, rests, snap steps)
    rebar.py         Change visual time signature: bars + tempo stretched together, the same sound
    fx.py            a shape's Claw machine / Strum / Chop pages in order, flips and velocities after them
    smooth.py        freehand made perfect: straight lines, smooth curves, perfect shapes
    picture.py       image to notes: a picture file -> a grid of up to 16 colours (picking, blending, details)
  files/            saving, MIDI, sound
    project.py       project files (.spwb packed, old .json plain), autosave (+ its backup), MIDI export
    dropfiles.py     files dragged in from Explorer onto the main window (Windows' OLE drop target)
    safefile.py      saving without half-written files
    saved_lists.py   the user's lists kept in one file (patterns, rhythms, colour formats): a damaged one loses nothing
    errors.py        spiderweb-error.log and the "something went wrong" message; spiderweb-crash.log when the
                     program dies outright (both next to Spiderweb)
    about.py         version number, the program's folder, the user folder (moving old files in)
    settings.py      the app's own settings (user/settings.ini: the look, later more)
    (window/) settings_window.py  the Settings window (gear button): Look (+ note colour palettes), Updates
    (window/) palettes.py  note colour palettes (user/palettes/), switched live
    (window/) button_look.py  Settings → Look's Button style (system's own, Plain, Raised, Outlined, Flat)
    system.py        what's asked of Windows / Linux (double-click speed, free memory, an error box)
    c_fill.py        makes a C runtime's do-nothing complex-number stand-ins work (before NumPy loads)
    update_check.py  asking GitHub for newer versions (and their notes)
    midi_bytes.py    small MIDI byte helpers shared by the writers (variable-length numbers)
    midi_out.py      MIDI file writer
    midi_read.py     reading any MIDI file safely (Load MIDI): layout, every track checked, fingerprint
    midi_scan.py     the byte loops reading / writing MIDI tracks (also in the own loops, native/loops)
    midi_copy.py     writing a loaded MIDI file out again, unchanged bytes copied, checked while writing
    midi_index.py    Load MIDI's loops: notes paired, the index to read a track anywhere, the overview, painting
    midi_play.py     the loops playing a loaded MIDI: tracks read up to a tick, put in order, thinned per synth
    play_loaded.py   playing a loaded MIDI along with the shapes: read ahead from the file, sent to the synths
    play_dense.py    Kestrel (GPU): a loaded MIDI's parts too dense to read live, read once ahead, copies collapsed
    domino_clip.py   Copy to / Paste from Domino (notes on the clipboard in Domino's own format)
    domino_view.py   Domino companion: where a running Domino's piano roll is on the screen and what it shows
    domino_builds.py Domino versions without a hand-made table: their reading spots found by class name
    domino_boxes.py  Domino's own select box worked out from a drag (quantize snap, bars, which notes it takes)
    mouse_watch.py   the left mouse button pressed / let go anywhere on the screen (raw input, Windows only)
    domino_link.py   Domino companion: notes into Domino (its cursor + paste) and its selection out (copy + delete)
    clipboard.py     the Windows clipboard (bytes in any format, and text)
    share.py         shapes / drawings as one line of text to share (packed, checked when pasted back)
    unzip.py         unpacking outside data with a size limit (no "zip bomb" can fill the memory)
    colour_list.py   picture colours as text for a player's settings (formats), and colours read out of any text
    colours.py       colour codes <-> numbers, mixing two colours, a colour lightened for the dark look
    playback.py      playing through Windows MIDI out
    maestro.py       Maestro (the user's own install) as a MIDI out, through its API: every note stamped with its time
    kestrel.py       Kestrel (GPU) through our connector DLL: live play, and pieces made ahead (Merlin / Kestrel)
    render_ahead.py  Rendered ahead's settings (the sound made into audio ahead of the play line)
    ahead_pieces.py  Rendered ahead's pieces: the song cut into blocks, made in the background, joined for playing
    slow_down.py     slow down to keep the sound going: the synth's load -> the speed the song plays at
    synth.py         the built-in synth (Hz bass preview): BASS + BASSMIDI DLLs in scripts/bass/
    sfz.py           the sample files an SFZ soundfont names (checked when one is picked)
    quicksound.py    the quick sound: single soundfont notes recorded once, copies laid at each note (live keys)
    speed.py         speed-ups: the own compiled loops loaded in the background, Pillow (optional) for pictures
    fastloops.py     the own loops' plain-Python twins (the source they copy; a refused call's last resort)
    native.py        our own compiled loops (Rust, built from native/loops): notes on screen, painting, overlaps...
    native_wraps.py  each own loop's call (room for its results made, a refused call handed to its twin)
    loop_marks.py    the marks fastloops' loops carry (plain Python: they compile nothing)
    numba_loops.py   no own library here (Mac, older Linux) but Numba installed: fastloops compiled by Numba instead
    mathexpr.py      math in number boxes (960*4 etc.)
    snap.py          the snap choices (bar, note lengths, custom ones) and their length in beats
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

from files import errors  # noqa: E402 (needs the path above)


def check_numpy():
    """NumPy does the heavy note maths. Run from source without it: offer to install it (pip), True when it's there.
    Only looks for it, never loads it: a package installed next (offer_extras) can swap NumPy for another version,
    and a loaded one leaves pip's half-removed copy behind."""
    import importlib.util
    if importlib.util.find_spec("numpy") is not None:
        return True
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
    failed, output = run_pip(root, [("numpy", "NumPy")])
    ok = not failed
    if ok:
        import importlib
        importlib.invalidate_caches()
        ok = importlib.util.find_spec("numpy") is not None
    if not ok:
        messagebox.showerror("Spiderweb", "Installing NumPy didn't work:\n\n" + output[-800:] + "\n\nTo try it "
                             "yourself, open a command prompt and type:\n\n" + command)
    root.destroy()
    return ok


def run_pip(root, packages):
    """pip installs each of packages = [(pip name, shown name)], one at a time (one that can't be installed here
    doesn't stop the next), while root shows a small window saying so: it can take minutes, and with nothing on
    screen a second start did nothing (the first one holds the lock) and looked broken. That window carries this
    folder's label, so a second start brings it to the front. -> (pip names that failed, their output's ends)."""
    import subprocess
    import threading
    import time
    from tkinter import ttk
    from files.lang import tr
    root.title("Spiderweb")
    try:
        root.iconbitmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "icons", "icon.ico"))
    except Exception:  # (no icon there / not Windows: Tk's own)
        pass
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)  # (pip stopped halfway can leave a package broken)
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    text = ttk.Label(frame, wraplength=380, justify="left")
    text.pack(anchor="w")
    bar = ttk.Progressbar(frame, mode="indeterminate", length=380)
    bar.pack(fill="x", pady=(12, 0))
    root.deiconify()
    root.update_idletasks()
    root.geometry("+%d+%d" % ((root.winfo_screenwidth() - root.winfo_reqwidth()) // 2,
                              (root.winfo_screenheight() - root.winfo_reqheight()) // 3))
    label_window(root)
    bar.start(15)
    failed, output = [], ""
    for pip, shown in packages:
        text.config(text=tr("extras.installing", name=shown))
        got = {}

        def work():
            try:
                done = subprocess.run([sys.executable, "-m", "pip", "install", PIP_SPEC.get(pip, pip)],
                                      capture_output=True, text=True,
                                      errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                got["ok"], got["out"] = done.returncode == 0, ((done.stdout or "") + (done.stderr or "")).strip()
            except Exception as e:  # (no pip, can't start it...)
                got["ok"], got["out"] = False, str(e)
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            root.update()
            time.sleep(0.03)
        if not got.get("ok"):
            failed.append(pip)
            output += got.get("out", "")[-400:] + "\n"
    bar.stop()
    for child in root.winfo_children():
        child.destroy()
    root.withdraw()
    return failed, output.strip()


EXTRAS = (("numba", "numba", "extras.numba"), ("PIL", "pillow", "extras.pillow"),
          ("uharfbuzz", "uharfbuzz", "extras.uharfbuzz"), ("bidi", "python-bidi", "extras.bidi"))  # (module, pip, text)
SHOWN = {"numba": "Numba", "pillow": "Pillow", "uharfbuzz": "uharfbuzz",
         "python-bidi": "python-bidi"}  # (their names while they're installed)
# Versions asked of pip. python-bidi is pinned to the tested 0.6 line: shaping.levels reads its Rust part's debug
# text, which it never promised to keep (test bidi_levels catches a change). The rest only get a lowest version:
# an upper limit would leave a brand-new Python without a ready-made build to install.
PIP_SPEC = {"uharfbuzz": "uharfbuzz>=0.57", "python-bidi": "python-bidi>=0.6,<0.7"}


def own_loops_work():
    """Our own compiled loops load here (files/native.py). Numba is offered ONLY when they can't (user 2026-11-09:
    a Mac, an older Linux); where they load it's never asked for, never imported."""
    try:
        from files import native
        return native.load() is not None
    except Exception:  # noqa: BLE001 (failing = they don't work here)
        return False


def offer_extras():
    """Pillow (pictures to the screen faster, more picture kinds) is optional: Spiderweb works without it, slower.
    Numba too, but only where our own compiled loops can't load (own_loops_work). uharfbuzz + python-bidi too
    (the Text tool's joined letters / right-to-left writing; without them it draws letters one by one). Run from source with one
    missing (or, for the Text tool's two, older than notes.shaping.LOWEST: pip updates it): offer to install it,
    once (a "No" or a failed install is remembered in packages.json; the exe has what it needs inside)."""
    if getattr(sys, "frozen", False):
        return
    import importlib
    import importlib.util
    import json
    from files.about import USER
    path = os.path.join(USER, "packages.json")
    try:
        with open(path, encoding="utf-8") as f:
            declined = list(json.load(f).get("declined", []))
    except (OSError, ValueError, AttributeError):
        declined = []
    from notes.shaping import too_old
    old = too_old()  # (installed but too old for the Text tool: offered like a missing one, pip updates it)
    missing = [(pip, text) for module, pip, text in EXTRAS
               if pip not in declined and (importlib.util.find_spec(module) is None or pip in old)]
    if any(pip == "numba" for pip, _ in missing) and own_loops_work():
        missing = [m for m in missing if m[0] != "numba"]
    if not missing:
        return
    import tkinter as tk
    from tkinter import messagebox
    from files.lang import tr, trn
    from files.safefile import write_text
    root = tk.Tk()
    root.withdraw()
    names = " ".join(f'"{PIP_SPEC[pip]}"' if pip in PIP_SPEC else pip for pip, _ in missing)
    command = f'"{sys.executable.replace("pythonw", "python")}" -m pip install {names}'
    failed = []
    if messagebox.askyesno("Spiderweb", trn("extras.ask", len(missing),
                                            list="\n".join("• " + tr(text) for _, text in missing))):
        failed, output = run_pip(root, [(pip, SHOWN[pip]) for pip, _ in missing])
        importlib.invalidate_caches()
        if failed:
            messagebox.showerror("Spiderweb", trn("extras.failed", len(failed), output=output, command=command))
    else:
        failed = [pip for pip, _ in missing]
    if failed:
        try:
            write_text(path, json.dumps({"declined": sorted(set(declined + failed))}) + "\n")
        except OSError:
            pass
    root.destroy()


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


def close_lock():
    """Lets go of this start's handle on the lock (a retry after the open Spiderweb closed: while any handle is
    open, the lock still counts as taken)."""
    global _lock
    if _lock:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_lock))
        _lock = None


if __name__ == "__main__":
    from files import c_fill
    c_filled = c_fill.fill()  # (before NumPy loads: some systems' C runtime has stand-ins that end the program)
    if "--loops-check" in sys.argv[1:]:  # (a run of its own: does the compiled loops' level work here? native.py)
        from files import native
        try:  # (a level the processor can't run crashes this run: ended at once, no crash box waiting for a click)
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except (AttributeError, OSError):
            pass
        sys.exit(native.self_check())
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
    import time
    started = time.time()
    given = [os.path.abspath(a) for a in sys.argv[1:] if os.path.isfile(a)]  # (opened with Spiderweb / dropped on it)
    updated = "--updated" in sys.argv[1:]  # (started by the old exe after an update: files/self_update.py)
    try:
        first = first_instance()
        if not first and updated:  # the old one is still closing: wait for it (up to a minute)
            for _ in range(600):
                time.sleep(0.1)
                close_lock()
                if first_instance():
                    first = True
                    break
            else:
                sys.exit(0)
        if not first:
            if not given:
                sys.exit(0)
            from window.open_files import hand_over, handed_taken
            handed = hand_over(given)  # (the open Spiderweb opens them)
            if handed is None or handed_taken(handed):
                sys.exit(0)
            # not taken: that Spiderweb is closing (or stuck): this one starts once it has gone
            for _ in range(50):
                time.sleep(0.1)
                close_lock()
                if first_instance():
                    break
            else:
                sys.exit(0)
    except (AttributeError, OSError):  # not Windows: no lock
        pass
    try:
        from files.self_update import clean_leftovers
        clean_leftovers()  # (an update's Spiderweb.exe.old / .new: exe only)
    except Exception:
        pass
    from files.system import WINE
    if WINE and getattr(sys, "frozen", False):  # (old unpacked copies of the exe a hard-ended session left: Temp)
        import threading
        from files.system import clean_old_unpacked
        threading.Thread(target=clean_old_unpacked, name="old unpacked", daemon=True).start()
    from window import open_files
    open_files.STARTED = started
    open_files.HOLD[0] = bool(given)  # (the last session's MIDI isn't read again before the given file opens)
    from files.about import move_old_files
    not_moved = move_old_files()
    errors.move_old_logs()
    errors.watch_crashes()
    if c_filled:
        errors.note("C runtime", f"{len(c_filled)} complex functions filled in")
    if not check_numpy():
        sys.exit(1)
    try:
        offer_extras()
    except Exception:  # (only an offer: never stops Spiderweb from starting)
        pass
    try:
        from window.app import App
        app = App()
        label_window(app)
        open_files.start(app, given)
        if errors.last_crash is not None:  # (the run before crashed: said once)
            app.after(400, lambda: errors.say_crash(app))
        if not_moved:  # (old files another program held: they stay beside Spiderweb, the user moves them)
            from tkinter import messagebox
            from files.about import HERE, USER
            from files.lang import tr
            app.after(500, lambda: messagebox.showwarning("Spiderweb", tr(
                "about.not_moved", files="\n".join(not_moved), here=HERE, user=USER), parent=app))
        app.mainloop()
    except Exception:  # couldn't start (errors inside the running window are caught by errors.install)
        errors.report(*sys.exc_info(), "(starting up)", fatal=True)
