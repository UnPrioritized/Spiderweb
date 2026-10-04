"""Piano roll: the Text tool. Click to put a blinking caret there and type straight onto the roll; click a text to
retype it. The text is a custom shape with sh["text"] (notes/text.py); it's rebuilt on every key."""

from files.lang import tr
from notes.custom import custom_settings
from notes.text import build, from_roll, layout, new_axes, restyle, text_axes, text_font, with_arial
from roll.roll_shared import CTRL, SHIFT

BLINK_MS = 530


class TextTyping:
    """Mixed into PianoRoll. self.typing: None, or {"i": the text's shape number (None while it's still empty),
    "caret": where in the text, "anchor": the other end of the selection (== caret: nothing selected), "tx" / "axes": the settings and place while there's no shape (only spaces or
    nothing typed yet), "undo": its undo step is taken}."""

    def text_at(self, x, y):
        """The text shape whose box is under (x, y) on screen, or None."""
        for i in range(len(self.app.shapes) - 1, -1, -1):
            sh = self.app.shapes[i]
            if sh.get("text"):
                corners = [(self.t2x(b), self.p2y(p)) for b, p in self.custom_corners(sh)]
                if self.inside_box(corners, x, y):
                    return i
        return None

    def typing_state(self):
        """(settings with the text, axes) of the text being typed."""
        ty = self.typing
        if ty["i"] is None:
            return ty["tx"], ty["axes"]
        sh = self.app.shapes[ty["i"]]
        return sh["text"], text_axes(sh)

    def text_click(self, e):
        """Text tool click: on a text = the caret there (Shift = select up to there; dragging selects),
        elsewhere = a new text starting at the (snapped) spot."""
        app = self.app
        i = self.text_at(e.x, e.y)
        self.drag = ("textsel",)
        if self.typing and i is not None and i == self.typing["i"]:
            ty = self.typing
            ty["caret"] = self.caret_at(e)
            if not e.state & SHIFT:
                ty["anchor"] = ty["caret"]
            return self.show_caret()
        self.end_typing()
        if i is not None:
            app.select(i)
            self.typing = {"i": i, "caret": 0, "anchor": 0, "tx": None, "axes": None, "undo": False}
            self.typing["caret"] = self.typing["anchor"] = self.caret_at(e)
        else:
            app.select(None)
            b, p = self.event_pt(e)
            tx = dict(app.text_defaults, text="", k=self.sy / self.sx)
            tx["cap"] = text_font(tx).cap
            self.typing = {"i": None, "caret": 0, "anchor": 0, "tx": tx, "axes": new_axes(tx, b, p, tx["k"]),
                           "undo": False}
        app.sync_text()
        self.show_caret()

    def text_drag(self, e):
        """Dragging after a Text tool click: selects from the click to the mouse."""
        if self.typing:
            self.typing["caret"] = self.caret_at(e)
            self.show_caret()

    def text_double(self, e):
        """Text tool double-click in the text being typed: selects the word there."""
        if not self.typing or self.text_at(e.x, e.y) != self.typing["i"] or self.typing["i"] is None:
            return self.on_press(e)
        text, c = self.typing_state()[0]["text"], self.caret_at(e)
        word = lambda ch: ch.isalnum() or ch == "_"
        kind = word(text[c]) if c < len(text) else word(text[c - 1]) if c else False
        a = c
        while a > 0 and text[a - 1] != "\n" and word(text[a - 1]) == kind:
            a -= 1
        b = c
        while b < len(text) and text[b] != "\n" and word(text[b]) == kind:
            b += 1
        self.typing["anchor"], self.typing["caret"] = a, b
        self.show_caret()

    def text_selection(self):
        """(start, end) of the selected characters (start == end: nothing selected)."""
        ty = self.typing
        return min(ty["anchor"], ty["caret"]), max(ty["anchor"], ty["caret"])

    def edit_text(self, e):
        """Double-click on a text with the Select tool: the Text tool, typing in it."""
        self.app.tool.set("text")
        self.text_click(e)

    def typing_empty(self):
        """A new text with nothing to see yet (or typed and erased again): its undo step changes nothing, so Ctrl+Z
        only takes the caret away (it took back an older step)."""
        ty = self.typing
        return bool(ty) and ty["i"] is None and (not ty["undo"] or bool(ty.get("new")))

    def end_typing(self):
        if not self.typing:
            return
        self.typing = None
        if self._caret_job:
            self.after_cancel(self._caret_job)
            self._caret_job = None
        self.delete("caret")
        self.app.sync_text()
        self.app.sync_custom()

    # ------------------------------------------------------------ keys

    def type_key(self, e):
        """A key while typing. "break" = it was the text's (so the window's shortcuts don't run too)."""
        app, ty = self.app, self.typing
        k = e.keysym
        text, c = self.typing_state()[0]["text"], ty["caret"]
        s0, s1 = self.text_selection()
        if e.state & CTRL:
            low = k.lower()
            if low == "z" and self.typing_empty():  # (only the caret goes, like Ctrl+Z mid-draw)
                return self.end_typing() or "break"
            if low in ("z", "y"):
                self.end_typing()
                app.undo() if low == "z" else app.redo()
                return "break"
            if low == "a":  # all of this text, not every shape
                ty["anchor"], ty["caret"] = 0, len(text)
            elif low in ("c", "x"):
                if s0 == s1:
                    return None if low == "c" else "break"  # nothing selected: Ctrl+C copies the shape
                self.clipboard_clear()
                self.clipboard_append(text[s0:s1])
                if low == "x":
                    self.insert_text("")
            elif low == "v":
                try:
                    clip = self.clipboard_get()
                except Exception:  # nothing (or no text) on the clipboard
                    return "break"
                self.insert_text(clip.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " "))
            else:
                return None  # Ctrl+S, flips, turns, ...: the window's shortcuts
            self.show_caret()
            return "break"
        if k == "Escape":
            return self.end_typing() or "break"
        if k in ("Return", "KP_Enter"):
            self.insert_text("\n")
        elif k in ("BackSpace", "Delete") and s0 != s1:
            self.insert_text("")
        elif k == "BackSpace":
            if c:
                self.set_text(text[:c - 1] + text[c:], c - 1)
        elif k == "Delete":
            if c < len(text):
                self.set_text(text[:c] + text[c + 1:], c)
        elif k in ("Left", "Right", "Home", "End", "Up", "Down"):
            if k in ("Left", "Right") and s0 != s1 and not e.state & SHIFT:
                c = s0 if k == "Left" else s1  # a selection: to its start / end
            elif k in ("Left", "Right"):
                c = max(0, min(len(text), c + (1 if k == "Right" else -1)))
            elif k in ("Home", "End"):
                start = text.rfind("\n", 0, c) + 1
                end = text.find("\n", c)
                c = start if k == "Home" else len(text) if end < 0 else end
            else:
                lines = text.split("\n")
                n = text.count("\n", 0, c)
                col = c - (text.rfind("\n", 0, c) + 1)
                m = n + (1 if k == "Down" else -1)
                if 0 <= m < len(lines):
                    c = sum(len(l) + 1 for l in lines[:m]) + min(col, len(lines[m]))
            ty["caret"] = c
            if not e.state & SHIFT:  # Shift = select up to there
                ty["anchor"] = c
        elif e.char and (e.char >= " " and e.char != "\x7f"):
            self.insert_text(e.char)
        self.show_caret()
        return "break"

    def insert_text(self, s):
        """Types s at the caret, in place of the selected characters."""
        text = self.typing_state()[0]["text"]
        s0, s1 = self.text_selection()
        self.set_text(text[:s0] + s + text[s1:], s0 + len(s))

    def set_text(self, text, caret):
        """The typed text changed: rebuild the shape (made on the first letter you can see, removed again when only
        spaces are left)."""
        app, ty = self.app, self.typing
        tx, axes = self.typing_state()
        if ty["i"] is not None and not text_font(tx).found:  # (a shared text whose font isn't installed here)
            if not app.missing_font_ok([tx]):
                self.end_typing()
                app.sync_text()
                return
            tx, axes = restyle(tx, axes, with_arial(tx, {}))
        tx = dict(tx, text=text)
        ty["caret"] = ty["anchor"] = caret
        if ty["i"] is not None:
            sh = app.shapes[ty["i"]]
            if not ty["undo"]:
                ty["was"] = sh.get("name") or "?"  # (the History says what the text was if it's all erased)
                app.push_undo(name=tr("roll_text.type", text=ty["was"]))
                ty["undo"], ty["step"], ty["new"] = True, app.undo_stack[-1], False
            if build(sh, tx, axes):
                app.shapes_changed()
            else:  # nothing to see any more: the shape goes, the typing stays
                ty["tx"], ty["axes"] = tx, axes
                del app.shapes[ty["i"]]
                ty["i"] = None
                app.select(None)
                app.shapes_changed()
        else:
            sh = dict(app.defaults, kind="custom", **custom_settings(app.custom_defaults))
            if build(sh, tx, axes):
                ty["i"] = len(app.shapes)  # before add_shape selects it (selecting another shape ends the typing)
                if ty["undo"]:
                    app.shapes.append(sh)
                    app.select(ty["i"])
                    app.shapes_changed()
                else:
                    app.add_shape(sh)
                    ty["undo"], ty["step"], ty["new"] = True, app.undo_stack[-1], True
            else:
                ty["tx"] = tx
        self.name_typing_step()
        app.sync_title()
        app.sync_custom()

    def name_typing_step(self):
        """The History name of this typing's undo step follows the text (it was named after the first letter)."""
        app, ty = self.app, self.typing
        step = ty.get("step")
        if not step or not app.undo_stack or app.undo_stack[-1] is not step:
            return  # (another step came after it)
        if ty["i"] is not None:
            sh = app.shapes[ty["i"]]
            name = (tr("app.draw", shape_label=app.shape_label(sh)) if ty.get("new") else
                    tr("roll_text.type", text=sh.get("name") or "?"))
        elif ty.get("new"):
            return  # (a new text typed and erased again: the step changes nothing)
        else:
            name = tr("roll_text.erase", text=ty["was"])
        if name != step[1]:
            ty["step"] = app.undo_stack[-1] = (step[0], name) + step[2:]
            app.sync_history()

    def retype(self, tx, axes):
        """New settings (the panel) for the text being typed."""
        ty = self.typing
        if ty["i"] is None:
            ty["tx"], ty["axes"] = tx, axes
            self.set_text(tx["text"], ty["caret"])
        else:
            build(self.app.shapes[ty["i"]], tx, axes)
            ty["undo"] = False  # the panel took its own undo step: the next key starts a new one
        self.show_caret()

    # ------------------------------------------------------------ caret

    def caret_spots(self):
        """[(x, baseline)] in em units for every caret position (see text.layout)."""
        tx, _ = self.typing_state()
        return layout(tx)[1]

    def caret_at(self, e):
        """The caret position nearest to the mouse."""
        tx, axes = self.typing_state()
        em = from_roll(axes, self.x2t(e.x), self.y2p(e.y))
        if em is None:
            return len(tx["text"])
        spots = layout(tx)[1]
        font = text_font(tx)
        mid = (font.ascent - font.descent) / 2
        line_of = [tx["text"].count("\n", 0, j) for j in range(len(tx["text"]) + 1)]
        base = {line_of[j]: y for j, (_, y) in enumerate(spots)}
        n = min(base, key=lambda n: abs(em[1] - base[n] - mid))
        return min((j for j in range(len(spots)) if line_of[j] == n), key=lambda j: abs(em[0] - spots[j][0]))

    def show_caret(self, on=True):
        """Draw the caret (on) and keep it blinking."""
        if self._caret_job:
            self.after_cancel(self._caret_job)
            self._caret_job = None
        self.delete("caret")
        if not self.typing or self.sx is None:
            return
        self.draw_typing(on)
        self._caret_job = self.after(BLINK_MS, lambda: self.show_caret(not on))

    def draw_typing(self, caret):
        """The dashed box around the text being typed, the selected characters' highlight and (caret) the caret."""
        tx, axes = self.typing_state()
        spots = layout(tx)[1]
        font = text_font(tx)
        O, X, Y = axes
        xy = lambda x, y: (self.t2x(O[0] + x * X[0] + y * Y[0]), self.p2y(O[1] + x * X[1] + y * Y[1]))
        up, down = font.ascent, font.descent

        def rect(x0, x1, y0, y1, **kw):  # a box in em units (turns / slants with the text)
            pts = [xy(x0, y0), xy(x1, y0), xy(x1, y1), xy(x0, y1)]
            return self.create_polygon([v for p in pts for v in p], tags="caret", **kw)

        pad = 0.08
        xs, ys = [x for x, _ in spots], [y for _, y in spots]
        rect(min(xs) - pad, max(xs) + pad, min(ys) - down - pad, max(ys) + up + pad, fill="",
             outline="#3a7bd5", dash=(4, 3), width=max(1, round(self.scale)))
        s0, s1 = self.text_selection()
        text = tx["text"]
        for j in range(s0, s1):
            x0, y = spots[j]
            x1 = spots[j + 1][0] if text[j] != "\n" else x0 + 0.25  # a selected line break: a little stub
            rect(x0, x1, y - down, y + up, fill="#3a7bd5", outline="", stipple="gray50")
        if caret and s0 == s1:
            x, y = spots[min(self.typing["caret"], len(spots) - 1)]
            self.create_line(*xy(x, y - down), *xy(x, y + up), fill="#000000",
                             width=max(2, round(2 * self.scale)), tags="caret")
