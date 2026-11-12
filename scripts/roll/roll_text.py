"""Piano roll: the Text tool. Click to put a blinking caret there and type straight onto the roll; click a text to
retype it. The text is a custom shape with sh["text"] (notes/text.py); it's rebuilt on every key."""

import json
import math
import unicodedata

from files import system
from files.lang import tr
from notes.custom import custom_settings
from notes.text import (anchor_spot, build, caret_line, caret_snap, caret_step, from_roll, layout, letter_matrix,
                        line_stops, missing_letters, new_axes, no_joining, onto_anchor, rebuild, restyle, text_axes,
                        text_font, with_arial)
from roll.roll_shared import CTRL, SHIFT
from window import look

BLINK_MS = 530
IME_MS = 40  # how often the typing helper is asked for a half-typed letter while typing (Windows)
IME_WAIT = 1  # a change shows after it held this many more asks (~40 ms; 2 = 80 ms didn't help the flashing: that
# was IME_KEY presses dropping the half-typed letter)
IME_ROOM, IME_WIDE = 28, 120  # px the typing helper's own box takes (about), kept inside the piano roll
IME_KEY = 229 # Windows' key code for a key the typing helper takes (VK_PROCESSKEY); Tk passes it on as a key press


class TextTyping:
    """Mixed into PianoRoll. self.typing: None, or {"i": the text's shape number (None while it's still empty),
    "caret": where in the text, "anchor": the other end of the selection (== caret: nothing selected), "tx" / "axes": the settings and place while there's no shape (only spaces or
    nothing typed yet), "undo": its undo step is taken}."""
    _ime_job = None

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
        self.drop_composing()  # (a half-typed letter: thrown away, as in Windows' own boxes when clicking elsewhere)
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
                           "undo": False, "at": (b, p)}  # (at: the text's first note starts here, onto_anchor)
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
        word = self.word_test(text, c)
        kind = word(text[c]) if c < len(text) else word(text[c - 1]) if c else False
        a = c
        while a > 0 and text[a - 1] != "\n" and word(text[a - 1]) == kind:
            a -= 1
        b = c
        while b < len(text) and text[b] != "\n" and word(text[b]) == kind:
            b += 1
        self.typing["anchor"], self.typing["caret"] = a, b
        self.show_caret()

    def word_test(self, text, c):
        """ch -> True when ch is part of a word (double-click, Ctrl+Backspace / Ctrl+Delete)."""
        marks = line_stops(text, c, self.typing_down())[0] is not None  # (joined lines: marks are part of a word)
        # (joined lines: the zero-width joiner / non-joiner are inside words too, Persian types them mid-word)
        return lambda ch: (ch.isalnum() or ch == "_"
                           or (marks and (unicodedata.category(ch)[0] == "M" or ch in "‌‍")))

    def word_end(self, text, c, forward):
        """Where Ctrl+Delete (forward) / Ctrl+Backspace (back) erase up to, as in Windows' boxes: back = over the
        spaces / signs before the caret, then the word before them; forward = the word (or signs) after the caret,
        then the spaces after it. A line break next to the caret is erased alone."""
        word = self.word_test(text, c)
        if forward:
            if c >= len(text):
                return c
            if text[c] == "\n":
                return c + 1
            kind = word(text[c])
            while c < len(text) and text[c] not in " \n" and word(text[c]) == kind:
                c += 1
            while c < len(text) and text[c] == " ":
                c += 1
            return c
        if c <= 0:
            return c
        if text[c - 1] == "\n":
            return c - 1
        while c > 0 and text[c - 1] == " ":
            c -= 1
        if c > 0 and text[c - 1] != "\n":
            kind = word(text[c - 1])
            while c > 0 and text[c - 1] not in " \n" and word(text[c - 1]) == kind:
                c -= 1
        return c

    def typing_down(self):
        """The text being typed has Direction Down (never joined: its caret moves as for Latin)."""
        return self.typing_state()[0].get("direction", "roll") == "down"

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
        self.drop_composing()
        self.typing = None
        if self._ime_job:
            self.after_cancel(self._ime_job)
            self._ime_job = None
        if self._caret_job:
            self.after_cancel(self._caret_job)
            self._caret_job = None
        self.delete("caret")
        self.app.sync_text()
        self.app.sync_custom()

    # ------------------------------------------------------------ keys

    # ------------------------------------------------------------ unfinished letters (Korean, Chinese, Japanese input)
    # Windows' typing helper holds a half-typed syllable (ㄱ -> 가 -> 각) and Tk only passes the finished letter
    # on, so the text asks the helper every IME_MS while typing (system.ime_composing) and shows the unfinished part
    # in the text as notes (user 2026-11-12), inside the typing's own undo step. ty["comp"] = (the text without
    # it, selection start, end, what's shown). The finished letter's key puts it in for real; anything else first
    # takes the unfinished part away. Elsewhere (Linux / Mac): None, finished letters only (untested there).

    def ime_start(self):
        if self._ime_job is None and system.WINDOWS:
            self._ime_job = self.after(IME_MS, self.ime_poll)

    def ime_poll(self):
        self._ime_job = None
        if not self.typing:
            return
        got = system.ime_composing()
        if got is None:
            return
        self.show_composing(got)
        self._ime_job = self.after(IME_MS, self.ime_poll)

    def show_composing(self, got):
        ty = self.typing
        comp = ty.get("comp")
        if got == (comp[3] if comp else ""):
            ty.pop("next", None)
            return
        # (a change shows once it has held for IME_WAIT more asks: the helper finishes a syllable and starts the next
        # one at once, but the finished letter's key reaches the text a moment later; acting at once showed the text
        # without it, then with it again: Korean flashed in and out, user 2026-11-12; 1 ask less but still some)
        seen, held = ty.get("next") or (None, 0)
        if seen != got:
            ty["next"] = (got, 1)
            return
        if held < IME_WAIT:
            ty["next"] = (got, held + 1)
            return
        ty.pop("next", None)
        if comp:
            real, s0, s1 = comp[:3]
        else:
            real = self.typing_state()[0]["text"]
            s0, s1 = self.text_selection()
        if got:
            ty["comp"] = (real, s0, s1, got)
            self.set_text(real[:s0] + got + real[s1:], s0 + len(got))
        else:  # (taken back in the helper: the text as it was, selection too)
            ty.pop("comp")
            self.set_text(real, s0)
            ty["anchor"], ty["caret"] = s0, s1
        self.show_caret()

    def drop_composing(self, cancel=True):
        """The text without the unfinished part again (cancel: the helper forgets it too)."""
        comp = self.typing and self.typing.pop("comp", None)
        if comp:
            real, s0, s1, _ = comp
            if cancel:
                system.ime_cancel()
            self.set_text(real, s0)
            self.typing["anchor"], self.typing["caret"] = s0, s1
        return comp

    def type_key(self, e):
        """A key while typing. "break" = it was the text's (so the window's shortcuts don't run too)."""
        app, ty = self.app, self.typing
        k = e.keysym
        if getattr(e, "keycode", None) == IME_KEY and not e.char:
            # (a key the typing helper is using to put a letter together: it's the helper's, not the text's; taken
            # as "another key", it dropped the half-typed letter until the next ask: Korean flashed, user's video)
            return "break"
        if ty.get("comp"):
            if e.char and e.char >= " " and e.char != "\x7f" and not e.state & CTRL:  # (the finished letter)
                real, s0, s1, _ = ty.pop("comp")
                self.set_text(real[:s0] + e.char + real[s1:], s0 + len(e.char))
                self.show_caret()
                return "break"
            self.drop_composing(cancel=False)
        text, c = self.typing_state()[0]["text"], ty["caret"]
        down = self.typing_down()
        s0, s1 = self.text_selection()
        if e.state & CTRL:
            low = k.lower()
            if low == "z" and not e.state & SHIFT and self.typing_empty():  # (only the caret goes, like Ctrl+Z mid-draw)
                return self.end_typing() or "break"
            if low in ("z", "y"):
                self.end_typing()
                app.undo() if low == "z" and not e.state & SHIFT else app.redo()  # (Ctrl+Shift+Z = redo too)
                return "break"
            if k in ("Left", "Right"):  # a word on (the caret is in the text: only selected, Ctrl+arrows turn it)
                forward = (k == "Right") != line_stops(text, c, down)[1]  # (right to left lines: swapped)
                ty["caret"] = self.word_end(text, c, forward)
                if not e.state & SHIFT:
                    ty["anchor"] = ty["caret"]
            elif k in ("BackSpace", "Delete"):  # a whole word (a selection: just that, as without Ctrl)
                if s0 != s1:
                    self.insert_text("")
                elif k == "Delete":
                    self.set_text(text[:c] + text[self.word_end(text, c, True):], c)
                else:
                    a = self.word_end(text, c, False)
                    self.set_text(text[:a] + text[c:], a)
            elif low == "a":  # all of this text, not every shape
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
        elif k == "Delete":  # (a joined line: the whole letter group, as Windows does; Backspace = one character)
            if c < len(text):
                self.set_text(text[:c] + text[caret_step(text, c, True, down):], c)
        elif k in ("Left", "Right", "Home", "End", "Up", "Down"):
            # (a right-to-left line, as Windows' boxes: Left = on in the typed text (reading order), Right = back)
            forward = (k == "Right") != line_stops(text, c, down)[1]
            if k in ("Left", "Right") and s0 != s1 and not e.state & SHIFT:
                c = s1 if forward else s0  # a selection: to its end / start
            elif k in ("Left", "Right"):
                c = caret_step(text, c, forward, down)
            elif k in ("Home", "End"):
                start, end = caret_line(text, c)
                c = start if k == "Home" else end
            else:
                c = self.caret_line_jump(text, c, 1 if k == "Down" else -1)
            ty["caret"] = c
            if not e.state & SHIFT:  # Shift = select up to there
                ty["anchor"] = c
        elif e.char and (e.char >= " " and e.char != "\x7f"):
            self.insert_text(e.char)
        self.show_caret()
        return "break"

    def caret_line_jump(self, text, c, way):
        """Up / Down (way -1 / +1): the same column on the line above / below, as before; when either line is
        joined (right to left / joined letters) the caret stop nearest the same spot along the line instead."""
        lines = text.split("\n")
        n = text.count("\n", 0, c)
        m = n + way
        if not 0 <= m < len(lines):
            return c
        start = sum(len(l) + 1 for l in lines[:m])
        down = self.typing_down()
        if line_stops(text, c, down)[0] is None and line_stops(text, start, down)[0] is None:
            col = c - (text.rfind("\n", 0, c) + 1)
            return start + min(col, len(lines[m]))
        tx = self.typing_state()[0]
        places = self.typing_places(tx)
        along = 1 if tx.get("direction", "roll") == "across" else 0  # (the letters run along x, or up the keys)
        spot = lambda j: (places[j]["caret"][0][along] + places[j]["caret"][1][along]) / 2
        here = spot(c)
        stops = line_stops(text, start, down)[0] or range(start, start + len(lines[m]) + 1)
        return min(stops, key=lambda j: (abs(spot(j) - here), j))

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
            at = anchor_spot(sh, app.notes_of, app.ppq)
            if rebuild(sh, tx, axes, app.notes_of, app.ppq):
                app.shapes_changed()
            else:  # nothing to see any more: the shape goes, the typing stays
                ty["tx"], ty["axes"], ty["at"] = tx, axes, at
                # (its own settings, Hz bass too, wait for the letters typed next, at its place in the list)
                ty["kept"] = (ty["i"], {k: v for k, v in sh.items() if k not in ("strokes", "pts", "text", "name")})
                del app.shapes[ty["i"]]
                ty["i"] = None
                app.select(None)
                app.shapes_changed()
        else:
            at, kept = ty.get("kept") or (None, None)
            sh = dict(kept) if kept else dict(app.defaults, kind="custom",
                                              **custom_settings(app.custom_defaults, patterns=False))
            if build(sh, tx, axes, app.ppq):
                if ty.get("at"):
                    onto_anchor(sh, ty["at"], app.notes_of, app.ppq)
                ty.pop("kept", None)
                at = len(app.shapes) if at is None or not ty["undo"] else min(at, len(app.shapes))
                ty["i"] = at  # before add_shape selects it (selecting another shape ends the typing)
                if ty["undo"]:
                    app.shapes.insert(at, sh)
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
        self.tell_missing(tx)

    def tell_missing(self, tx):
        """The status line names the letters no installed font has (they show as boxes or "?")."""
        gone = missing_letters(tx)
        if gone:
            self.app.status.config(text=tr("roll_text.no_font_has", letters=gone[:20]))
        elif tx.get("direction") != "down" and no_joining(tx["text"]):
            from notes.shaping import too_old
            old = too_old()  # (installed but too old: say so, "not installed" looked wrong)
            self.app.status.config(text=tr("roll_text.joining_too_old", names=" + ".join(old)) if old else
                                   tr("roll_text.no_joining"))

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
            rebuild(self.app.shapes[ty["i"]], tx, axes, self.app.notes_of, self.app.ppq)
            ty["undo"] = False  # the panel took its own undo step: the next key starts a new one
            self.tell_missing(tx)
        self.show_caret()

    # ------------------------------------------------------------ caret

    def caret_at(self, e):
        """The caret position nearest to the mouse."""
        tx, axes = self.typing_state()
        em = from_roll(axes, self.x2t(e.x), self.y2p(e.y))
        if em is None:
            return len(tx["text"])
        places = self.typing_places(tx)
        text = tx["text"]
        for j, pl in enumerate(places):  # (joined lines: inside a letter, the half clicked picks its side)
            if "rtl" not in pl or j >= len(text) or text[j] == "\n":
                continue
            p0, p1, _, p3 = pl["cell"]
            ux, uy, vx, vy = p1[0] - p0[0], p1[1] - p0[1], p3[0] - p0[0], p3[1] - p0[1]
            det = ux * vy - uy * vx
            if abs(det) < 1e-12:
                continue
            dx, dy = em[0] - p0[0], em[1] - p0[1]
            u, v = (dx * vy - dy * vx) / det, (ux * dy - uy * dx) / det  # (where in the letter's room, 0..1)
            if 0 <= u <= 1 and 0 <= v <= 1:
                return caret_snap(text, j if (u < 0.5) != pl["rtl"] else j + 1)

        def away(j):  # (from the caret line there)
            (xa, ya), (xb, yb) = places[j]["caret"]
            dx, dy = xb - xa, yb - ya
            t = max(0.0, min(1.0, ((em[0] - xa) * dx + (em[1] - ya) * dy) / (dx * dx + dy * dy or 1.0)))
            return math.hypot(em[0] - xa - t * dx, em[1] - ya - t * dy)
        near = min(range(len(places)), key=away)
        return caret_snap(text, near) if "rtl" in places[near] else near

    def show_caret(self, on=True):
        """Draw the caret (on) and keep it blinking."""
        if self._caret_job:
            self.after_cancel(self._caret_job)
            self._caret_job = None
        self.delete("caret")
        if not self.typing or self.sx is None:
            return
        self.ime_start()
        self.draw_typing(on)
        self._caret_job = self.after(BLINK_MS, lambda: self.show_caret(not on))

    _places = (None, None)

    def typing_places(self, tx):
        """layout(tx)'s places, kept for the last settings: the caret blinks and is drawn twice a key, and laying out
        a long text again each time took ~25 ms (800 letters)."""
        key = json.dumps(tx, sort_keys=True, default=str)
        if self._places[0] != key:
            self._places = (key, layout(tx)[1])
        return self._places[1]

    def draw_typing(self, caret):
        """The dashed box around the text being typed, the selected characters' highlight and (caret) the caret."""
        tx, axes = self.typing_state()
        places = self.typing_places(tx)
        O, X, Y = axes
        xy = lambda x, y: (self.t2x(O[0] + x * X[0] + y * Y[0]), self.p2y(O[1] + x * X[1] + y * Y[1]))

        def poly(pts, **kw):  # em points (turns / slants with the text)
            return self.create_polygon([v for p in pts for v in xy(*p)], tags="caret", **kw)

        pad = 0.08
        xs = [x for pl in places for x, _ in pl["cell"]]
        ys = [y for pl in places for _, y in pl["cell"]]
        x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
        poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], fill="", outline=look.TEXT_BOX, dash=(4, 3),
             width=max(1, round(self.scale)))
        s0, s1 = self.text_selection()
        text = tx["text"]
        for j in range(s0, s1):
            cell = places[j]["cell"]
            if text[j] == "\n" and tx.get("direction", "roll") != "down":  # a selected line break: a little stub
                (ax, ay), (bx, by) = places[j]["caret"]
                ux, uy = letter_matrix(tx)[0] * 0.25, letter_matrix(tx)[2] * 0.25
                cell = [(ax, ay), (ax + ux, ay + uy), (bx + ux, by + uy), (bx, by)]
            poly(cell, fill=look.TEXT_BOX, outline="", stipple="gray50")
        a, b = places[min(self.typing["caret"], len(places) - 1)]["caret"]
        if caret and s0 == s1:
            self.create_line(*xy(*a), *xy(*b), fill=look.TEXT_CARET, width=max(2, round(2 * self.scale)), tags="caret")
        # where the caret is, for the system's typing helper: its own box (Korean / Japanese / Chinese input) and its
        # list of choices (Japanese kanji, Korean Hanja) open there, not in the window's top-left corner (user
        # 2026-11-12: a black box by the File menu; Japanese choices up there)
        # JUST UNDER THE WHOLE TEXT (the dashed box), at the caret's x: the helper asks where the instant a key is
        # pressed, before the next ask draws the new letter, so at the caret itself its list opened where the caret
        # had been and covered the letters (user's pictures); a line grows sideways, so "under the text" holds
        (ax, ay), (bx, by) = xy(*a), xy(*b)
        corners = [xy(*p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        box = (min(x for x, _ in corners), min(y for _, y in corners), max(x for x, _ in corners),
               max(y for _, y in corners))
        # (always under the text, at most down to the piano roll's bottom edge: under a text reaching past it the box
        # went off the window; ABOVE the text was tried, but the helper opens its list downward over the text anyway,
        # user 2026-11-12)
        w, h, room = self.winfo_width(), self.winfo_height(), IME_ROOM * self.scale
        spot_y = max(0, min(box[3] + 2, h - room))
        spot_x = max(0, min(min(ax, bx), w - IME_WIDE * self.scale))
        try:
            self.tk.call("tk", "caret", self._w, "-x", int(round(spot_x)), "-y", int(round(spot_y)),
                         "-height", max(1, int(round(abs(by - ay)))))
        except Exception:  # (a Tk without "tk caret": the helper keeps its own place)
            pass
        system.ime_choices_beside(self, spot_x, spot_y, box)  # (and never over the text)
