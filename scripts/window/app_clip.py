"""Mixed into App: Copy / Cut / Paste of shapes (the program's clipboard, shared text, Domino)."""

import copy
from files.domino_clip import FORMAT as DOMINO_FORMAT, on_clipboard
from files.share import FIND, LONG_LINE, ShareError, made_by, read_shapes, shapes_line, unpack
from files.clipboard import RAW, copy_count, get_text, put_text_and, registered
from window import big_ask
from roll.roll_shared import cached_path
from files import clipboard, errors, notes_clip
from notes.engine import merges_unmade
from tkinter import messagebox
from files.lang import tr, trn


class AppClip:
    def copy_selected(self, whole=False, cut=False):
        """Ctrl+C: the selected shapes (or with curves highlighted: the curve's shape; a stroke picked: it). The
        Tempo pane clicked last: its points and catch-ups. Their CC events only when picked by time (without_cc);
        cut: always (cut + paste = a move, the CC events would be gone with the shape)."""
        if not whole and getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.copy()
        if not whole and self.roll.curve_parts():
            return self.roll.copy_curve()
        if not whole and self.picked():
            return self.roll.copy_stroke(*self.picked())
        if self.sels:
            self.colour_edit_leave()  # (a colour step being edited: each colour's row from the whole result)
            shapes = [sh if cut else self.without_cc(sh)
                      for sh in copy.deepcopy([self.shapes[i] for i in sorted(self.sels)])]
            cols = self.copy_columns(sorted(self.sels))  # (each colour's row: pasted the same way, like Domino)
            for i, sh in zip(sorted(self.sels), shapes):
                sh.pop("cols", None)
                if i in cols:
                    sh["cols"] = cols[i]
            self.put_clipboard(shapes)

    def put_clipboard(self, shapes, loaded=False, tempo=None):
        """shapes (copies, their "cols" set) are what Ctrl+V pastes now (also the track list's Ctrl+C; loaded: its
        boxes held a loaded MIDI's notes, which never go into a copy for other programs: said, hunt 2026-10-29).
        tempo: the Conductor's changes copied with them (the track list's boxes, track_tempo): the tempo copy too,
        pasted along with these shapes (track_list.key_paste)."""
        self.cancel_copy()  # (an older copy's text still being made: this one wins)
        for sh in shapes:
            sh["into_from"] = self.project_token  # (its tracks are this project's: put_into_current)
        self.clipboard = shapes
        self.clip_kind = "shapes"
        self.shapes_stamp = self.copy_stamp()
        self.remember_clip()  # (what the clipboard holds now is older than this copy, also while its text is made)
        if tempo is not None:
            tempo["with"], tempo["n"] = shapes, self.shapes_stamp
            self.tempo_clip = tempo
        msg = trn("app.copied_shape_s_ctrl_v_pastes", len(self.clipboard), n=len(self.clipboard))
        if tempo is not None:
            from window.track_tempo import count
            msg += trn("app.copied_tempo_too", count(tempo), n=count(tempo))
        also, domino, notes, why = [], None, None, ""
        # (what both extras copy, worked out once: a big song's Keep timing took twice as long, hunt 2026-10-29)
        got = self.copy_music() if RAW and (self.copy_domino.get() or self.copy_notes.get()) else False
        if self.copy_domino.get():  # (Settings → General: Copy to Domino's notes beside it, never a question)
            domino = self.domino_copy(quiet=True, got=got)
            if isinstance(domino, tuple):
                also.append((registered(DOMINO_FORMAT), domino[0]))
        if self.copy_notes.get():  # (... and Spiderweb Notes, for other programs: files/notes_clip.py)
            try:
                notes, why = self.notes_copy(got=got)
            except Exception as e:  # (the copy itself still goes on the clipboard)
                errors.write_log(type(e), e, e.__traceback__, "notes_copy")
                notes, why = None, "error"
            if notes:
                also.append((registered(notes_clip.NAME), notes))
            elif loaded and not why:
                why = "loaded"
        # the shared text (also on the Windows clipboard, to share: share.py) made on a thread: ~1 s at millions of
        # pasted notes (copy_wait); done quickly = finished here, as before
        from window.copy_wait import CopyJob
        job = CopyJob(self, lambda: shapes_line(shapes),
                      lambda j: self._copy_made(j, also, msg, domino, notes, why, loaded))
        if not job.quick():
            self._copy_job = job
            self.status.config(text=tr("app.copying_clip"))

    def _copy_made(self, job, also, msg, domino, notes, why, loaded):
        """put_clipboard's shared text is made: onto the Windows clipboard with the extras, unless something else
        went on it since the copy (copied in another program, Copy to Domino...: that's newer, kept)."""
        if getattr(self, "_copy_job", None) is job:
            self._copy_job = None
        if job.error is not None and not isinstance(job.error, MemoryError):
            raise job.error
        line = job.line
        if copy_count() != self.seen_clip:
            return self.status.config(text=msg)
        try:
            shared, extra = put_text_and(line, also) if line is not None else (False, False)
        except MemoryError:
            shared, extra = False, False
        self.remember_clip()
        if shared:
            if len(line) > LONG_LINE:
                msg += tr("app.too_long", chars=len(line))
        else:
            msg += tr("app.couldn_t_share")
        if isinstance(domino, tuple) and extra:
            self.own_domino_copy = self.seen_clip  # (pasted straight back: its tempo isn't asked about)
            msg += tr("app.copied_for_domino_too")
        elif domino:  # (no room on the clipboard / it would have asked or failed)
            msg += tr("app.not_copied_for_domino")
        if notes and extra:
            msg += tr("app.copied_as_notes_too") + (tr("load.domino_short") if loaded else "")
        elif notes:  # (made, but the clipboard had no room for it)
            msg += tr("app.not_copied_as_notes_room")
        elif why:  # (left out: off Windows, too far apart, out of memory, an error (errors.log), loaded notes only)
            msg += tr("app.not_copied_as_notes_" + why)
        self.status.config(text=msg)

    def cut_selected(self, whole=False):
        """Ctrl+X: what Ctrl+C and Delete work on (a funnel's highlighted curves, a picked stroke, else the selected
        shapes) copied, then deleted. whole (the menu's Cut): always the shapes."""
        if not whole and getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.cut()
        if not whole and self.roll.curve_parts():
            self.roll.copy_curve()
            return self.roll.delete_parts()
        if not whole and self.picked():
            self.roll.copy_stroke(*self.picked())
            return self.roll.delete_stroke(*self.picked())
        if self.sels:
            self.copy_selected(whole=True, cut=True)
            self.delete_selected(name=tr("app.cut"))

    def finish_copy(self):
        """A Ctrl+C's shared text still being made (copy_wait): waited for here, so it's on the clipboard (Spiderweb's
        own paste never runs beside half a copy; closing keeps it)."""
        job = getattr(self, "_copy_job", None)
        if job is not None:
            job.finish()

    def cancel_copy(self):
        """... dropped instead: something newer goes on the clipboard."""
        job = getattr(self, "_copy_job", None)
        self._copy_job = None
        if job is not None:
            job.drop()

    def copy_stamp(self):
        """A number for each copy (shapes / tempo), counting up: the track list's Ctrl+V pastes the newer."""
        self._copy_n = getattr(self, "_copy_n", 0) + 1
        return self._copy_n

    def remember_clip(self):
        """Spiderweb copied something: what the Windows clipboard holds now is older than that copy."""
        self.seen_clip = copy_count()

    def remember_own_copy(self):
        """A copy only Spiderweb keeps (a stroke, a curve's shape): with "Ctrl+C also copies…" ticked, the Windows
        clipboard is emptied if it still holds Spiderweb's last copy, so other programs don't paste those older notes
        (user's pick, 2026-10-20); something copied in another program since stays."""
        self.drop_own_copy()
        self.remember_clip()

    def drop_own_copy(self):
        """remember_own_copy's emptying alone (Hz window notes, Conductor pane copies: hunt 2026-10-29); what Ctrl+V
        pastes on the piano roll stays as it was."""
        self.cancel_copy()
        if RAW and (self.copy_domino.get() or self.copy_notes.get()) and copy_count() == self.seen_clip:
            clipboard.put_many([])  # (through the module: the tests' pretend clipboard replaces it there)

    def shared_clip(self, shapes_only=False):
        """The Windows clipboard's text if it holds a shared line put there after Spiderweb's last copy (so it's
        what was copied last; Windows' copy count tells, so the same line copied again counts too), "" if the
        clipboard is busy (another program has it open), else None. shapes_only: only if it's shapes that can
        be pasted."""
        if copy_count() == self.seen_clip:
            return None
        text = get_text()
        if text is None:
            return ""
        if not FIND.search(text):
            return None
        if shapes_only:
            try:
                got = unpack(text)
                if got["kind"] != "shapes":
                    return None
                with merges_unmade():  # (only whether it can be pasted)
                    read_shapes(got)
            except ShareError:
                return None
        return text

    def domino_newer(self):
        """True when the Windows clipboard holds notes copied in Domino, put there after Spiderweb's last copy (so
        they're what was copied last: paste takes them)."""
        return RAW and copy_count() != self.seen_clip and on_clipboard()

    def paste_shared(self, text, at=None):
        """Paste shapes shared as text (share.py), like paste; they become Spiderweb's copied shapes. Shapes left
        out, or a line made by another Spiderweb version: a heads-up after pasting (popup + status line)."""
        try:
            got = unpack(text)
            if got["kind"] == "drawing":
                messagebox.showinfo(tr("app.spiderweb_2"), tr("app.shared_drawing"), parent=self)
                return
            with merges_unmade():  # (merged shapes' notes made only once the count is OK'd: they can be millions)
                shapes, left = read_shapes(got)
        except ShareError as e:
            messagebox.showerror(tr("app.spiderweb_2"), tr("app.shared_newer") if e.why == "newer" else
                                 tr("app.shared_broken"), parent=self)
            return
        except MemoryError:
            messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=self)
            return
        if not big_ask.ask_shapes(self, len(shapes)):
            return
        top = self.last_step()
        try:
            if not self.confirm_big(shapes):
                return
            if any("merge" in sh for sh in shapes):
                try:
                    shapes, left = read_shapes(got)
                except ShareError:
                    messagebox.showerror(tr("app.spiderweb_2"), tr("app.shared_broken"), parent=self)
                    return
            clip = self.clipboard, self.clip_kind, self.seen_clip
            self.remember_clip()
            self.clipboard, self.clip_kind = shapes, "shapes"
            self.shapes_stamp = self.copy_stamp()  # (newer than a tempo copy: the track list's Ctrl+V)
            clamped = self.paste(whole=True, at=at)
        except Exception as e:  # (out of memory, or anything else: the shapes never stay half pasted, user)
            if self.last_step() is not top:  # (the paste's step: taken back, not kept for Ctrl+Y)
                kept = self._redo_kept
                self._restore(self.undo_stack, self.redo_stack)
                self.redo_stack.pop()
                if kept:
                    self.redo_stack[:] = kept[0]
                self.sync_history()
            if self.clipboard is shapes:  # (Ctrl+V again tries the line again, not an older copy)
                self.clipboard, self.clip_kind, self.seen_clip = clip
            if not isinstance(e, MemoryError):
                raise
            messagebox.showerror(tr("app.spiderweb_2"), tr("big_ask.out_of_memory"), parent=self)
            return
        made = made_by(got)
        notes = ([trn("app.shared_left_out", left, n=left)] if left else []) + (
            [tr(f"share.made_{made[0]}", version=made[1])] if made else [])
        msg = trn("app.pasted_shared", len(shapes), n=len(shapes))
        if left:
            msg += tr("app.shared_left_out_short", n=left)
        if made:
            msg += tr("app.shared_made_short", version=made[1])
        if clamped:
            msg += " " + trn("app.pasted_15", clamped, n=clamped)
        self.status.config(text=msg)
        if notes:
            messagebox.showwarning(tr("app.spiderweb_2"), "\n\n".join(notes), parent=self)

    def paste(self, whole=False, at=None):
        """Paste the copied shapes so they start at the play line (snapped to the grid), or at beat `at` (a Select
        double-click: the mouse, snapped already). With curves highlighted and a curve copied: its shape onto them.
        Shapes shared as text or notes copied in Domino, copied after Spiderweb's last copy, come first
        (paste_shared, paste_from_domino). The Tempo pane clicked last: its copied tempo at the play line."""
        self.finish_copy()
        if not whole and at is None and getattr(self, "key_pane", None) is self.tempo_pane:
            return self.tempo_pane.paste()
        if getattr(getattr(self, "stir_window", None), "drawing", None):  # (a Stir area being drawn: only new
            # strokes make it, a paste would be left behind, hunt C 3)
            self.status.config(text=tr("stir.draw_no_paste"))
            return
        text = self.shared_clip()
        if text == "":  # (busy: what's on it can't be told, so nothing older is pasted in its place)
            self.status.config(text=tr("app.clipboard_busy"))
            return
        if text is not None:
            return self.paste_shared(text, at)
        if self.domino_newer():
            return self.paste_from_domino(at)
        if not whole and self.roll.curve_parts() and self.roll.curve_clip:
            return self.roll.paste_curve()
        if not whole and self.clip_kind == "stroke":
            return self.roll.paste_stroke()
        if not self.clipboard:
            return
        start = min(b for sh in self.clipboard for b, _ in cached_path(sh))
        if at is None:
            at, sb = self.playhead, self.snap_beats()
            if sb:
                at = round(at / sb) * sb
        return self.add_copies(self.clipboard, at - start)
