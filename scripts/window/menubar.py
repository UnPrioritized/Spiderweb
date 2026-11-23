"""The menu bar along the top of the main window (user 2026-10-22: File / Edit / Shape / Tools / View / Help, like
many MIDI programs). Every item is also a key, a button or a right-click item somewhere: the menu is one more way
in, it doesn't replace them. Items that need a selection are greyed out while nothing is selected.
The dark look draws its own bar (a row of menu buttons): the system's menu bar can't be coloured (user: a light bar
"absolutely bothers me"); the light look keeps the system's."""
import tkinter as tk

from files.lang import tr
from window import look
from window.chop_window import open_chop, quick_chop
from window.claw_window import open_claw
from window.fill_window import open_fill
from window.fold_window import fold_targets, open_fold
from window.load_midi import find_loaded, load_midi
from window.path_window import open_path
from window.strum_window import open_strum
from window.trim_window import open_trim


RECENT_FOLDER_MOST = 48  # characters of a recent file's folder shown beside its name (the rest "…")


def fill_recent(app, m, kind):
    """One Open recent list, filled each time it opens: name + folder, newest first, then Clear list."""
    import os
    from files import recent
    m.delete(0, "end")
    paths = recent.items(kind)
    for p in paths:
        where = os.path.dirname(p)
        if len(where) > RECENT_FOLDER_MOST:
            where = "…" + where[-(RECENT_FOLDER_MOST - 1):]
        m.add_command(label=tr("menu.recent_item", name=os.path.basename(p), folder=where),
                      command=lambda p=p: app.open_recent(kind, p))
    if not paths:
        m.add_command(label=tr("menu.recent_none"), state="disabled")
    m.add_separator()
    m.add_command(label=tr("menu.recent_clear"), state="normal" if paths else "disabled",
                  command=lambda: recent.clear(kind))


