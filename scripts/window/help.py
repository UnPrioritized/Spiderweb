"""Tips and the Help window. A tip pops up the first time a tool or feature is used (which ones were seen is
remembered with the window settings; they can be turned off or all shown again). The Help window (F1) lists every
topic of help_texts.py, with a search box. Topics can have short clips (clips/*.gif, inside the .exe, played in
the Help window; which ones go where: see help_texts.py)."""

import os
import re
import sys
import tkinter as tk
import webbrowser
from tkinter import font as tkfont, messagebox, ttk

from files.lang import tr
from files.about import BANNER, BANNER_HALF, HERE, LICENSE, VERSION, WEBSITE
from files.system import WINDOWS, open_path
from window import look
from window.widgets import placed, remember_place
from window.help_texts import BY_ID, DRAWER_TOOL_TOPICS, NEXT, SECTION_NAMES, SECTIONS, SEE, TOOL_TOPICS, TOPICS
from window.updates import often_box

TOOL_TIPS = set(TOOL_TOPICS.values()) | set(DRAWER_TOOL_TOPICS.values())
# the tips' place once dragged (window_places; "tip" was saved where the first tip showed, moved or not: left behind)
TIP_PLACE = "tip_moved"
CLIPS = os.path.join(getattr(sys, "_MEIPASS", HERE), "clips")  # (the .exe carries them inside)


def look_box(parent):
    """Help → About: Light / Dark / Follow Windows (only on Windows). Saved at once; a different look than the one
    showing = a message to restart (user: it's put on at the next start)."""
    names = {k: tr("look." + k) for k in look.LOOKS if k != "windows" or WINDOWS}
    var = tk.StringVar(value=names.get(look.read_look(), names["light"]))
    box = ttk.Combobox(parent, textvariable=var, values=list(names.values()), state="readonly", width=18)

    def picked(e):
        how = next(k for k, n in names.items() if n == var.get())
        try:
            look.save_look(how)
        except OSError:
            messagebox.showerror(tr("look.title"), tr("look.not_saved"), parent=box.winfo_toplevel())
            var.set(names.get(look.read_look(), names["light"]))  # (back to the pick that's saved)
            return
        if look.is_dark(how) != look.DARK:
            messagebox.showinfo(tr("look.title"), tr("look.restart"), parent=box.winfo_toplevel())
    box.bind("<<ComboboxSelected>>", picked)
    return box


def gif_frames(path):
    """Split a GIF into its frames without decoding them: ((width, height), [(one-frame GIF, x, y, delay in ms,
    disposal)]), or None if it isn't a GIF. (Tk's own "gif -index n" decodes every frame before n again, so loading
    a clip frame by frame took seconds.)"""
    with open(path, "rb") as f:
        d = f.read()
    if d[:3] != b"GIF" or len(d) < 13:
        return None
    size = (int.from_bytes(d[6:8], "little"), int.from_bytes(d[8:10], "little"))
    flags, i = d[10], 13
    if flags & 0x80:
        i += 3 * (2 << (flags & 7))
    table = d[13:i]
    frames, gce = [], None
    try:
        while i < len(d) and d[i] != 0x3B:
            if d[i] == 0x21:  # extension; only the frame's timing / transparency one is kept
                start, label = i, d[i + 1]
                i += 2
                while d[i]:
                    i += d[i] + 1
                i += 1
                if label == 0xF9:
                    gce = d[start:i]
            elif d[i] == 0x2C:  # a frame: its box, then (maybe) its own colours and the picture data
                start = i
                x, y, w, h = (int.from_bytes(d[i + 1 + 2 * k:i + 3 + 2 * k], "little") for k in range(4))
                f = d[i + 9]
                i += 10
                if f & 0x80:
                    i += 3 * (2 << (f & 7))
                i += 1
                while d[i]:
                    i += d[i] + 1
                i += 1
                delay, disposal = 0, 0
                if gce:
                    delay, disposal = int.from_bytes(gce[4:6], "little"), (gce[3] >> 2) & 7
                one = (b"GIF89a" + w.to_bytes(2, "little") + h.to_bytes(2, "little") + bytes([flags & 0x87, 0, 0])
                       + table + (gce or b"") + b"\x2C" + bytes(4) + d[start + 5:i] + b"\x3B")
                # (like web browsers: no delay or a tiny one = a tenth of a second)
                frames.append((one, x, y, delay * 10 if delay >= 2 else 100, disposal))
                gce = None
            else:
                break
    except IndexError:  # cut off: play the frames that are whole
        pass
    return (size, frames) if frames else None


