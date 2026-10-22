"""The menu bar along the top of the main window (user 2026-10-22: File / Edit / Shape / Tools / View / Help, like
many MIDI programs). Every item is also a key, a button or a right-click item somewhere: the menu is one more way
in, it doesn't replace them. Items that need a selection are greyed out while nothing is selected."""
import tkinter as tk

from files.lang import tr
from window.chop_window import open_chop, quick_chop
from window.claw_window import open_claw
from window.path_window import open_path
from window.strum_window import open_strum


def build_menu(app):
    from window.settings_window import open_settings
    bar = tk.Menu(app)
    menus = []

    def menu(key, sync=None):
        m = tk.Menu(bar, tearoff=False)
        bar.add_cascade(label=tr(key), menu=m)
        if sync:
            m.config(postcommand=lambda: sync(m))
        menus.append(m)
        return m

    def add(m, key, keys, command, **kw):
        m.add_command(label=tr(key, **kw), accelerator=tr(keys) if keys else "", command=command)

    f = menu("menu.file")
    add(f, "menu.new", "menu.ctrl_n", app.new_project)
    add(f, "menu.open", "menu.ctrl_o", app.open_project)
    add(f, "menu.save", "menu.ctrl_s", app.save_project)
    add(f, "menu.save_as", "menu.ctrl_shift_s", app.save_project_as)
    f.add_separator()
    add(f, "menu.export_midi", "menu.ctrl_e", app.generate)
    f.add_separator()
    add(f, "menu.domino_paste", "", lambda: app.paste_from_domino())
    add(f, "menu.domino_copy", "menu.ctrl_shift_c", app.copy_to_domino)
    add(f, "menu.domino_export", "", app.export_dms)
    f.add_separator()
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
        for i in range(4):
            m.entryconfig(i, state="normal" if tools else "disabled")
        m.entryconfig(5, state="normal" if tools else "disabled")
        m.entryconfig(6, state="normal" if app.tumour_targets() else "disabled")
    t = menu("menu.tools", sync_tools)
    add(t, "roll_menu.claw_machine", "roll_menu.alt_w", lambda: open_claw(app))
    add(t, "roll_menu.strum", "roll_menu.alt_s", lambda: open_strum(app))
    add(t, "roll_menu.chop", "roll_menu.alt_u", lambda: open_chop(app))
    add(t, "roll_menu.quick_chop", "roll_menu.ctrl_u", lambda: quick_chop(app))
    t.add_separator()
    add(t, "roll_menu.copy_path", "", lambda: open_path(app))
    add(t, "roll_menu.tumours", "", app.open_tumours)

    v = menu("menu.view")
    redraw = lambda: (app.roll.request_redraw(), app.schedule_autosave())
    for key, var, command in (("app.velocity_pane", app.show_velocity, app.toggle_velocity),
                              ("app.tempo_pane", app.show_tempo, app.toggle_tempo),
                              ("app.history", app.show_history, app.toggle_history),
                              (None, None, None),
                              ("app.show_lines", app.show_lines, redraw),
                              ("app.show_notes", app.show_notes, redraw)):
        if key:
            v.add_checkbutton(label=tr(key), variable=var, command=command)
        else:
            v.add_separator()
    v.add_separator()
    add(v, "app.fit_view", "", lambda: app.roll.fit_view())

    h = menu("menu.help")
    add(h, "menu.help_item", "menu.f1", app.open_help)
    add(h, "menu.whats_new", "", lambda: app.open_help("whats_new"))
    h.add_separator()
    add(h, "menu.settings", "", lambda: open_settings(app))

    app.config(menu=bar)
    app.menubar = bar
    return menus