def build_menu(app):
    from window.settings_window import open_settings
    own = look.DARK
    if own:
        bar = tk.Frame(app)
        bar.pack(side="top", fill="x")
    else:
        bar = tk.Menu(app)
    menus = app.menus = []  # [(label, menu)]

    def menu(key, sync=None):
        if own:  # (a menu button's menu must be its child)
            b = tk.Menubutton(bar, text=tr(key), relief="flat", borderwidth=0, padx=7, pady=3,
                              activebackground=look.BUTTON_HOT)
            b.pack(side="left")
            m = tk.Menu(b, tearoff=False)
            b.config(menu=m)
        else:
            m = tk.Menu(bar, tearoff=False)
            bar.add_cascade(label=tr(key), menu=m)
        if sync:
            m.config(postcommand=lambda: sync(m))
        menus.append((tr(key), m))
        return m

    def add(m, key, keys, command, **kw):
        m.add_command(label=tr(key, **kw), accelerator=tr(keys) if keys else "", command=command)

    def sync_file(m):  # (a project's MIDI: found again only while not loaded; a copy for sharing once it is)
        m.entryconfig(9, state="normal" if app.midi_link is not None and app.loaded is None else "disabled")
        m.entryconfig(10, state="normal" if app.loaded is not None else "disabled")
    f = menu("menu.file", sync_file)
    add(f, "menu.new", "menu.ctrl_n", app.new_project)
    add(f, "menu.open", "menu.ctrl_o", app.open_project)
    # File → Open recent ▸ Projects ▸ / Loaded MIDI ▸ (QoL E, user 2026-11-23: 16 each, Clear list)
    rec = tk.Menu(f, tearoff=False)
    f.add_cascade(label=tr("menu.open_recent"), menu=rec)
    for kind, key in (("projects", "menu.recent_projects"), ("midi", "menu.recent_midi")):
        sub = tk.Menu(rec, tearoff=False)
        sub.config(postcommand=lambda m=sub, k=kind: fill_recent(app, m, k))
        rec.add_cascade(label=tr(key), menu=sub)
    add(f, "menu.open_backup", "", app.open_backup)
    add(f, "menu.save", "menu.ctrl_s", app.save_project)
    add(f, "menu.save_as", "menu.ctrl_shift_s", app.save_project_as)
    f.add_separator()
    add(f, "menu.export_midi", "menu.ctrl_e", app.generate)
    add(f, "menu.load_midi", "", lambda: load_midi(app))
    add(f, "menu.find_loaded", "", lambda: find_loaded(app))
    add(f, "menu.share_copy", "", app.save_sharing_copy)
    f.add_separator()
    add(f, "menu.domino_paste", "", lambda: app.paste_from_domino())
    add(f, "menu.domino_copy", "menu.ctrl_shift_c", app.copy_to_domino)
    add(f, "menu.domino_export", "", app.export_dms)
    f.add_separator()
    add(f, "menu.settings", "menu.ctrl_comma", lambda: open_settings(app))  # (moved from Help, user 2026-11-12)
    add(f, "menu.exit", "", app.on_close)

    def sync_edit(m):
        pane = getattr(app, "key_pane", None) is app.tempo_pane
        picked = bool(app.sels) or pane and bool(app.tempo_pane.sel)
        for i, on in ((0, bool(app.undo_stack)), (1, bool(app.redo_stack)), (3, picked), (4, picked),
                      (6, bool(app.sels)), (7, picked), (8, bool(app.shapes))):
            m.entryconfig(i, state="normal" if on else "disabled")
    e = menu("menu.edit", sync_edit)
    add(e, "app.undo", "menu.ctrl_z", app.undo_roll)
    add(e, "app.redo", "menu.ctrl_y", lambda: app.undo_roll(True))
    e.add_separator()
    add(e, "menu.cut", "roll_menu.ctrl_x", app.cut_selected)
    add(e, "menu.copy", "roll_menu.ctrl_c", app.copy_selected)
    add(e, "menu.paste", "roll_menu.ctrl_v", app.paste)
    add(e, "app.duplicate", "roll_menu.ctrl_d", app.duplicate)
    add(e, "app.delete", "roll_menu.del", app.delete_picked)
    add(e, "app.delete_all", "", app.delete_all)
    e.add_separator()
    add(e, "menu.select_all", "menu.ctrl_a", app.select_all)

    def sync_shape(m):
        for i in range(m.index("end") + 1):
            if m.type(i) == "command":
                m.entryconfig(i, state="normal" if app.sels else "disabled")
    s = menu("menu.shape", sync_shape)
    add(s, "menu.join", "roll_menu.ctrl_g", app.join_selected)
    add(s, "menu.split", "roll_menu.ctrl_shift_g", app.split_selected)
    s.add_separator()
    add(s, "roll_menu.flip_sideways", "roll_menu.ctrl_h", lambda: app.flip(True))
    add(s, "roll_menu.flip_upside_down", "roll_menu.ctrl_j", lambda: app.flip(False))
    add(s, "roll_menu.turn_90_left", "roll_menu.ctrl_left", lambda: app.rotate(False))
    add(s, "roll_menu.turn_90_right", "roll_menu.ctrl_right", lambda: app.rotate(True))
    s.add_separator()
    add(s, "roll_menu.turn_into_live_shape", "roll_menu.ctrl_r", app.turn_into_live)

    def sync_tools(m):
        tools = bool(app.note_tool_sels())
        for i in range(5):
            m.entryconfig(i, state="normal" if tools else "disabled")
        m.entryconfig(6, state="normal" if tools else "disabled")
        m.entryconfig(7, state="normal" if app.tumour_targets() else "disabled")
        m.entryconfig(9, state="normal" if app.fill_targets() else "disabled")
        m.entryconfig(10, state="normal" if fold_targets(app) else "disabled")
        m.entryconfig(12, state="normal" if tools else "disabled")  # (Gate tools ▸)
        m.entryconfig(13, state="normal" if app.merge_pair() else "disabled")
        m.entryconfig(14, state="normal" if tools else "disabled")  # (Glue)

    def sync_gates(m):
        """Gate tools ▸ (QoL F, user 2026-11-23: the right-click menu's gate items, greyed the same way)."""
        from window.sustain_window import any_sustained, sustain_targets
        from window.subtract_window import any_subtracted, subtract_targets
        from window.extend_window import any_extended
        from window.strips_window import any_strips, strips_targets
        gates_vars["sustain"].set(any_sustained(app))
        gates_vars["subtract"].set(any_subtracted(app))
        gates_vars["extend"].set(any_extended(app))
        gates_vars["strips"].set(bool(strips_targets(app)) and any_strips(app))
        gf = app.gate_flip_state()
        gates_vars["flip"].set(bool(gf and gf[1]))
        for i, on in ((0, sustain_targets(app)), (1, subtract_targets(app)), (2, subtract_targets(app)),
                      (3, gf and gf[0]), (4, strips_targets(app)), (5, app.line_targets())):
            m.entryconfig(i, state="normal" if on else "disabled")
    t = menu("menu.tools", sync_tools)
    add(t, "roll_menu.claw_machine", "roll_menu.alt_w", lambda: open_claw(app))
    add(t, "roll_menu.strum", "roll_menu.alt_s", lambda: open_strum(app))
    add(t, "roll_menu.chop", "roll_menu.alt_u", lambda: open_chop(app))
    add(t, "roll_menu.quick_chop", "roll_menu.ctrl_u", lambda: quick_chop(app))
    add(t, "roll_menu.gate_trim", "", lambda: open_trim(app))
    t.add_separator()
    add(t, "roll_menu.copy_path", "", lambda: open_path(app))
    add(t, "roll_menu.tumours", "", app.open_tumours)
    t.add_separator()
    add(t, "fill_gaps.menu", "", lambda: open_fill(app))
    add(t, "fold.menu", "", lambda: open_fold(app))
    # every tool listed (QoL F, user 2026-11-23: the gate tools were missing; no new keys)
    t.add_separator()
    from window.extend_window import open_extend
    from window.lines_window import open_lines
    from window.merge_window import open_merge
    from window.strips_window import open_strips
    from window.subtract_window import open_subtract
    from window.sustain_window import open_sustain
    gates = tk.Menu(t, tearoff=False, postcommand=lambda: sync_gates(gates))
    gates_vars = {k: tk.BooleanVar(app, value=False) for k in ("sustain", "subtract", "extend", "flip", "strips")}
    for key, var, command in (("sustain.menu", "sustain", lambda: open_sustain(app)),
                              ("subtract.menu", "subtract", lambda: open_subtract(app)),
                              ("extend.menu", "extend", lambda: open_extend(app)),
                              ("roll_menu.gate_flip", "flip", app.gate_flip),
                              ("strips.menu", "strips", lambda: open_strips(app))):
        gates.add_checkbutton(label=tr(key), variable=gates_vars[var], command=command)
    add(gates, "lines.menu_split", "", lambda: open_lines(app, "split"))
    t.add_cascade(label=tr("menu.gate_tools"), menu=gates)
    add(t, "join_split.merge_menu", "", lambda: open_merge(app))
    add(t, "roll_menu.glue", "", app.glue_selected)
    t.add_separator()
    add(t, "stir.button", "", app.open_stir)
    add(t, "warp.button", "", app.open_warp)
    add(t, "liquify.button", "", app.open_liquify)
    add(t, "recolour.shares", "", app.open_shares)

    v = menu("menu.view")
    redraw = lambda: (app.roll.request_redraw(), app.schedule_autosave())
    for key, var, command in (("app.velocity_pane", app.show_velocity, app.toggle_velocity),
                              ("app.tempo_pane", app.show_tempo, app.toggle_tempo),
                              ("app.history", app.show_history, app.toggle_history),
                              ("menu.side_panel", app.side_open_var(), app.toggle_side),
                              (None, None, None),
                              ("app.show_lines", app.show_lines, redraw),
                              ("app.show_notes", app.show_notes, redraw)):
        if key:
            v.add_checkbutton(label=tr(key), variable=var, command=command)
        else:
            v.add_separator()
    v.add_separator()
    add(v, "menu.track_list", "menu.tab", app.toggle_tracks)
    add(v, "app.fit_view", "", lambda: app.roll.fit_view())

    h = menu("menu.help")
    add(h, "menu.help_item", "menu.f1", app.open_help)
    add(h, "menu.whats_new", "", lambda: app.open_help("whats_new"))
    add(h, "menu.about", "", lambda: app.open_help("about"))

    if not own:
        app.config(menu=bar)
    return menus