class Clip:
    """A GIF playing in a label. Each frame is decoded when it's shown and drawn over the picture so far, so a
    clip opens at once, GIFs that only store the pixels that changed play right, and the GIF's own timing is kept."""

    def __init__(self, parent, path):
        (w, h), self.frames = gif_frames(path)
        self.image = tk.PhotoImage(width=w, height=h)
        self.clear = tk.PhotoImage(width=1, height=1)  # (see-through: copied over a spot to empty it)
        self.label = tk.Label(parent, image=self.image, borderwidth=0)
        self.i, self.saved, self.job = 0, None, None
        self.step()

    def step(self):
        if not self.label.winfo_exists():
            return
        img, n = self.image, self.i % len(self.frames)
        if n == 0:
            img.blank()
        else:  # what the frame before asked for once it's been shown
            _, x, y, _, disposal = prev = self.frames[n - 1]
            if disposal == 2:
                w, h = self.size(prev)
                img.tk.call(img, "copy", self.clear, "-to", x, y, x + w, y + h, "-compositingrule", "set")
            elif disposal == 3 and self.saved:
                img.tk.call(img, "copy", self.saved, "-compositingrule", "set")
        data, x, y, delay, disposal = self.frames[n]
        if disposal == 3:
            self.saved = tk.PhotoImage()
            self.saved.tk.call(self.saved, "copy", img)
        try:
            pic = tk.PhotoImage(data=data, format="gif")
            img.tk.call(img, "copy", pic, "-to", x, y)
        except tk.TclError:  # a broken frame: skip it
            pass
        self.i += 1
        self.job = self.label.after(delay, self.step)

    @staticmethod
    def size(frame):
        d = frame[0]
        return int.from_bytes(d[6:8], "little"), int.from_bytes(d[8:10], "little")

    def stop(self):
        if self.job:
            self.label.after_cancel(self.job)
            self.job = None


def topic_words(t):
    return " ".join((t["title"], t["tip"], t["text"], t.get("words", ""))).lower()


class Tips:
    """Which tips were seen, whether they show at all, and the popup."""

    def __init__(self, app):
        self.app = app
        self.seen = set()
        self.on = tk.BooleanVar(value=True)
        self.popup = None
        self.waiting = []  # [(topic, parent, force)] shown one after another once the open tip is closed

    def show(self, topic_id, parent=None, force=False, wait=False, done=False):
        """The topic's tip, unless it was seen before or tips are off (force: anyway). wait: if another tip is
        open, show it once that one is closed instead of replacing it. done: the open tip was read ("Got it" going
        on to the next one), so it isn't shown again later."""
        if topic_id not in BY_ID or not force and (not self.on.get() or topic_id in self.seen):
            return
        open_now = self.popup is not None and self.popup.winfo_exists()
        if wait and open_now and self.popup.topic != topic_id:
            if topic_id not in [w[0] for w in self.waiting]:
                self.waiting.append((topic_id, parent, force))
            return
        self.waiting = [w for w in self.waiting if w[0] != topic_id]
        if open_now and not done and self.popup.topic != topic_id and self.popup.topic not in TOOL_TIPS:
            # a tip pushed aside (by a tool's tip, say) comes back next; one tool's tip replacing another's doesn't
            self.waiting.insert(0, (self.popup.topic, self.popup.master, True))
        self.seen.add(topic_id)
        self.app.schedule_autosave()
        parent = parent or self.app
        if open_now and self.popup.master is parent:
            self.popup.set_topic(topic_id)
        else:
            self.close()
            self.popup = TipPopup(self, parent, topic_id)

    def close(self):
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = None

    def closed(self):
        """A tip was closed: the next one waiting, if any (skipping ones seen in the meantime). Tips turned off:
        the waiting ones are dropped too."""
        if not self.on.get():
            self.waiting = []
            return
        while self.waiting and (self.popup is None or not self.popup.winfo_exists()):
            topic_id, parent, force = self.waiting.pop(0)
            if parent is None or parent.winfo_exists():
                self.show(topic_id, parent, force=force)

    def reset(self):
        self.seen.clear()
        self.app.schedule_autosave()

    def state(self):
        """For the autosave's window settings."""
        return {"tips_seen": sorted(self.seen), "tips_on": self.on.get()}

    def restore(self, win):
        seen = win.get("tips_seen")
        if isinstance(seen, list):
            self.seen = {str(s) for s in seen}
        self.on.set(win.get("tips_on") is not False)


