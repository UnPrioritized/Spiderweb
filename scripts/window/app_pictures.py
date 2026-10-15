"""Mixed into App: placed pictures (Image to notes): placing, editing, their notes made again when resized or
when the colours change."""

import os

import numpy as np

from files.lang import tr, trn


class Pictures:
    """Mixed into App."""

    def open_image(self):
        """The image window (Picture tool)."""
        from window.image_window import ImageWindow
        return ImageWindow.open(self)

    def add_picture(self, sh):
        """A picture placed from the image window: a new shape, selected, one undo step."""
        self.push_undo(name=tr("image.step_name"))
        self.shapes.append(sh)
        missing = self.sync_pictures(sh["picture"]["set"], len(self.shapes) - 1)
        self.select(len(self.shapes) - 1)
        self.shapes_changed()
        self.status.config(text=tr("image.placed", name=sh["name"]) +
                           (trn("image.others_missing", missing, n=missing) if missing else ""))

    def sync_pictures(self, s, skip):
        """Every other placed picture takes the colours in settings s (one set for all, user): their notes made
        again with them (from their own picture files; a missing file keeps its notes, whose colours then shift).
        Returns how many files were missing."""
        from notes import picture as P
        from notes.custom import pack_notes
        pal = np.array([P.lin_of(h) for h in s["pal"]])
        missing = 0
        for j, sh in enumerate(self.shapes):
            if j == skip or "picture" not in sh:
                continue
            p = sh["picture"]
            t = p["set"]
            if (t.get("pal"), t.get("locked", []), t.get("use10", False)) == (s["pal"], s.get("locked", []),
                                                                              s.get("use10", False)):
                continue
            t.update(pal=list(s["pal"]), locked=list(s.get("locked", [])), use10=s.get("use10", False),
                     colours=len(s["pal"]))
            got = self.picture_notes_in(sh, pal)
            if got is None:
                missing += 1
            elif len(got):
                sh["notes"] = pack_notes(got)
        return missing

    def picture_notes_in(self, sh, pal):
        """A placed picture's notes made again from its own file in these colours (linear), None without the file."""
        from notes import picture as P
        p = sh["picture"]
        t = p["set"]
        pic = self.picture_for(p)
        if pic is None:
            return None
        steps, keys = p["grid"]
        rows, cols = (steps, keys) if t["view"] == "fall" else (keys, steps)
        cl, al = P.cells(pic, rows, cols)
        cl = P.adjust(cl, t["brightness"], t["contrast"], t["saturation"], t["sharpen"])
        return P.grid_notes(P.quantise(cl, pal, t["blend"], t["strength"], t["keep"], al, t["empty"]), t["view"],
                            t["join"])[0]

    def fit_pasted_pictures(self, first):
        """Pasted / duplicated pictures (shapes from first on) take the channel-10 setting of the pictures already
        here (one for the project). Without it, a 16-colour one loses its 16th colour, its notes going to the
        nearest of the others (made again from its file when it's there; hunt, user). Returns how many lost one."""
        from notes import picture as P
        from notes.custom import pack_notes, unpack_notes
        here = next((sh["picture"]["set"] for sh in self.shapes[:first] if "picture" in sh), None)
        if here is None:
            return 0
        want = here.get("use10", False)
        cut = 0
        for sh in self.shapes[first:]:
            t = (sh.get("picture") or {}).get("set")
            if t is None or t.get("use10", False) == want:
                continue
            pal = t.get("pal") or []
            if want or len(pal) <= 15 or self.all16():
                t["use10"] = want or len(pal) > 15  # (16 colours with the project's tick: kept, channel 10 too)
                continue
            lin = np.array([P.lin_of(h) for h in pal])
            got = self.picture_notes_in(sh, lin[:15])
            if got is None:  # (no file: colour 16's notes take the nearest colour)
                got = np.array(unpack_notes(sh["notes"]))
                near = int(np.argmin(((lin[:15] - lin[15]) ** 2).sum(1)))
                got[got[:, 4] >= 15, 4] = near
            if len(got):
                sh["notes"] = pack_notes(got)
            t.update(pal=pal[:15], colours=15, use10=False, locked=[k for k in t.get("locked", []) if k < 15])
            cut += 1
        return cut

    def picture_state(self, info):
        """"ok", "missing" (no file there), "unreadable" (a kind only Pillow reads, and it isn't installed) or
        "changed" (its fingerprint isn't the one it was made from)."""
        from notes import picture as P
        if not os.path.isfile(info["file"]):
            return "missing"
        if not P.readable(info["file"]):
            return "unreadable"
        return "ok" if self.picture_for(info) is not None else "changed"

    def edit_picture(self, i):
        """Double-click / "Change its look..." on a placed picture: the image window with its picture and settings,
        changes going to it."""
        if 0 <= i < len(self.shapes) and "picture" in self.shapes[i]:
            self.open_image().edit(i)

    def picture_for(self, info):
        """The placed picture's own picture file, read (kept while it's unchanged), or None if it's missing or its
        fingerprint isn't the one it was made from."""
        from notes import picture as P
        path = info["file"]
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (path, st.st_mtime_ns, st.st_size)
        got = self._pictures.get(key)
        if got is None:
            try:
                got = P.load(path, self)
            except Exception:  # (unreadable now)
                return None
            self._pictures = {k: v for k, v in self._pictures.items() if k[0] != path}
            self._pictures[key] = got
        return got if not info["sig"] or got.sig == info["sig"] else None

    def remake_pictures(self):
        """Placed pictures resized (box not turned): their notes made again from the ORIGINAL picture at the new
        size, so they stay sharp (user); the box snaps to whole keys and grid steps. Without the picture file they
        keep their notes, stretched."""
        from notes import picture as P
        from notes.custom import frame_upright, pack_notes
        for sh in self.shapes:
            p = sh.get("picture")
            if not p or "pal" not in p["set"] or not frame_upright(sh["pts"]):
                continue
            s, (steps0, keys0) = p["set"], p["grid"]
            (b0, k0), (b1, _), (_, k2) = sh["pts"]
            width, height = b1 - b0, k2 - k0
            keys, steps = max(1, round(abs(height))), max(1, round(abs(width) / s["step"]))
            if (steps, keys) == (steps0, keys0) or keys > 256:
                continue
            pic = self.picture_for(p)
            if pic is None:
                continue
            rows, cols = (steps, keys) if s["view"] == "fall" else (keys, steps)
            if rows * cols > 4_000_000:  # (a box dragged huge: kept stretched)
                continue
            cl, al = P.cells(pic, rows, cols)
            cl = P.adjust(cl, s["brightness"], s["contrast"], s["saturation"], s["sharpen"])
            pal = np.array([P.lin_of(h) for h in s["pal"]])
            grid = P.quantise(cl, pal, s["blend"], s["strength"], s["keep"], al, s["empty"])
            got, t, k = P.grid_notes(grid, s["view"], s["join"])
            if not len(got):
                continue
            sh["notes"] = pack_notes(got)
            p["grid"] = [t, k]
            s["keys"] = keys
            sgn_b, sgn_k = (1 if width >= 0 else -1), (1 if height >= 0 else -1)
            sh["pts"] = [[b0, k0], [b0 + sgn_b * t * s["step"], k0], [b0, k0 + sgn_k * k]]

    def replace_picture(self, i):
        """Right-click → Use another picture...: the image window for this picture, asking for the new file."""
        if 0 <= i < len(self.shapes) and "picture" in self.shapes[i]:
            w = self.open_image()
            w.edit(i, ask=False)  # (one file picker, even when its file is missing / changed)
            w.ask_file()

    def picture_box(self, i, own_shape=False):
        """The placed picture upright again: its grid's own size, centred where it is (own_shape: as wide as the
        picture's own shape for its keys, its bottom left kept). One undo step; the notes are made again."""
        sh = self.shapes[i]
        p = sh["picture"]
        s, (steps, keys) = p["set"], p["grid"]
        (b0, k0), (b1, k1), (b2, k2) = sh["pts"]
        if own_shape:
            w, h = p["size"]
            steps = max(1, round(keys * h / w if s["view"] == "fall" else keys * w / h)) * s["steps"]
            left, low = min(b0, b1, b2, b1 + b2 - b0), min(k0, k1, k2, k1 + k2 - k0)
        else:
            left = (b1 + b2) / 2 - steps * s["step"] / 2
            low = (k1 + k2) / 2 - keys / 2
        self.push_undo(name=tr("image.menu_own_shape") if own_shape else tr("image.menu_unturn"))
        sh["pts"] = [[left, low], [left + steps * s["step"], low], [left, low + keys]]
        self.shapes_changed()  # (remake_pictures makes its notes for the new size, if its picture file is there)

    def unturn_picture(self, i):
        self.picture_box(i)

    def picture_own_shape(self, i):
        self.picture_box(i, own_shape=True)
