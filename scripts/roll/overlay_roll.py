"""Drawing on Domino's piano roll (Domino companion): Spiderweb's own piano roll, but see-through and without its
grid, keyboard, ruler and play line, lying exactly over Domino's note grid. While companion mode is on it IS
app.roll, so every tool, the undo steps and the shapes work as in the main window; only the view comes from Domino
(set_domino_view): Domino's scroll and zoom, never our own (the wheel goes to Domino, see window/domino_companion)."""

from types import SimpleNamespace

from roll.pianoroll import PianoRoll
from roll.roll_shared import SHIFT

DOMINO_BLACK = 51 / 101  # Domino's black keys: 51 of its keyboard's 101 px (measured on the user's screen 2026-11-06)


class OverlayRoll(PianoRoll):
    # Domino cuts a note's x down to the pixel, Spiderweb rounds: half a pixel less = the same edges as Domino's
    # (measured 2026-11-06, dev/scratch/dj_check.py; user: what you see is what you get). Thin notes still get 1 px.
    note_x_shift = -0.4999
    ghosts = ()        # (remembered shapes whose notes are selected in Domino, where Domino has their notes now:
    # window/domino_companion.show_selected) = a white line with orange dashes over it, 3 px (look.COMPANION_OUTLINE)

    def redraw(self):
        super().redraw()
        self.delete("ghost")
        from window import look
        w = max(3, round(3 * self.scale))
        for sh in self.ghosts:
            self.draw_path(sh, look.COMPANION_OUTLINE_BACK, w, tags=("ghost",))
            self.draw_path(sh, look.COMPANION_OUTLINE, w, dash=(6, 4), tags=("ghost",))
    def __init__(self, parent, app, scale, see_through, wheel=None):
        super().__init__(parent, app, scale)
        self.see_through = see_through
        self.wheel_out = wheel  # (e) -> the wheel turned over the piano roll: Domino scrolls / zooms
        self.kb_w = self.ruler_h = 0  # (Domino's keyboard and ruler are beside the window, not in it)
        self.configure(bg=see_through)
        self.domino = None  # the ViewState shown

    # --- the view: Domino's ---
    def set_domino_view(self, v, keys):
        """v = files.domino_view.ViewState: the same beats / keys under the same pixels as Domino's grid."""
        self.domino = v
        sx, sy, t, top = v.beat_px, v.row_px, v.left_beat, keys - 0.5 - v.top_row
        if (self.sx, self.sy, self.view_t, self.view_top) != (sx, sy, t, top):
            self.sx, self.sy, self.view_t, self.view_top = sx, sy, t, top
            self.request_redraw()

    def clamp_view(self):
        pass  # (Domino decides where the view is)

    def room_keys(self):
        return 0  # (Space past the keys / before the start: main piano roll only; Domino's view can't go there)

    def room_beats(self):
        return 0.0

    def fit_view(self):
        if self.sx is None:  # (before Domino's view is known: anything, so redraw has numbers)
            self.sx, self.sy = 100.0, 10.0
        self.request_redraw()

    def apply_saved_view(self):
        self._saved_view = None

    def box_scroll(self):
        self.box_timer = None  # (a Select box past the edge doesn't scroll: the view is Domino's)

    def on_wheel(self, e):
        if self.wheel_out:
            self.wheel_out(e)
        return "break"

    def start_pan(self, e):
        return "break"

    def pan_to(self, e):
        return "break"

    def zoom_x(self, f, x):
        pass

    def zoom_y(self, f, y):
        pass

    # --- what isn't drawn: Domino shows its own ---
    def grid_parts(self, w, h):
        return [(0, h, self.see_through)], []

    def draw_grid(self, w, h, rows, cols):
        pass

    def draw_keyboard(self, h):
        """Domino's own keys show through; while picking Glissando keys (kb_w = Domino's keyboard, set by
        window/domino_companion.sync_keys) only the picked keys and the one held are lit over them, like the main
        piano roll's."""
        kb = self.kb_w
        if not kb:
            return
        from roll.roll_shared import BLACK
        from window import look
        lit = self.app.gliss_picks() or ()
        p_lo, p_hi = self.visible_pitches(h)
        for p in range(p_lo, p_hi + 1):
            if p != self.held_key and p not in lit:
                continue
            y0, y1 = self.row_y(p)
            black = p % 12 in BLACK
            fill = look.KEY_HELD if p == self.held_key else look.KEY_PICKED_BLACK if black else look.KEY_PICKED
            self.create_rectangle(0, y0, kb * DOMINO_BLACK if black else kb, y1, fill=fill, outline="", tags=("frame", "kbd"))

    def draw_ruler(self, w):
        pass

    def draw_playhead(self, keep=False):
        self.delete("playhead")

    def forwarded_double(self, e):
        """A double click passed on from the mouse catcher (window/domino_overlay.forward_to: Tk can't see those):
        what a real one does here. Double right-click while drawing = Domino's own mouse (user 2026-11-06), the
        same toggle as the main piano roll."""
        ev = SimpleNamespace(x=e.x, y=e.y, x_root=e.x_root, y_root=e.y_root, state=e.state, num=e.num, time=e.time,
                             widget=self, delta=0, keysym="??", char="", type="4")
        if e.num == 3:
            return self.right_twice(ev)
        return self.on_double(ev)

    def idle(self):
        return not (self.draft or self.drag or self.follow or self.typing)

    def on_key(self, e):
        # Esc with nothing going on: back to Domino's own mouse (the bar's pointer), instead of deselecting
        if e.keysym == "Escape" and self.idle() and not e.state & SHIFT:
            self.app.tool.set("select")
            return "break"
        # Enter with nothing going on: what's drawn here goes into Domino (user 2026-11-06: "when I'm done")
        if e.keysym in ("Return", "KP_Enter") and self.idle() and self.app.companion is not None \
                and self.app.tool.get() != "lasso":
            self.app.companion.send_all()
            return "break"
        return super().on_key(e)