class TipPopup(tk.Toplevel):
    """A small window in the top right corner of the roll (or the drawer): the tip, "More…" (the Help window at
    that topic), "Got it". It doesn't block anything."""

    def __init__(self, tips, parent, topic_id):
        f = parent.focus_get()  # (before this window exists)
        super().__init__(parent)
        self.tips, self.parent = tips, parent
        s = self.scale = tips.app.scale
        self.title(tr("help.tip"))
        self.transient(parent)
        self.resizable(False, False)
        try:
            self.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        box = ttk.Frame(self, padding=(12, 10, 12, 10))
        box.pack(fill="both", expand=True)
        self.head = ttk.Label(box, font=look.font(10, "bold"))
        self.head.pack(anchor="w")
        self.body = ttk.Label(box, wraplength=int(380 * s), justify="left")
        self.body.pack(anchor="w", pady=(4, 10))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Checkbutton(row, text=tr("help.show_tips"), variable=tips.on,
                        command=tips.app.schedule_autosave).pack(side="left")
        ttk.Button(row, text=tr("help.got_it"), command=self.got_it).pack(side="right")
        ttk.Button(row, text=tr("help.more"), command=self.more).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda e: self.got_it())
        self.protocol("WM_DELETE_WINDOW", lambda: (self.destroy(), tips.closed()))
        self.set_topic(topic_id)
        # back to what you were doing: the popup doesn't take the keyboard (a box being typed in keeps it)
        back = f if f is not None and f.winfo_toplevel() is parent.winfo_toplevel() else parent
        self.after(30, lambda: (back if back.winfo_exists() else parent).focus_force() if parent.winfo_exists()
                   else None)

    def set_topic(self, topic_id):
        t = BY_ID[topic_id]
        self.topic = topic_id
        self.head.config(text=t["title"])
        self.body.config(text=t["tip"])
        self.update_idletasks()
        p = self.parent
        area = getattr(p, "roll", None) or getattr(p, "canvas", None) or p
        x = area.winfo_rootx() + area.winfo_width() - self.winfo_reqwidth() - int(16 * self.scale)
        y = area.winfo_rooty() + int(16 * self.scale)
        # dragged once: every tip stays where it was put; until then each one goes to its own window's corner
        places = self._root().__dict__.setdefault("window_places", {})
        moved = placed(self, places.get(TIP_PLACE, ""))
        if moved:
            self.put = None
            self.geometry(moved)
        else:
            self.put = (max(0, x), max(0, y))
            self.geometry("+%d+%d" % self.put)
        if not getattr(self, "watched", False):
            self.watched = True
            self.bind("<Configure>", self.keep_place, add="+")
        self.deiconify()

    def keep_place(self, e):
        """Remembered only when the user moved it (not where it was put)."""
        if e.widget is not self or not self.winfo_ismapped():
            return
        m = re.search(r"\+(-?\d+)\+(-?\d+)$", self.wm_geometry())
        if m and (self.put is None or abs(int(m[1]) - self.put[0]) > 2 or abs(int(m[2]) - self.put[1]) > 2):
            self._root().window_places[TIP_PLACE] = f"+{m[1]}+{m[2]}"
            self.put = None

    def got_it(self):
        """Close it, or go on to the tip that follows this one (NEXT) if that wasn't seen yet."""
        nxt = NEXT.get(self.topic)
        if nxt and self.tips.on.get() and nxt not in self.tips.seen:
            self.tips.show(nxt, parent=self.parent, done=True)
        else:
            self.destroy()
            self.tips.closed()

    def more(self):
        """The Help window at this topic. A tip waiting stays waiting (it would take the keyboard from Help)."""
        open_help(self.tips.app, self.topic)
        self.destroy()


