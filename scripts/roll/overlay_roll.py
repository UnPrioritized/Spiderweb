"""Drawing on Domino's piano roll (Domino companion): Spiderweb's own piano roll, but see-through and without its
grid, keyboard, ruler and play line, lying exactly over Domino's note grid. While companion mode is on it IS
app.roll, so every tool, the undo steps and the shapes work as in the main window; only the view comes from Domino
(set_domino_view): Domino's scroll and zoom, never our own (the wheel goes to Domino, see window/domino_companion)."""

from roll.pianoroll import PianoRoll
from roll.roll_shared import SHIFT


class OverlayRoll(PianoRoll):
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
        pass

    def draw_ruler(self, w):
        pass

    def draw_playhead(self, keep=False):
        self.delete("playhead")

    def on_key(self, e):
        # Esc with nothing going on: back to Domino's own mouse (the bar's pointer), instead of deselecting
        if e.keysym == "Escape" and not (self.draft or self.drag or self.follow or self.typing) \
                and not e.state & SHIFT:
            self.app.tool.set("select")
            return "break"
        return super().on_key(e)