class HelpWindow(tk.Toplevel):
    """Every topic: a list by section with a search box (all the words typed have to be in the topic), the chosen
    topic on the right (the words searched for highlighted), its clip if there is one."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        s = self.scale = app.scale
        self.title(tr("help.spiderweb_help", VERSION=VERSION))
        self.geometry(f"{int(900 * s)}x{int(620 * s)}")
        self.minsize(int(600 * s), int(360 * s))
        remember_place(self, "help")
        self.topic = None
        self.drawn = None  # (topic, search) on the page now: the same again isn't drawn twice
        self.clips = []  # the Clip objects playing in the shown topic
        self.pictures = {}  # clips/<name>.png read once while the window is open

        left = ttk.Frame(self, padding=(8, 8, 4, 8))
        left.pack(side="left", fill="y")
        self.query = tk.StringVar()
        search = ttk.Entry(left, textvariable=self.query, width=30)
        search.pack(fill="x")
        ttk.Label(left, text=tr("help.search_type_words_all_of_them"), foreground=look.HINT,
                  font=look.font(8)).pack(anchor="w", pady=(2, 4))
        self.query.trace_add("write", lambda *_: self.fill_list())
        search.bind("<Down>", lambda e: (self.tree.focus_set(), self.pick_first()))
        search.bind("<Return>", lambda e: self.pick_first())
        tree_box = ttk.Frame(left)
        tree_box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_box, show="tree", selectmode="browse")
        font = tkfont.nametofont(look.TK)  # (wide enough for the longest title, in any language)
        self.tree.column("#0", width=max(font.measure(t["title"]) for t in TOPICS) + int(48 * s))
        sb = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_pick())

        right = ttk.Frame(self, padding=(4, 8, 8, 8))
        right.pack(side="left", fill="both", expand=True)
        bottom = ttk.Frame(right)
        bottom.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Checkbutton(bottom, text=tr("help.show_a_tip_the_first_time"), variable=app.tips.on,
                        command=app.schedule_autosave).pack(side="left")
        ttk.Button(bottom, text=tr("help.show_all_tips_again"), command=self.reset_tips).pack(side="left", padx=(8, 0))
        self.reset_note = ttk.Label(bottom, text="", foreground=look.GOOD)
        self.reset_note.pack(side="left", padx=(6, 0))
        version = ttk.Label(bottom, text=tr("help.spiderweb", VERSION=VERSION), foreground=look.FAINT_TEXT, cursor="hand2")
        version.pack(side="right")
        version.bind("<Button-1>", lambda e: self.open_topic("about"))
        version.bind("<Enter>", lambda e: version.config(font=look.font(9, "underline")))
        version.bind("<Leave>", lambda e: version.config(font=look.font(9)))
        text_box = ttk.Frame(right)
        text_box.pack(fill="both", expand=True)
        bg = ttk.Style().lookup("TFrame", "background") or "SystemButtonFace"
        self.text = tk.Text(text_box, wrap="word", font=look.font(10), relief="flat", borderwidth=0,
                            highlightthickness=0, padx=10, pady=6, cursor="arrow", background=bg,
                            spacing2=2, spacing3=4)
        tsb = ttk.Scrollbar(text_box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=tsb.set)
        self.text.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.text.tag_configure("title", font=look.font(14, "bold"), spacing3=8)
        self.text.tag_configure("section", font=look.font(9), foreground=look.HINT)
        self.text.tag_configure("hit", background=look.SEARCH_HIT)
        self.text.tag_configure("link", foreground=look.LINK)
        self.text.tag_configure("hover", underline=True)
        # (a long bullet line wraps under its text, not under the •)
        self.text.tag_configure("bullet", lmargin2=tkfont.Font(font=self.text.cget("font")).measure("•  "))
        self.text.config(state="disabled")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<F1>", lambda e: "break")  # (already open: stays on the page being read)
        self.bind("<Control-f>", lambda e: (search.focus_set(), search.select_range(0, "end")))
        self.bind("<Key>", lambda e: self.type_to_search(e, search))
        self.fill_list()
        search.focus_set()

    def type_to_search(self, e, search):
        """Typing anywhere in the window types into the search box (Backspace too)."""
        if e.widget is search or e.state & 0x4 or e.state & 0x20000:  # (Ctrl / Alt: shortcuts)
            return None
        if e.keysym == "BackSpace":
            search.focus_set()
            search.delete(max(0, len(search.get()) - 1), "end")
            return "break"
        if not e.char or not e.char.isprintable():
            return None
        if e.char == " " and isinstance(e.widget, (ttk.Button, ttk.Checkbutton)):
            return None  # (Space presses a button that has the keyboard)
        search.focus_set()
        search.select_clear()
        search.insert("end", e.char)
        search.icursor("end")
        return "break"

    def matches(self):
        words = self.query.get().lower().split()
        return [t for t in TOPICS if all(w in topic_words(t) for w in words)]

    def fill_list(self):
        found = self.matches()
        self.tree.delete(*self.tree.get_children())
        for section in SECTIONS:
            topics = [t for t in found if t["section"] == section]
            if not topics:
                continue
            node = self.tree.insert("", "end", iid="section:" + section, text=SECTION_NAMES[section], open=True)
            for t in topics:
                self.tree.insert(node, "end", iid=t["id"], text=t["title"])
        if self.topic and self.tree.exists(self.topic):
            self.tree.selection_set(self.topic)
            self.tree.see(self.topic)
            self.show(self.topic)  # (the highlighted words changed)
        elif found:
            self.pick_first()
        else:
            self.show(None)

    def pick_first(self):
        first = next((c for s in self.tree.get_children() for c in self.tree.get_children(s)), None)
        if first:
            self.tree.selection_set(first)
            self.tree.see(first)

    def on_pick(self):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("section:"):  # a section: its first topic
            kids = self.tree.get_children(iid)
            if kids:
                self.tree.selection_set(kids[0])
            return
        self.show(iid)

    def open_topic(self, topic_id):
        if self.query.get() and topic_id not in [t["id"] for t in self.matches()]:
            self.query.set("")
        self.topic = topic_id
        if self.tree.exists(topic_id):
            self.tree.selection_set(topic_id)
            self.tree.see(topic_id)
        self.show(topic_id)

    def show(self, topic_id):
        """The topic's page (None: "Nothing found", the open topic kept for when the search finds it again)."""
        if (topic_id, self.query.get()) == self.drawn:
            return
        self.drawn = (topic_id, self.query.get())
        if topic_id is not None:
            self.topic = topic_id
        t = self.text
        t.config(state="normal")
        self.stop_clip()
        for w in t.winfo_children():  # (deleting the text leaves them to Python, with their pictures)
            w.destroy()
        t.delete("1.0", "end")
        if topic_id is None:
            t.insert("end", tr("help.nothing_found_try_fewer_or_other"), "section")
        else:
            topic = BY_ID[topic_id]
            t.insert("end", SECTION_NAMES[topic["section"]] + "\n", "section")
            t.insert("end", topic["title"] + "\n", "title")
            if topic_id == "about":
                self.about_banner()
            if self.add_clip(topic_id):
                t.insert("end", "\n\n")
            for n, part in enumerate(re.split(r"\[clip:([^\]]*)\]\n?", topic["page"])):
                if n % 2:  # (the odd parts are the clip names)
                    if self.add_clip(part):
                        t.insert("end", "\n")
                else:
                    t.insert("end", part)
            for i in range(1, int(t.index("end").split(".")[0])):
                if t.get(f"{i}.0") == "•":
                    t.tag_add("bullet", f"{i}.0", f"{i}.end")
            for w in set(self.query.get().lower().split()):
                start = "1.0"
                while True:
                    start = t.search(w, start, "end", nocase=True)
                    if not start:
                        break
                    end = f"{start}+{len(w)}c"
                    t.tag_add("hit", start, end)
                    start = end
            if topic_id == "about":
                self.about_buttons()
            self.see_also(SEE.get(topic_id, []))
        t.config(state="disabled")

    def see_also(self, ids):
        """The clickable "See also" line: each related topic's title opens it."""
        t = self.text
        if not ids:
            return
        t.insert("end", tr("help.see_also"), "section")
        for n, tid in enumerate(ids):
            if n:
                t.insert("end", "  ·  ", "section")
            tag = "go:" + tid
            t.insert("end", BY_ID[tid]["title"], ("link", tag))
            t.tag_bind(tag, "<Button-1>", lambda e, tid=tid: self.open_topic(tid))
            t.tag_bind(tag, "<Enter>",
                       lambda e, tag=tag: (t.config(cursor="hand2"), t.tag_add("hover", *t.tag_ranges(tag))))
            t.tag_bind(tag, "<Leave>", lambda e: (t.config(cursor="arrow"), t.tag_remove("hover", "1.0", "end")))

    def about_banner(self):
        """The About page's picture (half size, full size on big screen scaling), if it's there."""
        try:  # (kept in self, or Tk forgets the picture)
            self._banner = tk.PhotoImage(file=BANNER if self.scale >= 1.5 else BANNER_HALF)
        except tk.TclError:
            return
        self.embed(tk.Label(self.text, image=self._banner, borderwidth=0))
        self.text.insert("end", "\n\n")

    def about_buttons(self):
        """The About page's website link and buttons: the website, the license, Spiderweb's folder; update checks."""
        t = self.text
        at = t.search(WEBSITE, "1.0", "end")
        if at:
            t.tag_add("web", at, f"{at}+{len(WEBSITE)}c")
            t.tag_add("link", at, f"{at}+{len(WEBSITE)}c")
            t.tag_bind("web", "<Button-1>", lambda e: webbrowser.open(WEBSITE))
            t.tag_bind("web", "<Enter>", lambda e: (t.config(cursor="hand2"), t.tag_add("hover", *t.tag_ranges("web"))))
            t.tag_bind("web", "<Leave>", lambda e: (t.config(cursor="arrow"), t.tag_remove("hover", "1.0", "end")))
        row = ttk.Frame(self.text)
        ttk.Button(row, text=tr("help.website"), takefocus=False,
                   command=lambda: webbrowser.open(WEBSITE)).pack(side="left", padx=(0, 8))
        if os.path.exists(LICENSE):
            ttk.Button(row, text=tr("help.license"), takefocus=False,
                       command=lambda: open_path(LICENSE, text=True)).pack(side="left")
        ttk.Button(row, text=tr("help.open_spiderweb_s_folder"), takefocus=False,
                   command=lambda: open_path(HERE)).pack(side="left", padx=(8, 0))
        self.text.insert("end", "\n\n")
        self.embed(row)
        # updates: how often to look, and looking now (the answer shows next to the button)
        updates = self.app.updates
        box = ttk.Frame(self.text)
        row = ttk.Frame(box)
        row.pack(anchor="w")
        ttk.Label(row, text=tr("updates.check_for_updates")).pack(side="left")
        often_box(row, updates).pack(side="left", padx=(6, 8))
        status = ttk.Label(box, text="", foreground=look.HINT)  # (under the row, so a long answer isn't cut off)
        status.pack(anchor="w", pady=(4, 0))
        ttk.Button(row, text=tr("updates.check_now"), takefocus=False, command=lambda: (
            status.config(text=tr("updates.checking")),
            updates.check(report=lambda msg: status.winfo_exists() and status.config(text=msg)))).pack(side="left")
        self.text.insert("end", "\n\n")
        self.embed(box)
        # the look: light / dark / follow Windows (put on at the next start)
        row = ttk.Frame(self.text)
        ttk.Label(row, text=tr("look.look")).pack(side="left")
        look_box(row).pack(side="left", padx=(6, 0))
        self.text.insert("end", "\n\n")
        self.embed(row)

    def add_clip(self, name):
        """Put clips/<name>.gif (playing) or clips/<name>.png (a still picture) at the end of the text. False if
        there's neither."""
        path = os.path.join(CLIPS, name + ".gif")
        picture = os.path.join(CLIPS, name + ".png")
        if not os.path.exists(path) and os.path.exists(picture):
            image = self.pictures.get(name)
            if image is None:
                try:  # (kept in self.pictures, or Tk forgets it)
                    image = self.pictures[name] = tk.PhotoImage(file=picture)
                except tk.TclError:
                    return False
            label = tk.Label(self.text, image=image, borderwidth=0)
            self.embed(label)
            return True
        try:
            clip = Clip(self.text, path) if os.path.exists(path) and gif_frames(path) else None
        except (OSError, tk.TclError):
            clip = None
        if clip:
            self.clips.append(clip)
            self.embed(clip.label)
        return bool(clip)

    def embed(self, widget):
        """Put a widget (clip, picture, buttons) at the end of the text. The wheel over it still scrolls the text."""
        self.text.window_create("end", window=widget)
        for w in [widget] + widget.winfo_children():
            w.bind("<MouseWheel>", lambda e: self.text.event_generate("<MouseWheel>", delta=e.delta) or "break")

    def stop_clip(self):
        for clip in self.clips:
            clip.stop()
        self.clips = []

    def reset_tips(self):
        self.app.tips.reset()
        self.app.tips.on.set(True)
        self.reset_note.config(text=tr("help.done_every_tip_shows_again_the"))


def open_help(app, topic_id=None):
    """The Help window (one of it), at that topic."""
    win = getattr(app, "help_window", None)
    if win is None or not win.winfo_exists():
        win = app.help_window = HelpWindow(app)
    win.deiconify()
    win.lift()
    win.focus_force()
    win.open_topic(topic_id or "welcome")
